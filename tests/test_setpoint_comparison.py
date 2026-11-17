"""The indoor comparison is in-memory and uses the recorded baseline state."""
import copy
import unittest
from unittest.mock import patch

from ai.heat_loads import ashrae_saturation_pressure_kpa, humidity_ratio_from_db_wb, wet_bulb_from_db_rh
from backend import setpoint_comparison


class SetpointComparisonTests(unittest.TestCase):
    def test_thermodynamic_wet_bulb_round_trip_and_chart_point(self):
        pressure = 101.325
        # ASHRAE chart range used by the repository's independent heat-load
        # reference check: 40 °C DB / 20 °C WB at sea level gives about
        # 0.0065 kg/kg humidity ratio.
        chart_ratio = 0.0065
        chart_rh = (pressure * chart_ratio / (0.621945 + chart_ratio)) / ashrae_saturation_pressure_kpa(40)
        self.assertAlmostEqual(wet_bulb_from_db_rh(40, chart_rh, pressure), 20, delta=0.5)
        ratio = humidity_ratio_from_db_wb(24, 18, pressure, "thermodynamic")
        vapour = pressure * ratio / (0.621945 + ratio)
        rh = vapour / ashrae_saturation_pressure_kpa(24)
        self.assertAlmostEqual(rh, 0.56, delta=0.01)
        self.assertAlmostEqual(wet_bulb_from_db_rh(24, rh, pressure), 18, delta=0.01)
        self.assertAlmostEqual(wet_bulb_from_db_rh(22.5, rh, pressure), 16.73, delta=0.05)
        for invalid in (0, -0.2, 1.1):
            with self.assertRaises(ValueError):
                wet_bulb_from_db_rh(24, invalid, pressure)

    def test_variant_changes_only_indoor_conditions_and_never_mutates_source(self):
        model = {"rooms": [{"room_id": "r1", "indoor_cooling_setpoint_c": 24,
                            "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18},
                            "cooling_load": {"envelope_surfaces": [
                                {"surface_id": "to-bed", "boundary_method": "fixed_adjacent_temperature", "boundary_temperature_c": 24.0},
                                {"surface_id": "to-garage", "boundary_method": "fixed_adjacent_temperature", "boundary_temperature_c": 27.1},
                                {"surface_id": "outside", "boundary_method": "external", "boundary_temperature_c": None}]}}]}
        requirements = {"indoor_cooling_setpoint_c": 24, "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18}}
        original = {"input_fingerprint": "saved", "material": {"requirements": requirements,
                    "hourly_load_model": model, "design_day_scenarios": {"scenarios": [
                        {"atmospheric_pressure_kpa": 101.325}]}}}
        before = copy.deepcopy(original)
        result = {"included_scope_peak": {"final_design_total_kw": 10}}
        seen = []
        def calculate(variant, **_kwargs):
            seen.append(copy.deepcopy(variant))
            return result
        with patch("backend.setpoint_comparison.ai_preliminary.calculate", side_effect=calculate):
            answer = setpoint_comparison.compare(original, result)
        self.assertEqual(original, before)
        self.assertEqual([row["setpoint_c"] for row in answer["variants"]], [24, 22.5, 22.0])
        self.assertEqual(len(seen), 2)
        for variant in seen:
            restored = copy.deepcopy(variant)
            restored["material"]["requirements"]["indoor_cooling_setpoint_c"] = 24
            restored["material"]["requirements"]["cooling_load_conditions"]["indoor_cooling_wet_bulb_c"] = 18
            restored["material"]["hourly_load_model"]["rooms"][0]["indoor_cooling_setpoint_c"] = 24
            restored["material"]["hourly_load_model"]["rooms"][0]["cooling_load_conditions"]["indoor_cooling_wet_bulb_c"] = 18
            surfaces = {row["surface_id"]: row for row in variant["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_surfaces"]}
            # A wall to another cooled room follows the setpoint (no heat flows through it); the garage and outside don't.
            self.assertEqual(surfaces["to-bed"]["boundary_temperature_c"], variant["material"]["requirements"]["indoor_cooling_setpoint_c"])
            self.assertEqual(surfaces["to-garage"]["boundary_temperature_c"], 27.1)
            restored["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_surfaces"][0]["boundary_temperature_c"] = 24.0
            self.assertEqual(restored, original)

    def test_mixed_setpoints_are_not_silently_replaced(self):
        data = {"material": {"requirements": {"indoor_cooling_setpoint_c": 24,
                "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18}},
                "design_day_scenarios": {"scenarios": [{"atmospheric_pressure_kpa": 101.325}]},
                "hourly_load_model": {"rooms": [{"indoor_cooling_setpoint_c": 22,
                    "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18}}]}}}
        self.assertEqual(setpoint_comparison.compare(data, {"included_scope_peak": {"design_total_kw": 1}})["status"],
                         "not_calculated")


if __name__ == "__main__":
    unittest.main()
