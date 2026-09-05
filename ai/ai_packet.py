#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


AI_CONTEXT_CONFIDENCE_THRESHOLD = 0.83


def build_ai_packet(packet, decisions=None, decisions_source="", measurements=None, vision=None, spatial_ocr=None, coordinate_review=None, page_triage=None):
    decisions = decisions or {}
    decision_by_page = {item["page"]: item for item in decisions.get("pages", [])}
    triage_by_page = {item["page"]: item for item in page_triage_pages(page_triage)}
    structure_by_page = {item["page"]: item for item in packet.get("structured_pages", [])}
    human_reviewed = bool(decision_by_page)

    confirmed_pages = {
        "floor_plans": [],
        "reflected_ceiling_plans": [],
        "existing_hvac_or_services_plans": [],
        "reference_pages": [],
    }
    design_inputs = empty_design_inputs()
    questions = []

    pages_for_ai = list(packet.get("primary_pages", []))
    if human_reviewed:
        pages_for_ai += packet.get("reference_pages", [])

    confirmed_page_numbers = set()
    for page in pages_for_ai:
        decision = decision_for_page(page, decision_by_page, human_reviewed)
        if decision["decision"] == "Discard":
            continue
        triage = triage_by_page.get(page["page"])
        if triage and triage.get("disposition") == "exclude":
            continue
        page = page_with_triage(page, triage)

        ai_page = ai_page_summary(page, decision, human_reviewed, structure_by_page.get(page["page"]))
        add_confirmed_page(confirmed_pages, ai_page, page["type"], decision["decision"])
        collect_design_inputs(design_inputs, page, decision)
        confirmed_page_numbers.add(page["page"])

    design_inputs = final_design_inputs(design_inputs)
    measurement_review = measurement_summary(measurements or {})
    spatial_ocr_review = spatial_ocr_summary(spatial_ocr or {})
    vision_review = vision_summary(vision or {})
    coordinate_review_summary = coordinate_summary(coordinate_review or {})
    add_questions(questions, confirmed_pages, design_inputs, human_reviewed)

    return {
        "source_pdf": packet.get("pdf", ""),
        "source_files": source_files(packet, triage_by_page),
        "drawing_set": drawing_set(packet, decision_by_page, triage_by_page, structure_by_page, human_reviewed),
        "review_status": {
            "human_reviewed": human_reviewed,
            "decisions_source": decisions_source,
            "page_triage_applied": bool(triage_by_page),
        },
        "page_triage": {"pages": list(triage_by_page.values())} if triage_by_page else {},
        "confirmed_pages": confirmed_pages,
        "design_inputs": design_inputs,
        "building_model": build_building_model(packet, confirmed_pages, design_inputs, measurement_review, vision_review, coordinate_review_summary),
        "measurement_review": measurement_review,
        "spatial_ocr": spatial_ocr_review,
        "vision_review": vision_review,
        "coordinate_review": coordinate_review_summary,
        "high_confidence_context": high_confidence_context(packet, confirmed_page_numbers, triage_by_page),
        "questions_for_user": questions,
    }


def source_files(packet, triage_by_page=None):
    triage_by_page = triage_by_page or {}
    pages = packet.get("kept_pages", packet.get("primary_pages", []) + packet.get("reference_pages", []))
    return {
        "pdf": packet.get("pdf", ""),
        "page_images": [
            {
                "page": page["page"],
                "type": page.get("type", ""),
                "title": page.get("title", ""),
                "level_name": triage_by_page.get(page["page"], {}).get("floor_label") or page.get("extracted", {}).get("level_name", ""),
                "review_bucket": page.get("review_bucket", ""),
                "packet_role": page.get("packet_role", ""),
                "plan_role": page_plan_role(page),
                "sheet_classification": page.get("sheet_classification", page.get("type", "other")),
                "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
                "path": page["thumbnail_path"],
            }
            for page in pages
            if page.get("thumbnail_path")
        ],
    }


def drawing_set(packet, decision_by_page, triage_by_page, structure_by_page, human_reviewed):
    pages = []
    for source_page in packet.get("kept_pages", packet.get("primary_pages", []) + packet.get("reference_pages", [])):
        page = page_with_triage(source_page, triage_by_page.get(source_page["page"]))
        decision = decision_for_page(page, decision_by_page, human_reviewed)
        summary = ai_page_summary(page, decision, human_reviewed, structure_by_page.get(page["page"]))
        summary.update({
            "sheet_classification": page.get("sheet_classification", page.get("type", "other")),
            "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
            "classification_evidence": page.get("classification_evidence", "Retained drawing-set page."),
        })
        pages.append(summary)
    return {"pages": pages, "all_pages_retained": True}


def high_confidence_context(packet, confirmed_page_numbers, triage_by_page=None):
    triage_by_page = triage_by_page or {}
    pages = []
    for page in packet.get("kept_pages", []):
        if page.get("page") in confirmed_page_numbers:
            continue
        if triage_by_page.get(page.get("page"), {}).get("disposition") == "exclude":
            continue
        score = context_score(page)
        if score < AI_CONTEXT_CONFIDENCE_THRESHOLD:
            continue
        pages.append(context_page_summary(page, score))

    return {
        "threshold": AI_CONTEXT_CONFIDENCE_THRESHOLD,
        "pages": pages,
        "note": (
            "These pages are supplied as extra context only. Do not treat them as confirmed design inputs "
            "unless a human has reviewed them."
        ),
    }


