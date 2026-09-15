#!/usr/bin/env python3

"""Standalone, reviewed glazing calculations.

This module is not imported by the hourly calculation path.  It provides a
small audited boundary for later reviewed-envelope integration.  Geometry,
thermal, solar, and shading inputs must be explicit and cited.
"""

from math import isfinite


def opening_area(width_m, height_m, quantity):
    width = _positive(width_m, "opening width")
    height = _positive(height_m, "opening height")
    count = _positive(quantity, "opening quantity")
    if not float(count).is_integer():
        raise ValueError("Opening quantity must be a whole number.")
    return round(width * height * int(count), 6)


def glass_area(*, opening_area_m2=None, explicit_glass_area_m2=None, frame_fraction=None):
    if explicit_glass_area_m2 not in (None, ""):
        return _positive(explicit_glass_area_m2, "explicit glass area")
    if opening_area_m2 in (None, ""):
        raise ValueError("Opening area is required when explicit glass area is absent.")
    area = _positive(opening_area_m2, "opening area")
    if frame_fraction in (None, ""):
        raise ValueError("Frame fraction is required when explicit glass area is absent.")
    fraction = _fraction(frame_fraction, "frame fraction")
    if fraction >= 1:
        raise ValueError("Frame fraction must be less than 1 when deriving glass area.")
    return round(area * (1 - fraction), 6)


def corrected_glass_area(glass_area_m2, correction_factor):
    return round(_positive(glass_area_m2, "glass area") * _positive(correction_factor, "glass-area correction factor"), 6)


def glazing_conduction(u_value_w_m2k, opening_area_m2, boundary_temperature_c, indoor_temperature_c):
    u_value = _positive(u_value_w_m2k, "U-value")
    area = _positive(opening_area_m2, "opening area")
    boundary = _number(boundary_temperature_c, "boundary temperature")
    indoor = _number(indoor_temperature_c, "indoor temperature")
    return round(u_value * area * (boundary - indoor) / 1000, 6)


def manual_solar_transmission(incident_solar_w_m2, corrected_glass_area_m2, solar_transmission_factor,
                              external_shading_factor, internal_shading_factor):
    incident = _non_negative(incident_solar_w_m2, "incident solar")
    area = _positive(corrected_glass_area_m2, "corrected glass area")
    transmission = _fraction(solar_transmission_factor, "solar-transmission factor")
    external = _fraction(external_shading_factor, "external shading factor")
    internal = _fraction(internal_shading_factor, "internal shading factor")
    return round(incident * area * transmission * external * internal / 1000, 6)


def assess_glazing_eligibility(surface, window, manual_solar, *, boundary_temperature_c=None, indoor_temperature_c=None):
    """Return blocking requirements without inventing or mutating any input."""
    if not isinstance(surface, dict):
        return ["surface record is missing"]
    if not isinstance(window, dict):
        return ["window record is missing"]
    if not isinstance(manual_solar, dict):
        return ["manual solar record is missing"]
    issues = []
    if surface.get("opening_mapping_status") != "confirmed":
        issues.append("opening-to-surface relationship is not confirmed")
    if not surface.get("owner_room_id"):
        issues.append("owning room is missing")
    if not surface.get("owner_zone_id"):
        issues.append("owning zone is missing")
    if surface.get("boundary_method") not in {"external", "fixed_adjacent_temperature"}:
        issues.append("boundary method is missing or unsupported")
    if surface.get("review_status") != "confirmed":
        issues.append("surface review status is not confirmed")
    if window.get("review_status") != "confirmed":
        issues.append("window review status is not confirmed")
    if not surface.get("source") or not surface.get("citations"):
        issues.append("surface source and citations are required")
    if not window.get("source") or not window.get("citations"):
        issues.append("window source and citations are required")
    has_dimensions = all(surface.get(key) not in (None, "") for key in ("opening_width_m", "opening_height_m", "opening_quantity"))
    has_glass_area = surface.get("explicit_glass_area_m2") not in (None, "")
    if not has_dimensions and not has_glass_area:
        issues.append("positive opening dimensions or explicit glass area are required")
    if has_dimensions:
        try:
            opening_area(surface["opening_width_m"], surface["opening_height_m"], surface["opening_quantity"])
        except ValueError as error:
            issues.append(str(error))
    if has_glass_area:
        try:
            _positive(surface["explicit_glass_area_m2"], "explicit glass area")
        except ValueError as error:
            issues.append(str(error))
    if window.get("u_value_w_m2k") in (None, ""):
        issues.append("U-value is missing")
    solar_property = [window.get("shgc"), window.get("solar_transmission_factor")]
    if sum(value not in (None, "") for value in solar_property) != 1:
        issues.append("exactly one reviewed SHGC or solar-transmission factor is required")
    if window.get("glass_area_correction") in (None, ""):
        issues.append("glass-area correction factor is missing")
    if not has_glass_area and window.get("frame_fraction") in (None, ""):
        issues.append("frame fraction is required when glass area is not explicit")
    if window.get("internal_shading_factor") in (None, ""):
        issues.append("internal shading factor is missing")
    if not manual_solar.get("source") or not manual_solar.get("citations"):
        issues.append("manual solar source and citations are required")
    if _solar_value(manual_solar) is None:
        issues.append("complete manual incident solar input is required")
    external_factor = manual_solar.get("external_shading_factor", manual_solar.get("shading_factor"))
    if external_factor in (None, ""):
        issues.append("external shading factor is missing")
    if boundary_temperature_c is None and surface.get("boundary_temperature_c") is None and surface.get("adjacent_temperature_c") is None:
        issues.append("reviewed boundary temperature basis is missing")
    if indoor_temperature_c is None:
        issues.append("indoor temperature is required")
    return list(dict.fromkeys(issues))


