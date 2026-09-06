#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.design_requirements import validate_design_requirements
from ai.ventilation import calculate_ventilation_report


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def complete_requirements():
    return {
        "zones": [{
            "zone_id": "zone_001",
            "name": "Retail sales",
            "usage": "Retail",
            "source_room_labels": ["SALES"],
            "area_m2": 20,
            "occupancy": 10,
            "ventilation_requirements": {
                "process_type": "none",
                "basis_name": "Approved project ventilation table",
                "basis_source": "Mechanical designer record V1",
                "outside_air_method": "combined",
                "people_rate_lps_per_person": 5,
                "area_rate_lps_per_m2": 2,
                "fixed_minimum_lps": None,
                "process_exhaust_requirement": "not_required",
                "process_exhaust_lps": None,
                "hood_type_or_duty": "",
                "recirculable": "yes",
                "allowable_transfer_air_lps": 0,
                "allowable_outside_air_credit_lps": 0,
                "design_supply_lps_including_outside_air": 70,
                "return_or_relief_lps": 70,
                "dedicated_make_up_air_lps": 0,
                "verification_status": "confirmed",
                "source": "Designer-approved zone assumptions",
            },
        }],
    }


def report_for(data):
    return calculate_ventilation_report(validate_design_requirements(data))


def main():
    report = report_for(complete_requirements())
    zone = report["zone_results"][0]
    check("combined outside-air calculation uses largest component", zone["outside_air"]["required_lps"] == 50 and zone["outside_air"]["governing_component"] == "occupancy")
    check("entered air balance reconciles", zone["air_balance"]["status"] == "evaluated" and zone["air_balance"]["net_lps"] == 0)

    occupancy = complete_requirements()
    vent = occupancy["zones"][0]["ventilation_requirements"]
    vent.update({"outside_air_method": "occupancy", "area_rate_lps_per_m2": None})
    check("occupancy method calculates", report_for(occupancy)["zone_results"][0]["outside_air"]["required_lps"] == 50)

    area = complete_requirements()
    vent = area["zones"][0]["ventilation_requirements"]
    vent.update({"outside_air_method": "area", "people_rate_lps_per_person": None})
    check("area method calculates", report_for(area)["zone_results"][0]["outside_air"]["required_lps"] == 40)

    fixed = complete_requirements()
    vent = fixed["zones"][0]["ventilation_requirements"]
    vent.update({"outside_air_method": "fixed", "people_rate_lps_per_person": None, "area_rate_lps_per_m2": None, "fixed_minimum_lps": 60})
    check("fixed method calculates", report_for(fixed)["zone_results"][0]["outside_air"]["required_lps"] == 60)

    exhaust = complete_requirements()
    vent = exhaust["zones"][0]["ventilation_requirements"]
    vent.update({"process_exhaust_requirement": "required", "process_exhaust_lps": 100, "allowable_transfer_air_lps": 25, "allowable_outside_air_credit_lps": 35, "dedicated_make_up_air_lps": 40, "return_or_relief_lps": 35})
    exhaust_zone = report_for(exhaust)["zone_results"][0]
    check("make-up air applies explicit credits", exhaust_zone["make_up_air"]["required_lps"] == 40)
    check("air balance includes transfer and exhaust", exhaust_zone["air_balance"]["net_lps"] == 0)

    zero_make_up = complete_requirements()
    vent = zero_make_up["zones"][0]["ventilation_requirements"]
    vent.update({"process_exhaust_requirement": "required", "process_exhaust_lps": 50, "allowable_transfer_air_lps": 30, "allowable_outside_air_credit_lps": 30})
    check("make-up air never becomes negative", report_for(zero_make_up)["zone_results"][0]["make_up_air"]["required_lps"] == 0)

    provisional = complete_requirements()
    provisional["zones"][0]["ventilation_requirements"]["verification_status"] = "provisional"
    check("provisional ventilation stays visible", report_for(provisional)["status"] == "calculated_provisional")

    excessive_credit = complete_requirements()
    excessive_credit["zones"][0]["ventilation_requirements"]["allowable_outside_air_credit_lps"] = 80
    check("excessive outside-air credit warns", report_for(excessive_credit)["zone_results"][0]["warnings"])

    try:
        invalid = complete_requirements()
        invalid["zones"][0]["ventilation_requirements"]["people_rate_lps_per_person"] = -1
        validate_design_requirements(invalid)
        raise AssertionError("negative rate should fail")
    except ValueError as error:
        check("negative flow is rejected", "cannot be negative" in str(error))

    try:
        invalid = complete_requirements()
        invalid["zones"][0]["ventilation_requirements"].update({"process_type": "kitchen", "process_exhaust_requirement": "unknown"})
        validate_design_requirements(invalid)
        raise AssertionError("kitchen without exhaust requirement should fail")
    except ValueError as error:
        check("kitchen requires process exhaust", "process-exhaust" in str(error))

    try:
        invalid = complete_requirements()
        invalid["zones"][0]["ventilation_requirements"]["outside_air_method"] = "invented"
        validate_design_requirements(invalid)
        raise AssertionError("invalid method should fail")
    except ValueError as error:
        check("invalid outside-air method is rejected", "outside-air method" in str(error))

    try:
        invalid = complete_requirements()
        invalid["zones"][0]["ventilation_requirements"].update({"area_rate_lps_per_m2": None, "fixed_minimum_lps": None})
        validate_design_requirements(invalid)
        raise AssertionError("incomplete combined method should fail")
    except ValueError as error:
        check("combined method needs two rate bases", "two approved rate bases" in str(error))

    try:
        invalid = complete_requirements()
        invalid["zones"][0]["ventilation_requirements"].update({"process_type": "baking", "process_exhaust_requirement": "not_required"})
        validate_design_requirements(invalid)
        raise AssertionError("baking cannot omit process exhaust")
    except ValueError as error:
        check("baking cannot mark process exhaust not required", "process-exhaust" in str(error))

    old = validate_design_requirements({"zones": [{"zone_id": "zone_001", "name": "Existing"}]})
    check("existing zones remain valid without ventilation inputs", old["zones"][0]["ventilation_requirements"]["verification_status"] == "missing")


if __name__ == "__main__":
    main()