def context_score(page):
    visual = page.get("visual_features", {})
    return max(
        number_or_zero(page.get("confidence")),
        number_or_zero(visual.get("plan_confidence")),
    )


def context_page_summary(page, score):
    extracted = page.get("extracted", {})
    visual = page.get("visual_features", {})
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "detected_type": page.get("type", ""),
        "review_bucket": page.get("review_bucket", ""),
        "confidence": page.get("confidence"),
        "context_score": round(score, 3),
        "level_name": extracted.get("level_name", ""),
        "scale": extracted.get("scale"),
        "thumbnail_path": page.get("thumbnail_path", ""),
        "visual": {
            "likely_view": visual.get("likely_view", ""),
            "plan_confidence": visual.get("plan_confidence"),
            "top_down_score": visual.get("top_down_score"),
            "side_view_score": visual.get("side_view_score"),
        },
    }


def number_or_zero(value):
    if isinstance(value, (int, float)):
        return value
    return 0


def empty_design_inputs():
    return {
        "scales": [],
        "levels": [],
        "geometry_evidence_pages": [],
        "dimension_evidence_pages": [],
        "finish_or_fitout_context_pages": [],
        "rcp_service_context_pages": [],
        "legend_key_pages": [],
        "written_dimensions": [],
        "rooms": [],
        "ceiling_constraints": [],
        "hvac_terms": [],
        "drawing_numbers": [],
        "notes": [],
        "scale_notes": [],
    }


def decision_for_page(page, decision_by_page, human_reviewed):
    decision = decision_by_page.get(page["page"])
    if decision:
        return decision
    return {
        "page": page["page"],
        "decision": "Confirm as detected",
        "scale_confirmed": False,
        "note": "" if human_reviewed else "Page has not been human reviewed.",
    }


def page_triage_pages(page_triage):
    result = page_triage.get("result", {}) if isinstance(page_triage, dict) else {}
    triage = result.get("page_triage", {}) if isinstance(result, dict) else {}
    pages = triage.get("pages", []) if isinstance(triage, dict) else []
    return pages if isinstance(pages, list) else []


def page_with_triage(page, triage):
    if not triage:
        return page
    adjusted = dict(page)
    extracted = dict(page.get("extracted", {}))
    if triage.get("floor_label"):
        extracted["level_name"] = triage["floor_label"]
    adjusted["extracted"] = extracted
    if triage.get("page_role"):
        adjusted["plan_role"] = triage["page_role"]
    adjusted["vision_triage"] = triage
    return adjusted


def ai_page_summary(page, decision, human_reviewed, structure=None):
    extracted = page.get("extracted", {})
    summary = {
        "page": page["page"],
        "title": page.get("title", ""),
        "detected_type": page.get("type", ""),
        "confirmed_decision": decision["decision"],
        "confidence": page.get("confidence"),
        "importance": page.get("importance", ""),
        "packet_role": page.get("packet_role", ""),
        "plan_role": page_plan_role(page),
        "sheet_classification": page.get("sheet_classification", page.get("type", "other")),
        "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
        "classification_evidence": page.get("classification_evidence", ""),
        "drawing_number": extracted.get("drawing_number", ""),
        "level_name": extracted.get("level_name", ""),
        "scale": extracted.get("scale"),
        "written_dimensions": extracted.get("written_dimensions", []),
        "rooms": extracted.get("rooms", []),
        "ceiling_constraints": extracted.get("ceiling_constraints", []),
        "hvac_terms": extracted.get("hvac_terms", []),
        "scale_confirmed": bool(decision.get("scale_confirmed")),
        "thumbnail_path": page.get("thumbnail_path", ""),
        "human_reviewed": human_reviewed,
        "review_note": decision.get("note", ""),
        "vision_triage": page.get("vision_triage", {}),
    }
    if structure:
        summary["structured_content"] = {
            "word_count": structure["word_count"],
            "table_count": structure["table_count"],
            "needs_ocr": structure["needs_ocr"],
            "ocr_status": structure.get("ocr_status", "not_checked"),
            "markdown": structure["markdown"],
            "elements": structure["elements"],
        }
    return summary


def add_confirmed_page(confirmed_pages, ai_page, detected_type, decision):
    confirmed_pages[page_bucket(detected_type, decision)].append(ai_page)


def page_bucket(detected_type, decision):
    if decision == "Keep as reference":
        return "reference_pages"
    if decision == "Confirm as floor plan" or detected_type in {"floor_plan", "roof_plan"}:
        return "floor_plans"
    if decision == "Confirm as RCP" or detected_type == "reflected_ceiling_plan":
        return "reflected_ceiling_plans"
    if detected_type == "existing_hvac_or_services_plan":
        return "existing_hvac_or_services_plans"
    return "reference_pages"


