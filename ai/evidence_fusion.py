"""Deterministic fusion of architect-page evidence into a cited review artifact.

This module deliberately produces proposals and conflicts only.  It never
approves geometry or engineering inputs and never calculates loads.
"""

from collections import defaultdict
import hashlib
import json

from ai.drawing_coverage import source_fingerprint, timestamp
from ai.fact_registry import registry_from_fusion


ROLE_ALIASES = {
    "main_floor_plan": "primary_geometry_plan",
    "supporting_geometry_plan": "supporting_geometry_plan",
    "reflected_ceiling_plan": "reflected_ceiling_or_service_plan",
    "services_or_lighting_plan": "reflected_ceiling_or_service_plan",
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
            "title": page.get("title", ""), "proposed_role": proposed,
            "level_candidate": page.get("level_name") or role.get("level_name", ""),
            "confidence": role.get("confidence", page.get("confidence", 0)),
            "classification_evidence": role.get("classification_evidence", page.get("classification_evidence", [])),
            "geometry_eligible": bool(role.get("geometry_eligible", proposed in {"primary_geometry_plan", "supporting_geometry_plan"})),
            "reference_only": bool(role.get("reference_only", proposed in {"legend_or_general_notes", "construction_or_detail", "3d_reference"})),
            "authority_status": role.get("authority_status", "proposed"),
            "source_fingerprint": fp,
            "source": {"page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "kind": "architect_pdf_page"},
        })
    return rows


def _citation(item, page_map):
    evidence = (item.get("evidence") or [{}])[0]
    page = page_map.get(evidence.get("page"), {})
    return {**evidence, "drawing_number": evidence.get("drawing_number") or page.get("drawing_number", ""),
            "source": "architect_pdf"}


def build_evidence_fusion(ai_input, coverage=None, building=None, spatial_ocr=None,
                          vector_geometry=None, vision_response=None):
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
            label = item.get("name") or item.get("tag") or item.get("reference") or item.get("kind") or item.get("id")
            entity = {
                "entity_id": _stable_id(fp, page, citation.get("drawing_number"), item.get("level_name"), label, item.get("geometry_reference", "")),
                "kind": kind, "label": label, "value": item, "status": "proposal",
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

    relationships = []
    for page in pages:
        same_drawing = [other["page"] for other in pages if other["page"] != page["page"] and other["drawing_number"] and other["drawing_number"] == page["drawing_number"]]
        if same_drawing:
            relationships.append({"relationship_id": "page_link_" + _fingerprint([page["page"], same_drawing])[:16],
                                  "from_page": page["page"], "to_pages": same_drawing[:12],
                                  "kind": "same_drawing_number", "status": "proposed"})

    review_items = []
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
                                           "facts": fusion["facts"], "conflicts": conflicts})
    return fusion
