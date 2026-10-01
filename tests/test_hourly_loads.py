#!/usr/bin/env python3

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.web_app as web_app
from ai.design_requirements import validate_design_requirements
from ai.heat_loads import infiltration_load
from ai.hourly_loads import (
    build_hourly_load_model,
    calculate_hourly_load_report,
    validate_design_day_scenarios,
    validate_hourly_load_model,
    validate_schedule_library,
)
from ai.infiltration_gate import empty_infiltration_method_gate, validate_infiltration_method_gate
from ai.moisture_loads import empty_moisture_method_gate, validate_moisture_method_gate
from ai import safety_factor_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def requirements_data():
    return {
        "indoor_cooling_setpoint_c": 24, "outdoor_summer_db_c": 35,
        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18, "outdoor_summer_wet_bulb_c": 24, "atmospheric_pressure_kpa": 101.325, "verification_status": "confirmed", "source": "Engineer conditions"},
        "zones": [{
            "zone_id": "zone_001", "name": "Retail", "usage": "Retail", "source_room_labels": ["Retail"], "area_m2": 20, "occupancy": 10,
            "heat_sources": [{"name": "Fridge", "quantity": 1, "watts": 1000, "kind": "refrigeration", "diversity_factor": 1, "space_gain_factor": 1, "verification_status": "confirmed", "source": "Equipment schedule"}],
            "cooling_load": {"people_sensible_w_per_person": 75, "people_latent_w_per_person": 55, "people_diversity_factor": 1, "lighting_w_m2": 10, "lighting_diversity_factor": 1, "outside_air_lps": 100, "safety_factor": 1.1, "envelope_not_applicable": False, "verification_status": "confirmed", "source": "Cooling basis", "envelope_surfaces": [{"surface_id": "north", "kind": "glazing", "orientation": "N", "area_m2": 10, "u_value_w_m2k": 0.5, "solar_design_w_m2": 500, "solar_gain_factor": 0.6, "shading_factor": 0.5, "verification_status": "confirmed", "source": "Facade basis"}]},
        }],
    }


def profile(values, status="confirmed"):
    return {"values": values, "status": status, "source": "Engineer schedule", "citations": []}


def library(status="confirmed"):
    values = [0.0] * 24
    values[14] = 1.0
    return {"schedules": [{"schedule_id": name, "title": name, "description": "", "status": status, "source": "Engineer schedule", "citations": [], "day_profiles": {"weekday": profile(values, status), "saturday": profile([], "missing"), "sunday_holiday": profile([], "missing")}} for name in ("people", "lights", "air", "infil", "fridge", "solar")]}


def scenarios(mode="cooling", status="confirmed"):
    citation = [{"reference": "Weather source W-1", "page": 1, "excerpt": "Hourly dry- and wet-bulb values."}]
    hours = [{"hour": hour, "outdoor_dry_bulb_c": {"value": 35, "status": status, "source": "Weather sequence", "citations": citation}, "outdoor_wet_bulb_c": {"value": 24, "status": status, "source": "Weather sequence", "citations": citation}} for hour in range(24)]
    return {"scenarios": [{"scenario_id": "jan_weekday", "title": "January weekday", "mode": mode, "representative_month": "January", "day_type": "weekday", "status": status, "source": "Weather sequence", "citations": [], "atmospheric_pressure_kpa": {"value": 101.325, "status": status, "source": "Weather sequence", "citations": []}, "hours": hours}]}


def reviewed_model(requirements):
    model = build_hourly_load_model(requirements)
    model["floors"][0].update({"floor_id": "level_01", "name": "Level 1", "elevation_m": 0, "verification_status": "confirmed", "source": "Architectural drawing A-101"})
    model["zones"][0].update({"floor_id": "level_01", "verification_status": "confirmed", "source": "Engineer zoning decision"})
    room = model["rooms"][0]
    room["mapping_status"] = "confirmed"
    room["verification_status"] = "confirmed"
    room["source"] = "Engineer reviewed room map"
    room["schedule_assignments"] = {"people": "people", "lighting": "lights", "outside_air": "air", "equipment": {room["heat_sources"][0]["source_id"]: "fridge"}, "solar": {"north": "solar"}}
    for component in room["unapproved_components"]:
        component.update({
            "value": None, "unit": "", "source_room_id": "", "source": "Engineer room-services review",
            "citations": [], "verification_status": "confirmed", "calculation_status": "not_present_confirmed",
        })
    return model


