"""Deterministic fusion of architect-page evidence into a cited review artifact.

This module deliberately produces proposals and conflicts only.  It never
approves geometry or engineering inputs and never calculates loads.
"""

from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from ai.drawing_coverage import source_fingerprint, timestamp
from ai.fact_registry import registry_from_fusion
from ai.geometry_resolution import build_geometry_resolution


ROLE_ALIASES = {
    "main_floor_plan": "primary_geometry_plan",
    "supporting_geometry_plan": "supporting_geometry_plan",
    "reflected_ceiling_plan": "reflected_ceiling_or_service_plan",
    "services_or_lighting_plan": "reflected_ceiling_or_service_plan",
    "opening_elevation": "opening_elevation",
    "opening_schedule": "opening_schedule",
    "elevation": "elevation_or_section",
    "section": "elevation_or_section",
    "detail": "construction_or_detail",
    "reference": "legend_or_general_notes",
    "3d_render": "3d_reference",
}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _stable_id(fingerprint, page, drawing_number, floor, label, location=""):
    key = "|".join(str(part or "") for part in (fingerprint, page, drawing_number, floor, label, location))
    return "evidence_" + hashlib.sha256(key.encode()).hexdigest()[:20]


def _role(page, coverage_role):
    raw = coverage_role.get("proposed_role") or page.get("plan_role") or page.get("sheet_classification") or "excluded"
    return ROLE_ALIASES.get(raw, raw)


