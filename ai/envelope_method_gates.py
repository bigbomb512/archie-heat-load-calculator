"""Approval gates for envelope methods beyond the legacy external wall path."""

from copy import deepcopy
from ai.site_design_conditions import validate_citations

METHOD_ID = "ground_contact_fixed_v1"
APPROVAL_STATES = {"placeholder", "approved"}
DYNAMIC_THERMAL_MASS_METHOD_ID = "dynamic_thermal_mass_rc_v1"
SOLAR_RADIATION_METHOD_ID = "cited_solar_radiation_v1"


def empty_ground_contact_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": "Reviewed steady-state ground-contact floor conduction only.",
        "policy": {
            "boundary_basis": "explicit_reviewed_ground_temperature",
            "equation": "U × area × (ground temperature − indoor temperature)",
            "unsupported": ["dynamic_soil_response", "groundwater", "annual_analysis"],
        },
        "citations": [],
    }


def validate_ground_contact_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Ground-contact method gate must be a JSON object.")
    result = deepcopy(empty_ground_contact_method_gate())
    result.update({key: raw.get(key, result[key]) for key in result})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Ground-contact method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError("Ground-contact approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), "Ground-contact method gate")
    if result.get("policy") != empty_ground_contact_method_gate()["policy"]:
        raise ValueError("Ground-contact method policy is fixed for V1.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            raise ValueError("Approved ground-contact method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citation"])) + ".")
    return result


def ground_contact_gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == METHOD_ID)


def _empty_advanced_gate(method_id, scope, policy):
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": method_id,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "",
        "scope": scope,
        "policy": policy,
        "citations": [],
    }


def empty_dynamic_thermal_mass_method_gate():
    return _empty_advanced_gate(
        DYNAMIC_THERMAL_MASS_METHOD_ID,
        "First-order hourly resistance-capacitance envelope response only.",
        {
            "model": "first_order_rc_hourly",
            "timestep_hours": 1,
            "unsupported": ["multi_zone_dynamic_coupling", "groundwater", "annual_analysis"],
        },
    )


def empty_solar_radiation_method_gate():
    return _empty_advanced_gate(
        SOLAR_RADIATION_METHOD_ID,
        "Cited hourly incident irradiance supplied on the reviewed surface plane.",
        {
            "basis": "cited_hourly_surface_irradiance",
            "requires_orientation": True,
            "unsupported": ["uncited_weather_lookup", "diffuse_sky_inference", "annual_analysis"],
        },
    )


WEATHER_FACADE_POLICY = {
    "basis": "cited_hourly_horizontal_weather_pvlib_isotropic_v1",
    "requires_orientation": True,
    "unsupported": ["uncited_weather_lookup", "inferred_ground_reflectance", "annual_analysis"],
}


def _validate_advanced_gate(raw, empty, label):
    if not isinstance(raw, dict):
        raise ValueError(f"{label} method gate must be a JSON object.")
    result = deepcopy(empty())
    result.update({key: raw.get(key, result[key]) for key in result})
    if result["method_id"] != empty()["method_id"]:
        raise ValueError(f"{label} method ID is fixed for V1.")
    if result["approval_status"] not in APPROVAL_STATES:
        raise ValueError(f"{label} approval status must be placeholder or approved.")
    for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[field] = str(result.get(field, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", []), f"{label} method gate")
    allowed_policies = [empty()["policy"]]
    if label == "Solar-radiation":
        allowed_policies.append(WEATHER_FACADE_POLICY)
    if result.get("policy") not in allowed_policies:
        raise ValueError(f"{label} method policy is fixed for V1.")
    if result["approval_status"] == "approved":
        missing = [field.replace("_", " ") for field in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[field]]
        if missing or not result["citations"]:
            raise ValueError("Approved " + label.lower() + " method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citation"])) + ".")
    return result


def validate_dynamic_thermal_mass_method_gate(raw):
    return _validate_advanced_gate(raw, empty_dynamic_thermal_mass_method_gate, "Dynamic thermal-mass")


def validate_solar_radiation_method_gate(raw):
    return _validate_advanced_gate(raw, empty_solar_radiation_method_gate, "Solar-radiation")


def dynamic_thermal_mass_gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == DYNAMIC_THERMAL_MASS_METHOD_ID)


def solar_radiation_gate_is_approved(gate):
    return bool(gate and gate.get("approval_status") == "approved" and gate.get("method_id") == SOLAR_RADIATION_METHOD_ID)


def solar_radiation_weather_gate_is_approved(gate):
    return solar_radiation_gate_is_approved(gate) and gate.get("policy") == WEATHER_FACADE_POLICY
