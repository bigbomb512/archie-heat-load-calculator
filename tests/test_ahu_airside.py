import copy
import unittest

from ai.ahu_airside import (
    calculate_ahu_report,
    empty_air_side_method_gate,
    validate_ahu_systems,
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
    def test_single_zone_report_keeps_room_and_coil_views_separate(self):
        report = calculate_ahu_report({"scenario_results": [scenario()]}, systems(), air_side_model(), empty_air_side_method_gate())
        self.assertEqual(report["status"], "draft")
        result = report["scenario_results"][0]
        self.assertEqual(len(result["ahus"]), 1)
        hour = result["ahus"][0]["hours"][0]
        self.assertEqual(hour["room_load_excluding_central_outside_air"]["design_total_kw"], 3.0)
        self.assertGreater(hour["coil_total_kw"], 0)
        self.assertAlmostEqual(hour["flows_lps"]["outside_air"] + hour["flows_lps"]["return"], hour["flows_lps"]["supply"])

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
