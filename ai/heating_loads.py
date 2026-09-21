"""Separate, approval-gated hourly room heating calculations."""

from copy import deepcopy

from ai.heat_loads import contribution, humidity_ratio_from_db_wb, infiltration_flow_lps, specific_volume_m3_kg
from ai.heating_gate import empty_heating_method_gate, heating_gate_is_approved, validate_heating_method_gate
from ai.heating_readiness import assess_heating_readiness
from ai.glazing_gate import gate_is_approved as glazing_gate_is_approved
from ai.hourly_loads import (
    aggregate_floors,
    aggregate_project,
    aggregate_zones,
    artifact_snapshot,
    peak,
    room_volume_m3,
    validate_design_day_scenarios,
    validate_hourly_load_model,
    validate_schedule_library,
)
from ai.glazing_calculation import opening_area
from ai.infiltration_gate import empty_infiltration_method_gate, gate_is_approved as infiltration_gate_is_approved, validate_infiltration_method_gate


def heating_conduction(surfaces, outdoor_db_c, indoor_db_c):
    """Calculate positive sensible heat loss from reviewed opaque surfaces."""
    total = 0.0
    rows = []
    blocked = []
    for surface in surfaces or []:
        if surface.get("room_coupling", {}).get("enabled") or surface.get("dynamic_thermal_mass", {}).get("enabled"):
            blocked.append({"surface_id": surface.get("surface_id", ""), "reason": "dynamic envelope method is not integrated into heating V1"})
            continue
        boundary_method = surface.get("boundary_method", "external")
        boundary = outdoor_db_c if boundary_method == "external" else surface.get("boundary_temperature_c")
        if boundary is None:
            blocked.append({"surface_id": surface.get("surface_id", ""), "reason": "heating boundary temperature is missing"})
            continue
        area = surface.get("area_m2")
        u_value = surface.get("u_value_w_m2k")
        if not area or not u_value:
            blocked.append({"surface_id": surface.get("surface_id", ""), "reason": "heating surface area or U-value is missing"})
            continue
        signed_kw = float(u_value) * float(area) * (float(indoor_db_c) - float(boundary)) / 1000.0
        applied_kw = max(signed_kw, 0.0)
        total += applied_kw
        rows.append({
            "surface_id": surface.get("surface_id", ""),
            "boundary_method": boundary_method,
            "boundary_temperature_c": boundary,
            "indoor_temperature_c": indoor_db_c,
            "area_m2": area,
            "u_value_w_m2k": u_value,
            "raw_signed_heating_kw": round(signed_kw, 6),
            "applied_heating_kw": round(applied_kw, 6),
            "source": surface.get("source", ""),
            "citations": deepcopy(surface.get("citations", [])),
        })
    return contribution("heating_envelope", total, inputs={"surfaces": rows, "blocked": blocked}, formula="U × area × (indoor temperature − boundary temperature) ÷ 1000"), blocked


def heating_glazing_conduction(room, outdoor_db_c, indoor_db_c, glazing_gate):
    if not glazing_gate_is_approved(glazing_gate):
        return contribution("heating_glazing_conduction", 0.0, inputs={"blocked": "glazing method gate is not approved"}, formula="reviewed glazing U × opening area × (indoor − boundary) ÷ 1000"), [{"reason": "glazing method gate is not approved"}]
    total = 0.0
    rows = []
    blocked = []
    for surface in room.get("cooling_load", {}).get("glazing_surfaces", []):
        window = surface.get("window", {})
        boundary_method = surface.get("boundary_method", "external")
        boundary = outdoor_db_c if boundary_method == "external" else surface.get("boundary_temperature_c")
        if boundary is None:
            blocked.append({"surface_id": surface.get("surface_id", ""), "reason": "glazing boundary temperature is missing"})
            continue
        try:
            area = surface.get("explicit_opening_area_m2")
            if area in (None, ""):
                area = opening_area(surface.get("opening_width_m"), surface.get("opening_height_m"), surface.get("opening_quantity"))
            u_value = float(window["u_value_w_m2k"])
            if u_value <= 0:
                raise ValueError("glazing U-value must be positive")
            signed_kw = u_value * float(area) * (float(indoor_db_c) - float(boundary)) / 1000.0
        except (KeyError, TypeError, ValueError) as error:
            blocked.append({"surface_id": surface.get("surface_id", ""), "reason": str(error)})
            continue
        applied_kw = max(signed_kw, 0.0)
        total += applied_kw
        rows.append({
            "surface_id": surface.get("surface_id", ""), "opening_area_m2": round(float(area), 6),
            "u_value_w_m2k": u_value, "boundary_temperature_c": boundary,
            "raw_signed_heating_kw": round(signed_kw, 6), "applied_heating_kw": round(applied_kw, 6),
            "source": surface.get("source", ""), "citations": deepcopy(surface.get("citations", [])),
        })
    return contribution("heating_glazing_conduction", total, inputs={"openings": rows, "blocked": blocked}, formula="overall-window U × opening area × (indoor temperature − boundary temperature) ÷ 1000"), blocked


