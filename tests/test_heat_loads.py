#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.design_requirements import validate_design_requirements
from ai.heat_loads import (
    calculate_heat_load_report,
    humidity_ratio_from_db_wb,
    lighting_load,
    outside_air_load,
    people_load,
)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def close(value, expected, tolerance=0.0002):
    return abs(value - expected) <= tolerance


def complete_cooling_requirements():
    return {
        "indoor_cooling_setpoint_c": 24,
        "outdoor_summer_db_c": 35,
        "cooling_load_conditions": {
            "indoor_cooling_wet_bulb_c": 18,
            "outdoor_summer_wet_bulb_c": 24,
            "atmospheric_pressure_kpa": 101.325,
            "verification_status": "confirmed",
            "source": "Designer summer design conditions",
        },
        "zones": [{
            "zone_id": "zone_001",
            "name": "Retail sales",
            "usage": "Retail",
            "source_room_labels": ["SALES"],
            "area_m2": 20,
            "occupancy": 10,
            "heat_sources": [{
                "name": "Display refrigerator",
                "quantity": 2,
                "watts": 1000,
                "kind": "refrigeration",
                "diversity_factor": 0.5,
                "space_gain_factor": 0.8,
                "verification_status": "confirmed",
                "source": "Manufacturer heat-rejection schedule",
            }],
            "cooling_load": {
                "people_sensible_w_per_person": 75,
                "people_latent_w_per_person": 55,
                "people_diversity_factor": 0.8,
                "lighting_w_m2": 10,
                "lighting_diversity_factor": 0.9,
                "outside_air_lps": 100,
                "safety_factor": 1.1,
                "envelope_not_applicable": False,
                "verification_status": "confirmed",
                "source": "Mechanical designer cooling-load basis",
                "envelope_surfaces": [{
                    "surface_id": "surface_001",
                    "kind": "glazing",
                    "orientation": "N",
                    "area_m2": 10,
                    "u_value_w_m2k": 0.5,
                    "solar_design_w_m2": 500,
                    "solar_gain_factor": 0.6,
                    "shading_factor": 0.5,
                    "verification_status": "confirmed",
                    "source": "Facade schedule and designer solar basis",
                }],
            },
        }],
    }


def main():
    people = people_load(10, 75, 55, 0.8)
    check("people sensible load", close(people["sensible_kw"], 0.6))
    check("people latent load", close(people["latent_kw"], 0.44))

    lighting = lighting_load(20, 10, 0.9)
    check("lighting load", close(lighting["total_kw"], 0.18))

    outside = outside_air_load(100, 24, 18, 35, 24, 101.325)
    check("outside air separates sensible and latent", outside["sensible_kw"] > 0 and outside["latent_kw"] > 0)
    check("outside air total reconciles", close(outside["total_kw"], outside["sensible_kw"] + outside["latent_kw"]))

    requirements = validate_design_requirements(complete_cooling_requirements())
    report = calculate_heat_load_report(requirements)
    zone = report["zone_results"][0]
    contributions = {item["name"]: item for item in zone["contributions"]}
    check("report calculates one zone", report["status"] == "calculated" and report["calculated_zone_count"] == 1)
    check("equipment refrigeration load", close(contributions["equipment_refrigeration"]["total_kw"], 0.8))
    check("envelope load", close(contributions["envelope"]["total_kw"], 0.055))
    check("solar load", close(contributions["solar"]["total_kw"], 1.5))
    check("safety allowance applies after subtotal", close(zone["design_total_kw"], zone["subtotal_kw"] * 1.1))

    missing_surface = complete_cooling_requirements()
    missing_surface["zones"][0]["cooling_load"]["envelope_surfaces"] = []
    blocked = calculate_heat_load_report(validate_design_requirements(missing_surface))["zone_results"][0]
    check("missing envelope declaration blocks zone", blocked["status"] == "blocked" and any("envelope" in item for item in blocked["blocked_reasons"]))

    internal_zone = complete_cooling_requirements()
    internal_zone["zones"][0]["cooling_load"]["envelope_surfaces"] = []
    internal_zone["zones"][0]["cooling_load"]["envelope_not_applicable"] = True
    internal = calculate_heat_load_report(validate_design_requirements(internal_zone))["zone_results"][0]
    check("internal zone may have no envelope", internal["status"] == "calculated")

    provisional = complete_cooling_requirements()
    provisional["zones"][0]["cooling_load"]["verification_status"] = "provisional"
    check("provisional basis remains visible", calculate_heat_load_report(validate_design_requirements(provisional))["status"] == "calculated_provisional")

    try:
        humidity_ratio_from_db_wb(24, 25, 101.325)
        raise AssertionError("wet bulb above dry bulb should fail")
    except ValueError as error:
        check("wet bulb validation", "cannot exceed" in str(error))

    try:
        invalid_factor = complete_cooling_requirements()
        invalid_factor["zones"][0]["cooling_load"]["people_diversity_factor"] = 1.2
        validate_design_requirements(invalid_factor)
        raise AssertionError("invalid diversity factor should fail")
    except ValueError as error:
        check("diversity factor validation", "between 0 and 1" in str(error))


if __name__ == "__main__":
    main()