def _page_register(ai_input, coverage):
    fp = source_fingerprint(ai_input)
    roles = {item.get("page"): item for item in coverage.get("page_roles", [])}
    rows = []
    for page in ai_input.get("drawing_set", {}).get("pages", []):
        role = roles.get(page.get("page"), {})
        proposed = _role(page, role)
        rows.append({
            "page": page.get("page"), "drawing_number": page.get("drawing_number", ""),
            "drawing_number_candidates": (role.get("identity") or {}).get("drawing_number_candidates", []),
            "title_candidates": (role.get("identity") or {}).get("title_candidates", []),
            "identity_status": (role.get("identity") or {}).get("status", role.get("drawing_number_status", "missing")),
            "title": page.get("title", ""), "proposed_role": proposed,
            "level_candidate": page.get("level_name") or role.get("level_name", ""),
            "confidence": role.get("confidence", page.get("confidence", 0)),
            "classification_evidence": role.get("classification_evidence", page.get("classification_evidence", [])),
            "geometry_eligible": bool(role.get("geometry_eligible", proposed in {"primary_geometry_plan", "supporting_geometry_plan"})),
            "opening_geometry_eligible": bool(role.get("opening_geometry_eligible", proposed == "opening_elevation")),
            "reference_only": bool(role.get("reference_only", proposed in {"legend_or_general_notes", "construction_or_detail", "3d_reference"})),
            "authority_status": role.get("authority_status", "proposed"),
            "capabilities": role.get("capabilities", []),
            "capability_map": role.get("capability_map", {}),
            "relevance": role.get("relevance", {}),
            "selection": role.get("selection", "reference_only"),
            "selection_reasons": role.get("selection_reasons", []),
            "visual_available": role.get("visual_available", False),
            "text_available": role.get("text_available", False),
            "vector_available": role.get("vector_available", False),
            "page_group": role.get("page_group", ""),
            "source_fingerprint": fp,
            "source": {"page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "kind": "architect_pdf_page"},
        })
    return rows


def _citation(item, page_map):
    evidence = (item.get("evidence") or [{}])[0]
    page = page_map.get(evidence.get("page"), {})
    return {**evidence, "drawing_number": evidence.get("drawing_number") or page.get("drawing_number", ""),
            "source": "architect_pdf"}


def _normalise_label(value):
    return " ".join(str(value or "").casefold().split())


def _opening_tag(entity):
    return str(entity.get("value", {}).get("tag", "")).upper().strip()


def _direct_geometry(entity):
    geometry = entity.get("value", {}).get("geometry") or {}
    dimensions = entity.get("value", {}).get("dimensions") or {}
    return bool(geometry.get("direct_dimension") and dimensions.get("width_mm") and dimensions.get("height_mm"))


def _surface_label(entity):
    value = entity.get("value", {})
    return _normalise_label(value.get("surface_label") or value.get("adjacency") or entity.get("label"))


def reconcile_plan_elevation_geometry(entities, pages):
    """Link only unique, directly evidenced plan/elevation opening relationships."""
    page_map = {row["page"]: row for row in pages}
    plan_openings = defaultdict(list)
    elevation_openings = []
    plan_surfaces = defaultdict(list)
    elevation_surfaces, boundary_surfaces = [], []
    for entity in entities:
        page = page_map.get(entity["source"].get("page"), {})
        if entity["kind"] == "opening":
            tag = _opening_tag(entity)
            if page.get("geometry_eligible") and tag:
                plan_openings[tag].append(entity)
            if page.get("opening_geometry_eligible") and tag and _direct_geometry(entity):
                elevation_openings.append(entity)
        elif entity["kind"] == "surface":
            label = _surface_label(entity)
            geometry = entity.get("value", {}).get("geometry") or {}
            if page.get("geometry_eligible") and label:
                plan_surfaces[label].append(entity)
            if page.get("opening_geometry_eligible") and label in {"shopfront", "storefront", "frontage"}:
                boundary_surfaces.append(entity)
            if page.get("opening_geometry_eligible") and geometry.get("direct_dimension") and label:
                elevation_surfaces.append(entity)

    relationships, conflicts, review_items = [], [], []
    for elevation in elevation_openings:
        tag = _opening_tag(elevation)
        matches = plan_openings[tag]
        if len(matches) == 1:
            plan = matches[0]
            elevation["value"]["geometry"]["unique_target"] = True
            elevation["value"]["geometry"]["matched_plan_evidence_id"] = plan["evidence_ids"][0]
            elevation["value"]["geometry"]["auto_activation_basis"] = "direct_dimension_unique_plan_tag"
            relationships.append({
                "relationship_id": "opening_match_" + _fingerprint([plan["entity_id"], elevation["entity_id"]])[:16],
                "kind": "plan_elevation_opening_match", "from_entity_id": plan["entity_id"],
                "to_entity_id": elevation["entity_id"], "status": "matched",
                "basis": "exact_opening_tag", "pages": sorted({plan["source"].get("page"), elevation["source"].get("page")}),
            })
        else:
            kind = "opening_plan_mapping_missing" if not matches else "opening_plan_mapping_ambiguous"
            conflicts.append({
                "conflict_id": "conflict_" + _fingerprint([kind, tag, elevation["entity_id"], [row["entity_id"] for row in matches]])[:16],
                "kind": kind, "label": tag, "entity_ids": [elevation["entity_id"], *[row["entity_id"] for row in matches]],
                "pages": sorted({elevation["source"].get("page"), *[row["source"].get("page") for row in matches]}),
                "status": "review_required",
                "reason": "A directly dimensioned elevation opening could not be matched to exactly one plan opening.",
            })
    for elevation in elevation_surfaces:
        label = _surface_label(elevation)
        matches = plan_surfaces[label]
        if len(matches) == 1:
            plan = matches[0]
            elevation["value"]["geometry"]["matched_plan_evidence_id"] = plan["evidence_ids"][0]
            relationships.append({
                "relationship_id": "surface_match_" + _fingerprint([plan["entity_id"], elevation["entity_id"]])[:16],
                "kind": "plan_elevation_parent_surface_match", "from_entity_id": plan["entity_id"],
                "to_entity_id": elevation["entity_id"], "status": "matched",
                "basis": "exact_surface_label", "pages": sorted({plan["source"].get("page"), elevation["source"].get("page")}),
            })
        elif matches:
            conflicts.append({
                "conflict_id": "conflict_" + _fingerprint(["surface_mapping", label, elevation["entity_id"]])[:16],
                "kind": "surface_plan_mapping_ambiguous", "label": label,
                "entity_ids": [elevation["entity_id"], *[row["entity_id"] for row in matches]],
                "pages": sorted({elevation["source"].get("page"), *[row["source"].get("page") for row in matches]}),
                "status": "review_required",
                "reason": "A named elevation surface matches more than one plan surface.",
            })
    for surface in boundary_surfaces:
        review_items.append({
            "item_id": "fusion_issue_" + _fingerprint(["boundary", surface["entity_id"]])[:16],
            "affected_id": surface["entity_id"], "status": "blocked", "field": "boundary_method",
            "source_artifact": "architect_evidence_fusion.json", "page": surface["source"].get("page"),
            "reason": "Storefront geometry is evidenced, but its thermal boundary is not established.",
            "remediation": "Confirm whether the storefront faces outdoor air or an adjacent conditioned/unconditioned space before thermal use.",
        })
    return relationships, conflicts, review_items


def build_evidence_fusion(ai_input, coverage=None, building=None, spatial_ocr=None,
                          vector_geometry=None, vision_response=None,
                          dimension_matches=None, geometry_confirmation=None):
    """Build a stable, proposal-only evidence graph from all architect pages."""
    coverage = coverage or {}
    building = building or {}
    fp = source_fingerprint(ai_input)
    pages = _page_register(ai_input, coverage)
    page_map = {row["page"]: row for row in pages}
    entities = []
    family_map = {"spaces": "room", "levels": "floor", "openings": "opening", "surfaces": "surface",
                  "constructions": "construction", "lighting": "lighting", "equipment": "equipment"}
    for family, kind in family_map.items():
        for item in building.get(family, []):
            citation = _citation(item, page_map)
            page = citation.get("page")
            # ``drawing_coverage`` creates an explicit ``Unassigned level``
            # placeholder when no sheet carries a reliable level identity.
            # Keep that state in the review queue, but do not turn the
            # synthetic placeholder into a source-backed floor fact.  The
            # fact registry is intentionally strict about provenance.
            if kind == "floor" and not citation.get("page") and not citation.get("reference"):
                continue
            label = item.get("name") or item.get("tag") or item.get("reference") or item.get("kind") or item.get("id")
            entity = {
                "entity_id": _stable_id(fp, page, citation.get("drawing_number"), item.get("level_name"), label, item.get("geometry_reference", "")),
                "kind": kind, "label": label, "value": deepcopy(item), "status": "proposal",
                "confidence": item.get("confidence", "unknown"), "extraction_method": item.get("extraction_method", "structured_pdf"),
                "geometry_status": item.get("geometry_status"), "source": citation,
                "citations": [citation], "evidence_ids": [item.get("id", "")],
            }
            entities.append(entity)

    by_label = defaultdict(list)
    for entity in entities:
        if entity["kind"] in {"room", "floor"} and entity["label"]:
            by_label[str(entity["label"]).casefold()].append(entity)
    conflicts = []
    for label, matches in by_label.items():
        pages_for_label = {row["source"].get("page") for row in matches}
        levels = {row["value"].get("level_name", "") for row in matches}
        if len(pages_for_label) > 1 and (len(levels) > 1 or any(row["kind"] == "room" for row in matches)):
            conflicts.append({"conflict_id": "conflict_" + _fingerprint([label, sorted(pages_for_label)])[:16],
                              "kind": "duplicate_or_ambiguous_identity", "label": label,
                              "entity_ids": [row["entity_id"] for row in matches],
                              "pages": sorted(pages_for_label), "status": "review_required",
                              "reason": "Matching labels occur on multiple architect pages or levels; confirm identity before activation."})

    relationships, geometry_conflicts, geometry_review_items = reconcile_plan_elevation_geometry(entities, pages)
    conflicts.extend(geometry_conflicts)
    geometry_resolution = build_geometry_resolution(
        ai_input, coverage, building, spatial_ocr, vector_geometry,
        dimension_matches=dimension_matches, geometry_confirmation=geometry_confirmation, vision_response=vision_response,
    )
    # Keep the existing entity relationship contract while adding the broader
    # page/witness graph.  The new graph is evidence-only and cannot activate
    # calculator inputs by itself.
    relationships.extend(geometry_resolution["relationships"])
    conflicts.extend(geometry_resolution["conflicts"])
    geometry_review_items.extend(geometry_resolution["review_items"])
    for page in pages:
        same_drawing = [other["page"] for other in pages if other["page"] != page["page"] and other["drawing_number"] and other["drawing_number"] == page["drawing_number"]]
        if same_drawing:
            relationships.append({"relationship_id": "page_link_" + _fingerprint([page["page"], same_drawing])[:16],
                                  "from_page": page["page"], "to_pages": same_drawing[:12],
                                  "kind": "same_drawing_number", "status": "proposed"})

    review_items = list(geometry_review_items)
    for level in building.get("levels", []):
        evidence = level.get("evidence") or []
        if not any(row.get("page") or row.get("reference") for row in evidence if isinstance(row, dict)):
            review_items.append({
                "item_id": "fusion_issue_" + _fingerprint(["floor_identity", level.get("id", "unassigned")])[:16],
                "affected_id": level.get("id", "unassigned"),
                "status": "blocked",
                "field": "floor",
                "source_artifact": "building_evidence.json",
                "page": None,
                "reason": "No architect page provides a reliable floor identity for this coverage group.",
                "remediation": "Link a dimensioned plan, elevation, or section that names the level before activating a floor.",
            })
    for entity in entities:
        value = entity["value"]
        if entity["kind"] == "room":
            missing = list(value.get("unresolved_fields", []))
            if value.get("geometry_status") != "geometry_confirmed" and "geometry" not in missing:
                missing.append("geometry")
            if not value.get("level_name") and "floor" not in missing:
                missing.append("floor")
            for field in missing:
                review_items.append({"item_id": "fusion_issue_" + _fingerprint([entity["entity_id"], field])[:16],
                                     "affected_id": entity["entity_id"], "status": "blocked", "field": field,
                                     "source_artifact": "architect_evidence_fusion.json", "page": entity["source"].get("page"),
                                     "reason": f"Room evidence is missing reviewed {field}.", "remediation": "Review the cited architect page or leave unresolved."})
        elif entity["kind"] == "opening":
            geometry = value.get("geometry") or {}
            dimensions = value.get("dimensions") or {}
            if geometry.get("direct_dimension") and not dimensions.get("unit"):
                review_items.append({
                    "item_id": "fusion_issue_" + _fingerprint([entity["entity_id"], "dimension_unit"])[:16],
                    "affected_id": entity["entity_id"], "status": "blocked", "field": "dimension_unit",
                    "source_artifact": "architect_evidence_fusion.json", "page": entity["source"].get("page"),
                    "reason": "Opening dimensions are printed, but the unit is not explicit in the cited evidence.",
                    "remediation": "Link a cited unit note or leave the opening geometry inactive.",
                })
    review_items.extend({"item_id": item["conflict_id"], "affected_id": item["conflict_id"], "status": "blocked",
                         "source_artifact": "architect_evidence_fusion.json", "page": (item.get("pages") or [None])[0],
                         "reason": item["reason"], "remediation": "Resolve the duplicate identity in the evidence-to-calculator review."} for item in conflicts)
    entities.sort(key=lambda row: row["entity_id"])
    conflicts.sort(key=lambda row: row["conflict_id"])
    relationships.sort(key=lambda row: row["relationship_id"])
    review_items.sort(key=lambda row: row["item_id"])
    fusion = {
        "schema_version": 2, "source_pdf": ai_input.get("source_pdf", ""), "source_fingerprint": fp,
        "generated_from": ["ai_input.json", "drawing_coverage.json", "building_evidence.json", "spatial_ocr.json", "vector_geometry.json", "vision_response.json"],
        "generated_at": timestamp(), "pages": pages, "entities": entities, "relationships": relationships,
        "geometry_resolution": geometry_resolution,
        "conflicts": conflicts, "review_items": review_items,
        "sources": {"spatial_ocr": bool(spatial_ocr), "vector_geometry": bool(vector_geometry), "vision_response": bool(vision_response)},
        "activation_policy": "two_tier_metadata_only",
        "fingerprint": "",
    }
    registry = registry_from_fusion(fusion)
    fusion["facts"] = registry["facts"]
    fusion["fact_registry"] = {"schema_version": registry["schema_version"], "revision": registry.get("revision", 0),
                                "source_fingerprint": fp, "fact_count": len(registry["facts"]),
                                "fingerprint": registry.get("fingerprint", "")}
    fusion["fingerprint"] = _fingerprint({"source": fp, "pages": pages, "entities": entities,
                                           "facts": fusion["facts"], "relationships": relationships,
                                           "geometry_resolution": geometry_resolution, "conflicts": conflicts})
    return fusion
