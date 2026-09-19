"""Gated cooling plant and hydraulic aggregation.

This module consumes the reviewed hourly AHU report.  It deliberately stops at
plant duty and reconciliation: it does not select equipment, infer ownership,
or model annual hydraulics.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

from ai.site_design_conditions import validate_citations


PLANT_TYPES = {"chiller", "boiler", "package_unit"}
CIRCUIT_TYPES = {"chilled_water", "heating_water", "refrigerant", "hydraulic"}
DUTY_BASES = {"combined_equipment", "representative_per_unit"}
STATUSES = {"missing", "provisional", "confirmed"}
METHOD_ID = "cooling_plant_hydraulics_v1"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_plant_systems():
    return {"schema_version": 1, "updated_at": "", "systems": []}


def empty_hydraulic_circuits():
    return {"schema_version": 1, "updated_at": "", "circuits": []}


def empty_plant_method_gate():
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
        "scope": "Cooling plant coincidence, explicit chilled-water circuits, pump power, pipe effects, and plant diversity.",
        "citations": [],
    }


def validate_plant_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Plant method gate must be an object.")
    result = deepcopy(empty_plant_method_gate())
    result.update({key: raw.get(key, value) for key, value in result.items()})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Plant method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in {"placeholder", "approved"}:
        raise ValueError("Plant method gate approval status must be placeholder or approved.")
    for key in ("method_version", "engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[key] = str(result.get(key, "") or "").strip()
    result["citations"] = validate_citations(result.get("citations", []), "Plant method gate")
    if result["approval_status"] == "approved":
        required = ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope")
        if any(not result[key] for key in required) or not result["citations"]:
            raise ValueError("Approved plant method gate requires engineer metadata, scope, and citations.")
    result["fingerprint"] = fingerprint(result)
    return result


def plant_gate_is_approved(gate):
    return bool(gate and gate.get("method_id") == METHOD_ID and gate.get("approval_status") == "approved")


def validate_plant_systems(raw, known_ahu_ids=None, known_circuit_ids=None):
    if not isinstance(raw, dict) or not isinstance(raw.get("systems", []), list):
        raise ValueError("Plant systems must contain a systems list.")
    known_ahu_ids, known_circuit_ids = set(known_ahu_ids or []), set(known_circuit_ids or [])
    systems, seen, assigned_ahus = [], set(), {}
    for index, item in enumerate(raw["systems"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Plant system {index} must be an object.")
        plant_id = _id(item.get("plant_id", ""), f"Plant system {index} ID")
        if plant_id in seen:
            raise ValueError(f"Plant system ID '{plant_id}' is duplicated.")
        plant_type = str(item.get("plant_type", "")).strip()
        if plant_type not in PLANT_TYPES:
            raise ValueError(f"Plant {plant_id} type must be chiller, boiler, or package_unit.")
        number_off = _positive_int(item.get("number_off"), f"Plant {plant_id} number-off")
        duty_basis = str(item.get("duty_basis", "")).strip()
        if duty_basis not in DUTY_BASES:
            raise ValueError(f"Plant {plant_id} needs duty_basis combined_equipment or representative_per_unit.")
        circuit_ids = _string_list(item.get("circuit_ids", []), f"Plant {plant_id} circuit IDs")
        if known_circuit_ids and set(circuit_ids) - known_circuit_ids:
            raise ValueError(f"Plant {plant_id} references an unknown circuit.")
        ahu_ids = _string_list(item.get("served_ahu_ids", []), f"Plant {plant_id} AHU IDs")
        if known_ahu_ids and set(ahu_ids) - known_ahu_ids:
            raise ValueError(f"Plant {plant_id} references an unknown AHU.")
        duplicate = [ahu_id for ahu_id in ahu_ids if ahu_id in assigned_ahus]
        if duplicate:
            raise ValueError("An AHU may not belong to multiple plant systems in V1: " + ", ".join(sorted(set(duplicate))))
        status, source, citations = _review(item, f"Plant {plant_id}")
        diversity = item.get("diversity_factor")
        if plant_type == "chiller":
            diversity = _factor(diversity, f"Plant {plant_id} diversity factor", required=True)
        elif diversity is not None:
            diversity = _factor(diversity, f"Plant {plant_id} diversity factor", required=True)
        rated = item.get("rated_capacity_kw")
        if rated is not None:
            rated = _non_negative(rated, f"Plant {plant_id} rated capacity")
        row = {
            "plant_id": plant_id, "name": str(item.get("name", plant_id)).strip() or plant_id,
            "plant_type": plant_type, "number_off": number_off, "duty_basis": duty_basis,
            "circuit_ids": circuit_ids, "served_ahu_ids": ahu_ids, "diversity_factor": diversity,
            "rated_capacity_kw": rated, "review_status": status, "source": source,
            "citations": citations, "notes": str(item.get("notes", "") or "").strip(),
        }
        systems.append(row)
        seen.add(plant_id)
        assigned_ahus.update({ahu_id: plant_id for ahu_id in ahu_ids})
    return {"schema_version": 1, "updated_at": str(raw.get("updated_at", "") or ""), "systems": systems}


def validate_hydraulic_circuits(raw, plant_ids=None, known_ahu_ids=None):
    if not isinstance(raw, dict) or not isinstance(raw.get("circuits", []), list):
        raise ValueError("Hydraulic circuits must contain a circuits list.")
    plant_ids, known_ahu_ids = set(plant_ids or []), set(known_ahu_ids or [])
    circuits, seen, assigned_ahus = [], set(), {}
    for index, item in enumerate(raw["circuits"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Circuit {index} must be an object.")
        circuit_id = _id(item.get("circuit_id", ""), f"Circuit {index} ID")
        if circuit_id in seen:
            raise ValueError(f"Circuit ID '{circuit_id}' is duplicated.")
        plant_id = _ref(item.get("plant_id", ""), "plant", plant_ids, f"Circuit {circuit_id}")
        circuit_type = str(item.get("circuit_type", "")).strip()
        if circuit_type not in CIRCUIT_TYPES:
            raise ValueError(f"Circuit {circuit_id} has an unsupported circuit type.")
        ahu_ids = _string_list(item.get("served_ahu_ids", []), f"Circuit {circuit_id} AHU IDs")
        if known_ahu_ids and set(ahu_ids) - known_ahu_ids:
            raise ValueError(f"Circuit {circuit_id} references an unknown AHU.")
        duplicate = [ahu_id for ahu_id in ahu_ids if ahu_id in assigned_ahus]
        if duplicate:
            raise ValueError("An AHU may not belong to multiple circuits in V1: " + ", ".join(sorted(set(duplicate))))
        status, source, citations = _review(item, f"Circuit {circuit_id}")
        flow_lps, flow_basis = _airflow(item, f"Circuit {circuit_id} flow")
        supply_temp = _optional_number(item.get("supply_temperature_c"), f"Circuit {circuit_id} supply temperature")
        return_temp = _optional_number(item.get("return_temperature_c"), f"Circuit {circuit_id} return temperature")
        pumps = [_validate_pump(pump, circuit_id, pump_index) for pump_index, pump in enumerate(item.get("pumps", []), start=1)]
        pipes = [_validate_pipe(pipe, circuit_id, pipe_index) for pipe_index, pipe in enumerate(item.get("pipe_effects", []), start=1)]
        row = {
            "circuit_id": circuit_id, "name": str(item.get("name", circuit_id)).strip() or circuit_id,
            "plant_id": plant_id, "circuit_type": circuit_type, "served_ahu_ids": ahu_ids,
            "flow_lps": flow_lps, "flow_basis": flow_basis, "supply_temperature_c": supply_temp,
            "return_temperature_c": return_temp, "pumps": pumps, "pipe_effects": pipes,
            "review_status": status, "source": source, "citations": citations,
        }
        circuits.append(row)
        seen.add(circuit_id)
        assigned_ahus.update({ahu_id: circuit_id for ahu_id in ahu_ids})
    return {"schema_version": 1, "updated_at": str(raw.get("updated_at", "") or ""), "circuits": circuits}


def calculate_plant_report(ahu_report, plants_raw, circuits_raw, gate_raw, *, selected_plant_ids=None, scenario_ids=None, snapshot_fingerprint=""):
    gate = validate_plant_method_gate(gate_raw or empty_plant_method_gate())
    raw_circuits = circuits_raw or empty_hydraulic_circuits()
    circuit_ids = {str(row.get("circuit_id", "")) for row in raw_circuits.get("circuits", []) if isinstance(row, dict)}
    plants = validate_plant_systems(plants_raw or empty_plant_systems(), known_circuit_ids=circuit_ids)
    circuits = validate_hydraulic_circuits(raw_circuits, {row["plant_id"] for row in plants["systems"]})
    circuit_lookup = {row["circuit_id"]: row for row in circuits["circuits"]}
    plant_lookup = {row["plant_id"]: row for row in plants["systems"]}
    selected = set(selected_plant_ids or plant_lookup)
    unknown = selected - set(plant_lookup)
    if unknown:
        raise ValueError("Unknown plant IDs: " + ", ".join(sorted(unknown)))
    report = {
        "schema_version": 1, "report_type": "hourly_plant_load_report", "status": "blocked",
        "selected_plant_ids": sorted(selected), "plants": deepcopy(plants["systems"]),
        "circuits": deepcopy(circuits["circuits"]), "scenario_results": [],
        "included_scope_peak": {}, "project_peak": {}, "blocked_plants": [],
        "package_unit_exclusions": [row for row in plants["systems"] if row["plant_type"] == "package_unit"],
        "warnings": [], "input_snapshot_fingerprint": snapshot_fingerprint,
        "method_gate_fingerprint": gate["fingerprint"], "validated": False,
        "source_citation_register": _source_register(plants, circuits, gate),
    }
    if not ahu_report or not ahu_report.get("scenario_results"):
        report["blocked_plants"] = [{"plant_id": plant_id, "reasons": ["Current hourly AHU report is unavailable."]} for plant_id in sorted(selected)]
        return report
    ahu_scenarios = {row.get("scenario_id"): row for row in ahu_report.get("scenario_results", [])}
    wanted = scenario_ids or list(ahu_scenarios)
    for scenario_id in wanted:
        scenario = ahu_scenarios.get(scenario_id)
        if scenario:
            report["scenario_results"].append(_calculate_scenario(scenario, [plant_lookup[plant_id] for plant_id in selected], circuit_lookup, gate, ahu_report))
    if not report["scenario_results"]:
        report["blocked_plants"] = [{"plant_id": plant_id, "reasons": ["No selected cooling scenario is available in the AHU report."]} for plant_id in sorted(selected)]
        return report
    usable = [row for row in report["scenario_results"] if row.get("status") != "blocked"]
    if not usable:
        report["blocked_plants"] = [item for row in report["scenario_results"] for item in row.get("blocked_plants", [])]
        return report
    report["status"] = "review_ready" if plant_gate_is_approved(gate) and all(row.get("status") == "review_ready" for row in usable) else "draft"
    report["included_scope_peak"] = _governing(usable, "included_scope_peak")
    required_ahus = set(ahu_report.get("selected_ahu_ids", []))
    mapped_ahus = set().union(*(set(plant_lookup[plant_id].get("served_ahu_ids", [])) for plant_id in selected)) if selected else set()
    if required_ahus - mapped_ahus:
        report["warnings"].append("Complete project plant peak is suppressed because some selected AHUs are not mapped to a central plant.")
    elif report["status"] == "review_ready" and len(usable) == len(report["scenario_results"]):
        report["project_peak"] = deepcopy(report["included_scope_peak"])
    elif report["included_scope_peak"]:
        report["warnings"].append("Project plant peak is suppressed until every selected plant and AHU is review-ready.")
    return report


def _calculate_scenario(ahu_scenario, plants, circuits, gate, ahu_report):
    result = {"scenario_id": ahu_scenario.get("scenario_id", ""), "status": "blocked", "plants": [], "included_scope_hours": [], "included_scope_peak": {}, "blocked_plants": []}
    ahu_lookup = {row.get("ahu_id"): row for row in ahu_scenario.get("ahus", [])}
    for plant in plants:
        if plant["plant_type"] != "chiller":
            result["plants"].append({"plant_id": plant["plant_id"], "status": "excluded", "reason": f"{plant['plant_type']} plant calculation is deferred from cooling V1.", "hours": [], "peak": {}})
            continue
        reasons = []
        if plant["review_status"] != "confirmed":
            reasons.append("Plant system review is not confirmed.")
        if not plant["served_ahu_ids"]:
            reasons.append("Chiller has no explicitly mapped AHUs.")
        if not plant["circuit_ids"]:
            reasons.append("Chiller has no explicitly mapped circuit.")
        plant_circuits = [circuits.get(circuit_id) for circuit_id in plant["circuit_ids"]]
        if any(circuit is None for circuit in plant_circuits):
            reasons.append("Chiller references a missing circuit.")
        plant_circuits = [circuit for circuit in plant_circuits if circuit]
        if any(circuit["plant_id"] != plant["plant_id"] for circuit in plant_circuits):
            reasons.append("Plant and circuit ownership do not match.")
        active_circuits = [circuit for circuit in plant_circuits if circuit["circuit_type"] == "chilled_water"]
        if not active_circuits:
            reasons.append("Cooling plant requires at least one reviewed chilled-water circuit.")
        if any(circuit["review_status"] != "confirmed" for circuit in active_circuits):
            reasons.append("Every active chilled-water circuit must be confirmed and cited.")
        circuit_ahus = set().union(*(set(circuit.get("served_ahu_ids", [])) for circuit in active_circuits)) if active_circuits else set()
        if circuit_ahus != set(plant["served_ahu_ids"]):
            reasons.append("Chiller AHU ownership must match its chilled-water circuit ownership.")
        mapped = [ahu_lookup.get(ahu_id) for ahu_id in plant["served_ahu_ids"]]
        if any(row is None for row in mapped):
            reasons.append("One or more mapped AHUs are missing from the current AHU scenario.")
        if reasons:
            result["blocked_plants"].append({"plant_id": plant["plant_id"], "reasons": reasons})
            continue
        hours = []
        for hour in range(24):
            mapped_rows = [row for row in mapped if row]
            coincident = sum((row.get("hours", [])[hour].get("coil_total_kw", 0.0) if len(row.get("hours", [])) > hour else 0.0) for row in mapped_rows)
            if any(len(row.get("hours", [])) != 24 for row in mapped_rows):
                result["blocked_plants"].append({"plant_id": plant["plant_id"], "hour": hour, "reasons": ["Mapped AHU hourly results are incomplete."]})
                continue
            diversity = plant["diversity_factor"]
            diversified = coincident * diversity
            pump_kw = sum(_scheduled_value(pump.get("power_kw", 0.0), pump.get("schedule", []), hour) * pump.get("number_off", 1) for circuit in active_circuits for pump in circuit["pumps"] if pump["review_status"] == "confirmed")
            pipe_kw = sum(_scheduled_value(pipe.get("effect_kw", 0.0), pipe.get("schedule", []), hour) for circuit in active_circuits for pipe in circuit["pipe_effects"] if pipe["review_status"] == "confirmed")
            multiplier = plant["number_off"] if plant["duty_basis"] == "representative_per_unit" else 1
            total = diversified * multiplier + pump_kw + pipe_kw
            hours.append({
                "hour": hour, "coincident_ahu_duty_kw": round(coincident, 6),
                "diversity_factor": diversity, "diversified_ahu_duty_kw": round(diversified, 6),
                "equipment_number_off": plant["number_off"], "duty_basis": plant["duty_basis"],
                "pump_power_kw": round(pump_kw, 6), "pipe_effect_kw": round(pipe_kw, 6),
                "plant_duty_kw": round(total, 6),
                "mapped_ahu_ids": list(plant["served_ahu_ids"]), "mapped_circuit_ids": list(plant["circuit_ids"]),
                "reconciliation_difference_kw": round(coincident - sum(row.get("hours", [])[hour].get("coil_total_kw", 0.0) for row in mapped_rows), 6),
                "status": "calculated",
            })
        if len(hours) != 24:
            continue
        peak = _peak(hours, "plant_duty_kw")
        result["plants"].append({"plant_id": plant["plant_id"], "name": plant["name"], "plant_type": plant["plant_type"], "number_off": plant["number_off"], "duty_basis": plant["duty_basis"], "served_ahu_ids": plant["served_ahu_ids"], "circuit_ids": plant["circuit_ids"], "hours": hours, "peak": peak, "status": "review_ready" if plant_gate_is_approved(gate) else "draft"})
    active = [row for row in result["plants"] if row.get("status") in {"draft", "review_ready"}]
    result["status"] = "review_ready" if active and len(active) == sum(plant["plant_type"] == "chiller" for plant in plants) and not result["blocked_plants"] and all(row["status"] == "review_ready" for row in active) else "draft" if active else "blocked"
    if active:
        result["included_scope_hours"] = [_sum_hours([row["hours"][hour] for row in active], hour) for hour in range(24)]
        result["included_scope_peak"] = _peak(result["included_scope_hours"], "plant_duty_kw")
    return result


def _review(item, label):
    status = str(item.get("review_status", "missing"))
    if status not in STATUSES:
        raise ValueError(f"{label} review status is invalid.")
    source = str(item.get("source", "") or "").strip()
    citations = validate_citations(item.get("citations", []), label)
    if status in {"confirmed", "provisional"} and (not source or not citations):
        raise ValueError(f"{label} requires source and citations when reviewed.")
    return status, source, citations


def _validate_pump(item, circuit_id, index):
    status, source, citations = _review(item, f"Pump {circuit_id}/{index}")
    pump_id = _id(item.get("pump_id", ""), f"Pump {circuit_id}/{index} ID")
    number_off = _positive_int(item.get("number_off"), f"Pump {pump_id} number-off")
    power = _non_negative(item.get("power_kw"), f"Pump {pump_id} power", required=status == "confirmed")
    return {"pump_id": pump_id, "number_off": number_off, "power_kw": power, "flow_lps": _optional_number(item.get("flow_lps"), f"Pump {pump_id} flow"), "schedule": _profile(item.get("schedule", []), f"Pump {pump_id} schedule"), "review_status": status, "source": source, "citations": citations}


def _validate_pipe(item, circuit_id, index):
    status, source, citations = _review(item, f"Pipe effect {circuit_id}/{index}")
    pipe_id = _id(item.get("pipe_id", ""), f"Pipe effect {circuit_id}/{index} ID")
    effect = _number(item.get("effect_kw"), f"Pipe effect {pipe_id}") if status == "confirmed" else _optional_number(item.get("effect_kw"), f"Pipe effect {pipe_id}") or 0.0
    return {"pipe_id": pipe_id, "effect_kw": effect, "schedule": _profile(item.get("schedule", []), f"Pipe effect {pipe_id} schedule"), "review_status": status, "source": source, "citations": citations}


def _airflow(item, label):
    value = item.get("flow_lps", item.get("flow_value", item.get("value")))
    if value is None:
        return None, {"value": None, "unit": "L/s", "conversion": "missing"}
    numeric = _non_negative(value, f"{label} flow")
    unit = str(item.get("flow_unit", item.get("unit", "L/s")) or "L/s").lower().replace("³", "3").replace(" ", "")
    factors = {"l/s": 1.0, "ls": 1.0, "m3/s": 1000.0, "m3/h": 1000.0 / 3600.0, "m3h": 1000.0 / 3600.0}
    if unit not in factors:
        raise ValueError(f"{label} unit must be L/s, m³/s, or m³/h.")
    return numeric * factors[unit], {"value": numeric, "unit": item.get("flow_unit", item.get("unit", "L/s")), "conversion": f"multiply by {factors[unit]} to L/s"}


def _scheduled_value(value, schedule, hour):
    return float(value) * (schedule[hour] if schedule else 1.0)


def _source_register(plants, circuits, gate):
    records = []
    for plant in plants.get("systems", []):
        records.append({"kind": "plant_system", "id": plant["plant_id"], "source": plant.get("source", ""), "citations": deepcopy(plant.get("citations", []))})
    for circuit in circuits.get("circuits", []):
        records.append({"kind": "circuit", "id": circuit["circuit_id"], "source": circuit.get("source", ""), "citations": deepcopy(circuit.get("citations", []))})
        records.extend({"kind": "pump", "id": pump["pump_id"], "source": pump.get("source", ""), "citations": deepcopy(pump.get("citations", []))} for pump in circuit.get("pumps", []))
        records.extend({"kind": "pipe_effect", "id": pipe["pipe_id"], "source": pipe.get("source", ""), "citations": deepcopy(pipe.get("citations", []))} for pipe in circuit.get("pipe_effects", []))
    records.append({"kind": "plant_method_gate", "id": gate.get("method_id", ""), "source": gate.get("method_citation", ""), "citations": deepcopy(gate.get("citations", []))})
    return records


def _governing(rows, key):
    return max((row.get(key) or {} for row in rows if row.get(key)), key=lambda row: row.get("plant_duty_kw", 0.0), default={})


def _peak(rows, key):
    if not rows:
        return {}
    maximum = max(row.get(key, 0.0) for row in rows)
    tied = [row.get("hour") for row in rows if row.get(key, 0.0) == maximum]
    return {"plant_duty_kw": maximum, "tied_hours": tied, "display_hour": min(tied), "hour": min(tied)}


def _sum_hours(rows, hour):
    return {"hour": hour, "plant_duty_kw": round(sum(row.get("plant_duty_kw", 0.0) for row in rows), 6), "coincident_ahu_duty_kw": round(sum(row.get("coincident_ahu_duty_kw", 0.0) for row in rows), 6), "pump_power_kw": round(sum(row.get("pump_power_kw", 0.0) for row in rows), 6), "pipe_effect_kw": round(sum(row.get("pipe_effect_kw", 0.0) for row in rows), 6), "status": "calculated"}


def _id(value, label):
    value = str(value or "").strip()
    if not value or not value[0].islower() or not all(char.isalnum() or char in "_-" for char in value):
        raise ValueError(f"{label} must be a stable lowercase ID.")
    return value


def _ref(value, label, allowed, context):
    value = str(value or "").strip()
    if not value or (allowed and value not in allowed):
        raise ValueError(f"{context} references an unknown {label}.")
    return value


def _string_list(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must be a list of IDs.")
    return list(dict.fromkeys(item.strip() for item in value))


def _number(value, label):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} is required.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite.")
    return result


def _optional_number(value, label):
    return None if value is None else _number(value, label)


def _non_negative(value, label, required=False):
    if value is None and not required:
        return 0.0
    result = _number(value, label)
    if result < 0:
        raise ValueError(f"{label} cannot be negative.")
    return result


def _positive_int(value, label):
    result = _number(value, label)
    if result <= 0 or not result.is_integer():
        raise ValueError(f"{label} must be a positive integer.")
    return int(result)


def _factor(value, label, required=False):
    if value is None and not required:
        return None
    result = _number(value, label)
    if result < 0 or result > 1:
        raise ValueError(f"{label} must be between 0 and 1.")
    return result


def _profile(value, label):
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) != 24 or any(not isinstance(item, (int, float)) or item < 0 or item > 1 for item in value):
        raise ValueError(f"{label} must contain 24 values between 0 and 1.")
    return [float(item) for item in value]
