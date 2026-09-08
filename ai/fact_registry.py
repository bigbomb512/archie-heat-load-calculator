"""Validation and safe activation for AI-extracted project facts."""

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone


FACT_CATEGORIES = {
    "floor", "room", "room_boundary", "area", "ceiling_height", "wall", "roof", "floor_surface",
    "ceiling", "opening", "orientation", "glazing", "lighting", "equipment", "occupancy", "schedule",
    "outside_air", "adjacency", "weather", "design_condition", "construction",
}
SOURCE_TYPES = {"architect_pdf", "vision_extraction", "approved_research", "project_review"}
AUTO_CATEGORIES = {"page_metadata", "drawing_number", "source_fingerprint", "evidence_id", "label", "unit", "schedule_structure"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def empty_registry():
    return {"schema_version": 2, "revision": 0, "facts": [], "conflicts": [], "review_items": [], "source_fingerprint": "", "updated_at": ""}


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate_fact(raw, known_pages=None):
    if not isinstance(raw, dict):
        raise ValueError("Fact must be an object.")
    required = ("fact_id", "category", "value", "source_type", "validation_status", "activation_status")
    missing = [key for key in required if raw.get(key) in (None, "")]
    if missing:
        raise ValueError("Fact missing: " + ", ".join(missing))
    if raw["category"] not in FACT_CATEGORIES:
        raise ValueError("Unsupported fact category: " + str(raw["category"]))
    if raw["source_type"] not in SOURCE_TYPES:
        raise ValueError("Unsupported fact source type: " + str(raw["source_type"]))
    if raw["activation_status"] not in {"active", "proposed", "blocked", "excluded"}:
        raise ValueError("Invalid fact activation status.")
    if raw["validation_status"] not in {"valid", "needs_review", "invalid", "conflict"}:
        raise ValueError("Invalid fact validation status.")
    source = raw.get("source") or {}
    if not source.get("page") and raw["source_type"] != "approved_research" and not source.get("reference"):
        raise ValueError("Non-research facts require a source page or reference.")
    if known_pages and source.get("page") is not None and source["page"] not in known_pages:
        raise ValueError("Fact cites a page not present in the architect packet.")
    if raw["category"] in {"area", "ceiling_height"} and not _positive_number(raw["value"]):
        raise ValueError("Numeric geometry facts require a positive value.")
    if raw["category"] in {"area", "ceiling_height", "opening", "glazing", "construction"} and not raw.get("unit") and raw["category"] in {"area", "ceiling_height"}:
        raise ValueError("Geometry facts require units.")
    return deepcopy(raw)


def validate_registry(registry, known_pages=None):
    if not isinstance(registry, dict) or registry.get("schema_version") not in {1, 2}:
        raise ValueError("Unsupported fact registry schema.")
    facts = [validate_fact(row, known_pages) for row in registry.get("facts", [])]
    ids = [row["fact_id"] for row in facts]
    if len(ids) != len(set(ids)):
        raise ValueError("Fact IDs must be unique.")
    result = deepcopy(registry)
    result["schema_version"] = 2
    result["facts"] = facts
    result.setdefault("conflicts", [])
    result.setdefault("review_items", [])
    return result


def fact_from_entity(entity):
    value = entity.get("value", {})
    kind = entity.get("kind")
    surface_kind = str(value.get("kind", "wall")).lower()
    surface_category = "roof" if "roof" in surface_kind else "floor_surface" if "floor" in surface_kind else "ceiling" if "ceiling" in surface_kind else "wall"
    category = {"room": "room", "floor": "floor", "opening": "opening", "surface": surface_category,
                "construction": "construction", "lighting": "lighting", "equipment": "equipment"}.get(kind, kind)
    category = "floor_surface" if category == "ground_floor" else category
    if category not in FACT_CATEGORIES:
        category = "construction" if kind == "construction" else "room"
    source = entity.get("source", {})
    status = "valid" if entity.get("geometry_status") == "geometry_confirmed" or kind in {"floor", "opening", "construction", "lighting", "equipment"} else "needs_review"
    activation = "active" if category in {"floor", "opening", "construction", "lighting", "equipment"} and status == "valid" else "proposed"
    return {
        "fact_id": entity["entity_id"], "category": category, "value": value.get("area") if category == "area" else value,
        "unit": value.get("unit", ""), "affected_id": value.get("room_id") or value.get("level_name") or value.get("id", ""),
        "source_type": "architect_pdf", "source": source, "excerpt": source.get("excerpt", ""),
        "extraction_confidence": entity.get("confidence", "unknown"), "validation_status": status,
        "activation_status": activation, "conflicts": [],
        "candidate_fingerprint": fingerprint(entity), "evidence_ids": entity.get("evidence_ids", []),
        "dependencies": list(value.get("dependencies", [])) if isinstance(value, dict) else [],
    }


def registry_from_fusion(fusion):
    registry = empty_registry()
    registry["source_fingerprint"] = fusion.get("source_fingerprint", "")
    pages = {row.get("page") for row in fusion.get("pages", [])}
    registry["facts"] = [fact_from_entity(entity) for entity in fusion.get("entities", [])]
    registry["facts"].extend(deepcopy(fusion.get("facts", [])))
    deduped = {}
    for fact in registry["facts"]:
        deduped[fact["fact_id"]] = fact
    registry["facts"] = list(deduped.values())
    registry["conflicts"] = deepcopy(fusion.get("conflicts", []))
    registry["review_items"] = deepcopy(fusion.get("review_items", []))
    registry = validate_registry(registry, pages)
    registry["updated_at"] = timestamp()
    registry["fingerprint"] = fingerprint(registry)
    return registry


def activate_safe_facts(registry):
    """Activate only facts whose evidence is explicit and conflict-free."""
    result = deepcopy(validate_registry(registry))
    conflict_ids = {fact_id for conflict in result.get("conflicts", []) for fact_id in conflict.get("entity_ids", [])}
    for fact in result["facts"]:
        if fact["fact_id"] in conflict_ids or fact["validation_status"] != "valid":
            fact["activation_status"] = "proposed"
            continue
        value = fact.get("value")
        exact = fact["category"] in {"floor", "opening", "construction", "lighting", "equipment"}
        if fact["category"] in {"room", "room_boundary", "area", "ceiling_height", "glazing", "orientation", "schedule", "occupancy", "adjacency"}:
            exact = False
        fact["activation_status"] = "active" if exact else "proposed"
    result["revision"] = int(result.get("revision", 0)) + 1
    result["updated_at"] = timestamp()
    result["fingerprint"] = fingerprint(result)
    return result