def heating_air_load(flow_lps, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa, name="heating_outside_air"):
    outdoor_ratio = humidity_ratio_from_db_wb(outdoor_db_c, outdoor_wb_c, pressure_kpa)
    mass_flow = flow_lps / 1000.0 / specific_volume_m3_kg(outdoor_db_c, outdoor_ratio, pressure_kpa)
    signed = mass_flow * 1.006 * (outdoor_db_c - indoor_db_c)
    return contribution(
        name, max(-signed, 0.0), inputs={"flow_lps": flow_lps, "indoor_db_c": indoor_db_c, "outdoor_db_c": outdoor_db_c, "outdoor_wb_c": outdoor_wb_c, "atmospheric_pressure_kpa": pressure_kpa, "mass_flow_kg_s": round(mass_flow, 6), "raw_signed_sensible_kw": signed, "applied_heating_kw": max(-signed, 0.0)},
        formula="outside-air mass flow × air heat capacity × (indoor DB − outdoor DB)",
    )


def heating_infiltration_load(value, unit, indoor_db_c, indoor_wb_c, outdoor_db_c, outdoor_wb_c, pressure_kpa, *, room_volume_m3=None, schedule_factor=1.0, method_id="", gate_version=""):
    base_flow = infiltration_flow_lps(value, unit, room_volume_m3)
    applied_flow = base_flow * schedule_factor
    outdoor_ratio = humidity_ratio_from_db_wb(outdoor_db_c, outdoor_wb_c, pressure_kpa)
    mass_flow = applied_flow / 1000.0 / specific_volume_m3_kg(outdoor_db_c, outdoor_ratio, pressure_kpa)
    signed = mass_flow * 1.006 * (outdoor_db_c - indoor_db_c)
    return contribution(
        "heating_infiltration", max(-signed, 0.0), inputs={
            "flow_lps": applied_flow, "outdoor_db_c": outdoor_db_c, "outdoor_wb_c": outdoor_wb_c, "atmospheric_pressure_kpa": pressure_kpa,
            "input_value": value, "input_unit": unit,
            "resolved_flow_lps": round(base_flow, 6), "applied_flow_lps": round(applied_flow, 6),
            "room_volume_m3": room_volume_m3, "schedule_factor": schedule_factor,
            "raw_signed_sensible_kw": signed, "raw_signed_latent_kw": 0.0,
            "method_id": method_id, "gate_version": gate_version,
        }, formula="approved infiltration flow × air heat capacity × (indoor DB − outdoor DB)",
    )


