#!/usr/bin/env python3

"""Create a clearly labelled, non-engineering hourly cooling API test case."""

from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backend.web_app as web_app
from ai.design_requirements import validate_design_requirements


PROJECT_ID = "synthetic-hourly-cooling-baseline"
WARNING = "SYNTHETIC TEST FIXTURE — NOT FOR ENGINEERING DESIGN, EQUIPMENT SELECTION, OR BENCHMARK PARITY."
SOURCE = "Synthetic test fixture created for hourly cooling workflow regression testing."


class Request:
    def __init__(self, payload, path):
        body = json.dumps(payload)
        self.path = path
        self.headers = {"Content-Length": str(len(body.encode("utf-8")))}
        self.rfile = BytesIO(body.encode("utf-8"))


def field(value):
    return {"value": value, "status": "provisional", "source": SOURCE, "citations": []}


def profile(values):
    return {"values": values, "status": "provisional", "source": SOURCE, "citations": []}


def values_by_hour(points):
    return [points.get(hour, 0.0) for hour in range(24)]


def requirements_data():
    return {
        "scope": "project",
        "space_usage": "Synthetic small retail cooling test case",
        "occupancy": 8,
        "operating_hours": "Synthetic weekday 08:00-18:00",
        "indoor_cooling_setpoint_c": 24,
        "indoor_heating_setpoint_c": 20,
        "outdoor_summer_db_c": 34,
        "outdoor_winter_db_c": 5,
        "fresh_air_basis": "Synthetic fixed outdoor-air basis for workflow testing",
        "exhaust_basis": "Synthetic case: no process exhaust",
        "cooking_activity": "none",
        "hood_requirement": "not_required",
        "exhaust_outcome": "not_required",
        "make_up_air_requirement": "not_required",
        "ceiling_height_mm": 3000,
        "ceiling_void_height_mm": 300,
        "heat_sources": [],
        "cooling_load_conditions": {
            "indoor_cooling_wet_bulb_c": 17.5,
            "outdoor_summer_wet_bulb_c": 23,
            "atmospheric_pressure_kpa": 101.325,
            "verification_status": "provisional",
            "source": SOURCE,
        },
        "existing_services": "Synthetic case only; no existing-services assessment.",
        "code_basis": "Synthetic workflow test only; no code basis is asserted.",
        "designer_notes": WARNING,
        "verification": {
            category: {"status": "provisional", "source": SOURCE}
            for category in ("occupancy", "design_conditions", "outside_air", "exhaust", "heat_sources", "ceiling", "existing_services")
        },
        "zones": [{
            "zone_id": "synthetic_retail_zone",
            "name": "Synthetic Retail Room",
            "usage": "Synthetic retail test space",
            "source_room_labels": ["SYNTHETIC RETAIL ROOM — NOT FOR DESIGN"],
            "area_m2": 40,
            "occupancy": 8,
            "operating_hours": "Synthetic weekday 08:00-18:00",
            "indoor_cooling_setpoint_c": 24,
            "indoor_heating_setpoint_c": 20,
            "ceiling_height_mm": 3000,
            "heat_sources": [{
                "name": "Synthetic display refrigeration",
                "quantity": 1,
                "watts": 500,
                "kind": "refrigeration",
                "diversity_factor": 0.8,
                "space_gain_factor": 0.9,
                "verification_status": "provisional",
                "source": SOURCE,
            }],
            "cooling_load": {
                "people_sensible_w_per_person": 75,
                "people_latent_w_per_person": 55,
                "people_diversity_factor": 0.8,
                "lighting_w_m2": 12,
                "lighting_diversity_factor": 0.9,
                "outside_air_lps": 40,
                "safety_factor": 1.1,
                "envelope_not_applicable": False,
                "verification_status": "provisional",
                "source": SOURCE,
                "envelope_surfaces": [
                    {
                        "surface_id": "synthetic_south_wall",
                        "kind": "opaque_wall",
                        "orientation": "S",
                        "area_m2": 18,
                        "u_value_w_m2k": 0.6,
                        "solar_design_w_m2": 350,
                        "solar_gain_factor": 0.2,
                        "shading_factor": 0.9,
                        "verification_status": "provisional",
                        "source": SOURCE,
                    },
                    {
                        "surface_id": "synthetic_west_glazing",
                        "kind": "glazing",
                        "orientation": "W",
                        "area_m2": 6,
                        "u_value_w_m2k": 2.5,
                        "solar_design_w_m2": 500,
                        "solar_gain_factor": 0.4,
                        "shading_factor": 0.8,
                        "verification_status": "provisional",
                        "source": SOURCE,
                    },
                ],
            },
        }],
    }


