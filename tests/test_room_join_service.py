import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend import (ai_preliminary_service as loads, contractor_report, job_plan_service, need_answers_service,
                     job_summary_service, quick_fix_service, room_join_service as joins)


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = {"id": "job", "review_dir": str(self.root)}
        self.rooms = [{"room_id": "a", "label": "Ensuite (1)", "name": "Ensuite (1)", "level": "First Floor",
                       "area_m2": 8, "use_id": "bathroom", "cooled": True, "people": 2},
                      {"room_id": "b", "label": "Ensuite (2)", "name": "Ensuite (2)", "level": "First Floor",
                       "area_m2": 4, "use_id": "study", "cooled": False, "people": 1}]
        (self.root / "internal_gains_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "a", "override": {"occupancy_count": {"value": 3, "reviewer": "old"}}},
            {"room_id": "b", "equipment": [{"origin": "typical_home_value", "name": "Fixed appliance"}],
             "override": {"occupancy_count": {"value": 1, "reviewer": "old"}}}]}))
        (self.root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "a"}, {"room_id": "b"}]}))

    def test_validation(self):
        with patch.object(quick_fix_service, "get", return_value={"rooms": self.rooms, "uses": {"bathroom": "Bathroom"}}):
            for ids in ([], ["a"], ["a", "a"], ["a", "missing"]):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    joins.join(None, self.project, {"room_ids": ids}, "Tester")
            with self.assertRaises(ValueError):
                joins.join(None, self.project, {"room_ids": ["a", "b"], "cooled": "yes"}, "Tester")
            with self.assertRaises(ValueError):
                joins.split(None, self.project, "missing", "Tester")
        with patch.object(quick_fix_service, "get", return_value={"rooms": [self.rooms[0], {**self.rooms[1], "level": "Ground"}], "uses": {}}):
            with self.assertRaises(ValueError):
                joins.join(None, self.project, {"room_ids": ["a", "b"]}, "Tester")

    def test_join_split_moves_people_and_restores_overrides(self):
        calls = []
        def save(_web, _project, data, _reviewer):
            calls.append(data)
            return {"applied": ["use and cooled unified"]}
        with patch.object(quick_fix_service, "get", return_value={"rooms": self.rooms, "uses": {"bathroom": "Bathroom"}}), \
             patch.object(quick_fix_service, "save", side_effect=save), \
             patch.object(need_answers_service, "apply", side_effect=lambda _web, _project, data: calls.append(data)), \
             patch.object(joins, "_apply_internal", side_effect=lambda *args: calls.append((args[2], args[3], args[4]))), \
             patch.object(joins, "_clear_internal", side_effect=lambda *args: calls.append((args[2], args[3], "clear"))), \
             patch.object(joins, "_restore_use", side_effect=lambda *args: calls.append((args[2], "restore-use"))):
            result = joins.join(None, self.project, {"room_ids": ["a", "b"], "people": 4}, "Tester")
            record = result["join"]
            self.assertEqual((record["name"], record["primary_id"], record["member_ids"]), ("Ensuite", "a", ["a", "b"]))
            self.assertEqual(calls[0]["rooms"], [{"room_id": "a", "cooled": True, "use": "bathroom"},
                                                   {"room_id": "b", "cooled": True, "use": "bathroom"}])
            self.assertTrue(any(isinstance(call, dict) and call.get("room") == "Ensuite (1)" and call.get("value") == 4 for call in calls))
            self.assertTrue(any(isinstance(call, dict) and call.get("room") == "Ensuite (2)" and call.get("value") == 0 for call in calls))
            self.assertIn(("b", "equipment", []), calls)
            self.assertEqual(joins.read(self.root)[0]["id"], record["id"])
            with self.assertRaises(ValueError):
                joins.join(None, self.project, {"room_ids": ["a", "b"]}, "Tester")
            with patch.object(joins, "_record", return_value={"override": {
                    "occupancy_count": {"note": f"room join {record['id']}"},
                    "equipment": {"note": f"room join {record['id']}"}}}):
                joins.split(None, self.project, record["id"], "Tester")
            self.assertEqual(joins.read(self.root), [])
            self.assertIn(("b", "occupancy_count", 1), calls)
            self.assertIn(("a", "occupancy_count", "clear"), calls)
            self.assertIn(("b", "equipment", "clear"), calls)

    def test_simultaneous_peak_and_month(self):
        def room(ident, values):
            return {"room_id": ident, "name": ident, "peak": {"design_total_kw": max(values)},
                    "hours": [{"hour": h, "design_total_kw": value,
                               "components": {"people": {"total_kw": value, "sensible_kw": value, "latent_kw": 0}}}
                              for h, value in enumerate(values)]}
        jan = {"scenario_id": "jan", "representative_month": "January", "rooms": [room("1", [5, 0] + [0]*22), room("2", [0, 4] + [0]*22)]}
        feb = {"scenario_id": "feb", "representative_month": "February", "rooms": [room("1", [3, 0] + [0]*22), room("2", [3, 0] + [0]*22)]}
        report = {"scenario_results": [jan, feb], "included_scope_peak": {"scenario_id": "jan"},
                  "room_identities": {"1": "a", "2": "b"}, "room_joins": [{"id": "join:x", "name": "Ensuite", "member_ids": ["a", "b"]}]}
        rows = loads._room_peaks(report)
        group = next(row for row in rows if row.get("source_room_id") == "join:x")
        self.assertEqual((group["design_total_kw"], group["month"], group["hour"]), (6, "February", 0))
        self.assertLess(group["design_total_kw"], 9)
        self.assertEqual(group["components"]["people"]["total_kw"], 6)
        self.assertTrue(all(row.get("join_id") == "join:x" for row in rows if row.get("source_room_id") in {"a", "b"}))

    def test_fingerprint_changes_on_join_and_split(self):
        path = self.root / "room_joins.json"
        before = loads._file_fingerprint(path, {})
        joins._write(self.root, [{"id": "join:x"}])
        after_join = loads._file_fingerprint(path, {})
        joins._write(self.root, [])
        self.assertNotEqual(before, after_join)
        self.assertNotEqual(after_join, loads._file_fingerprint(path, {}))

    def test_api_rejects_running_calculation_and_unknown_action(self):
        with patch("backend.calculation_service._JOB.status", return_value={"status": "running"}):
            with self.assertRaisesRegex(ValueError, "calculation is running"):
                joins.api(None, self.project, {"action": "join", "room_ids": ["a", "b"]}, "Tester")
        with patch("backend.calculation_service._JOB.status", return_value={"status": "idle"}):
            with self.assertRaisesRegex(ValueError, "join or split"):
                joins.api(None, self.project, {"action": "unknown"}, "Tester")

    def test_occupancy_answer_accepts_zero(self):
        with patch.object(need_answers_service, "options", return_value={"rooms": [{"label": "Ensuite (2)", "level": "First Floor"}]}), \
             patch.object(need_answers_service, "_internal_gains_override") as apply:
            answer = need_answers_service.apply(None, self.project, {"kind": "occupancy", "room": "Ensuite (2)",
                                                                      "value": 0, "reviewer": "Tester"})
        self.assertTrue(answer["applied"])
        self.assertEqual(apply.call_args.args[4], 0)

    def test_summary_action_and_contractor_one_row(self):
        rooms = [{"room_id": key, "name": name, "level": "First Floor", "area_m2": 4,
                  "kind": "not_cooled", "state": "not_cooled", "included": False, "final_kw": None}
                 for key, name in (("a", "Ensuite"), ("b", "Ensuite (1)"))]
        item = next(row for row in job_summary_service._room_items(rooms, "final_kw") if row["kind"] == "split_room")
        self.assertEqual(item["actions"][0], {"type": "join", "room_ids": ["a", "b"], "name": "Ensuite", "label": "Join into one room"})
        plan = {"rooms": [{**row, "join_id": "join:x", "join_name": "Ensuite"} for row in rooms],
                "joined_rooms": [{"id": "join:x", "name": "Ensuite", "member_ids": ["a", "b"],
                                  "area_m2": 8, "final_kw": 3, "cooled": True}]}
        self.assertFalse(any(row["kind"] == "split_room" for row in job_summary_service._room_items(job_summary_service._display_rooms(plan), "final_kw")))
        report = {"room_peaks": [{"source_room_id": "join:x", "name": "Ensuite", "design_total_kw": 3,
                                  "sensible_kw": 2, "hour": 15, "own_peak": {"month": "January"}}],
                  "scenario_results": [{"representative_month": "January", "rooms": [
                      {"name": "Ensuite", "peak": {"design_total_kw": 1, "sensible_kw": 1}},
                      {"name": "Ensuite (1)", "peak": {"design_total_kw": 2, "sensible_kw": 1}}]}]}
        plan["rooms"] = [{**row, "final_kw": 1} for row in plan["rooms"]]
        sizing = contractor_report.room_sizing(report, plan["rooms"])
        self.assertEqual([row["name"] for row in sizing], ["Ensuite"])
        inputs = {"material": {"hourly_load_model": {"rooms": [
            {"name": "Ensuite", "area_m2": 4, "occupancy": 1, "cooling_load": {"lighting_w_m2": 5, "outside_air_lps": 2}},
            {"name": "Ensuite (1)", "area_m2": 6, "occupancy": 0, "cooling_load": {"lighting_w_m2": 10, "outside_air_lps": 3}}]}}}
        values, _equipment = contractor_report.room_values(inputs, plan["rooms"])
        self.assertEqual(len(values), 1)
        self.assertEqual((values[0]["area_m2"], values[0]["lighting_w_m2"], values[0]["outside_air_lps"]), (10, 8, 5))

    def test_plan_projects_join_and_member_outlines(self):
        joins._write(self.root, [{"id": "join:x", "name": "Ensuite", "level": "First Floor",
                                  "member_ids": ["a", "b"], "primary_id": "a"}])
        (self.root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "a", "taxonomy_id": "house_wet", "taxonomy_label": "House wet area", "space_scope": "not_cooled"}]}))
        candidates = [{"key": key, "label": f"Ensuite ({index})", "level": "First Floor",
                       "area_m2": area, "include": True} for index, (key, area) in enumerate((("a", 8), ("b", 4)), 1)]
        report = {"room_peaks": [{"source_room_id": "join:x", "name": "Ensuite", "design_total_kw": 3}]}
        trace = {"room_id": "a", "room_label": "Ensuite (1)", "page": 1,
                 "points_image_px": [[0, 0], [10, 0], [10, 10], [0, 10]]}
        g = job_plan_service.reviewer_room_geometry_service
        with patch.object(job_plan_service.job_service, "status", return_value={"area_overrides": [], "results_ready": True, "result_stale": False}), \
             patch.object(job_plan_service.ai_preliminary_service, "get", return_value={
                 "room_scope": {"candidates": candidates}, "hourly_ai_preliminary_load_report": report}), \
             patch.object(g, "current_artifact_input", return_value={"records": [trace]}), \
             patch.object(g, "current_traced_areas", return_value={}), \
             patch.object(g, "_paths", return_value={}), \
             patch.object(g, "_page_context", return_value=[]), \
             patch("backend.autonomous_tasks_service._all_current", return_value=[]):
            plan = job_plan_service.get(SimpleNamespace(), self.project)
        self.assertEqual(plan["joined_rooms"][0]["area_m2"], 12)
        self.assertEqual(plan["joined_rooms"][0]["final_kw"], 3)
        self.assertEqual(plan["joined_rooms"][0]["final_w_m2"], 250)
        self.assertTrue(all(row["join_id"] == "join:x" for row in plan["rooms"]))
        self.assertTrue(all(row["use"]["taxonomy_id"] == "house_wet" for row in plan["rooms"]))
        self.assertEqual(plan["outlines"][0]["join_name"], "Ensuite")


if __name__ == "__main__":
    unittest.main()