def _heating_room_blockers(room, zone, heating_gate, infiltration_gate):
    blockers = []
    if room.get("heating_applicability", "not_assessed") != "confirmed":
        blockers.append("heating applicability is not confirmed")
    if room.get("indoor_heating_setpoint_c") in (None, "") or not room.get("heating_setpoint_source"):
        blockers.append("cited indoor heating setpoint is missing")
    if room.get("verification_status") not in {"confirmed", "provisional"} or room.get("mapping_status") != "confirmed":
        blockers.append("room topology is not confirmed")
    if room.get("cooling_load", {}).get("outside_air_lps") is None:
        blockers.append("cited outside-air airflow is missing")
    if room.get("heating_internal_gain_policy") != "explicit_sensible_only":
        blockers.append("heating internal-gain policy is invalid")
    if room.get("heating_internal_gain_status") not in {"confirmed", "not_applicable"}:
        blockers.append("heating internal-gain credit decision is missing")
    if room.get("heating_safety_factor") is None:
        blockers.append("cited heating safety factor is missing")
    for source in room.get("heat_sources", []):
        if source.get("heating_credit_status", "not_assessed") not in {"confirmed", "not_applicable"}:
            blockers.append(f"equipment {source.get('source_id', '')} heating credit is unresolved")
    infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration"), None)
    if infiltration and infiltration.get("calculation_status") == "calculated" and not infiltration_gate_is_approved(infiltration_gate):
        blockers.append("approved infiltration method gate is missing")
    return blockers


def heating_internal_gain_credit(room, profiles, hour, gross_heating_sensible_kw):
    """Return a negative sensible component for eligible, scheduled gains."""
    status = room.get("heating_internal_gain_status", "not_assessed")
    if status == "not_applicable":
        return contribution("heating_internal_gain_credit", 0.0, inputs={"policy": "explicit_sensible_only", "status": status, "requested_credit_kw": 0.0, "applied_credit_kw": 0.0, "sources": []}, formula="no internal sensible gain credit declared"), []
    load = room.get("cooling_load", {})
    sources = []
    omitted_sources = []
    requested = 0.0
    occupancy = float(room.get("occupancy") or 0.0)
    people_w = float(load.get("people_sensible_w_per_person") or 0.0)
    people_factor = _profile_factor(profiles, "people", hour)
    people_kw = occupancy * people_w * people_factor / 1000.0
    if people_kw:
        requested += people_kw
        sources.append({"source_type": "people", "requested_kw": round(people_kw, 6), "schedule_factor": people_factor, "source": load.get("source", ""), "citations": deepcopy(room.get("citations", []))})
    area = float(room.get("area_m2") or 0.0)
    lighting_w = float(load.get("lighting_w_m2") or 0.0)
    lighting_factor = _profile_factor(profiles, "lighting", hour)
    lighting_kw = area * lighting_w * lighting_factor / 1000.0
    if lighting_kw:
        requested += lighting_kw
        sources.append({"source_type": "lighting", "requested_kw": round(lighting_kw, 6), "schedule_factor": lighting_factor, "source": load.get("source", ""), "citations": deepcopy(room.get("citations", []))})
    for source in room.get("heat_sources", []):
        if source.get("heating_credit_status") != "confirmed":
            omitted_sources.append({
                "source_type": "equipment",
                "source_id": source.get("source_id", ""),
                "reason": "equipment heat-to-space is not explicitly confirmed",
                "source": source.get("heating_credit_source", ""),
                "citations": deepcopy(source.get("heating_credit_citations", [])),
            })
            continue
        watts = float(source.get("heating_heat_to_space_watts") or 0.0) * float(source.get("quantity") or 0.0)
        factor = _profile_factor(profiles, f"equipment:{source.get('source_id', '')}", hour)
        equipment_kw = watts * factor / 1000.0
        if equipment_kw:
            requested += equipment_kw
            sources.append({"source_type": "equipment", "source_id": source.get("source_id", ""), "requested_kw": round(equipment_kw, 6), "schedule_factor": factor, "source": source.get("heating_credit_source", ""), "citations": deepcopy(source.get("heating_credit_citations", []))})
    applied = min(max(float(gross_heating_sensible_kw), 0.0), requested)
    return contribution(
        "heating_internal_gain_credit", -applied,
        inputs={"policy": "explicit_sensible_only", "status": status, "requested_credit_kw": round(requested, 6), "applied_credit_kw": round(applied, 6), "sources": sources, "omitted_sources": omitted_sources, "latent_credits_kw": 0.0, "solar_credits_kw": 0.0},
        formula="−min(gross heating sensible, scheduled explicit sensible gains)",
    ), []