def collect_design_inputs(design_inputs, page, decision):
    extracted = page.get("extracted", {})
    append_unique(design_inputs["scales"], extracted.get("scale"))
    collect_geometry_evidence(design_inputs, page, decision)
    collect_level(design_inputs, page, decision)
    collect_legend_key(design_inputs, page)
    append_many_unique(design_inputs["written_dimensions"], extracted.get("written_dimensions", []))
    append_many_unique(design_inputs["rooms"], extracted.get("rooms", []))
    append_many_unique(design_inputs["ceiling_constraints"], extracted.get("ceiling_constraints", []))
    append_many_unique(design_inputs["hvac_terms"], extracted.get("hvac_terms", []))
    append_unique(design_inputs["drawing_numbers"], extracted.get("drawing_number"))
    append_many_unique(design_inputs["notes"], extracted.get("notes_for_ai", []))

    note = decision.get("note", "").strip()
    if note:
        design_inputs["scale_notes"].append({"page": page["page"], "note": note})

    if extracted.get("scale") and not decision.get("scale_confirmed"):
        design_inputs["scale_notes"].append(
            {"page": page["page"], "note": "Scale was detected but has not been confirmed."}
        )


def collect_geometry_evidence(design_inputs, page, decision):
    if decision.get("decision") == "Keep as reference":
        return

    role = page_plan_role(page)
    detected_type = page.get("type")
    summary = page_evidence_summary(page)

    if role in {"main_floor_plan", "supporting_geometry_plan", "furniture_plan", "uncertain_top_down_context"}:
        append_unique(design_inputs["geometry_evidence_pages"], summary)
    if is_dimension_evidence(page):
        append_unique(design_inputs["dimension_evidence_pages"], summary)
    if role in {"supporting_geometry_plan", "furniture_plan"}:
        append_unique(design_inputs["finish_or_fitout_context_pages"], summary)
    if detected_type in {"reflected_ceiling_plan", "existing_hvac_or_services_plan"}:
        append_unique(design_inputs["rcp_service_context_pages"], summary)


def page_evidence_summary(page):
    extracted = page.get("extracted", {})
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "detected_type": page.get("type", ""),
        "plan_role": page_plan_role(page),
        "scale": extracted.get("scale"),
        "level_name": extracted.get("level_name", ""),
        "drawing_number": extracted.get("drawing_number", ""),
        "written_dimension_count": len(extracted.get("written_dimensions", [])),
        "thumbnail_path": page.get("thumbnail_path", ""),
    }


def is_dimension_evidence(page):
    title = clean_lower(page.get("title", ""))
    extracted = page.get("extracted", {})
    return (
        "dimension plan" in title
        or "setout" in title
        or "set out" in title
        or (page_plan_role(page) == "supporting_geometry_plan" and bool(extracted.get("written_dimensions")))
        or len(extracted.get("written_dimensions", [])) >= 6
    )


def final_design_inputs(design_inputs):
    scales = design_inputs["scales"]
    return {
        "preferred_scale": scales[0] if len(scales) == 1 else None,
        "scale_status": scale_status(design_inputs),
        "scales": scales,
        "levels": labelled_levels(design_inputs["levels"]),
        "geometry_evidence_pages": design_inputs["geometry_evidence_pages"],
        "dimension_evidence_pages": design_inputs["dimension_evidence_pages"],
        "finish_or_fitout_context_pages": design_inputs["finish_or_fitout_context_pages"],
        "rcp_service_context_pages": design_inputs["rcp_service_context_pages"],
        "legend_key_pages": design_inputs["legend_key_pages"],
        "written_dimensions": design_inputs["written_dimensions"],
        "rooms": design_inputs["rooms"],
        "ceiling_constraints": design_inputs["ceiling_constraints"],
        "hvac_terms": design_inputs["hvac_terms"],
        "drawing_numbers": design_inputs["drawing_numbers"],
        "notes": design_inputs["notes"],
        "scale_notes": design_inputs["scale_notes"],
    }


def scale_status(design_inputs):
    scales = design_inputs["scales"]
    if not scales:
        if design_inputs["written_dimensions"]:
            return "direct_dimensions_present_scale_optional"
        return "missing"
    if len(scales) == 1:
        return "single_scale_found"
    return "multiple_scales_found"


