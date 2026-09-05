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
from ai.hourly_loads import (
    build_hourly_load_model,
    calculate_hourly_load_report,
    validate_design_day_scenarios,
    validate_hourly_load_model,
    validate_schedule_library,
)


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
    return {"schedules": [{"schedule_id": name, "title": name, "description": "", "status": status, "source": "Engineer schedule", "citations": [], "day_profiles": {"weekday": profile(values, status), "saturday": profile([], "missing"), "sunday_holiday": profile([], "missing")}} for name in ("people", "lights", "air", "fridge", "solar")]}


def scenarios(mode="cooling", status="confirmed"):
    hours = [{"hour": hour, "outdoor_dry_bulb_c": {"value": 35, "status": status, "source": "Weather sequence", "citations": []}, "outdoor_wet_bulb_c": {"value": 24, "status": status, "source": "Weather sequence", "citations": []}} for hour in range(24)]
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
    check("schema-v1 model normalises to provisional unassigned topology and unassessed room components", migrated["schema_version"] == 3 and migrated["floors"][0]["floor_id"] == "unassigned" and migrated["zones"][0]["floor_id"] == "unassigned" and migrated["rooms"][0]["unapproved_components"][0]["calculation_status"] == "not_assessed")

    stored_component = reviewed_model(requirements)
    stored_component["rooms"][0]["unapproved_components"][0].update({
        "value": 0.25, "unit": "ACH", "source": "Engineer infiltration observation", "verification_status": "confirmed",
        "calculation_status": "stored_not_calculated",
    })
    stored = calculate_hourly_load_report(requirements, library(), scenarios(), stored_component, ["jan_weekday"])
    check("stored non-calculated room component keeps result draft without changing contribution", stored["status"] == "draft" and not stored["project_peak"] and not stored["scenario_results"][0]["scope_summary"]["complete_scope"] and stored["scenario_results"][0]["rooms"][0]["room_input_scope"]["stored_not_calculated"][0]["component_type"] == "infiltration")

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
            check("API saves schema-v2 room models as schema-v3 with unassessed components", migrated_api["hourly_load_model"]["schema_version"] == 3 and migrated_api["hourly_load_model"]["rooms"][0]["unapproved_components"][0]["calculation_status"] == "not_assessed")
            saved_model = reviewed_model(requirements)
            web_app.api_save_hourly_load_model(Request(json.dumps({"project_id": "p1", "action": "save", "hourly_load_model": saved_model}), "/api/hourly-load-model"))
            calculated = web_app.api_save_hourly_load_report(Request(json.dumps({"project_id": "p1", "selected_scenario_ids": ["jan_weekday"]}), "/api/hourly-load-report"))
            check("API persists isolated artifacts", saved_library["url"].endswith("schedule_library.json") and saved_scenarios["url"].endswith("design_day_scenarios.json") and built["url"].endswith("hourly_load_model.json") and (root / "hourly_load_report.json").exists())
            check("API marks report current", calculated["status"] == "current" and web_app.api_hourly_load_report(Request("", "/api/hourly-load-report?project_id=p1"))["status"] == "current")
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
