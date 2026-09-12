#!/usr/bin/env python3

"""Temporary internal-only calculation sanity harness.

This is development QA, not a contractor-facing feature and not a CAMEL+
validation suite. Expected values below are fixed independent spot checks so
the tests do not derive their answers by calling the same Archie function.
"""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.heat_loads import (
    envelope_load,
    equipment_load,
    humidity_ratio_from_db_wb,
    infiltration_flow_lps,
    infiltration_load,
    lighting_load,
    moist_air_enthalpy_kj_kg,
    outside_air_load,
    people_load,
    saturation_pressure_kpa,
    specific_volume_m3_kg,
    solar_load,
    contribution,
)
from ai.hourly_loads import (
    aggregate_floors,
    aggregate_project,
    aggregate_zones,
    calculate_hourly_load_report,
    hour_total,
    peak,
    scale,
)
from ai.design_requirements import validate_design_requirements
from tests.test_hourly_loads import library as hourly_library
from tests.test_hourly_loads import requirements_data, reviewed_model, scenarios as hourly_scenarios


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def close(value, expected, tolerance=0.0002):
    return abs(value - expected) <= tolerance


def main():
    # Internal gains: fixed hand-calculated values.
    people = people_load(10, 75, 55, 0.8)
    check("people sensible spot check", close(people["sensible_kw"], 0.6))
    check("people latent spot check", close(people["latent_kw"], 0.44))

    lighting = lighting_load(20, 10, 0.9)
    check("lighting spot check", close(lighting["total_kw"], 0.18))

    equipment = equipment_load([{
        "name": "Display refrigerator", "kind": "refrigeration", "quantity": 2,
        "watts": 1000, "diversity_factor": 0.5, "space_gain_factor": 0.8,
    }])
    check("equipment spot check", close(equipment["total_kw"], 0.8))

    # Steady-state envelope and manual solar.
    surfaces = [{
        "surface_id": "wall-1", "orientation": "N", "area_m2": 10,
        "u_value_w_m2k": 0.5, "boundary_temperature_c": 35,
        "boundary_method": "external", "construction_id": "wall-a",
    }]
    conduction = envelope_load(surfaces, 35, 24)
    check("external UA delta-T spot check", close(conduction["total_kw"], 0.055))
    check("warmer boundary produces positive conduction", conduction["total_kw"] > 0)
    larger = envelope_load([{**surfaces[0], "area_m2": 20}], 35, 24)
    check("conduction increases with area", larger["total_kw"] > conduction["total_kw"])

    solar = solar_load([{
        "surface_id": "window-1", "orientation": "N", "area_m2": 10,
        "solar_design_w_m2": 500, "solar_gain_factor": 0.6,
        "shading_factor": 0.5,
    }])
    check("manual solar spot check", close(solar["total_kw"], 1.5))

    # Psychrometric fixed-vector checks. These are temporary comparison values,
    # not an authorised CAMEL+ benchmark.
    pressure = 101.325
    indoor_w = humidity_ratio_from_db_wb(24, 18, pressure)
    outdoor_w = humidity_ratio_from_db_wb(35, 24, pressure)
    check("saturation pressure at 24 C", close(saturation_pressure_kpa(24), 2.9781, 0.002))
    check("indoor humidity-ratio spot check", close(indoor_w, 0.01029667, 0.00001))
    check("outdoor humidity-ratio spot check", close(outdoor_w, 0.01394566, 0.00001))
    check("moist-air enthalpy spot check", close(moist_air_enthalpy_kj_kg(24, indoor_w), 50.3556, 0.002))
    check("specific-volume spot check", close(specific_volume_m3_kg(24, indoor_w, pressure), 0.855759, 0.00001))

    outside = outside_air_load(100, 24, 18, 35, 24, pressure)
    check("outside-air sensible spot check", close(outside["sensible_kw"], 1.2398, 0.001))
    check("outside-air latent spot check", close(outside["latent_kw"], 1.0727, 0.001))
    check("outside-air total reconciles", close(outside["total_kw"], outside["sensible_kw"] + outside["latent_kw"]))
    more_air = outside_air_load(200, 24, 18, 35, 24, pressure)
    check("positive outside-air load increases with flow", more_air["total_kw"] > outside["total_kw"])

    # Infiltration units and positive-only applied cooling contribution.
    expected_flow = 6.0
    check("ACH conversion", close(infiltration_flow_lps(0.36, "ACH", 60), expected_flow))
    check("L/s conversion", close(infiltration_flow_lps(6, "L/s"), expected_flow))
    check("m3/s conversion", close(infiltration_flow_lps(0.006, "m3/s"), expected_flow))
    check("m3/h conversion", close(infiltration_flow_lps(21.6, "m3/h"), expected_flow))
    ach = infiltration_load(0.36, "ACH", 24, 18, 35, 24, pressure, room_volume_m3=60)
    direct = infiltration_load(6, "L/s", 24, 18, 35, 24, pressure)
    check("ACH and direct airflow produce equal infiltration load", close(ach["total_kw"], direct["total_kw"]))
    off = infiltration_load(6, "L/s", 24, 18, 35, 24, pressure, schedule_factor=0)
    check("zero infiltration schedule produces zero applied load", off["total_kw"] == 0)
    check("infiltration retains signed diagnostics", "raw_signed_sensible_kw" in ach["inputs"] and "raw_signed_latent_kw" in ach["inputs"])

    # Schedule scaling and one-time safety factor.
    half = scale(people, 0.5, "people")
    zero = scale(people, 0.0, "people")
    check("schedule halves a component", close(half["total_kw"], 0.52))
    check("zero schedule removes a component", zero["total_kw"] == 0)
    total = hour_total(14, [people], 1.1)
    check("safety factor is applied once", close(total["subtotal_kw"], 1.04) and close(total["design_total_kw"], 1.144))

    # Coincident aggregation: separate room peaks must not be added together.
    zero_row = hour_total(0, [contribution("test", 0, 0)], 1.0)
    room_one_hours = [zero_row.copy() for _ in range(24)]
    room_two_hours = [zero_row.copy() for _ in range(24)]
    room_one_hours[13] = hour_total(13, [contribution("test", 1.0, 0)], 1.0)
    room_two_hours[14] = hour_total(14, [contribution("test", 2.0, 0)], 1.0)
    rooms = [
        {"room_id": "room-1", "zone_id": "zone-1", "hours": room_one_hours},
        {"room_id": "room-2", "zone_id": "zone-1", "hours": room_two_hours},
    ]
    zones = aggregate_zones(rooms, {"zone-1": {"name": "Zone 1", "floor_id": "floor-1"}})
    floors = aggregate_floors(zones, {"floor-1": {"name": "Floor 1"}})
    project = aggregate_project(zones)
    project_peak = peak(project)
    check("zone aggregation sums same-hour room loads", zones[0]["hours"][13]["subtotal_kw"] == 1.0 and zones[0]["hours"][14]["subtotal_kw"] == 2.0)
    check("floor aggregation preserves zone total", floors[0]["hours"][14]["subtotal_kw"] == 2.0)
    check("project peak uses coincident hour", project_peak["design_total_kw"] == 2.0 and project_peak["display_hour"] == 14)
    check("individual non-coincident peaks are not added", project_peak["design_total_kw"] < 3.0)

    # Full report safety: an omitted active room produces an included-scope
    # subtotal but never a complete project peak.
    requirements = validate_design_requirements(requirements_data())
    partial_model = reviewed_model(requirements)
    blocked_room = deepcopy(partial_model["rooms"][0])
    blocked_room["room_id"] = "blocked-room"
    blocked_room["name"] = "Blocked room"
    blocked_room["schedule_assignments"]["people"] = ""
    partial_model["rooms"].append(blocked_room)
    partial_report = calculate_hourly_load_report(
        requirements, hourly_library(), hourly_scenarios(), partial_model, ["jan_weekday"]
    )
    check("blocked room suppresses complete project peak", not partial_report["project_peak"] and partial_report["included_scope_peak"])

    print("POLICY OPEN - negative conduction treatment requires a product decision.")
    print("POLICY OPEN - negative sensible/latent air-load treatment requires a product decision.")
    print("POLICY OPEN - outside-air and infiltration duplication requires project validation.")
    print("POLICY OPEN - safety-factor scope and psychrometric validity limits require method approval.")
    print("TEMPORARY SANITY HARNESS - development confidence only; not CAMEL+ validation.")


if __name__ == "__main__":
    main()
