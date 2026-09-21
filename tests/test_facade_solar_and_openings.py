#!/usr/bin/env python3
"""Independent smoke vectors for cited façade weather and AI opening matching."""

from copy import deepcopy
import json
from math import cos, radians
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import apply_reviewed_envelope_to_hourly_model, normalize_glazing_surfaces, validate_envelope_model
from ai.calculation_extraction import evidence_input_fingerprints
from ai.envelope_method_gates import WEATHER_FACADE_POLICY, empty_solar_radiation_method_gate
from ai.hourly_loads import calculate_hourly_load_report
from ai.opening_resolution import resolve_openings
from ai.site_orientation import validate_site_orientation
from ai.glazing_gate import WEATHER_FACADE_GLAZING_POLICY, validate_glazing_method_gate
from ai.solar_radiation import facade_irradiance, validate_solar_radiation_source
from backend.calculation_extraction_service import _display_evidence, input_artifacts_current
from test_glazing_hourly_integration import approved_gate, citation, envelope, hourly_model, requirements, schedules, scenarios


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print("PASS - " + label)


def weather_source():
    hours = []
    for hour in range(24):
        daylight = 6 <= hour <= 18
        hours.append({"hour": hour, "dni_w_m2": 800 if daylight else 0,
                      "dhi_w_m2": 100 if daylight else 0, "ghi_w_m2": 650 if daylight else 0})
    return validate_solar_radiation_source({
        "source_id": "summer-weather", "scenario_id": "summer", "location": "Sydney",
        "timezone": "Australia/Sydney", "date": "2026-01-15", "latitude_deg": -33.87,
        "longitude_deg": 151.21, "hour_convention": "hour_start",
        "irradiance_basis": "horizontal_components", "weather_day_type": "design_day",
        "ground_reflectance": 0.2, "ground_reflectance_source": "Reviewed ground basis",
        "ground_reflectance_citations": citation("ground"), "hours": hours,
        "method": "Cited synthetic development vector", "source": "Test fixture only", "citations": citation("solar"),
    })


def opening_fixture():
    record = {"opening_id": "P5-VOPEN-001", "tag": "W01", "level_name": "Level 1",
              "owner_room_id": "room-1", "owner_zone_id": "zone-1", "host_wall_id": "wall-1",
              "facade": "N", "width_m": 2, "height_m": 1, "quantity": 1,
              "opening_bbox_px": [20, 20, 40, 30], "source_crop": "crop-1.png",
              "elevation_refs": [{"page": 2, "reference": "W01 elevation"}], "citations": citation("plan")}
    return {"geometry_review": {"pages": [{"page": 1, "opening_candidates": [record]}]}}, record


def weather_glazing_gate():
    gate = approved_gate()
    gate["policy"] = WEATHER_FACADE_GLAZING_POLICY
    return validate_glazing_method_gate(gate)


