#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.design_requirements import empty_design_requirements, requirements_summary, validate_design_requirements
from ai.reasoning_packet import build_reasoning_prompt


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def complete_requirements():
    return {
        "space_usage": "Retail tenancy",
        "occupancy": 12,
        "operating_hours": "Mon-Fri 08:00-18:00",
        "indoor_cooling_setpoint_c": 24,
        "indoor_heating_setpoint_c": 20,
        "outdoor_summer_db_c": 35,
        "outdoor_winter_db_c": 5,
        "fresh_air_basis": "AS 1668 designer basis",
        "exhaust_basis": "No process exhaust is required for this tenancy.",
        "cooking_activity": "none",
        "hood_requirement": "not_required",
        "exhaust_outcome": "not_required",
        "make_up_air_requirement": "not_required",
        "ceiling_height_mm": 3000,
        "ceiling_void_height_mm": 450,
        "heat_sources": [{
            "name": "Display fridge", "quantity": 2, "watts": 700,
            "verification_status": "confirmed", "source": "Manufacturer schedule",
        }],
        "existing_services": "Existing power and condensate point",
        "service_constraints": {"electrical_capacity": "Confirmed by landlord"},
        "code_basis": "NCC and AS 1668",
        "verification": {
            "occupancy": {"status": "confirmed", "source": "Client brief"},
            "design_conditions": {"status": "confirmed", "source": "Designer basis"},
            "outside_air": {"status": "confirmed", "source": "AS 1668"},
            "exhaust": {"status": "not_applicable", "source": "Client confirms no cooking process"},
            "heat_sources": {"status": "confirmed", "source": "Manufacturer schedule"},
            "ceiling": {"status": "confirmed", "source": "Architectural plan"},
            "existing_services": {"status": "confirmed", "source": "Site survey"},
        },
    }


def main():
    blank = empty_design_requirements()
    check("empty template blocks final design", requirements_summary(blank)["status"] == "final_design_blocked")

    valid = validate_design_requirements(complete_requirements())
    check("confirmed requirements complete final inputs", requirements_summary(valid)["status"] == "final_design_inputs_complete")

    old_record = complete_requirements()
    old_record.pop("zones", None)
    check("existing project requirements load without zones", validate_design_requirements(old_record)["zones"] == [])

    zoned = complete_requirements()
    zoned["zones"] = [{
        "zone_id": "zone_001",
        "name": "Sales area",
        "usage": "Retail sales",
        "source_room_labels": ["SHOP 31B"],
        "area_m2": 30,
        "occupancy": 12,
        "heat_sources": [{
            "name": "Display fridge", "quantity": 2, "watts": 700,
            "verification_status": "confirmed", "source": "Manufacturer schedule",
        }],
    }]
    zone_readiness = requirements_summary(zoned)["zone_readiness"][0]
    check("zone inherits project-wide operating conditions", "operating hours" in zone_readiness["inherited_inputs"])
    check("complete zone keeps final inputs ready", requirements_summary(zoned)["status"] == "final_design_inputs_complete")
    zone_prompt = build_reasoning_prompt({}, {}, {}, {}, [], [], [], [], requirements=zoned)
    check("reasoning prompt identifies designer-owned zones", "designer-owned HVAC design/control areas" in zone_prompt and "Sales area" in zone_prompt)

    incomplete_zone = complete_requirements()
    incomplete_zone["zones"] = [{"zone_id": "zone_001", "name": "Storage"}]
    check("incomplete zone blocks final design", requirements_summary(incomplete_zone)["final_design_blocked"])

    provisional = complete_requirements()
    provisional["verification"]["occupancy"]["status"] = "provisional"
    check("provisional inputs allow only brief", requirements_summary(provisional)["status"] == "brief_allowed")
    check("provisional inputs block final design", requirements_summary(provisional)["final_design_blocked"])

    unresolved = complete_requirements()
    unresolved["fresh_air_basis"] = "To be confirmed against the final occupancy."
    summary = requirements_summary(unresolved)
    check("unresolved text is provisional", "outside-air basis" in summary["provisional_inputs"])
    check("unresolved text blocks final design", summary["final_design_blocked"])

    no_exhaust_rationale = complete_requirements()
    no_exhaust_rationale["verification"]["exhaust"]["source"] = ""
    check("not applicable exhaust needs rationale", "exhaust basis" in requirements_summary(no_exhaust_rationale)["missing_inputs"])

    try:
        validate_design_requirements({"ceiling_height_mm": 3.2})
        raise AssertionError("3.2 mm ceiling height should fail")
    except ValueError as error:
        check("implausible ceiling height rejected", "whole-millimetre" in str(error))

    try:
        validate_design_requirements({"heat_sources": [{"name": "Oven", "quantity": 1, "watts": "bad"}]})
        raise AssertionError("invalid heat source wattage should fail")
    except ValueError as error:
        check("invalid heat-source wattage rejected", "must be numbers" in str(error))

    try:
        validate_design_requirements({"zones": [
            {"zone_id": "zone_001"}, {"zone_id": "zone_001"},
        ]})
        raise AssertionError("duplicate zone ID should fail")
    except ValueError as error:
        check("duplicate zone ID rejected", "duplicated" in str(error))

    try:
        validate_design_requirements({"zones": [{"zone_id": "zone_001", "area_m2": -2}]})
        raise AssertionError("negative zone area should fail")
    except ValueError as error:
        check("negative zone area rejected", "cannot be negative" in str(error))


if __name__ == "__main__":
    main()
