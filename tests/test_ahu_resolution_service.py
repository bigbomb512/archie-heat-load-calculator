import unittest

from backend.ahu_resolution_service import _attach_preliminary_weather


class AhuPreliminaryWeatherAdapterTests(unittest.TestCase):
    def test_attaches_raw_24_hour_weather_from_matching_draft_scenario(self):
        room_report = {"scenario_results": [{"scenario_id": "cooling", "rooms": [{"room_id": "r1"}]}]}
        input_set = {"material": {"design_day_scenarios": {"scenarios": [{
            "scenario_id": "cooling",
            "atmospheric_pressure_kpa": {"value": 100.8, "status": "provisional"},
            "hours": [{"hour": hour,
                       "outdoor_dry_bulb_c": {"value": 30 + hour / 10, "status": "provisional"},
                       "outdoor_wet_bulb_c": {"value": 21, "status": "provisional"}}
                      for hour in range(24)],
        }]}}}

        adapted = _attach_preliminary_weather(room_report, input_set)

        scenario = adapted["scenario_results"][0]
        self.assertEqual(scenario["atmospheric_pressure_kpa"], 100.8)
        self.assertEqual(len(scenario["hours"]), 24)
        self.assertEqual(scenario["hours"][0]["outdoor_wet_bulb_c"], 21)
        self.assertEqual(scenario["hours"][23]["hour"], 23)
        # Adapter operates on a copy; it does not mutate the source room report.
        self.assertNotIn("hours", room_report["scenario_results"][0])

    def test_unmatched_scenario_is_not_given_invented_weather(self):
        room_report = {"scenario_results": [{"scenario_id": "unknown", "rooms": []}]}
        adapted = _attach_preliminary_weather(room_report, {"material": {"design_day_scenarios": {"scenarios": []}}})
        self.assertNotIn("hours", adapted["scenario_results"][0])


if __name__ == "__main__":
    unittest.main()