def build_building_model(packet, confirmed_pages, design_inputs, measurement_review, vision_review, coordinate_review):
    floors = []
    rooms = []
    dimensions = []

    support_pages = building_support_pages(confirmed_pages)
    floor_plan_pages = confirmed_pages.get("floor_plans", [])
    ungrouped_pages = []

    for floor_index, level in enumerate(design_inputs.get("levels", []), start=1):
        floor_id = f"floor_{floor_index:03d}"
        floor_geometry_pages = related_pages_for_level(floor_plan_pages, level)
        main_plan_pages = pages_by_roles(floor_geometry_pages, {"main_floor_plan"})
        dimension_pages = pages_by_dimension_evidence(floor_geometry_pages)
        supporting_geometry_pages = pages_by_roles(
            floor_geometry_pages,
            {"supporting_geometry_plan", "furniture_plan", "uncertain_top_down_context"},
        )
        floor_support_pages, support_pages = split_support_pages_for_floor(support_pages, level, len(design_inputs.get("levels", [])))
        ceiling_context_pages = pages_by_roles(floor_support_pages, {"reflected_ceiling_plan", "existing_hvac_plan"})
        reference_context_pages = [
            page for page in floor_support_pages
            if page_plan_role(page) == "reference_context"
        ]
        floor_rooms = []
        for room in level.get("rooms", []):
            room_item = building_room(room, floor_id, len(rooms) + 1, level.get("plan_page"))
            floor_rooms.append(room_item)
            rooms.append(room_item)

        floor_dimensions = [
            building_dimension(item, floor_id, level.get("plan_page"))
            for item in level.get("written_dimensions", [])
        ]
        floor_coordinate_links = coordinate_links_for_level(coordinate_review, level)
        dimensions.extend(floor_dimensions + floor_coordinate_links)

        floors.append(
            {
                "floor_id": floor_id,
                "label": level.get("level_label", ""),
                "level_status": level.get("level_status", "needs_confirmation"),
                "source_pages": unique_list(
                    page_numbers(floor_geometry_pages)
                    + page_numbers(floor_support_pages)
                    + [level.get("plan_page")]
                ),
                "main_plan_pages": unique_list(page_numbers(main_plan_pages) or [level.get("plan_page")]),
                "dimension_pages": page_refs(dimension_pages),
                "supporting_geometry_pages": page_refs(supporting_geometry_pages),
                "ceiling_context_pages": page_refs(ceiling_context_pages),
                "reference_pages": page_refs(reference_context_pages),
                "supporting_pages": floor_support_pages,
                "scale": level.get("scale"),
                "rooms": floor_rooms,
                "walls": coordinate_walls_for_level(coordinate_review, level),
                "openings": [],
                "dimensions": floor_dimensions + floor_coordinate_links,
                "ceiling_constraints": floor_ceiling_constraints(confirmed_pages, level),
                "hvac_context": floor_hvac_context(confirmed_pages, level),
                "uncertainties": floor_uncertainties(level, floor_rooms),
            }
        )

    for page in support_pages:
        ungrouped_pages.append(dict(page, ungrouped_reason="No confirmed main floor plan could be matched safely."))
    for page in ungrouped_geometry_pages(floor_plan_pages, floors):
        ungrouped_pages.append(dict(page_evidence_summary(page), ungrouped_reason="Useful top-down evidence, but no confirmed floor could be matched safely."))

    return {
        "project": {
            "source_pdf": packet.get("pdf", ""),
            "model_status": "partial_from_reviewed_pdf_evidence",
            "human_review_required": True,
            "note": "Pages are evidence. This building model is only a partial design model until vision/manual review confirms geometry.",
        },
        "floors": floors,
        "ungrouped_plan_context": ungrouped_pages,
        "rooms": rooms,
        "walls": coordinate_review.get("wall_candidates", []),
        "openings": [],
        "dimensions": dimensions + model_dimension_evidence(measurement_review, vision_review),
        "ceiling_constraints": design_inputs.get("ceiling_constraints", []),
        "hvac_context": {
            "terms": design_inputs.get("hvac_terms", []),
            "legend_key_pages": design_inputs.get("legend_key_pages", []),
            "existing_hvac_pages": confirmed_pages.get("existing_hvac_or_services_plans", []),
        },
        "source_evidence": building_source_evidence(confirmed_pages, measurement_review, vision_review),
        "limitations": [
            "Rooms are only listed when room text was extracted from confirmed floor plan pages.",
            "Walls, doors, openings, and room-to-dimension links are not inferred by this code.",
            "Vision/manual review must confirm geometry before HVAC design or AutoCAD generation.",
        ],
    }


def building_room(room, floor_id, room_number, source_page):
    return {
        "room_id": f"room_{room_number:03d}",
        "floor_id": floor_id,
        "name": room.get("name", ""),
        "area": room.get("area", ""),
        "source_page": source_page,
        "walls": [],
        "doors": [],
        "openings": [],
        "dimensions": [],
        "ceiling_constraints": [],
        "hvac_symbols": [],
        "uncertainties": ["Room boundary, walls, doors, and HVAC symbols need vision/manual confirmation."],
    }


def building_dimension(item, floor_id, source_page):
    return {
        "floor_id": floor_id,
        "source_page": source_page,
        "value": item.get("value"),
        "unit": item.get("unit", ""),
        "assigned_to": "unassigned_floor_dimension",
        "confidence": "extracted_text_only",
    }


def building_support_pages(confirmed_pages):
    pages = []
    for bucket in ["floor_plans", "reflected_ceiling_plans", "existing_hvac_or_services_plans", "reference_pages"]:
        for page in confirmed_pages.get(bucket, []):
            if page_plan_role(page) in {"main_floor_plan", "supporting_geometry_plan", "furniture_plan", "uncertain_top_down_context"}:
                continue
            pages.append(support_page_summary(page))
    return pages


def pages_by_roles(pages, roles):
    return [page for page in pages if page_plan_role(page) in roles]


def pages_by_title_words(pages, words):
    result = []
    for page in pages:
        title = clean_lower(page.get("title", ""))
        if any(word in title for word in words):
            result.append(page)
    return result


def pages_by_dimension_evidence(pages):
    result = []
    for page in pages:
        title = clean_lower(page.get("title", ""))
        if any(word in title for word in ["dimension plan", "dimensioned", "setout", "set out"]):
            result.append(page)
            continue
        if page_plan_role(page) == "supporting_geometry_plan" and page.get("written_dimensions"):
            result.append(page)
            continue
        if len(page.get("written_dimensions", [])) >= 6:
            result.append(page)
    return result


def page_numbers(pages):
    return [page.get("page") for page in pages]


def ungrouped_geometry_pages(pages, floors):
    grouped = set()
    for floor in floors:
        grouped.update(floor.get("source_pages", []))
    return [
        page for page in pages
        if page.get("page") not in grouped
        and page_plan_role(page) in {"supporting_geometry_plan", "furniture_plan", "uncertain_top_down_context"}
    ]


