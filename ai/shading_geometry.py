"""Pure, cited-geometry external shading calculations for reviewed glazing."""

from math import cos, radians, tan


FACADE_AZIMUTHS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def assess_geometric_shading(surface, record, *, position_override=None):
    if not isinstance(surface, dict) or not isinstance(record, dict):
        return ["surface and shading record are required"]
    issues = []
    if surface.get("orientation") not in FACADE_AZIMUTHS:
        issues.append("a cardinal glazing orientation is required")
    if surface.get("opening_mapping_status") != "confirmed":
        issues.append("confirmed opening mapping is required")
    if surface.get("review_status") != "confirmed":
        issues.append("confirmed glazing surface review is required")
    if record.get("review_status") != "confirmed":
        issues.append("confirmed shading record review is required")
    if not record.get("source") or not record.get("citations"):
        issues.append("shading record source and citations are required")
    geometry = record.get("geometry", {})
    if not isinstance(geometry, dict):
        issues.append("shading geometry is required")
    elif not any(float(geometry.get(key, 0) or 0) > 0 for key in ("overhang_depth_m", "left_fin_depth_m", "right_fin_depth_m", "reveal_depth_m", "obstruction_altitude_deg")):
        issues.append("at least one positive shading dimension or obstruction altitude is required")
    positions = record.get("hourly_sun_positions", [])
    if position_override is not None:
        positions = [{"hour": hour, "azimuth_deg": position_override["azimuth_deg"], "altitude_deg": position_override["altitude_deg"]} for hour in range(24)]
    if not isinstance(positions, list) or len(positions) != 24:
        issues.append("24 cited hourly sun positions are required")
    else:
        seen = set()
        for item in positions:
            try:
                hour, azimuth, altitude = int(item.get("hour")), float(item.get("azimuth_deg")), float(item.get("altitude_deg"))
                if hour not in range(24) or hour in seen or not 0 <= azimuth < 360 or not -90 <= altitude <= 90:
                    raise ValueError
                seen.add(hour)
            except (AttributeError, TypeError, ValueError):
                issues.append("hourly sun positions need unique hours 0–23, azimuth 0–360, and altitude −90–90")
                break
    return list(dict.fromkeys(issues))


def geometric_shading_factor(surface, record, hour, *, position_override=None):
    """Return an auditable 0–1 external factor.  It never estimates sun data."""
    issues = assess_geometric_shading(surface, record, position_override=position_override)
    if issues:
        return {"status": "blocked", "unresolved_requirements": issues}
    position = position_override or next(item for item in record["hourly_sun_positions"] if int(item["hour"]) == int(hour))
    azimuth, altitude = float(position["azimuth_deg"]), float(position["altitude_deg"])
    geometry = record["geometry"]
    facade = FACADE_AZIMUTHS[surface["orientation"]]
    relative = ((azimuth - facade + 180) % 360) - 180
    # The manual solar value is already stated on the glazing plane. When the
    # cited sun vector is behind the plane or below the horizon, V1 leaves that
    # basis untouched rather than inventing a diffuse/direct split.
    if altitude <= 0 or cos(radians(relative)) <= 0:
        return _result(1.0, position, relative, 0.0, 0.0, "sun is not in front of the glazing plane")
    if geometry.get("obstruction_altitude_deg") not in (None, "") and altitude <= float(geometry["obstruction_altitude_deg"]):
        return _result(0.0, position, relative, 1.0, 1.0, "cited obstruction blocks the sun vector")
    width, height = _opening_size(surface)
    profile_cos = max(cos(radians(relative)), 0.1)
    vertical_shadow = float(geometry.get("overhang_depth_m", 0) or 0) * tan(radians(altitude)) / profile_cos
    vertical_fraction = _clamp(vertical_shadow / height)
    side_depth = float(geometry.get("right_fin_depth_m" if relative >= 0 else "left_fin_depth_m", 0) or 0)
    side_depth += float(geometry.get("reveal_depth_m", 0) or 0)
    lateral_shadow = side_depth * abs(tan(radians(relative)))
    lateral_fraction = _clamp(lateral_shadow / width)
    factor = round((1 - vertical_fraction) * (1 - lateral_fraction), 6)
    return _result(factor, position, relative, vertical_fraction, lateral_fraction, "confirmed geometric shading")


def _opening_size(surface):
    width, height = surface.get("opening_width_m"), surface.get("opening_height_m")
    if width in (None, "") or height in (None, "") or float(width) <= 0 or float(height) <= 0:
        raise ValueError("Geometric shading requires positive opening width and height.")
    return float(width), float(height)


def _clamp(value):
    return max(0.0, min(1.0, value))


def _result(factor, position, relative, vertical, lateral, reason):
    return {
        "status": "calculated", "external_shading_factor": factor,
        "hourly_sun_position": {"hour": int(position["hour"]), "azimuth_deg": float(position["azimuth_deg"]), "altitude_deg": float(position["altitude_deg"])},
        "relative_azimuth_deg": round(relative, 6), "vertical_shaded_fraction": round(vertical, 6),
        "lateral_shaded_fraction": round(lateral, 6), "reason": reason,
        "formula": "(1 − vertical shaded fraction) × (1 − lateral shaded fraction)",
    }
