import copy
import unittest

from ai.plant_hydraulics import (
    calculate_plant_report,
    empty_plant_method_gate,
    validate_hydraulic_circuits,
    validate_plant_systems,
)


def citation(reference="SYN-PLANT"):
    return [{"reference": reference, "page": 1, "excerpt": "Synthetic development fixture"}]


def ahu_report():
    def ahu(ahu_id, loads):
        return {
            "ahu_id": ahu_id,
            "name": ahu_id,
            "status": "review_ready",
            "hours": [{"hour": hour, "coil_total_kw": load} for hour, load in enumerate(loads)],
            "peak": {"plant_duty_kw": max(loads)},
        }

    return {
        "selected_ahu_ids": ["ahu_01", "ahu_02"],
        "scenario_results": [{
            "scenario_id": "summer",
            "status": "review_ready",
            "ahus": [ahu("ahu_01", [5.0, 0.0] + [5.0] * 22), ahu("ahu_02", [0.0, 3.0] + [3.0] * 22)],
        }],
    }


def plant_systems(duty_basis="combined_equipment", number_off=1):
    reviewed = {"review_status": "confirmed", "source": "Synthetic reviewed plant", "citations": citation()}
    return {"systems": [{
        **reviewed, "plant_id": "plant_01", "name": "Synthetic chiller", "plant_type": "chiller",
        "number_off": number_off, "duty_basis": duty_basis, "circuit_ids": ["circuit_01"],
        "served_ahu_ids": ["ahu_01", "ahu_02"], "diversity_factor": 0.5,
    }]}


def circuits():
    reviewed = {"review_status": "confirmed", "source": "Synthetic reviewed circuit", "citations": citation()}
    return {"circuits": [{
        **reviewed, "circuit_id": "circuit_01", "name": "Chilled water", "plant_id": "plant_01",
        "circuit_type": "chilled_water", "served_ahu_ids": ["ahu_01", "ahu_02"],
        "flow_lps": 100.0, "pumps": [{**reviewed, "pump_id": "pump_01", "number_off": 1, "power_kw": 0.2}],
        "pipe_effects": [{**reviewed, "pipe_id": "pipe_01", "effect_kw": 0.1}],
    }]}


class PlantHydraulicsTests(unittest.TestCase):
    def test_same_hour_coincidence_and_explicit_components(self):
        report = calculate_plant_report(ahu_report(), plant_systems(), circuits(), empty_plant_method_gate())
        self.assertEqual(report["status"], "draft")
        scenario = report["scenario_results"][0]
        self.assertEqual(scenario["plants"][0]["status"], "draft")
        hour_zero = scenario["plants"][0]["hours"][0]
        self.assertEqual(hour_zero["coincident_ahu_duty_kw"], 5.0)
        self.assertEqual(hour_zero["diversified_ahu_duty_kw"], 2.5)
        self.assertEqual(hour_zero["pump_power_kw"], 0.2)
        self.assertEqual(hour_zero["pipe_effect_kw"], 0.1)
        self.assertEqual(hour_zero["plant_duty_kw"], 2.8)
        self.assertEqual(scenario["plants"][0]["peak"]["display_hour"], 2)

    def test_approved_gate_enables_review_ready(self):
        gate = empty_plant_method_gate()
        gate.update({"approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng", "approved_at": "2026-09-18", "method_citation": "PLANT-01", "citations": citation("PLANT-01")})
        report = calculate_plant_report(ahu_report(), plant_systems(), circuits(), gate)
        self.assertEqual(report["status"], "review_ready")
        self.assertEqual(report["project_peak"], report["included_scope_peak"])

    def test_representative_number_off_multiplies_only_equipment_duty(self):
        report = calculate_plant_report(ahu_report(), plant_systems("representative_per_unit", 2), circuits(), empty_plant_method_gate())
        hour = report["scenario_results"][0]["plants"][0]["hours"][0]
        self.assertEqual(hour["plant_duty_kw"], 5.3)

    def test_non_cooling_plant_types_are_excluded_from_cooling_total(self):
        raw = plant_systems()
        boiler = copy.deepcopy(raw["systems"][0])
        boiler.update({"plant_id": "plant_boiler", "plant_type": "boiler", "circuit_ids": [], "served_ahu_ids": [], "diversity_factor": None})
        package = copy.deepcopy(raw["systems"][0])
        package.update({"plant_id": "plant_package", "plant_type": "package_unit", "circuit_ids": [], "served_ahu_ids": [], "diversity_factor": None})
        raw["systems"].extend([boiler, package])
        report = calculate_plant_report(ahu_report(), raw, circuits(), empty_plant_method_gate())
        excluded = {row["plant_id"]: row["status"] for row in report["scenario_results"][0]["plants"] if row["status"] == "excluded"}
        self.assertEqual(excluded, {"plant_boiler": "excluded", "plant_package": "excluded"})

    def test_duplicate_ahu_or_circuit_ownership_is_rejected(self):
        raw = plant_systems()
        second = copy.deepcopy(raw["systems"][0])
        second["plant_id"] = "plant_02"
        raw["systems"].append(second)
        with self.assertRaises(ValueError):
            validate_plant_systems(raw)
        circuits_raw = circuits()
        second_circuit = copy.deepcopy(circuits_raw["circuits"][0])
        second_circuit["circuit_id"] = "circuit_02"
        circuits_raw["circuits"].append(second_circuit)
        with self.assertRaises(ValueError):
            validate_hydraulic_circuits(circuits_raw, {"plant_01"})

    def test_invalid_pump_power_is_rejected(self):
        raw = circuits()
        raw["circuits"][0]["pumps"][0]["power_kw"] = -1
        with self.assertRaises(ValueError):
            validate_hydraulic_circuits(raw, {"plant_01"})


if __name__ == "__main__":
    unittest.main()
