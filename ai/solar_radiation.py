"""Validation and deterministic use of cited hourly surface irradiance."""

from copy import deepcopy
import hashlib
import json


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
    if result["irradiance_basis"] != "surface_plane_incident":
        raise ValueError("Solar-radiation V1 requires surface_plane_incident irradiance.")
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
        value = row.get("irradiance_w_m2")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError("Solar-radiation irradiance must be a non-negative number.")
        seen.add(row["hour"])
        checked.append({"hour": row["hour"], "irradiance_w_m2": float(value)})
    if seen != set(range(24)):
        raise ValueError("Solar-radiation hours must contain every hour from 0 through 23.")
    result["hours"] = sorted(checked, key=lambda row: row["hour"])
    result["fingerprint"] = fingerprint({key: result[key] for key in result if key not in {"updated_at", "fingerprint"}})
    return result


def irradiance_for_hour(source, hour):
    source = validate_solar_radiation_source(source)
    if not isinstance(hour, int) or hour < 0 or hour > 23:
        raise ValueError("Solar-radiation hour must be between 0 and 23.")
    return source["hours"][hour]["irradiance_w_m2"]


def absorbed_solar_gain_kw(source, hour, area_m2, absorptance):
    if not isinstance(area_m2, (int, float)) or area_m2 <= 0:
        raise ValueError("Solar-radiation area must be positive.")
    if not isinstance(absorptance, (int, float)) or not 0 <= absorptance <= 1:
        raise ValueError("Solar-radiation absorptance must be between 0 and 1.")
    return round(irradiance_for_hour(source, hour) * area_m2 * absorptance / 1000.0, 6)
