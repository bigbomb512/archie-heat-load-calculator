"""Project-local approval record for the cooling infiltration method."""

from copy import deepcopy
from datetime import datetime, timezone

from ai.site_design_conditions import validate_citations


METHOD_ID = "infiltration_psychrometric_v1"
APPROVAL_STATES = {"placeholder", "approved"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_infiltration_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": "Cooling infiltration sensible and latent load only.",
        "policy": {
            "accepted_units": ["ACH", "L/s", "m3/s", "m3/h"],
            "flow_reference": "outdoor_design_condition",
            "schedule_policy": "dedicated_schedule_required",
            "safety_factor_policy": "existing_room_factor_once",
            "air_path": "uncontrolled_infiltration",
            "negative_cooling_policy": "signed_diagnostic_positive_components_only",
        },
        "citations": [],
    }


def validate_infiltration_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Infiltration method gate must be a JSON object.")
    result = deepcopy(empty_infiltration_method_gate())
    result.update({key: raw.get(key, result[key]) for key in result})
    result["schema_version"] = 1
    result["method_id"] = str(result["method_id"] or "").strip()
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Infiltration method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError("Infiltration approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Infiltration method gate")
    policy = result.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("Infiltration method policy must be an object.")
    expected = empty_infiltration_method_gate()["policy"]
    if policy != expected:
        raise ValueError("Infiltration method policy is fixed for V1; create a new approved method for a policy change.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            detail = ", ".join(missing + ([] if result["citations"] else ["citation"]))
            raise ValueError(f"Approved infiltration method gate requires {detail}.")
    result["updated_at"] = str(raw.get("updated_at", ""))
    return result


def gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)
