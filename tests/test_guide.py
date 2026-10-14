"""The Next step card: the job's steps in plain words, in order, with the one to do now."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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


def guide(status, reading="done", equipment=None, drawing="done", review=None, review_dir="/nonexistent"):
    equipment = equipment or {"status": "done", "open": 0}
    review = review if review is not None else {"status": "needs_review", "findings": [], "answers": {}}
    with patch.object(job_service, "status", return_value=status), \
            patch.object(page_inventory_service, "status", return_value={"status": reading}), \
            patch.object(page_extraction_service, "status", return_value=equipment), \
            patch.object(drawing_tasks_service, "status", return_value={"status": drawing}), \
            patch.object(skill_workflow_service, "get", return_value=review):
        return guide_service.steps(None, {"id": "j", "review_dir": review_dir})


class GuideTests(unittest.TestCase):
    def test_while_the_ai_reads_the_project_steps_come_first_and_the_ai_checks_wait(self):
        result = guide(job(rooms="needed"), reading="running")
        steps = {row["id"]: row for row in result["steps"]}
        self.assertEqual(steps["ai"]["state"], "working")
        self.assertIn("reading every page", steps["ai"]["why"])
        self.assertEqual(result["now"], "ai")                            # while reading, no manual Phase 1 checklist is offered
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

    def test_phase_one_can_run_on_saved_drawing_evidence_when_a_noncritical_review_step_failed(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "building_evidence.json").write_text('{"spaces":[{"name":"Shop"}]}', encoding="utf-8")
            result = guide(job(address="1 Main St", above="floor"), review={"status": "failed", "findings": [], "answers": {},
                           "problem": "Numeric inference needs citation_ids and a method."}, review_dir=directory)
        steps = {row["id"]: row for row in result["steps"]}
        self.assertEqual(steps["ai"]["state"], "error")
        self.assertEqual(result["now"], "base_load")
        self.assertEqual(steps["base_load"]["action"]["kind"], "calculate_base")
        self.assertIn("saved drawing evidence", steps["base_load"]["why"])

    def test_with_an_address_but_no_weather_location_the_step_asks_for_it_with_the_suggestion(self):
        result = guide(job(address="2 Central Park Ave, Chippendale NSW", above="floor", weather="", suggestion="Sydney"))
        step = {row["id"]: row for row in result["steps"]}["address"]
        self.assertEqual((result["now"], step["state"]), ("base_load", "done"))
        self.assertIn("Sydney", step["why"])
        # No weather-table selection blocks Phase 1; the generic design day is labelled in the result.
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

    def test_phase_one_is_ai_run_then_phase_two_loads_precede_optional_findings(self):
        review = {"status": "needs_review", "answers": {"n1": {}},
                  "findings": [{"id": "n1", "subskill_id": "information_needs", "field": "needs"},
                               {"id": "n2", "subskill_id": "information_needs", "field": "needs"},
                               {"id": "f1", "subskill_id": "lighting_evidence", "field": "lighting", "status": "proposed"}]}
        result = guide(job(address="1 Main St", above="floor", rooms="check"), review=review,
                       equipment={"status": "done", "open": 4})
        self.assertEqual(result["now"], "base_load")
        self.assertEqual([row["id"] for row in result["steps"]],
                         ["ai", "address", "above", "rooms", "walls", "roof", "windows", "base_load", "questions", "equipment",
                          "calculate", "findings", "result"])
        steps = {row["id"]: row for row in result["steps"]}
        self.assertEqual((steps["questions"]["count"], steps["questions"]["action"]["label"]), (1, "Answer 1 question"))
        self.assertEqual(steps["equipment"]["action"]["label"], "Check 4 items")
        self.assertEqual(steps["findings"]["state"], "optional")
        ready = guide(job(address="1 Main St", above="floor"), review={**review, "answers": {"n1": {}, "n2": {}}})
        self.assertEqual((ready["now"], {row["id"] for row in ready["steps"] if row["state"] == "needed"},
                          {row["id"]: row for row in ready["steps"]}["base_load"]["action"]["kind"]),
                         ("base_load", {"base_load", "calculate"}, "calculate_base"))
        finished = guide(job(address="1 Main St", above="floor", results="done"), review={**review, "answers": {"n1": {}, "n2": {}}})
        self.assertEqual(finished["now"], "base_load")                   # Phase 1 is independently calculated before Phase 2
        clean = guide(job(address="1 Main St", above="floor", results="done"))
        self.assertEqual((clean["now"], clean["done"], clean["total"]), ("base_load", 12, 13))


if __name__ == "__main__":
    unittest.main()