def split_support_pages_for_floor(pages, level, floor_count):
    matched = []
    remaining = []
    for page in pages:
        if support_page_matches_floor(page, level, floor_count):
            matched.append(page)
        else:
            remaining.append(page)
    return matched, remaining


def support_page_matches_floor(page, level, floor_count):
    if page_plan_role(page) in {"enlarged_plan", "detail_plan"} and page.get("confirmed_decision") != "Confirm as floor plan":
        return False
    page_level = clean_lower(page.get("level_name", ""))
    level_name = clean_lower(level.get("level_name") or level.get("level_label") or "")
    if page_level and level_name:
        return page_level == level_name
    return floor_count == 1


def support_page_summary(page):
    role = page_plan_role(page)
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "detected_type": page.get("detected_type", ""),
        "plan_role": role,
        "packet_role": page.get("packet_role", ""),
        "level_name": page.get("level_name", ""),
        "scale": page.get("scale"),
        "confirmed_decision": page.get("confirmed_decision", ""),
        "reason": support_page_reason(role),
    }


def support_page_reason(role):
    if role in {"enlarged_plan", "detail_plan"}:
        return "Detail/enlarged context; do not use as main floor geometry unless explicitly confirmed."
    return "Supporting context; does not create a separate floor."


def floor_ceiling_constraints(confirmed_pages, level):
    pages = related_pages_for_level(confirmed_pages.get("reflected_ceiling_plans", []), level)
    constraints = []
    for page in pages:
        constraints.extend(page.get("ceiling_constraints", []))
    return unique_list(constraints)


def floor_hvac_context(confirmed_pages, level):
    pages = related_pages_for_level(confirmed_pages.get("existing_hvac_or_services_plans", []), level)
    return {
        "source_pages": [page["page"] for page in pages],
        "note": "Existing HVAC pages are floor context only until symbols and routes are visually confirmed.",
    }


def related_pages_for_level(pages, level):
    level_name = (level.get("level_name") or level.get("level_label") or "").lower()
    if not level_name:
        return pages
    return [
        page for page in pages
        if not page.get("level_name") or page.get("level_name", "").lower() == level_name
    ]


def floor_uncertainties(level, rooms):
    uncertainties = []
    if level.get("level_status") != "detected":
        uncertainties.append("Floor label needs confirmation.")
    if not rooms:
        uncertainties.append("No room labels were confidently attached to this floor.")
    if level.get("written_dimensions"):
        uncertainties.append("Written dimensions are not assigned to exact walls yet.")
    return uncertainties


def building_source_evidence(confirmed_pages, measurement_review, vision_review):
    return {
        "floor_plan_pages": page_refs(confirmed_pages.get("floor_plans", [])),
        "reflected_ceiling_plan_pages": page_refs(confirmed_pages.get("reflected_ceiling_plans", [])),
        "existing_hvac_pages": page_refs(confirmed_pages.get("existing_hvac_or_services_plans", [])),
        "reference_pages": page_refs(confirmed_pages.get("reference_pages", [])),
        "confirmed_wall_measurements": measurement_review.get("confirmed_wall_measurements", []),
        "vision_wall_dimensions": vision_review.get("wall_dimensions", []),
        "unassigned_dimensions": (
            measurement_review.get("proposed_wall_measurements", [])
            + vision_review.get("unassigned_dimensions", [])
        )[:100],
    }


def model_dimension_evidence(measurement_review, vision_review):
    dimensions = []
    for item in measurement_review.get("confirmed_wall_measurements", []):
        dimensions.append(dict(item, source="measurement_review", assigned_to="confirmed_wall_measurement"))
    for item in measurement_review.get("proposed_wall_measurements", []):
        dimensions.append(dict(item, source="measurement_review", assigned_to="proposed_wall_measurement"))
    for item in vision_review.get("wall_dimensions", []):
        dimensions.append(dict(item, source="vision_review", assigned_to="vision_wall_dimension"))
    for item in vision_review.get("unassigned_dimensions", []):
        dimensions.append(dict(item, source="vision_review", assigned_to="unassigned_dimension"))
    return dimensions[:120]


def coordinate_summary(coordinate_review):
    if not coordinate_review:
        return {
            "source": "",
            "coordinate_systems": [],
            "wall_candidates": [],
            "dimension_candidates": [],
            "room_label_candidates": [],
            "opening_candidates": [],
            "proposed_wall_dimension_links": [],
            "validation": {"issue_count": 0, "issues": []},
            "note": "No coordinate review has been attached.",
        }

    validation = coordinate_review.get("validation", {})
    return {
        "source": coordinate_review.get("source", ""),
        "provider": coordinate_review.get("provider", ""),
        "model": coordinate_review.get("model", ""),
        "coordinate_systems": coordinate_review.get("coordinate_systems", [])[:40],
        "wall_candidates": coordinate_review.get("wall_candidates", [])[:120],
        "dimension_candidates": coordinate_review.get("dimension_candidates", [])[:120],
        "room_label_candidates": coordinate_review.get("room_label_candidates", [])[:80],
        "opening_candidates": coordinate_review.get("opening_candidates", [])[:80],
        "proposed_wall_dimension_links": coordinate_review.get("proposed_wall_dimension_links", [])[:120],
        "validation": {
            "issue_count": validation.get("issue_count", 0),
            "usable_measurement_ids": validation.get("usable_measurement_ids", []),
            "issues": validation.get("issues", [])[:80],
        },
        "note": "Coordinate evidence is review material only until validated and human-confirmed.",
    }