def site_conditions():
    return {
        "site": {
            "project_name": field("SYNTHETIC — Hourly Cooling Baseline"),
            "address": field("No physical address — synthetic fixture"),
            "location_description": field("No physical location — synthetic fixture"),
            "weather_station_reference": field("Synthetic 24-hour weather sequence — no station"),
            "elevation_m": field(0),
            "north_orientation_note": field("No drawing orientation; solar values are synthetic test data."),
        },
        "design_basis": {
            "name": "Synthetic hourly cooling workflow baseline",
            "reference_version_or_date": "v1 fixture",
            "status": "provisional",
            "source": SOURCE,
            "citations": [],
        },
        "summer": {
            "outdoor_dry_bulb_c": field(34),
            "outdoor_wet_bulb_c": field(23),
            "indoor_dry_bulb_c": field(24),
            "indoor_relative_humidity_percent": field(50),
            "atmospheric_pressure_kpa": field(101.325),
        },
        "winter": {
            "outdoor_dry_bulb_c": field(5),
            "outdoor_relative_humidity_percent": field(80),
            "indoor_dry_bulb_c": field(20),
            "indoor_relative_humidity_percent": field(50),
        },
    }


def schedules():
    profiles = {
        "synthetic_people": values_by_hour({8: 0.25, 9: 0.5, 10: 0.8, 11: 0.8, 12: 0.8, 13: 0.8, 14: 0.8, 15: 0.8, 16: 0.8, 17: 0.4}),
        "synthetic_lighting": values_by_hour({7: 0.2, 8: 0.6, 9: 0.9, 10: 0.9, 11: 0.9, 12: 0.9, 13: 0.9, 14: 0.9, 15: 0.9, 16: 0.9, 17: 0.7, 18: 0.2}),
        "synthetic_outside_air": values_by_hour({7: 0.3, 8: 0.6, 9: 0.8, 10: 0.8, 11: 0.8, 12: 0.8, 13: 0.8, 14: 0.8, 15: 0.8, 16: 0.8, 17: 0.6, 18: 0.2}),
        "synthetic_refrigeration": values_by_hour({7: 0.3, 8: 0.7, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 18: 0.5}),
        "synthetic_solar": values_by_hour({7: 0.1, 8: 0.3, 9: 0.5, 10: 0.7, 11: 0.9, 12: 1, 13: 1, 14: 1, 15: 0.8, 16: 0.5, 17: 0.2}),
    }
    return {
        "schedules": [{
            "schedule_id": schedule_id,
            "title": schedule_id.replace("synthetic_", "Synthetic ").replace("_", " ").title(),
            "description": WARNING,
            "status": "provisional",
            "source": SOURCE,
            "citations": [],
            "day_profiles": {
                "weekday": profile(values),
                "saturday": {"values": [], "status": "missing", "source": "", "citations": []},
                "sunday_holiday": {"values": [], "status": "missing", "source": "", "citations": []},
            },
        } for schedule_id, values in profiles.items()],
    }


def design_day_scenarios():
    dry_bulb = [20, 19, 18, 18, 17, 18, 20, 23, 26, 29, 31, 33, 34, 35, 35, 34, 32, 29, 26, 24, 23, 22, 21, 20]
    wet_bulb = [15, 15, 14, 14, 14, 14, 15, 16, 18, 20, 21, 22, 23, 23, 23, 22, 21, 20, 19, 18, 17, 16, 16, 15]
    return {
        "scenarios": [{
            "scenario_id": "synthetic_january_weekday",
            "title": "Synthetic January weekday cooling design day",
            "mode": "cooling",
            "representative_month": "January",
            "day_type": "weekday",
            "status": "provisional",
            "source": SOURCE,
            "citations": [],
            "atmospheric_pressure_kpa": field(101.325),
            "hours": [{
                "hour": hour,
                "outdoor_dry_bulb_c": field(dry_bulb[hour]),
                "outdoor_wet_bulb_c": field(wet_bulb[hour]),
            } for hour in range(24)],
        }],
    }


def post(handler, path, payload):
    return handler(Request(payload, path))


