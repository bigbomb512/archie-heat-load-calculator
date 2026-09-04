#!/usr/bin/env python3

"""Deterministic, designer-input ventilation and exhaust calculations."""


def calculate_zone_ventilation(zone):
    requirements = zone.get("ventilation_requirements", {})
    missing = missing_inputs(zone, requirements)
    if missing:
        return blocked_zone(zone, missing)

    components = outside_air_components(zone, requirements)
    governing = max(components, key=lambda item: item["flow_lps"])
    outside_air_lps = governing["flow_lps"]
    exhaust_lps = requirements["process_exhaust_lps"] or 0.0
    transfer_lps = requirements["allowable_transfer_air_lps"] or 0.0
    outside_credit_lps = requirements["allowable_outside_air_credit_lps"] or 0.0
    make_up_lps = max(0.0, exhaust_lps - transfer_lps - outside_credit_lps)
    warnings = []
    if outside_credit_lps > outside_air_lps:
        warnings.append("Approved outside-air credit exceeds the calculated outside-air requirement.")
    if requirements["process_exhaust_requirement"] == "unknown":
        warnings.append("Process-exhaust requirement remains unknown.")
    if requirements["verification_status"] != "confirmed":
        warnings.append("Ventilation assumptions are provisional.")

    balance = air_balance(requirements, exhaust_lps, transfer_lps)
    return {
        "zone_id": zone["zone_id"],
        "zone_name": zone.get("name", zone["zone_id"]),
        "status": "calculated_provisional" if warnings else "calculated",
        "basis": {"name": requirements["basis_name"], "source": requirements["basis_source"]},
        "outside_air": {
            "required_lps": round(outside_air_lps, 3),
            "governing_component": governing["name"],
            "components": components,
            "formula": "max(selected occupancy, area, and fixed-minimum components)",
        },
        "process_exhaust_lps": round(exhaust_lps, 3),
        "make_up_air": {
            "required_lps": round(make_up_lps, 3),
            "transfer_credit_lps": round(transfer_lps, 3),
            "outside_air_credit_lps": round(outside_credit_lps, 3),
            "formula": "max(0, process exhaust − transfer credit − outside-air credit)",
        },
        "air_balance": balance,
        "warnings": warnings,
    }


def outside_air_components(zone, requirements):
    method = requirements["outside_air_method"]
    components = []
    if method in {"occupancy", "combined"} and requirements.get("people_rate_lps_per_person") is not None:
        components.append({
            "name": "occupancy",
            "flow_lps": round(zone["occupancy"] * requirements["people_rate_lps_per_person"], 3),
            "formula": "zone occupancy × approved L/s/person",
        })
    if method in {"area", "combined"} and requirements.get("area_rate_lps_per_m2") is not None:
        components.append({
            "name": "area",
            "flow_lps": round(zone["area_m2"] * requirements["area_rate_lps_per_m2"], 3),
            "formula": "zone area × approved L/s/m²",
        })
    if method in {"fixed", "combined"} and requirements.get("fixed_minimum_lps") is not None:
        components.append({"name": "fixed_minimum", "flow_lps": round(requirements["fixed_minimum_lps"], 3), "formula": "approved fixed minimum L/s"})
    return components


def air_balance(requirements, exhaust_lps, transfer_lps):
    entered = (
        requirements.get("design_supply_lps_including_outside_air"),
        requirements.get("return_or_relief_lps"),
        requirements.get("dedicated_make_up_air_lps"),
    )
    if any(value is None for value in entered):
        return {"status": "not_evaluated", "reason": "Enter supply, return/relief, and dedicated make-up-air flows to check zone balance."}
    supply_lps, return_lps, dedicated_make_up_lps = entered
    net_lps = supply_lps + dedicated_make_up_lps + transfer_lps - return_lps - exhaust_lps
    return {
        "status": "evaluated",
        "supply_lps_including_outside_air": round(supply_lps, 3),
        "return_or_relief_lps": round(return_lps, 3),
        "dedicated_make_up_air_lps": round(dedicated_make_up_lps, 3),
        "transfer_air_lps": round(transfer_lps, 3),
        "net_lps": round(net_lps, 3),
        "formula": "supply + dedicated make-up + transfer − return/relief − process exhaust",
    }


def missing_inputs(zone, requirements):
    missing = []
    for key, label in (("basis_name", "approved basis name"), ("basis_source", "approved basis source"), ("source", "ventilation source")):
        if not requirements.get(key):
            missing.append(label)
    if requirements.get("verification_status") == "missing":
        missing.append("ventilation verification status")
    method = requirements.get("outside_air_method")
    if method == "occupancy":
        if zone.get("occupancy") is None:
            missing.append("zone occupancy")
        if requirements.get("people_rate_lps_per_person") is None:
            missing.append("approved people rate")
    if method == "area":
        if zone.get("area_m2") is None:
            missing.append("zone area")
        if requirements.get("area_rate_lps_per_m2") is None:
            missing.append("approved area rate")
    if method == "fixed" and requirements.get("fixed_minimum_lps") is None:
        missing.append("approved fixed minimum")
    if method == "combined":
        rate_keys = ("people_rate_lps_per_person", "area_rate_lps_per_m2", "fixed_minimum_lps")
        rate_count = sum(requirements.get(key) is not None for key in rate_keys)
        if rate_count < 2:
            missing.append("two combined rate bases")
        if requirements.get("people_rate_lps_per_person") is not None and zone.get("occupancy") is None:
            missing.append("zone occupancy")
        if requirements.get("area_rate_lps_per_m2") is not None and zone.get("area_m2") is None:
            missing.append("zone area")
    if requirements.get("process_exhaust_requirement") == "required" and requirements.get("process_exhaust_lps") is None:
        missing.append("approved process exhaust")
    return missing


def blocked_zone(zone, reasons):
    return {
        "zone_id": zone.get("zone_id", ""),
        "zone_name": zone.get("name", zone.get("zone_id", "Unnamed zone")),
        "status": "blocked",
        "blocked_reasons": reasons,
        "warnings": [],
    }


def calculate_ventilation_report(requirements):
    results = [calculate_zone_ventilation(zone) for zone in requirements.get("zones", [])]
    calculated = [item for item in results if item["status"] != "blocked"]
    blocked = [item for item in results if item["status"] == "blocked"]
    provisional = [item for item in calculated if item["status"] == "calculated_provisional"]
    return {
        "report_type": "preliminary_zone_ventilation",
        "requirements_updated_at": requirements.get("updated_at", ""),
        "status": "blocked" if not calculated else ("calculated_provisional" if blocked or provisional else "calculated"),
        "calculation_basis": "Designer-entered metric ventilation inputs. No code rates are embedded.",
        "zone_results": results,
        "total_outside_air_lps": round(sum(item.get("outside_air", {}).get("required_lps", 0) for item in calculated), 3),
        "total_process_exhaust_lps": round(sum(item.get("process_exhaust_lps", 0) for item in calculated), 3),
        "total_required_make_up_air_lps": round(sum(item.get("make_up_air", {}).get("required_lps", 0) for item in calculated), 3),
        "calculated_zone_count": len(calculated),
        "blocked_zone_count": len(blocked),
    }
