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


VERSION = "local-room-inference-v4"
PLAN_ROLES = {
    "main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan",
    "uncertain_top_down_context", "reflected_ceiling_plan", "reflected_ceiling",
}
ROOM_ORIGIN_ROLES = {
    "main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan",
    "enlarged_plan", "enlarged_floor_plan", "reflected_ceiling_plan", "reflected_ceiling",
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
    selected = [{**page, "level_name": coverage_pages.get(page.get("page"), {}).get("level_name", page.get("level_name", "") if not coverage else ""),
                 "level_status": coverage_pages.get(page.get("page"), {}).get("level_status", "page_metadata_unverified" if page.get("level_name") and not coverage else "missing")}
                for page in selected]
    selected_numbers = {page.get("page") for page in selected}
    for number in sorted(linked_numbers):
        page = by_number.get(number)
        if page and number not in selected_numbers:
            selected.append({**page, "level_name": coverage_pages.get(number, {}).get("level_name", page.get("level_name", "") if not coverage else ""),
                             "level_status": coverage_pages.get(number, {}).get("level_status", "page_metadata_unverified" if page.get("level_name") and not coverage else "missing")})
            selected_numbers.add(number)
    return sorted(selected, key=lambda page: (page.get("page") is None, page.get("page")))


def _level(page):
    return _text(page.get("level_name") or page.get("level") or page.get("floor")) or "Unassigned level"


def _room_origin_page(page):
    role = str(page.get("plan_role") or page.get("proposed_role") or page.get("page_role") or "").casefold()
    detected = str(page.get("detected_type") or page.get("sheet_classification") or page.get("classification") or "").casefold()
    blocked = {"render_or_photo", "electrical_or_fire", "schedule", "cover_or_drawing_list", "detail", "notes",
               "reference", "reference_context", "services_or_lighting_plan", "opening_elevation", "opening_schedule",
               "elevation_or_section", "3d_render"}
    if detected in blocked or role in blocked:
        return False
    if role in ROOM_ORIGIN_ROLES:
        return True
    # Legacy page packets carry only the detected sheet type. Reflected
    # ceiling plans remain eligible because the supported Butcher Buffet set
    # labels its rooms there; labels still have to pass the strict item test.
    return detected in {"floor_plan", "reflected_ceiling_plan"}


def parse_room_label_item(value):
    """Return a supported room phrase from one OCR label item, or reject it.

    Only accept a known room phrase with an optional identifier and area suffix.
    OCR notes, legends and details may mention room terms but do not match the
    phrase exactly once those suffixes have been removed.
    """
    text = _text(value)
    if not text or len(text) > 80:
        return None, None, None
    area_match = re.search(r"\s+(\d+(?:\.\d+)?)\s*(m²|m2|sqm)\s*$", text, flags=re.I)
    area = float(area_match.group(1)) if area_match else None
    text = text[:area_match.start()] if area_match else text
    text = re.sub(r"\s+(?:area)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s+\d{1,3}(?:\.\d{1,3})?\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;-/")
    if not text or len(text.split()) > 5 or re.search(r"\d", text):
        return None, None, None
    known_terms = (
        "cool room", "coolroom", "freezer", "food preparation", "back of house", "dining area", "dining",
        "restaurant", "kitchen", "bar", "store room", "store", "stockroom", "office",
        "meeting", "reception", "amenity", "toilet", "bathroom", "corridor", "foyer", "lobby",
        "showroom", "retail", "shop", "bedroom", "living", "lounge", "service counter", "front of house",
        "counter", "kiosk", "plant room", "staff room", "staff", "storage",
    )
    matches = [term for term in known_terms if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text, re.I)]
    matches = [term for term in matches if not any(term != other and term in other for other in matches)]
    # A single OCR item such as "Coolroom Freezer" is two room labels, not
    # evidence that either is a complete standalone label.
    if len(matches) != 1 or re.sub(r"\s+", " ", text).casefold() != matches[0].casefold():
        return None, None, None
    display = text.title() if text.isupper() else text
    return display, matches[0], area


def filter_room_label_fragments(items):
    """Remove shorter room labels that are part of another accepted label."""
    unique = {}
    for label, area in items:
        unique.setdefault(label.casefold(), (label, area))
    values = list(unique.values())
    return [item for item in values if not any(
        item[0].casefold() != other[0].casefold()
        and re.search(r"(?<![a-z0-9])" + re.escape(item[0].casefold()) + r"(?![a-z0-9])", other[0].casefold())
        for other in values
    )]


def _room_label_context_is_note(text):
    return any(re.search(pattern, text, re.I) for pattern in (
        r"\bflat\s+bar\b",
        r"\bshop\s+drawings?\b",
        r"\bshopfitter\b",
        r"\bkitchen\s+exchange\b",
        r"\bexchange\s+air\s+ductwork\b",
        r"\bmanufacture\b",
    ))


