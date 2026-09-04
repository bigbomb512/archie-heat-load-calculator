#!/usr/bin/env python3

"""Validated, engineer-owned project/site design-condition records.

This module deliberately stores design inputs only. It does not select climate
data, infer missing values, or copy values into calculation requirements.
"""

from copy import deepcopy
from datetime import datetime, timezone


STATUSES = {"missing", "provisional", "confirmed", "not_applicable"}
SCHEMA_VERSION = 1


def empty_site_design_conditions():
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "",
        "site": {
            "project_name": empty_text_field(),
            "address": empty_text_field(),
            "location_description": empty_text_field(),
            "weather_station_reference": empty_text_field(),
            "elevation_m": empty_numeric_field(),
            "north_orientation_note": empty_text_field(),
        },
        "design_basis": {
            "name": "",
            "reference_version_or_date": "",
            "status": "missing",
            "source": "",
            "citations": [],
        },
        "summer": {
            "outdoor_dry_bulb_c": empty_numeric_field(),
            "outdoor_wet_bulb_c": empty_numeric_field(),
            "indoor_dry_bulb_c": empty_numeric_field(),
            "indoor_relative_humidity_percent": empty_numeric_field(),
            "atmospheric_pressure_kpa": empty_numeric_field(),
        },
        "winter": {
            "outdoor_dry_bulb_c": empty_numeric_field(),
            "outdoor_relative_humidity_percent": empty_numeric_field(),
            "indoor_dry_bulb_c": empty_numeric_field(),
            "indoor_relative_humidity_percent": empty_numeric_field(),
        },
    }


def empty_text_field():
    return {"value": "", "status": "missing", "source": "", "citations": []}


def empty_numeric_field():
    return {"value": None, "status": "missing", "source": "", "citations": []}


def validate_site_design_conditions(raw):
    if not isinstance(raw, dict):
        raise ValueError("Site design conditions must be a JSON object.")
    result = empty_site_design_conditions()
    for section in ("site", "design_basis", "summer", "winter"):
        if section in raw:
            if not isinstance(raw[section], dict):
                raise ValueError(f"{section.replace('_', ' ').capitalize()} must be an object.")
            result[section] = deepcopy(raw[section])

    result["site"] = validate_site(result["site"])
    result["design_basis"] = validate_design_basis(result["design_basis"])
    result["summer"] = validate_conditions(result["summer"], "summer", SUMMER_FIELDS)
    result["winter"] = validate_conditions(result["winter"], "winter", WINTER_FIELDS)
    validate_physical_consistency(result)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


SITE_FIELDS = {
    "project_name": ("Project name", "text"),
    "address": ("Site address", "text"),
    "location_description": ("Location description", "text"),
    "weather_station_reference": ("Weather-station reference", "text"),
    "elevation_m": ("Elevation", "elevation"),
    "north_orientation_note": ("North/orientation note", "text"),
}
SUMMER_FIELDS = {
    "outdoor_dry_bulb_c": ("Summer outdoor dry-bulb", "temperature"),
    "outdoor_wet_bulb_c": ("Summer outdoor wet-bulb", "temperature"),
    "indoor_dry_bulb_c": ("Summer indoor dry-bulb", "temperature"),
    "indoor_relative_humidity_percent": ("Summer indoor relative humidity", "humidity"),
    "atmospheric_pressure_kpa": ("Atmospheric pressure", "pressure"),
}
WINTER_FIELDS = {
    "outdoor_dry_bulb_c": ("Winter outdoor dry-bulb", "temperature"),
    "outdoor_relative_humidity_percent": ("Winter outdoor relative humidity", "humidity"),
    "indoor_dry_bulb_c": ("Winter indoor dry-bulb", "temperature"),
    "indoor_relative_humidity_percent": ("Winter indoor relative humidity", "humidity"),
}


def validate_site(raw):
    if not isinstance(raw, dict):
        raise ValueError("Site must be an object.")
    return validate_conditions(raw, "site", SITE_FIELDS)


def validate_conditions(raw, section, fields):
    if not isinstance(raw, dict):
        raise ValueError(f"{section.capitalize()} conditions must be an object.")
    result = {}
    for key, (label, kind) in fields.items():
        value = raw.get(key, empty_text_field() if kind == "text" else empty_numeric_field())
        result[key] = validate_field(value, label, kind)
    return result


def validate_field(raw, label, kind):
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must include value, status, source, and citations.")
    status = raw.get("status", "missing")
    if status not in STATUSES:
        raise ValueError(f"{label} has an invalid status.")
    source = clean_text(raw.get("source", ""), f"{label} source")
    citations = validate_citations(raw.get("citations", []), label)
    value = raw.get("value")
    if kind == "text":
        value = clean_text(value, label)
    else:
        value = numeric_value(value, label, kind)
    if status in {"confirmed", "provisional"}:
        if value in (None, ""):
            raise ValueError(f"{label} needs a value when {status}.")
        if not source:
            raise ValueError(f"{label} needs a source when {status}.")
    if status == "not_applicable" and value not in (None, ""):
        raise ValueError(f"{label} must be blank when not applicable.")
    return {"value": value, "status": status, "source": source, "citations": citations}


