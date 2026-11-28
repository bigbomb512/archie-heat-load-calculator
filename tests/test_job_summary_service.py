"""Behavioral checks for the read-only job summary."""

import unittest
from unittest.mock import patch

from backend import job_summary_service as service


def room(ident, name, area=15, kind="comfort", state="done", kw=1.2, level="First Floor", use_id="house_bedroom"):
    return {"room_id": ident, "name": name, "display_name": name, "area_m2": area, "level": level,
            "kind": kind, "state": state, "included": kind == "comfort", "final_kw": kw,
            "phase1_kw": kw, "use": {"taxonomy_id": use_id, "taxonomy_label": use_id}}


class SummaryTests(unittest.TestCase):
    def summary(self, rooms=(), status=None, final=2.4, base=None, result=None, drawing=None, supervisor=None,
                calculation=None, review_dir="/tmp/review", failure=None):
        peak = lambda value: {"included_scope_peak": {"final_design_total_kw": value, "sensible_kw": 2,
                 "latent_kw": .4, "month": "January", "display_hour": 15}} if value is not None else {}
        plan = {"status": {"phase2_complete": True, **(status or {})}, "rooms": list(rooms),
                "final_report": peak(final), "phase1_report": peak(base), "room_use_options": {"house_bedroom": "Bedroom"}}
        sources = ((service.job_plan_service, "get", plan),
                   (service.result_status_service, "gather", result or {}),
                   (service.drawing_tasks_service, "status", drawing or {}),
                   (service.phase1_supervisor_service, "status", supervisor or {}),
                   (service.calculation_service._JOB, "status", calculation or {}))
        patches = [patch.object(obj, method, side_effect=RuntimeError("broken") if failure == method
                         else None, return_value=value) for obj, method, value in sources]
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patch.object(service.contractor_report, "ducted", return_value={"suggested": "1 × 10 kW"}):
            return service.get(None, {"id": "job", **({"review_dir": review_dir} if review_dir else {})})

    def test_normal_rooms_have_no_room_todos(self):
        rooms = [room("bed", "Bedroom 1"), room("wir", "WIR", 2.9, "not_cooled", "not_cooled", None, use_id="robe"),
                 room("ens", "Ensuite", 5, "not_cooled", "not_cooled", None, use_id="bathroom"),
                 room("bal", "Balcony", 10, "not_cooled", "not_cooled", None, use_id="outdoor")]
        summary = self.summary(rooms)
        self.assertEqual(summary["todos"], [])
        self.assertEqual(summary["completeness"]["level"], "complete")

    def test_room_rules_and_saved_impact(self):
        rooms = [room("area", "Office", None, state="needs_area", kw=None),
                 room("wait", "Kitchen", 20, state="finding_surfaces", kw=None),
                 room("out", "Balcony", 11, kw=2.1, use_id="outdoor"),
                 room("small", "Bedroom 1", 3, kw=1.5),
                 room("robe", "WIR", 28.6, "not_cooled", "not_cooled", None, use_id="robe"),
                 room("large", "Living", 22, "not_cooled", "not_cooled", None, use_id="living"),
                 room("wet", "Ensuite", 21, "not_cooled", "not_cooled", None, use_id="bathroom"),
                 room("tiny", "Study", 1, kw=None, use_id="study")]
        summary = self.summary(rooms)
        kinds = {row["kind"] for row in summary["todos"]}
        self.assertTrue({"room_area", "room_not_counted", "outdoor_cooled", "room_size", "large_not_cooled"} <= kinds)
        self.assertEqual(summary["todos"][0]["severity"], "blocks")
        outdoor = next(row for row in summary["todos"] if row["kind"] == "outdoor_cooled")
        self.assertEqual(outdoor["impact"]["kw"], 2.1)
        self.assertIsNone(next(row for row in summary["todos"] if row["id"] == "size:tiny")["impact"])
        self.assertIn("WIR (28.6 m²)", next(row for row in summary["todos"] if row["id"] == "size:small")["detail"])
        self.assertIn("Bedroom 1 (3.0 m²)", next(row for row in summary["todos"] if row["id"] == "size:robe")["detail"])

    def test_other_sources_all_rules(self):
        summary = self.summary([room("office", "Office")], drawing={"unresolved": [
            {"task": "P0_room_outlines", "target": "room-dining-page-9"},
            {"task": "P0_room_outlines", "target": "room-corridor-page-9"}],
            "recovery": {"action": "retry"}}, supervisor={"exceptions": [
                {"kind": "room_area", "room": "Study", "message": "Area missing"},
                {"kind": "roof", "room": "Office"}, {"kind": "roof", "room": "Kitchen"}],
                "recovery_project_id": "job"}, result={"items": [
                    {"kind": "to_find", "topic": "Equipment with no room", "text": "Missing equipment"},
                    {"kind": "placeholder", "topic": "Equipment", "text": "Generic value"},
                    {"kind": "to_find", "topic": "PDF review", "text": "3 findings"},
                    {"kind": "not_set", "topic": "Site", "text": "Address unset"},
                    {"kind": "assumed", "topic": "Weather", "text": "AI picked"},
                    {"kind": "typical", "topic": "Bedroom", "text": "Typical values"}]},
                    status={"weather_ai_pick": "Sydney"})
        kinds = {row["kind"] for row in summary["todos"]}
        self.assertTrue({"drawing_task", "model_gap", "equipment", "drawing_findings", "site", "weather"} <= kinds)
        self.assertEqual(next(row for row in summary["todos"] if row["kind"] == "drawing_task")["actions"][0]["body"], {"action": "retry"})
        self.assertEqual(next(row for row in summary["todos"] if row["kind"] == "drawing_findings")["title"],
                         "3 drawing findings haven't been reviewed")
        self.assertIn("typical values", summary["completeness"]["text"])

    def test_stale_is_first_and_phase_depends_on_status(self):
        for phase2, expected in ((True, "full"), (False, "phase1_base_room")):
            with self.subTest(phase2=phase2):
                summary = self.summary([room("a", "Office", None, state="needs_area")],
                                       status={"result_stale": True, "phase2_complete": phase2})
                self.assertEqual(summary["answer"]["state"], "stale")
                self.assertEqual(summary["todos"][0]["id"], "stale")
                self.assertEqual(summary["todos"][0]["actions"][0]["phase"], expected)

    def test_answer_states_and_failures(self):
        building = self.summary([room("a", "Office", kw=.8)], final=None, base=1.1)
        self.assertEqual((building["answer"]["state"], building["answer"]["total_kw"],
                          building["answer"]["suggested_system"]), ("building_only", 1.1, None))
        none = self.summary(final=None, base=None, review_dir=None)
        self.assertEqual((none["answer"]["state"], none["answer"]["total_kw"]), ("none", None))
        working = self.summary(calculation={"status": "running"})
        self.assertEqual(working["answer"]["state"], "working")
        failed = self.summary([room("a", "Office", None, state="needs_area")], failure="gather")
        self.assertIn("result_status_service", failed["sources_failed"])
        self.assertIn("room_area", [row["kind"] for row in failed["todos"]])

    def test_wording_rounds_and_counts_plainly(self):
        summary = self.summary([room("bal", "Balcony", 16.7, kw=0.26915, use_id="house_living"),
                                room("x", "Study", state="finding_surfaces", kw=None)],
                               result={"items": [{"kind": "to_find", "topic": "Equipment with no room",
                                                  "text": "1 item(s). The drawings don't show which room."}]})
        balcony = next(row for row in summary["todos"] if row["id"] == "outdoor:bal")
        self.assertEqual(balcony["impact"], {"kw": 0.27, "text": "Adds 0.3 kW to the total now"})
        equipment = next(row for row in summary["todos"] if row["kind"] == "equipment")
        self.assertTrue(equipment["detail"].startswith("1 item. "))
        self.assertIn("1 cooled room isn't counted yet (1 has walls still being worked out)", summary["completeness"]["text"])
        # One list behind every screen's count: the room, why, and a plain reason.
        self.assertEqual([(row["name"], row["why"]) for row in summary["answer"]["rooms_not_counted"]],
                         [("Study", "finding_surfaces")])
        self.assertEqual(summary["answer"]["not_counted_text"], "1 has walls still being worked out")
        self.assertEqual(service._plain_count("76 finding(s) to review"), "76 findings to review")

    def test_a_room_left_out_and_too_small_is_one_row(self):
        summary = self.summary([room("m", "Master bedroom", 3.6, state="finding_surfaces", kw=None)])
        rows = [row for row in summary["todos"] if row["room"] and row["room"]["room_id"] == "m"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Master bedroom (3.6 m²) isn't in the total yet")
        self.assertIn("usually 9 m²", rows[0]["detail"])
        self.assertEqual([action["type"] for action in rows[0]["actions"]], ["link", "rename", "use", "cooled"])

    def test_a_kitchen_left_uncooled_names_the_open_plan_exception(self):
        summary = self.summary([room("k", "Kitchen", 29.2, "not_cooled", "not_cooled", None, use_id="house_living")])
        row = next(row for row in summary["todos"] if row["id"] == "kitchen-not-cooled:k")
        self.assertIn("exhaust hood", row["detail"])
        self.assertIn("open to a cooled living or dining area", row["detail"])
        self.assertEqual(row["actions"], [{"type": "cooled", "value": True, "label": "Cool it (open plan)"}])
        self.assertFalse(any(row["kind"] == "large_not_cooled" for row in summary["todos"]))

    def test_split_grouping_and_severity_order(self):
        rooms = [room("a", "Ensuite", 2, "not_cooled", "not_cooled", None),
                 room("b", "Ensuite (1)", 4, "not_cooled", "not_cooled", None),
                 room("c", "Ensuite (2)", 5.4, "not_cooled", "not_cooled", None),
                 room("d", "Patio", 13, kw=.5, use_id="outdoor"),
                 room("e", "Deck", 16, kw=2.2, use_id="outdoor")]
        summary = self.summary(rooms)
        self.assertEqual([row["id"] for row in summary["todos"][:2]], ["outdoor:e", "outdoor:d"])
        split = [row for row in summary["todos"] if row["kind"] == "split_room"]
        self.assertEqual(len(split), 1)
        self.assertIn("11.4 m² together", split[0]["title"])



class GlassItemTests(unittest.TestCase):
    def test_windows_left_off_the_load_are_one_item_with_their_glass_and_causes(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(service._glass_items(root), [])
            (root / "house_windows.json").write_text(json.dumps({"glass_coverage": {
                "in_load_m2": 61.08, "lost_m2": 21.32, "lost_windows": 6,
                "by_cause": {"no_room": {"windows": 4, "m2": 14.34}, "double_height": {"windows": 2, "m2": 6.98}}}}))
            [item] = service._glass_items(root)
        self.assertEqual(item["title"], "6 windows (21.3 m² of glass) aren't in the load")
        self.assertEqual(item["severity"], "changes_number")
        self.assertTrue(item["detail"].startswith("4 (14.3 m²): no traced room where its tag is"))
        self.assertIn("2 (7.0 m²): it is taller than its room's ceiling", item["detail"])
        self.assertIn("61.1 m² of glass is in the load", item["detail"])


class RobeJoinTests(unittest.TestCase):
    """A robe sharing a wall with exactly one bedroom is offered as a join into it (house research, 2026-11-28)."""

    @staticmethod
    def square(x, y, w, h):
        return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]

    def plan(self, robe_at):
        bedroom = {"taxonomy_id": "house_bedroom"}
        rooms = [{"room_id": "bed4", "name": "Bedroom 4", "level": "First Floor", "kind": "comfort", "area_m2": 16, "use": bedroom},
                 {"room_id": "bed3", "name": "Bedroom 3", "level": "First Floor", "kind": "comfort", "area_m2": 16, "use": bedroom},
                 {"room_id": "wir1", "name": "Walk in robe (1)", "level": "First Floor", "kind": "not_cooled", "area_m2": 3}]
        outlines = [{"room_id": "bed4", "page": 9, "points": self.square(0, 0, 400, 400)},
                    {"room_id": "bed3", "page": 9, "points": self.square(1000, 0, 400, 400)},
                    {"room_id": "wir1", "page": 9, "points": self.square(*robe_at, 150, 200)}]
        return {"outlines": outlines}, rooms

    def test_a_robe_beside_one_bedroom_is_offered_as_a_join(self):
        plan, rooms = self.plan((410, 0))            # a 10 px wall between the robe and Bedroom 4
        [item] = service._robe_items(plan, rooms)
        self.assertEqual(item["title"], "Walk in robe (1) opens off Bedroom 4")
        join = item["actions"][0]
        self.assertEqual((join["type"], join["room_ids"], join["name"]), ("join", ["bed4", "wir1"], "Bedroom 4"))
        # The joined room keeps the bedroom's use and stays cooled, whichever piece is larger.
        self.assertEqual((join["use"], join["cooled"]), ("house_bedroom", True))

    def test_a_robe_bigger_than_its_bedroom_is_not_offered(self):
        # Swapped names (house18: "Wir" 28.6 m² beside a 3.6 m² "Master bedroom"): joining would make the bedroom a robe.
        plan, rooms = self.plan((410, 0))
        rooms[2]["area_m2"] = 30
        self.assertEqual(service._robe_items(plan, rooms), [])

    def test_two_robes_off_one_bedroom_are_one_join(self):
        plan, rooms = self.plan((410, 0))
        rooms.append({"room_id": "wir2", "name": "Walk in robe (2)", "level": "First Floor", "kind": "not_cooled", "area_m2": 3})
        plan["outlines"].append({"room_id": "wir2", "page": 9, "points": self.square(410, 210, 150, 190)})
        [item] = service._robe_items(plan, rooms)
        self.assertEqual(item["title"], "Walk in robe (1) and Walk in robe (2) open off Bedroom 4")
        self.assertEqual(item["actions"][0]["room_ids"], ["bed4", "wir1", "wir2"])

    def test_a_robe_touching_no_bedroom_or_two_is_not_offered(self):
        plan, rooms = self.plan((700, 0))            # in the middle, touching neither
        self.assertEqual(service._robe_items(plan, rooms), [])
        plan, rooms = self.plan((410, 0))
        plan["outlines"][1]["points"] = self.square(570, 0, 400, 400)   # Bedroom 3 now on its other side
        self.assertEqual(service._robe_items(plan, rooms), [])

if __name__ == "__main__":
    unittest.main()
