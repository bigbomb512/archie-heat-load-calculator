"""Cited, approval-gated room moisture gains for hourly cooling estimates.

The mass-rate method follows the zone latent-load definition in ANSI/ASHRAE
Standard 140-2014 Addendum a: water is vaporised from a 0 C reference and the
resulting vapor is brought to zone temperature. ASHRAE's psychrometric
enthalpy approximation supplies the 2501 + 1.86 T coefficients. The result
is draft until a qualified engineer approves the project-local method gate.
"""

from copy import deepcopy
import math

from ai.site_design_conditions import validate_citations


METHOD_ID = "room_internal_moisture_v1"


def empty_moisture_method_gate():
    return {
        "schema_version": 1,
        "updated_at": "",
        "method_id": METHOD_ID,
        "approval_status": "placeholder",
        "engineer_name": "",
        "engineer_credential": "",
        "approved_at": "",
        "method_citation": "ANSI/ASHRAE Standard 140-2014 Addendum a, zone latent load definition; ASHRAE Handbook—Fundamentals, Chapter 1, Psychrometrics",
        "scope": "Direct room water-vapor latent gains supplied as W or water-vapor generation rate.",
        "policy": {
            "mass_rate_basis": "water_vapor_added_at_zero_c_reference_then_brought_to_zone_temperature",
            "vapor_enthalpy_kj_kg": "2501 + 1.86 × zone dry-bulb °C",
            "steam_mass_rate": "unsupported_without_source_state; provide reviewed latent W instead",
            "sensible_load_from_source": "not_included",
        },
        "citations": [{
            "reference": "ANSI/ASHRAE Standard 140-2014 Addendum a, Zone latent load definition; ASHRAE Handbook—Fundamentals, Chapter 1, Psychrometrics",
            "url": "https://www.ashrae.org/File%20Library/Technical%20Resources/Standards%20and%20Guidelines/Standards%20Addenda/140_2014_a_20170516.pdf",
            "excerpt": "Zone latent load uses the moisture mass rate times water-vapor enthalpy at zone temperature; specific vapor enthalpy follows h = 2501 + 1.86 T in SI units.",
        }],
    }


def validate_moisture_method_gate(raw):
    if not isinstance(raw, dict):
        raise ValueError("Moisture method gate must be an object.")
    result = deepcopy(empty_moisture_method_gate())
    result.update({key: deepcopy(raw.get(key, value)) for key, value in result.items()})
    if result["method_id"] != METHOD_ID:
        raise ValueError(f"Moisture method ID must be '{METHOD_ID}'.")
    if result["approval_status"] not in {"placeholder", "approved"}:
        raise ValueError("Moisture method gate approval status must be placeholder or approved.")
    if result["policy"] != empty_moisture_method_gate()["policy"]:
        raise ValueError("Moisture method policy is fixed for V1.")
    for key in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope"):
        result[key] = str(result.get(key, "") or "").strip()
    result["citations"] = validate_citations(raw.get("citations", result["citations"]), "Moisture method gate")
    if result["approval_status"] == "approved":
        missing = [key.replace("_", " ") for key in ("engineer_name", "engineer_credential", "approved_at", "method_citation", "scope") if not result[key]]
        if missing or not result["citations"]:
            raise ValueError("Approved moisture method gate requires " + ", ".join(missing + ([] if result["citations"] else ["citations"])) + ".")
    return result


def moisture_method_gate_is_approved(gate):
    return bool(gate and gate.get("method_id") == METHOD_ID and gate.get("approval_status") == "approved")


def moisture_gain(component, zone_dry_bulb_c):
    """Return a latent-only contribution from a reviewed component.

    `W` means an engineer-supplied latent heat rate. `kg/h` and `g/h` mean
    water-vapor generation under the Standard 140 0 C reference convention.
    Steam is accepted only as direct latent W until its source state is known.
    """
    value = component.get("value")
    unit = component.get("unit")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Moisture gain must be a positive finite value.")
    if isinstance(zone_dry_bulb_c, bool) or not isinstance(zone_dry_bulb_c, (int, float)) or not math.isfinite(zone_dry_bulb_c):
        raise ValueError("Zone dry-bulb temperature must be finite.")
    kind = component.get("component_type")
    if kind == "steam_gain" and unit != "W":
        raise ValueError("Steam mass flow requires a source-state method; supply reviewed latent W for V1.")
    if unit == "W":
        latent_kw = value / 1000.0
        formula = "engineer-supplied latent W ÷ 1000"
    elif unit in {"kg/h", "g/h"} and kind in {"vapour_gain", "process_latent_load"}:
        mass_kg_s = value / (3600.0 if unit == "kg/h" else 3_600_000.0)
        vapor_enthalpy_kj_kg = 2501.0 + 1.86 * zone_dry_bulb_c
        latent_kw = mass_kg_s * vapor_enthalpy_kj_kg
        formula = "water-vapor kg/s × (2501 + 1.86 × zone dry-bulb °C) kJ/kg"
    else:
        raise ValueError("Unsupported moisture input basis for this component.")
    return {
        "name": "internal_moisture",
        "sensible_kw": 0.0,
        "latent_kw": round(latent_kw, 4),
        "total_kw": round(latent_kw, 4),
        "inputs": {
            "component_id": component.get("component_id", ""),
            "component_type": kind,
            "value": value,
            "unit": unit,
            "zone_dry_bulb_c": zone_dry_bulb_c,
            "verification_status": component.get("verification_status", "missing"),
            "source": component.get("source", ""),
            "citations": deepcopy(component.get("citations", [])),
            "method_id": METHOD_ID,
        },
        "formula": formula,
    }