def calculate_heating_report(requirements, schedule_library, scenarios, model, selected_scenario_ids, *, glazing_gate=None, infiltration_gate=None, heating_gate=None, coverage=None):
    requirements = deepcopy(requirements or {})
    schedule_library = artifact_snapshot(schedule_library, validate_schedule_library)
    scenarios = artifact_snapshot(scenarios, validate_design_day_scenarios)
    model = artifact_snapshot(model, validate_hourly_load_model)
    heating_gate = validate_heating_method_gate(heating_gate or empty_heating_method_gate())
    infiltration_gate = validate_infiltration_method_gate(infiltration_gate or empty_infiltration_method_gate())
    selected = [item for item in scenarios["scenarios"] if item["scenario_id"] in set(selected_scenario_ids or []) and item["mode"] == "heating"]
    report = {
        "report_schema_version": 1, "report_type": "hourly_heating_load", "status": "blocked",
        "input_fingerprints": {"hourly_load_model_updated_at": model.get("updated_at", ""), "schedule_library_updated_at": schedule_library.get("updated_at", ""), "design_day_scenarios_updated_at": scenarios.get("updated_at", ""), "heating_method_gate_fingerprint": heating_gate.get("fingerprint", "")},
        "scenario_results": [], "included_scope_peak": {}, "project_peak": {}, "blocked_reasons": [], "coverage": deepcopy(coverage or {}),
        "excluded_components": ["heating latent/humidification credits", "heating solar credits", "unreviewed equipment credits", "AHU and plant effects"],
        "readiness": {"status": "blocked", "issues": []}, "stale_reasons": [],
    }
    if not selected:
        report["blocked_reasons"].append("Select at least one heating design-day scenario.")
        report["readiness"] = assess_heating_readiness(report, heating_gate)
        return report
    floors = {item["floor_id"]: item for item in model["floors"]}
    zones = {item["zone_id"]: item for item in model["zones"]}
    for scenario in selected:
        scenario_issues = _heating_scenario_issues(scenario)
        result = _calculate_heating_scenario(requirements, schedule_library, scenario, model["rooms"], zones, floors, glazing_gate, infiltration_gate, heating_gate, scenario_issues)
        report["scenario_results"].append(result)
    report["included_scope_peak"] = _governing_heating_peak(report["scenario_results"])
    complete_results = [item for item in report["scenario_results"] if item.get("status") == "review_ready" and item.get("scope_summary", {}).get("complete_scope")]
    report["scope_summary"] = deepcopy((complete_results[0] if complete_results else next((item for item in report["scenario_results"] if item.get("included_scope_peak")), report["scenario_results"][0])).get("scope_summary", {}))
    if complete_results:
        report["project_peak"] = deepcopy(report["included_scope_peak"])
        report["status"] = "review_ready"
    elif report["included_scope_peak"]:
        report["status"] = "draft"
    else:
        report["blocked_reasons"].extend(reason for item in report["scenario_results"] for reason in item.get("blocked_reasons", []))
    report["readiness"] = assess_heating_readiness(report, heating_gate)
    return report


def _heating_scenario_issues(scenario):
    issues = []
    if scenario.get("status") not in {"confirmed", "provisional"}:
        issues.append("heating scenario must be confirmed or provisional")
    if len(scenario.get("hours", [])) != 24:
        issues.append("heating scenario requires 24 hourly weather records")
    if not scenario.get("source") or not scenario.get("citations"):
        issues.append("heating scenario source and citations are required")
    pressure = scenario.get("atmospheric_pressure_kpa", {})
    if pressure.get("value") is None or not pressure.get("source") or not pressure.get("citations"):
        issues.append("heating scenario atmospheric pressure source and citations are required")
    for row in scenario.get("hours", []):
        for key in ("outdoor_dry_bulb_c", "outdoor_wet_bulb_c"):
            value = row.get(key, {})
            if value.get("value") is None or not value.get("source") or not value.get("citations"):
                issues.append(f"heating scenario hour {row.get('hour')} {key} source and citations are required")
    return sorted(set(issues))


