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
    room = model["rooms"][0]
    room["mapping_status"] = "confirmed"
    room["verification_status"] = "confirmed"
    room["source"] = "Engineer reviewed room map"
    room["schedule_assignments"] = {"people": "people", "lighting": "lights", "outside_air": "air", "equipment": {room["heat_sources"][0]["source_id"]: "fridge"}, "solar": {"north": "solar"}}
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
    check("hourly report calculates confirmed model", report["status"] == "calculated" and room["status"] == "calculated")
    check("scheduled drivers peak only at assigned hour", room["hours"][14]["components"]["people"]["total_kw"] > 0 and room["hours"][13]["components"]["people"]["total_kw"] == 0)
    check("safety is applied after hourly subtotal", room["hours"][14]["design_total_kw"] == round(room["hours"][14]["subtotal_kw"] + room["hours"][14]["safety_allowance_kw"], 4) and room["hours"][14]["safety_allowance_kw"] == round(room["hours"][14]["subtotal_kw"] * 0.1, 4))
    check("model calculation does not mutate requirements", requirements == source_before)

    tied_library = library()
    for schedule in tied_library["schedules"]:
        schedule["day_profiles"]["weekday"]["values"][13] = 1.0
    tied = calculate_hourly_load_report(requirements, tied_library, scenarios(), reviewed_model(requirements), ["jan_weekday"])
    tied_peak = tied["scenario_results"][0]["project_peak"]
    check("tied hourly peaks retain all ties and earliest display hour", tied_peak["tied_hours"] == [13, 14] and tied_peak["display_hour"] == 13)

    missing_assignment = reviewed_model(requirements)
    missing_assignment["rooms"][0]["schedule_assignments"]["people"] = ""
    blocked = calculate_hourly_load_report(requirements, library(), scenarios(), missing_assignment, ["jan_weekday"])
    check("missing timed schedule blocks affected room", blocked["status"] == "blocked" and "people schedule assignment" in blocked["scenario_results"][0]["rooms"][0]["blocked_reasons"])

    provisional = calculate_hourly_load_report(requirements, library("provisional"), scenarios(), reviewed_model(requirements), ["jan_weekday"])
    check("provisional evidence remains visible", provisional["status"] == "calculated_provisional")
    heating = calculate_hourly_load_report(requirements, library(), scenarios("heating"), reviewed_model(requirements), ["jan_weekday"])
    check("heating scenario is stored but unsupported", heating["status"] == "blocked" and "heating calculation is not implemented" in heating["scenario_results"][0]["blocked_reasons"][0])

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
    finally:
        web_app.project_by_id, web_app.update_project = originals


if __name__ == "__main__":
    main()
