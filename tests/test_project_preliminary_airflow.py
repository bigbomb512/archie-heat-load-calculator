from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.project_preliminary_airflow import calculate_additive_fallback_outside_air


class AdditiveFallbackOutsideAirTests(unittest.TestCase):
    def test_hand_calculated_additive_case(self):
        result = calculate_additive_fallback_outside_air(20, 100)
        self.assertEqual(result["components_lps"], {"people": 150.0, "area": 30.0})
        self.assertEqual(result["total_lps"], 180.0)
        self.assertEqual(result["status"], "provisional")

    def test_known_occupancy_with_unresolved_area_is_partial_only(self):
        result = calculate_additive_fallback_outside_air(140, None)
        self.assertEqual(result["components_lps"]["people"], 1050.0)
        self.assertIsNone(result["components_lps"]["area"])
        self.assertEqual(result["known_component_sum_lps"], 1050.0)
        self.assertIsNone(result["total_lps"])
        self.assertEqual(result["unresolved_fields"], ["area_m2"])
        self.assertEqual(result["status"], "incomplete")

    def test_zero_inputs_are_valid_known_values(self):
        result = calculate_additive_fallback_outside_air(0, 0)
        self.assertEqual(result["total_lps"], 0.0)
        self.assertEqual(result["unresolved_fields"], [])

    def test_missing_occupancy_does_not_become_a_final_total(self):
        result = calculate_additive_fallback_outside_air(None, 50)
        self.assertEqual(result["components_lps"]["area"], 15.0)
        self.assertIsNone(result["total_lps"])

    def test_invalid_operands_are_rejected(self):
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                calculate_additive_fallback_outside_air(value, 10)


if __name__ == "__main__":
    unittest.main()
