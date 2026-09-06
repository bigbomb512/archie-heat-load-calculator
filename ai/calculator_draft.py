#!/usr/bin/env python3

"""Evidence-backed proposals for the authoritative hourly cooling artifacts.

This module deliberately does not calculate a load or infer missing engineering
inputs.  It turns the existing reviewed drawing evidence into a project-local
review queue.  Only an engineer's explicit accept/edit decision can create a
floor, zone, room, or complete schedule in the calculator artifacts.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re

from ai.building_evidence import slug
from ai.envelope import empty_envelope_library, empty_envelope_model, validate_envelope_library, validate_envelope_model
from ai.hourly_loads import (
    DAY_TYPES,
    default_room_components,
    empty_hourly_load_model,
    empty_schedule_library,
    validate_hourly_load_model,
    validate_schedule_library,
)


DECISIONS = {"accept", "edit", "reject", "needs_evidence"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def empty_calculator_draft():
    return {
        "schema_version": 1,
        "updated_at": "",
        "status": "not_built",
        "source_artifacts": {},
        "candidates": {
            "floors": [], "zones": [], "rooms": [], "room_inputs": [],
            "schedules": [], "envelope": [],
        },
        "review_items": [],
        "decisions": {},
    }


def build_calculator_draft(thermal_model, building_evidence, drawing_coverage, previous=None, source_artifacts=None):
    """Build a proposal-only artifact without modifying calculator inputs."""
    previous = previous or {}
    decisions = deepcopy(previous.get("decisions", {})) if isinstance(previous, dict) else {}
    result = empty_calculator_draft()
    result.update({
        "updated_at": timestamp(),
        "status": "review_required",
        "source_artifacts": deepcopy(source_artifacts or {}),
        "decisions": decisions,
    })
    candidates = result["candidates"]

    levels = drawing_coverage.get("levels", []) or building_evidence.get("levels", [])
    floor_by_level = {}
    for index, level in enumerate(levels, start=1):
        name = str(level.get("level_name", level.get("name", ""))).strip()
        if not name:
            continue
        floor_id = stable("floor", name, index)
        floor_by_level[name.lower()] = floor_id
        evidence = level.get("purpose_evidence", level.get("evidence", []))
        candidates["floors"].append(candidate(
            f"floor:{floor_id}", "floor", "hourly_load_model", {
                "floor_id": floor_id, "name": name, "elevation_m": None,
                "verification_status": "provisional",
            }, evidence, level.get("purpose_status", level.get("status", "provisional")),
            "Drawing coverage identifies this level; confirm its name and elevation before relying on it.",
        ))

    thermal_zones = {item.get("name", "").strip().lower(): item for item in thermal_model.get("zones", [])}
    seen_rooms = set()
    for index, space in enumerate(building_evidence.get("spaces", []), start=1):
        if space.get("status") != "direct":
            continue
        name = str(space.get("name", "")).strip()
        level_name = str(space.get("level_name", "")).strip()
        if not name:
            continue
        room_id = stable("room", name, index)
        if room_id in seen_rooms:
            room_id = f"{room_id}_{index}"
        seen_rooms.add(room_id)
        evidence = space.get("evidence", [])
        floor_id = floor_by_level.get(level_name.lower())
        if not floor_id:
            result["review_items"].append(review_item(
                f"room_floor:{room_id}", "room", room_id,
                "A cited room was found, but its floor could not be linked to reviewed drawing coverage. Confirm the room's floor before applying it.",
                evidence,
            ))
            continue
        zone_id = stable("zone", name, index)
        candidates["zones"].append(candidate(
            f"zone:{zone_id}", "zone", "hourly_load_model", {
                "zone_id": zone_id, "name": name, "floor_id": floor_id,
                "verification_status": "provisional",
            }, evidence, "direct",
            "A one-room provisional zone is proposed only as a starting topology; review zoning before calculation.",
        ))
        area = number_from_text(space.get("area"))
        room_value = {
            "room_id": room_id, "name": name, "zone_id": zone_id,
            "source_zone_id": "", "mapping_status": "inferred",
            "verification_status": "provisional", "source_room_labels": [name],
            "area_m2": area,
        }
        candidates["rooms"].append(candidate(
            f"room:{room_id}", "room", "hourly_load_model", room_value, evidence, "direct",
            "Room identity is directly cited. Confirm room-to-zone mapping and every cooling input before calculation.",
        ))
        if area is None:
            result["review_items"].append(review_item(
                f"room_area:{room_id}", "room", room_id,
                "Room area was not directly cited. Measure or enter a reviewed area; no area is guessed.", evidence,
            ))
        zone_model = thermal_zones.get(name.lower(), {})
        ceiling = zone_model.get("ceiling_height_mm")
        if ceiling is not None:
            candidates["room_inputs"].append(candidate(
                f"room_input:{room_id}:ceiling_height", "room_input", "hourly_load_model", {
                    "room_id": room_id, "field": "ceiling_height_mm", "value": ceiling, "unit": "mm",
                }, evidence, zone_model.get("status", "provisional"),
                "Cited ceiling evidence is retained for review. Ceiling height is not yet a standalone hourly cooling calculation input.",
            ))
        else:
            result["review_items"].append(review_item(
                f"room_ceiling:{room_id}", "room", room_id,
                "Ceiling height is required for the wider room model but was not directly cited for this room.", evidence,
            ))
        result["review_items"].append(review_item(
            f"schedule_required:{room_id}", "room", room_id,
            "No complete cited 24-hour schedule was available. Select or enter a reviewed schedule; none is defaulted.", evidence,
        ))

    for index, item in enumerate(building_evidence.get("lighting", []), start=1):
        candidates["room_inputs"].append(candidate(
            f"room_input:lighting:{index}", "room_input", "hourly_load_model", {
                "field": "lighting_connected_w", "value": item.get("connected_w"), "unit": "W",
                "level_name": item.get("level_name", ""),
            }, item.get("evidence", []), item.get("status", "direct"),
            "Connected lighting is cited, but room allocation, diversity, and a schedule are missing; it cannot become a cooling load automatically.",
        ))
    for index, item in enumerate(building_evidence.get("equipment", []), start=1):
        candidates["room_inputs"].append(candidate(
            f"room_input:equipment:{index}", "room_input", "hourly_load_model", {
                "field": "equipment_candidate", "name": item.get("name", "Equipment"), "kind": item.get("kind", ""),
                "quantity": item.get("quantity"), "watts": item.get("watts"), "level_name": item.get("level_name", ""),
            }, item.get("evidence", []), item.get("status", "direct"),
            "Equipment identity is cited, but heat-to-space wattage, diversity, and a schedule are missing; it cannot become a cooling load automatically.",
        ))

    schedule_candidates(result, thermal_model)
    envelope_candidates(result, building_evidence)
    return result


def candidate(candidate_id, kind, target_artifact, value, evidence, confidence, reason):
    return {
        "candidate_id": candidate_id,
        "kind": kind,
        "target_artifact": target_artifact,
        "value": deepcopy(value),
        "confidence": confidence if confidence in {"high", "medium", "low", "direct", "provisional"} else "medium",
        "source": "Reviewed drawing evidence",
        "citations": citations(evidence),
        "evidence_ids": [],
        "proposal_status": "pending_review",
        "reason": reason,
    }


def review_item(item_id, scope, affected_id, reason, evidence):
    return {
        "item_id": item_id, "scope": scope, "affected_id": affected_id,
        "status": "needs_evidence", "reason": reason,
        "citations": citations(evidence), "source_artifact": "building_evidence.json",
    }


def schedule_candidates(result, thermal_model):
    """Accept future manual evidence only when each cited profile has 24 values."""
    for index, raw in enumerate(thermal_model.get("schedule_candidates", []), start=1):
        profiles = raw.get("day_profiles", {}) if isinstance(raw, dict) else {}
        complete = isinstance(profiles, dict) and all(
            isinstance(profiles.get(day, {}).get("values"), list) and len(profiles[day]["values"]) == 24
            for day in DAY_TYPES
        )
        evidence = raw.get("evidence", []) if isinstance(raw, dict) else []
        if not complete:
            result["review_items"].append(review_item(
                f"schedule_profile:{index}", "schedule", str(raw.get("schedule_id", index)),
                "A schedule candidate was found without complete cited 24-hour profiles. It remains a review item and cannot be applied.", evidence,
            ))
            continue
        schedule_id = str(raw.get("schedule_id", "")).strip() or f"schedule_{index}"
        result["candidates"]["schedules"].append(candidate(
            f"schedule:{schedule_id}", "schedule", "schedule_library", {
                "schedule_id": schedule_id, "title": raw.get("title", schedule_id),
                "description": raw.get("description", ""), "status": "provisional",
                "day_profiles": profiles,
            }, evidence, raw.get("confidence", "medium"),
            "A complete cited profile is proposed. Engineer approval is required before it can be assigned to a room.",
        ))


def envelope_candidates(result, building_evidence):
    """Keep thermal/geometry evidence visible, but never activate a surface model."""
    for index, item in enumerate(building_evidence.get("constructions", []), start=1):
        result["candidates"]["envelope"].append(candidate(
            f"envelope:construction:{index}", "construction", "envelope_library", {
                "record_id": stable("construction", item.get("reference", item.get("kind", "construction")), index),
                "title": item.get("reference", item.get("kind", "Construction")), "kind": item.get("kind", ""),
                "thermal_performance": item.get("thermal_performance"),
            }, item.get("evidence", []), item.get("status", "direct"),
            "Construction evidence is stored for review only. Missing reviewed thermal properties prevent activation and calculation.",
        ))
    for index, item in enumerate(building_evidence.get("openings", []), start=1):
        result["candidates"]["envelope"].append(candidate(
            f"envelope:opening:{index}", "opening", "envelope_model", {
                "tag": item.get("tag", ""), "kind": item.get("kind", ""), "dimensions": item.get("dimensions"),
                "performance": item.get("performance"), "level_name": item.get("level_name", ""),
            }, item.get("evidence", []), item.get("status", "direct"),
            "Opening evidence is proposal-only. Detailed glazing and geometric shading are not calculated in this release.",
        ))
    for index, item in enumerate(building_evidence.get("surfaces", []), start=1):
        result["candidates"]["envelope"].append(candidate(
            f"envelope:surface:{index}", "surface", "envelope_model", {
                "kind": item.get("kind", ""), "adjacency": item.get("adjacency", ""), "geometry": item.get("geometry"),
                "level_name": item.get("level_name", ""),
            }, item.get("evidence", []), item.get("status", "direct"),
            "Boundary evidence is proposal-only. Geometry, construction, boundary method, and reviewed temperatures are required before a surface can calculate.",
        ))


def apply_calculator_draft(draft, decisions, hourly_model=None, schedule_library=None, envelope_library=None, envelope_model=None, source_requirements_updated_at=""):
    """Apply only explicitly accepted/edit proposals; never overwrite authored IDs."""
    if not isinstance(draft, dict) or draft.get("schema_version") != 1:
        raise ValueError("Build a calculator draft before applying decisions.")
    decisions = decisions or {}
    model = deepcopy(hourly_model or empty_hourly_load_model())
    schedules = deepcopy(schedule_library or empty_schedule_library())
    library = deepcopy(envelope_library or empty_envelope_library())
    model_changed = schedules_changed = library_changed = False
    summary = {"created": [], "already_present": [], "skipped_conflicts": [], "unresolved": [], "reports_marked_stale": []}
    provenance = "Engineer review of calculator draft"

    by_id = {item.get("candidate_id"): item for rows in draft.get("candidates", {}).values() for item in rows}
    accepted = []
    for candidate_id, item in by_id.items():
        decision = decisions.get(candidate_id, draft.get("decisions", {}).get(candidate_id, {}))
        action, value = decision_value(decision, item)
        if action in {"accept", "edit"}:
            accepted.append((item, value))
        elif action in {"reject", "needs_evidence"}:
            summary["unresolved"].append({"candidate_id": candidate_id, "decision": action, "reason": item.get("reason", "")})

    for kind in ("floor", "zone", "room"):
        for item, value in [row for row in accepted if row[0]["kind"] == kind]:
            changed = apply_topology_record(model, kind, value, item, provenance, summary)
            model_changed = model_changed or changed
    for item, value in [row for row in accepted if row[0]["kind"] == "schedule"]:
        changed = apply_schedule_record(schedules, value, item, provenance, summary)
        schedules_changed = schedules_changed or changed
    for item, value in [row for row in accepted if row[0]["kind"] == "construction"]:
        changed = apply_construction_record(library, value, item, provenance, summary)
        library_changed = library_changed or changed

    # Envelope openings/surfaces and room input candidates intentionally remain review-only.
    for item, _value in [row for row in accepted if row[0]["kind"] in {"opening", "surface", "room_input"}]:
        summary["unresolved"].append({"candidate_id": item["candidate_id"], "decision": "needs_engineering_input", "reason": item["reason"]})

    if model_changed:
        model["source_requirements_updated_at"] = source_requirements_updated_at or model.get("source_requirements_updated_at", "")
        model = validate_hourly_load_model(model)
        summary["reports_marked_stale"].append("hourly_load_report.json")
    else:
        model = validate_hourly_load_model(model)
        model["updated_at"] = (hourly_model or {}).get("updated_at", "")
    if schedules_changed:
        schedules = validate_schedule_library(schedules)
        summary["reports_marked_stale"].append("hourly_load_report.json")
    else:
        schedules = validate_schedule_library(schedules)
        schedules["updated_at"] = (schedule_library or {}).get("updated_at", "")
    if library_changed:
        library = validate_envelope_library(library)
        summary["reports_marked_stale"].append("hourly_load_report.json")
    else:
        library = validate_envelope_library(library)
        library["updated_at"] = (envelope_library or {}).get("updated_at", "")
    # The bridge cannot activate or create calculating envelope surfaces.
    model_envelope = validate_envelope_model(envelope_model or empty_envelope_model(), library)
    model_envelope["updated_at"] = (envelope_model or {}).get("updated_at", "")
    return {
        "hourly_load_model": model,
        "schedule_library": schedules,
        "envelope_library": library,
        "envelope_model": model_envelope,
        "changed": {"hourly_load_model": model_changed, "schedule_library": schedules_changed, "envelope_library": library_changed},
        "summary": summary,
    }


def apply_topology_record(model, kind, value, item, provenance, summary):
    key = {"floor": "floor_id", "zone": "zone_id", "room": "room_id"}[kind]
    bucket = {"floor": "floors", "zone": "zones", "room": "rooms"}[kind]
    record_id = value.get(key, "")
    existing = next((row for row in model[bucket] if row.get(key) == record_id), None)
    if existing:
        record_status(summary, item, existing.get("source", ""), provenance)
        return False
    record = deepcopy(value)
    record.update({
        "source": f"{provenance}: {item['candidate_id']}",
        "citations": deepcopy(item.get("citations", [])),
    })
    if kind == "floor":
        record["verification_status"] = "confirmed"
    elif kind == "zone":
        record["verification_status"] = "confirmed"
    else:
        record.update({
            "mapping_status": "confirmed", "verification_status": "confirmed",
            "occupancy": None, "indoor_cooling_setpoint_c": None, "heat_sources": [],
            "cooling_load": {}, "cooling_load_conditions": {},
            "schedule_assignments": {"people": "", "lighting": "", "outside_air": "", "equipment": {}, "solar": {}},
            "unapproved_components": default_room_components(),
        })
    model[bucket].append(record)
    summary["created"].append({"candidate_id": item["candidate_id"], "artifact": "hourly_load_model.json", "id": record_id})
    return True


def apply_schedule_record(library, value, item, provenance, summary):
    record_id = value.get("schedule_id", "")
    existing = next((row for row in library["schedules"] if row.get("schedule_id") == record_id), None)
    if existing:
        record_status(summary, item, existing.get("source", ""), provenance)
        return False
    record = deepcopy(value)
    record.update({"status": "confirmed", "source": f"{provenance}: {item['candidate_id']}", "citations": deepcopy(item.get("citations", []))})
    for day in DAY_TYPES:
        profile = record["day_profiles"][day]
        profile.update({"status": "confirmed", "source": f"{provenance}: {item['candidate_id']}", "citations": deepcopy(item.get("citations", []))})
    library["schedules"].append(record)
    summary["created"].append({"candidate_id": item["candidate_id"], "artifact": "schedule_library.json", "id": record_id})
    return True


def apply_construction_record(library, value, item, provenance, summary):
    # Construction evidence with no reviewed U-value is retained only in the draft.
    kind = {"roof": "roof", "wall": "opaque_wall", "floor": "floor", "ceiling": "ceiling", "partition": "partition"}.get(value.get("kind"))
    thermal = value.get("thermal_performance")
    u_value = thermal.get("u_value_w_m2k") if isinstance(thermal, dict) else None
    if not kind or u_value in (None, ""):
        summary["unresolved"].append({"candidate_id": item["candidate_id"], "decision": "needs_evidence", "reason": "Construction needs a supported kind and reviewed U-value before it can enter the envelope library."})
        return False
    record_id = value.get("record_id", "")
    existing = next((row for row in library["constructions"] if row.get("record_id") == record_id), None)
    if existing:
        record_status(summary, item, existing.get("source", ""), provenance)
        return False
    library["constructions"].append({
        "record_id": record_id, "title": value.get("title", record_id), "revision": 1,
        "review_status": "confirmed", "source": f"{provenance}: {item['candidate_id']}",
        "citations": deepcopy(item.get("citations", [])), "kind": kind,
        "u_value_w_m2k": u_value, "absorptivity": None,
    })
    summary["created"].append({"candidate_id": item["candidate_id"], "artifact": "envelope_library.json", "id": record_id})
    return True


def record_status(summary, item, existing_source, provenance):
    bucket = "already_present" if str(existing_source).startswith(provenance) else "skipped_conflicts"
    summary[bucket].append({"candidate_id": item["candidate_id"], "reason": "A record with the same stable ID already exists; authored records are never overwritten."})


def decision_value(decision, item):
    if not isinstance(decision, dict):
        return "", deepcopy(item["value"])
    action = decision.get("decision", "")
    if action not in DECISIONS:
        return "", deepcopy(item["value"])
    value = deepcopy(item["value"])
    if action == "edit":
        if not isinstance(decision.get("value", value), dict):
            raise ValueError(f"Edited value for {item['candidate_id']} must be a JSON object.")
        value.update(deepcopy(decision["value"]))
    return action, value


def citations(evidence):
    result = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        page = item.get("page")
        excerpt = str(item.get("excerpt", "")).strip()
        if page in (None, "") and not excerpt:
            continue
        result.append({"reference": "Reviewed PDF drawing evidence", "page": page if page not in (None, "") else None, "excerpt": excerpt})
    return result


def stable(prefix, value, index):
    return f"{prefix}_{slug(value)}_{index}" if prefix in {"room", "zone"} else f"{prefix}_{slug(value)}"


def number_from_text(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None
