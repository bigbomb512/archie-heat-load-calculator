#!/usr/bin/env python3
"""Regression checks for reviewed glazing in the hourly cooling path."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import (
    apply_reviewed_envelope_to_hourly_model, empty_envelope_library,
    normalize_surfaces, validate_envelope_library, validate_envelope_model,
)
from ai.glazing_gate import empty_glazing_method_gate, validate_glazing_method_gate
from ai.hourly_loads import calculate_hourly_load_report


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed project evidence"}]


def approved_gate():
    gate = empty_glazing_method_gate()
    gate.update({
        "approval_status": "approved", "engineer_name": "A. Engineer", "engineer_credential": "CPEng",
        "approved_at": "2026-09-15", "method_citation": "GM-01", "citations": citation("GM-01"),
    })
    return validate_glazing_method_gate(gate)


def envelope():
    library = validate_envelope_library({"constructions": [{
        "record_id": "wall", "title": "Wall", "revision": 1, "review_status": "confirmed", "source": "Wall schedule", "citations": citation("A-201"), "kind": "opaque_wall", "u_value_w_m2k": 1.0,
    }], "windows": [{
        "record_id": "win", "title": "W01", "revision": 1, "review_status": "confirmed", "source": "Window schedule", "citations": citation("A-300"),
        "u_value_w_m2k": 2.0, "u_value_basis": "overall_window", "shgc": 0.5, "frame_fraction": 0.1,
        "glass_area_correction": 1.0, "internal_shading_factor": 1.0,
    }]})
    model = validate_envelope_model({"active_for_calculation": True, "surfaces": [
        {"surface_id": "wall-1", "owner_zone_id": "zone-1", "owner_room_id": "", "kind": "opaque_wall", "orientation": "N", "area_m2": 10,
         "area_basis": "gross_with_confirmed_openings", "linked_opening_surface_ids": ["window-1"], "opening_coverage_status": "confirmed",
         "construction_id": "wall", "window_id": "", "boundary_method": "external", "review_status": "confirmed", "source": "Plan", "citations": citation("A-202"), "manual_solar": {"enabled": False}},
        {"surface_id": "window-1", "owner_zone_id": "zone-1", "owner_room_id": "room-1", "kind": "glazing", "orientation": "N", "area_m2": None,
         "opening_mapping_status": "confirmed", "window_id": "win", "opening_width_m": 2, "opening_height_m": 1, "opening_quantity": 1,
         "boundary_method": "external", "review_status": "confirmed", "source": "Plan/elevation", "citations": citation("A-202"),
         "manual_solar": {"enabled": True, "incident_solar_w_m2": 500, "external_shading_factor": 1, "review_status": "confirmed", "source": "Manual solar", "citations": citation("Solar basis")}},
    ]}, library)
    return library, model


def requirements():
    return {"updated_at": "r1", "zones": [], "cooling_load_conditions": {}}


def hourly_model():
    component_types = ["infiltration", "minimum_supply_air", "extract_air", "spill_air", "transfer_air", "make_up_air", "vapour_gain", "steam_gain", "process_latent_load"]
    return {"schema_version": 4, "updated_at": "m1", "source_requirements_updated_at": "r1", "floors": [{"floor_id": "floor-1", "name": "Floor", "verification_status": "confirmed", "source": "Plan", "citations": citation("A-202")}], "zones": [{"zone_id": "zone-1", "name": "Zone", "floor_id": "floor-1", "verification_status": "confirmed", "source": "Plan", "citations": citation("A-202")}], "rooms": [{
        "room_id": "room-1", "name": "Room", "zone_id": "zone-1", "mapping_status": "confirmed", "verification_status": "confirmed", "source": "Plan", "citations": citation("A-202"), "area_m2": 20, "occupancy": 0, "indoor_cooling_setpoint_c": 24,
        "heat_sources": [{"source_id": "source-1", "name": "None", "quantity": 0, "watts": 0, "kind": "other", "diversity_factor": 1, "space_gain_factor": 1, "verification_status": "confirmed", "source": "Brief", "citations": citation("Brief")}],
        "cooling_load": {"people_sensible_w_per_person": 0, "people_latent_w_per_person": 0, "people_diversity_factor": 1, "lighting_w_m2": 0, "lighting_diversity_factor": 1, "outside_air_lps": 0, "safety_factor": 1.1, "envelope_not_applicable": False, "envelope_surfaces": [], "verification_status": "confirmed", "source": "Basis"},
        "cooling_load_conditions": {"indoor_cooling_wet_bulb_c": 18, "verification_status": "confirmed", "source": "Basis"},
        "schedule_assignments": {"people": "", "lighting": "", "outside_air": "", "infiltration": "", "equipment": {}, "solar": {"window-1": "solar"}},
        "unapproved_components": [{"component_id": item, "component_type": item, "value": None, "unit": "", "source_room_id": "", "source": "Review", "citations": citation("Review"), "verification_status": "confirmed", "calculation_status": "not_present_confirmed", "method_id": "", "air_path": "", "flow_reference": ""} for item in component_types],
    }]}


def schedules():
    values = [0.0] * 24
    values[14] = 1.0
    return {"schedules": [{"schedule_id": "solar", "title": "Solar", "description": "", "status": "confirmed", "source": "Solar basis", "citations": citation("Solar basis"), "day_profiles": {"weekday": {"values": values, "status": "confirmed", "source": "Solar basis", "citations": citation("Solar basis")}, "saturday": {"values": [], "status": "missing", "source": "", "citations": []}, "sunday_holiday": {"values": [], "status": "missing", "source": "", "citations": []}}}]}


def scenarios():
    hours = [{"hour": hour, "outdoor_dry_bulb_c": {"value": 34, "status": "confirmed", "source": "Weather", "citations": citation("Weather")}, "outdoor_wet_bulb_c": {"value": 20, "status": "confirmed", "source": "Weather", "citations": citation("Weather")}} for hour in range(24)]
    return {"scenarios": [{"scenario_id": "summer", "title": "Summer", "mode": "cooling", "representative_month": "January", "day_type": "weekday", "status": "confirmed", "source": "Weather", "citations": citation("Weather"), "atmospheric_pressure_kpa": {"value": 101.325, "status": "confirmed", "source": "Weather", "citations": citation("Weather")}, "hours": hours}]}


def main():
    library, model = envelope()
    opaque, blocked, _stored = normalize_surfaces(library, model)
    check("gross wall derives net opaque area from every confirmed linked opening", not blocked and opaque[0]["area_m2"] == 8)
    hourly = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, model, approved_gate())
    report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), hourly, ["summer"], glazing_gate=approved_gate())
    hour_14 = report["scenario_results"][0]["rooms"][0]["hours"][14]
    check("reviewed glazing has separate conduction and solar components", hour_14["components"]["glazing_conduction"]["total_kw"] == 0.04 and hour_14["components"]["glazing_solar"]["total_kw"] == 0.45)
    check("solar schedule turns glazing solar off", report["scenario_results"][0]["rooms"][0]["hours"][13]["components"]["glazing_solar"]["total_kw"] == 0)
    blocked_hourly = apply_reviewed_envelope_to_hourly_model(hourly_model(), library, model)
    blocked_report = calculate_hourly_load_report(requirements(), schedules(), scenarios(), blocked_hourly, ["summer"])
    check("missing glazing gate leaves glazing out of the hourly payload", "glazing_conduction" not in blocked_report["scenario_results"][0]["rooms"][0]["hours"][14]["components"])
    invalid = deepcopy(model)
    invalid["surfaces"][0]["opening_coverage_status"] = "proposed"
    _opaque, invalid_blocked, _stored = normalize_surfaces(library, invalid)
    check("incomplete opening coverage blocks gross opaque calculation", invalid_blocked and "complete opening coverage" in invalid_blocked[0]["reason"])


if __name__ == "__main__":
    main()
