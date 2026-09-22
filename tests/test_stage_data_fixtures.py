"""Development fixtures prove Stage 2, 5, and 7 plumbing without authorising production use."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from ai.glazing_calculation import calculate_glazing
from ai.glazing_gate import empty_glazing_method_gate, gate_is_approved, validate_glazing_method_gate
from ai.parity_harness import compare_stage6_benchmark_case, validate_benchmark_case
from ai.research_cache import eligible_bindings, source_pack_release_manifest
from ai.envelope import validate_envelope_library
from tools.seed_au_default_pack import inspect, seed


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class StageDataFixtureTests(unittest.TestCase):
    def load(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_complete_default_candidate_coverage_is_present_but_not_released(self):
        path = FIXTURES / "development_default_pack.json"
        report = inspect(path)
        self.assertEqual(report["missing_coverage"], [])
        self.assertEqual(report["candidate_count"], 8)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["environment"], "development_only")

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "research_cache.json"
            result = seed(cache_path, path)
            self.assertEqual(result["missing_coverage"], [])
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertTrue(cache["records"])
            self.assertTrue(all(row["review_status"] == "proposed" and not row["released"] for row in cache["records"]))
            self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "development_fixture"}))

        # The checked-in production authority is deliberately untouched.
        self.assertEqual(source_pack_release_manifest()["releases"], [])

    def test_complete_glazing_fixture_is_calculable_only_as_development_data(self):
        fixture = self.load("reviewed_glazing_solar_case.json")
        library = validate_envelope_library(fixture["library"])
        gate = validate_glazing_method_gate({**empty_glazing_method_gate(), **fixture["method_gate"]})
        self.assertFalse(gate_is_approved(gate))
        result = calculate_glazing(fixture["surface"], library["windows"][0], fixture["manual_solar"], indoor_temperature_c=24)
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["opening_area_m2"], 3.0)
        self.assertEqual(result["solar_gain_kw"], 0.54)
        self.assertEqual(fixture["environment"], "development_only")

    def test_synthetic_benchmark_compares_but_cannot_be_authorised(self):
        case = self.load("development_benchmark_case.json")
        validate_benchmark_case(case)
        actual = deepcopy(case["reference_results"])
        result = compare_stage6_benchmark_case(case, actual)
        self.assertEqual(result["stage6_validation"]["status"], "not_authorised")
        self.assertFalse(result["final_parity_allowed"])
        self.assertEqual(case["environment"], "development_only")


if __name__ == "__main__":
    unittest.main()
