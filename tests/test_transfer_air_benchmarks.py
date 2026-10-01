"""Independent analytical checks for the draft transfer-air load arithmetic."""

import json
from pathlib import Path
import unittest

from ai.heat_loads import transfer_air_load


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "transfer_air_benchmarks_v2.json"


class TransferAirBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.case = cls.suite["case"]

    def test_draft_transfer_air_matches_fixed_analytical_case(self):
        case = self.case
        inputs = dict(case["inputs"])
        inputs.pop("flow_reference")
        result = transfer_air_load(
            inputs.pop("flow_value"), inputs.pop("flow_unit"),
            inputs.pop("source_db_c"), inputs.pop("source_wb_c"),
            inputs.pop("target_db_c"), inputs.pop("target_wb_c"),
            inputs.pop("pressure_kpa"),
            source_wet_bulb_basis=inputs.pop("source_wet_bulb_basis"),
            target_wet_bulb_basis=inputs.pop("target_wet_bulb_basis"),
        )
        expected = case["expected"]
        tol = self.suite["rounding_tolerance_kw"]
        for metric in ("sensible_kw", "latent_kw", "total_kw"):
            actual = result[metric]
            deviation = actual - expected[metric]
            self.assertLessEqual(
                abs(deviation), tol,
                f"{case['case_id']}/{metric}: expected {expected[metric]}, actual {actual}, deviation {deviation:+.8f}",
            )
        self.assertAlmostEqual(result["inputs"]["mass_flow_kg_s"], expected["mass_flow_kg_s"], places=8)
        self.assertEqual(result["inputs"]["flow_reference"], case["inputs"]["flow_reference"])
        self.assertAlmostEqual(result["total_kw"], result["sensible_kw"] + result["latent_kw"], places=4)

    def test_reversing_source_and_target_reverses_exchange_sign(self):
        inputs = self.case["inputs"]
        result = transfer_air_load(
            inputs["flow_value"], inputs["flow_unit"],
            inputs["target_db_c"], inputs["target_wb_c"],
            inputs["source_db_c"], inputs["source_wb_c"], inputs["pressure_kpa"],
            source_wet_bulb_basis=inputs["target_wet_bulb_basis"],
            target_wet_bulb_basis=inputs["source_wet_bulb_basis"],
        )
        self.assertLess(result["sensible_kw"], 0)
        self.assertLess(result["latent_kw"], 0)


if __name__ == "__main__":
    unittest.main()
