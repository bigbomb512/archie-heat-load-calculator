#!/usr/bin/env python3
"""Focused smoke checks for the separate heating calculation foundation."""

from pathlib import Path
from copy import deepcopy
import json
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.heating_gate import empty_heating_method_gate, heating_gate_fingerprint, heating_gate_is_approved, validate_heating_method_gate
from ai.heating_loads import calculate_heating_report, heating_air_load, heating_conduction, heating_glazing_conduction, heating_infiltration_load, heating_internal_gain_credit
from ai.glazing_gate import empty_glazing_method_gate, validate_glazing_method_gate
from ai.hourly_loads import build_hourly_load_model, validate_design_day_scenarios
from ai.design_requirements import validate_design_requirements
from ai.infiltration_gate import empty_infiltration_method_gate, validate_infiltration_method_gate
from ai import heating_loads as heating_loads_module
import backend.web_app as web_app
from ai import safety_factor_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed heating basis"}]


def apply_test_infiltration(room, *, status="calculated", verification="confirmed", unit="ACH", value=0.36):
    component = next(item for item in room["unapproved_components"] if item["component_type"] == "infiltration")
    component.update({
        "value": value, "unit": unit, "source": "Reviewed leakage test", "verification_status": verification,
        "citations": citation("INF-01"), "calculation_status": status,
        "method_id": "infiltration_psychrometric_v1", "air_path": "uncontrolled_infiltration",
        "flow_reference": "outdoor_design_condition",
    })
    room["ceiling_height_mm"] = 3000
    room["schedule_assignments"]["infiltration"] = "infiltration"
    return component


def approved_heating_gate():
    gate = empty_heating_method_gate()
    gate.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-17", "method_citation": "HM-01", "citations": citation("HM-01"),
    })
    return validate_heating_method_gate(gate)


def approved_glazing_gate():
    gate = empty_glazing_method_gate()
    gate.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-17", "method_citation": "GM-01", "citations": citation("GM-01"),
    })
    return validate_glazing_method_gate(gate)