def main():
    projects = web_app.load_projects()
    if PROJECT_ID in projects:
        raise SystemExit(f"{PROJECT_ID} already exists. Existing synthetic data was left untouched.")

    review_dir = ROOT / "output" / "web_review" / PROJECT_ID
    if review_dir.exists():
        raise SystemExit(f"{review_dir} already exists. Existing synthetic data was left untouched.")
    review_dir.mkdir(parents=True)

    now = web_app.timestamp()
    project = {
        "id": PROJECT_ID,
        "name": "SYNTHETIC — Hourly Cooling Baseline (NOT FOR DESIGN)",
        "pages": 0,
        "size_bytes": 0,
        "analysed": True,
        "relevant": 0,
        "review_dir": str(review_dir),
        "synthetic_test_case": True,
        "synthetic_warning": WARNING,
        "created_at": now,
        "updated_at": now,
    }
    projects[PROJECT_ID] = project
    web_app.save_projects(projects)

    requirements = validate_design_requirements(requirements_data())
    requirements_path = review_dir / "design_requirements.json"
    requirements_path.write_text(json.dumps(requirements, indent=2), encoding="utf-8")
    project["design_requirements"] = str(requirements_path)
    web_app.update_project(project)

    (review_dir / "drawing_coverage.json").write_text(json.dumps({
        "synthetic_test_case": True,
        "coverage_exceptions": [{
            "item_id": "synthetic-no-drawings",
            "status": "unresolved",
            "message": "Synthetic fixture has no drawings and can never support a final calculation.",
        }],
    }, indent=2), encoding="utf-8")

    site = post(web_app.api_save_site_design_conditions, "/api/site-design-conditions", {"project_id": PROJECT_ID, "site_design_conditions": site_conditions()})
    schedule_library = post(web_app.api_save_schedules, "/api/schedules", {"project_id": PROJECT_ID, "schedule_library": schedules()})
    scenarios = post(web_app.api_save_design_day_scenarios, "/api/design-day-scenarios", {"project_id": PROJECT_ID, "design_day_scenarios": design_day_scenarios()})
    built = post(web_app.api_save_hourly_load_model, "/api/hourly-load-model", {"project_id": PROJECT_ID, "action": "build"})

    model = built["hourly_load_model"]
    room = model["rooms"][0]
    room["mapping_status"] = "confirmed"
    room["verification_status"] = "provisional"
    room["source"] = SOURCE
    source_id = room["heat_sources"][0]["source_id"]
    room["schedule_assignments"] = {
        "people": "synthetic_people",
        "lighting": "synthetic_lighting",
        "outside_air": "synthetic_outside_air",
        "equipment": {source_id: "synthetic_refrigeration"},
        "solar": {
            "synthetic_south_wall": "synthetic_solar",
            "synthetic_west_glazing": "synthetic_solar",
        },
    }
    saved_model = post(web_app.api_save_hourly_load_model, "/api/hourly-load-model", {"project_id": PROJECT_ID, "action": "save", "hourly_load_model": model})
    report = post(web_app.api_save_hourly_load_report, "/api/hourly-load-report", {
        "project_id": PROJECT_ID,
        "selected_scenario_ids": ["synthetic_january_weekday"],
        "calculation_stage": "preliminary",
    })

    result = report["hourly_load_report"]
    if result["status"] != "calculated_provisional":
        raise RuntimeError(f"Expected calculated_provisional synthetic report, received {result['status']!r}.")
    manifest = {
        "case_name": project["name"],
        "warning": WARNING,
        "purpose": "Exercise the site/schedule/scenario/room-model/hourly-report API workflow with no real project evidence.",
        "not_valid_for": ["engineering design", "equipment selection", "CAMEL+/DA09 parity", "final calculation"],
        "artifacts": {
            "site_design_conditions": site["url"],
            "schedule_library": schedule_library["url"],
            "design_day_scenarios": scenarios["url"],
            "hourly_load_model": saved_model["url"],
            "hourly_load_report": report["artifact_url"],
        },
        "report_status": result["status"],
        "governing_project_peak": result["governing_project_peak"],
        "excluded_components": result["excluded_components"],
    }
    (review_dir / "synthetic_case_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"project_id": PROJECT_ID, "review_dir": str(review_dir), "report_status": result["status"], "governing_project_peak": result["governing_project_peak"]}, indent=2))


if __name__ == "__main__":
    main()