def _calculate_heating_scenario(requirements, library, scenario, rooms, zones, floors, glazing_gate, infiltration_gate, heating_gate, scenario_issues=None):
    scenario_issues = scenario_issues or []
    if scenario_issues:
        return {"scenario_id": scenario["scenario_id"], "title": scenario["title"], "mode": "heating", "status": "blocked", "rooms": [], "zones": [], "floors": [], "included_scope_hours": [], "included_scope_peak": {}, "scope_summary": {"active_room_ids": [], "included_room_ids": [], "blocked_rooms": [], "complete_scope": False}, "blocked_reasons": scenario_issues}
    room_results = []
    weather_rows = scenario.get("hours", [])
    for room in rooms:
        zone = zones.get(room["zone_id"], {})
        blockers = _heating_room_blockers(room, zone, heating_gate, infiltration_gate)
        profiles, profile_missing, provisional = _heating_profiles(library, scenario["day_type"], room)
        blockers.extend(profile_missing)
        result = {"room_id": room["room_id"], "name": room["name"], "zone_id": room["zone_id"], "status": "blocked", "hours": [], "peak": {}, "blocked_reasons": sorted(set(blockers))}
        if blockers:
            room_results.append(result)
            continue
        setpoint = room["indoor_heating_setpoint_c"]
        for weather in weather_rows:
            hour = weather["hour"]
            outdoor_db = weather["outdoor_dry_bulb_c"]["value"]
            outdoor_wb = weather["outdoor_wet_bulb_c"]["value"]
            pressure = scenario["atmospheric_pressure_kpa"]["value"]
            envelope, envelope_blocked = heating_conduction(room["cooling_load"].get("envelope_surfaces", []), outdoor_db, setpoint)
            glazing, glazing_blocked = heating_glazing_conduction(room, outdoor_db, setpoint, glazing_gate)
            contributions = [envelope, glazing]
            flow = room["cooling_load"].get("outside_air_lps")
            if flow:
                contributions.append(heating_air_load(flow * _profile_factor(profiles, "outside_air", hour), setpoint, room["cooling_load_conditions"].get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure))
            infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration" and item.get("calculation_status") == "calculated"), None)
            if infiltration:
                factor = _profile_factor(profiles, "infiltration", hour)
                contributions.append(heating_infiltration_load(infiltration["value"], infiltration["unit"], setpoint, room["cooling_load_conditions"].get("indoor_cooling_wet_bulb_c", setpoint), outdoor_db, outdoor_wb, pressure, room_volume_m3=room_volume_m3(room, zone), schedule_factor=factor, method_id=infiltration.get("method_id", ""), gate_version=infiltration_gate.get("updated_at", "")))
            gross = round(sum(max(item["sensible_kw"], 0.0) for item in contributions), 4)
            credit, _credit_blocked = heating_internal_gain_credit(room, profiles, hour, gross)
            net = round(max(0.0, gross + credit["sensible_kw"]), 4)
            safety_factor = float(room["heating_safety_factor"])
            safety_allowance = round(net * (safety_factor - 1.0), 4)
            design_total = round(net + safety_allowance, 4)
            contributions.append(credit)
            inputs = {"blocked_surfaces": envelope_blocked + glazing_blocked, "setpoint_c": setpoint, "outdoor_db_c": outdoor_db, "gross_heating_sensible_kw": gross, "requested_internal_gain_credit_kw": credit["inputs"]["requested_credit_kw"], "applied_internal_gain_credit_kw": credit["inputs"]["applied_credit_kw"], "net_heating_sensible_kw": net, "heating_safety_factor": safety_factor, "heating_safety_factor_source": room.get("heating_safety_factor_source", ""), "heating_safety_factor_citations": deepcopy(room.get("heating_safety_factor_citations", []))}
            result["hours"].append({"hour": hour, "components": {item["name"]: {"sensible_kw": item["sensible_kw"], "latent_kw": 0.0, "total_kw": item["total_kw"], "inputs": item.get("inputs", {}), "input_rows": [item.get("inputs", {})], "formula": item.get("formula", "")} for item in contributions}, "subtotal_sensible_kw": net, "subtotal_latent_kw": 0.0, "subtotal_kw": net, "design_total_kw": design_total, "safety_allowance_kw": safety_allowance, "inputs": inputs})
        result["peak"] = _heating_peak(result["hours"])
        result["status"] = "draft" if provisional or not heating_gate_is_approved(heating_gate) else "review_ready"
        room_results.append(result)
    calculated = [item for item in room_results if item["status"] != "blocked"]
    zone_results = aggregate_zones(calculated, zones) if calculated else []
    floor_results = aggregate_floors(zone_results, floors) if zone_results else []
    for aggregate in zone_results + floor_results:
        aggregate["peak"] = _heating_peak(aggregate["hours"])
    project_hours = aggregate_project(zone_results) if zone_results else []
    blocked = [{"room_id": item["room_id"], "reasons": item["blocked_reasons"]} for item in room_results if item["status"] == "blocked"]
    complete = bool(room_results) and not blocked and all(item["status"] == "review_ready" for item in room_results)
    return {"scenario_id": scenario["scenario_id"], "title": scenario["title"], "mode": "heating", "status": "review_ready" if complete else ("draft" if calculated else "blocked"), "rooms": room_results, "zones": zone_results, "floors": floor_results, "included_scope_hours": project_hours, "included_scope_peak": _heating_peak(project_hours), "scope_summary": {"active_room_ids": [item["room_id"] for item in room_results], "included_room_ids": [item["room_id"] for item in calculated], "blocked_rooms": blocked, "complete_scope": complete}, "blocked_reasons": []}


