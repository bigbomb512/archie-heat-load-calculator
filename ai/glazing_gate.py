"""Project-local approval record for reviewed glazing with manual solar."""

from copy import deepcopy
from datetime import datetime, timezone

from ai.site_design_conditions import validate_citations


METHOD_ID = "reviewed_glazing_manual_solar_v1"
APPROVAL_STATES = {"placeholder", "approved"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_glazing_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": "Reviewed glazing conduction and manual hourly solar transmission only.",
        "policy": {
            "solar_basis": "reviewed_manual_incident_hourly_basis",
            "u_value_basis": "overall_window",
            "opening_mapping": "confirmed_room_owned_opening_required",
            "opaque_area_policy": "net_opaque_or_gross_minus_complete_confirmed_openings",
            "safety_factor_policy": "existing_room_factor_once",
            "unsupported": ["solar_position", "geometric_shading", "dynamic_shading", "annual_analysis"],
        },
        "citations": [],
    }


def validate_glazing_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Glazing method gate must be a JSON object.")
    result = deepcopy(empty_glazing_method_gate())
    result.update({key: raw.get(key, result[key]) for key in result})
    result["schema_version"] = 1
    result["method_id"] = str(result["method_id"] or "").strip()
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Glazing method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError("Glazing approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Glazing method gate")
    if result.get("policy") != empty_glazing_method_gate()["policy"]:
        raise ValueError("Glazing method policy is fixed for V1; create a new approved method for a policy change.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            detail = ", ".join(missing + ([] if result["citations"] else ["citation"]))
            raise ValueError(f"Approved glazing method gate requires {detail}.")
    result["updated_at"] = str(raw.get("updated_at", ""))
    return result


def gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)
