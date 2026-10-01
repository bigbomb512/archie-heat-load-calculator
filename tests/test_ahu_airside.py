import copy
import json
from pathlib import Path
import unittest

from ai.ahu_airside import (
    _coil_cooling_duty,
    _liquid_water_enthalpy_kj_kg,
    _mix_states,
    calculate_ahu_report,
    empty_air_side_method_gate,
    validate_ahu_systems,
    validate_air_side_model,
)


def citation(reference="SYN-AHU"):
    return [{"reference": reference, "page": 1, "excerpt": "Synthetic development fixture"}]


def scenario(scenario_id="summer"):
    return {
        "scenario_id": scenario_id,
        "atmospheric_pressure_kpa": 101.325,
        "hours": [
            {"hour": hour, "outdoor_dry_bulb_c": 35.0, "outdoor_wet_bulb_c": 23.0}
            for hour in range(24)
        ],
        "rooms": [
            {
                "room_id": "room_01",
                "zone_id": "zone_01",
                "hours": [
                    {
                        "hour": hour,
                        "subtotal_sensible_kw": 4.0,
                        "subtotal_latent_kw": 0.0,
                        "design_total_kw": 4.0,
                        "components": {
                            "outside_air": {"sensible_kw": 1.0, "latent_kw": 0.0, "total_kw": 1.0},
                            "internal": {"sensible_kw": 3.0, "latent_kw": 0.0, "total_kw": 3.0},
                        },
                    }
                    for hour in range(24)
                ],
            }
        ],
    }


def systems(number_off=1):
    return {
        "systems": [
            {
                "ahu_id": "ahu_01",
                "name": "Synthetic AHU",
                "system_type": "single_zone_constant_volume",
                "number_off": number_off,
                "served_zone_ids": ["zone_01"],
                "review_status": "confirmed",
                "source": "Synthetic reviewed system",
                "citations": citation(),
            }
        ]
    }


def air_side_model():
    review = {"review_status": "confirmed", "source": "Synthetic reviewed air-side input", "citations": citation()}
    return {
        "outside_air_ownership": "central_ahu",
        "airflow_records": [
            {**review, "ahu_id": "ahu_01", "path_type": "outside_air", "source_node": "outside_air", "destination_node": "mixed_air", "flow_lps": 40},
            {**review, "ahu_id": "ahu_01", "path_type": "return", "source_node": "room_return", "destination_node": "return_air", "flow_lps": 100, "state": {"dry_bulb_c": 24.0, "wet_bulb_c": 18.0}},
            {**review, "ahu_id": "ahu_01", "path_type": "supply", "source_node": "mixed_air", "destination_node": "supply_air", "flow_lps": 140},
        ],
        "fans": [{**review, "ahu_id": "ahu_01", "location": "supply", "heat_kw": 0.2}],
        "duct_effects": [{**review, "ahu_id": "ahu_01", "sensible_kw": 0.1}],
        "coils": [{**review, "ahu_id": "ahu_01", "leaving_db_c": 14.0, "leaving_wb_c": 11.0}],
    }


