import unittest
from unittest.mock import patch

from backend import ac_design_input_service as service


class ACDesignInputTests(unittest.TestCase):
    def setUp(self):
        self.project = {"id": "p1", "name": "Example.pdf"}
        self.status = {"name": "Example", "found_site": "Found street", "dwelling": True,
                       "results_ready": True, "result_stale": False}
        self.peak = {"design_total_kw": 5, "sensible_kw": 4, "latent_kw": 1,
                     "month": "January", "display_hour": 17, "safety_factor": 1.1}
        self.report = {"included_scope_peak": self.peak,
                       "room_peaks": [{"source_room_id": "a", "name": "Alpha", "own_peak": {"kw": 3, "hour": 15, "month": "February",
                                                                                         "components": {"solar": {"total_kw": 1.2}}},
                                       "hourly": {"total_kw": [1] * 17 + [2] + [1] * 6},
                                       "components": {"solar": {"total_kw": 1.2}}},
                                      {"source_room_id": "b", "name": "Beta", "design_total_kw": 2}]}
        self.rooms = [{"room_id": "b", "name": "Beta", "level": "Upper", "area_m2": 12,
                       "included": True, "kind": "comfort", "use": {"taxonomy_id": "bedroom", "taxonomy_label": "Bedroom"}},
                      {"room_id": "a", "name": "Alpha", "level": "Lower", "area_m2": 20,
                       "included": True, "kind": "comfort", "occupancy": {"value": 2}},
                      {"room_id": "c", "name": "Closet", "level": "Lower", "area_m2": 4,
                       "included": False, "kind": "not_cooled"}]

    def call(self, final=None, base=None, joined=None):
        plan = {"status": self.status, "final_report": final if final is not None else self.report,
                "phase1_report": base or {}, "rooms": self.rooms, "joined_rooms": joined or []}
        with patch.object(service.job_plan_service, "get", return_value=plan), \
             patch.object(service.result_status_service, "gather", return_value={"items": [
                 {"kind": "typical", "text": "Typical people."},
                 {"kind": "typical", "text": "Typical people."},
                 {"kind": "to_find", "text": "Missing drawing."}]}), \
             patch.object(service.job_summary_service, "get", return_value={"todos": [
                 {"severity": "blocks", "title": "Update load"},
                 {"severity": "check", "title": "Check address"}]}):
            return service.api(None, self.project)

    def test_current_hourly_order_and_missing_values(self):
        result = self.call()
        self.assertEqual(result["status"], {"state": "current", "reason": "The saved cooling load is current.", "ready": True})
        self.assertEqual([r["id"] for r in result["rooms"]], ["a", "b"])
        self.assertEqual(result["rooms"][0]["peak"]["kw"], 3)
        self.assertEqual(result["rooms"][0]["peak"]["month"], "February")
        self.assertEqual(result["rooms"][0]["at_house_peak_kw"], 2)
        self.assertEqual(result["rooms"][0]["components_at_peak"], {"solar": 1.2})
        self.assertIsNone(result["rooms"][1]["at_house_peak_kw"])
        self.assertIsNone(result["rooms"][1]["people"])
        self.assertEqual(result["not_cooled"][0]["id"], "c")
        self.assertEqual(result["assumptions"], ["Typical people."])
        self.assertEqual(result["open_items"], {"count": 1, "titles": ["Update load"]})
        self.assertIsNone(result["design_conditions"]["indoor_db_c"])

    def test_a_room_meant_to_be_cooled_but_not_counted_is_not_filed_as_not_cooled(self):
        self.rooms.append({"room_id": "m", "name": "Master bedroom", "level": "Upper", "area_m2": 3.6,
                           "included": False, "kind": "comfort", "state": "finding_surfaces"})
        result = self.call()
        self.assertEqual([row["id"] for row in result["not_counted"]], ["m"])
        self.assertIn("walls and windows", result["not_counted"][0]["reason"])
        self.assertNotIn("m", [row["id"] for row in result["not_cooled"]])
        self.assertNotIn("m", [row["id"] for row in result["rooms"]])

    def test_indoor_conditions_come_from_the_saved_comparison_baseline(self):
        comparison = {"status": "calculated", "relative_humidity": 0.56,
                      "variants": [{"setpoint_c": 24.0, "baseline": True, "wet_bulb_c": 18.0, "relative_humidity": 0.56},
                                   {"setpoint_c": 22.5, "baseline": False, "wet_bulb_c": 17.0}]}
        from backend import setpoint_comparison_service
        with patch.object(setpoint_comparison_service, "cached", return_value=comparison):
            conditions = self.call()["design_conditions"]
        self.assertEqual((conditions["indoor_db_c"], conditions["indoor_wb_c"], conditions["indoor_rh"]), (24.0, 18.0, 0.56))
        with patch.object(setpoint_comparison_service, "cached", return_value=None):
            self.assertIsNone(self.call()["design_conditions"]["indoor_db_c"])

    def test_stale_still_exposes_saved_numbers(self):
        self.status["result_stale"] = True
        result = self.call()
        self.assertEqual(result["status"]["state"], "stale")
        self.assertFalse(result["status"]["ready"])
        self.assertTrue(result["status"]["reason"].startswith("Out of date:"))
        self.assertEqual(result["totals"]["peak_kw"], 5)
        self.assertEqual(result["rooms"][0]["peak"]["kw"], 3)

    def test_building_only_and_none(self):
        base = {"included_scope_peak": {"design_total_kw": 2, "display_hour": 13},
                "room_peaks": [{"source_room_id": "a", "design_total_kw": 1.5}]}
        result = self.call(final={}, base=base)
        self.assertEqual(result["status"]["state"], "building_only")
        self.assertEqual(result["rooms"][0]["peak"]["kw"], 1.5)
        self.assertIsNone(result["totals"]["suggested_system"])
        result = self.call(final={}, base={})
        self.assertEqual(result["status"]["state"], "none")
        self.assertIsNone(result["totals"]["peak_kw"])
        self.assertIsNone(result["rooms"][0]["peak"]["kw"])
        self.assertIsNone(result["rooms"][0]["hourly_kw"])

    def test_joined_room_replaces_members(self):
        self.report["room_peaks"].append({"source_room_id": "join:j1", "own_peak": {"kw": 4, "hour": 16, "month": "January"}})
        joined = [{"id": "j1", "name": "Combined", "level": "Lower", "member_ids": ["a", "b"],
                   "area_m2": 32, "cooled": True, "people": 3}]
        result = self.call(joined=joined)
        self.assertEqual([r["id"] for r in result["rooms"]], ["join:j1"])
        self.assertEqual(result["rooms"][0]["member_ids"], ["a", "b"])
        self.assertEqual(result["rooms"][0]["peak"]["kw"], 4)


if __name__ == "__main__":
    unittest.main()