def main():
    source = weather_source()
    north_midday = facade_irradiance(source, 12, "N", "summer")
    south_midday = facade_irradiance(source, 12, "S", "summer")
    check("southern-hemisphere north façade has more summer noon direct sun than south", north_midday["direct_w_m2"] > south_midday["direct_w_m2"])
    check("nighttime façade radiation is zero", sum(facade_irradiance(source, 2, "N", "summer")[key] for key in ("direct_w_m2", "sky_diffuse_w_m2", "ground_diffuse_w_m2")) == 0)
    check("east morning exceeds west morning direct", facade_irradiance(source, 8, "E", "summer")["direct_w_m2"] > facade_irradiance(source, 8, "W", "summer")["direct_w_m2"])
    check("west afternoon exceeds east afternoon direct", facade_irradiance(source, 16, "W", "summer")["direct_w_m2"] > facade_irradiance(source, 16, "E", "summer")["direct_w_m2"])
    check("isotropic sky diffuse matches independent half-sky reference", abs(north_midday["sky_diffuse_w_m2"] - 50.0) < 0.000001)
    check("ground-reflected component matches independent vertical-plane reference", abs(north_midday["ground_diffuse_w_m2"] - 65.0) < 0.000001)
    direct_reference = 800 * max(0, cos(radians(north_midday["solar_altitude_deg"])) * cos(radians(north_midday["solar_azimuth_deg"])))
    check("direct façade component matches independent incidence calculation", abs(north_midday["direct_w_m2"] - direct_reference) < 0.001)
    vision, raw = opening_fixture()
    resolved = resolve_openings(vision, "pdf-hash", {1, 2})
    check("cited unique opening is AI-estimated but not reviewed", resolved["openings"][0]["status"] == "ai_estimated")
    proposed_vision = deepcopy(vision)
    proposed_vision["geometry_review"]["pages"][0]["opening_candidates"][0]["window_properties"] = {
        "u_value_w_m2k": 2.5, "shgc": 0.42, "source": "Cited test schedule", "citations": citation("window-schedule")}
    property_row = resolve_openings(proposed_vision, "pdf-hash", {1, 2})["openings"][0]
    check("AI-cited window properties remain evidence-only until reviewed record", property_row["proposed_window_properties"]["shgc"] == 0.42 and property_row["properties_status"] == "evidence_only_until_reviewed_window_record")
    repeated = deepcopy(vision)
    second = deepcopy(raw)
    second["opening_bbox_px"] = [60, 20, 80, 30]
    repeated["geometry_review"]["pages"][0]["opening_candidates"].append(second)
    check("repeated same-level tag remains a conflict", all(row["status"] == "conflict" for row in resolve_openings(repeated, "pdf-hash", {1, 2})["openings"]))
    wrong_schedule = deepcopy(vision)
    wrong_schedule["geometry_review"]["pages"][0]["opening_candidates"][0]["schedule_refs"] = [{"page": 2, "reference": "W01 schedule", "width_m": 3}]
    check("conflicting schedule geometry cannot confirm the opening", resolve_openings(wrong_schedule, "pdf-hash", {1, 2})["openings"][0]["status"] == "blocked")
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "thumbnails").mkdir()
        (root / "thumbnails" / "page_001.png").write_bytes(b"fixture")
        display = _display_evidence(type("Web", (), {"safe_link": staticmethod(lambda path: "/safe/" + Path(path).name)})(), root,
                                    {"opening_register": {"openings": [{"page": 1, "source_crop": "../../private/source.png"}]}})
        check("opening preview is project-local and rejects escaping crop paths", display["opening_register"]["openings"][0]["page_preview_url"] == "/safe/page_001.png" and "crop_preview_url" not in display["opening_register"]["openings"][0] and display["opening_register"]["openings"][0]["source_crop"] == "source.png")
        (root / "vision_response.json").write_text(json.dumps(vision), encoding="utf-8")
        stored = {"input_artifact_fingerprints": evidence_input_fingerprints({}, {}, {}, {}, vision, {}, {}, {})}
        check("unchanged AI opening response keeps derived register current", input_artifacts_current(root, stored) is True)
        (root / "vision_response.json").write_text(json.dumps(repeated), encoding="utf-8")
        check("changed AI opening response stales derived register", input_artifacts_current(root, stored) is False)
    library, model = envelope()
    surface = model["surfaces"][1]
    surface.update({"solar_basis": "weather_facade", "solar_radiation_source_id": "summer-weather",
                    "solar_shading_mode": "manual", "direct_shading_factor": 0.5,
                    "diffuse_shading_factor": 1.0, "diffuse_shading_source": "Reviewed no diffuse obstruction",
                    "diffuse_shading_citations": citation("shade"), "opening_evidence_id": resolved["openings"][0]["opening_id"],
                    "host_surface_id": "wall-1", "host_wall_evidence_id": "wall-1", "manual_solar": {"enabled": False}})
    model = validate_envelope_model(model, library)
    included, blocked, _stored = normalize_glazing_surfaces(library, model, weather_glazing_gate(), opening_register=resolved)
    check("reviewed weather-facade opening is eligible without manual solar", len(included) == 1 and not blocked)
    orientation = validate_site_orientation({"status": "reviewed", "tenancy_id": "tenancy-1",
        "alignment": {"kind": "north_arrow", "plan_up_azimuth_deg": 0, "source": "Cited plan",
                      "citations": ["A1 north arrow"]},
        "facades": [{"host_surface_id": "wall-1", "opening_ids": [resolved["openings"][0]["opening_id"]],
                     "tenancy_id": "tenancy-1", "line_px": [[0, 0], [10, 0]], "outward_side": "left",
                     "exposure": "external", "mapping_landmark_ids": ["door-A", "column-B"],
                     "source": "Cited plan/elevation", "citations": ["A1/A2"], "review_status": "confirmed"}]})
    oriented_model = deepcopy(model)
    oriented_model["surfaces"][1].update({"azimuth_deg": 0.0, "external_exposure": "external",
                                           "orientation_source_fingerprint": orientation["fingerprint"]})
    oriented_model = validate_envelope_model(oriented_model, library)
    oriented_included, oriented_blocked, _ = normalize_glazing_surfaces(
        library, oriented_model, weather_glazing_gate(), opening_register=resolved, site_orientation=orientation)
    check("current reviewed true-north azimuth reaches eligible glazing", len(oriented_included) == 1 and not oriented_blocked)
    stale_orientation = deepcopy(orientation)
    stale_orientation["facades"][0]["mapping_landmark_ids"].append("new-evidence")
    stale_orientation = validate_site_orientation(stale_orientation)
    check("changed site alignment blocks old azimuth fingerprint", bool(normalize_glazing_surfaces(
        library, oriented_model, weather_glazing_gate(), opening_register=resolved, site_orientation=stale_orientation)[1]))
    radiation_gate = empty_solar_radiation_method_gate()
    radiation_gate.update({"approval_status": "approved", "policy": WEATHER_FACADE_POLICY})
    hourly = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, model, weather_glazing_gate(), opening_register=resolved)
    report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), hourly, ["summer"],
                                          glazing_gate=weather_glazing_gate(), radiation_gate=radiation_gate, radiation_source=source)
    noon = report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]
    check("hourly report separates glazing conduction and weather-facade solar", "glazing_conduction" in noon and noon["glazing_solar"]["total_kw"] > 0)
    inputs = noon["glazing_solar"]["input_rows"][0]
    check("hourly solar retains source, sun position and irradiance components", inputs["facade_irradiance"]["source_fingerprint"] == source["fingerprint"] and "solar_azimuth_deg" in inputs["facade_irradiance"])
    full_solar = noon["glazing_solar"]["total_kw"]
    direct_shaded = deepcopy(model)
    direct_shaded["surfaces"][1]["direct_shading_factor"] = 0.0
    shaded_hourly = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, direct_shaded, weather_glazing_gate(), opening_register=resolved)
    shaded_report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), shaded_hourly, ["summer"],
                                                 glazing_gate=weather_glazing_gate(), radiation_gate=radiation_gate, radiation_source=source)
    shaded_solar = shaded_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["glazing_solar"]["total_kw"]
    check("full direct shading retains explicitly unshaded diffuse solar", 0 < shaded_solar < full_solar)
    direct_shaded["surfaces"][1]["diffuse_shading_factor"] = 0.0
    fully_shaded_hourly = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, direct_shaded, weather_glazing_gate(), opening_register=resolved)
    fully_shaded_report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), fully_shaded_hourly, ["summer"],
                                                       glazing_gate=weather_glazing_gate(), radiation_gate=radiation_gate, radiation_source=source)
    check("separate direct and diffuse factors can fully shade solar without affecting conduction", fully_shaded_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["glazing_solar"]["total_kw"] == 0 and fully_shaded_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"]["glazing_conduction"]["total_kw"] == noon["glazing_conduction"]["total_kw"])
    no_u_library = deepcopy(library)
    no_u_library["windows"][0]["u_value_w_m2k"] = None
    check("missing cited overall-window U-value blocks glazing", bool(normalize_glazing_surfaces(no_u_library, model, weather_glazing_gate(), opening_register=resolved)[1]))
    no_shgc_library = deepcopy(library)
    no_shgc_library["windows"][0]["shgc"] = None
    check("missing solar property blocks glazing", bool(normalize_glazing_surfaces(no_shgc_library, model, weather_glazing_gate(), opening_register=resolved)[1]))
    unapproved_radiation = calculate_hourly_load_report(requirements(), schedules(), scenarios(), hourly, ["summer"],
                                                        glazing_gate=weather_glazing_gate(), radiation_gate=empty_solar_radiation_method_gate(), radiation_source=source)
    check("unapproved weather-radiation gate excludes glazing", bool(unapproved_radiation["scenario_results"][0]["rooms"][0]["excluded_glazing"]))
    preliminary_model = deepcopy(model)
    preliminary_model["surfaces"][1].update({"geometry_mode": "preliminary_ai_estimate", "opening_mapping_status": "proposed", "review_status": "provisional"})
    preliminary = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, preliminary_model, weather_glazing_gate(), opening_register=resolved)
    preliminary_report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), preliminary, ["summer"],
                                                       glazing_gate=weather_glazing_gate(), radiation_gate=radiation_gate, radiation_source=source)
    check("AI-estimated geometry can produce a labelled draft glazing subtotal", preliminary_report["scenario_results"][0]["rooms"][0]["status"] == "draft" and "glazing_solar" in preliminary_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"] and not preliminary_report["project_peak"])
    reviewed_model = deepcopy(preliminary_model)
    reviewed_model["surfaces"][1]["geometry_mode"] = "engineering_reviewed"
    _eligible, reviewed_blocked, _stored = normalize_glazing_surfaces(library, reviewed_model, weather_glazing_gate(), opening_register=resolved)
    check("strict reviewed geometry rejects proposed opening mapping", bool(reviewed_blocked))
    missing = deepcopy(source)
    missing["scenario_id"] = "other"
    missing = validate_solar_radiation_source(missing)
    excluded = calculate_hourly_load_report(requirements(), schedules(), scenarios(), hourly, ["summer"],
                                            glazing_gate=weather_glazing_gate(), radiation_gate=radiation_gate, radiation_source=missing)
    room = excluded["scenario_results"][0]["rooms"][0]
    check("scenario mismatch excludes glazing and leaves draft subtotal", room["status"] == "draft" and room["excluded_glazing"] and "glazing_solar" not in room["hours"][14]["components"])


if __name__ == "__main__":
    main()