def coordinate_links_for_level(coordinate_review, level):
    pages = level_source_pages(level)
    links = []
    usable_ids = set(coordinate_review.get("validation", {}).get("usable_measurement_ids", []))
    for item in coordinate_review.get("proposed_wall_dimension_links", []):
        if pages and item.get("page") not in pages:
            continue
        assigned = "validated_coordinate_wall_dimension" if item.get("measurement_id") in usable_ids else "coordinate_wall_dimension_candidate"
        links.append(dict(item, source="coordinate_review", assigned_to=assigned))
    return links[:80]


def coordinate_walls_for_level(coordinate_review, level):
    pages = level_source_pages(level)
    walls = []
    for item in coordinate_review.get("wall_candidates", []):
        if pages and item.get("page") not in pages:
            continue
        walls.append(dict(item, source="coordinate_review", assigned_to="wall_candidate"))
    return walls[:80]


def level_source_pages(level):
    return set(unique_list([level.get("plan_page")] + level.get("evidence_pages", [])))


def page_refs(pages):
    return [
        {
            "page": page.get("page"),
            "title": page.get("title", ""),
            "detected_type": page.get("detected_type", ""),
            "packet_role": page.get("packet_role", ""),
            "plan_role": page_plan_role(page),
            "scale": page.get("scale"),
        }
        for page in pages
    ]


def collect_legend_key(design_inputs, page):
    if not is_legend_key_context(page):
        return

    extracted = page.get("extracted", {})
    append_unique(
        design_inputs["legend_key_pages"],
        {
            "page": page["page"],
            "title": page.get("title", ""),
            "detected_type": page.get("type", ""),
            "packet_role": page.get("packet_role") or legend_packet_role(page),
            "scale": extracted.get("scale"),
            "drawing_number": extracted.get("drawing_number", ""),
            "matched_title_words": page.get("matched_title_words", []),
            "matched_support_words": page.get("matched_support_words", []),
            "terms": unique_list(extracted.get("hvac_terms", []) + extracted.get("ceiling_constraints", [])),
            "notes": extracted.get("notes_for_ai", [])[:8],
            "thumbnail_path": page.get("thumbnail_path", ""),
        },
    )


def is_legend_key_context(page):
    if page.get("type") in {"hvac_or_rcp_legend", "equipment_or_fixture_schedule"}:
        return True
    if page.get("type") not in {"reflected_ceiling_plan", "existing_hvac_or_services_plan"}:
        return False
    return embedded_legend_terms(page) >= 3


def embedded_legend_terms(page):
    text = embedded_legend_text(page)
    words = [
        "legend",
        "indicative of light fixture",
        "light switch",
        "lighting circuit",
        "access panel",
        "air condition register",
        "supply slot diffuser",
        "sprinkler head",
        "smoke detector",
        "grille",
        "diffuser",
        "register",
    ]
    return len([word for word in words if word in text])


def embedded_legend_text(page):
    extracted = page.get("extracted", {})
    parts = [
        page.get("title", ""),
        " ".join(page.get("matched_title_words", [])),
        " ".join(page.get("matched_support_words", [])),
        " ".join(extracted.get("hvac_terms", [])),
        " ".join(extracted.get("ceiling_constraints", [])),
        " ".join(extracted.get("notes_for_ai", [])),
    ]
    return clean_lower(" ".join(parts))


def legend_packet_role(page):
    if page.get("type") == "equipment_or_fixture_schedule":
        return "equipment_schedule_context"
    if page.get("type") in {"reflected_ceiling_plan", "existing_hvac_or_services_plan"}:
        return "embedded_symbol_key_context"
    return "symbol_key_context"


def labelled_levels(levels):
    labelled = []
    for index, level in enumerate(sorted(levels, key=level_sort_value), start=1):
        item = dict(level)
        if item.get("level_name"):
            item["level_label"] = item["level_name"]
            item["level_status"] = "detected"
        else:
            item["level_label"] = f"Unlabelled Floor Plan {index}"
            item["level_status"] = "needs_confirmation"
        labelled.append(item)
    return labelled


def level_sort_value(level):
    text = " ".join(
        str(level.get(key, ""))
        for key in ["level_name", "title", "drawing_number"]
    ).lower()
    if "basement" in text:
        return -200
    if "lower ground" in text:
        return -100
    if "ground floor" in text or "ground level" in text or "main level" in text or "main floor" in text:
        return 0
    if "roof" in text:
        return 900

    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    for word, value in ordinals.items():
        if f"{word} floor" in text:
            return value

    import re

    match = re.search(r"\b(?:level|floor)\s+(\d+)", text)
    if match:
        return int(match.group(1))
    return 500


