#!/usr/bin/env python3

"""Reviewed project envelope artifacts and cooling-input normalization.

The artifact model intentionally separates construction evidence from measured
surface instances. It supports steady-state opaque conduction, approved
reviewed glazing with a manual hourly solar basis, and fixed-temperature
partitions. Geometric shading requires its separate approved method gate; all
other stored records remain excluded.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re

from ai.site_design_conditions import validate_citations
from ai.glazing_calculation import assess_glazing_eligibility, opening_area
from ai.glazing_gate import empty_glazing_method_gate, gate_is_approved, validate_glazing_method_gate
from ai.shading_gate import empty_shading_method_gate, gate_is_approved as shading_gate_is_approved, validate_shading_method_gate
from ai.shading_geometry import assess_geometric_shading
from ai.envelope_method_gates import empty_ground_contact_method_gate, ground_contact_gate_is_approved, validate_ground_contact_method_gate
from ai.thermal_mass import validate_rc_surface
from ai.room_coupling import validate_coupling_record


ID = re.compile(r"^[a-z][a-z0-9_-]*$")
STATUSES = {"missing", "provisional", "confirmed", "not_applicable"}
CONSTRUCTION_KINDS = {"opaque_wall", "roof", "floor", "ceiling", "partition"}
SURFACE_KINDS = CONSTRUCTION_KINDS | {"glazing"}
ORIENTATIONS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW", "horizontal", "internal"}
BOUNDARY_METHODS = {"external", "fixed_adjacent_temperature", "ground_contact", "room_to_room_dynamic", "outdoor_offset", "proportional_ambient_difference"}
CALCULABLE_BOUNDARY_METHODS = {"external", "fixed_adjacent_temperature", "ground_contact", "room_to_room_dynamic"}
OPAQUE_AREA_BASES = {"legacy_net_opaque", "net_opaque", "gross_with_confirmed_openings"}
OPENING_COVERAGE_STATUSES = {"missing", "proposed", "confirmed"}


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
        "u_value_basis": choice(raw.get("u_value_basis", "") or "", {"", "overall_window"}, f"Window {row['record_id']} U-value basis"),
        "shgc": optional_factor(raw.get("shgc"), f"Window {row['record_id']} SHGC"),
        "solar_transmission_factor": optional_factor(raw.get("solar_transmission_factor"), f"Window {row['record_id']} solar-transmission factor"),
        "frame_fraction": optional_factor(raw.get("frame_fraction"), f"Window {row['record_id']} frame fraction"),
        "glass_area_correction": optional_factor(raw.get("glass_area_correction"), f"Window {row['record_id']} glass-area correction"),
        "internal_shading_factor": optional_factor(raw.get("internal_shading_factor"), f"Window {row['record_id']} internal shading factor"),
        "internal_shading": text(raw.get("internal_shading", ""), f"Window {row['record_id']} internal shading"),
        "geometry": raw.get("geometry", {}),
        "calculation_status": "reviewed_glazing_candidate",
    })
    if not isinstance(row["geometry"], dict):
        raise ValueError(f"Window {row['record_id']} geometry must be an object.")
    return row


def validate_shading(raw, index):
    row = common_record(raw, index, "shading record")
    kind = choice(raw.get("kind", "reference"), {"reference", "geometric_v1"}, f"Shading record {row['record_id']} kind")
    positions = raw.get("hourly_sun_positions", [])
    if positions and not isinstance(positions, list):
        raise ValueError(f"Shading record {row['record_id']} hourly sun positions must be a list.")
    geometry = raw.get("geometry", {})
    if not isinstance(geometry, dict):
        raise ValueError(f"Shading record {row['record_id']} geometry must be an object.")
    normalized_geometry = {
        key: optional_number(geometry.get(key), f"Shading record {row['record_id']} {key.replace('_', ' ')}", 0, 100)
        for key in ("overhang_depth_m", "left_fin_depth_m", "right_fin_depth_m", "reveal_depth_m")
    }
    normalized_geometry["obstruction_altitude_deg"] = optional_number(
        geometry.get("obstruction_altitude_deg"), f"Shading record {row['record_id']} obstruction altitude", 0, 90
    )
    normalized_positions = []
    for position in positions:
        if not isinstance(position, dict):
            raise ValueError(f"Shading record {row['record_id']} hourly sun positions must contain objects.")
        hour = positive_integer(int(position.get("hour", -1)) + 1, f"Shading record {row['record_id']} sun-position hour") - 1
        azimuth = optional_number(position.get("azimuth_deg"), f"Shading record {row['record_id']} sun-position azimuth", 0, 359.999999)
        altitude = optional_number(position.get("altitude_deg"), f"Shading record {row['record_id']} sun-position altitude", -90, 90)
        if azimuth is None or altitude is None:
            raise ValueError(f"Shading record {row['record_id']} sun positions require azimuth and altitude.")
        normalized_positions.append({"hour": hour, "azimuth_deg": azimuth, "altitude_deg": altitude})
    row.update({
        "kind": kind,
        "geometry": normalized_geometry,
        "hourly_sun_positions": normalized_positions,
        "calculation_status": "stored_not_calculated",
    })
    if kind == "geometric_v1" and row["review_status"] == "confirmed":
        # This validates the cited sun-vector structure without activating it.
        issues = assess_geometric_shading({"orientation": "N", "opening_mapping_status": "confirmed", "review_status": "confirmed", "opening_width_m": 1, "opening_height_m": 1}, row)
        issues = [issue for issue in issues if issue not in {"a cardinal glazing orientation is required", "confirmed opening mapping is required", "confirmed glazing surface review is required"}]
        if issues:
            raise ValueError(f"Shading record {row['record_id']} is incomplete: {'; '.join(issues)}.")
    return row


def common_record(raw, index, label):
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope {label} {index} must be an object.")
    record_id = stable_id(raw.get("record_id", raw.get(f"{label.replace(' ', '_')}_id", "")), f"Envelope {label} {index} ID")
    review_status = status(raw.get("review_status", raw.get("verification_status", "missing")), f"Envelope {label} {record_id}")
    result = {
        "record_id": record_id,
        "bridge_provenance": deepcopy(raw.get("bridge_provenance", {})),
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
        "bridge_provenance": deepcopy(raw.get("bridge_provenance", {})),
        "owner_zone_id": stable_id(raw.get("owner_zone_id", ""), f"Envelope surface {surface_id} owner zone ID"),
        "owner_room_id": text(raw.get("owner_room_id", ""), f"Envelope surface {surface_id} owner room ID"),
        "opening_mapping_status": choice(raw.get("opening_mapping_status", "missing"), {"missing", "proposed", "confirmed", "conflict"}, f"Envelope surface {surface_id} opening mapping status"),
        "kind": kind,
        "orientation": choice(raw.get("orientation", ""), ORIENTATIONS, f"Envelope surface {surface_id} orientation"),
        "area_m2": positive_number(raw.get("area_m2"), f"Envelope surface {surface_id} area", required=kind != "glazing" and raw.get("review_status", "missing") == "confirmed"),
        "area_basis": choice(raw.get("area_basis", "legacy_net_opaque"), OPAQUE_AREA_BASES, f"Envelope surface {surface_id} area basis"),
        "linked_opening_surface_ids": string_list(raw.get("linked_opening_surface_ids", []), f"Envelope surface {surface_id} linked opening surface IDs"),
        "opening_coverage_status": choice(raw.get("opening_coverage_status", "missing"), OPENING_COVERAGE_STATUSES, f"Envelope surface {surface_id} opening coverage status"),
        "construction_id": construction_id,
        "window_id": window_id,
        "opening_tag": text(raw.get("opening_tag", ""), f"Envelope surface {surface_id} opening tag"),
        "opening_width_m": positive_number(raw.get("opening_width_m"), f"Envelope surface {surface_id} opening width", required=False),
        "opening_height_m": positive_number(raw.get("opening_height_m"), f"Envelope surface {surface_id} opening height", required=False),
        "opening_quantity": optional_positive_integer(raw.get("opening_quantity"), f"Envelope surface {surface_id} opening quantity"),
        "explicit_opening_area_m2": positive_number(raw.get("explicit_opening_area_m2"), f"Envelope surface {surface_id} explicit opening area", required=False),
        "explicit_glass_area_m2": positive_number(raw.get("explicit_glass_area_m2"), f"Envelope surface {surface_id} explicit glass area", required=False),
        "shading_record_ids": shading_ids_used,
        "boundary_method": boundary,
        "boundary_temperature_c": optional_number(raw.get("boundary_temperature_c"), f"Envelope surface {surface_id} boundary temperature", -100, 100),
        "adjacent_temperature_c": optional_number(raw.get("adjacent_temperature_c"), f"Envelope surface {surface_id} adjacent temperature", -100, 100),
        "adjacent_boundary_id": text(raw.get("adjacent_boundary_id", ""), f"Envelope surface {surface_id} adjacent boundary ID"),
        "adjacent_temperature_source": text(raw.get("adjacent_temperature_source", ""), f"Envelope surface {surface_id} adjacent temperature source"),
        "adjacent_temperature_citations": validate_citations(raw.get("adjacent_temperature_citations", []), f"Envelope surface {surface_id} adjacent temperature"),
        "ground_temperature_c": optional_number(raw.get("ground_temperature_c"), f"Envelope surface {surface_id} ground temperature", -100, 100),
        "ground_temperature_source": text(raw.get("ground_temperature_source", ""), f"Envelope surface {surface_id} ground temperature source"),
        "ground_temperature_citations": validate_citations(raw.get("ground_temperature_citations", []), f"Envelope surface {surface_id} ground temperature"),
        "boundary_temperature_profile": validate_temperature_profile(raw.get("boundary_temperature_profile", {}), surface_id),
        "dynamic_thermal_mass": validate_dynamic_thermal_mass_record({
            **(raw.get("dynamic_thermal_mass") or {}),
            "source": (raw.get("dynamic_thermal_mass") or {}).get("source") or raw.get("source", ""),
            "citations": (raw.get("dynamic_thermal_mass") or {}).get("citations") or raw.get("citations", []),
            "area_m2": (raw.get("dynamic_thermal_mass") or {}).get("area_m2", raw.get("area_m2")),
        }, surface_id),
        "room_coupling": validate_room_coupling_record(raw.get("room_coupling", {}), surface_id, kind, owner_room_id=text(raw.get("owner_room_id", ""), f"Envelope surface {surface_id} owner room ID")),
        "solar_radiation_source_id": text(raw.get("solar_radiation_source_id", ""), f"Envelope surface {surface_id} solar-radiation source ID"),
        "manual_solar": validate_manual_solar(raw.get("manual_solar", {}), surface_id, is_glazing=kind == "glazing"),
        "review_status": status(raw.get("review_status", "missing"), f"Envelope surface {surface_id}"),
        "source": text(raw.get("source", ""), f"Envelope surface {surface_id} source"),
        "citations": validate_citations(raw.get("citations", []), f"Envelope surface {surface_id}"),
        "legacy_migrated": bool(raw.get("legacy_migrated", False)),
    }
    if boundary == "fixed_adjacent_temperature" and result["adjacent_temperature_c"] is None and not result["boundary_temperature_profile"]["values"]:
        raise ValueError(f"Envelope surface {surface_id} needs an adjacent temperature for fixed_adjacent_temperature.")
    if boundary == "ground_contact" and result["ground_temperature_c"] is None and not result["boundary_temperature_profile"]["values"]:
        raise ValueError(f"Envelope surface {surface_id} needs a reviewed ground temperature or 24-hour profile for ground_contact.")
    return result


def validate_dynamic_thermal_mass_record(raw, surface_id):
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {surface_id} dynamic thermal mass must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"Envelope surface {surface_id} dynamic thermal mass enabled must be true or false.")
    if not enabled:
        return {"enabled": False}
    surface = validate_rc_surface({"surface_id": surface_id, **raw})
    surface.pop("surface_id", None)
    surface["enabled"] = True
    initial = raw.get("initial_state_temperature_c")
    if not isinstance(initial, (int, float)) or isinstance(initial, bool):
        raise ValueError(f"Envelope surface {surface_id} dynamic thermal mass requires initial_state_temperature_c.")
    surface["initial_state_temperature_c"] = float(initial)
    return surface


def validate_room_coupling_record(raw, surface_id, kind, owner_room_id=None):
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {surface_id} room coupling must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"Envelope surface {surface_id} room coupling enabled must be true or false.")
    if not enabled:
        return {"enabled": False}
    if kind != "partition":
        raise ValueError(f"Envelope surface {surface_id} room coupling is only valid for partitions.")
    checked = validate_coupling_record({"surface_id": surface_id, **raw})
    if owner_room_id and checked["owner_room_id"] != owner_room_id:
        raise ValueError(f"Envelope surface {surface_id} coupling owner does not match surface owner room.")
    checked.pop("surface_id", None)
    checked["enabled"] = True
    return checked


def validate_temperature_profile(raw, surface_id):
    if raw in (None, ""):
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {surface_id} boundary temperature profile must be an object.")
    values = raw.get("values", [])
    if not isinstance(values, list) or values and len(values) != 24:
        raise ValueError(f"Envelope surface {surface_id} boundary temperature profile needs 24 values.")
    checked = [optional_number(value, f"Envelope surface {surface_id} boundary profile", -100, 100) for value in values]
    return {"values": checked, "status": status(raw.get("status", "missing"), f"Envelope surface {surface_id} boundary profile"), "source": text(raw.get("source", ""), f"Envelope surface {surface_id} boundary profile source"), "citations": validate_citations(raw.get("citations", []), f"Envelope surface {surface_id} boundary profile")}


def validate_manual_solar(raw, surface_id, *, is_glazing=False):
    if not isinstance(raw, dict):
        raise ValueError(f"Envelope surface {surface_id} manual solar must be an object.")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"Envelope surface {surface_id} manual solar enabled must be true or false.")
    result = {
        "enabled": enabled,
        "solar_design_w_m2": optional_number(raw.get("solar_design_w_m2"), f"Envelope surface {surface_id} design solar", 0, 100000),
        "incident_solar_w_m2": optional_number(raw.get("incident_solar_w_m2"), f"Envelope surface {surface_id} incident solar", 0, 100000),
        "solar_gain_factor": optional_factor(raw.get("solar_gain_factor"), f"Envelope surface {surface_id} solar gain factor"),
        "shading_factor": optional_factor(raw.get("shading_factor"), f"Envelope surface {surface_id} shading factor"),
        "external_shading_factor": optional_factor(raw.get("external_shading_factor"), f"Envelope surface {surface_id} external shading factor"),
        "review_status": status(raw.get("review_status", "missing"), f"Envelope surface {surface_id} manual solar"),
        "source": text(raw.get("source", ""), f"Envelope surface {surface_id} manual solar source"),
        "citations": validate_citations(raw.get("citations", []), f"Envelope surface {surface_id} manual solar"),
    }
    if enabled:
        required = ("solar_design_w_m2", "solar_gain_factor", "shading_factor")
        if is_glazing:
            if result["incident_solar_w_m2"] is None and result["solar_design_w_m2"] is None:
                raise ValueError(f"Envelope surface {surface_id} manual solar needs incident solar when enabled.")
            if result["external_shading_factor"] is None and result["shading_factor"] is None:
                raise ValueError(f"Envelope surface {surface_id} manual solar needs external shading factor when enabled.")
            required = ()
        for key in required:
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


def envelope_summary(library, model, glazing_gate=None, shading_gate=None, ground_contact_gate=None):
    library = validate_envelope_library(library)
    model = validate_envelope_model(model, library)
    ground_contact_gate = validate_ground_contact_method_gate(ground_contact_gate or empty_ground_contact_method_gate())
    included, blocked, stored = normalize_surfaces(library, model, ground_contact_gate)
    glazing_included, glazing_blocked, glazing_stored = normalize_glazing_surfaces(library, model, glazing_gate, shading_gate)
    needs_review = bool(blocked or glazing_blocked or glazing_stored or not model["active_for_calculation"])
    return {
        "status": "review_required" if needs_review else "ready",
        "active_for_calculation": model["active_for_calculation"],
        "construction_count": len(library["constructions"]), "window_count": len(library["windows"]), "shading_record_count": len(library["shading_records"]),
        "included": included + glazing_included, "blocked": blocked,
        "stored_not_calculated": [row for row in stored if row.get("kind") != "glazing"],
        "draft_only": glazing_blocked + glazing_stored,
        "requires_engineer_review": bool(blocked or glazing_blocked or glazing_stored) or not model["active_for_calculation"],
        "ground_contact_method": {
            "method_id": ground_contact_gate["method_id"],
            "status": "approved" if ground_contact_gate_is_approved(ground_contact_gate) else "placeholder",
            "calculation_enabled": ground_contact_gate_is_approved(ground_contact_gate),
        },
    }


def normalize_surfaces(library, model, ground_contact_gate=None):
    """Normalize opaque/partition/ground rows for the hourly envelope path.

    Glazing rows are intentionally returned as stored-only here and normalized
    by :func:`normalize_glazing_surfaces`, where the separate glazing and
    shading method gates can be applied without changing legacy opaque rules.
    """
    library = validate_envelope_library(library)
    model = validate_envelope_model(model, library)
    constructions = {item["record_id"]: item for item in library["constructions"]}
    surfaces = {item["surface_id"]: item for item in model["surfaces"]}
    included, blocked, stored, partition_boundaries = [], [], [], set()
    ground_contact_gate = validate_ground_contact_method_gate(ground_contact_gate or empty_ground_contact_method_gate())
    for item in model["surfaces"]:
        if item["kind"] == "glazing" or item["window_id"] or item["shading_record_ids"]:
            stored.append({"surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "kind": item["kind"], "reason": "stored_not_calculated: detailed glazing is evaluated by the separately gated glazing/shading path"})
            continue
        reason = calculation_exclusion(item, constructions, ground_contact_gate)
        summary = {"surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "kind": item["kind"], "reason": reason}
        if reason:
            blocked.append(summary)
        else:
            net_area, area_reason, derivation = resolve_opaque_area(item, surfaces)
            if area_reason:
                summary["reason"] = area_reason
                blocked.append(summary)
                continue
            construction = constructions[item["construction_id"]]
            if item["kind"] == "partition":
                boundary_id = item["adjacent_boundary_id"] or item.get("room_coupling", {}).get("coupling_id", "")
                if not boundary_id:
                    summary["reason"] = "partition requires a stable adjacent boundary or coupling ID"
                    blocked.append(summary)
                    continue
                if boundary_id in partition_boundaries:
                    summary["reason"] = "adjacent boundary or coupling ID is already owned by another partition surface"
                    blocked.append(summary)
                    continue
                partition_boundaries.add(boundary_id)
            solar = item["manual_solar"]
            included.append({
                "surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "kind": item["kind"], "orientation": item["orientation"],
                "area_m2": net_area, "u_value_w_m2k": construction["u_value_w_m2k"], "boundary_method": item["boundary_method"],
                "boundary_temperature_c": item["adjacent_temperature_c"] if item["boundary_method"] == "fixed_adjacent_temperature" else item["ground_temperature_c"] if item["boundary_method"] == "ground_contact" else None,
                "boundary_temperature_profile": deepcopy(item["boundary_temperature_profile"]),
                "dynamic_thermal_mass": deepcopy(item["dynamic_thermal_mass"]),
                "room_coupling": deepcopy(item["room_coupling"]),
                "solar_radiation_source_id": item["solar_radiation_source_id"],
                "adjacent_boundary_id": item["adjacent_boundary_id"], "adjacent_temperature_source": item["adjacent_temperature_source"],
                "adjacent_temperature_citations": deepcopy(item["adjacent_temperature_citations"]), "owner_room_id": item["owner_room_id"],
                "construction_id": construction["record_id"], "construction_revision": construction["revision"], "source": item["source"], "citations": item["citations"],
                "verification_status": "confirmed", "solar_design_w_m2": solar["solar_design_w_m2"] if solar["enabled"] else 0.0,
                "solar_gain_factor": solar["solar_gain_factor"] if solar["enabled"] else 0.0, "shading_factor": solar["shading_factor"] if solar["enabled"] else 0.0,
                "manual_solar_source": solar["source"] if solar["enabled"] else "", "area_derivation": derivation,
            })
    return included, blocked, stored


def resolve_opaque_area(surface, surfaces):
    """Return the eligible net opaque area; never silently subtract openings."""
    if surface["area_m2"] is None:
        return None, "surface area is missing", {}
    if surface["area_basis"] in {"legacy_net_opaque", "net_opaque"}:
        return surface["area_m2"], "", {"basis": surface["area_basis"]}
    if surface["opening_coverage_status"] != "confirmed":
        return None, "gross opaque surface requires confirmed complete opening coverage", {}
    linked = surface["linked_opening_surface_ids"]
    if not linked:
        return None, "gross opaque surface requires linked confirmed openings", {}
    openings, missing = [], []
    for opening_id in linked:
        opening = surfaces.get(opening_id)
        if not opening or opening["kind"] != "glazing" or opening["opening_mapping_status"] != "confirmed" or opening["review_status"] != "confirmed":
            missing.append(opening_id)
            continue
        area = opening_area_for_surface(opening)
        if area is None:
            missing.append(opening_id)
        else:
            openings.append({"surface_id": opening_id, "opening_area_m2": area})
    if missing:
        return None, "gross opaque surface has incomplete linked opening geometry: " + ", ".join(sorted(missing)), {}
    net = round(surface["area_m2"] - sum(row["opening_area_m2"] for row in openings), 6)
    if net <= 0:
        return None, "linked opening area must be less than gross opaque surface area", {}
    return net, "", {"basis": "gross_minus_confirmed_openings", "gross_area_m2": surface["area_m2"], "openings": openings}


def opening_area_for_surface(surface):
    if surface.get("explicit_opening_area_m2") is not None:
        return surface["explicit_opening_area_m2"]
    if all(surface.get(key) is not None for key in ("opening_width_m", "opening_height_m", "opening_quantity")):
        try:
            return opening_area(surface["opening_width_m"], surface["opening_height_m"], surface["opening_quantity"])
        except ValueError:
            return None
    return None


def normalize_glazing_surfaces(library, model, glazing_gate=None, shading_gate=None):
    """Return reviewed glazing rows separately from opaque envelope rows.

    Keeping this path separate prevents a window from being silently treated
    as an opaque wall and makes the gate, opening ownership, and solar basis
    visible in the report audit.
    """
    library = validate_envelope_library(library)
    model = validate_envelope_model(model, library)
    gate = validate_glazing_method_gate(glazing_gate or empty_glazing_method_gate())
    shading_gate = validate_shading_method_gate(shading_gate or empty_shading_method_gate())
    windows = {item["record_id"]: item for item in library["windows"]}
    shading_records = {item["record_id"]: item for item in library["shading_records"]}
    included, blocked, stored = [], [], []
    for item in model["surfaces"]:
        if item["kind"] != "glazing":
            continue
        summary = {"surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "owner_room_id": item["owner_room_id"], "kind": "glazing"}
        if not gate_is_approved(gate):
            summary["reason"] = "stored_not_calculated: glazing method gate is not approved"
            stored.append(summary)
            continue
        window = windows.get(item["window_id"])
        manual = item["manual_solar"]
        # Indoor/outdoor temperatures are supplied by the hourly room/scenario
        # adapter. This gate validates the reviewed geometry/property record.
        issues = assess_glazing_eligibility(item, window, manual, indoor_temperature_c=0)
        if window and window.get("u_value_basis") != "overall_window":
            issues.append("overall-window U-value basis is required")
        if item["boundary_method"] == "fixed_adjacent_temperature" and item.get("adjacent_temperature_c") is None:
            issues.append("reviewed adjacent boundary temperature is missing")
        if issues:
            summary["reason"] = "; ".join(dict.fromkeys(issues))
            blocked.append(summary)
            continue
        geometric_shading = None
        shading_warnings = []
        if item["shading_record_ids"]:
            if len(item["shading_record_ids"]) != 1:
                shading_warnings.append("manual external shading remains active because geometric shading needs exactly one linked record")
            elif not shading_gate_is_approved(shading_gate):
                shading_warnings.append("manual external shading remains active because the geometric shading method gate is not approved")
            else:
                candidate = shading_records.get(item["shading_record_ids"][0])
                if not candidate or candidate.get("kind") != "geometric_v1":
                    shading_warnings.append("manual external shading remains active because the linked geometric shading record is unavailable")
                else:
                    geometry_issues = assess_geometric_shading(item, candidate)
                    if geometry_issues:
                        shading_warnings.append("manual external shading remains active: " + "; ".join(geometry_issues))
                    else:
                        geometric_shading = deepcopy(candidate)
        included.append({
            "surface_id": item["surface_id"], "owner_zone_id": item["owner_zone_id"], "owner_room_id": item["owner_room_id"],
            "orientation": item["orientation"], "boundary_method": item["boundary_method"],
            "boundary_temperature_c": item["adjacent_temperature_c"] if item["boundary_method"] == "fixed_adjacent_temperature" else None,
            "adjacent_temperature_c": item["adjacent_temperature_c"], "review_status": "confirmed",
            "opening_mapping_status": item["opening_mapping_status"], "opening_width_m": item["opening_width_m"],
            "opening_height_m": item["opening_height_m"], "opening_quantity": item["opening_quantity"],
            "explicit_opening_area_m2": item["explicit_opening_area_m2"], "explicit_glass_area_m2": item["explicit_glass_area_m2"],
            "window": deepcopy(window), "manual_solar": deepcopy(manual), "source": item["source"], "citations": deepcopy(item["citations"]),
            "verification_status": "confirmed", "method_id": gate["method_id"], "gate_updated_at": gate.get("updated_at", ""),
            "geometric_shading": geometric_shading, "shading_warnings": shading_warnings,
        })
    return included, blocked, stored


def calculation_exclusion(surface, constructions, ground_contact_gate=None):
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
    if surface["kind"] == "partition" and surface.get("room_coupling", {}).get("enabled"):
        if surface["boundary_method"] != "room_to_room_dynamic":
            return "dynamic room coupling requires room_to_room_dynamic boundary method"
        coupling = surface["room_coupling"]
        if coupling.get("owner_room_id") != surface.get("owner_room_id"):
            return "dynamic room coupling owner does not match surface owner"
        if not coupling.get("adjacent_room_id"):
            return "dynamic room coupling requires an adjacent room"
    elif surface["kind"] == "partition":
        if not surface["owner_room_id"]:
            return "partition requires one owning room"
        if surface["boundary_method"] != "fixed_adjacent_temperature":
            return "partition V1 requires fixed adjacent temperature boundary"
        profile = surface.get("boundary_temperature_profile") or {}
        if not surface["adjacent_temperature_source"] and not profile.get("source"):
            return "partition requires a cited adjacent temperature basis"
        if not surface["adjacent_temperature_citations"] and not profile.get("citations"):
            return "partition requires a cited adjacent temperature basis"
    if surface["boundary_method"] == "ground_contact":
        if not ground_contact_gate_is_approved(ground_contact_gate or {}):
            return "ground-contact method gate is not approved"
        if surface.get("ground_temperature_c") is None and not surface.get("boundary_temperature_profile", {}).get("values"):
            return "ground-contact temperature basis is missing"
        if not surface.get("ground_temperature_source") and not surface.get("boundary_temperature_profile", {}).get("source"):
            return "ground-contact temperature source is missing"
        if not surface.get("ground_temperature_citations") and not surface.get("boundary_temperature_profile", {}).get("citations"):
            return "ground-contact temperature citation is missing"
    solar = surface["manual_solar"]
    if solar["enabled"] and solar["review_status"] != "confirmed":
        return "manual solar review status is not confirmed"
    return ""


def apply_reviewed_envelope_to_requirements(requirements, library, model, glazing_gate=None, shading_gate=None, ground_contact_gate=None):
    """Return a copy with reviewed envelope rows substituted by zone.

    The reviewed artifact is authoritative only when active.  Legacy rows are
    left untouched otherwise, making migration non-disruptive.
    """
    result = deepcopy(requirements)
    model = validate_envelope_model(model, library)
    if not model["active_for_calculation"]:
        return result, {"source": "legacy", "included": [], "blocked": [], "stored_not_calculated": [], "draft_only": []}
    included, blocked, stored = normalize_surfaces(library, model, ground_contact_gate)
    glazing_included, glazing_blocked, glazing_stored = normalize_glazing_surfaces(library, model, glazing_gate, shading_gate)
    zone_ids = {zone.get("zone_id", "") for zone in result.get("zones", [])}
    unknown_owner = [surface for surface in included if surface["owner_zone_id"] not in zone_ids]
    if unknown_owner:
        included = [surface for surface in included if surface["owner_zone_id"] in zone_ids]
        blocked.extend({"surface_id": surface["surface_id"], "owner_zone_id": surface["owner_zone_id"], "kind": surface["kind"], "reason": "owner zone is absent from current design requirements"} for surface in unknown_owner)
    by_zone, partitions_by_room = {}, {}
    for surface in included:
        if surface["kind"] == "partition":
            partitions_by_room.setdefault(surface["owner_room_id"], []).append(surface)
        else:
            by_zone.setdefault(surface["owner_zone_id"], []).append(surface)
    for zone in result.get("zones", []):
        zone["cooling_load"]["envelope_surfaces"] = by_zone.get(zone["zone_id"], [])
        zone["cooling_load"]["envelope_not_applicable"] = not bool(by_zone.get(zone["zone_id"]))
    return result, {
        "source": "reviewed_envelope_model", "included": included + glazing_included,
        "blocked": blocked,
        "stored_not_calculated": [row for row in stored if row.get("kind") != "glazing"],
        "draft_only": glazing_blocked + glazing_stored,
        "opaque_included": included, "glazing_included": glazing_included,
    }


def apply_reviewed_envelope_to_hourly_model(hourly_model, library, model, glazing_gate=None, shading_gate=None, ground_contact_gate=None):
    result = deepcopy(hourly_model)
    model = validate_envelope_model(model, library)
    if not model["active_for_calculation"]:
        return result
    included, _, _ = normalize_surfaces(library, model, ground_contact_gate)
    glazing, _, _ = normalize_glazing_surfaces(library, model, glazing_gate, shading_gate)
    by_zone, partitions_by_room = {}, {}
    for surface in included:
        if surface["kind"] == "partition":
            partitions_by_room.setdefault(surface["owner_room_id"], []).append(surface)
        else:
            by_zone.setdefault(surface["owner_zone_id"], []).append(surface)
    rooms = {room.get("room_id", ""): room for room in result.get("rooms", [])}
    glazing_by_room = {}
    for item in glazing:
        owner = item["owner_room_id"]
        room = rooms.get(owner)
        if room is not None and room.get("zone_id") == item["owner_zone_id"]:
            glazing_by_room.setdefault(owner, []).append(item)
    for room in result.get("rooms", []):
        room["cooling_load"]["envelope_surfaces"] = by_zone.get(room["zone_id"], []) + partitions_by_room.get(room["room_id"], [])
        room["cooling_load"]["envelope_not_applicable"] = not bool(room["cooling_load"]["envelope_surfaces"])
        room["cooling_load"]["glazing_surfaces"] = glazing_by_room.get(room["room_id"], [])
        known_surface_ids = {
            surface["surface_id"] for surface in room["cooling_load"]["envelope_surfaces"]
        } | {surface["surface_id"] for surface in room["cooling_load"]["glazing_surfaces"]}
        assignments = room.get("schedule_assignments", {}).get("solar", {})
        if isinstance(assignments, dict):
            room["schedule_assignments"]["solar"] = {
                surface_id: schedule_id for surface_id, schedule_id in assignments.items()
                if surface_id in known_surface_ids
            }
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


def optional_positive_integer(value, label):
    if value in (None, ""):
        return None
    return positive_integer(value, label)


def optional_factor(value, label):
    result = optional_number(value, label, 0, 1)
    return result


def require_reviewed_evidence(record, label):
    if record["review_status"] == "confirmed" and not record["source"]:
        raise ValueError(f"{label} needs a source when confirmed.")
