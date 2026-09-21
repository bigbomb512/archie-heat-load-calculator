#!/usr/bin/env python3
"""Focused smoke checks for the separate heating calculation foundation."""

from pathlib import Path
from copy import deepcopy
import json
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.heating_gate import empty_heating_method_gate, heating_gate_fingerprint, heating_gate_is_approved, validate_heating_method_gate
from ai.heating_loads import calculate_heating_report, heating_air_load, heating_conduction, heating_glazing_conduction, heating_infiltration_load, heating_internal_gain_credit
from ai.glazing_gate import empty_glazing_method_gate, validate_glazing_method_gate
from ai.hourly_loads import build_hourly_load_model, validate_design_day_scenarios
from ai.design_requirements import validate_design_requirements
import backend.web_app as web_app


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed heating basis"}]


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
    room_peak = ready["scenario_results"][0]["rooms"][0]["peak"]
    check("approved heating gate enables review-ready scope", ready["status"] == "review_ready" and ready["project_peak"])
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
