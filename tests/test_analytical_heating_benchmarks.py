"""Fixed analytical checks for the separate room-heating calculation path."""

import json
import unittest
from pathlib import Path

from ai.heating_loads import (
    heating_air_load,
    heating_conduction,
    heating_infiltration_load,
    heating_internal_gain_credit,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "analytical_heating_benchmarks_v1.json"


def assert_benchmark(test, name, metric, expected, actual, tolerance):
    deviation = actual - expected
    test.assertLessEqual(
        abs(deviation), tolerance,
        f"{name} / {metric}: expected={expected}, actual={actual}, deviation={deviation}, tolerance={tolerance}",
    )


class AnalyticalHeatingBenchmarks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.cases = {case["case_id"]: case for case in cls.suite["cases"]}
        cls.tolerance = cls.suite["rounding_policy"]["absolute_tolerance_kw"]

    def check_component(self, case, actual):
        for metric in ("sensible_kw", "latent_kw", "total_kw"):
            assert_benchmark(self, case["case_id"], metric, case["expected"][metric], actual[metric], self.tolerance)
        self.assertAlmostEqual(
            actual["sensible_kw"] + actual["latent_kw"], actual["total_kw"],
            delta=self.tolerance,
            msg=f"{case['case_id']}: sensible plus latent must reconcile to total",
        )

    def test_opaque_transmission_matches_fixed_hand_calculation(self):
        case = self.cases["single_opaque_surface_heating_transmission"]
        actual, blockers = heating_conduction(
            case["inputs"]["surfaces"], case["inputs"]["outdoor_db_c"], case["inputs"]["indoor_db_c"]
        )
        self.assertEqual(blockers, [])
        self.check_component(case, actual)

    def test_standard_air_outside_air_matches_fixed_sensible_calculation(self):
        case = self.cases["outside_air_sensible_heating_standard_air_basis"]
        actual = heating_air_load(**case["inputs"])
        self.check_component(case, actual)
        assert_benchmark(self, case["case_id"], "mass_flow_kg_s", case["expected"]["mass_flow_kg_s"],
                         actual["inputs"]["mass_flow_kg_s"], 0.000001)
        self.assertEqual(actual["inputs"]["flow_reference_basis"], "standard_air_1_2kg_da_m3")
        self.assertEqual(actual["inputs"]["flow_reference_state"]["dry_air_density_kg_m3"], 1.2)

    def test_heating_component_clamps_warm_outdoor_air_and_preserves_zero_flow(self):
        for case_id in (
            "outside_air_warm_outdoor_state_has_no_heating_demand",
            "zero_outside_air_flow_has_zero_heating_demand",
        ):
            with self.subTest(case_id=case_id):
                case = self.cases[case_id]
                actual = heating_air_load(**case["inputs"])
                self.check_component(case, actual)

    def test_scheduled_ach_infiltration_matches_fixed_psychrometric_calculation(self):
        case = self.cases["scheduled_ach_infiltration_heating_at_outdoor_state"]
        actual = heating_infiltration_load(**case["inputs"])
        self.check_component(case, actual)
        expected = case["expected"]
        assert_benchmark(self, case["case_id"], "resolved_flow_lps", expected["resolved_flow_lps"],
                         actual["inputs"]["resolved_flow_lps"], 0.000001)
        assert_benchmark(self, case["case_id"], "applied_flow_lps", expected["applied_flow_lps"],
                         actual["inputs"]["applied_flow_lps"], 0.000001)
        assert_benchmark(self, case["case_id"], "outdoor_humidity_ratio_kg_kg",
                         expected["outdoor_humidity_ratio_kg_kg"],
                         actual["inputs"]["flow_reference_state"]["humidity_ratio"], 0.000000001)
        assert_benchmark(self, case["case_id"], "raw_signed_sensible_kw",
                         expected["raw_signed_sensible_kw"],
                         actual["inputs"]["raw_signed_sensible_kw"], 0.00000001)
        self.assertEqual(actual["inputs"]["flow_reference_basis"], "outdoor_design_condition")

    def test_scheduled_sensible_internal_gain_credit_is_capped(self):
        case = self.cases["scheduled_internal_sensible_credit_is_capped_at_gross_heating"]
        inputs = case["inputs"]
        profiles = {key: [value] * 24 for key, value in inputs["schedule_factors"].items()}
        actual, _ = heating_internal_gain_credit(
            inputs["room"], profiles, 0, inputs["gross_heating_sensible_kw"]
        )
        expected = case["expected"]
        for metric, expected_key in (("requested_credit_kw", "total_requested_credit_kw"),
                                     ("applied_credit_kw", "applied_credit_kw")):
            assert_benchmark(self, case["case_id"], metric, expected[expected_key],
                             actual["inputs"][metric], self.tolerance)
        self.assertEqual(actual["sensible_kw"], expected["sensible_kw"])
        self.assertEqual(actual["latent_kw"], expected["latent_kw"])
        self.assertEqual(actual["total_kw"], expected["total_kw"])
        by_type = {item["source_type"]: item["requested_kw"] for item in actual["inputs"]["sources"]}
        for source_type in ("people", "lighting", "equipment"):
            expected_key = f"{source_type}_requested_credit_kw"
            assert_benchmark(self, case["case_id"], f"{source_type}_requested_credit_kw",
                             expected[expected_key], by_type[source_type], self.tolerance)


if __name__ == "__main__":
    unittest.main()