def validate_design_basis(raw):
    if not isinstance(raw, dict):
        raise ValueError("Design basis must be an object.")
    status = raw.get("status", "missing")
    if status not in STATUSES:
        raise ValueError("Design basis has an invalid status.")
    result = {
        "name": clean_text(raw.get("name", ""), "Design-basis name"),
        "reference_version_or_date": clean_text(raw.get("reference_version_or_date", ""), "Design-basis version or date"),
        "status": status,
        "source": clean_text(raw.get("source", ""), "Design-basis source"),
        "citations": validate_citations(raw.get("citations", []), "Design basis"),
    }
    if status in {"confirmed", "provisional"}:
        for key, label in (("name", "name"), ("reference_version_or_date", "version or date"), ("source", "source")):
            if not result[key]:
                raise ValueError(f"Design basis needs a {label} when {status}.")
    if status == "not_applicable":
        raise ValueError("Design basis cannot be marked not applicable.")
    return result


def clean_text(value, label):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    return value.strip()


def numeric_value(value, label, kind):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number or blank.")
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number or blank.") from error
    limits = {
        "temperature": (-100, 100),
        "humidity": (0, 100),
        "pressure": (50, 120),
        "elevation": (-1000, 10000),
    }
    low, high = limits[kind]
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return value


def validate_citations(raw, label):
    if not isinstance(raw, list):
        raise ValueError(f"{label} citations must be a list.")
    result = []
    for index, citation in enumerate(raw, start=1):
        if not isinstance(citation, dict):
            raise ValueError(f"{label} citation {index} must be an object.")
        reference = clean_text(citation.get("reference", ""), f"{label} citation {index} reference")
        page = citation.get("page")
        if page not in (None, "") and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            raise ValueError(f"{label} citation {index} page must be a positive whole number or blank.")
        excerpt = clean_text(citation.get("excerpt", ""), f"{label} citation {index} excerpt")
        if not reference and not excerpt:
            raise ValueError(f"{label} citation {index} needs a reference or excerpt.")
        result.append({"reference": reference, "page": page if page not in (None, "") else None, "excerpt": excerpt})
    return result


def validate_physical_consistency(packet):
    summer = packet["summer"]
    outdoor_db = summer["outdoor_dry_bulb_c"]["value"]
    outdoor_wb = summer["outdoor_wet_bulb_c"]["value"]
    if outdoor_db is not None and outdoor_wb is not None and outdoor_wb > outdoor_db:
        raise ValueError("Summer outdoor wet-bulb cannot exceed summer outdoor dry-bulb.")


def site_design_conditions_summary(packet):
    packet = validate_site_design_conditions(packet)
    sections = {
        "site": section_summary(packet["site"], ("project_name", "address", "location_description", "weather_station_reference")),
        "design_basis": basis_summary(packet["design_basis"]),
        "summer": section_summary(packet["summer"], tuple(SUMMER_FIELDS)),
        "winter": section_summary(packet["winter"], tuple(WINTER_FIELDS)),
    }
    missing = [item for section in sections.values() for item in section["missing"]]
    provisional = [item for section in sections.values() for item in section["provisional"]]
    if not missing and not provisional:
        status = "confirmed"
        completion_status = "confirmed"
    elif not missing:
        status = "review_required"
        completion_status = "ready_for_engineer_confirmation"
    else:
        status = "review_required"
        completion_status = "review_required"
    return {
        "status": status,
        "completion_status": completion_status,
        "final_design_blocked": status != "confirmed",
        "requires_engineer_review": bool(missing or provisional),
        "missing": missing,
        "provisional": provisional,
        "sections": sections,
    }


def section_summary(section, required_keys):
    missing, provisional = [], []
    for key in required_keys:
        field = section[key]
        label = key.replace("_", " ")
        if field["status"] in {"missing", "not_applicable"} or field["value"] in (None, "") or not field["source"]:
            missing.append(label)
        elif field["status"] == "provisional":
            provisional.append(label)
    return {"status": section_status(missing, provisional), "missing": missing, "provisional": provisional}


def basis_summary(basis):
    missing, provisional = [], []
    if basis["status"] == "missing" or not all((basis["name"], basis["reference_version_or_date"], basis["source"])):
        missing.append("design basis")
    elif basis["status"] == "provisional":
        provisional.append("design basis")
    return {"status": section_status(missing, provisional), "missing": missing, "provisional": provisional}


def section_status(missing, provisional):
    if missing:
        return "review_required"
    if provisional:
        return "ready_for_engineer_confirmation"
    return "confirmed"