def add_questions(questions, confirmed_pages, design_inputs, human_reviewed):
    if not human_reviewed:
        questions.append("Confirm the detected pages before using this packet for HVAC design decisions.")
    if not confirmed_pages["floor_plans"]:
        questions.append("Which page should be used as the architectural floor plan?")
    if floor_plans_without_levels(confirmed_pages):
        questions.append("Confirm the floor or level name for each floor plan page.")
    if not confirmed_pages["reflected_ceiling_plans"]:
        questions.append("Which page should be used as the reflected ceiling plan?")
    if not design_inputs["scales"] and not design_inputs["written_dimensions"]:
        questions.append("What scale should be used, or are written dimensions enough for this drawing set?")
    elif len(design_inputs["scales"]) > 1:
        questions.append("Multiple scales were found. Confirm which scale applies to each design page.")
    if pages_needing_ocr(confirmed_pages):
        questions.append("Some kept pages still need OCR before the AI can reliably read them.")
    if design_inputs["hvac_terms"] and not design_inputs["legend_key_pages"]:
        questions.append("Which legend/key page defines the HVAC or RCP symbols used in this drawing set?")


def pages_needing_ocr(confirmed_pages):
    for pages in confirmed_pages.values():
        for page in pages:
            if page.get("structured_content", {}).get("needs_ocr"):
                return True
    return False


def measurement_summary(measurements):
    if not measurements:
        return {
            "source": "",
            "confirmed_wall_measurements": [],
            "proposed_wall_measurements": [],
            "note": "No wall measurement review has been attached.",
        }

    confirmed = []
    proposed = []
    rejected = []
    for page in measurements.get("pages", []):
        for match in page.get("matches", []):
            item = {
                "page": page["page"],
                "level_label": page.get("level_label", ""),
                "dimension_text": match.get("dimension_text", ""),
                "value_mm": match.get("value_mm"),
                "orientation": match.get("orientation", ""),
                "confidence": match.get("confidence"),
                "overlay": page.get("overlay", ""),
                "decision": match.get("decision", "needs_review"),
            }
            if item["decision"] == "accepted":
                confirmed.append(item)
            elif item["decision"] == "needs_review":
                proposed.append(item)
            elif item["decision"] == "rejected":
                rejected.append(item)

    return {
        "source": measurements.get("source", ""),
        "confirmed_wall_measurements": confirmed,
        "proposed_wall_measurements": proposed[:80],
        "rejected_wall_measurements": rejected[:80],
        "note": "Only confirmed_wall_measurements should be used for calculations.",
    }


def spatial_ocr_summary(spatial_ocr):
    if not spatial_ocr:
        return {
            "source": "",
            "pages": [],
            "note": "No spatial OCR evidence has been attached.",
        }

    pages = []
    for page in spatial_ocr.get("pages", []):
        pages.append(
            {
                "page": page.get("page"),
                "detected_type": page.get("detected_type", ""),
                "quality": page.get("quality", {}),
                "title_blocks": page.get("title_blocks", [])[:3],
                "scale_candidates": page.get("scale_candidates", [])[:10],
                "drawing_number_candidates": page.get("drawing_number_candidates", [])[:10],
                "dimension_candidates": page.get("dimension_candidates", [])[:60],
                "room_label_candidates": page.get("room_label_candidates", [])[:40],
                "rotated_text": page.get("rotated_text", [])[:30],
            }
        )
    return {
        "source": spatial_ocr.get("source", ""),
        "pages": pages,
        "note": "Spatial OCR is coordinate evidence. It should guide vision/reasoning, not override visual uncertainty.",
    }


def vision_summary(vision):
    if not vision:
        return {
            "source": "",
            "wall_dimensions": [],
            "unassigned_dimensions": [],
            "uncertainties": [],
            "note": "No vision model output has been attached.",
        }

    result = vision.get("result", {})
    wall_dimensions = []
    unassigned = []
    uncertainties = list(result.get("overall_uncertainties", []))
    for page in result.get("pages", []):
        for item in page.get("wall_dimensions", []):
            wall_dimensions.append(dict(item, page=page.get("page"), level_label=page.get("level_label", "")))
        for item in page.get("unassigned_dimensions", []):
            unassigned.append(dict(item, page=page.get("page"), level_label=page.get("level_label", "")))
        for item in page.get("uncertainties", []):
            uncertainties.append(f"Page {page.get('page')}: {item}")

    return {
        "source": vision.get("source", ""),
        "provider": vision.get("provider", ""),
        "model": vision.get("model", ""),
        "wall_dimensions": wall_dimensions,
        "unassigned_dimensions": unassigned,
        "uncertainties": uncertainties,
        "note": "Vision output is model-interpreted evidence. Use confidence and uncertainty fields before calculations.",
    }


def collect_level(design_inputs, page, decision):
    if page.get("type") not in {"floor_plan", "roof_plan"} or decision.get("decision") == "Keep as reference":
        return
    role = page_plan_role(page)
    if decision.get("decision") != "Confirm as floor plan" and role != "main_floor_plan":
        return
    if role not in {"main_floor_plan", "supporting_geometry_plan", "furniture_plan", "uncertain_top_down_context"}:
        return

    extracted = page.get("extracted", {})
    if role != "main_floor_plan" and decision.get("decision") != "Confirm as floor plan" and not has_existing_level(design_inputs["levels"], extracted):
        return

    level = {
        "level_name": extracted.get("level_name", ""),
        "plan_page": page["page"],
        "title": page.get("title", ""),
        "drawing_number": extracted.get("drawing_number", ""),
        "plan_role": role,
        "scale": extracted.get("scale"),
        "written_dimensions": extracted.get("written_dimensions", []),
        "rooms": extracted.get("rooms", []),
    }
    existing = matching_level(design_inputs["levels"], extracted)
    if existing:
        merge_level(existing, level)
        return
    append_unique(design_inputs["levels"], level)


