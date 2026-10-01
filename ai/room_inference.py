"""Local, evidence-only room inference for the contractor workflow.

This adapter deliberately does not invent boundaries or areas.  It extracts
room-use clues from ranked plan/RCP pages and leaves geometry proof to the
existing geometry resolver.  Every candidate is retained with its source page
and a confidence that is useful for review prioritisation.
"""

from __future__ import annotations

import hashlib
import json
import re

from ai.room_use_resolution import room_identity
from ai.drawing_coverage import has_current_level_classification


VERSION = "local-room-inference-v3"
PLAN_ROLES = {
    "main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan",
    "uncertain_top_down_context", "reflected_ceiling_plan", "reflected_ceiling",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _text(value):
    return " ".join(str(value or "").split())


def _page_selected(page):
    role = str(page.get("plan_role") or page.get("proposed_role") or page.get("sheet_classification") or "").casefold()
    detected = str(page.get("detected_type") or "").casefold()
    structured = str((page.get("structured_content") or {}).get("markdown", "")).casefold()
    title_block_layout = any(term in structured for term in (
        "proposed floor layout", "proposed shop floor layout", "existing shop floor layout",
    ))
    return title_block_layout or detected in {"floor_plan", "reflected_ceiling_plan"} or role in PLAN_ROLES


def select_pages(ai_input, coverage=None):
    """Select ranked plans/RCPs and their explicitly linked context pages."""
    drawing_pages = ((ai_input or {}).get("drawing_set") or {}).get("pages", [])
    coverage_pages = ({page.get("page"): page for page in coverage.get("pages", [])
                       if isinstance(page, dict)} if isinstance(coverage, dict)
                      and has_current_level_classification(coverage) else {})
    selected = [page for page in drawing_pages if isinstance(page, dict) and _page_selected(page)]
    if not selected and isinstance(coverage, dict):
        selected = [page for page in coverage.get("page_roles", []) if isinstance(page, dict) and _page_selected(page)]
    selected_numbers = {page.get("page") for page in selected}
    links = (coverage or {}).get("page_relationships", []) if isinstance(coverage, dict) else []
    linked_numbers = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        pages = [value for value in link.get("pages", link.get("page_numbers", [])) if value is not None]
        if not pages:
            pages = [value for value in (link.get("from_page"), link.get("to_page")) if value is not None]
        if selected_numbers.intersection(pages):
            linked_numbers.update(pages)
    by_number = {page.get("page"): page for page in drawing_pages if isinstance(page, dict)}
    selected = [{**page, "level_name": coverage_pages.get(page.get("page"), {}).get("level_name", ""),
                 "level_status": coverage_pages.get(page.get("page"), {}).get("level_status", "missing")}
                for page in selected]
    selected_numbers = {page.get("page") for page in selected}
    for number in sorted(linked_numbers):
        page = by_number.get(number)
        if page and number not in selected_numbers:
            selected.append({**page, "level_name": coverage_pages.get(number, {}).get("level_name", ""),
                             "level_status": coverage_pages.get(number, {}).get("level_status", "missing")})
            selected_numbers.add(number)
    return sorted(selected, key=lambda page: (page.get("page") is None, page.get("page")))


def _markdown(page):
    content = page.get("structured_content") or {}
    return _text(content.get("markdown") or page.get("text") or page.get("ocr_text"))


def _level(page):
    return _text(page.get("level_name") or page.get("level") or page.get("floor")) or "Unassigned level"


def _candidate_labels(text):
    # Long phrases first prevent `bar` from being extracted out of a longer
    # label.  These are room-use clues, not proof of a closed room boundary.
    phrases = [
        "cool room", "coolroom", "freezer", "food preparation", "back of house",
        "dining", "restaurant", "kitchen", "bar", "store room", "store",
        "stockroom", "office", "meeting", "reception", "amenity", "toilet",
        "bathroom", "corridor", "foyer", "showroom", "retail", "shop",
        "bedroom", "living", "lounge",
    ]
    found = []
    for phrase in phrases:
        if re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text, re.I):
            label = " ".join(word.capitalize() for word in phrase.split())
            if label.casefold() not in {value.casefold() for value in found}:
                found.append(label)
    return found


def _label_key(label):
    return re.sub(r"[^a-z0-9]", "", str(label or "").casefold())


