"""Approval boundary for deterministic geometric shading V1."""

from copy import deepcopy

from ai.site_design_conditions import validate_citations


METHOD_ID = "reviewed_geometric_shading_manual_solar_v1"
APPROVAL_STATES = {"placeholder", "approved"}


def empty_shading_method_gate():
    return {
        "schema_version": 1, "updated_at": "", "method_id": METHOD_ID,
        "approval_status": "placeholder", "engineer_name": "", "engineer_credential": "",
        "approved_at": "", "method_citation": "",
        "scope": "Reviewed glazing external shading from cited geometry and cited hourly sun positions only.",
        "policy": {
            "solar_basis": "reviewed_manual_direct_incident_hourly_basis",
            "geometry": "confirmed_cited_opening_and_shading_geometry",
            "combination": "geometric_factor_replaces_manual_external_factor",
            "unsupported": ["solar_position_sourcing", "diffuse_sky_model", "dynamic_shading", "annual_analysis"],
        },
        "citations": [],
    }


def validate_shading_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Shading method gate must be a JSON object.")
    result = deepcopy(empty_shading_method_gate())
    result.update({key: raw.get(key, result[key]) for key in result})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Shading method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError("Shading approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Shading method gate")
    if result["policy"] != empty_shading_method_gate()["policy"]:
        raise ValueError("Shading method policy is fixed for V1; create a new approved method for a policy change.")
    if result["approval_status"] == "approved":
        required = ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope")
        missing = [field.replace("_", " ") for field in required if not result[field]]
        if missing or not result["citations"]:
            raise ValueError("Approved shading method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citation"])) + ".")
    result["updated_at"] = str(raw.get("updated_at", ""))
    return result


def gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)
