"""Deterministic assembly and readiness gate for calculator inputs.

This stage does not calculate heat loads.  It proves that the inputs handed to
the existing hourly engine are current, traceable, and complete enough for the
declared scope.
"""

from copy import deepcopy
import hashlib
import json

from ai.hourly_loads import (
    validate_hourly_load_model, validate_schedule_library, validate_design_day_scenarios,
    room_static_missing, scenario_ready,
)
from ai.research_cache import validate_cache, eligible_records


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def assemble_calculator_inputs(hourly_model, schedule_library, scenarios, selected_scenario_ids=None,
                               fusion=None, research_cache=None, envelope=None):
    model = validate_hourly_load_model(hourly_model)
    schedules = validate_schedule_library(schedule_library)
    scenario_library = validate_design_day_scenarios(scenarios)
    selected = selected_scenario_ids or [row["scenario_id"] for row in scenario_library["scenarios"] if row.get("mode") == "cooling"]
    scenario_ids = {row["scenario_id"] for row in scenario_library["scenarios"]}
    research_cache = validate_cache(research_cache or {"schema_version": 1, "revision": 0, "records": []})
    issues, included, excluded = [], [], []
    floor_ids = {row["floor_id"] for row in model["floors"]}
    zone_map = {row["zone_id"]: row for row in model["zones"]}
    schedule_map = {row["schedule_id"]: row for row in schedules["schedules"]}
    for room in model["rooms"]:
        room_id = room["room_id"]
        zone = zone_map.get(room["zone_id"])
        if not zone or zone.get("floor_id") not in floor_ids:
            issues.append({"status": "blocked", "affected_id": room_id, "reason": "Room has no valid floor/zone mapping.", "source_artifact": "hourly_load_model.json"})
            excluded.append(room_id)
            continue
        room_issues = room_static_missing(room)
        assignments = room.get("schedule_assignments", {})
        for assignment in assignments.values() if isinstance(assignments, dict) else []:
            schedule_id = assignment.get("schedule_id") if isinstance(assignment, dict) else assignment
            if schedule_id not in schedule_map:
                room_issues.append("assigned schedule is missing")
        if room_issues:
            status = "draft" if room.get("area_m2") else "blocked"
            issues.extend({"status": status, "affected_id": room_id, "reason": reason, "source_artifact": "hourly_load_model.json"} for reason in room_issues)
            (included if status == "draft" else excluded).append(room_id)
        else:
            included.append(room_id)
    selected_scenarios = {row["scenario_id"]: row for row in scenario_library["scenarios"]}
    for scenario_id in selected:
        if scenario_id not in scenario_ids:
            issues.append({"status": "blocked", "affected_id": scenario_id, "reason": "Selected scenario is missing.", "source_artifact": "design_day_scenarios.json"})
        else:
            missing, provisional = scenario_ready(selected_scenarios[scenario_id])
            for reason in missing:
                issues.append({"status": "blocked", "affected_id": scenario_id, "reason": reason, "source_artifact": "design_day_scenarios.json"})
            if provisional:
                issues.append({"status": "draft", "affected_id": scenario_id, "reason": "Selected design-day scenario contains provisional inputs.", "source_artifact": "design_day_scenarios.json"})
    if not selected:
        issues.append({"status": "blocked", "affected_id": "project", "reason": "No cooling design scenario is selected.", "source_artifact": "design_day_scenarios.json"})
    if not model["rooms"]:
        issues.append({"status": "blocked", "affected_id": "project", "reason": "No rooms are available for calculation.", "source_artifact": "hourly_load_model.json"})
    status = "blocked" if any(row["status"] == "blocked" for row in issues) and not included else ("draft" if issues else "ready")
    result = {
        "schema_version": 1, "status": status, "selected_scenario_ids": selected,
        "included_room_ids": sorted(set(included)), "excluded_room_ids": sorted(set(excluded)),
        "issues": issues, "inputs_used": {
            "hourly_model": {"schema_version": model.get("schema_version"), "updated_at": model.get("updated_at")},
            "schedule_library": {"updated_at": schedules.get("updated_at")},
            "scenarios": {"updated_at": scenario_library.get("updated_at")},
            "fusion": {"fingerprint": (fusion or {}).get("fingerprint", "")},
            "research_cache": {"fingerprint": (research_cache or {}).get("fingerprint", "")},
            "envelope": {"fingerprint": (envelope or {}).get("fingerprint", "")},
        },
        "research_defaults_available": [
            {"record_id": row["record_id"], "category": row["category"], "value": row["value"],
             "unit": row["unit"], "citation": row["citation"], "scope": row["scope"]}
            for row in research_cache["records"] if row.get("review_status") == "approved"
        ],
        "excluded_components": ["unresolved or unsupported inputs remain excluded until their approved calculation method exists"],
    }
    result["input_fingerprint"] = _fingerprint(result)
    return result
