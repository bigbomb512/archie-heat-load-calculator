"""Public-method analytical regression cases; never engineering acceptance."""

import json
from copy import deepcopy
from pathlib import Path
import unittest

from ai.design_requirements import validate_design_requirements
from ai.glazing_calculation import glazing_conduction, manual_solar_transmission
from ai.heat_loads import (
    envelope_load,
    equipment_load,
    humidity_ratio_from_db_wb,
    infiltration_flow_lps,
    infiltration_load,
    lighting_load,
    outside_air_load,
    people_load,
)
from ai.hourly_loads import calculate_hourly_load_report
from tests.test_hourly_loads import library, requirements_data, reviewed_model, scenarios


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "analytical_cooling_benchmarks_v1.json"


def load_suite():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def assert_metric(test, case_id, metric, expected, actual, tolerance):
    deviation = actual - expected
    test.assertLessEqual(
        abs(deviation), tolerance,
        f"{case_id}/{metric}: expected {expected}, actual {actual}, deviation {deviation:+.8f} (tolerance {tolerance})",
    )


class AnalyticalCoolingBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite = load_suite()
        cls.cases = {case["case_id"]: case for case in cls.suite["cases"]}

    def check_load(self, case_id, result, expected=None):
        case = self.cases[case_id]
        expected = expected or case["expected"]
        tolerance = self.suite["rounding_policy"]["load_absolute_tolerance_kw"]
        for metric in ("sensible_kw", "latent_kw", "total_kw"):
            if metric in expected:
                assert_metric(self, case_id, metric, expected[metric], result[metric], tolerance)
        assert_metric(self, case_id, "sensible + latent reconciliation", result["total_kw"],
                      result["sensible_kw"] + result["latent_kw"], tolerance)

    def test_opaque_conduction_reference_case(self):
        case = self.cases["opaque_conduction_single_surface"]
        self.check_load(case["case_id"], envelope_load(**case["inputs"]))

    def test_conduction_reverses_sign_when_boundary_is_cooler(self):
        case = self.cases["opaque_conduction_single_surface"]
        inputs = case["inputs"]
        result = envelope_load(inputs["surfaces"], outdoor_db_c=20.0, indoor_db_c=24.0)
        assert_metric(self, case["case_id"], "cooler-boundary sensible_kw", -0.02,
                      result["sensible_kw"], self.suite["rounding_policy"]["load_absolute_tolerance_kw"])

    def test_people_reference_case_with_diversity(self):
        case = self.cases["people_sensible_latent_and_diversity"]
        self.check_load(case["case_id"], people_load(**case["inputs"]))

    def test_lighting_reference_case(self):
        case = self.cases["lighting_sensible_gain"]
        inputs = case["inputs"]
        result = lighting_load(inputs["area_m2"], inputs["lighting_w_m2"], inputs["diversity_factor"])
        self.check_load(case["case_id"], result)

    def test_equipment_reference_case(self):
        case = self.cases["equipment_heat_to_space_and_diversity"]
        self.check_load(case["case_id"], equipment_load(case["inputs"]["sources"]))

    def test_outdoor_air_sensible_latent_and_total_reference(self):
        case = self.cases["outside_air_psychrometric_load"]
        psych = self.suite["psychrometric_equations"]
        for label, db_key, wb_key in (("indoor", "indoor_db_c", "indoor_wb_c"),
                                      ("outdoor", "outdoor_db_c", "outdoor_wb_c")):
            actual = humidity_ratio_from_db_wb(case["inputs"][db_key], case["inputs"][wb_key], case["inputs"]["pressure_kpa"])
            assert_metric(self, case["case_id"], f"{label}_humidity_ratio",
                          case["expected"][f"{label}_humidity_ratio"], actual,
                          psych["humidity_ratio_absolute_tolerance"])
        result = outside_air_load(**case["inputs"])
        self.check_load(case["case_id"], result)
        self.assertEqual(result["inputs"]["flow_reference_basis"], "legacy_unverified")
        self.assertEqual(result["inputs"]["flow_reference_status"], "calculation_assumption_unverified")
        self.assertEqual(result["inputs"]["flow_reference_state"]["dry_bulb_c"], case["inputs"]["outdoor_db_c"])

    def test_standard_air_reference_uses_declared_dry_air_density(self):
        # Hand-calculated from the fixture's independently stored humidity ratios,
        # the moist-air enthalpy equation, and ASHRAE's 1.2 kg_da/m3 standard air.
        case = self.cases["outside_air_standard_air_reference"]
        result = outside_air_load(**case["inputs"])
        self.check_load(case["case_id"], result)
        self.assertAlmostEqual(result["inputs"]["mass_flow_kg_s"], case["expected"]["mass_flow_kg_s"], places=6)
        self.assertEqual(result["inputs"]["flow_reference_state"]["dry_air_density_kg_m3"], 1.2)
        self.assertEqual(result["inputs"]["flow_reference_status"], "declared_basis_unverified")

    def test_infiltration_reference_and_equivalent_flow_units(self):
        case = self.cases["infiltration_ach_flow_and_load"]
        inputs = dict(case["inputs"])
        expected_flow = inputs.pop("equivalent_flow_lps")
        flow = infiltration_flow_lps(inputs["value"], inputs["unit"], inputs["room_volume_m3"])
        assert_metric(self, case["case_id"], "ACH to L/s", expected_flow, flow, 1e-9)
        for value, unit in ((expected_flow, "L/s"), (expected_flow * 3.6, "m3/h"), (expected_flow / 1000, "m3/s")):
            assert_metric(self, case["case_id"], f"equivalent flow {unit}", expected_flow,
                          infiltration_flow_lps(value, unit), 1e-9)
        result = infiltration_load(
            case["inputs"]["value"], case["inputs"]["unit"],
            case["inputs"]["indoor_db_c"], case["inputs"]["indoor_wb_c"],
            case["inputs"]["outdoor_db_c"], case["inputs"]["outdoor_wb_c"], case["inputs"]["pressure_kpa"],
            room_volume_m3=case["inputs"]["room_volume_m3"], schedule_factor=case["inputs"]["schedule_factor"],
        )
        self.assertEqual(result["inputs"]["flow_reference_basis"], "outdoor_design_condition")
        self.assertEqual(result["inputs"]["flow_reference_status"], "calculation_assumption_unverified")
        psych = self.suite["psychrometric_equations"]
        for label, db, wb in (("indoor", case["inputs"]["indoor_db_c"], case["inputs"]["indoor_wb_c"]),
                              ("outdoor", case["inputs"]["outdoor_db_c"], case["inputs"]["outdoor_wb_c"])):
            actual_ratio = humidity_ratio_from_db_wb(db, wb, case["inputs"]["pressure_kpa"])
            assert_metric(self, case["case_id"], f"{label}_humidity_ratio",
                          case["expected"][f"{label}_humidity_ratio"], actual_ratio,
                          psych["humidity_ratio_absolute_tolerance"])
        expected = {key: case["expected"][key] for key in ("sensible_kw", "latent_kw", "total_kw")}
        self.check_load(case["case_id"], result, expected)

    def test_glazing_conduction_and_solar_reference(self):
        case = self.cases["glazing_conduction_and_solar_transmission"]
        inputs = case["inputs"]
        conduction = glazing_conduction(inputs["u_value_w_m2k"], inputs["opening_area_m2"],
                                        inputs["boundary_temperature_c"], inputs["indoor_temperature_c"])
        solar = manual_solar_transmission(inputs["incident_solar_w_m2"], inputs["corrected_glass_area_m2"],
                                          inputs["solar_transmission_factor"], inputs["external_shading_factor"],
                                          inputs["internal_shading_factor"])
        tolerance = self.suite["rounding_policy"]["glazing_absolute_tolerance_kw"]
        assert_metric(self, case["case_id"], "conduction_kw", case["expected"]["conduction_kw"], conduction, tolerance)
        assert_metric(self, case["case_id"], "solar_kw", case["expected"]["solar_kw"], solar, tolerance)
        assert_metric(self, case["case_id"], "total_kw", case["expected"]["total_kw"], conduction + solar, tolerance)

    def test_invalid_psychrometric_inputs_are_rejected(self):
        for case in self.suite["invalid_inputs"]:
            with self.subTest(case=case["case_id"]):
                with self.assertRaisesRegex(ValueError, case["expected_error_contains"]):
                    humidity_ratio_from_db_wb(case["dry_bulb_c"], case["wet_bulb_c"], case["pressure_kpa"])

    def test_non_finite_design_inputs_are_rejected_before_calculation(self):
        for field, setter in (
            ("indoor setpoint", lambda data, value: data.update(indoor_cooling_setpoint_c=value)),
            ("zone area", lambda data, value: data["zones"][0].update(area_m2=value)),
            ("outside-air flow", lambda data, value: data["zones"][0]["cooling_load"].update(outside_air_lps=value)),
            ("envelope U-value", lambda data, value: data["zones"][0]["cooling_load"]["envelope_surfaces"][0].update(u_value_w_m2k=value)),
        ):
            for invalid in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=field, value=invalid):
                    requirements = requirements_data()
                    setter(requirements, invalid)
                    with self.assertRaisesRegex(ValueError, "finite number"):
                        validate_design_requirements(requirements)

    def test_zero_gain_cases_remain_zero(self):
        results = [
            people_load(0, 75, 55, 1), lighting_load(20, 0, 1), equipment_load([]),
            envelope_load([], 35, 24), outside_air_load(0, 24, 18, 35, 24, 101.325),
        ]
        for result in results:
            with self.subTest(name=result["name"]):
                self.assertEqual((result["sensible_kw"], result["latent_kw"], result["total_kw"]), (0, 0, 0))

    def test_scheduled_room_case_matches_independent_components_and_peak(self):
        case = self.cases["scheduled_hourly_room_peak"]
        inputs = case["inputs"]
        requirements = requirements_data()
        zone = requirements["zones"][0]
        room = inputs["room"]
        requirements["indoor_cooling_setpoint_c"] = room["indoor_db_c"]
        requirements["cooling_load_conditions"].update({
            "indoor_cooling_wet_bulb_c": room["indoor_wb_c"],
            "outdoor_summer_db_c": inputs["weather_for_all_24_hours"]["outdoor_db_c"],
            "outdoor_summer_wet_bulb_c": inputs["weather_for_all_24_hours"]["outdoor_wb_c"],
            "atmospheric_pressure_kpa": inputs["pressure_kpa"],
        })
        zone.update({"area_m2": room["area_m2"], "occupancy": room["occupancy"]})
        zone["heat_sources"] = [{
            "name": source["name"], "quantity": source["quantity"], "watts": source["heat_to_space_w_each"],
            "kind": "refrigeration", "diversity_factor": source["diversity_factor"],
            "space_gain_factor": source["space_gain_factor"], "verification_status": "confirmed", "source": "Fixture input",
        } for source in inputs["equipment"]]
        cooling = zone["cooling_load"]
        cooling.update({
            "people_sensible_w_per_person": inputs["people_gain"]["sensible_w_per_person"],
            "people_latent_w_per_person": inputs["people_gain"]["latent_w_per_person"],
            "people_diversity_factor": inputs["people_gain"]["diversity_factor"],
            "lighting_w_m2": inputs["lighting_gain"]["w_per_m2"],
            "lighting_diversity_factor": inputs["lighting_gain"]["diversity_factor"],
            "outside_air_lps": inputs["outside_air_lps"], "safety_factor": room["safety_factor"],
        })
        surface = cooling["envelope_surfaces"][0]
        surface.update(inputs["envelope"][0])
        requirements = validate_design_requirements(requirements)
        model = reviewed_model(requirements)
        schedule_library = library()
        schedule_by_id = {item["schedule_id"]: item for item in schedule_library["schedules"]}
        for key, schedule_id in inputs["schedule_ids"].items():
            values = [inputs["schedule_profiles"][key]["default"]] * 24
            for hour in inputs["schedule_profiles"][key]["on_hours"]:
                values[hour] = 1.0
            schedule_by_id[schedule_id]["day_profiles"]["weekday"]["values"] = values
        scenario_bundle = scenarios()
        weather = inputs["weather_for_all_24_hours"]
        for point in scenario_bundle["scenarios"][0]["hours"]:
            point["outdoor_dry_bulb_c"]["value"] = weather["outdoor_db_c"]
            point["outdoor_wet_bulb_c"]["value"] = weather["outdoor_wb_c"]
        scenario_bundle["scenarios"][0]["atmospheric_pressure_kpa"]["value"] = inputs["pressure_kpa"]
        report = calculate_hourly_load_report(requirements, schedule_library, scenario_bundle, model, [inputs["scenario_id"]])
        self.assertEqual(report["status"], "review_ready", report.get("readiness"))
        result = report["scenario_results"][0]["rooms"][0]
        peak_hour = result["peak"]["display_hour"]
        self.assertEqual(peak_hour, case["expected"]["peak_hour"])
        peak_row = result["hours"][peak_hour]
        for metric, expected_key in (("subtotal_sensible_kw", "hour_14_sensible_kw"),
                                     ("subtotal_latent_kw", "hour_14_latent_kw"),
                                     ("subtotal_kw", "hour_14_subtotal_kw"),
                                     ("design_total_kw", "hour_14_design_total_kw")):
            assert_metric(self, case["case_id"], f"{peak_hour}/{metric}", case["expected"][expected_key],
                          peak_row[metric], self.suite["rounding_policy"]["load_absolute_tolerance_kw"])
        assert_metric(self, case["case_id"], "hour_15_subtotal_kw", case["expected"]["hour_15_subtotal_kw"],
                      result["hours"][15]["subtotal_kw"], self.suite["rounding_policy"]["load_absolute_tolerance_kw"])
        self.assertEqual(peak_row["components"]["people"]["sensible_kw"], 0.75)
        self.assertEqual(peak_row["components"]["people"]["latent_kw"], 0.55)
        self.assertEqual(peak_row["components"]["outside_air"]["sensible_kw"], 1.2398)
        self.assertEqual(peak_row["components"]["outside_air"]["latent_kw"], 1.0727)
        self.assertEqual(peak_row["components"]["solar"]["total_kw"], 1.5)

        standard_requirements = deepcopy(requirements)
        standard_requirements["zones"][0]["cooling_load"]["outside_air_flow_reference_basis"] = "standard_air_1_2kg_da_m3"
        standard_requirements = validate_design_requirements(standard_requirements)
        standard_report = calculate_hourly_load_report(
            standard_requirements, deepcopy(schedule_library), deepcopy(scenario_bundle),
            reviewed_model(standard_requirements), [inputs["scenario_id"]],
        )
        standard_outside_air = standard_report["scenario_results"][0]["rooms"][0]["hours"][peak_hour]["components"]["outside_air"]
        assert_metric(self, case["case_id"], "hourly standard-air sensible", 1.3279,
                      standard_outside_air["sensible_kw"], self.suite["rounding_policy"]["load_absolute_tolerance_kw"])
        assert_metric(self, case["case_id"], "hourly standard-air latent", 1.1489,
                      standard_outside_air["latent_kw"], self.suite["rounding_policy"]["load_absolute_tolerance_kw"])
        self.assertEqual(standard_outside_air["inputs"]["flow_reference_basis"], "standard_air_1_2kg_da_m3")
        self.assertEqual(standard_outside_air["inputs"]["flow_reference_state"]["dry_air_density_kg_m3"], 1.2)


if __name__ == "__main__":
    unittest.main()
