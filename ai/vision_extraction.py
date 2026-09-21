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


def estimate(settings, groups, cost_per_group_aud=None):
    chosen = settings.get("selected_group_ids") or [group["group_id"] for group in groups]
    selected = [group for group in groups if group["group_id"] in chosen]
    page_count = sum(len(group.get("pages", [])) for group in selected)
    if cost_per_group_aud is None:
        estimated = None
    else:
        estimated = round(len(selected) * float(cost_per_group_aud), 2)
    return {
        "group_count": len(selected), "request_count": len(selected), "page_count": page_count,
        "selected_group_ids": [group["group_id"] for group in selected],
        "estimated_cost_aud": estimated,
        "estimate_available": estimated is not None,
        "within_budget": estimated is not None and settings.get("max_budget_aud") is not None and estimated <= settings["max_budget_aud"],
    }


def extraction_schema():
    """Strict, bounded output shape accepted from the provider."""
    entity = {
        "type": "object", "additionalProperties": False,
        "required": ["kind", "page", "drawing_number", "label", "level_name", "area_m2", "ceiling_height_mm", "width_mm", "height_mm", "unit", "geometry_status", "boundary_reference", "opening_tag", "surface_kind", "orientation", "preliminary_profile_id", "witnesses", "excerpt", "confidence", "unresolved_fields"],
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
            # Optional controlled vocabulary only. The provider never returns
            # thermal numbers; the preliminary assembler maps this selection
            # to its local, versioned assumption pack.
            "preliminary_profile_id": {"type": "string", "enum": ["", "retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"]},
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
            if row.get("preliminary_profile_id", "") not in {"", "retail", "office", "hospitality", "storage", "residential", "generic_conditioned_room"}:
                raise ValueError("Vision extraction preliminary profile selection is invalid.")
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
