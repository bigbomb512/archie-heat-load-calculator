"""The Next step card: the job's steps in plain words, in order, with the one to do now."""

import unittest
from unittest.mock import patch

from backend import (drawing_tasks_service, guide_service, job_service, page_extraction_service, page_inventory_service,
                     skill_workflow_service)


def job(address="", above="", rooms="done", walls="done", roof="done", windows="done", results="todo", drawings="done",
        found="", weather="Sydney", locations=("Sydney", "Newcastle"), suggestion=""):
    tab = lambda state, detail="", count=0: {"state": state, "detail": detail, "count": count}
    return {"address": address, "above": above, "found_site": found, "weather_location": weather,
            "weather_locations": list(locations), "weather_suggestion": suggestion,
            "tabs": {"drawings": tab(drawings), "rooms": tab(rooms), "windows": tab(windows, "Check glazing"), "results": tab(results)},
            "subtabs": {"project": {"job_site": tab("done"), "tenancy_context": tab("done" if above else "needed")},
                        "rooms": {"room_details": tab(rooms, "Choose a use for 2 room(s).", 2), "measurements": tab("done")},
                        "walls": {"walls": tab(walls, "Review 3 AI wall decision(s).", 3), "roof": tab(roof)}}}


def guide(status, reading="done", equipment=None, drawing="done", review=None):
    equipment = equipment or {"status": "done", "open": 0}
    review = review if review is not None else {"status": "needs_review", "findings": [], "answers": {}}
    with patch.object(job_service, "status", return_value=status), \
            patch.object(page_inventory_service, "status", return_value={"status": reading}), \
            patch.object(page_extraction_service, "status", return_value=equipment), \
            patch.object(drawing_tasks_service, "status", return_value={"status": drawing}), \
            patch.object(skill_workflow_service, "get", return_value=review):
        return guide_service.steps(None, {"id": "j", "review_dir": "/nonexistent"})


class GuideTests(unittest.TestCase):
    def test_while_the_ai_reads_the_project_steps_come_first_and_the_ai_checks_wait(self):
        result = guide(job(rooms="needed"), reading="running")
        steps = {row["id"]: row for row in result["steps"]}
        self.assertEqual(steps["ai"]["state"], "working")
        self.assertIn("reading every page", steps["ai"]["why"])
        self.assertEqual(result["now"], "address")                       # not the rooms: the AI hasn't finished them
        self.assertEqual(steps["address"]["action"], {"kind": "go", "tab": "project", "subtab": "job-site", "label": "Check the address"})
        done = guide(job(address="1 Main St", above="floor", rooms="needed"), reading="running")
        self.assertEqual(done["now"], "ai")                               # nothing else to do yet: wait for the AI

    def test_the_address_found_on_the_drawings_is_quoted_and_a_stopped_review_says_why(self):
        result = guide(job(found="Shop 3, 22 Lemon Tree Av"), review={"status": "failed", "findings": [], "answers": {},
                                                                       "issues": [{"reason": "The AI usage limit is reached."}]})
        steps = {row["id"]: row for row in result["steps"]}
        self.assertIn("“Shop 3, 22 Lemon Tree Av”", steps["address"]["why"])
        self.assertEqual(result["now"], "ai")
        self.assertIn("The AI usage limit is reached.", steps["ai"]["why"])
        self.assertEqual(steps["questions"]["state"], "later")             # no list of questions exists yet

    def test_with_an_address_but_no_weather_location_the_step_asks_for_it_with_the_suggestion(self):
        result = guide(job(address="2 Central Park Ave, Chippendale NSW", above="floor", weather="", suggestion="Sydney"))
        step = {row["id"]: row for row in result["steps"]}["address"]
        self.assertEqual((result["now"], step["title"], step["state"]), ("address", "Choose the weather location", "needed"))
        self.assertIn("Suggested from the address: Sydney.", step["why"])
        # Without the handbook table there is nothing to choose: the step is done once the address is saved.
        self.assertEqual({row["id"]: row for row in guide(job(address="x", above="floor", weather="", locations=()))["steps"]}
                         ["address"]["state"], "done")

    def test_a_review_blocked_on_one_step_but_with_findings_has_finished(self):
        review = {"status": "blocked", "answers": {}, "issues": [{"reason": "Confirm the project address and design basis."}],
                  "findings": [{"id": "f1", "subskill_id": "lighting_evidence", "field": "lighting", "status": "proposed"}]}
        result = guide(job(address="1 Main St", above="floor"), review=review)
        self.assertEqual({row["id"]: row for row in result["steps"]}["ai"]["state"], "done")
        self.assertNotEqual(result["now"], "ai")
        nothing = guide(job(address="1 Main St", above="floor"), review={**review, "findings": []})
        self.assertEqual(nothing["now"], "ai")                                  # blocked before finding anything: stopped

    def test_after_the_ai_the_checks_go_in_order_then_calculate_before_the_optional_findings(self):
        review = {"status": "needs_review", "answers": {"n1": {}},
                  "findings": [{"id": "n1", "subskill_id": "information_needs", "field": "needs"},
                               {"id": "n2", "subskill_id": "information_needs", "field": "needs"},
                               {"id": "f1", "subskill_id": "lighting_evidence", "field": "lighting", "status": "proposed"}]}
        result = guide(job(address="1 Main St", above="floor", rooms="check"), review=review,
                       equipment={"status": "done", "open": 4})
        self.assertEqual(result["now"], "rooms")
        self.assertEqual([row["id"] for row in result["steps"]],
                         ["ai", "address", "above", "rooms", "walls", "roof", "windows", "questions", "equipment",
                          "calculate", "findings", "result"])
        steps = {row["id"]: row for row in result["steps"]}
        self.assertEqual((steps["questions"]["count"], steps["questions"]["action"]["label"]), (1, "Answer 1 question"))
        self.assertEqual(steps["equipment"]["action"]["label"], "Check 4 items")
        self.assertEqual(steps["findings"]["state"], "optional")
        ready = guide(job(address="1 Main St", above="floor"), review={**review, "answers": {"n1": {}, "n2": {}}})
        self.assertEqual((ready["now"], {row["id"]: row for row in ready["steps"]}["calculate"]["action"]["kind"]),
                         ("calculate", "calculate"))
        finished = guide(job(address="1 Main St", above="floor", results="done"), review={**review, "answers": {"n1": {}, "n2": {}}})
        self.assertEqual(finished["now"], "findings")                     # optional, offered once everything else is done
        clean = guide(job(address="1 Main St", above="floor", results="done"))
        self.assertEqual((clean["now"], clean["done"], clean["total"]), ("result", 12, 12))


if __name__ == "__main__":
    unittest.main()
