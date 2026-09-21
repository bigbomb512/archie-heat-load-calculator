"""Validation and deterministic use of cited hourly surface irradiance."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from math import isfinite
from zoneinfo import ZoneInfo


HORIZONTAL_BASIS = "horizontal_components"
SOLAR_METHOD = "pvlib-0.15.2-isotropic-v1"
AZIMUTHS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_solar_radiation_source():
    return {
        "schema_version": 1,
        "source_id": "",
        "location": "",
        "timezone": "",
        "date": "",
        "surface_orientation": "",
        "irradiance_basis": "surface_plane_incident",
        "scenario_id": "",
        "weather_day_type": "design_day",
        "latitude_deg": None,
        "longitude_deg": None,
        "hour_convention": "hour_start",
        "ground_reflectance": None,
        "ground_reflectance_source": "",
        "ground_reflectance_citations": [],
        "hours": [],
        "method": "",
        "source": "",
        "citations": [],
        "updated_at": "",
        "fingerprint": "",
    }


def validate_solar_radiation_source(raw):
    if not isinstance(raw, dict):
        raise ValueError("Solar-radiation source must be an object.")
    result = {**empty_solar_radiation_source(), **deepcopy(raw)}
    if not any(result.get(field) for field in ("source_id", "location", "timezone", "date", "hours", "method", "source", "citations")):
        return result
    for field in ("source_id", "location", "timezone", "date", "surface_orientation", "method", "source"):
        result[field] = str(result.get(field, "") or "").strip()
    if not result["source_id"] or not result["location"] or not result["timezone"] or not result["date"]:
        raise ValueError("Solar-radiation source requires source_id, location, timezone, and date.")
    basis = result["irradiance_basis"]
    if basis not in {"surface_plane_incident", HORIZONTAL_BASIS}:
        raise ValueError("Solar-radiation irradiance basis is unsupported.")
    if not result["method"] or not result["source"] or not result.get("citations"):
        raise ValueError("Solar-radiation source requires a method, source, and citation.")
    hours = result.get("hours")
    if not isinstance(hours, list) or len(hours) != 24:
        raise ValueError("Solar-radiation source requires exactly 24 hourly records.")
    seen = set()
    checked = []
    for row in hours:
        if not isinstance(row, dict) or row.get("hour") in seen or not isinstance(row.get("hour"), int) or not 0 <= row["hour"] <= 23:
            raise ValueError("Solar-radiation hours must contain unique hour integers from 0 through 23.")
        fields = ("irradiance_w_m2",) if basis == "surface_plane_incident" else ("dni_w_m2", "dhi_w_m2", "ghi_w_m2")
        values = {field: _nonnegative(row.get(field), field) for field in fields}
        seen.add(row["hour"])
        checked.append({"hour": row["hour"], **values})
    if seen != set(range(24)):
        raise ValueError("Solar-radiation hours must contain every hour from 0 through 23.")
    result["hours"] = sorted(checked, key=lambda row: row["hour"])
    if basis == HORIZONTAL_BASIS:
        if not result.get("scenario_id") or result.get("weather_day_type") not in {"design_day", "representative_weather_file_day"}:
            raise ValueError("Horizontal solar weather needs a scenario ID and explicit design/representative day type.")
        if result.get("hour_convention") not in {"hour_start", "hour_end"}:
            raise ValueError("Horizontal solar weather needs an hour convention.")
        try:
            date = datetime.fromisoformat(result["date"])
            ZoneInfo(result["timezone"])
        except (ValueError, KeyError) as error:
            raise ValueError("Horizontal solar weather needs an ISO date and IANA timezone.") from error
        if date.tzinfo is not None or date.time() != datetime.min.time():
            raise ValueError("Horizontal solar weather date must be a local calendar date.")
        for field, lower, upper in (("latitude_deg", -90, 90), ("longitude_deg", -180, 180), ("ground_reflectance", 0, 1)):
            value = result.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"Horizontal solar weather needs valid {field}.")
            result[field] = float(value)
        if not result.get("ground_reflectance_source") or not result.get("ground_reflectance_citations"):
            raise ValueError("Ground reflectance needs a reviewed source and citation.")
        result["transposition_method"] = SOLAR_METHOD
    result["fingerprint"] = fingerprint({key: result[key] for key in result if key not in {"updated_at", "fingerprint"}})
    return result


def _nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"Solar-radiation {label} must be a finite non-negative number.")
    return float(value)


def facade_irradiance(source, hour, orientation, scenario_id):
    """Cited horizontal weather to vertical-façade POA; never a weather lookup."""
    source = validate_solar_radiation_source(source)
    if source["irradiance_basis"] != HORIZONTAL_BASIS or source["scenario_id"] != scenario_id:
        raise ValueError("Solar weather must match the selected scenario and horizontal basis.")
    if isinstance(orientation, str):
        if orientation not in AZIMUTHS:
            raise ValueError("A reviewed cardinal façade or degree azimuth is required.")
        azimuth_deg = AZIMUTHS[orientation]
    elif type(orientation) in (int, float) and isfinite(orientation) and 0 <= orientation < 360:
        azimuth_deg = float(orientation)
    else:
        raise ValueError("A reviewed cardinal façade or degree azimuth is required.")
    if not isinstance(hour, int) or hour not in range(24):
        raise ValueError("A reviewed hour 0–23 is required.")
    try:
        from pvlib import irradiance, solarposition
        import pandas as pd
    except ImportError as error:
        raise ValueError("The pinned pvlib solar dependency is unavailable.") from error
    stamp = datetime.fromisoformat(source["date"]).replace(tzinfo=ZoneInfo(source["timezone"]))
    stamp += timedelta(hours=hour, minutes=30 if source["hour_convention"] == "hour_start" else -30)
    position = solarposition.get_solarposition(pd.DatetimeIndex([stamp]), source["latitude_deg"], source["longitude_deg"]).iloc[0]
    data = source["hours"][hour]
    poa = irradiance.get_total_irradiance(90, azimuth_deg, position["apparent_zenith"], position["azimuth"],
                                          data["dni_w_m2"], data["ghi_w_m2"], data["dhi_w_m2"],
                                          albedo=source["ground_reflectance"], model="isotropic")
    daylight = position["apparent_elevation"] > 0
    return {"hour": hour, "timestamp_local": stamp.isoformat(), "method": SOLAR_METHOD,
            "source_id": source["source_id"], "source_fingerprint": source["fingerprint"],
            "source_citations": source["citations"], "scenario_id": scenario_id,
            "weather_day_type": source["weather_day_type"], "orientation": orientation, "surface_azimuth_deg": azimuth_deg,
            "solar_azimuth_deg": float(position["azimuth"]), "solar_altitude_deg": float(position["apparent_elevation"]),
            "direct_w_m2": max(0.0, float(poa["poa_direct"])) if daylight else 0.0,
            "sky_diffuse_w_m2": max(0.0, float(poa["poa_sky_diffuse"])) if daylight else 0.0,
            "ground_diffuse_w_m2": max(0.0, float(poa["poa_ground_diffuse"])) if daylight else 0.0}


def irradiance_for_hour(source, hour):
    source = validate_solar_radiation_source(source)
    if source["irradiance_basis"] != "surface_plane_incident":
        raise ValueError("Surface-plane irradiance is required for this calculation.")
    if not isinstance(hour, int) or hour < 0 or hour > 23:
        raise ValueError("Solar-radiation hour must be between 0 and 23.")
    return source["hours"][hour]["irradiance_w_m2"]


def absorbed_solar_gain_kw(source, hour, area_m2, absorptance):
    if not isinstance(area_m2, (int, float)) or area_m2 <= 0:
        raise ValueError("Solar-radiation area must be positive.")
    if not isinstance(absorptance, (int, float)) or not 0 <= absorptance <= 1:
        raise ValueError("Solar-radiation absorptance must be between 0 and 1.")
    return round(irradiance_for_hour(source, hour) * area_m2 * absorptance / 1000.0, 6)