def calculate_glazing(surface, window, manual_solar, *, boundary_temperature_c=None, indoor_temperature_c=None):
    """Calculate a reviewed opening or return a traceable blocked result."""
    issues = assess_glazing_eligibility(surface, window, manual_solar,
                                        boundary_temperature_c=boundary_temperature_c,
                                        indoor_temperature_c=indoor_temperature_c)
    if issues:
        return {"status": "blocked", "review_status": "stored_not_calculated", "unresolved_requirements": issues}
    if boundary_temperature_c is None:
        boundary_temperature_c = surface.get("boundary_temperature_c", surface.get("adjacent_temperature_c"))
    if surface.get("opening_width_m") not in (None, ""):
        opening = opening_area(surface["opening_width_m"], surface["opening_height_m"], surface["opening_quantity"])
    else:
        opening = _positive(surface["explicit_glass_area_m2"], "explicit glass area")
    resolved_glass = glass_area(opening_area_m2=opening,
                                explicit_glass_area_m2=surface.get("explicit_glass_area_m2"),
                                frame_fraction=window.get("frame_fraction"))
    corrected = corrected_glass_area(resolved_glass, window["glass_area_correction"])
    property_name = "shgc" if window.get("shgc") not in (None, "") else "solar_transmission_factor"
    external = manual_solar.get("external_shading_factor", manual_solar.get("shading_factor"))
    conduction = glazing_conduction(window["u_value_w_m2k"], opening, boundary_temperature_c, indoor_temperature_c)
    solar = manual_solar_transmission(_solar_value(manual_solar), corrected, window[property_name], external, window["internal_shading_factor"])
    return {
        "status": "calculated", "review_status": "confirmed",
        "opening_area_m2": opening, "glass_area_m2": resolved_glass, "corrected_glass_area_m2": corrected,
        "raw_signed_conduction_kw": conduction, "solar_gain_kw": solar, "total_kw": round(conduction + solar, 6),
        "formulas": {
            "opening_area": "width × height × quantity",
            "glass_area": "explicit glass area or opening area × (1 − frame fraction)",
            "corrected_glass_area": "glass area × glass-area correction factor",
            "conduction": "U-value × opening area × (boundary temperature − indoor temperature) ÷ 1000",
            "solar": "incident solar × corrected glass area × transmission factor × external shading × internal shading ÷ 1000",
        },
        "operands": {"surface_id": surface.get("surface_id", ""), "window_id": window.get("record_id", ""),
                     "boundary_temperature_c": boundary_temperature_c, "indoor_temperature_c": indoor_temperature_c,
                     "incident_solar_w_m2": _solar_value(manual_solar), "solar_property": property_name},
        "citations": {"surface": surface.get("citations", []), "window": window.get("citations", []), "manual_solar": manual_solar.get("citations", [])},
        "unresolved_requirements": [],
    }


def combined_glazing_contribution(*args, **kwargs):
    return calculate_glazing(*args, **kwargs)


def _solar_value(manual_solar):
    for key in ("incident_solar_w_m2", "solar_design_w_m2"):
        value = manual_solar.get(key)
        if value not in (None, ""):
            try:
                return _non_negative(value, key.replace("_", " "))
            except ValueError:
                return None
    return None


def _number(value, label):
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError(f"{label} is required.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric.") from error
    if not isfinite(result):
        raise ValueError(f"{label} must be finite.")
    return result


def _positive(value, label):
    result = _number(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive.")
    return result


def _non_negative(value, label):
    result = _number(value, label)
    if result < 0:
        raise ValueError(f"{label} cannot be negative.")
    return result


def _fraction(value, label):
    result = _number(value, label)
    if not 0 <= result <= 1:
        raise ValueError(f"{label} must be between 0 and 1.")
    return result