def _profile_factor(profiles, name, hour):
    return float(profiles.get(name, [0.0] * 24)[hour])


def _heating_profiles(library, day_type, room):
    """Resolve only the existing air-path schedules needed by heating."""
    lookup = {item["schedule_id"]: item for item in library["schedules"]}
    profiles, missing, provisional = {}, [], False
    assignments = room.get("schedule_assignments", {})
    required = {}
    outside_air = room.get("cooling_load", {}).get("outside_air_lps")
    if outside_air is not None and float(outside_air) > 0:
        required["outside_air"] = assignments.get("outside_air", "")
    if room.get("heating_internal_gain_status") == "confirmed":
        load = room.get("cooling_load", {})
        if float(room.get("occupancy") or 0.0) * float(load.get("people_sensible_w_per_person") or 0.0) > 0:
            required["people"] = assignments.get("people", "")
        if float(room.get("area_m2") or 0.0) * float(load.get("lighting_w_m2") or 0.0) > 0:
            required["lighting"] = assignments.get("lighting", "")
        for source in room.get("heat_sources", []):
            if source.get("heating_credit_status") == "confirmed" and float(source.get("heating_heat_to_space_watts") or 0.0) * float(source.get("quantity") or 0.0) > 0:
                required[f"equipment:{source.get('source_id', '')}"] = assignments.get("equipment", {}).get(source.get("source_id", ""), "")
    infiltration = next((item for item in room.get("unapproved_components", []) if item.get("component_type") == "infiltration" and item.get("calculation_status") == "calculated"), None)
    if infiltration:
        required["infiltration"] = assignments.get("infiltration", "")
    for name, schedule_id in required.items():
        if not schedule_id:
            missing.append(f"{name} heating schedule assignment")
            continue
        schedule = lookup.get(schedule_id)
        profile = schedule.get("day_profiles", {}).get(day_type) if schedule else None
        if not schedule or not profile or schedule.get("status") in {"missing", "not_applicable"} or profile.get("status") in {"missing", "not_applicable"} or len(profile.get("values", [])) != 24 or not profile.get("source") or not profile.get("citations"):
            missing.append(f"{name} schedule has no usable {day_type} profile")
            continue
        profiles[name] = profile["values"]
        provisional = provisional or schedule.get("status") != "confirmed" or profile.get("status") != "confirmed"
    return profiles, missing, provisional


def _governing_heating_peak(results):
    candidates = [item for item in results if item.get("included_scope_peak")]
    if not candidates:
        return {}
    return deepcopy(max(candidates, key=lambda item: item["included_scope_peak"].get("design_total_kw", 0))["included_scope_peak"])


def _heating_peak(hours):
    result = peak(hours)
    if result:
        display = next(item for item in hours if item["hour"] == result["display_hour"])
        result["safety_allowance_kw"] = display.get("safety_allowance_kw", 0.0)
    return result