def approved_infiltration_gate():
    gate = empty_infiltration_method_gate()
    gate.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-09", "method_citation": "Project infiltration method IM-01",
        "scope": "Cooling infiltration sensible and latent load only.",
        "citations": [{"reference": "IM-01", "page": 1, "excerpt": "Approved method"}],
    })
    return validate_infiltration_method_gate(gate)


def calculated_infiltration(model, value, unit="ACH", status="confirmed"):
    room = model["rooms"][0]
    room["ceiling_height_mm"] = 3000
    room["schedule_assignments"]["infiltration"] = "infil"
    component = next(item for item in room["unapproved_components"] if item["component_type"] == "infiltration")
    component.update({
        "value": value, "unit": unit, "source": "Site leakage test", "verification_status": status,
        "citations": [{"reference": "LT-01", "page": 1, "excerpt": "Infiltration rate"}],
        "calculation_status": "calculated", "method_id": "infiltration_psychrometric_v1",
        "air_path": "uncontrolled_infiltration", "flow_reference": "outdoor_design_condition",
    })
    return model


class Request:
    def __init__(self, body, path):
        self.path = path
        self.headers = {"Content-Length": str(len(body.encode("utf-8")))}
        self.rfile = BytesIO(body.encode("utf-8"))


def main():
    try:
        validate_schedule_library({"schedules": [{"schedule_id": "bad", "title": "Bad", "status": "confirmed", "source": "x", "citations": [], "day_profiles": {"weekday": profile([0] * 23), "saturday": profile([], "missing"), "sunday_holiday": profile([], "missing")}}]})
        raise AssertionError("23-hour schedule should fail")
    except ValueError as error:
        check("schedule requires exactly 24 values", "exactly 24" in str(error))

    invalid_weather = scenarios()
    invalid_weather["scenarios"][0]["hours"][0]["outdoor_wet_bulb_c"]["value"] = 36
    try:
        validate_design_day_scenarios(invalid_weather)
        raise AssertionError("wet bulb above dry bulb should fail")
    except ValueError as error:
        check("hourly DB/WB physical validation", "cannot exceed" in str(error))

    requirements = validate_design_requirements(requirements_data())
    source_before = deepcopy(requirements)
    model = reviewed_model(requirements)
    report = calculate_hourly_load_report(requirements, library(), scenarios(), model, ["jan_weekday"])
    scenario = report["scenario_results"][0]
    room = scenario["rooms"][0]
    check("hourly report is review-ready for confirmed complete scope", report["status"] == "review_ready" and room["status"] == "review_ready" and report["project_peak"])
    check("scheduled drivers peak only at assigned hour", room["hours"][14]["components"]["people"]["total_kw"] > 0 and room["hours"][13]["components"]["people"]["total_kw"] == 0)
    check("safety is applied after hourly subtotal", room["hours"][14]["design_total_kw"] == round(room["hours"][14]["subtotal_kw"] + room["hours"][14]["safety_allowance_kw"], 4) and room["hours"][14]["safety_allowance_kw"] == round(room["hours"][14]["subtotal_kw"] * 0.1, 4))
    check("model calculation does not mutate requirements", requirements == source_before)

    missing_room_factor_model = reviewed_model(requirements)
    missing_room_factor_model["rooms"][0]["cooling_load"]["safety_factor"] = None
    missing_room_factor_report = calculate_hourly_load_report(
        requirements, library(), scenarios(), missing_room_factor_model, ["jan_weekday"],
    )
    check(
        "missing room safety factor blocks cleanly instead of crashing report generation",
        missing_room_factor_report["status"] == "blocked"
        and "safety factor" in " ".join(
            missing_room_factor_report["scenario_results"][0]["rooms"][0]["blocked_reasons"]
        ).lower(),
    )

    policy_artifact = safety_factor_resolution.resolve({"cooling_safety_factor": {
        "factor": 1.10, "source": "Engineer design brief",
        "citations": [{"reference": "Brief C-1", "page": 2, "excerpt": "Cooling design margin"}],
    }})
    policy_artifact = safety_factor_resolution.approve(policy_artifact, "cooling", "A. Engineer", "2026-09-30", "Final coincident cooling total")
    policy = safety_factor_resolution.policy_for(policy_artifact, "cooling")
    policy_model = reviewed_model(requirements)
    policy_model["rooms"][0]["cooling_load"]["safety_factor"] = 1.1
    policy_report = calculate_hourly_load_report(
        requirements, library(), scenarios(), policy_model, ["jan_weekday"],
        safety_factor_policy=policy,
    )
    policy_room = policy_report["scenario_results"][0]["rooms"][0]
    check("policy mode neutralizes every room-hour factor", all(hour["safety_factor"] == 1.0 and hour["safety_allowance_kw"] == 0 for hour in policy_room["hours"]))
    policy_scenario = policy_report["scenario_results"][0]
    check("policy mode carries no room allowance into zone or floor hours", all(hour.get("safety_allowance_kw", 0) == 0 for area in [*policy_scenario["zones"], *policy_scenario["floors"]] for hour in area["hours"]))
    policy_peak = policy_report["included_scope_peak"]
    check("policy mode applies the approved factor once to coincident project peak", policy_report["status"] == "review_ready" and policy_peak["final_design_total_kw"] == round(policy_peak["raw_coincident_total_kw"] * 1.10, 4))
    check("approved policy retains cited room factor as evidence", policy_report["legacy_room_safety_factors"] == [{"room_id": "zone_001-room-1", "factor": 1.1}])
    check("approved policy applies despite cited room factor above one", policy_report["safety_policy_applied"] and policy_report["status"] == "review_ready")

    basis_requirements = deepcopy(requirements)
    basis_requirements["cooling_load_conditions"].update({"indoor_wet_bulb_basis": "thermodynamic", "outdoor_wet_bulb_basis": "thermodynamic"})
    basis_report = calculate_hourly_load_report(basis_requirements, library(), scenarios(), reviewed_model(basis_requirements), ["jan_weekday"])
    basis_component = basis_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["outside_air"]
    check("hourly outside-air calculation applies and reports declared thermodynamic basis", basis_component["inputs"]["indoor_wet_bulb_method"] == "thermodynamic_ashrae_eq33_iapws_water_ice_v3" and basis_component["inputs"]["outdoor_wet_bulb_basis"] == "thermodynamic")
    weather_provenance = basis_component["inputs"]["outdoor_weather_provenance"]
    check("hourly outside-air result carries its weather state sources, citations, status, and basis", weather_provenance["dry_bulb"]["source"] == "Weather sequence" and weather_provenance["dry_bulb"]["status"] == "confirmed" and weather_provenance["dry_bulb"]["citations"][0]["reference"] == "Weather source W-1" and weather_provenance["wet_bulb_basis"] == "thermodynamic")

    moisture_model = reviewed_model(requirements)
    moisture_component = next(item for item in moisture_model["rooms"][0]["unapproved_components"] if item["component_type"] == "vapour_gain")
    moisture_component.update({
        "value": 1.0, "unit": "kg/h", "source": "Process moisture schedule",
        "citations": [{"reference": "Process schedule PS-1", "page": 2, "excerpt": "Water vapor generation rate"}],
        "verification_status": "confirmed", "calculation_status": "calculated", "method_id": "room_internal_moisture_v1", "air_path": "direct_to_room",
    })
    moisture_model["rooms"][0]["schedule_assignments"].setdefault("moisture", {})[moisture_component["component_id"]] = "vapour"
    moisture_library = library()
    moisture_values = [0.0] * 24
    moisture_values[14] = 1.0
    moisture_library["schedules"].append({
        "schedule_id": "vapour", "title": "Process vapour", "status": "confirmed", "source": "Process schedule PS-1",
        "citations": [], "day_profiles": {"weekday": profile(moisture_values), "saturday": profile([], "missing"), "sunday_holiday": profile([], "missing")},
    })
    draft_moisture = calculate_hourly_load_report(
        requirements, moisture_library, scenarios(), moisture_model, ["jan_weekday"],
        moisture_gate=empty_moisture_method_gate(),
    )
    moisture_hour = draft_moisture["scenario_results"][0]["rooms"][0]["hours"][14]
    check("water-vapor rate contributes psychrometric latent gain", moisture_hour["components"]["internal_moisture"]["latent_kw"] == 0.7071)
    check("unapproved moisture method keeps calculated result draft and suppresses project peak", draft_moisture["status"] == "draft" and not draft_moisture["project_peak"])
    missing_moisture_schedule = deepcopy(moisture_model)
    missing_moisture_schedule["rooms"][0]["schedule_assignments"]["moisture"] = {}
    blocked_moisture = calculate_hourly_load_report(requirements, moisture_library, scenarios(), missing_moisture_schedule, ["jan_weekday"])
    check("calculated moisture gain requires a dedicated schedule", blocked_moisture["scenario_results"][0]["rooms"][0]["status"] == "blocked")
    approved_moisture = empty_moisture_method_gate()
    approved_moisture.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-29", "method_citation": "ASHRAE Standard 140-2014 Addendum a",
        "scope": "Room water-vapor latent load using the documented reference convention.",
    })
    approved_moisture = validate_moisture_method_gate(approved_moisture)
    reviewed_moisture = calculate_hourly_load_report(requirements, moisture_library, scenarios(), moisture_model, ["jan_weekday"], moisture_gate=approved_moisture)
    check("approved method can produce a review-ready, traceable moisture calculation", reviewed_moisture["status"] == "review_ready" and reviewed_moisture["project_peak"])

    transfer_model = reviewed_model(requirements)
    source_room = deepcopy(transfer_model["rooms"][0])
    source_room.update({"room_id": "room_002", "name": "Source room", "indoor_cooling_setpoint_c": 20})
    source_room["cooling_load_conditions"]["indoor_cooling_wet_bulb_c"] = 15
    target_room = transfer_model["rooms"][0]
    target_room["name"] = "Receiving room"
    transfer_component = next(item for item in target_room["unapproved_components"] if item["component_type"] == "transfer_air")
    transfer_component.update({
        "value": 50, "unit": "L/s", "source_room_id": "room_002", "source": "Reviewed room air balance",
        "citations": [{"reference": "Air balance AB-1", "page": 1, "excerpt": "Transfer air rate"}],
        "verification_status": "confirmed", "calculation_status": "calculated",
        "method_id": "room_air_transfer_psychrometric_v1", "air_path": "room_to_room",
        "flow_reference": "sending_room_air_state",
    })
    target_room["schedule_assignments"].setdefault("airflow", {})[transfer_component["component_id"]] = "transfer"
    transfer_model["rooms"].append(source_room)
    transfer_library = library()
    transfer_library["schedules"].append({
        "schedule_id": "transfer", "title": "Transfer air", "status": "confirmed", "source": "Air balance AB-1",
        "citations": [], "day_profiles": {"weekday": profile([1.0] * 24), "saturday": profile([], "missing"), "sunday_holiday": profile([], "missing")},
    })
    transfer_report = calculate_hourly_load_report(requirements, transfer_library, scenarios(), transfer_model, ["jan_weekday"])
    transfer_result = transfer_report["scenario_results"][0]["rooms"][0]
    transfer_row = transfer_result["hours"][14]["components"]["transfer_air"]
    check("room-to-room transfer includes sending and receiving psychrometric states", transfer_row["sensible_kw"] < 0 and transfer_row["latent_kw"] < 0 and transfer_row["input_rows"][0]["source_room_id"] == "room_002")
    transfer_input = transfer_row["input_rows"][0]
    check("transfer-air result retains flow citation and both room-state sources and wet-bulb bases", transfer_input["flow_source"] == "Reviewed room air balance" and transfer_input["flow_citations"][0]["reference"] == "Air balance AB-1" and transfer_input["source_room_conditions"]["source"] == "Engineer conditions" and transfer_input["target_room_conditions"]["wet_bulb_basis"] == "legacy_unverified")
    check("transfer-air calculation remains draft pending HVAC engineer review", transfer_result["status"] == "draft" and transfer_report["status"] == "draft")
    missing_transfer_schedule = deepcopy(transfer_model)
    missing_transfer_schedule["rooms"][0]["schedule_assignments"]["airflow"] = {}
    blocked_transfer = calculate_hourly_load_report(requirements, transfer_library, scenarios(), missing_transfer_schedule, ["jan_weekday"])
    check("calculated transfer air requires its dedicated schedule", blocked_transfer["scenario_results"][0]["rooms"][0]["status"] == "blocked")

    tied_library = library()
    for schedule in tied_library["schedules"]:
        schedule["day_profiles"]["weekday"]["values"][13] = 1.0
    tied = calculate_hourly_load_report(requirements, tied_library, scenarios(), reviewed_model(requirements), ["jan_weekday"])
    tied_peak = tied["scenario_results"][0]["included_scope_peak"]
    check("tied hourly peaks retain all ties and earliest display hour", tied_peak["tied_hours"] == [13, 14] and tied_peak["display_hour"] == 13)

    missing_assignment = reviewed_model(requirements)
    missing_assignment["rooms"][0]["schedule_assignments"]["people"] = ""
    blocked = calculate_hourly_load_report(requirements, library(), scenarios(), missing_assignment, ["jan_weekday"])
    check("missing timed schedule blocks affected room", blocked["status"] == "blocked" and "people schedule assignment" in blocked["scenario_results"][0]["rooms"][0]["blocked_reasons"])

    provisional = calculate_hourly_load_report(requirements, library("provisional"), scenarios(), reviewed_model(requirements), ["jan_weekday"])
    check("provisional evidence produces a draft", provisional["status"] == "draft")
    heating = calculate_hourly_load_report(requirements, library(), scenarios("heating"), reviewed_model(requirements), ["jan_weekday"])
    check("heating scenario is stored but unsupported", heating["status"] == "blocked" and "heating calculation is not implemented" in heating["scenario_results"][0]["blocked_reasons"][0])

    legacy_model = build_hourly_load_model(requirements)
    migrated = validate_hourly_load_model({"schema_version": 1, "updated_at": legacy_model["updated_at"], "source_requirements_updated_at": legacy_model["source_requirements_updated_at"], "rooms": legacy_model["rooms"]})
    check("schema-v1 model normalises to provisional unassigned topology and unassessed room components", migrated["schema_version"] == 4 and migrated["floors"][0]["floor_id"] == "unassigned" and migrated["zones"][0]["floor_id"] == "unassigned" and migrated["rooms"][0]["unapproved_components"][0]["calculation_status"] == "not_assessed")

    infiltration_model = calculated_infiltration(reviewed_model(requirements), 0.36)
    infiltration_report = calculate_hourly_load_report(requirements, library(), scenarios(), infiltration_model, ["jan_weekday"], infiltration_gate=approved_infiltration_gate())
    infiltration_hour = infiltration_report["scenario_results"][0]["rooms"][0]["hours"][14]
    infiltration_component = infiltration_hour["components"]["infiltration"]
    check("approved ACH infiltration contributes separately before the room safety factor", infiltration_report["status"] == "review_ready" and infiltration_component["inputs"]["resolved_flow_lps"] == 6 and infiltration_component["total_kw"] > 0)
    infiltration_weather = infiltration_component["inputs"]["outdoor_weather_provenance"]
    check("infiltration result carries outdoor weather source and wet-bulb basis", infiltration_weather["dry_bulb"]["source"] == "Weather sequence" and infiltration_weather["wet_bulb"]["citations"][0]["reference"] == "Weather source W-1" and infiltration_weather["wet_bulb_basis"] == "legacy_unverified")
    check("infiltration schedule turns contribution off outside the assigned hour", infiltration_report["scenario_results"][0]["rooms"][0]["hours"][13]["components"]["infiltration"]["total_kw"] == 0)

    direct_flow_model = calculated_infiltration(reviewed_model(requirements), 6, "L/s")
    direct_flow_report = calculate_hourly_load_report(requirements, library(), scenarios(), direct_flow_model, ["jan_weekday"], infiltration_gate=approved_infiltration_gate())
    direct_component = direct_flow_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["infiltration"]
    check("equivalent ACH and direct airflow produce the same infiltration cooling load", direct_component["total_kw"] == infiltration_component["total_kw"])

    sensible_only = infiltration_load(10, "L/s", 24, 18, 35, 18, 101.325)
    latent_only = infiltration_load(10, "L/s", 24, 18, 24, 22, 101.325)
    negative_diagnostics = infiltration_load(10, "L/s", 24, 18, 20, 15, 101.325)
    check("infiltration retains signed sensible and latent diagnostics while applying only cooling gains", sensible_only["sensible_kw"] > 0 and sensible_only["latent_kw"] == 0 and latent_only["sensible_kw"] == 0 and latent_only["latent_kw"] > 0 and negative_diagnostics["inputs"]["raw_signed_sensible_kw"] < 0 and negative_diagnostics["inputs"]["raw_signed_latent_kw"] < 0 and negative_diagnostics["total_kw"] == 0)

    zone_height_model = calculated_infiltration(reviewed_model(requirements), 0.36)
    zone_height_model["rooms"][0]["ceiling_height_mm"] = None
    zone_height_model["zones"][0]["ceiling_height_mm"] = 3000
    zone_height_report = calculate_hourly_load_report(requirements, library(), scenarios(), zone_height_model, ["jan_weekday"], infiltration_gate=approved_infiltration_gate())
    check("ACH uses cited zone height when a room height is absent", zone_height_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["infiltration"]["inputs"]["room_volume_m3"] == 60)

    missing_gate = calculate_hourly_load_report(requirements, library(), scenarios(), calculated_infiltration(reviewed_model(requirements), 0.36), ["jan_weekday"])
    check("unapproved infiltration gate blocks the affected room", missing_gate["status"] == "blocked" and "approved infiltration method gate" in missing_gate["scenario_results"][0]["rooms"][0]["blocked_reasons"])

    provisional_infiltration = calculate_hourly_load_report(requirements, library(), scenarios(), calculated_infiltration(reviewed_model(requirements), 0.36, status="provisional"), ["jan_weekday"], infiltration_gate=approved_infiltration_gate())
    check("provisional eligible infiltration remains draft-only", provisional_infiltration["status"] == "draft")

    stored_component = reviewed_model(requirements)
    stored_component["rooms"][0]["unapproved_components"][0].update({
        "value": 0.25, "unit": "ACH", "source": "Engineer infiltration observation", "verification_status": "confirmed",
        "calculation_status": "stored_not_calculated",
    })
    stored = calculate_hourly_load_report(requirements, library(), scenarios(), stored_component, ["jan_weekday"])
    check("stored non-calculated infiltration excludes the affected room until it is eligible", stored["status"] == "blocked" and "infiltration calculation eligibility" in stored["scenario_results"][0]["rooms"][0]["blocked_reasons"])

    invalid_transfer = reviewed_model(requirements)
    invalid_transfer["rooms"][0]["unapproved_components"][4].update({
        "value": 50, "unit": "L/s", "source": "Air balance sketch", "verification_status": "confirmed",
        "calculation_status": "stored_not_calculated", "source_room_id": "missing_room",
    })
    try:
        validate_hourly_load_model(invalid_transfer)
        raise AssertionError("Unknown transfer source room should fail")
    except ValueError as error:
        check("unknown transfer source room is rejected", "unknown source room" in str(error))

    invalid_unit = reviewed_model(requirements)
    invalid_unit["rooms"][0]["unapproved_components"][0].update({
        "value": 12, "unit": "cfm", "source": "Site note", "verification_status": "confirmed",
        "calculation_status": "stored_not_calculated",
    })
    try:
        validate_hourly_load_model(invalid_unit)
        raise AssertionError("Unsupported capture unit should fail")
    except ValueError as error:
        check("unsupported room-component units are rejected", "unit must be one of" in str(error))

    invalid_citation = reviewed_model(requirements)
    invalid_citation["rooms"][0]["unapproved_components"][0].update({
        "value": 0.25, "unit": "ACH", "source": "Site note", "verification_status": "confirmed",
        "calculation_status": "stored_not_calculated", "citations": [{"reference": "", "page": None, "excerpt": ""}],
    })
    try:
        validate_hourly_load_model(invalid_citation)
        raise AssertionError("Empty component citation should fail")
    except ValueError as error:
        check("invalid room-component citations are rejected", "needs a reference or excerpt" in str(error))

    partial_model = reviewed_model(requirements)
    second = deepcopy(partial_model["rooms"][0])
    second["room_id"] = "zone_001-room-2"
    second["name"] = "Blocked room"
    second["schedule_assignments"] = {**second["schedule_assignments"], "people": ""}
    partial_model["rooms"].append(second)
    partial = calculate_hourly_load_report(requirements, library(), scenarios(), partial_model, ["jan_weekday"])
    check("blocked room creates a partial draft", partial["status"] == "draft" and not partial["scope_summary"]["complete_scope"] and not partial["project_peak"] and partial["included_scope_peak"])

    originals = web_app.project_by_id, web_app.update_project
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            other_root = root / "other"
            other_root.mkdir()
            requirements_path = root / "design_requirements.json"
            requirements_path.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
            project = {"id": "p1", "review_dir": str(root), "updated_at": "before"}
            other_project = {"id": "p2", "review_dir": str(other_root), "updated_at": "before"}
            web_app.project_by_id = lambda project_id: project if project_id == "p1" else other_project if project_id == "p2" else None
            web_app.update_project = lambda saved: None
            saved_library = web_app.api_save_schedules(Request(json.dumps({"project_id": "p1", "schedule_library": library()}), "/api/schedules"))
            saved_scenarios = web_app.api_save_design_day_scenarios(Request(json.dumps({"project_id": "p1", "design_day_scenarios": scenarios()}), "/api/design-day-scenarios"))
            built = web_app.api_save_hourly_load_model(Request(json.dumps({"project_id": "p1", "action": "build"}), "/api/hourly-load-model"))
            model_path = root / "hourly_load_model.json"
            legacy_v2 = reviewed_model(requirements)
            legacy_v2["schema_version"] = 2
            legacy_v2["rooms"][0].pop("unapproved_components")
            migrated_api = web_app.api_save_hourly_load_model(Request(json.dumps({"project_id": "p1", "action": "save", "hourly_load_model": legacy_v2}), "/api/hourly-load-model"))
            check("API saves schema-v2 room models as schema-v4 with unassessed components", migrated_api["hourly_load_model"]["schema_version"] == 4 and migrated_api["hourly_load_model"]["rooms"][0]["unapproved_components"][0]["calculation_status"] == "not_assessed")
            saved_model = reviewed_model(requirements)
            web_app.api_save_hourly_load_model(Request(json.dumps({"project_id": "p1", "action": "save", "hourly_load_model": saved_model}), "/api/hourly-load-model"))
            saved_gate = web_app.api_save_infiltration_method_gate(Request(json.dumps({"project_id": "p1", "infiltration_method_gate": approved_infiltration_gate()}), "/api/infiltration-method-gate"))
            moisture_gate = web_app.api_internal_moisture_method_gate(Request("", "/api/internal-moisture-method-gate?project_id=p1"))
            saved_moisture_gate = web_app.api_save_internal_moisture_method_gate(Request(json.dumps({"project_id": "p1", "internal_moisture_method_gate": empty_moisture_method_gate()}), "/api/internal-moisture-method-gate"))
            calculated = web_app.api_save_hourly_load_report(Request(json.dumps({"project_id": "p1", "selected_scenario_ids": ["jan_weekday"]}), "/api/hourly-load-report"))
            check("API withholds paths outside a registered project root", saved_library["url"] == "" and saved_scenarios["url"] == "" and built["url"] == "" and (root / "hourly_load_report.json").exists())
            check("API stores an engineer-approved infiltration gate separately", saved_gate["readiness"]["calculation_enabled"] and (root / "infiltration_method_gate.json").exists())
            check("internal moisture method defaults to a draft gate and can be saved per project", moisture_gate["readiness"]["review_ready_enabled"] is False and saved_moisture_gate["readiness"]["status"] == "placeholder" and (root / "internal_moisture_method_gate.json").exists())
            check("API marks legacy report current before a policy artifact exists", calculated["status"] == "current" and calculated["hourly_load_report"]["status"] == "review_ready" and web_app.api_hourly_load_report(Request("", "/api/hourly-load-report?project_id=p1"))["status"] == "current")
            fallback_path = root / "safety_factor_resolution.json"
            fallback_path.write_text(json.dumps(safety_factor_resolution.resolve({})), encoding="utf-8")
            fallback_report = web_app.api_save_hourly_load_report(Request(json.dumps({"project_id": "p1", "selected_scenario_ids": ["jan_weekday"]}), "/api/hourly-load-report"))["hourly_load_report"]
            check("strict cooling report blocks and withholds the peak for an unapproved fallback policy", fallback_report["status"] == "blocked" and fallback_report["readiness"]["status"] == "blocked" and not fallback_report["project_peak"] and not fallback_report["safety_policy_applied"] and fallback_report["safety_policy"]["origin"] == "controlled_preliminary_fallback")
            fallback_path.unlink()
            calculated = web_app.api_save_hourly_load_report(Request(json.dumps({"project_id": "p1", "selected_scenario_ids": ["jan_weekday"]}), "/api/hourly-load-report"))
            context = {
                "schema_version": 1,
                "site": {"country": "AU", "locality": "Sydney", "state": "NSW", "climate_zone": "5", "source": "Project brief", "citations": []},
                "building_use": "retail", "room_uses": {"zone_001-room-1": {"use": "retail", "source": "A-101", "citations": []}},
                "conditioned_scope": {"status": "confirmed", "mode": "all_rooms", "room_ids": [], "source": "Client cooling brief", "citations": []},
                "reviewer": "Project owner",
            }
            saved_context = web_app.api_save_calculator_inputs(Request(json.dumps({"project_id": "p1", "action": "save_context", "project_context": context}), "/api/calculator-inputs"))
            assembled = web_app.api_save_calculator_inputs(Request(json.dumps({"project_id": "p1", "action": "assemble", "selected_scenario_ids": ["jan_weekday"]}), "/api/calculator-inputs"))
            input_set = assembled["calculator_input_set"]
            snapshot_report = web_app.api_save_hourly_load_report(Request(json.dumps({"project_id": "p1", "input_set_fingerprint": input_set["input_fingerprint"]}), "/api/hourly-load-report"))
            check("API writes immutable input snapshots and calculates from the selected snapshot", saved_context["project_context"]["revision"] == 1 and (root / "calculator_input_sets" / f"{input_set['input_fingerprint']}.json").exists() and snapshot_report["hourly_load_report"]["calculator_input_set"]["input_fingerprint"] == input_set["input_fingerprint"])
            override = {"override_id": "retail-lighting", "target": "rooms.zone_001-room-1.cooling_load.lighting_w_m2", "value": 12, "unit": "W/m2", "source": "Lighting schedule", "reviewer": "Project owner", "citations": [{"reference": "LS-01", "page": 1, "excerpt": "12 W/m2"}]}
            web_app.api_save_calculator_inputs(Request(json.dumps({"project_id": "p1", "action": "save_override", "expected_revision": 0, "override": override}), "/api/calculator-inputs"))
            check("override changes stale a snapshot-backed report without rewriting it", web_app.api_hourly_load_report(Request("", "/api/hourly-load-report?project_id=p1"))["status"] == "stale")
            check("API does not rewrite requirements", json.loads(requirements_path.read_text(encoding="utf-8"))["updated_at"] == requirements["updated_at"])
            check("API isolates projects", web_app.api_schedules(Request("", "/api/schedules?project_id=p2"))["schedule_library"]["schedules"] == [])
            changed = json.loads(requirements_path.read_text(encoding="utf-8"))
            changed["updated_at"] = "later-requirements-revision"
            requirements_path.write_text(json.dumps(changed), encoding="utf-8")
            check("requirements revision makes hourly report stale", web_app.api_hourly_load_report(Request("", "/api/hourly-load-report?project_id=p1"))["status"] == "stale")
            legacy_path = root / "heat_load_report.json"
            legacy_path.write_text(json.dumps({"legacy_result": 1}), encoding="utf-8")
            project["heat_load_report"] = str(legacy_path)
            legacy = web_app.api_heat_load(Request("", "/api/heat-load?project_id=p1"))
            check("legacy cooling report remains readable when stale", legacy["legacy"] and legacy["deprecated"] and legacy["status"] == "stale" and legacy["report"]["legacy_result"] == 1)
            try:
                web_app.api_save_heat_load(Request(json.dumps({"project_id": "p1"}), "/api/heat-load"))
                raise AssertionError("Retired endpoint should reject POST")
            except ValueError as error:
                check("legacy cooling endpoint cannot recalculate", "retired" in str(error))
    finally:
        web_app.project_by_id, web_app.update_project = originals


if __name__ == "__main__":
    main()
