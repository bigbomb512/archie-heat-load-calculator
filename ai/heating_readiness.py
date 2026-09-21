"""Readiness assessment for the independent hourly heating report."""

from copy import deepcopy

from ai.heating_gate import heating_gate_is_approved


def assess_heating_readiness(report, heating_gate):
    issues = []
    for reason in report.get("blocked_reasons", []):
        issues.append({"scope": "project", "status": "blocked", "affected_id": "project", "reason": reason, "source_artifact": "hourly_heating_load_report.json", "remediation": "Resolve the heating report blocker and calculate again."})
    for scenario in report.get("scenario_results", []):
        for room in scenario.get("rooms", []):
            for reason in room.get("blocked_reasons", []):
                issues.append({"scope": "room", "status": "blocked", "affected_id": room.get("room_id", ""), "reason": reason, "source_artifact": "hourly_load_model.json", "remediation": "Complete the cited room heating input."})
    if not heating_gate_is_approved(heating_gate):
        issues.append({"scope": "project", "status": "draft", "affected_id": "heating_method_gate", "reason": "Heating method gate is placeholder; complete heating remains draft-only.", "source_artifact": "heating_method_gate.json", "remediation": "Obtain the named HVAC engineer approval."})
    complete_scope = bool(report.get("scope_summary", {}).get("complete_scope"))
    if issues and not report.get("included_scope_peak"):
        status = "blocked"
    elif issues or not complete_scope or not heating_gate_is_approved(heating_gate):
        status = "draft"
    else:
        status = "review_ready"
    return {
        "status": status,
        "issues": deepcopy(issues),
        "scope_summary": deepcopy(report.get("scope_summary", {})),
        "complete_scope": complete_scope,
        "active_room_ids": list(report.get("scope_summary", {}).get("active_room_ids", [])),
        "included_room_ids": list(report.get("scope_summary", {}).get("included_room_ids", [])),
        "blocked_rooms": deepcopy(report.get("scope_summary", {}).get("blocked_rooms", [])),
    }
