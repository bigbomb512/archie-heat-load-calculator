"""Approval-gated first-order hourly resistance-capacitance calculations.

This module is intentionally independent from the legacy steady-state envelope
path. It accepts only explicit, cited surface parameters and returns an audit
record; callers must check the dynamic method gate before using the result.
"""

from copy import deepcopy


def validate_rc_surface(raw):
    if not isinstance(raw, dict):
        raise ValueError("RC surface must be an object.")
    required = ("surface_id", "area_m2", "r_exterior_m2k_w", "r_interior_m2k_w", "capacitance_kj_m2k")
    missing = [key for key in required if raw.get(key) in (None, "")]
    if missing:
        raise ValueError("RC surface is missing: " + ", ".join(missing))
    for key in required[1:]:
        value = raw[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"RC surface {key} must be positive.")
    absorptance = raw.get("solar_absorptance", 0.0)
    if not isinstance(absorptance, (int, float)) or isinstance(absorptance, bool) or not 0 <= absorptance <= 1:
        raise ValueError("RC surface solar_absorptance must be between 0 and 1.")
    citations = raw.get("citations") or []
    if not raw.get("source") or not citations:
        raise ValueError("RC surface requires a source and citation.")
    return {
        **deepcopy(raw),
        "area_m2": float(raw["area_m2"]),
        "r_exterior_m2k_w": float(raw["r_exterior_m2k_w"]),
        "r_interior_m2k_w": float(raw["r_interior_m2k_w"]),
        "capacitance_kj_m2k": float(raw["capacitance_kj_m2k"]),
        "solar_absorptance": float(absorptance),
    }


def calculate_first_order_rc(surface, boundary_temperature_c, indoor_temperature_c,
                             previous_state_temperature_c, *, irradiance_w_m2=0.0,
                             timestep_hours=1.0, method_id="dynamic_thermal_mass_rc_v1",
                             gate_version=""):
    """Return one explicit Euler hour for a two-resistance, one-capacitance node.

    The node receives heat through the exterior resistance and explicit solar
    absorption, then rejects heat through the interior resistance. All values
    are signed so the caller can apply the project's cooling sign policy.
    """
    surface = validate_rc_surface(surface)
    values = (boundary_temperature_c, indoor_temperature_c, previous_state_temperature_c, irradiance_w_m2, timestep_hours)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in values):
        raise ValueError("RC temperatures, irradiance, and timestep must be numeric.")
    if irradiance_w_m2 < 0 or timestep_hours <= 0:
        raise ValueError("RC irradiance must be non-negative and timestep must be positive.")
    dt_seconds = timestep_hours * 3600.0
    # Explicit Euler is stable only when the timestep is bounded by the
    # combined resistance/capacitance time constant.  Fail closed rather than
    # returning a numerically plausible but unstable heat flow.
    stability_number = (dt_seconds / (surface["capacitance_kj_m2k"] * 1000.0)) * (
        1.0 / surface["r_exterior_m2k_w"] + 1.0 / surface["r_interior_m2k_w"]
    )
    if stability_number > 2.0:
        raise ValueError("RC timestep is unstable for the cited resistance and capacitance.")
    absorbed_flux_w_m2 = irradiance_w_m2 * surface["solar_absorptance"]
    exterior_flux_w_m2 = (boundary_temperature_c - previous_state_temperature_c) / surface["r_exterior_m2k_w"]
    state_rate_k_s = (exterior_flux_w_m2 + absorbed_flux_w_m2) / (surface["capacitance_kj_m2k"] * 1000.0)
    next_state = previous_state_temperature_c + state_rate_k_s * dt_seconds
    interior_flux_w_m2 = (next_state - indoor_temperature_c) / surface["r_interior_m2k_w"]
    sensible_kw = interior_flux_w_m2 * surface["area_m2"] / 1000.0
    return {
        "status": "calculated",
        "surface_id": surface["surface_id"],
        "sensible_kw": round(sensible_kw, 6),
        "raw_signed_sensible_kw": round(sensible_kw, 6),
        "state_temperature_c": round(next_state, 6),
        "operands": {
            "boundary_temperature_c": boundary_temperature_c,
            "indoor_temperature_c": indoor_temperature_c,
            "previous_state_temperature_c": previous_state_temperature_c,
            "irradiance_w_m2": irradiance_w_m2,
            "timestep_hours": timestep_hours,
            "area_m2": surface["area_m2"],
            "r_exterior_m2k_w": surface["r_exterior_m2k_w"],
            "r_interior_m2k_w": surface["r_interior_m2k_w"],
            "capacitance_kj_m2k": surface["capacitance_kj_m2k"],
            "solar_absorptance": surface["solar_absorptance"],
            "stability_number": round(stability_number, 9),
        },
        "formula": "T_next = T_state + dt/C × ((T_boundary − T_state)/R_ext + α×I); Q_room = (T_next − T_indoor)/R_int × A",
        "method_id": method_id,
        "gate_version": gate_version,
        "source": surface["source"],
        "citations": deepcopy(surface["citations"]),
    }
