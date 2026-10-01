"""Evidence-only contracts for automated architect-drawing vision extraction.

This module is deliberately provider-neutral. It indexes the complete local
evidence set, selects a bounded capability-ranked context, validates the
narrow extraction payload, and creates a provenance-preserving vision response
that existing evidence artifacts can consume.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

ENTITY_KINDS = {"floor", "room", "opening", "surface", "ceiling", "lighting", "equipment"}
GEOMETRY_STATES = {"label_detected", "geometry_proposed", "geometry_confirmed", "not_applicable"}
ROLE_GROUPS = {
    "plan_geometry": {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan"},
    "ceiling_lighting": {"reflected_ceiling_plan", "reflected_ceiling_or_service_plan", "services_or_lighting_plan", "architect_lighting_plan", "architect_electrical_plan"},
    "opening_elevation": {"opening_elevation", "opening_schedule", "elevation_or_section", "elevation", "section"},
    "visual_cross_check": {"3d_render", "3d_reference", "3d_crosscheck"},
    "schedules_and_construction": {"schedule", "material_schedule", "equipment_schedule", "lighting_schedule", "construction_or_detail", "detail", "reference"},
}

# Context selection is intentionally bounded.  The complete architect packet
# is still indexed, but provider requests use only the smallest ranked union
# that covers the available evidence categories.
CONTEXT_SELECTION_POLICY_VERSION = "ranked-context-v1"
MAX_MAIN_CONTEXT_PAGES = 24
MAX_3D_CROSS_CHECK_PAGES = 3
CATEGORY_QUOTAS = {
    "room_geometry": 6,
    "openings_windows": 5,
    "ceiling_lighting": 4,
    "vertical_heights": 4,
    "equipment": 4,
    "occupancy_schedules": 4,
    "construction_boundaries": 4,
    "3d_cross_check": MAX_3D_CROSS_CHECK_PAGES,
}
ROLE_CATEGORY_FALLBACK = {
    "main_floor_plan": {"room_geometry": 0.85, "openings_windows": 0.45},
    "primary_geometry_plan": {"room_geometry": 0.85, "openings_windows": 0.45},
    "supporting_geometry_plan": {"room_geometry": 0.55},
    "reflected_ceiling_plan": {"ceiling_lighting": 0.85},
    "reflected_ceiling_or_service_plan": {"ceiling_lighting": 0.75},
    "services_or_lighting_plan": {"ceiling_lighting": 0.75, "equipment": 0.35},
    "architect_lighting_plan": {"ceiling_lighting": 0.75},
    "architect_electrical_plan": {"ceiling_lighting": 0.75},
    "opening_elevation": {"openings_windows": 0.85, "vertical_heights": 0.45},
    "opening_schedule": {"openings_windows": 0.85},
    "elevation_or_section": {"vertical_heights": 0.75, "construction_boundaries": 0.45},
    "elevation": {"vertical_heights": 0.75},
    "section": {"vertical_heights": 0.75},
    "equipment_schedule": {"equipment": 0.85},
    "lighting_schedule": {"ceiling_lighting": 0.75},
    "schedule": {"occupancy_schedules": 0.65},
    "material_schedule": {"construction_boundaries": 0.65},
    "construction_or_detail": {"construction_boundaries": 0.65},
    "detail": {"construction_boundaries": 0.35},
    "3d_render": {"3d_cross_check": 0.55},
    "3d_reference": {"3d_cross_check": 0.55},
    "3d_crosscheck": {"3d_cross_check": 0.55},
}


def _page_relationship_ids(role):
    """Return explicit page links recorded by the coverage graph."""
    result = set()
    for relation in role.get("related_pages", []) or []:
        if not isinstance(relation, dict):
            continue
        for key in ("from_page", "to_page", "page"):
            value = relation.get(key)
            if isinstance(value, int):
                result.add(value)
    result.discard(role.get("page"))
    return result


def _role_page_score(role):
    relevance = role.get("relevance") or {}
    return max((float(value) for value in relevance.values() if isinstance(value, (int, float))), default=0.0)


def _ranked_context(ai_input, coverage):
    """Rank all indexed pages and return a bounded, deterministic selection.

    This function deliberately does not activate evidence.  It only decides
    which already-discovered pages are sent in the normal extraction context;
    ambiguous pages remain available in a separate exception appendix.
    """
    pages = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", [])}
    roles = coverage.get("page_roles", []) if isinstance(coverage, dict) else []
    role_by_page = {row.get("page"): row for row in roles if row.get("page") in pages}
    category_pages = {category: [] for category in CATEGORY_QUOTAS}
    exceptions, references = [], []
    for page_number, source in pages.items():
        role = role_by_page.get(page_number, {})
        relevance = role.get("relevance") or {}
        fallback = ROLE_CATEGORY_FALLBACK.get(role.get("proposed_role", ""), {})
        scores = {category: round(float(relevance.get(category, fallback.get(category, 0)) or 0), 2) for category in CATEGORY_QUOTAS}
        max_score = max(scores.values(), default=0.0)
        selection = role.get("selection") or (
            "primary_context" if max_score >= 0.70 else
            "supporting_context" if max_score >= 0.30 else
            "reference_only"
        )
        identity_status = (role.get("identity") or {}).get("status", role.get("drawing_number_status", ""))
        # A missing level is a review issue, but it should not hide an
        # otherwise high-value plan/elevation from the main context. Only an
        # explicit ranked exception or conflicting identity is appendix-only.
        is_exception = selection == "ranked_exception" or identity_status == "ambiguous"
        role_name = role.get("proposed_role", source.get("plan_role", "reference"))
        is_reference = (
            selection == "reference_only"
            or (role.get("reference_only") is True and role_name not in ROLE_GROUPS["visual_cross_check"])
            or not max_score
        )
        row = {
            "page": page_number,
            "drawing_number": role.get("resolved_drawing_number") or source.get("drawing_number", ""),
            "title": source.get("title", ""),
            "role": role_name,
            "relevance": scores,
            "max_relevance": max_score,
            "selection": selection,
            "selection_reasons": role.get("selection_reasons", []),
            "related_pages": sorted(_page_relationship_ids(role)),
            "identity_status": identity_status or "missing",
        }
        if is_exception:
            exceptions.append(row)
        elif is_reference:
            references.append(row)
        else:
            for category, score in scores.items():
                if score > 0:
                    category_pages[category].append(row)

    def order(row, category=None):
        score = row["relevance"].get(category, 0) if category else row["max_relevance"]
        return (-score, -row["max_relevance"], str(row.get("drawing_number", "")), row["page"])

    selected = {}
    primary = set()
    # Reserve the strongest pages for every category that actually has
    # evidence.  This prevents a large geometry group from crowding out all
    # service, opening, schedule, or vertical evidence.
    for category, quota in CATEGORY_QUOTAS.items():
        for row in sorted(category_pages[category], key=lambda item: order(item, category))[:quota]:
            selected.setdefault(row["page"], row)
            primary.add(row["page"])

    # Add linked supporting pages only when the coverage graph provides an
    # explicit relationship.  Role compatibility alone is not sufficient.
    linked = set(page for row in selected.values() for page in row["related_pages"])
    supporting = set()
    for row in sorted((item for item in category_pages["room_geometry"] + category_pages["openings_windows"]
                       + category_pages["ceiling_lighting"] + category_pages["vertical_heights"]
                       + category_pages["equipment"] + category_pages["occupancy_schedules"]
                       + category_pages["construction_boundaries"]), key=order):
        if row["page"] in selected or row["page"] not in linked:
            continue
        selected[row["page"]] = row
        supporting.add(row["page"])

    # Strong, directly relevant pages can fill remaining capacity even when a
    # packet lacks explicit cross-sheet links.  Weak role-only pages cannot.
    for row in sorted((item for values in category_pages.values() for item in values), key=order):
        if len(selected) >= MAX_MAIN_CONTEXT_PAGES:
            break
        if row["page"] in selected or row["max_relevance"] < 0.70:
            continue
        selected[row["page"]] = row
        supporting.add(row["page"])

    # Keep 3D evidence explicitly bounded and cross-check-only.
    three_d = [row for row in sorted(category_pages["3d_cross_check"], key=lambda item: order(item, "3d_cross_check"))
               if row["page"] in selected]
    for row in three_d[MAX_3D_CROSS_CHECK_PAGES:]:
        selected.pop(row["page"], None)
        primary.discard(row["page"])
        supporting.discard(row["page"])
    cross_check = {row["page"] for row in three_d[:MAX_3D_CROSS_CHECK_PAGES]}

    # The category quota union can be 26 pages. Trim deterministically while
    # preserving one representative for every category with evidence.
    category_representatives = {
        category: next((row["page"] for row in sorted(category_pages[category], key=lambda item: order(item, category))
                        if row["page"] in selected), None)
        for category in CATEGORY_QUOTAS
    }
    protected = {page for page in category_representatives.values() if page is not None}
    if len(selected) > MAX_MAIN_CONTEXT_PAGES:
        removable = sorted((row for page, row in selected.items() if page not in protected), key=order, reverse=True)
        for row in removable[:len(selected) - MAX_MAIN_CONTEXT_PAGES]:
            selected.pop(row["page"], None)
            primary.discard(row["page"])
            supporting.discard(row["page"])
            cross_check.discard(row["page"])

    main_pages = sorted(selected.values(), key=lambda row: (row["page"]))
    main_ids = {row["page"] for row in main_pages}
    category_coverage = {
        category: sorted(row["page"] for row in rows if row["page"] in main_ids)
        for category, rows in category_pages.items()
        if any(row["page"] in main_ids for row in rows)
    }
    for row in main_pages:
        row["context_selection"] = "cross_check_context" if row["page"] in cross_check else "primary_context" if row["page"] in primary else "supporting_context"
        row["context_selection_reasons"] = list(dict.fromkeys([
            *(row.get("selection_reasons") or []),
            "category quota coverage" if row["page"] in primary else "explicit cross-page link" if row["page"] in supporting else "strong category relevance",
        ]))
    for row in exceptions:
        row["context_selection"] = "ranked_exception"
        row["context_selection_reasons"] = list(dict.fromkeys([*(row.get("selection_reasons") or []), "ambiguous or conflicting page evidence"]))
    for row in references:
        row["context_selection"] = "reference_only"
        row["context_selection_reasons"] = ["retained in page register; no strong calculation capability"]
    all_rows = sorted(main_pages + exceptions + references, key=lambda row: row["page"])
    summary = {
        "policy_version": CONTEXT_SELECTION_POLICY_VERSION,
        "max_main_context_pages": MAX_MAIN_CONTEXT_PAGES,
        "max_3d_cross_check_pages": MAX_3D_CROSS_CHECK_PAGES,
        "main_context_pages": [row["page"] for row in main_pages],
        "supporting_context_pages": sorted(supporting & main_ids),
        "cross_check_pages": sorted(cross_check & main_ids),
        "exception_pages": [row["page"] for row in exceptions],
        "reference_only_pages": [row["page"] for row in references],
        "category_coverage": category_coverage,
        "pages": all_rows,
    }
    summary["fingerprint"] = fingerprint({key: value for key, value in summary.items() if key != "fingerprint"})
    return summary


def build_ranked_context(ai_input, coverage):
    """Public wrapper used by API and handoff builders."""
    return _ranked_context(ai_input, coverage)


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
        "selected_group_ids": [],
        "model": "",
    }


def validate_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError("Vision extraction settings must be an object.")
    result = deepcopy(empty_settings())
    result.update({key: raw.get(key, result[key]) for key in result})
    result["owner_opt_in"] = bool(result["owner_opt_in"])
    if not isinstance(result["selected_group_ids"], list) or not all(isinstance(value, str) for value in result["selected_group_ids"]):
        raise ValueError("Vision extraction selected groups must be a list of IDs.")
    result["selected_group_ids"] = list(dict.fromkeys(result["selected_group_ids"]))
    result["model"] = str(result["model"] or "").strip()
    result["updated_at"] = str(raw.get("updated_at", ""))
    return result


def select_page_groups(ai_input, coverage):
    """Create stable, capability-aware request groups from the complete register.

    Older coverage artifacts contain only ``proposed_role``; those remain
    supported. New coverage adds selection and capability metadata, allowing
    the same selection to drive both manual and optional provider workflows.
    """
    pages = {row.get("page"): row for row in ai_input.get("drawing_set", {}).get("pages", [])}
    context = _ranked_context(ai_input, coverage)
    selected_pages = set(context["main_context_pages"])
    context_by_page = {row["page"]: row for row in context["pages"]}
    roles = coverage.get("page_roles", []) if isinstance(coverage, dict) else []
    grouped = []
    for group_id, accepted_roles in ROLE_GROUPS.items():
        members = []
        for role in roles:
            page = role.get("page")
            if role.get("proposed_role") not in accepted_roles or page not in pages or page not in selected_pages:
                continue
            source = pages[page]
            selection = role.get("selection", "")
            # Keep ambiguous pages in the dedicated exception appendix rather
            # than duplicating them in a normal role group. This ensures the
            # provider sees the page with its uncertainty clearly labelled.
            if selection in {"reference_only", "ranked_exception"}:
                continue
            members.append({
                "page": page,
                "drawing_number": role.get("resolved_drawing_number") or source.get("drawing_number", ""),
                "drawing_number_candidates": (role.get("identity") or {}).get("drawing_number_candidates", []),
                "title": source.get("title", ""),
                "role": role.get("proposed_role"),
                "level_name": role.get("level_name") or source.get("level_name", ""),
                "structured_text": str(source.get("structured_content", {}).get("markdown", ""))[:12000],
                "capability_map": role.get("capability_map", {}),
                "relevance": role.get("relevance", {}),
                "selection": selection or "legacy_role_selection",
                "context_selection": context_by_page.get(page, {}).get("context_selection", "primary_context"),
                "selection_reasons": context_by_page.get(page, {}).get("context_selection_reasons") or role.get("selection_reasons", []),
                "related_pages": role.get("related_pages", []),
            })
        if members:
            grouped.append({"group_id": group_id, "title": group_id.replace("_", " ").title(), "pages": sorted(members, key=lambda row: row["page"])})
    return grouped


def selection_summary(settings, groups):
    """Return the non-financial user-visible scope of an AI extraction."""
    chosen = settings.get("selected_group_ids") or [group["group_id"] for group in groups]
    selected = [group for group in groups if group["group_id"] in chosen]
    page_count = sum(len(group.get("pages", [])) for group in selected)
    return {
        "group_count": len(selected), "request_count": len(selected), "page_count": page_count,
        "selected_group_ids": [group["group_id"] for group in selected],
    }


def extraction_schema():
    """Strict, bounded output shape accepted from the provider."""
    point = {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}
    lighting_fixture = {"type": "object", "additionalProperties": False, "properties": {
        "fixture_id": {"type": "string"}, "name": {"type": "string"},
        "quantity": {"type": ["number", "null"]}, "wattage_w": {"type": ["number", "null"]},
        "page": {"type": "integer"}, "drawing_number": {"type": "string"}, "excerpt": {"type": "string"},
    }}
    equipment_item = {"type": "object", "additionalProperties": False, "properties": {
        "equipment_id": {"type": "string"}, "name": {"type": "string"}, "model": {"type": "string"},
        "quantity": {"type": ["number", "null"]}, "rated_input_w": {"type": ["number", "null"]},
        "watts": {"type": ["number", "null"]}, "heat_to_space_factor": {"type": ["number", "null"]},
        "diversity_factor": {"type": ["number", "null"]}, "page": {"type": "integer"},
        "drawing_number": {"type": "string"}, "excerpt": {"type": "string"},
    }}
    entity = {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "page", "drawing_number", "label", "level_name", "area_m2", "ceiling_height_mm", "width_mm", "height_mm", "unit", "geometry_status", "boundary_reference", "opening_tag", "surface_kind", "orientation", "room_use_category", "room_use_rationale", "room_use_alternatives", "preliminary_profile_id", "witnesses", "excerpt", "confidence", "unresolved_fields"],
        "properties": {
            "kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
            "page": {"type": "integer"}, "drawing_number": {"type": "string"}, "label": {"type": "string"}, "level_name": {"type": "string"},
            "area_m2": {"type": ["number", "null"]}, "ceiling_height_mm": {"type": ["number", "null"]},
            "width_mm": {"type": ["number", "null"]}, "height_mm": {"type": ["number", "null"]}, "unit": {"type": "string"},
            "geometry_status": {"type": "string", "enum": sorted(GEOMETRY_STATES)}, "boundary_reference": {"type": "string"},
            "boundary_points_px": {"type": "array", "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
            "wall_ids": {"type": "array", "items": {"type": "string"}},
            "dimension_ids": {"type": "array", "items": {"type": "string"}},
            "independent_witness_page": {"type": ["integer", "null"]},
            "opening_tag": {"type": "string"}, "surface_kind": {"type": "string"}, "orientation": {"type": "string"},
            "occupancy_count": {"type": ["integer", "null"]}, "seat_count": {"type": ["integer", "null"]},
            "workstation_count": {"type": ["integer", "null"]},
            "lighting_fixtures": {"type": "array", "items": lighting_fixture},
            "equipment": {"type": "array", "items": equipment_item},
            "operating_hours_evidence": {"type": "string"},
            # Optional controlled vocabulary only. The provider never returns
            # thermal numbers; the preliminary assembler maps this selection
            # to its local, versioned assumption pack.
            "preliminary_profile_id": {"type": "string", "enum": ["", "retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"]},
            "room_use_category": {"type": "string", "enum": ["", "dining", "kitchen", "retail", "office", "storage", "ancillary_conditioned", "plant_or_unconditioned", "refrigeration_process", "residential", "generic_conditioned"]},
            "room_use_rationale": {"type": "string"},
            "room_use_alternatives": {"type": "array", "items": {"type": "string", "enum": ["dining", "kitchen", "retail", "office", "storage", "ancillary_conditioned", "plant_or_unconditioned", "refrigeration_process", "residential", "generic_conditioned"]}},
            "witnesses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "kind", "reference"], "properties": {"page": {"type": "integer"}, "kind": {"type": "string"}, "reference": {"type": "string"}}}},
            "excerpt": {"type": "string"}, "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    geometry_wall = {
        "type": "object", "additionalProperties": False,
        "required": ["wall_id", "classification", "geometry_role", "points_px", "confidence"],
        "properties": {
            "wall_id": {"type": "string"},
            "classification": {"type": "string", "enum": ["existing_wall", "new_solid_wall", "new_partition"]},
            "geometry_role": {"type": "string", "enum": ["outer_boundary_wall", "internal_partition"]},
            "points_px": {"type": "array", "minItems": 2, "items": point},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
    }
    geometry_dimension = {
        "type": "object", "additionalProperties": False,
        "required": ["dimension_id", "value_mm", "measured_span_start_px", "measured_span_end_px", "confidence"],
        "properties": {
            "dimension_id": {"type": "string"}, "value_mm": {"type": "number"},
            "measured_span_start_px": {"type": ["array", "null"], "items": point},
            "measured_span_end_px": {"type": ["array", "null"], "items": point},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
    }
    geometry_link = {
        "type": "object", "additionalProperties": False,
        "required": ["dimension_id", "target_wall_id", "reason", "source_reference"],
        "properties": {
            "dimension_id": {"type": "string"}, "target_wall_id": {"type": "string"},
            "reason": {"type": "string"}, "source_reference": {"type": "string"},
        },
    }
    geometry_room = {
        "type": "object", "additionalProperties": False,
        "required": ["room_geometry_id", "label", "room_label_bbox", "level_name", "boundary_points_px", "ordered_wall_ids", "dimension_ids", "dimension_wall_links", "independent_witnesses", "confidence", "confidence_score", "scale_mm_per_px", "source_pages", "source_crop", "assumptions", "conflicts", "unresolved_fields"],
        "properties": {
            "room_geometry_id": {"type": "string"}, "label": {"type": "string"}, "level_name": {"type": "string"},
            "room_label_bbox": {"type": ["array", "null"], "minItems": 4, "maxItems": 4, "items": {"type": "number"}},
            "boundary_points_px": {"type": "array", "items": point},
            "ordered_wall_ids": {"type": "array", "items": {"type": "string"}},
            "dimension_ids": {"type": "array", "items": {"type": "string"}},
            "dimension_wall_links": {"type": "array", "items": geometry_link},
            "independent_witnesses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["page", "reference"], "properties": {"page": {"type": "integer"}, "reference": {"type": "string"}}}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]}, "confidence_score": {"type": "number"},
            "scale_mm_per_px": {"type": ["number", "null"]}, "source_pages": {"type": "array", "items": {"type": "integer"}},
            "source_crop": {"type": "string"}, "assumptions": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}}, "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    geometry_page = {
        "type": "object", "additionalProperties": False,
        "required": ["page", "walls", "major_dimensions", "dimension_wall_links", "room_geometry_candidates", "conflicts"],
        "properties": {
            "page": {"type": "integer"}, "walls": {"type": "array", "items": geometry_wall},
            "major_dimensions": {"type": "array", "items": geometry_dimension},
            "dimension_wall_links": {"type": "array", "items": geometry_link},
            "room_geometry_candidates": {"type": "array", "items": geometry_room},
            "conflicts": {"type": "array", "items": {"type": "string"}},
        },
    }
    # AHU candidates are optional so older provider responses remain valid.
    # They contain observations only; numerical values are accepted by the
    # resolver only when accompanied by a cited page/source.
    air_path = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "path_id": {"type": "string"}, "tag": {"type": "string"}, "path_type": {"type": "string"},
            "zone_id": {"type": "string"}, "room_id": {"type": "string"}, "source_node": {"type": "string"},
            "destination_node": {"type": "string"}, "airflow_value": {"type": ["number", "null"]},
            "airflow_unit": {"type": "string"}, "flow_lps": {"type": ["number", "null"]},
            "page": {"type": ["integer", "null"]}, "drawing_number": {"type": "string"},
            "source": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"page": {"type": ["integer", "null"]}, "reference": {"type": "string"}, "excerpt": {"type": "string"}}}},
            "schedule": {"type": "object"}, "state": {"type": "object"}, "confidence": {"type": "string"},
            "confidence_score": {"type": ["number", "null"]}, "rationale": {"type": "string"}, "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    component = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "record_id": {"type": "string"}, "tag": {"type": "string"}, "page": {"type": ["integer", "null"]},
            "drawing_number": {"type": "string"}, "source": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"page": {"type": ["integer", "null"]}, "reference": {"type": "string"}, "excerpt": {"type": "string"}}}},
            "location": {"type": "string"}, "heat_kw": {"type": ["number", "null"]}, "sensible_kw": {"type": ["number", "null"]},
            "airflow_value": {"type": ["number", "null"]}, "airflow_unit": {"type": "string"}, "airflow_lps": {"type": ["number", "null"]},
            "source_node": {"type": "string"}, "destination_node": {"type": "string"},
            "sensible_effectiveness": {"type": ["number", "null"]}, "latent_effectiveness": {"type": ["number", "null"]},
            "reference_db_c": {"type": ["number", "null"]}, "reference_wb_c": {"type": ["number", "null"]},
            "leaving_db_c": {"type": ["number", "null"]}, "leaving_wb_c": {"type": ["number", "null"]},
            "confidence": {"type": "string"}, "confidence_score": {"type": ["number", "null"]}, "rationale": {"type": "string"},
            "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    air_side_candidate = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "ahu_id": {"type": "string"}, "system_id": {"type": "string"}, "tag": {"type": "string"}, "name": {"type": "string"},
            "system_type": {"type": "string"}, "number_off": {"type": ["number", "null"]},
            "served_zone_ids": {"type": "array", "items": {"type": "string"}}, "zone_ids": {"type": "array", "items": {"type": "string"}},
            "served_room_ids": {"type": "array", "items": {"type": "string"}}, "room_ids": {"type": "array", "items": {"type": "string"}},
            "page": {"type": ["integer", "null"]}, "drawing_number": {"type": "string"}, "source_pages": {"type": "array", "items": {"type": "integer"}},
            "source": {"type": "string"}, "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"page": {"type": ["integer", "null"]}, "reference": {"type": "string"}, "excerpt": {"type": "string"}}}},
            "airflow_records": {"type": "array", "items": air_path}, "air_paths": {"type": "array", "items": air_path}, "paths": {"type": "array", "items": air_path},
            "fans": {"type": "array", "items": component}, "duct_effects": {"type": "array", "items": component}, "leakage": {"type": "array", "items": component},
            "heat_recovery": {"type": "array", "items": component}, "preconditioning": {"type": "array", "items": component}, "coils": {"type": "array", "items": component},
            "confidence": {"type": "string"}, "confidence_score": {"type": ["number", "null"]}, "rationale": {"type": "string"}, "assumptions": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}}, "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    plant_child = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "plant_id": {"type": "string"}, "circuit_id": {"type": "string"}, "pump_id": {"type": "string"}, "pipe_id": {"type": "string"},
            "tag": {"type": "string"}, "name": {"type": "string"}, "plant_type": {"type": "string"}, "circuit_type": {"type": "string"},
            "plant_ids": {"type": "array", "items": {"type": "string"}}, "circuit_ids": {"type": "array", "items": {"type": "string"}},
            "served_ahu_ids": {"type": "array", "items": {"type": "string"}}, "ahu_ids": {"type": "array", "items": {"type": "string"}},
            "page": {"type": ["integer", "null"]}, "source": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"page": {"type": ["integer", "null"]}, "reference": {"type": "string"}, "excerpt": {"type": "string"}}}},
            "flow_value": {"type": ["number", "null"]}, "flow_unit": {"type": "string"}, "flow_lps": {"type": ["number", "null"]}, "power_kw": {"type": ["number", "null"]}, "effect_kw": {"type": ["number", "null"]},
            "number_off": {"type": ["number", "null"]}, "schedule": {"type": "object"}, "confidence": {"type": "string"}, "confidence_score": {"type": ["number", "null"]}, "rationale": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}}, "conflicts": {"type": "array", "items": {"type": "string"}}, "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    plant_record = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "plant_id": {"type": "string"}, "tag": {"type": "string"}, "name": {"type": "string"},
            "plant_type": {"type": "string"}, "number_off": {"type": ["number", "null"]},
            "duty_basis": {"type": "string"}, "capacity_kw": {"type": ["number", "null"]},
            "served_ahu_ids": {"type": "array", "items": {"type": "string"}}, "ahu_ids": {"type": "array", "items": {"type": "string"}},
            "circuit_ids": {"type": "array", "items": {"type": "string"}},
            "page": {"type": ["integer", "null"]}, "source_pages": {"type": "array", "items": {"type": "integer"}},
            "drawing_number": {"type": "string"}, "source": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"page": {"type": ["integer", "null"]}, "reference": {"type": "string"}, "excerpt": {"type": "string"}}}},
            "flow_value": {"type": ["number", "null"]}, "flow_unit": {"type": "string"}, "flow_lps": {"type": ["number", "null"]},
            "supply_temperature_c": {"type": ["number", "null"]}, "return_temperature_c": {"type": ["number", "null"]},
            "diversity_factor": {"type": ["number", "null"]}, "schedule": {"type": "object"},
            "pumps": {"type": "array", "items": plant_child}, "pipe_effects": {"type": "array", "items": plant_child},
            "circuits": {"type": "array", "items": plant_child}, "hydraulic_circuits": {"type": "array", "items": plant_child},
            "confidence": {"type": "string"}, "confidence_score": {"type": ["number", "null"]}, "rationale": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}}, "conflicts": {"type": "array", "items": {"type": "string"}}, "unresolved_fields": {"type": "array", "items": {"type": "string"}},
        },
    }
    # Responses API strict schemas require every declared property to be
    # required.  The nullable fields above let the provider explicitly return
    # an absent observation as null instead of inventing a value.
    def strict_object(node):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("properties"), dict):
            node["required"] = list(node["properties"])
            for child in node["properties"].values():
                strict_object(child)
        strict_object(node.get("items"))
    strict_object(air_side_candidate)
    strict_object(plant_child)
    strict_object(plant_record)
    return {"type": "object", "additionalProperties": False, "required": ["groups", "air_side_candidates", "plant_candidates"], "properties": {
        "groups": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["group_id", "entities", "conflicts", "missing_evidence"], "properties": {
            "group_id": {"type": "string"}, "entities": {"type": "array", "items": entity},
            "conflicts": {"type": "array", "items": {"type": "string"}}, "missing_evidence": {"type": "array", "items": {"type": "string"}},
            "geometry_pages": {"type": "array", "items": geometry_page},
        }}},
        "air_side_candidates": {"type": "array", "items": air_side_candidate},
        "plant_candidates": {"type": "array", "items": plant_record},
    }}


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _geometry_points(value):
    return isinstance(value, list) and len(value) >= 2 and all(
        isinstance(point, list) and len(point) == 2 and all(
            isinstance(coordinate, (int, float)) and not isinstance(coordinate, bool) and math.isfinite(coordinate)
            for coordinate in point
        ) for point in value
    )


def _geometry_bbox(value):
    return value is None or (
        isinstance(value, list) and len(value) == 4 and all(
            isinstance(coordinate, (int, float)) and not isinstance(coordinate, bool) and math.isfinite(coordinate)
            for coordinate in value
        )
    )


def _validate_geometry_page(page, selected_pages, group_pages):
    """Reject malformed or cross-packet AI geometry before normalization."""
    if not isinstance(page, dict) or page.get("page") not in group_pages:
        raise ValueError("Vision geometry page must belong to its selected evidence group.")
    walls = page.get("walls", [])
    dimensions = page.get("major_dimensions", [])
    links = page.get("dimension_wall_links", [])
    rooms = page.get("room_geometry_candidates", [])
    if not all(isinstance(rows, list) for rows in (walls, dimensions, links, rooms)):
        raise ValueError("Vision geometry page collections are invalid.")
    wall_ids = [str(row.get("wall_id", "")) for row in walls if isinstance(row, dict)]
    dimension_ids = [str(row.get("dimension_id", "")) for row in dimensions if isinstance(row, dict)]
    if not all(wall_ids) or len(wall_ids) != len(set(wall_ids)) or not all(dimension_ids) or len(dimension_ids) != len(set(dimension_ids)):
        raise ValueError("Vision geometry wall and dimension IDs must be unique.")
    for wall in walls:
        if not isinstance(wall, dict) or not _geometry_points(wall.get("points_px")):
            raise ValueError("Vision geometry wall needs finite endpoint geometry.")
    for dimension in dimensions:
        if not isinstance(dimension, dict) or not _positive(dimension.get("value_mm")):
            raise ValueError("Vision geometry dimension needs a positive finite value.")
    for link in links:
        if not isinstance(link, dict) or link.get("dimension_id") not in dimension_ids or link.get("target_wall_id") not in wall_ids or not str(link.get("reason", "")).strip():
            raise ValueError("Vision geometry dimension-to-wall link is invalid.")
    for room in rooms:
        if not isinstance(room, dict) or not str(room.get("label", "")).strip() or not str(room.get("level_name", "")).strip():
            raise ValueError("Vision room geometry needs a room label and level.")
        if not _geometry_bbox(room.get("room_label_bbox")):
            raise ValueError("Vision room geometry label bounding box is invalid.")
        if not room.get("boundary_points_px") and not room.get("ordered_wall_ids"):
            raise ValueError("Vision room geometry needs a boundary polygon or ordered wall sequence.")
        if room.get("boundary_points_px") and not _geometry_points(room["boundary_points_px"]):
            raise ValueError("Vision room boundary contains invalid coordinates.")
        if any(wall_id not in wall_ids for wall_id in room.get("ordered_wall_ids", [])):
            raise ValueError("Vision room geometry references an unknown wall.")
        if any(dimension_id not in dimension_ids for dimension_id in room.get("dimension_ids", [])):
            raise ValueError("Vision room geometry references an unknown dimension.")
        for link in room.get("dimension_wall_links", []):
            if not isinstance(link, dict) or link.get("dimension_id") not in dimension_ids or link.get("target_wall_id") not in wall_ids or not str(link.get("reason", "")).strip():
                raise ValueError("Vision room dimension-to-wall link is invalid.")
        if room.get("scale_mm_per_px") is not None and not _positive(room.get("scale_mm_per_px")):
            raise ValueError("Vision room scale must be positive when supplied.")
        if room.get("confidence") not in {"low", "medium", "high"} or not isinstance(room.get("confidence_score"), (int, float)) or not 0 <= room["confidence_score"] <= 1:
            raise ValueError("Vision room geometry confidence is invalid.")
        if any(
            not isinstance(witness, dict)
            or witness.get("page") not in selected_pages
            or not str(witness.get("reference", "")).strip()
            for witness in room.get("independent_witnesses", [])
        ):
            raise ValueError("Vision independent geometry witnesses must cite selected pages.")
        if not all(isinstance(page_number, int) and page_number in selected_pages for page_number in room.get("source_pages", [])):
            raise ValueError("Vision room geometry source pages must cite selected evidence pages.")


def validate_provider_output(raw, groups):
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
        raise ValueError("Vision extraction must return an object with groups.")
    group_pages = {group["group_id"]: {page["page"]: page for page in group["pages"]} for group in groups}
    found_groups = set()
    entities, conflicts, missing, geometry_pages = [], [], [], []
    packet_pages = {member["page"] for group in groups for member in group["pages"]}

    def validate_air_side_candidate(candidate):
        if not isinstance(candidate, dict):
            raise ValueError("Vision AHU candidate must be an object.")
        for page in ([candidate.get("page")] if candidate.get("page") is not None else []) + list(candidate.get("source_pages", [])):
            if page not in packet_pages:
                raise ValueError("Vision AHU candidate cites a page outside the selected packet.")
        for row in candidate.get("airflow_records", []) + candidate.get("air_paths", []) + candidate.get("paths", []):
            if row.get("page") is not None and row.get("page") not in packet_pages:
                raise ValueError("Vision AHU airflow path cites a page outside the selected packet.")
            for evidence in row.get("evidence", []):
                if evidence.get("page") is not None and evidence.get("page") not in packet_pages:
                    raise ValueError("Vision AHU airflow evidence cites a page outside the selected packet.")
        for kind in ("fans", "duct_effects", "leakage", "heat_recovery", "preconditioning", "coils"):
            for row in candidate.get(kind, []):
                if row.get("page") is not None and row.get("page") not in packet_pages:
                    raise ValueError("Vision AHU component cites a page outside the selected packet.")
        return candidate
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
            if row.get("preliminary_profile_id", "") not in {"", "retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"}:
                raise ValueError("Vision extraction preliminary profile selection is invalid.")
            if row.get("room_use_category", "") not in {"", "dining", "kitchen", "retail", "office", "storage", "ancillary_conditioned", "plant_or_unconditioned", "refrigeration_process", "residential", "generic_conditioned"}:
                raise ValueError("Vision extraction room-use category is invalid.")
            if not isinstance(row.get("room_use_rationale", ""), str) or not isinstance(row.get("room_use_alternatives", []), list):
                raise ValueError("Vision extraction room-use evidence is invalid.")
            for key in ("area_m2", "ceiling_height_mm", "width_mm", "height_mm"):
                if row.get(key) is not None and not _positive(row[key]):
                    raise ValueError(f"Vision extraction {key} must be positive when present.")
            for key in ("occupancy_count", "seat_count", "workstation_count"):
                value = row.get(key)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                    raise ValueError(f"Vision extraction {key} must be a non-negative integer when present.")
            for key in ("lighting_fixtures", "equipment"):
                values = row.get(key, [])
                if not isinstance(values, list):
                    raise ValueError(f"Vision extraction {key} must be a list.")
                for item in values:
                    if not isinstance(item, dict) or item.get("page") not in packet_pages or not str(item.get("excerpt", "")).strip():
                        raise ValueError(f"Vision extraction {key} needs a cited selected-page excerpt.")
                    if item.get("drawing_number", "") != group_pages[group_id][item["page"]].get("drawing_number", ""):
                        raise ValueError(f"Vision extraction {key} drawing identity does not match its cited page.")
                    numeric_keys = ("quantity", "wattage_w") if key == "lighting_fixtures" else ("quantity", "rated_input_w", "watts", "heat_to_space_factor", "diversity_factor")
                    for numeric_key in numeric_keys:
                        value = item.get(numeric_key)
                        is_factor = numeric_key in {"heat_to_space_factor", "diversity_factor"}
                        valid_factor = is_factor and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1
                        if value is not None and not _positive(value) and not valid_factor:
                            raise ValueError(f"Vision extraction {key} {numeric_key} is invalid.")
            witnesses = row.get("witnesses")
            if not isinstance(witnesses, list) or any(not isinstance(item, dict) or item.get("page") not in {member["page"] for g in groups for member in g["pages"]} or not item.get("reference") for item in witnesses):
                raise ValueError("Vision extraction witnesses must cite packet pages and references.")
            record = deepcopy(row)
            record["group_id"] = group_id
            record["auto_activate"] = record["geometry_status"] == "geometry_confirmed" and len({(item.get("page"), item.get("reference")) for item in witnesses}) >= 2 and not record.get("unresolved_fields")
            record["candidate_fingerprint"] = fingerprint({key: value for key, value in record.items() if key != "auto_activate"})
            entities.append(record)
        for geometry_page in group.get("geometry_pages", []):
            _validate_geometry_page(geometry_page, {member["page"] for values in group_pages.values() for member in values.values()}, group_pages[group_id])
            record = deepcopy(geometry_page)
            record["page_role"] = group_pages[group_id][record["page"]].get("role", "not_geometry")
            geometry_pages.append(record)
        conflicts.extend({"group_id": group_id, "reason": str(item)} for item in group["conflicts"])
        missing.extend({"group_id": group_id, "reason": str(item)} for item in group["missing_evidence"])
    if found_groups != set(group_pages):
        raise ValueError("Vision extraction did not return every selected page group.")
    air_side_candidates = [validate_air_side_candidate(row) for row in raw.get("air_side_candidates", [])]
    plant_candidates = []
    for row in raw.get("plant_candidates", []):
        if not isinstance(row, dict):
            raise ValueError("Vision plant candidate must be an object.")
        for page in ([row.get("page")] if row.get("page") is not None else []) + list(row.get("source_pages", [])):
            if page not in packet_pages:
                raise ValueError("Vision plant candidate cites a page outside the selected packet.")
        for evidence in row.get("evidence", []):
            if evidence.get("page") is not None and evidence.get("page") not in packet_pages:
                raise ValueError("Vision plant evidence cites a page outside the selected packet.")
        plant_candidates.append(deepcopy(row))
    return {"entities": entities, "conflicts": conflicts, "missing_evidence": missing, "geometry_pages": geometry_pages, "air_side_candidates": air_side_candidates, "plant_candidates": plant_candidates}


def vision_response_from_extraction(validated, source_fingerprint_value, model):
    return {
        "provider": "openai_responses", "model": model, "source": "automated_project_opt_in", "store": False,
        "source_fingerprint": source_fingerprint_value,
        "air_side_candidates": validated.get("air_side_candidates", []),
        "plant_candidates": validated.get("plant_candidates", []),
        "result": {
            "geometry_review": {"pages": validated.get("geometry_pages", [])},
            "auto_extraction": {"entities": validated["entities"], "conflicts": validated["conflicts"], "missing_evidence": validated["missing_evidence"], "air_side_candidates": validated.get("air_side_candidates", []), "plant_candidates": validated.get("plant_candidates", [])},
            "air_side_candidates": validated.get("air_side_candidates", []),
            "plant_candidates": validated.get("plant_candidates", []),
        },
    }