def floor_plans_without_levels(confirmed_pages):
    return any(not page.get("level_name") for page in confirmed_pages["floor_plans"] if page_plan_role(page) == "main_floor_plan")


def has_existing_level(levels, extracted):
    return bool(levels) and matching_level(levels, extracted)


def matching_level(levels, extracted):
    level_name = clean_lower(extracted.get("level_name", ""))
    if level_name:
        for level in levels:
            if clean_lower(level.get("level_name", "")) == level_name:
                return level
        return None
    if len(levels) == 1:
        return levels[0]
    return None


def merge_level(existing, new_level):
    existing["written_dimensions"] = unique_list(existing.get("written_dimensions", []) + new_level.get("written_dimensions", []))
    existing["rooms"] = unique_list(existing.get("rooms", []) + new_level.get("rooms", []))
    pages = existing.setdefault("evidence_pages", unique_list([existing.get("plan_page")]))
    append_unique(pages, new_level.get("plan_page"))
    if new_level.get("plan_role") == "main_floor_plan" and existing.get("plan_role") != "main_floor_plan":
        existing["plan_page"] = new_level.get("plan_page")
        existing["plan_role"] = "main_floor_plan"
    if not existing.get("scale") and new_level.get("scale"):
        existing["scale"] = new_level.get("scale")


def append_unique(items, item):
    if not item:
        return
    if item not in items:
        items.append(item)


def append_many_unique(items, new_items):
    for item in new_items:
        append_unique(items, item)


def unique_list(items):
    unique = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
    return unique


def page_plan_role(page):
    role = page.get("plan_role")
    if role:
        return role
    detected_type = page.get("detected_type") or page.get("type")
    if detected_type in {"floor_plan", "roof_plan"}:
        return "main_floor_plan"
    if detected_type == "reflected_ceiling_plan":
        return "reflected_ceiling_plan"
    if detected_type == "existing_hvac_or_services_plan":
        return "existing_hvac_plan"
    return "reference_context"


def clean_lower(value):
    return str(value or "").strip().lower()


def load_json(path):
    with Path(path).open(encoding="utf-8") as file:
        return json.load(file)


def default_output_path(packet_path):
    return Path(packet_path).with_name("ai_input.json")


def main():
    parser = argparse.ArgumentParser(description="Create an AI-ready JSON packet from a reviewed PDF packet.")
    parser.add_argument("packet", help="Path to packet.json from the review packet")
    parser.add_argument("--decisions", help="Optional reviewed_decisions.json from review.html")
    parser.add_argument("--measurements", help="Optional measurement_review.json with accepted wall measurements")
    parser.add_argument("--spatial-ocr", help="Optional spatial_ocr.json with text bounding boxes and title-block evidence")
    parser.add_argument("--vision", help="Optional reviewed vision JSON, such as a saved ChatGPT response")
    parser.add_argument("--coordinate-review", help="Optional coordinate_review.json with validated coordinate candidates")
    parser.add_argument("--output", help="Output path; defaults to ai_input.json beside packet.json")
    args = parser.parse_args()

    packet_path = Path(args.packet)
    if not packet_path.exists():
        raise SystemExit(f"Packet not found: {packet_path}")

    packet = load_json(packet_path)
    decisions = None
    decisions_source = ""
    if args.decisions:
        decisions_path = Path(args.decisions)
        if not decisions_path.exists():
            raise SystemExit(f"Decisions file not found: {decisions_path}")
        decisions = load_json(decisions_path)
        decisions_source = str(decisions_path)
    measurements = None
    if args.measurements:
        measurements_path = Path(args.measurements)
        if not measurements_path.exists():
            raise SystemExit(f"Measurements file not found: {measurements_path}")
        measurements = load_json(measurements_path)
        measurements = dict(measurements, source=str(measurements_path))
    spatial_ocr = None
    if args.spatial_ocr:
        spatial_ocr_path = Path(args.spatial_ocr)
        if not spatial_ocr_path.exists():
            raise SystemExit(f"Spatial OCR file not found: {spatial_ocr_path}")
        spatial_ocr = load_json(spatial_ocr_path)
        spatial_ocr = dict(spatial_ocr, source=str(spatial_ocr_path))
    vision = None
    if args.vision:
        vision_path = Path(args.vision)
        if not vision_path.exists():
            raise SystemExit(f"Vision file not found: {vision_path}")
        vision = load_json(vision_path)
        vision = dict(vision, source=str(vision_path))
    coordinate_review = None
    if args.coordinate_review:
        coordinate_path = Path(args.coordinate_review)
        if not coordinate_path.exists():
            raise SystemExit(f"Coordinate review file not found: {coordinate_path}")
        coordinate_review = load_json(coordinate_path)
        coordinate_review = dict(coordinate_review, source=str(coordinate_path))

    output_path = Path(args.output or default_output_path(packet_path))
    output_path.write_text(
        json.dumps(
            build_ai_packet(packet, decisions, decisions_source, measurements, vision, spatial_ocr, coordinate_review),
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"AI packet created: {output_path}")


if __name__ == "__main__":
    main()