def _spatial_label_items(spatial_page):
    standalone = (spatial_page or {}).get("standalone_text_items")
    if isinstance(standalone, list) and standalone:
        candidates = list(standalone)
    else:
        # Compatibility with existing saved artifacts predating the complete
        # text-item field. Word samples recover labels omitted by the bounded
        # room-label-candidate list; the strict parser filters note fragments.
        candidates = list((spatial_page or {}).get("word_samples", []))
        candidates.extend((spatial_page or {}).get("room_label_candidates", []))

    parsed = []
    context_lines = _text_item_lines(standalone if isinstance(standalone, list) and standalone else (spatial_page or {}).get("word_samples", []))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "")
        context = ""
        bbox = item.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            context = next((line for line, line_bbox in context_lines
                            if abs(((line_bbox[1] + line_bbox[3]) - (bbox[1] + bbox[3])) / 2) <= 2.5
                            and bbox[0] >= line_bbox[0] - 1 and bbox[0] <= line_bbox[2] + 1), "")
        if context and _room_label_context_is_note(context):
            continue
        label, _, area = parse_room_label_item(text)
        if label:
            parsed.append((label, area))
            continue
        # A compact legacy item may combine adjacent standalone room names
        # (for example COOLROOM FREEZER). Split only when every token is itself
        # an exact supported room phrase; notes with extra words remain rejected.
        words = _text(text).strip(" .,:;-/").split()
        if len(words) > 1:
            parts = [parse_room_label_item(word) for word in words]
            if all(part[0] and part[2] is None for part in parts):
                parsed.extend((part[0], None) for part in parts)

    # Recover multiword labels from adjacent text-layer tokens on the same
    # baseline, such as SERVICE COUNTER. Reject a whole line with extra note
    # text rather than mining valid-looking words out of it.
    if isinstance(standalone, list) and standalone:
        for line, _ in context_lines:
            label, _, area = parse_room_label_item(line)
            if label:
                parsed.append((label, area))
    return filter_room_label_fragments(parsed)


def _text_item_lines(items):
    rows = [item for item in items if isinstance(item, dict) and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4]
    rows.sort(key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2, item["bbox"][0]))
    lines = []
    for item in rows:
        x0, top, x1, bottom = item["bbox"]
        center = (top + bottom) / 2
        line = next((entry for entry in reversed(lines) if abs(center - entry["center"]) <= 2.5), None)
        if line is None:
            line = {"center": center, "items": []}
            lines.append(line)
        line["items"].append((x0, x1, _text(item.get("text")), item["bbox"]))
    output = []
    for line in lines:
        items_on_line = sorted(line["items"])
        segments, current = [], []
        previous_right = None
        for x0, x1, word, _ in items_on_line:
            gap = None if previous_right is None else x0 - previous_right
            if gap is not None and gap > 24:
                if current:
                    segments.append(current)
                current = []
            current.append((word, x0, x1))
            previous_right = x1
        if current:
            segments.append(current)
        for segment in segments:
            text = " ".join(word for word, _, _ in segment)
            output.append((text, [segment[0][1], min(i[3][1] for i in line["items"]), segment[-1][2], max(i[3][3] for i in line["items"])]))
    return output


def room_label_items(spatial_page):
    return _spatial_label_items(spatial_page)


def _label_key(label):
    return re.sub(r"[^a-z0-9]", "", str(label or "").casefold())


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
        area_value = raw.get("area_m2")
        if area_value is None:
            area_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m²|m2|sqm)(?:\s|$)", _text(raw.get("area")), re.I)
            if area_match:
                area_value = float(area_match.group(1))
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
        if area_value is not None:
            geometry["area_m2"] = area_value
            geometry["formula"] = raw.get("formula", "explicit_room_area")
            geometry["operands"] = raw.get("operands", {"value_m2": area_value})
        if raw.get("test_fixture_only"):
            geometry["test_fixture_only"] = True
            geometry["fixture_id"] = raw.get("fixture_id", "")
        output.append({
            "label": label,
            "level_name": _level(raw) if any(raw.get(key) for key in ("level_name", "level", "floor")) else _level(page),
            "page": raw.get("page", page_number),
            "source_pages": list(raw.get("source_pages") or [raw.get("page", page_number)]),
            "area_m2": area_value,
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
        if not _room_origin_page(page):
            continue
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
    candidate_pages = [page for page in pages if _page_selected(page) and _room_origin_page(page)]
    for page in candidate_pages:
        number = page.get("page")
        spatial_page = spatial_by_page.get(number, {})
        for label, area in room_label_items(spatial_page):
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
