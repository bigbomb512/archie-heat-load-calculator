import unittest

from backend import contractor_report as report


class EngineerReportTests(unittest.TestCase):
    def test_signed_hourly_table_uses_saved_loads_and_keeps_negative_offsets(self):
        hours = [{"hour": hour, "components": {"envelope": {"total_kw": -1.0}},
                  "subtotal_sensible_kw": -1.0, "subtotal_latent_kw": 0.0, "design_total_kw": -1.0}
                 for hour in range(24)]
        source = {"included_scope_peak": {"scenario_id": "day", "hour": 15},
                  "scenario_results": [{"scenario_id": "day", "representative_month": "January",
                                        "included_scope_hours": hours}],
                  "seasonal": {"months": [{"scenario_id": "day", "hourly_kw": [-1.0] * 24}]}}
        day = report.design_day(source)
        self.assertEqual(day["actual_hours"][0]["total_kw"], -1)
        self.assertEqual(day["actual_hours"][0]["groups"]["conduction"], -1)
        html = report.engineer_tables_html({"design_day": day})
        self.assertIn("-1.0", html)
        self.assertIn("negative values offset cooling demand", html)

    def test_room_peak_and_missing_facts_are_explicit(self):
        result = {"scenario_results": [{"representative_month": "January", "rooms": [{
            "room_id": "r1", "name": "Room", "status": "calculated",
            "peak": {"design_total_kw": 2.0, "sensible_kw": 1.6, "latent_kw": 0.4, "hour": 15},
            "hours": [{"hour": 15, "design_total_kw": 2.0, "subtotal_sensible_kw": 1.6,
                       "subtotal_latent_kw": 0.4, "components": {"people": {
                           "sensible_kw": 1.6, "latent_kw": 0.4, "total_kw": 2.0}}}]}]}]}
        rows = report.engineer_room_details(result, {"material": {"hourly_load_model": {"rooms": [
            {"room_id": "r1", "name": "Room", "area_m2": 20, "cooling_load": {}}]}}},
            [{"name": "Room", "level": "Ground"}])
        self.assertEqual(rows[0]["peak"]["latent_kw"], 0.4)
        html = report.engineer_tables_html({"engineer_rooms": rows})
        self.assertIn("Room peak summary", html)
        self.assertIn("not recorded", html)
        self.assertIn("January 15:00", html)
        self.assertIn("People", html)                       # the report's own component labels, not raw keys

    def test_a_rooms_level_comes_from_its_floor_in_the_model(self):
        # Set 18's Corridor showed "Level: not recorded" when the level was looked up by display name (review 2026-11-17).
        result = {"scenario_results": [{"representative_month": "February", "rooms": [{
            "room_id": "r1", "name": "Corridor", "status": "calculated",
            "peak": {"design_total_kw": 0.5, "sensible_kw": 0.5, "latent_kw": 0.0, "hour": 15}, "hours": []}]}]}
        model = {"floors": [{"floor_id": "f1", "name": "First Floor"}], "zones": [{"zone_id": "z1", "floor_id": "f1"}],
                 "rooms": [{"room_id": "r1", "name": "Corridor", "zone_id": "z1", "area_m2": 15.7, "cooling_load": {}}]}
        rows = report.engineer_room_details(result, {"material": {"hourly_load_model": model}}, [{"name": "Corridors"}])
        self.assertEqual(rows[0]["level"], "First Floor")


if __name__ == "__main__":
    unittest.main()