def main():
    placeholder = validate_heating_method_gate(empty_heating_method_gate())
    check("placeholder heating gate is draft-only", not heating_gate_is_approved(placeholder))
    check("approved heating gate requires and records engineer approval", heating_gate_is_approved(approved_heating_gate()))

    surfaces = [{
        "surface_id": "wall-1", "boundary_method": "external", "area_m2": 20,
        "u_value_w_m2k": 0.5, "source": "Envelope schedule", "citations": citation("E-01"),
    }, {
        "surface_id": "partition-1", "boundary_method": "fixed_adjacent", "boundary_temperature_c": 12,
        "area_m2": 10, "u_value_w_m2k": 1.0, "source": "Partition schedule", "citations": citation("E-02"),
    }]
    result, blocked = heating_conduction(surfaces, -5, 20)
    check("opaque heating conduction uses indoor minus boundary", result["sensible_kw"] == 0.33 and not blocked)
    higher_area, _ = heating_conduction([{**surfaces[0], "area_m2": 40}], -5, 20)
    check("larger heating surface increases demand", higher_area["sensible_kw"] > result["sensible_kw"])

    room = {"cooling_load": {"glazing_surfaces": [{
        "surface_id": "window-1", "boundary_method": "external", "opening_width_m": 2,
        "opening_height_m": 1, "opening_quantity": 1, "window": {"u_value_w_m2k": 2.0},
        "source": "Opening schedule", "citations": citation("W-01"),
    }]}}
    glazing, glazing_blocked = heating_glazing_conduction(room, -5, 20, approved_glazing_gate())
    check("glazing heating uses opening area rather than glass area", glazing["sensible_kw"] == 0.1 and not glazing_blocked)
    blocked_glazing, reasons = heating_glazing_conduction(room, -5, 20, empty_glazing_method_gate())
    check("unapproved glazing gate excludes heating glazing", blocked_glazing["sensible_kw"] == 0 and reasons)

    outside = heating_air_load(10, 20, 15, 0, 0, 101.325)
    check("outside-air heating is positive when indoor exceeds outdoor", outside["sensible_kw"] > 0)
    infiltration = heating_infiltration_load(0.36, "ACH", 20, 15, 0, 0, 101.325, room_volume_m3=60, schedule_factor=1)
    direct = heating_infiltration_load(6, "L/s", 20, 15, 0, 0, 101.325, room_volume_m3=60, schedule_factor=1)
    off = heating_infiltration_load(0.36, "ACH", 20, 15, 0, 0, 101.325, room_volume_m3=60, schedule_factor=0)
    check("ACH and equivalent direct airflow match for heating", infiltration["sensible_kw"] == direct["sensible_kw"])
    check("infiltration schedule off produces zero heating", off["sensible_kw"] == 0)

    hours = [{
        "hour": hour,
        "outdoor_dry_bulb_c": {"value": 0, "status": "confirmed", "source": "Winter weather", "citations": citation("WX-01")},
        "outdoor_wet_bulb_c": {"value": -2, "status": "confirmed", "source": "Winter weather", "citations": citation("WX-01")},
    } for hour in range(24)]
    scenario = {"scenarios": [{
        "scenario_id": "winter_design", "title": "Winter design", "mode": "heating", "representative_month": "July",
        "day_type": "weekday", "status": "confirmed", "source": "Winter weather", "citations": citation("WX-01"),
        "atmospheric_pressure_kpa": {"value": 101.325, "status": "confirmed", "source": "Weather basis", "citations": citation("WX-01")},
        "hours": hours,
    }]}
    check("heating scenario accepts a complete cited 24-hour winter basis", len(validate_design_day_scenarios(scenario)["scenarios"][0]["hours"]) == 24)
    incomplete = {"scenarios": [{**scenario["scenarios"][0], "hours": hours[:23]}]}
    try:
        validate_design_day_scenarios(incomplete)
    except ValueError:
        check("incomplete heating weather is rejected", True)
    else:
        check("incomplete heating weather is rejected", False)

    requirements = validate_design_requirements({
        "updated_at": "requirements-1", "indoor_cooling_setpoint_c": 24, "outdoor_summer_db_c": 35,
        "indoor_heating_setpoint_c": 20,
        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18, "outdoor_summer_wet_bulb_c": 24, "atmospheric_pressure_kpa": 101.325, "verification_status": "confirmed", "source": "Cooling basis"},
        "zones": [{"zone_id": "zone-1", "name": "Zone", "usage": "Office", "source_room_labels": ["Room"], "area_m2": 20, "occupancy": 4,
                   "heat_sources": [], "cooling_load": {"people_sensible_w_per_person": 100, "people_latent_w_per_person": 50, "people_diversity_factor": 1, "lighting_w_m2": 10, "lighting_diversity_factor": 1, "outside_air_lps": 10, "safety_factor": 1.1, "envelope_not_applicable": False, "envelope_surfaces": [{"surface_id": "wall-1", "kind": "opaque_wall", "orientation": "N", "area_m2": 20, "u_value_w_m2k": 0.5, "solar_design_w_m2": 0, "solar_gain_factor": 0, "shading_factor": 0, "verification_status": "confirmed", "source": "Envelope"}], "verification_status": "confirmed", "source": "Cooling basis"}}],
    })
    model = build_hourly_load_model(requirements)
    model["floors"][0].update({"floor_id": "floor-1", "name": "Floor", "verification_status": "confirmed", "source": "Plan"})
    model["zones"][0].update({"floor_id": "floor-1", "verification_status": "confirmed", "source": "Zoning"})
    room = model["rooms"][0]
    room.update({"mapping_status": "confirmed", "verification_status": "confirmed", "source": "Plan", "area_m2": 20, "occupancy": 4,
                 "indoor_heating_setpoint_c": 20, "heating_applicability": "confirmed", "heating_setpoint_source": "Heating brief", "heating_citations": citation("HB-01"),
                 "heating_internal_gain_policy": "explicit_sensible_only", "heating_internal_gain_status": "confirmed", "heating_internal_gain_source": "Heating brief", "heating_internal_gain_citations": citation("HB-02"),
                 "heating_safety_factor": 1.1, "heating_safety_factor_source": "Heating brief", "heating_safety_factor_citations": citation("HB-03"),
                 "schedule_assignments": {"people": "people", "lighting": "lighting", "outside_air": "outside-air", "infiltration": "", "equipment": {}, "solar": {}}})
    room["citations"] = citation("A-101")
    room["cooling_load"]["envelope_surfaces"] = [{"surface_id": "wall-1", "boundary_method": "external", "area_m2": 20, "u_value_w_m2k": 0.5, "source": "Envelope", "citations": citation("E-01")}]
    room["cooling_load"].update({"outside_air_source": "Mechanical schedule M-01", "outside_air_verification_status": "confirmed"})
    for component in room["unapproved_components"]:
        component.update({"source": "Services review", "verification_status": "confirmed", "calculation_status": "not_present_confirmed", "citations": citation("SR-01")})

    def schedule(schedule_id, values):
        profile = {"values": values, "status": "confirmed", "source": "Schedule", "citations": citation("S-01")}
        return {"schedule_id": schedule_id, "title": schedule_id, "description": "", "status": "confirmed", "source": "Schedule", "citations": citation("S-01"), "day_profiles": {"weekday": profile, "saturday": {"values": [], "status": "not_applicable", "source": "Schedule", "citations": citation("S-01")}, "sunday_holiday": {"values": [], "status": "not_applicable", "source": "Schedule", "citations": citation("S-01")}}}

    active = [1.0] * 24
    schedules = {"schedules": [schedule("people", active), schedule("lighting", active), schedule("outside-air", active)]}
    winter = {"scenarios": [{"scenario_id": "winter", "title": "Winter", "mode": "heating", "representative_month": "July", "day_type": "weekday", "status": "confirmed", "source": "Weather", "citations": citation("WX-01"), "atmospheric_pressure_kpa": {"value": 101.325, "status": "confirmed", "source": "Weather", "citations": citation("WX-01")}, "hours": [{"hour": h, "outdoor_dry_bulb_c": {"value": 0, "status": "confirmed", "source": "Weather", "citations": citation("WX-01")}, "outdoor_wet_bulb_c": {"value": -2, "status": "confirmed", "source": "Weather", "citations": citation("WX-01")}} for h in range(24)]}]}
    draft = calculate_heating_report(requirements, schedules, winter, model, ["winter"], heating_gate=placeholder, glazing_gate=approved_glazing_gate())
    check("placeholder heating gate produces draft-only report", draft["status"] == "draft" and not draft["project_peak"])
    ready = calculate_heating_report(requirements, schedules, winter, model, ["winter"], heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate())
    reviewed_infiltration_gate = empty_infiltration_method_gate()
    reviewed_infiltration_gate.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-30", "method_citation": "IM-01", "scope": "Cooling infiltration method used as current heating gate basis.",
        "citations": citation("IM-01"),
    })
    reviewed_infiltration_gate = validate_infiltration_method_gate(reviewed_infiltration_gate)
    schedules_with_infiltration = deepcopy(schedules)
    schedules_with_infiltration["schedules"].append(schedule("infiltration", active))

    def two_room_case(change_first_room=None, case_schedules=schedules_with_infiltration):
        case_model = deepcopy(model)
        second_room = deepcopy(case_model["rooms"][0])
        second_room.update({"room_id": "room-2", "name": "Unaffected room", "source": "Plan room 2"})
        case_model["rooms"].append(second_room)
        if change_first_room:
            change_first_room(case_model["rooms"][0])
        case_report = calculate_heating_report(
            requirements, case_schedules, winter, case_model, ["winter"],
            heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate(),
            infiltration_gate=reviewed_infiltration_gate,
        )
        return case_model, case_report

    def duplicate_infiltration_path(target):
        apply_test_infiltration(target)
        duplicate_component = deepcopy(next(item for item in target["unapproved_components"] if item["component_type"] == "infiltration"))
        duplicate_component["component_id"] = "infiltration-extra"
        target["unapproved_components"].append(duplicate_component)

    _duplicate_model, duplicate_report = two_room_case(duplicate_infiltration_path)
    check("heating blocks multiple active infiltration paths without affecting other rooms", duplicate_report["scenario_results"][0]["rooms"][0]["status"] == "blocked" and "single declared infiltration air path" in " ".join(duplicate_report["scenario_results"][0]["rooms"][0]["blocked_reasons"]) and duplicate_report["scenario_results"][0]["rooms"][1]["status"] == "review_ready")

    def mark_infiltration_status(status):
        def change(target):
            component = next(item for item in target["unapproved_components"] if item["component_type"] == "infiltration")
            if status == "not_assessed":
                component.update({"calculation_status": status, "value": None, "unit": "", "source": "", "verification_status": "missing", "citations": [], "method_id": "", "air_path": "", "flow_reference": ""})
            else:
                component.update({"calculation_status": status, "value": 6, "unit": "L/s"})
        return change

    for state, blocker in (("not_assessed", "infiltration assessment"), ("stored_not_calculated", "infiltration calculation eligibility")):
        _case_model, case_report = two_room_case(mark_infiltration_status(state))
        case_rooms = case_report["scenario_results"][0]["rooms"]
        check(f"heating blocks {state} infiltration and leaves other rooms calculable", case_rooms[0]["status"] == "blocked" and blocker in case_rooms[0]["blocked_reasons"] and case_rooms[1]["status"] == "review_ready")

    def shared_air_schedule(target):
        apply_test_infiltration(target)
        target["schedule_assignments"]["infiltration"] = "outside-air"

    _case_model, shared_schedule_report = two_room_case(shared_air_schedule)
    shared_room = shared_schedule_report["scenario_results"][0]["rooms"][0]
    check("heating blocks infiltration that reuses the outside-air schedule", shared_room["status"] == "blocked" and any("dedicated infiltration schedule separate from the outside-air schedule" in reason for reason in shared_room["blocked_reasons"]) and shared_schedule_report["scenario_results"][0]["rooms"][1]["status"] == "review_ready")

    def missing_ach_volume(target):
        apply_test_infiltration(target)
        target["ceiling_height_mm"] = None

    _missing_volume_model, missing_volume_report = two_room_case(missing_ach_volume)
    missing_volume_room = missing_volume_report["scenario_results"][0]["rooms"][0]
    check("missing ACH volume blocks only the affected heating room", missing_volume_room["status"] == "blocked" and "reviewed room or zone ceiling height for ACH infiltration" in missing_volume_room["blocked_reasons"] and missing_volume_report["scenario_results"][0]["rooms"][1]["status"] == "review_ready")

    for label, change, expected in (
        ("missing outside-air review status", lambda target: target["cooling_load"].update({"outside_air_verification_status": "missing"}), "outside-air flow review status"),
        ("missing outside-air source", lambda target: target["cooling_load"].update({"outside_air_source": ""}), "outside-air flow source"),
    ):
        _case_model, airflow_report = two_room_case(change)
        airflow_room = airflow_report["scenario_results"][0]["rooms"][0]
        check(f"heating blocks {label} without affecting other rooms", airflow_room["status"] == "blocked" and expected in airflow_room["blocked_reasons"] and airflow_report["scenario_results"][0]["rooms"][1]["status"] == "review_ready")

    for label, change in (
        ("provisional infiltration", lambda target: apply_test_infiltration(target, verification="provisional")),
        ("provisional outside air", lambda target: target["cooling_load"].update({"outside_air_verification_status": "provisional"})),
        ("provisional room verification", lambda target: target.update({"verification_status": "provisional"})),
    ):
        _case_model, provisional_report = two_room_case(change)
        provisional_rooms = provisional_report["scenario_results"][0]["rooms"]
        check(f"{label} keeps heating report draft without a project peak", provisional_rooms[0]["status"] == "draft" and provisional_report["status"] == "draft" and not provisional_report["project_peak"] and provisional_rooms[1]["status"] == "review_ready")

    airflow_error_model = deepcopy(model)
    apply_test_infiltration(airflow_error_model["rooms"][0])
    second_airflow_room = deepcopy(airflow_error_model["rooms"][0])
    second_airflow_room.update({"room_id": "room-2", "name": "Unaffected room", "source": "Plan room 2"})
    airflow_error_model["rooms"].append(second_airflow_room)
    real_infiltration_calculation = heating_loads_module.heating_infiltration_load
    infiltration_calls = {"count": 0}

    def fail_first_infiltration_hour(*args, **kwargs):
        infiltration_calls["count"] += 1
        if infiltration_calls["count"] == 1:
            raise ValueError("synthetic missing-volume calculation failure")
        return real_infiltration_calculation(*args, **kwargs)

    with patch.object(heating_loads_module, "heating_infiltration_load", side_effect=fail_first_infiltration_hour):
        airflow_error_report = calculate_heating_report(
            requirements, schedules_with_infiltration, winter, airflow_error_model, ["winter"],
            heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate(),
            infiltration_gate=reviewed_infiltration_gate,
        )
    error_rooms = airflow_error_report["scenario_results"][0]["rooms"]
    check("hourly ValueError blocks only its heating room and preserves the next room", error_rooms[0]["status"] == "blocked" and "synthetic missing-volume calculation failure" in " ".join(error_rooms[0]["blocked_reasons"]) and error_rooms[1]["status"] == "review_ready")

    preliminary_fallback = safety_factor_resolution.resolve({})
    strict_fallback_policy = safety_factor_resolution.policy_for(preliminary_fallback, "heating", preliminary=False)
    strict_fallback_report = calculate_heating_report(requirements, schedules, winter, model, ["winter"], heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate(), safety_factor_policy=strict_fallback_policy)
    check("strict heating calculation blocks and withholds the peak for the unapproved preliminary fallback policy", ready["status"] == "review_ready" and strict_fallback_report["status"] == "blocked" and not strict_fallback_report["project_peak"] and not strict_fallback_report["safety_policy_applied"])
    policy_artifact = safety_factor_resolution.resolve({"heating_safety_factor": {
        "factor": 1.10, "source": "Engineer design brief",
        "citations": [{"reference": "Brief H-1", "page": 2, "excerpt": "Heating design margin"}],
    }})
    policy_artifact = safety_factor_resolution.approve(policy_artifact, "heating", "A. Engineer", "2026-09-30", "Final coincident heating total")
    policy = safety_factor_resolution.policy_for(policy_artifact, "heating")
    policy_model = deepcopy(model)
    policy_model["rooms"][0]["heating_safety_factor"] = 1.1
    policy_report = calculate_heating_report(requirements, schedules, winter, policy_model, ["winter"], heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate(), safety_factor_policy=policy)
    policy_room = policy_report["scenario_results"][0]["rooms"][0]
    check("heating policy mode neutralizes every room-hour factor", all(hour["safety_factor"] == 1.0 and hour["safety_allowance_kw"] == 0 for hour in policy_room["hours"]))
    policy_peak = policy_report["included_scope_peak"]
    check("heating policy mode applies approved factor once to coincident project peak", policy_report["status"] == "review_ready" and policy_peak["final_design_total_kw"] == round(policy_peak["raw_coincident_total_kw"] * 1.10, 4))
    check("approved heating policy retains cited room factor and neutralizes it", policy_report["legacy_room_safety_factors"][0]["factor"] == 1.1 and all(hour["safety_factor"] == 1.0 for hour in policy_room["hours"]))
    check("approved heating policy applies despite cited room factor above one", policy_report["safety_policy_applied"] and policy_report["status"] == "review_ready")
    room_peak = ready["scenario_results"][0]["rooms"][0]["peak"]
    check("approved heating gate enables review-ready scope", ready["status"] == "review_ready" and ready["project_peak"])
    heating_oa_inputs = room_peak["components"]["heating_outside_air"]["inputs"]
    heating_weather = heating_oa_inputs["weather_provenance"]
    check("heating outside-air result retains weather source, citations, pressure and basis", heating_oa_inputs["flow_source"] == "Mechanical schedule M-01" and heating_weather["dry_bulb"]["source"] == "Weather" and heating_weather["dry_bulb"]["citations"][0]["reference"] == "WX-01" and heating_weather["pressure"]["source"] == "Weather" and heating_weather["wet_bulb_basis"] == "legacy_unverified")
    check("legacy heating airflow reference remains explicit and unverified", heating_oa_inputs["flow_reference_basis"] == "legacy_unverified" and heating_oa_inputs["flow_reference_status"] == "calculation_assumption_unverified" and heating_oa_inputs["flow_reference_state"]["dry_bulb_c"] == 0)
    standard_heat = heating_air_load(100, 20, 15, 0, -2, 101.325, airflow_reference_basis="standard_air_1_2kg_da_m3")
    check("standard-air heating uses 1.2 kg dry air per cubic metre", standard_heat["inputs"]["mass_flow_kg_s"] == 0.12 and standard_heat["total_kw"] == 2.4144 and standard_heat["inputs"]["flow_reference_status"] == "declared_basis_unverified")
    infiltration_basis = heating_infiltration_load(0.5, "ACH", 20, 15, 0, -2, 101.325, room_volume_m3=100)["inputs"]
    check("heating infiltration airflow reference is explicit and unverified", infiltration_basis["flow_reference_basis"] == "outdoor_design_condition" and infiltration_basis["flow_reference_status"] == "calculation_assumption_unverified")
    check("internal sensible credit is retained and capped", room_peak["components"]["heating_internal_gain_credit"]["inputs"]["applied_credit_kw"] <= room_peak["components"]["heating_internal_gain_credit"]["inputs"]["requested_credit_kw"])
    check("latent and solar gains are never heating credits", room_peak["components"]["heating_internal_gain_credit"]["inputs"]["latent_credits_kw"] == 0 and room_peak["components"]["heating_internal_gain_credit"]["inputs"]["solar_credits_kw"] == 0)
    off_credit, _ = heating_internal_gain_credit(room, {"people": [0.0] * 24, "lighting": [0.0] * 24}, 0, 1.0)
    check("heating internal gains follow schedules", off_credit["inputs"]["applied_credit_kw"] == 0)
    check("heating safety is applied once after credit", room_peak["design_total_kw"] == round(room_peak["sensible_kw"] + room_peak["safety_allowance_kw"], 4) and room_peak["safety_allowance_kw"] == round(room_peak["sensible_kw"] * 0.1, 4))
    check("room, zone, floor and project heating rollups exist", bool(ready["scenario_results"][0]["zones"]) and bool(ready["scenario_results"][0]["floors"]) and ready["project_peak"])
    missing_safety = deepcopy(model)
    missing_safety["rooms"][0]["heating_safety_factor"] = None
    blocked_safety = calculate_heating_report(requirements, schedules, winter, missing_safety, ["winter"], heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate())
    check("missing heating safety factor blocks complete scope", blocked_safety["status"] == "blocked" and not blocked_safety["project_peak"])
    unresolved_equipment = deepcopy(model)
    unresolved_equipment["rooms"][0]["heat_sources"] = [{"source_id": "equipment-1", "heating_credit_status": "not_assessed"}]
    blocked_equipment = calculate_heating_report(requirements, schedules, winter, unresolved_equipment, ["winter"], heating_gate=approved_heating_gate(), glazing_gate=approved_glazing_gate())
    check("unreviewed equipment heat remains excluded and blocks complete scope", blocked_equipment["status"] == "blocked" and any("equipment equipment-1 heating credit is unresolved" in issue["reasons"] for issue in blocked_equipment["scenario_results"][0]["scope_summary"]["blocked_rooms"]))

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        project = {"id": "heating-test", "review_dir": str(root), "hourly_heating_load_report": str(root / "hourly_heating_load_report.json")}
        paths = web_app.hourly_paths(project)
        for key in ("schedules", "scenarios", "model", "envelope_library", "envelope_model"):
            paths[key].write_text(json.dumps({"updated_at": "v1"}), encoding="utf-8")
        paths["evidence_fusion"].write_text(json.dumps({"fingerprint": "e1"}), encoding="utf-8")
        gate = empty_heating_method_gate()
        report_fingerprints = {"schedule_library_updated_at": "v1", "design_day_scenarios_updated_at": "v1", "hourly_load_model_updated_at": "v1", "heating_method_gate_fingerprint": heating_gate_fingerprint(gate), "envelope_library_updated_at": "v1", "envelope_model_updated_at": "v1", "infiltration_method_gate_updated_at": "", "glazing_method_gate_updated_at": "", "evidence_fusion_fingerprint": "e1"}
        paths["heating_report"].write_text(json.dumps({"input_fingerprints": report_fingerprints}), encoding="utf-8")
        check("unchanged heating dependencies remain current", web_app.heating_report_stale_reasons(project) == [])
        paths["model"].write_text(json.dumps({"updated_at": "v2"}), encoding="utf-8")
        check("heating room-model edits produce a specific stale reason", "heating room inputs changed" in web_app.heating_report_stale_reasons(project))


if __name__ == "__main__":
    main()