class AhuAirsideTests(unittest.TestCase):
    def test_ashrae_example4_mixed_air_uses_mass_weighted_psychrometric_properties(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "ahu_psychrometric_benchmarks_v1.json").read_text())
        case = fixture["cases"][0]
        states = [{
            "dry_bulb_c": row["dry_bulb_c"],
            "humidity_ratio": row["humidity_ratio_kg_kg"],
            "enthalpy_kj_kg": row["enthalpy_kj_kg"],
        } for row in case["streams"]]
        flows_lps = [row["flow_m3_s"] * 1000 for row in case["streams"]]
        actual = _mix_states(list(zip(states, flows_lps)), case["pressure_kpa"])
        expected = case["expected"]
        tolerances = case["tolerances"]

        def check(name, actual_value, expected_value, tolerance):
            deviation = actual_value - expected_value
            self.assertLessEqual(abs(deviation), tolerance,
                f"{case['id']} {name}: expected {expected_value}; actual {actual_value}; deviation {deviation}")

        check("outdoor mass flow", actual["dry_air_stream_mass_flows_kg_s"][0], expected["outdoor_dry_air_mass_flow_kg_s"], tolerances["mass_flow_kg_s"])
        check("return mass flow", actual["dry_air_stream_mass_flows_kg_s"][1], expected["return_dry_air_mass_flow_kg_s"], tolerances["mass_flow_kg_s"])
        check("mixed humidity ratio", actual["humidity_ratio"], expected["mixed_humidity_ratio_kg_kg"], tolerances["humidity_ratio_kg_kg"])
        check("mixed enthalpy", actual["enthalpy_kj_kg"], expected["mixed_enthalpy_kj_kg"], tolerances["enthalpy_kj_kg"])
        check("mixed dry-bulb", actual["dry_bulb_c"], expected["mixed_dry_bulb_c"], tolerances["dry_bulb_c"])
        check("published chart dry-bulb", actual["dry_bulb_c"], expected["source_chart_mixed_dry_bulb_c"], tolerances["source_chart_dry_bulb_c"])

    def test_ashrae_example3_coil_duty_includes_drained_condensate_enthalpy(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" / "ahu_psychrometric_benchmarks_v1.json").read_text())
        case = next(item for item in fixture["cases"] if item["id"] == "ashrae_f25_ch1_example3_cooling_dehumidification")
        inlet = {**case["inlet"], "humidity_ratio": case["inlet"]["humidity_ratio_kg_kg"]}
        leaving = {**case["leaving"], "humidity_ratio": case["leaving"]["humidity_ratio_kg_kg"]}
        actual = _coil_cooling_duty(
            inlet, leaving, case["volume_flow_m3_s_at_inlet"] * 1000, case["pressure_kpa"],
        )
        for metric, expected_key in (
            ("dry_air_mass_flow_kg_s", "dry_air_mass_flow_kg_s"),
            ("condensate_kg_s", "condensate_kg_s"),
            ("total_kw", "total_kw"), ("sensible_kw", "sensible_kw"), ("latent_kw", "latent_kw"),
        ):
            expected = case["expected"][expected_key]
            deviation = actual[metric] - expected
            self.assertLessEqual(abs(deviation), case["tolerances"][metric],
                f"{case['id']} {metric}: expected {expected}; actual {actual[metric]}; deviation {deviation}")
        self.assertEqual(actual["airflow_reference_basis"], "assumed_at_mixed_air_coil_inlet")
        self.assertEqual(actual["airflow_reference_state"]["dry_bulb_c"], inlet["dry_bulb_c"])
        self.assertEqual(actual["sensible_split_basis"], "dry_air_specific_heat_only")
        self.assertEqual(actual["sensible_specific_heat_kj_kg_da_k"], 1.006)
        self.assertEqual(_liquid_water_enthalpy_kj_kg(10), case["leaving"]["condensate_enthalpy_kj_kg"])

    def test_condensing_coil_blocks_freezing_and_unsupported_water_table_range(self):
        inlet = {"dry_bulb_c": 20, "humidity_ratio": 0.01, "enthalpy_kj_kg": 45}
        for temperature in (-1, 31):
            leaving = {"dry_bulb_c": temperature, "humidity_ratio": 0.005, "enthalpy_kj_kg": 20}
            with self.assertRaisesRegex(ValueError, "outside the supported 0–30°C"):
                _coil_cooling_duty(inlet, leaving, 1000, 101.325)

    def test_single_zone_report_keeps_room_and_coil_views_separate(self):
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), air_side_model(), empty_air_side_method_gate())
        self.assertEqual(report["status"], "draft")
        result = report["scenario_results"][0]
        self.assertEqual(len(result["ahus"]), 1)
        hour = result["ahus"][0]["hours"][0]
        self.assertEqual(hour["room_load_excluding_central_outside_air"]["design_total_kw"], 3.0)
        self.assertGreater(hour["coil_total_kw"], 0)
        split = hour["psychrometric_provenance"]["coil_sensible_split"]
        self.assertEqual(split["basis"], "dry_air_specific_heat_only")
        self.assertEqual(split["specific_heat_kj_kg_da_k"], 1.006)
        self.assertEqual(split["review_status"], "unvalidated_component_convention")
        self.assertAlmostEqual(hour["flows_lps"]["outside_air"] + hour["flows_lps"]["return"], hour["flows_lps"]["supply"])
        reference = hour["psychrometric_provenance"]["supply_airflow_reference"]
        self.assertEqual(reference["basis"], "assumed_at_mixed_air_coil_inlet")
        self.assertFalse(reference["verified"])
        self.assertIn("no explicit volumetric reference condition", reference["source"])

    def test_room_reconciliation_excludes_safety_allowance_and_does_not_change_coil_duty(self):
        unfactored = scenario()
        factored = scenario()
        for room in factored["rooms"]:
            for hour in room["hours"]:
                hour["safety_factor"] = 1.1
                hour["safety_allowance_kw"] = 0.4
                hour["design_total_kw"] = 4.4
        base = calculate_ahu_report({"scenario_results": [unfactored]}, systems(), air_side_model(), empty_air_side_method_gate())
        with_factor = calculate_ahu_report({"scenario_results": [factored]}, systems(), air_side_model(), empty_air_side_method_gate())
        base_hour = base["scenario_results"][0]["ahus"][0]["hours"][0]
        factored_hour = with_factor["scenario_results"][0]["ahus"][0]["hours"][0]
        self.assertEqual(factored_hour["room_load_excluding_central_outside_air"]["design_total_kw"], 3.0)
        self.assertEqual(factored_hour["room_load_excluding_central_outside_air"]["safety_basis"], "excludes_room_safety_allowance")
        self.assertEqual(factored_hour["coil_total_kw"], base_hour["coil_total_kw"])

    def test_active_make_up_air_blocks_unresolved_ahu_thermal_load(self):
        model = air_side_model()
        model["airflow_records"].append({
            "ahu_id": "ahu_01", "path_type": "make_up", "source_node": "make_up_air",
            "destination_node": "mixed_air", "flow_lps": 20,
            "review_status": "confirmed", "source": "Synthetic make-up schedule", "citations": citation(),
        })
        model["airflow_records"][-1]["schedule"] = [1.0] * 24
        model["airflow_records"][2]["flow_lps"] = 160
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
        self.assertEqual(report["status"], "blocked")
        reasons = [reason for row in report["scenario_results"][0]["blocked_ahus"] for reason in row["reasons"]]
        self.assertTrue(any("Positive make-up airflow is not included in V1 mixed-air psychrometrics" in reason for reason in reasons))

    def test_zero_scheduled_make_up_air_does_not_block_ahu_thermal_load(self):
        model = air_side_model()
        model["airflow_records"].append({
            "ahu_id": "ahu_01", "path_type": "make_up", "source_node": "make_up_air",
            "destination_node": "mixed_air", "flow_lps": 20,
            "schedule": [0.0] * 24,
            "review_status": "confirmed", "source": "Synthetic inactive make-up schedule", "citations": citation(),
        })
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
        self.assertEqual(report["status"], "draft")
        self.assertEqual(report["scenario_results"][0]["ahus"][0]["hours"][0]["flows_lps"]["make_up"], 0.0)

    def test_active_transfer_air_blocks_unresolved_ahu_thermal_load(self):
        model = air_side_model()
        model["airflow_records"].append({
            "ahu_id": "ahu_01", "path_type": "transfer", "source_node": "room_return",
            "destination_node": "mixed_air", "flow_lps": 20,
            "review_status": "confirmed", "source": "Synthetic transfer schedule", "citations": citation(),
        })
        model["airflow_records"][2]["flow_lps"] = 160
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
        self.assertEqual(report["status"], "blocked")
        reasons = [reason for row in report["scenario_results"][0]["blocked_ahus"] for reason in row["reasons"]]
        self.assertTrue(any("Positive transfer-air airflow is not included in V1 mixed-air psychrometrics" in reason for reason in reasons))

    def test_active_leakage_blocks_unresolved_ahu_thermal_load(self):
        model = air_side_model()
        model["leakage"] = [{
            "ahu_id": "ahu_01", "airflow_lps": 20, "source_node": "supply_air",
            "destination_node": "outside_air", "review_status": "confirmed",
            "source": "Synthetic leakage record", "citations": citation(),
        }]
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
        self.assertEqual(report["status"], "blocked")
        reasons = [reason for row in report["scenario_results"][0]["blocked_ahus"] for reason in row["reasons"]]
        self.assertTrue(any("Positive leakage airflow is not included in V1 mixed-air psychrometrics" in reason for reason in reasons))

    def test_active_exhaust_and_relief_block_unresolved_coil_airflow_basis(self):
        for path_type, source_node, destination_node in (
            ("exhaust", "return_air", "exhaust_air"),
            ("relief", "mixed_air", "relief_air"),
        ):
            with self.subTest(path_type=path_type):
                model = air_side_model()
                model["airflow_records"].append({
                    "ahu_id": "ahu_01", "path_type": path_type, "source_node": source_node,
                    "destination_node": destination_node, "flow_lps": 20,
                    "review_status": "confirmed", "source": f"Synthetic {path_type} schedule", "citations": citation(),
                })
                model["airflow_records"][2]["flow_lps"] = 120
                report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
                self.assertEqual(report["status"], "blocked")
                reasons = [reason for row in report["scenario_results"][0]["blocked_ahus"] for reason in row["reasons"]]
                self.assertTrue(any(f"Positive {path_type} airflow is not included in V1 mixed-air psychrometrics" in reason for reason in reasons))

    def test_upstream_fan_heat_blocks_until_applied_to_coil_inlet_state(self):
        for location in ("return", "mixed_air"):
            with self.subTest(location=location):
                model = air_side_model()
                model["fans"].append({
                    "ahu_id": "ahu_01", "location": location, "heat_kw": 0.25,
                    "review_status": "confirmed", "source": f"Synthetic {location} fan", "citations": citation(),
                })
                report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
                self.assertEqual(report["status"], "blocked")
                reasons = [reason for row in report["scenario_results"][0]["blocked_ahus"] for reason in row["reasons"]]
                self.assertTrue(any("Positive return- or mixed-air fan heat is not applied to the coil-inlet state" in reason for reason in reasons))

    def test_ahu_uses_and_reports_weather_evidence_retained_by_room_load(self):
        room_report = scenario()
        room_report.pop("hours")
        room_report.pop("atmospheric_pressure_kpa")
        provenance = {
            "scenario_id": "summer", "dry_bulb": {"value": 35.0, "source": "Design weather set", "status": "provisional", "citations": citation("WX-DB")},
            "wet_bulb": {"value": 23.0, "source": "Design weather set", "status": "provisional", "citations": citation("WX-WB")},
            "wet_bulb_basis": "thermodynamic", "pressure_kpa": 101.325,
            "pressure": {"value": 101.325, "source": "Station pressure", "status": "confirmed", "citations": citation("WX-P")},
        }
        room_report["rooms"][0]["hours"][0]["components"]["outside_air"]["inputs"] = {"outdoor_weather_provenance": provenance}
        # Replicate the retained hourly evidence across the full synthetic design day.
        for row in room_report["rooms"][0]["hours"]:
            row["components"]["outside_air"]["inputs"] = {"outdoor_weather_provenance": provenance}
        model = air_side_model()
        model["airflow_records"][1]["state"]["wet_bulb_basis"] = "psychrometer"
        model["coils"][0]["wet_bulb_basis"] = "thermodynamic"
        report = calculate_ahu_report({"scenario_results": [room_report]}, systems(), model, empty_air_side_method_gate())
        peak = report["scenario_results"][0]["ahus"][0]["peak"]["psychrometric_provenance"]
        self.assertEqual(peak["outdoor"]["dry_bulb"]["citations"][0]["reference"], "WX-DB")
        self.assertEqual(peak["outdoor"]["wet_bulb_basis"], "thermodynamic")
        self.assertEqual(peak["pressure"]["source"], "Station pressure")
        self.assertEqual(peak["return_air"]["wet_bulb_basis"], "psychrometer")
        self.assertEqual(peak["coil_leaving"]["wet_bulb_basis"], "thermodynamic")

    def test_number_off_multiplies_representative_duty(self):
        one = calculate_ahu_report({"scenario_results": [scenario()]}, systems(1), air_side_model(), empty_air_side_method_gate())
        two = calculate_ahu_report({"scenario_results": [scenario()]}, systems(2), air_side_model(), empty_air_side_method_gate())
        self.assertAlmostEqual(two["scenario_results"][0]["ahus"][0]["hours"][0]["design_total_kw"], 2 * one["scenario_results"][0]["ahus"][0]["hours"][0]["design_total_kw"], places=5)

    def test_duplicate_zone_assignment_is_rejected(self):
        raw = systems()
        raw["systems"].append(copy.deepcopy(raw["systems"][0]))
        raw["systems"][1]["ahu_id"] = "ahu_02"
        with self.assertRaises(ValueError):
            validate_ahu_systems(raw)

    def test_missing_return_state_blocks_air_side_result(self):
        model = air_side_model()
        del model["airflow_records"][1]["state"]
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), model, empty_air_side_method_gate())
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["blocked_ahus"])

    def test_v1_rejects_ambiguous_duplicate_singleton_airside_inputs(self):
        review = {"review_status": "confirmed", "source": "Second synthetic input", "citations": citation()}
        for kind, duplicate in (
            ("coils", {"leaving_db_c": 13.0, "leaving_wb_c": 10.0}),
            ("heat_recovery", {"sensible_effectiveness": 0.5, "latent_effectiveness": 0.0}),
            ("preconditioning", {"sensible_effectiveness": 0.2, "latent_effectiveness": 0.0,
                                 "reference_db_c": 22.0, "reference_wb_c": 16.0}),
        ):
            with self.subTest(kind=kind):
                model = air_side_model()
                model.setdefault(kind, [])
                model[kind].extend([{**review, "ahu_id": "ahu_01", **duplicate}] * 2)
                with self.assertRaisesRegex(ValueError, "multiple"):
                    validate_air_side_model(model, {"ahu_01"})

    def test_v1_rejects_multiple_return_air_state_records(self):
        model = air_side_model()
        model["airflow_records"].append({
            **model["airflow_records"][1], "flow_lps": 0,
            "state": {"dry_bulb_c": 22.0, "wet_bulb_c": 16.0},
        })
        with self.assertRaisesRegex(ValueError, "multiple return-air state records"):
            validate_air_side_model(model, {"ahu_01"})

    def test_calculated_air_paths_must_match_the_stream_used_by_v1(self):
        for index, source, destination in (
            (0, "room_return", "mixed_air"),
            (1, "outside_air", "return_air"),
            (2, "return_air", "supply_air"),
        ):
            with self.subTest(index=index):
                model = air_side_model()
                model["airflow_records"][index]["source_node"] = source
                model["airflow_records"][index]["destination_node"] = destination
                with self.assertRaisesRegex(ValueError, "must connect"):
                    validate_air_side_model(model, {"ahu_01"})

    def test_requested_missing_or_empty_scenarios_never_produce_a_partial_peak(self):
        for requested in (["summer", "missing"], []):
            with self.subTest(requested=requested):
                report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), air_side_model(),
                                              empty_air_side_method_gate(), scenario_ids=requested)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(report["project_peak"], {})
                self.assertTrue(report["blocked_ahus"])

    def test_empty_ahu_selection_does_not_expand_to_all_systems(self):
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), air_side_model(),
                                      empty_air_side_method_gate(), selected_ahu_ids=[])
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["project_peak"], {})

    def test_vav_can_serve_multiple_explicit_zones_at_same_hour(self):
        room_report = scenario()
        second = copy.deepcopy(room_report["rooms"][0])
        second["room_id"], second["zone_id"] = "room_02", "zone_02"
        room_report["rooms"].append(second)
        vav = systems()
        vav["systems"][0].update({"system_type": "vav", "served_zone_ids": ["zone_01", "zone_02"]})
        model = air_side_model()
        model["airflow_records"][2].update({"zone_id": "zone_01", "flow_lps": 70})
        model["airflow_records"].append({**model["airflow_records"][2], "zone_id": "zone_02", "flow_lps": 70})
        report = calculate_ahu_report({"scenario_results": [room_report]}, vav, model, empty_air_side_method_gate())
        self.assertEqual(len(report["scenario_results"][0]["ahus"][0]["room_ids"]), 2)
        self.assertEqual(report["scenario_results"][0]["ahus"][0]["system_type"], "vav")

    def test_approved_gate_can_produce_review_ready_report(self):
        gate = empty_air_side_method_gate()
        gate.update({"approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng", "approved_at": "2026-09-18", "method_citation": "AHU-01", "citations": citation("AHU-01")})
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), air_side_model(), gate)
        self.assertEqual(report["status"], "review_ready")

    def test_approved_report_stays_draft_when_any_selected_scenario_is_blocked(self):
        gate = empty_air_side_method_gate()
        gate.update({"approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng", "approved_at": "2026-09-18", "method_citation": "AHU-01", "citations": citation("AHU-01")})
        blocked = scenario("winter")
        blocked["hours"][0]["outdoor_wet_bulb_c"] = blocked["hours"][0]["outdoor_dry_bulb_c"] + 1
        report = calculate_ahu_report({"scenario_results": [scenario(), blocked]}, systems(), air_side_model(), gate)

        self.assertEqual(report["scenario_results"][0]["status"], "review_ready")
        self.assertEqual(report["scenario_results"][1]["status"], "blocked")
        self.assertEqual(report["status"], "draft")
        self.assertEqual(report["project_peak"], {})
        self.assertTrue(report["included_scope_peak"])
        self.assertTrue(any("every selected AHU and scenario" in warning for warning in report["warnings"]))

    def test_airflow_units_normalize_to_litres_per_second(self):
        raw = air_side_model()
        raw["airflow_records"][0].pop("flow_lps")
        raw["airflow_records"][0].update({"airflow_value": 0.04, "airflow_unit": "m3/s"})
        raw["airflow_records"][1].pop("flow_lps")
        raw["airflow_records"][1].update({"airflow_value": 360, "airflow_unit": "m3/h"})
        raw["airflow_records"][2].pop("flow_lps")
        raw["airflow_records"][2].update({"airflow_value": 140, "airflow_unit": "L/s"})
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), raw, empty_air_side_method_gate())
        self.assertEqual(report["scenario_results"][0]["ahus"][0]["hours"][0]["flows_lps"]["outside_air"], 40.0)
        self.assertEqual(report["scenario_results"][0]["ahus"][0]["hours"][0]["flows_lps"]["return"], 100.0)


if __name__ == "__main__":
    unittest.main()
