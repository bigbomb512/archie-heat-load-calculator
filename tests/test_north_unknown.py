"""North unknown (no true north arrow, or the pages disagree): the sun on each outside wall and window is the average of
the four directions it could face (user decision 2026-11-03), labelled, and nothing is left blocked or omitted."""

import unittest

from ai import ai_preliminary, sun


class AveragedSunTests(unittest.TestCase):
    def setUp(self):
        self.site = ai_preliminary._site_sun({"confirmed_address": "Sydney NSW", "location": {"latitude_deg": -33.87, "longitude_deg": 151.21,
                                                          "timezone": "Australia/Sydney", "state": "NSW"}})
        self.scenarios = {"scenarios": [{"scenario_id": "jan", "sun_month": 1}, {"scenario_id": "jun", "sun_month": 6}]}

    def test_the_average_of_the_four_directions(self):
        averaged = ai_preliminary._averaged_surface_sun(self.site, self.scenarios, 90.0)
        each = [ai_preliminary._surface_sun(self.site, self.scenarios, 90.0, bearing) for bearing in (0.0, 90.0, 180.0, 270.0)]
        for scenario in ("jan", "jun"):
            for hour in range(24):
                self.assertAlmostEqual(averaged[scenario][hour], sum(row[scenario][hour] for row in each) / 4, places=3)
        # Between the extremes: not the worst direction, not zero.
        self.assertLess(max(averaged["jan"]), max(max(row["jan"]) for row in each))
        self.assertGreater(max(averaged["jan"]), 0)

    def test_without_site_sun_the_cardinal_profiles_are_averaged(self):
        envelope = ai_preliminary.load_pack()["preliminary_envelope"]
        profiles = envelope["cardinal_solar_profiles_w_m2"]
        averaged = ai_preliminary._averaged_cardinal_profile(envelope)
        self.assertAlmostEqual(averaged[15], sum(profiles[d][15] for d in "NESW") / 4, places=3)


class NoMajorityNorthTests(unittest.TestCase):
    def test_pages_that_disagree_settle_as_north_unknown(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from ai import autonomous_tasks
        from backend import autonomous_tasks_service as tasks
        with TemporaryDirectory() as folder:
            record = {"task": "P2_north", "target": "page-4", "packet": {"page": 4}, "retry_count": 1,
                      "accuracy": {"auto_apply": False}}
            with patch.object(autonomous_tasks, "true_north", return_value=(90.0, "true")), \
                    patch.object(autonomous_tasks, "north_consensus", return_value={"status": "disagreement"}), \
                    patch.object(tasks, "_north_pages", return_value=[{"page": 4}]):
                result = tasks._apply_p2(None, {"id": "x", "review_dir": folder}, Path(folder), record,
                                         {"found": True, "label": "true"})
        self.assertNotEqual(result["status"], "blocked")
        self.assertIsNone(result["applied_value"]["plan_up_azimuth_deg"])
        self.assertEqual(result["applied_value"]["north_basis"], "no_majority")


if __name__ == "__main__":
    unittest.main()
