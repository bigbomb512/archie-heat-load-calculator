"""Evidence-only contracts for automated architect-drawing vision extraction.

This module is deliberately provider-neutral.  It selects local evidence,
validates the narrow extraction payload, and creates a provenance-preserving
vision response that existing evidence artifacts can consume.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from ai.drawing_coverage import source_fingerprint


ENTITY_KINDS = {"floor", "room", "opening", "surface", "ceiling", "lighting", "equipment"}
GEOMETRY_STATES = {"label_detected", "geometry_proposed", "geometry_confirmed", "not_applicable"}
ROLE_GROUPS = {
    "plan_geometry": {"primary_geometry_plan", "supporting_geometry_plan"},
    "ceiling_lighting": {"reflected_ceiling_or_service_plan"},
    "opening_elevation": {"opening_elevation", "elevation_or_section"},
    "visual_cross_check": {"3d_reference"},
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def empty_settings():
    return {
        "schema_version": 1,
        "updated_at": "",
        "owner_opt_in": False,
        "max_budget_aud": None,
        "selected_group_ids": [],
        "model": "",
    }


def validate_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError("Vision extraction settings must be an object.")
    result = deepcopy(empty_settings())
    result.update({key: raw.get(key, result[key]) for key in result})
    result["owner_opt_in"] = bool(result["owner_opt_in"])
    if result["max_budget_aud"] is not None:
        if not isinstance(result["max_budget_aud"], (int, float)) or isinstance(result["max_budget_aud"], bool) or not math.isfinite(result["max_budget_aud"]) or result["max_budget_aud"] <= 0:
            raise ValueError("Vision extraction maximum budget must be a positive AUD amount.")
        result["max_budget_aud"] = round(float(result["max_budget_aud"]), 2)
    if not isinstance(result["selected_group_ids"], list) or not all(isinstance(value, str) for value in result["selected_group_ids"]):
        raise ValueError("Vision extraction selected groups must be a list of IDs.")
    result["selected_group_ids"] = list(dict.fromkeys(result["selected_group_ids"]))
    result["model"] = str(result["model"] or "").strip()
    result["updated_at"] = str(raw.get("updated_at", ""))
    return result


def select_page_groups(ai_input, coverage):
    """Create stable, role-based request groups from the complete page register."""
    pages = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", [])}
    roles = coverage.get("page_roles", []) if isinstance(coverage, dict) else []
    grouped = []
    for group_id, accepted_roles in ROLE_GROUPS.items():
        members = []
        for role in roles:
            page = role.get("page")
            if role.get("proposed_role") not in accepted_roles or page not in pages:
                continue
            source = pages[page]
            members.append({
                "page": page,
                "drawing_number": source.get("drawing_number", ""),
                "title": source.get("title", ""),
                "role": role.get("proposed_role"),
                "level_name": role.get("level_name") or source.get("level_name", ""),
                "structured_text": str(source.get("structured_content", {}).get("markdown", ""))[:12000],
            })
        if members:
            grouped.append({"group_id": group_id, "title": group_id.replace("_", " ").title(), "pages": sorted(members, key=lambda row: row["page"])})
    return grouped


def estimate(settings, groups, cost_per_group_aud=None):
    chosen = settings.get("selected_group_ids") or [group["group_id"] for group in groups]
    selected = [group for group in groups if group["group_id"] in chosen]
    if cost_per_group_aud is None:
        estimated = None
    else:
        estimated = round(len(selected) * float(cost_per_group_aud), 2)
    return {
        "group_count": len(selected), "request_count": len(selected), "selected_group_ids": [group["group_id"] for group in selected],
        "estimated_cost_aud": estimated,
        "estimate_available": estimated is not None,
        "within_budget": estimated is not None and settings.get("max_budget_aud") is not None and estimated <= settings["max_budget_aud"],
    }


def extraction_schema():
    """Strict, bounded output shape accepted from the provider."""
    entity = {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "page", "drawing_number", "label", "level_name", "area_m2", "ceiling_height_mm", "width_mm", "height_mm", "unit", "geometry_status", "boundary_reference", "opening_tag", "surface_kind", "orientation", "witnesses", "excerpt", "confidence", "unresolved_fields"],
        "properties": {
            "kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
            "page": {"type": "integer"}, "drawing_number": {"type": "string"}, "label": {"type": "string"}, "level_name": {"type": "string"},
            "area_m2": {"type": ["number", "null"]}, "ceiling_height_mm": {"type": ["number", "null"]},
            "width_mm": {"type": ["number", "null"]}, "height_mm": {"type": ["number", "null"]}, "unit": {"type": "string"},
            "geometry_status": {"type": "string", "enum": sorted(GEOMETRY_STATES)}, "boundary_reference": {"type": "string"},
            "opening_tag": {"type": "string"}, "surface_kind": {"type": "string"}, "orientation": {"type": "string"},
            "witnesses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "kind", "reference"], "properties": {"page": {"type": "integer"}, "kind": {"type": "string"}, "reference": {"type": "string"}}}},
            "excerpt": {"type": "string"}, "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {
        "groups": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["group_id", "entities", "conflicts", "missing_evidence"], "properties": {
            "group_id": {"type": "string"}, "entities": {"type": "array", "items": entity},
            "conflicts": {"type": "array", "items": {"type": "string"}}, "missing_evidence": {"type": "array", "items": {"type": "string"}},
        }}},
    }}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def validate_provider_output(raw, groups):
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
        raise ValueError("Vision extraction must return an object with groups.")
    group_pages = {group["group_id"]: {page["page"]: page for page in group["pages"]} for group in groups}
    found_groups = set()
    entities, conflicts, missing = [], [], []
    for group in raw["groups"]:
        group_id = group.get("group_id")
        if group_id not in group_pages or group_id in found_groups:
            raise ValueError("Vision extraction returned an unknown or duplicate page group.")
        found_groups.add(group_id)
        if not isinstance(group.get("entities"), list) or not isinstance(group.get("conflicts"), list) or not isinstance(group.get("missing_evidence"), list):
            raise ValueError("Vision extraction group has invalid collections.")
        for row in group["entities"]:
            if not isinstance(row, dict) or row.get("kind") not in ENTITY_KINDS:
                raise ValueError("Vision extraction entity kind is invalid.")
            page = row.get("page")
            source_page = group_pages[group_id].get(page)
            if not source_page or row.get("drawing_number", "") != source_page.get("drawing_number", ""):
                raise ValueError("Vision extraction entity cites an unknown page or drawing number.")
            if not str(row.get("label", "")).strip() or not str(row.get("excerpt", "")).strip():
                raise ValueError("Vision extraction entities need a label and a cited excerpt.")
            if row.get("geometry_status") not in GEOMETRY_STATES or row.get("confidence") not in {"low", "medium", "high"}:
                raise ValueError("Vision extraction geometry status or confidence is invalid.")
            for key in ("area_m2", "ceiling_height_mm", "width_mm", "height_mm"):
                if row.get(key) is not None and not _positive(row[key]):
                    raise ValueError(f"Vision extraction {key} must be positive when present.")
            witnesses = row.get("witnesses")
            if not isinstance(witnesses, list) or any(not isinstance(item, dict) or item.get("page") not in {member["page"] for g in groups for member in g["pages"]} or not item.get("reference") for item in witnesses):
                raise ValueError("Vision extraction witnesses must cite packet pages and references.")
            record = deepcopy(row)
            record["group_id"] = group_id
            record["auto_activate"] = record["geometry_status"] == "geometry_confirmed" and len({(item.get("page"), item.get("reference")) for item in witnesses}) >= 2 and not record.get("unresolved_fields")
            record["candidate_fingerprint"] = fingerprint({key: value for key, value in record.items() if key != "auto_activate"})
            entities.append(record)
        conflicts.extend({"group_id": group_id, "reason": str(item)} for item in group["conflicts"])
        missing.extend({"group_id": group_id, "reason": str(item)} for item in group["missing_evidence"])
    if found_groups != set(group_pages):
        raise ValueError("Vision extraction did not return every selected page group.")
    return {"entities": entities, "conflicts": conflicts, "missing_evidence": missing}


def vision_response_from_extraction(validated, source_fingerprint_value, model):
    return {
        "provider": "openai_responses", "model": model, "source": "automated_project_opt_in", "store": False,
        "source_fingerprint": source_fingerprint_value,
        "result": {
            "geometry_review": {"pages": []},
            "auto_extraction": {"entities": validated["entities"], "conflicts": validated["conflicts"], "missing_evidence": validated["missing_evidence"]},
        },
    }
