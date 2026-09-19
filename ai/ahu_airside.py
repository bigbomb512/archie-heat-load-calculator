"""Gated cooling AHU and air-side calculations.

The module consumes an existing room-level hourly cooling report.  It does not
modify room physics or reinterpret missing system data.  Every air-side value
that can affect a coil result is explicit, reviewed, and cited.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

from ai.heat_loads import (
    humidity_ratio_from_db_wb,
    moist_air_enthalpy_kj_kg,
    specific_volume_m3_kg,
)
from ai.site_design_conditions import validate_citations


SYSTEM_TYPES = {"single_zone_constant_volume", "vav"}
NODE_TYPES = {"outside_air", "return_air", "mixed_air", "supply_air", "room_return", "exhaust_air", "relief_air", "make_up_air"}
PATH_TYPES = {"supply", "return", "outside_air", "exhaust", "relief", "make_up", "leakage"}
STATUSES = {"missing", "provisional", "confirmed"}
METHOD_ID = "ahu_airside_cooling_v1"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_ahu_systems():
    return {"schema_version": 1, "updated_at": "", "systems": []}


def empty_air_side_model():
    return {
        "schema_version": 1,
        "updated_at": "",
        "outside_air_ownership": "central_ahu",
        "airflow_records": [],
        "fans": [],
        "duct_effects": [],
        "leakage": [],
        "heat_recovery": [],
        "preconditioning": [],
        "coils": [],
    }


def empty_air_side_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "method_version": "1.0",
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": "Cooling AHU aggregation, explicit air paths, fixed recovery/preconditioning, and mixed-air coil states.",
        "citations": [],
    }


def validate_air_side_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Air-side method gate must be an object.")
    result = deepcopy(empty_air_side_method_gate())
    result.update({key: raw.get(key, value) for key, value in result.items()})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Air-side method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in {"placeholder", "approved"}:
        raise ValueError("Air-side method gate approval status must be placeholder or approved.")
    for key in ("method_version", "engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[key] = str(result.get(key, "") or "").strip()
    result["citations"] = validate_citations(result.get("citations", []), "Air-side method gate")
    if result["approval_status"] == "approved":
        missing = [key for key in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[key]]
        if missing or not result["citations"]:
            raise ValueError("Approved air-side method gate requires engineer metadata, scope, and citations.")
    result["fingerprint"] = fingerprint(result)
    return result


def air_side_gate_is_approved(gate):
    return bool(gate and gate.get("method_id") == METHOD_ID and gate.get("approval_status") == "approved")


def validate_ahu_systems(raw, known_zone_ids=None):
    if not isinstance(raw, dict):
        raise ValueError("AHU systems must be an object.")
    rows = raw.get("systems", [])
    if not isinstance(rows, list):
        raise ValueError("AHU systems must contain a systems list.")
    known_zone_ids = set(known_zone_ids or [])
    systems, seen, assigned = [], set(), {}
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"AHU system {index} must be an object.")
        ahu_id = _id(item.get("ahu_id", ""), f"AHU system {index} ID")
        if ahu_id in seen:
            raise ValueError(f"AHU system ID '{ahu_id}' is duplicated.")
        system_type = str(item.get("system_type", "")).strip()
        if system_type not in SYSTEM_TYPES:
            raise ValueError(f"AHU {ahu_id} system type must be single_zone_constant_volume or vav.")
        number_off = _positive_int(item.get("number_off"), f"AHU {ahu_id} number-off")
        zones = item.get("served_zone_ids", [])
        if not isinstance(zones, list) or not zones or any(not isinstance(zone, str) or not zone.strip() for zone in zones):
            raise ValueError(f"AHU {ahu_id} needs served zone IDs.")
        zones = list(dict.fromkeys(zone.strip() for zone in zones))
        if system_type == "single_zone_constant_volume" and len(zones) != 1:
            raise ValueError(f"AHU {ahu_id} constant-volume systems must serve exactly one zone.")
        unknown = set(zones) - known_zone_ids if known_zone_ids else set()
        if unknown:
            raise ValueError(f"AHU {ahu_id} references unknown zones: {', '.join(sorted(unknown))}.")
        duplicate = [zone for zone in zones if zone in assigned]
        if duplicate:
            raise ValueError(f"Zones may only belong to one AHU in V1: {', '.join(sorted(duplicate))}.")
        status = str(item.get("review_status", "missing"))
        if status not in STATUSES:
            raise ValueError(f"AHU {ahu_id} review status is invalid.")
        source = str(item.get("source", "") or "").strip()
        citations = validate_citations(item.get("citations", []), f"AHU {ahu_id}")
        if status in {"confirmed", "provisional"} and (not source or not citations):
            raise ValueError(f"AHU {ahu_id} requires source and citations when reviewed.")
        row = {
            "ahu_id": ahu_id, "name": str(item.get("name", ahu_id)).strip() or ahu_id,
            "system_type": system_type, "number_off": number_off,
            "served_zone_ids": zones, "review_status": status, "source": source,
            "citations": citations, "scope": deepcopy(item.get("scope") or {}),
            "notes": str(item.get("notes", "") or "").strip(),
        }
        systems.append(row)
        seen.add(ahu_id)
        assigned.update({zone: ahu_id for zone in zones})
    return {"schema_version": 1, "updated_at": str(raw.get("updated_at", "") or ""), "systems": systems}


def validate_air_side_model(raw, ahu_ids=None, zone_ids=None):
    if not isinstance(raw, dict):
        raise ValueError("Air-side model must be an object.")
    result = deepcopy(empty_air_side_model())
    result.update({key: deepcopy(raw.get(key, value)) for key, value in result.items()})
    if result["outside_air_ownership"] not in {"central_ahu", "room_level"}:
        raise ValueError("Air-side outside-air ownership must be central_ahu or room_level.")
    ahu_ids, zone_ids = set(ahu_ids or []), set(zone_ids or [])
    for key in ("airflow_records", "fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"):
        if not isinstance(result[key], list):
            raise ValueError(f"Air-side {key} must be a list.")
    seen_paths = set()
    paths = []
    for index, item in enumerate(result["airflow_records"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Airflow record {index} must be an object.")
        row = _common_component(item, f"airflow record {index}")
        row["ahu_id"] = _ref(item.get("ahu_id", ""), "AHU", ahu_ids, f"airflow record {index}")
        row["zone_id"] = str(item.get("zone_id", "") or "").strip()
        if row["zone_id"] and zone_ids and row["zone_id"] not in zone_ids:
            raise ValueError(f"Airflow record {index} references an unknown zone.")
        row["path_type"] = str(item.get("path_type", "")).strip()
        if row["path_type"] not in PATH_TYPES:
            raise ValueError(f"Airflow record {index} has an unsupported path type.")
        row["source_node"] = str(item.get("source_node", "")).strip()
        row["destination_node"] = str(item.get("destination_node", "")).strip()
        if row["source_node"] not in NODE_TYPES or row["destination_node"] not in NODE_TYPES:
            raise ValueError(f"Airflow record {index} needs supported source and destination nodes.")
        row["flow_lps"], row["airflow_basis"] = _airflow_lps(item, f"airflow record {index}", required=row["review_status"] == "confirmed")
        row["schedule"] = _profile(item.get("schedule", []), f"airflow record {index} schedule")
        row["state"] = deepcopy(item.get("state") or {})
        path_key = (row["ahu_id"], row["zone_id"], row["path_type"], row["source_node"], row["destination_node"])
        if path_key in seen_paths:
            raise ValueError(f"Duplicate air path for {path_key}.")
        seen_paths.add(path_key)
        paths.append(row)
    result["airflow_records"] = paths
    for key in ("fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"):
        result[key] = [_validate_component(row, key, ahu_ids, zone_ids, index) for index, row in enumerate(result[key], start=1)]
    result["updated_at"] = str(raw.get("updated_at", "") or "")
    return result


def calculate_ahu_report(room_report, systems_raw, model_raw, gate_raw, *, selected_ahu_ids=None, scenario_ids=None, snapshot_fingerprint=""):
    gate = validate_air_side_method_gate(gate_raw or empty_air_side_method_gate())
    systems = validate_ahu_systems(systems_raw or empty_ahu_systems())
    ahu_ids = {row["ahu_id"] for row in systems["systems"]}
    model = validate_air_side_model(model_raw or empty_air_side_model(), ahu_ids)
    selected = set(selected_ahu_ids or ahu_ids)
    unknown = selected - ahu_ids
    if unknown:
        raise ValueError("Unknown AHU IDs: " + ", ".join(sorted(unknown)))
    if model["outside_air_ownership"] != "central_ahu":
        return _blocked_report(systems, "V1 requires central AHU outside-air ownership.", snapshot_fingerprint, gate)
    report = {
        "schema_version": 1, "report_type": "hourly_ahu_load_report", "status": "blocked",
        "selected_ahu_ids": sorted(selected), "systems": [], "scenario_results": [],
        "included_scope_peak": {}, "project_peak": {}, "blocked_ahus": [], "warnings": [],
        "input_snapshot_fingerprint": snapshot_fingerprint, "method_gate_fingerprint": gate["fingerprint"],
        "outside_air_ownership": model["outside_air_ownership"], "validated": False,
    }
    report["systems"] = deepcopy(systems["systems"])
    report["source_citation_register"] = _source_register(systems, model, gate)
    if not room_report or not room_report.get("scenario_results"):
        report["blocked_ahus"] = [{"ahu_id": ahu, "reasons": ["Current hourly room-load report is unavailable."]} for ahu in sorted(selected)]
        return report
    room_lookup_by_scenario = {row.get("scenario_id"): row for row in room_report.get("scenario_results", [])}
    wanted_scenarios = scenario_ids or list(room_lookup_by_scenario)
    for scenario_id in wanted_scenarios:
        scenario = room_lookup_by_scenario.get(scenario_id)
        if not scenario:
            continue
        result = _calculate_scenario(scenario, [row for row in systems["systems"] if row["ahu_id"] in selected], model, gate)
        report["scenario_results"].append(result)
    if not report["scenario_results"]:
        report["blocked_ahus"] = [{"ahu_id": ahu, "reasons": ["No selected cooling scenario is available in the current room-load report."]} for ahu in sorted(selected)]
        return report
    usable = [row for row in report["scenario_results"] if row.get("status") != "blocked"]
    if not usable:
        report["blocked_ahus"] = [item for row in report["scenario_results"] for item in row.get("blocked_ahus", [])]
        return report
    report["status"] = "review_ready" if air_side_gate_is_approved(gate) and all(row["status"] == "review_ready" for row in usable) else "draft"
    report["included_scope_peak"] = _governing(usable, "included_scope_peak")
    if report["status"] == "review_ready" and len(usable) == len(report["scenario_results"]):
        report["project_peak"] = deepcopy(report["included_scope_peak"])
    elif report["included_scope_peak"]:
        report["warnings"].append("Project AHU peak is suppressed until every selected AHU and scenario is review-ready.")
    return report


def _calculate_scenario(scenario, systems, model, gate):
    result = {"scenario_id": scenario.get("scenario_id", ""), "status": "blocked", "ahus": [], "included_scope_hours": [], "included_scope_peak": {}, "blocked_ahus": []}
    room_rows = {row.get("room_id"): row for row in scenario.get("rooms", [])}
    zone_rows = {row.get("zone_id"): row for row in scenario.get("zones", [])}
    for system in systems:
        rooms = [row for row in scenario.get("rooms", []) if row.get("zone_id") in system["served_zone_ids"]]
        reasons = []
        if system["review_status"] != "confirmed":
            reasons.append("AHU system review is not confirmed.")
        if not rooms:
            reasons.append("AHU has no calculated served rooms in this scenario.")
        path_rows = [row for row in model["airflow_records"] if row["ahu_id"] == system["ahu_id"]]
        if not path_rows:
            reasons.append("AHU has no reviewed air paths.")
        if any(row.get("review_status") != "confirmed" for row in path_rows):
            reasons.append("Every active AHU air path must be confirmed and cited.")
        for kind in ("fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"):
            if any(row.get("ahu_id") == system["ahu_id"] and row.get("review_status") != "confirmed" for row in model.get(kind, [])):
                reasons.append(f"Every configured {kind.replace('_', ' ')} input must be confirmed and cited.")
        if system["system_type"] == "vav":
            missing_terminals = [zone_id for zone_id in system["served_zone_ids"] if not any(row.get("path_type") == "supply" and row.get("zone_id") == zone_id and row.get("flow_lps", 0) > 0 for row in path_rows)]
            if missing_terminals:
                reasons.append("VAV supply airflow is missing for zone(s): " + ", ".join(sorted(missing_terminals)) + ".")
        if reasons:
            result["blocked_ahus"].append({"ahu_id": system["ahu_id"], "reasons": reasons})
            continue
        hours = []
        for hour in range(24):
            room_load = _room_load_at_hour(rooms, hour)
            paths = path_rows
            flow_totals = _path_flows(path_rows, hour)
            flow_issues = _flow_issues(flow_totals)
            if flow_issues:
                result["blocked_ahus"].append({"ahu_id": system["ahu_id"], "hour": hour, "reasons": flow_issues})
                continue
            try:
                airside = _airside_hour(system, model, scenario, paths, room_load, hour)
            except ValueError as error:
                result["blocked_ahus"].append({"ahu_id": system["ahu_id"], "hour": hour, "reasons": [str(error)]})
                continue
            airside["room_load"] = room_load
            airside["hour"] = hour
            hours.append(airside)
        if len(hours) != 24:
            continue
        peak = _peak(hours)
        result["ahus"].append({"ahu_id": system["ahu_id"], "name": system["name"], "system_type": system["system_type"], "number_off": system["number_off"], "served_zone_ids": system["served_zone_ids"], "room_ids": [row.get("room_id") for row in rooms], "hours": hours, "peak": peak, "status": "review_ready" if air_side_gate_is_approved(gate) else "draft"})
    complete = len(result["ahus"]) == len(systems) and not result["blocked_ahus"]
    result["status"] = "review_ready" if complete and all(row["status"] == "review_ready" for row in result["ahus"]) else "draft" if result["ahus"] else "blocked"
    if result["ahus"]:
        result["included_scope_hours"] = [_sum_hours([row["hours"][hour] for row in result["ahus"]], hour) for hour in range(24)]
        result["included_scope_peak"] = _peak(result["included_scope_hours"])
    return result


def _airside_hour(system, model, scenario, paths, room_load, hour):
    weather = next((row for row in scenario.get("hours", []) if row.get("hour") == hour), None)
    if not weather:
        raise ValueError("Scenario weather hour is missing.")
    outdoor = _state(weather["outdoor_dry_bulb_c"], weather["outdoor_wet_bulb_c"], scenario.get("atmospheric_pressure_kpa"), "outdoor")
    flows = {kind: sum(row["flow_lps"] * _schedule_factor(row.get("schedule", []), hour) for row in paths if row["path_type"] == kind) for kind in PATH_TYPES}
    flows["leakage"] += sum(row["airflow_lps"] * _schedule_factor(row.get("schedule", []), hour) for row in model["leakage"] if row.get("ahu_id") == system["ahu_id"] and row.get("review_status") == "confirmed")
    return_flow = flows["return"]
    if return_flow <= 0:
        raise ValueError("A positive return-air flow is required for mixed-air calculation.")
    fixed = next((row for row in model["airflow_records"] if row["ahu_id"] == system["ahu_id"] and row["path_type"] == "return" and row.get("state")), None)
    if not fixed:
        raise ValueError("Reviewed return-air state is required.")
    return_state = _state(fixed["state"].get("dry_bulb_c"), fixed["state"].get("wet_bulb_c"), scenario.get("atmospheric_pressure_kpa"), "return")
    mixed = _mix_states([(outdoor, flows["outside_air"] + flows["leakage"]), (return_state, return_flow)], scenario.get("atmospheric_pressure_kpa"))
    recovery = _component_for(model["heat_recovery"], system["ahu_id"])
    if recovery:
        exhaust_record = next((row for row in model["airflow_records"] if row["ahu_id"] == system["ahu_id"] and row["path_type"] == "exhaust" and row.get("state")), None)
        exhaust_data = recovery.get("exhaust_state") or (exhaust_record or {}).get("state")
        if not exhaust_data:
            raise ValueError("Heat recovery requires an explicit return/exhaust state.")
        exhaust_state = _state(exhaust_data.get("dry_bulb_c"), exhaust_data.get("wet_bulb_c"), scenario.get("atmospheric_pressure_kpa"), "heat-recovery exhaust")
        mixed = _effectiveness_state(mixed, exhaust_state, recovery, scenario.get("atmospheric_pressure_kpa"), "heat recovery")
    precondition = _component_for(model["preconditioning"], system["ahu_id"])
    if precondition:
        mixed = _effectiveness_state(mixed, _state(precondition.get("reference_db_c", return_state["dry_bulb_c"]), precondition.get("reference_wb_c", return_state["wet_bulb_c"]), scenario.get("atmospheric_pressure_kpa"), "preconditioning reference"), precondition, scenario.get("atmospheric_pressure_kpa"), "preconditioning")
    coil = _component_for(model["coils"], system["ahu_id"])
    if not coil:
        raise ValueError("Reviewed coil leaving state is required.")
    leaving = _state(coil.get("leaving_db_c"), coil.get("leaving_wb_c"), scenario.get("atmospheric_pressure_kpa"), "coil leaving")
    total_flow = flows["supply"]
    if total_flow <= 0:
        raise ValueError("Positive supply airflow is required.")
    volume = specific_volume_m3_kg(mixed["dry_bulb_c"], mixed["humidity_ratio"], scenario.get("atmospheric_pressure_kpa"))
    mass_flow = total_flow / 1000 / volume
    total = mass_flow * (mixed["enthalpy_kj_kg"] - leaving["enthalpy_kj_kg"])
    sensible = mass_flow * 1.006 * (mixed["dry_bulb_c"] - leaving["dry_bulb_c"])
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Coil leaving state must produce a positive cooling enthalpy reduction.")
    fan = _sum_component(model["fans"], system["ahu_id"], "heat_kw")
    duct = _sum_component(model["duct_effects"], system["ahu_id"], "sensible_kw")
    leakage = flows["leakage"]
    return {
        "flows_lps": flows, "flow_balance_lps": round(flows["outside_air"] + flows["return"] + flows["make_up"] - flows["supply"] - flows["exhaust"] - flows["relief"], 6),
        "room_load_excluding_central_outside_air": room_load["room_load_excluding_outside_air"],
        "outside_air_conditioning": round(max(0.0, total * (flows["outside_air"] / total_flow)), 6),
        "fan_heat_kw": round(fan, 6), "duct_effect_kw": round(duct, 6), "duct_leakage_lps": round(leakage, 6),
        "mixed_air_state": mixed, "coil_leaving_state": leaving,
        "coil_sensible_kw": round(sensible, 6), "coil_latent_kw": round(total - sensible, 6), "coil_total_kw": round(total, 6),
        "heat_recovery": deepcopy(recovery or {}), "preconditioning": deepcopy(precondition or {}),
        "design_total_kw": round((total + fan + duct) * system["number_off"], 6),
    }


def _room_load_at_hour(rooms, hour):
    subtotal = 0.0
    sensible = 0.0
    latent = 0.0
    outside = 0.0
    components = {}
    for room in rooms:
        row = next((item for item in room.get("hours", []) if item.get("hour") == hour), None)
        if not row:
            continue
        sensible += row.get("subtotal_sensible_kw", 0.0)
        latent += row.get("subtotal_latent_kw", 0.0)
        subtotal += row.get("design_total_kw", row.get("subtotal_kw", 0.0))
        for name, component in row.get("components", {}).items():
            current = components.setdefault(name, {"sensible_kw": 0.0, "latent_kw": 0.0, "total_kw": 0.0})
            for key in ("sensible_kw", "latent_kw", "total_kw"):
                current[key] = round(current[key] + component.get(key, 0.0), 6)
        outside += row.get("components", {}).get("outside_air", {}).get("total_kw", 0.0)
    return {"sensible_kw": round(sensible, 6), "latent_kw": round(latent, 6), "design_total_kw": round(subtotal, 6), "components": components, "outside_air_kw": round(outside, 6), "room_load_excluding_outside_air": {"sensible_kw": round(sensible - components.get("outside_air", {}).get("sensible_kw", 0.0), 6), "latent_kw": round(latent - components.get("outside_air", {}).get("latent_kw", 0.0), 6), "design_total_kw": round(subtotal - outside, 6)}}


def _state(db, wb, pressure, label):
    if isinstance(db, dict): db = db.get("value")
    if isinstance(wb, dict): wb = wb.get("value")
    if isinstance(pressure, dict): pressure = pressure.get("value")
    if db is None or wb is None or pressure is None:
        raise ValueError(f"Complete {label} dry-bulb, wet-bulb, and pressure are required.")
    ratio = humidity_ratio_from_db_wb(float(db), float(wb), float(pressure))
    return {"dry_bulb_c": round(float(db), 6), "wet_bulb_c": round(float(wb), 6), "humidity_ratio": round(ratio, 9), "enthalpy_kj_kg": round(moist_air_enthalpy_kj_kg(float(db), ratio), 6), "pressure_kpa": float(pressure)}


def _mix_states(rows, pressure):
    total_flow = sum(flow for _state_row, flow in rows)
    if total_flow <= 0:
        raise ValueError("Mixed-air calculation requires positive total airflow.")
    humidity = sum(state["humidity_ratio"] * flow for state, flow in rows) / total_flow
    enthalpy = sum(state["enthalpy_kj_kg"] * flow for state, flow in rows) / total_flow
    db = (enthalpy - 2501 * humidity) / (1.006 + 1.86 * humidity)
    return {"dry_bulb_c": round(db, 6), "wet_bulb_c": None, "humidity_ratio": round(humidity, 9), "enthalpy_kj_kg": round(enthalpy, 6), "pressure_kpa": pressure.get("value") if isinstance(pressure, dict) else pressure}


def _effectiveness_state(current, reference, component, pressure, label):
    sensible = _factor(component.get("sensible_effectiveness"), f"{label} sensible effectiveness")
    latent = _factor(component.get("latent_effectiveness"), f"{label} latent effectiveness")
    humidity = current["humidity_ratio"] + latent * (reference["humidity_ratio"] - current["humidity_ratio"])
    enthalpy = current["enthalpy_kj_kg"] + sensible * (reference["enthalpy_kj_kg"] - current["enthalpy_kj_kg"])
    db = (enthalpy - 2501 * humidity) / (1.006 + 1.86 * humidity)
    return {**current, "dry_bulb_c": round(db, 6), "humidity_ratio": round(humidity, 9), "enthalpy_kj_kg": round(enthalpy, 6), "pressure_kpa": pressure.get("value") if isinstance(pressure, dict) else pressure}


def _path_flows(rows, hour):
    return {kind: round(sum(row["flow_lps"] * _schedule_factor(row.get("schedule", []), hour) for row in rows if row["path_type"] == kind), 6) for kind in PATH_TYPES}


def _flow_issues(flows):
    if flows["supply"] <= 0: return ["Positive supply airflow is required."]
    if flows["outside_air"] < 0 or flows["return"] < 0: return ["Airflow cannot be negative."]
    if abs(flows["outside_air"] + flows["return"] + flows["make_up"] - flows["supply"] - flows["exhaust"] - flows["relief"]) > 1e-3:
        return ["Outside, return, supply, exhaust, relief, and make-up air paths do not balance."]
    return []


def _component_for(rows, ahu_id):
    return next((row for row in rows if row.get("ahu_id") == ahu_id and row.get("review_status") == "confirmed"), None)


def _sum_component(rows, ahu_id, key):
    return sum(float(row.get(key, 0) or 0) for row in rows if row.get("ahu_id") == ahu_id and row.get("review_status") == "confirmed")


def _sum_hours(rows, hour):
    components = {}
    for row in rows:
        for name, value in row.get("components", {}).items():
            current = components.setdefault(name, {"sensible_kw": 0.0, "latent_kw": 0.0, "total_kw": 0.0})
            for key in current:
                current[key] = round(current[key] + value.get(key, 0.0), 6)
    total = round(sum(row.get("design_total_kw", 0.0) for row in rows), 6)
    return {"hour": hour, "components": components, "design_total_kw": total, "status": "calculated"}


def _peak(rows):
    if not rows: return {}
    maximum = max(row.get("design_total_kw", 0.0) for row in rows)
    tied = [row.get("hour") for row in rows if row.get("design_total_kw", 0.0) == maximum]
    return {"design_total_kw": maximum, "tied_hours": tied, "display_hour": min(tied), "hour": min(tied)}


def _governing(scenario_results, key):
    candidates = [row.get(key) or {} for row in scenario_results if row.get(key)]
    if not candidates:
        return {}
    return max(candidates, key=lambda row: row.get("design_total_kw", 0.0))


def _blocked_report(systems, reason, snapshot, gate):
    return {"schema_version": 1, "report_type": "hourly_ahu_load_report", "status": "blocked", "systems": systems.get("systems", []), "scenario_results": [], "included_scope_peak": {}, "project_peak": {}, "blocked_ahus": [{"ahu_id": row.get("ahu_id", ""), "reasons": [reason]} for row in systems.get("systems", [])], "warnings": [], "input_snapshot_fingerprint": snapshot, "method_gate_fingerprint": gate.get("fingerprint", ""), "source_citation_register": _source_register(systems, empty_air_side_model(), gate), "validated": False}


def _source_register(systems, model, gate):
    records = []
    for item in systems.get("systems", []):
        records.append({"kind": "ahu_system", "id": item.get("ahu_id", ""), "source": item.get("source", ""), "citations": deepcopy(item.get("citations", []))})
    for kind in ("airflow_records", "fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"):
        for index, item in enumerate(model.get(kind, []), start=1):
            records.append({"kind": kind, "id": item.get("record_id", f"{kind}_{index}"), "ahu_id": item.get("ahu_id", ""), "source": item.get("source", ""), "citations": deepcopy(item.get("citations", []))})
    records.append({"kind": "air_side_method_gate", "id": gate.get("method_id", ""), "source": gate.get("method_citation", ""), "citations": deepcopy(gate.get("citations", []))})
    return records


def _common_component(item, label):
    status = str(item.get("review_status", "missing"))
    if status not in STATUSES: raise ValueError(f"{label} review status is invalid.")
    source = str(item.get("source", "") or "").strip()
    citations = validate_citations(item.get("citations", []), label)
    if status in {"confirmed", "provisional"} and (not source or not citations): raise ValueError(f"{label} requires source and citations when reviewed.")
    return {"review_status": status, "source": source, "citations": citations}


def _validate_component(item, kind, ahu_ids, zone_ids, index):
    if not isinstance(item, dict): raise ValueError(f"{kind} component {index} must be an object.")
    row = _common_component(item, f"{kind} component {index}")
    row.update({key: deepcopy(value) for key, value in item.items() if key not in row})
    row["ahu_id"] = _ref(item.get("ahu_id", ""), "AHU", ahu_ids, f"{kind} component {index}")
    if kind == "fans" and item.get("location", "") not in {"supply", "return", "mixed_air"}:
        raise ValueError("Fan location must be supply, return, or mixed_air.")
    if kind == "fans": row["heat_kw"] = _non_negative(item.get("heat_kw"), f"fan component {index} heat", required=True)
    if kind == "duct_effects":
        row["sensible_kw"] = _number(item.get("sensible_kw", 0), f"duct component {index} sensible effect")
    if kind == "leakage":
        row["airflow_lps"], row["airflow_basis"] = _airflow_lps(item, f"leakage component {index}", required=True)
        if not item.get("source_node") or not item.get("destination_node"): raise ValueError("Leakage requires source and destination nodes.")
        row["schedule"] = _profile(item.get("schedule", []), f"leakage component {index} schedule")
        row["state"] = deepcopy(item.get("state") or {})
    if kind in {"heat_recovery", "preconditioning"}:
        row["sensible_effectiveness"] = _factor(item.get("sensible_effectiveness"), f"{kind} sensible effectiveness")
        row["latent_effectiveness"] = _factor(item.get("latent_effectiveness"), f"{kind} latent effectiveness")
        if kind == "preconditioning":
            row["reference_db_c"] = _number(item.get("reference_db_c"), "preconditioning reference dry-bulb")
            row["reference_wb_c"] = _number(item.get("reference_wb_c"), "preconditioning reference wet-bulb")
    if kind == "coils":
        row["leaving_db_c"] = _number(item.get("leaving_db_c"), "coil leaving dry-bulb")
        row["leaving_wb_c"] = _number(item.get("leaving_wb_c"), "coil leaving wet-bulb")
    return row


def _ref(value, label, allowed, context):
    value = str(value or "").strip()
    if not value: raise ValueError(f"{context} needs a {label} ID.")
    if allowed and value not in allowed: raise ValueError(f"{context} references unknown {label} '{value}'.")
    return value


def _id(value, label):
    value = str(value or "").strip()
    if not value or not all(char.isalnum() or char in "_-" for char in value) or not value[0].islower(): raise ValueError(f"{label} must be a stable lowercase ID.")
    return value


def _number(value, label):
    if value is None or isinstance(value, bool): raise ValueError(f"{label} is required.")
    try: result = float(value)
    except (TypeError, ValueError) as error: raise ValueError(f"{label} must be numeric.") from error
    if not math.isfinite(result): raise ValueError(f"{label} must be finite.")
    return result


def _non_negative(value, label, required=False):
    if value is None and not required: return 0.0
    result = _number(value, label)
    if result < 0: raise ValueError(f"{label} cannot be negative.")
    return result


def _airflow_lps(item, label, required=False):
    """Normalize an explicit airflow to L/s while retaining the input basis."""
    if item.get("flow_lps") is not None or item.get("airflow_lps") is not None:
        key = "flow_lps" if item.get("flow_lps") is not None else "airflow_lps"
        return _non_negative(item.get(key), f"{label} airflow", required=required), {"value": item.get(key), "unit": "L/s", "conversion": "identity"}
    value = item.get("airflow_value", item.get("value", item.get("flow")))
    if value is None:
        if required:
            raise ValueError(f"{label} airflow is required.")
        return 0.0, {"value": 0.0, "unit": "L/s", "conversion": "missing"}
    unit = str(item.get("airflow_unit", item.get("unit", "L/s")) or "L/s").strip().lower().replace("³", "3").replace(" ", "")
    factors = {"l/s": 1.0, "ls": 1.0, "m3/s": 1000.0, "m3s": 1000.0, "m3/h": 1000.0 / 3600.0, "m3h": 1000.0 / 3600.0}
    if unit not in factors:
        raise ValueError(f"{label} airflow unit must be L/s, m³/s, or m³/h.")
    numeric = _non_negative(value, f"{label} airflow", required=True)
    return numeric * factors[unit], {"value": numeric, "unit": item.get("airflow_unit", item.get("unit", "L/s")), "conversion": f"multiply by {factors[unit]} to L/s"}


def _positive_int(value, label):
    result = _number(value, label)
    if result <= 0 or not result.is_integer(): raise ValueError(f"{label} must be a positive integer.")
    return int(result)


def _factor(value, label):
    result = _number(value, label)
    if result < 0 or result > 1: raise ValueError(f"{label} must be between 0 and 1.")
    return result


def _profile(value, label):
    if value in (None, []): return []
    if not isinstance(value, list) or len(value) != 24 or any(not isinstance(item, (int, float)) or item < 0 or item > 1 for item in value): raise ValueError(f"{label} must contain 24 values between 0 and 1.")
    return [float(item) for item in value]


def _schedule_factor(values, hour):
    return values[hour] if values else 1.0