def _area_near_label(text, label):
    match = re.search(r"(?<![a-z0-9])" + re.escape(label) + r"[^\n]{0,80}?([0-9]+(?:\.[0-9]+)?)\s*(?:m2|m²|sqm)\b", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    return value if value > 0 else None


def _geometry_rows(page, page_number):
    """Return structured geometry candidates already attached to a page.

    The local interpreter is not allowed to invent a polygon from visual
    proportions.  It can, however, carry through geometry produced by the
    PDF/vector or vision extraction layers so the shared geometry resolver
    can perform the authoritative validation and area calculation.
    """
    rows = []
    for key in ("room_geometry_candidates", "room_boundaries", "rooms"):
        values = page.get(key, []) if isinstance(page, dict) else []
        if isinstance(values, list):
            rows.extend(value for value in values if isinstance(value, dict))
        if rows:
            break
    output = []
    for raw in rows:
        label = _text(raw.get("label") or raw.get("room_label") or raw.get("name"))
        if not label:
            continue
        points = raw.get("boundary_points_px") or raw.get("polygon_points_px") or raw.get("points_px") or raw.get("boundary_points") or []
        geometry = {
            "boundary_points_px": points,
            "wall_ids": list(raw.get("wall_ids") or raw.get("ordered_wall_ids") or []),
            "dimension_ids": list(raw.get("dimension_ids") or []),
            "dimension_wall_links": list(raw.get("dimension_wall_links") or []),
            "scale_mm_per_px": raw.get("scale_mm_per_px") or raw.get("mm_per_px"),
            "scale_source": raw.get("scale_source") or "linked_dimension_or_pdf_scale",
            "walls": raw.get("walls") or [],
            "dimensions": raw.get("dimensions") or raw.get("major_dimensions") or [],
            "source_crop": raw.get("source_crop", ""),
            "independent_witnesses": raw.get("independent_witnesses") or [],
        }
        if raw.get("area_m2") is not None:
            geometry["area_m2"] = raw.get("area_m2")
            geometry["formula"] = raw.get("formula", "explicit_room_area")
            geometry["operands"] = raw.get("operands", {"value_m2": raw.get("area_m2")})
        if raw.get("test_fixture_only"):
            geometry["test_fixture_only"] = True
            geometry["fixture_id"] = raw.get("fixture_id", "")
        output.append({
            "label": label,
            "level_name": _level(raw) if isinstance(raw, dict) else _level(page),
            "page": raw.get("page", page_number),
            "source_pages": list(raw.get("source_pages") or [raw.get("page", page_number)]),
            "area_m2": raw.get("area_m2"),
            "preliminary_profile_id": raw.get("preliminary_profile_id", ""),
            "space_scope": raw.get("space_scope", ""),
            "geometry": geometry,
            "confidence": raw.get("confidence", "medium"),
            "confidence_score": raw.get("confidence_score"),
            "room_label_bbox": raw.get("room_label_bbox") or raw.get("label_bbox") or raw.get("label_bounding_box"),
            "rationale": raw.get("rationale", "Structured PDF/vector room geometry candidate; shared geometry validation determines eligibility."),
            "unresolved_fields": list(raw.get("unresolved_fields") or []),
            "conflicts": list(raw.get("conflicts") or []),
            "evidence": list(raw.get("evidence") or [{"page": raw.get("page", page_number), "excerpt": label}]),
        })
    return output


def _vision_geometry_pages(vision_response):
    """Expose provider geometry pages in the local room proposal shape."""
    if not isinstance(vision_response, dict):
        return {}
    result = vision_response.get("result", {}) if isinstance(vision_response.get("result", {}), dict) else {}
    pages = (result.get("geometry_review", {}) or {}).get("pages", [])
    return {row.get("page"): row for row in pages if isinstance(row, dict) and row.get("page") is not None}


def infer(ai_input, coverage=None, spatial_ocr=None, vector_geometry=None, vision_response=None):
    pages = select_pages(ai_input, coverage)
    candidates = {}
    spatial_by_page = {
        row.get("page"): row for row in (spatial_ocr or {}).get("pages", [])
        if isinstance(row, dict) and row.get("page") is not None
    }
    vector_by_page = {
        row.get("page"): row for row in ((vector_geometry or {}).get("geometry_key_points") or {}).get("pages", [])
        if isinstance(row, dict) and row.get("page") is not None
    }
    vision_by_page = _vision_geometry_pages(vision_response)

    def add_candidate(label, level, number, evidence, confidence=0.68, geometry=None, metadata=None):
        key = room_identity(_label_key(label), level)
        row = candidates.setdefault(key, {
            "kind": "room", "room_id": key, "label": label, "level_name": level,
            "source_pages": [], "evidence": [], "confidence": 0.0,
            "rationale": "Room-use clue extracted from a ranked plan/RCP page; closed geometry requires downstream validation.",
            "unresolved_fields": ["closed_boundary", "room_area"], "geometry": {},
        })
        row["confidence"] = max(row["confidence"], min(0.95, confidence if isinstance(confidence, (int, float)) else 0.68))
        if number is not None and number not in row["source_pages"]:
            row["source_pages"].append(number)
        if evidence and evidence not in row["evidence"]:
            row["evidence"].append(evidence)
        if geometry:
            row["geometry"] = geometry
            area = (metadata or {}).get("area_m2", geometry.get("area_m2"))
            if isinstance(area, (int, float)):
                row["area_m2"] = area
            row["rationale"] = (metadata or {}).get("rationale", row["rationale"])
            row["room_label_bbox"] = (metadata or {}).get("room_label_bbox")
            row["conflicts"] = list((metadata or {}).get("conflicts") or [])
            row["unresolved_fields"] = sorted(set((metadata or {}).get("unresolved_fields") or []))
            if (metadata or {}).get("test_fixture_only"):
                row["test_fixture_only"] = True
                row["origin"] = "test_fixture_assumption"
            if (metadata or {}).get("preliminary_profile_id"):
                row["preliminary_profile_id"] = metadata["preliminary_profile_id"]
            if (metadata or {}).get("space_scope"):
                row["space_scope"] = metadata["space_scope"]
            row["page"] = number
            row["geometry_source"] = "structured_pdf_or_vision_candidate"
        return row

    # Carry geometry candidates from the PDF/vector/vision preprocessing
    # layer into the same normalized room proposal used by the resolver.
    for page in pages:
        number = page.get("page")
        sources = [
            _geometry_rows(page, number),
            _geometry_rows(vision_by_page.get(number, {}), number),
            _geometry_rows(spatial_by_page.get(number, {}), number),
            _geometry_rows(vector_by_page.get(number, {}), number),
        ]
        for geometry_row in next((rows for rows in sources if rows), []):
            level = _text(geometry_row.get("level_name")) or _level(page)
            evidence = (geometry_row.get("evidence") or [{"page": number, "excerpt": geometry_row["label"]}])[0]
            score = geometry_row.get("confidence_score")
            confidence = score if isinstance(score, (int, float)) else {"high": 0.9, "medium": 0.7, "low": 0.4}.get(str(geometry_row.get("confidence", "medium")).casefold(), 0.6)
            add_candidate(geometry_row["label"], level, number, evidence, confidence, geometry_row.get("geometry"), geometry_row)

    # Linked elevations/schedules/sections are retained as context evidence,
    # but only ranked plan/RCP pages can originate a room label in this first
    # pass. This prevents title-block and detail text from becoming rooms.
    candidate_pages = [page for page in pages if _page_selected(page)]
    for page in candidate_pages:
        number = page.get("page")
        text = _markdown(page)
        if not text:
            continue
        for label in _candidate_labels(text):
            level = _level(page)
            # Treat spelling variants such as "cool room" and "coolroom" as
            # one candidate while retaining the clearest display label.
            key = room_identity(_label_key(label), level)
            # Explicit plan/RCP text gives a useful draft clue; requiring a
            # boundary/area witness for high confidence is intentional.
            confidence = 0.68
            if page.get("scale") or page.get("scale_confirmed"):
                confidence += 0.07
            if page.get("rooms"):
                confidence += 0.08
            evidence = {
                "page": number,
                "drawing_number": page.get("drawing_number", ""),
                "title": page.get("title", ""),
                "excerpt": label,
                "view_type": page.get("detected_type", page.get("plan_role", "")),
            }
            row = add_candidate(label, level, number, evidence, confidence)
            area = _area_near_label(text, label)
            if area is not None:
                row["area_m2"] = area
                row["geometry"] = {"area_m2": area, "formula": "explicit_room_area", "operands": {"value_m2": area}}
                row["unresolved_fields"] = [field for field in row["unresolved_fields"] if field != "room_area"]
            row["confidence"] = max(row["confidence"], min(0.82, confidence))
    rows = []
    for row in sorted(candidates.values(), key=lambda item: item["room_id"]):
        row["source_pages"] = sorted(row["source_pages"])
        row["confidence_score"] = round(row.pop("confidence"), 4)
        row["confidence_band"] = "high" if row["confidence_score"] >= 0.8 else "medium" if row["confidence_score"] >= 0.5 else "low"
        rows.append(row)
    return {
        "schema_version": 1, "version": VERSION, "rooms": rows,
        "selected_pages": [page.get("page") for page in pages if page.get("page") is not None],
        "source_fingerprint": fingerprint({"ai_input": ai_input or {}, "coverage": coverage or {}, "spatial_ocr": spatial_ocr or {}, "vector_geometry": vector_geometry or {}, "vision_response": vision_response or {}}),
        "status": "completed",
    }
