"""Approval gate for the separate hourly heating calculation path."""

from copy import deepcopy
import hashlib
import json

from ai.site_design_conditions import validate_citations


METHOD_ID = "heating_room_load_v1"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_heating_method_gate():
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
        "scope": "Separate hourly room heating conduction and sensible air-load method.",
        "policy": {
            "positive_sensible_only": True,
            "internal_gain_credit": False,
            "heating_solar_credit": False,
            "annual_analysis": False,
        },
        "citations": [],
    }


def heating_gate_fingerprint(gate):
    canonical = deepcopy(gate or {})
    canonical.pop("fingerprint", None)
    return _fingerprint(canonical)


def validate_heating_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Heating method gate must be a JSON object.")
    result = deepcopy(empty_heating_method_gate())
    result.update({key: raw.get(key, value) for key, value in result.items()})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Heating method ID must be '{METHOD_ID}'.")
    if result["method_version"] != "1.0":
        raise ValueError("Heating method version must be 1.0 for this slice.")
    if result["approval_status"] not in {"placeholder", "approved"}:
        raise ValueError("Heating method approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Heating method gate")
    if result.get("policy") != empty_heating_method_gate()["policy"]:
        raise ValueError("Heating method policy is fixed for V1.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            raise ValueError("Approved heating method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citation"])) + ".")
    result["fingerprint"] = heating_gate_fingerprint(result)
    return result


def heating_gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)
