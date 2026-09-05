#!/usr/bin/env python3

"""Reviewed project envelope artifacts and cooling-input normalization.

The artifact model intentionally separates construction evidence from measured
surface instances.  It supports only steady-state opaque conduction and a
manual, reviewed solar term.  Window and geometric-shading data can be stored
for later approved methods, but cannot enter the calculation in this release.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re

from ai.site_design_conditions import validate_citations


ID = re.compile(r"^[a-z][a-z0-9_-]*$")
STATUSES = {"missing", "provisional", "confirmed", "not_applicable"}
CONSTRUCTION_KINDS = {"opaque_wall", "roof", "floor", "ceiling", "partition"}
SURFACE_KINDS = CONSTRUCTION_KINDS | {"glazing"}
ORIENTATIONS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW", "horizontal", "internal"}
BOUNDARY_METHODS = {"external", "fixed_adjacent_temperature", "outdoor_offset", "proportional_ambient_difference"}
CALCULABLE_BOUNDARY_METHODS = {"external", "fixed_adjacent_temperature"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_envelope_library():
    return {"schema_version": 1, "updated_at": "", "constructions": [], "windows": [], "shading_records": []}


def empty_envelope_model():
    return {"schema_version": 1, "updated_at": "", "active_for_calculation": False, "surfaces": []}


def validate_envelope_library(raw):
    if not isinstance(raw, dict):
        raise ValueError("Envelope library must be a JSON object.")
    constructions = validate_records(raw.get("constructions", []), "construction", validate_construction)
    windows = validate_records(raw.get("windows", []), "window", validate_window)
    shading = validate_records(raw.get("shading_records", raw.get("shading", [])), "shading record", validate_shading)
    return {"schema_version": 1, "updated_at": timestamp(), "constructions": constructions, "windows": windows, "shading_records": shading}


def validate_records(rows, label, validator):
    if not isinstance(rows, list):
        raise ValueError(f"Envelope {label}s must be a list.")
    result, seen = [], set()
    for index, raw in enumerate(rows, start=1):
        item = validator(raw, index)
        item_id = item["record_id"]
        if item_id in seen:
            raise ValueError(f"Envelope {label} ID '{item_id}' is duplicated.")
        seen.add(item_id)
        result.append(item)
    return result


def validate_construction(raw, index):
    row = common_record(raw, index, "construction")
    kind = choice(raw.get("kind", ""), CONSTRUCTION_KINDS, f"Construction {row['record_id']} kind")
    u_value = positive_number(raw.get("u_value_w_m2k"), f"Construction {row['record_id']} U-value", required=row["review_status"] == "confirmed")
    absorptivity = optional_factor(raw.get("absorptivity"), f"Construction {row['record_id']} absorptivity")
    row.update({"kind": kind, "u_value_w_m2k": u_value, "absorptivity": absorptivity})
    require_reviewed_evidence(row, f"Construction {row['record_id']}")
    return row


def validate_window(raw, index):
    row = common_record(raw, index, "window")
    row.update({
        "u_value_w_m2k": positive_number(raw.get("u_value_w_m2k"), f"Window {row['record_id']} U-value", required=False),
        "frame_fraction": optional_factor(raw.get("frame_fraction"), f"Window {row['record_id']} frame fraction"),
        "glass_area_correction": optional_factor(raw.get("glass_area_correction"), f"Window {row['record_id']} glass-area correction"),
        "internal_shading": text(raw.get("internal_shading", ""), f"Window {row['record_id']} internal shading"),
        "calculation_status": "stored_not_calculated",
    })
    return row


def validate_shading(raw, index):
    row = common_record(raw, index, "shading record")
    row.update({
        "kind": text(raw.get("kind", ""), f"Shading record {row['record_id']} kind"),
        "geometry": raw.get("geometry", {}),
        "calculation_status": "stored_not_calculated",
    })
    if not isinstance(row["geometry"], dict):
        raise ValueError(f"Shading record {row['record_id']} geometry must be an object.")
    return row


def common_record(raw, index, label):
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope {label} {index} must be an object.")
    record_id = stable_id(raw.get("record_id", raw.get(f"{label.replace(' ', '_')}_id", "")), f"Envelope {label} {index} ID")
    review_status = status(raw.get("review_status", raw.get("verification_status", "missing")), f"Envelope {label} {record_id}")
    result = {
        "record_id": record_id,
        "title": text(raw.get("title", ""), f"Envelope {label} {record_id} title"),
        "revision": positive_integer(raw.get("revision", 1), f"Envelope {label} {record_id} revision"),
        "review_status": review_status,
        "source": text(raw.get("source", ""), f"Envelope {label} {record_id} source"),
        "citations": validate_citations(raw.get("citations", []), f"Envelope {label} {record_id}"),
    }
    return result


def validate_envelope_model(raw, library=None):
    if not isinstance(raw, dict):
        raise ValueError("Envelope model must be a JSON object.")
    if not isinstance(raw.get("active_for_calculation", False), bool):
        raise ValueError("Envelope model active_for_calculation must be true or false.")
    library = validate_envelope_library(library or empty_envelope_library())
    construction_ids = {item["record_id"] for item in library["constructions"]}
    window_ids = {item["record_id"] for item in library["windows"]}
    shading_ids = {item["record_id"] for item in library["shading_records"]}
    rows = raw.get("surfaces", [])
    if not isinstance(rows, list):
        raise ValueError("Envelope model surfaces must be a list.")
    result, seen = [], set()
    for index, raw_surface in enumerate(rows, start=1):
        surface = validate_surface(raw_surface, index, construction_ids, window_ids, shading_ids)
        if surface["surface_id"] in seen:
            raise ValueError(f"Envelope surface ID '{surface['surface_id']}' is duplicated.")
        seen.add(surface["surface_id"])
        result.append(surface)
    return {"schema_version": 1, "updated_at": timestamp(), "active_for_calculation": raw.get("active_for_calculation", False), "surfaces": result}


def validate_surface(raw, index, construction_ids, window_ids, shading_ids):
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {index} must be an object.")
    surface_id = stable_id(raw.get("surface_id", ""), f"Envelope surface {index} ID")
    kind = choice(raw.get("kind", ""), SURFACE_KINDS, f"Envelope surface {surface_id} kind")
    boundary = choice(raw.get("boundary_method", ""), BOUNDARY_METHODS, f"Envelope surface {surface_id} boundary method")
    construction_id = text(raw.get("construction_id", ""), f"Envelope surface {surface_id} construction ID")
    if kind != "glazing" and construction_id not in construction_ids:
        raise ValueError(f"Envelope surface {surface_id} references unknown construction '{construction_id}'.")
    window_id = text(raw.get("window_id", ""), f"Envelope surface {surface_id} window ID")
    if window_id and window_id not in window_ids:
        raise ValueError(f"Envelope surface {surface_id} references unknown window '{window_id}'.")
    shading_ids_used = string_list(raw.get("shading_record_ids", []), f"Envelope surface {surface_id} shading record IDs")
    unknown_shading = set(shading_ids_used) - shading_ids
    if unknown_shading:
        raise ValueError(f"Envelope surface {surface_id} references unknown shading records: {', '.join(sorted(unknown_shading))}.")
    result = {
        "surface_id": surface_id,
        "owner_zone_id": stable_id(raw.get("owner_zone_id", ""), f"Envelope surface {surface_id} owner zone ID"),
        "owner_room_id": text(raw.get("owner_room_id", ""), f"Envelope surface {surface_id} owner room ID"),
        "kind": kind,
        "orientation": choice(raw.get("orientation", ""), ORIENTATIONS, f"Envelope surface {surface_id} orientation"),
        "area_m2": positive_number(raw.get("area_m2"), f"Envelope surface {surface_id} area", required=raw.get("review_status", "missing") == "confirmed"),
        "construction_id": construction_id,
        "window_id": window_id,
        "shading_record_ids": shading_ids_used,
        "boundary_method": boundary,
        "adjacent_temperature_c": optional_number(raw.get("adjacent_temperature_c"), f"Envelope surface {surface_id} adjacent temperature", -100, 100),
        "manual_solar": validate_manual_solar(raw.get("manual_solar", {}), surface_id),
        "review_status": status(raw.get("review_status", "missing"), f"Envelope surface {surface_id}"),
        "source": text(raw.get("source", ""), f"Envelope surface {surface_id} source"),
        "citations": validate_citations(raw.get("citations", []), f"Envelope surface {surface_id}"),
        "legacy_migrated": bool(raw.get("legacy_migrated", False)),
    }
    if boundary == "fixed_adjacent_temperature" and result["adjacent_temperature_c"] is None:
        raise ValueError(f"Envelope surface {surface_id} needs an adjacent temperature for fixed_adjacent_temperature.")
    return result


def validate_manual_solar(raw, surface_id):
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {surface_id} manual solar must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"Envelope surface {surface_id} manual solar enabled must be true or false.")
    result = {
        "enabled": enabled,
        "solar_design_w_m2": optional_number(raw.get("solar_design_w_m2"), f"Envelope surface {surface_id} design solar", 0, 100000),
        "solar_gain_factor": optional_factor(raw.get("solar_gain_factor"), f"Envelope surface {surface_id} solar gain factor"),
        "shading_factor": optional_factor(raw.get("shading_factor"), f"Envelope surface {surface_id} shading factor"),
        "review_status": status(raw.get("review_status", "missing"), f"Envelope surface {surface_id} manual solar"),
        "source": text(raw.get("source", ""), f"Envelope surface {surface_id} manual solar source"),
        "citations": validate_citations(raw.get("citations", []), f"Envelope surface {surface_id} manual solar"),
    }
    if enabled:
        for key in ("solar_design_w_m2", "solar_gain_factor", "shading_factor"):
            if result[key] is None:
                raise ValueError(f"Envelope surface {surface_id} manual solar needs {key.replace('_', ' ')} when enabled.")
        if not result["source"]:
            raise ValueError(f"Envelope surface {surface_id} manual solar needs a source when enabled.")
    return result


def migrate_legacy_envelope(requirements):
    """Seed provisional records from legacy zone surfaces without altering requirements."""
    requirements = deepcopy(requirements)
    constructions, surfaces, seen_constructions = [], [], set()
    for zone in requirements.get("zones", []):
        for legacy in zone.get("cooling_load", {}).get("envelope_surfaces", []):
            surface_id = legacy.get("surface_id", "").strip()
            if not surface_id:
                continue
            construction_id = f"legacy-{surface_id}-construction"
            if construction_id not in seen_constructions:
                seen_constructions.add(construction_id)
                constructions.append({"record_id": construction_id, "title": f"Legacy {surface_id} construction", "revision": 1, "review_status": "provisional", "source": legacy.get("source", "Legacy design requirements"), "citations": [], "kind": legacy.get("kind") if legacy.get("kind") in CONSTRUCTION_KINDS else "opaque_wall", "u_value_w_m2k": legacy.get("u_value_w_m2k"), "absorptivity": None})
            surfaces.append({
                "surface_id": f"legacy-{zone.get('zone_id', 'zone')}-{surface_id}", "owner_zone_id": zone.get("zone_id", ""), "owner_room_id": "",
                "kind": legacy.get("kind") if legacy.get("kind") in SURFACE_KINDS else "opaque_wall", "orientation": legacy.get("orientation") if legacy.get("orientation") in ORIENTATIONS else "horizontal",
                "area_m2": legacy.get("area_m2"), "construction_id": construction_id, "window_id": "", "shading_record_ids": [], "boundary_method": "external", "adjacent_temperature_c": None,
                "manual_solar": {"enabled": all(legacy.get(key) not in (None, "") for key in ("solar_design_w_m2", "solar_gain_factor", "shading_factor")), "solar_design_w_m2": legacy.get("solar_design_w_m2"), "solar_gain_factor": legacy.get("solar_gain_factor"), "shading_factor": legacy.get("shading_factor"), "review_status": "provisional", "source": legacy.get("source", "Legacy design requirements"), "citations": []},
                "review_status": "provisional", "source": legacy.get("source", "Legacy design requirements"), "citations": [], "legacy_migrated": True,
            })
    return validate_envelope_library({"constructions": constructions}), validate_envelope_model({"active_for_calculation": False, "surfaces": surfaces}, {"constructions": constructions})


def envelope_summary(library, model):
    library = validate_envelope_library(library)
    model = validate_envelope_model(model, library)
    included, blocked, stored = normalize_surfaces(library, model)
    return {
        "status": "ready" if model["active_for_calculation"] and not blocked else ("review_required" if blocked or not model["active_for_calculation"] else "ready"),
        "active_for_calculation": model["active_for_calculation"],
        "construction_count": len(library["constructions"]), "window_count": len(library["windows"]), "shading_record_count": len(library["shading_records"]),
        "included": included, "blocked": blocked, "stored_not_calculated": stored,
        "requires_engineer_review": bool(blocked) or not model["active_for_calculation"],
    }


def normalize_surfaces(library, model):
    """Return (eligible surface rows, blocked rows, stored-only rows)."""
    library = validate_envelope_library(library)
    model = validate_envelope_model(model, library)
    constructions = {item["record_id"]: item for item in library["constructions"]}
    included, blocked, stored = [], [], []
    for item in model["surfaces"]:
        reason = calculation_exclusion(item, constructions)
        summary = {"surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "kind": item["kind"], "reason": reason}
        if item["kind"] == "glazing" or item["window_id"] or item["shading_record_ids"]:
            summary["reason"] = "stored_not_calculated: detailed glazing and geometric shading are not implemented"
            stored.append(summary)
        elif reason:
            blocked.append(summary)
        else:
            construction = constructions[item["construction_id"]]
            solar = item["manual_solar"]
            included.append({
                "surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "kind": item["kind"], "orientation": item["orientation"],
                "area_m2": item["area_m2"], "u_value_w_m2k": construction["u_value_w_m2k"], "boundary_method": item["boundary_method"],
                "boundary_temperature_c": item["adjacent_temperature_c"] if item["boundary_method"] == "fixed_adjacent_temperature" else None,
                "construction_id": construction["record_id"], "construction_revision": construction["revision"], "source": item["source"], "citations": item["citations"],
                "verification_status": "confirmed", "solar_design_w_m2": solar["solar_design_w_m2"] if solar["enabled"] else 0.0,
                "solar_gain_factor": solar["solar_gain_factor"] if solar["enabled"] else 0.0, "shading_factor": solar["shading_factor"] if solar["enabled"] else 0.0,
                "manual_solar_source": solar["source"] if solar["enabled"] else "",
            })
    return included, blocked, stored


def calculation_exclusion(surface, constructions):
    if surface["boundary_method"] not in CALCULABLE_BOUNDARY_METHODS:
        return f"unsupported boundary method: {surface['boundary_method']}"
    if surface["review_status"] != "confirmed":
        return "surface review status is not confirmed"
    construction = constructions.get(surface["construction_id"])
    if not construction:
        return "construction record is missing"
    if construction["review_status"] != "confirmed":
        return "construction review status is not confirmed"
    if construction["u_value_w_m2k"] is None:
        return "construction U-value is missing"
    if surface["area_m2"] is None:
        return "surface area is missing"
    solar = surface["manual_solar"]
    if solar["enabled"] and solar["review_status"] != "confirmed":
        return "manual solar review status is not confirmed"
    return ""


def apply_reviewed_envelope_to_requirements(requirements, library, model):
    """Return a copy with reviewed envelope rows substituted by zone.

    The reviewed artifact is authoritative only when active.  Legacy rows are
    left untouched otherwise, making migration non-disruptive.
    """
    result = deepcopy(requirements)
    model = validate_envelope_model(model, library)
    if not model["active_for_calculation"]:
        return result, {"source": "legacy", "included": [], "blocked": [], "stored_not_calculated": []}
    included, blocked, stored = normalize_surfaces(library, model)
    zone_ids = {zone.get("zone_id", "") for zone in result.get("zones", [])}
    unknown_owner = [surface for surface in included if surface["owner_zone_id"] not in zone_ids]
    if unknown_owner:
        included = [surface for surface in included if surface["owner_zone_id"] in zone_ids]
        blocked.extend({"surface_id": surface["surface_id"], "owner_zone_id": surface["owner_zone_id"], "kind": surface["kind"], "reason": "owner zone is absent from current design requirements"} for surface in unknown_owner)
    by_zone = {}
    for surface in included:
        by_zone.setdefault(surface["owner_zone_id"], []).append(surface)
    for zone in result.get("zones", []):
        zone["cooling_load"]["envelope_surfaces"] = by_zone.get(zone["zone_id"], [])
        zone["cooling_load"]["envelope_not_applicable"] = not bool(by_zone.get(zone["zone_id"]))
    return result, {"source": "reviewed_envelope_model", "included": included, "blocked": blocked, "stored_not_calculated": stored}


def apply_reviewed_envelope_to_hourly_model(hourly_model, library, model):
    result = deepcopy(hourly_model)
    model = validate_envelope_model(model, library)
    if not model["active_for_calculation"]:
        return result
    included, _, _ = normalize_surfaces(library, model)
    by_zone = {}
    for surface in included:
        by_zone.setdefault(surface["owner_zone_id"], []).append(surface)
    for room in result.get("rooms", []):
        room["cooling_load"]["envelope_surfaces"] = by_zone.get(room["zone_id"], [])
        room["cooling_load"]["envelope_not_applicable"] = not bool(by_zone.get(room["zone_id"]))
    return result


def stable_id(value, label):
    value = text(value, label)
    if not ID.fullmatch(value):
        raise ValueError(f"{label} must start with a letter and use lowercase letters, numbers, hyphens, or underscores.")
    return value


def choice(value, allowed, label):
    if value not in allowed:
        raise ValueError(f"Invalid {label.lower()}.")
    return value


def status(value, label):
    return choice(value, STATUSES, f"{label} review status")


def text(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    return value.strip()


def string_list(value, label):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of IDs.")
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def optional_number(value, label, low=None, high=None):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number or blank.")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number or blank.") from error
    if low is not None and result < low or high is not None and result > high:
        raise ValueError(f"{label} is outside the accepted range.")
    return result


def positive_number(value, label, required=True):
    result = optional_number(value, label, 0)
    if result is None and required:
        raise ValueError(f"{label} is required.")
    if result is not None and result <= 0:
        raise ValueError(f"{label} must be positive.")
    return result


def positive_integer(value, label):
    result = positive_number(value, label)
    if not result.is_integer():
        raise ValueError(f"{label} must be a whole number.")
    return int(result)


def optional_factor(value, label):
    result = optional_number(value, label, 0, 1)
    return result


def require_reviewed_evidence(record, label):
    if record["review_status"] == "confirmed" and not record["source"]:
        raise ValueError(f"{label} needs a source when confirmed.")
