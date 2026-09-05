#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

import pdfplumber

from ai.ai_packet import load_json
from pdf_pipeline.numeric_annotations import classify_numeric_annotation


MIN_LINE_LENGTH_PX = 18
MAX_ITEMS_PER_PAGE = 900
MAX_CONFIRMATION_LINES_PER_PAGE = 5000
MAX_CONFIRMATION_CURVES_PER_PAGE = 2000
MAX_WALL_LINE_CANDIDATES = 90
MAX_WALL_CURVE_CANDIDATES = 50
MIN_WALL_SCORE = 45
# A wall drawn as a filled band: long, thin, and closed. The thickness ceiling is
# generous because it is in screenshot pixels and so varies with sheet scale; the
# aspect ratio does the real work of separating walls from blocks and symbols.
MIN_WALL_BAND_LENGTH_PX = 120
MAX_WALL_BAND_THICKNESS_PX = 45
MIN_WALL_BAND_ASPECT = 8


def create_vector_geometry(ai_input_path, output_path=None, screenshots_dir=None, overlays_dir=None):
    ai_input_path = Path(ai_input_path)
    ai_input = load_json(ai_input_path)
    source_pdf = resolve_source_pdf(ai_input)
    screenshots_dir = Path(screenshots_dir) if screenshots_dir else ai_input_path.with_name("chatgpt_packet") / "screenshots"
    overlays_dir = Path(overlays_dir) if overlays_dir else ai_input_path.with_name("vector_overlays")
    page_refs = geometry_page_refs(ai_input)

    with pdfplumber.open(source_pdf) as pdf:
        pages = [
            extract_page_vectors(pdf.pages[ref["page"] - 1], ref, screenshots_dir)
            for ref in page_refs
            if 1 <= ref["page"] <= len(pdf.pages)
        ]

    output = {
        "source_pdf": str(source_pdf),
        "source_ai_input": str(ai_input_path),
        "geometry_key_points": {"pages": pages},
        "overlays": create_vector_overlays(pages, screenshots_dir, overlays_dir),
        "note": "Vector geometry is raw coordinate evidence. AI/manual review must classify which candidates are walls before CAD use.",
    }

    output_path = Path(output_path or ai_input_path.with_name("vector_geometry.json"))
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def resolve_source_pdf(ai_input):
    for value in [
        ai_input.get("source_pdf"),
        ai_input.get("source_files", {}).get("pdf"),
        ai_input.get("pdf"),
    ]:
        if value and Path(value).exists():
            return Path(value)
    raise FileNotFoundError("Source PDF path was not found in ai_input.json.")


def geometry_page_refs(ai_input):
    design_inputs = ai_input.get("design_inputs", {})
    seen = set()
    refs = []
    for key in ["geometry_evidence_pages", "dimension_evidence_pages", "finish_or_fitout_context_pages"]:
        for page in design_inputs.get(key, []):
            if page.get("page") in seen:
                continue
            if is_detail_or_reference(page):
                continue
            refs.append(
                {
                    "page": page.get("page"),
                    "title": page.get("title", ""),
                    "plan_role": page.get("plan_role", ""),
                    "evidence_bucket": key,
                }
            )
            seen.add(page.get("page"))
    if refs:
        return refs

    for page in ai_input.get("confirmed_pages", {}).get("floor_plans", []):
        if page.get("page") not in seen and not is_detail_or_reference(page):
            refs.append(
                {
                    "page": page.get("page"),
                    "title": page.get("title", ""),
                    "plan_role": page.get("plan_role", ""),
                    "evidence_bucket": "confirmed_floor_plans",
                }
            )
            seen.add(page.get("page"))
    return refs


def is_detail_or_reference(page):
    text = f"{page.get('plan_role', '')} {page.get('title', '')}".lower()
    blocked = [
        "detail",
        "enlarged",
        "section",
        "elevation",
        "joinery",
        "cabinet",
        "fixture",
        "legend",
        "schedule",
        "reflected ceiling",
        "reflected_ceiling",
        "rcp",
        "services",
        "mechanical",
        "hvac",
    ]
    return any(word in text for word in blocked)


def extract_page_vectors(pdf_page, page_ref, screenshots_dir):
    screenshot = screenshot_for_page(screenshots_dir, page_ref["page"])
    image_size = image_dimensions(screenshot)
    transform = page_transform(pdf_page.width, pdf_page.height, image_size, pdf_page.bbox)
    viewport = default_plan_viewport(image_size)

    raw_lines = [line_candidate(item, transform, viewport, page_ref["page"], index) for index, item in enumerate(pdf_page.lines or [], 1)]
    raw_curves = [curve_candidate(item, transform, viewport, page_ref["page"], index) for index, item in enumerate(pdf_page.curves or [], 1)]
    raw_rects = [rect_candidate(item, transform, viewport, page_ref["page"], index) for index, item in enumerate(pdf_page.rects or [], 1)]
    marker_bboxes = [item.get("bbox_px") for item in raw_curves + raw_rects if item]
    words = dimension_text_candidates(pdf_page.extract_words() or [], transform, viewport, page_ref["page"], marker_bboxes)

    lines = [item for item in raw_lines if item and item["length_px"] >= MIN_LINE_LENGTH_PX]
    curves = [item for item in raw_curves if item and item["point_count"] >= 3]
    rects = [item for item in raw_rects if item]

    viewport_info = detect_main_plan_viewport(lines, curves, rects, words, image_size)
    viewport = viewport_info.get("bbox_px")
    apply_plan_viewport(lines, curves, rects, words, viewport)
    apply_viewport_scores(lines, curves, viewport_info, image_size)

    return {
        "page": page_ref["page"],
        "title": page_ref.get("title", ""),
        "plan_role": page_ref.get("plan_role", ""),
        "evidence_bucket": page_ref.get("evidence_bucket", ""),
        "image": f"screenshots/{screenshot.name}" if screenshot else "",
        "plan_viewport": viewport_info,
        "coordinate_systems": coordinate_systems(image_size, viewport_info),
        "raw_counts": {
            "lines": len(pdf_page.lines or []),
            "curves": len(pdf_page.curves or []),
            "rects": len(pdf_page.rects or []),
            "dimension_texts": len(words),
        },
        "line_candidates": lines[:MAX_ITEMS_PER_PAGE],
        "curve_candidates": curves[:MAX_ITEMS_PER_PAGE],
        # The compact candidate lists keep the vision packet readable. Confirmation
        # needs a wider local search, so retain a page-local provenance index that is
        # queried only after vision supplies a wall location.
        "confirmation_line_candidates": lines[:MAX_CONFIRMATION_LINES_PER_PAGE],
        "confirmation_curve_candidates": curves[:MAX_CONFIRMATION_CURVES_PER_PAGE],
        "rect_candidates": rects[:250],
        "dimension_candidates": words[:160],
        "openings": [],
        "unclassified_geometry": [],
        "note": "Raw PDF-vector primitives are neutral evidence. The vision review decides which primitives form physical walls.",
    }


def screenshot_for_page(screenshots_dir, page_number):
    if not screenshots_dir or not screenshots_dir.exists():
        return None
    matches = sorted(screenshots_dir.glob(f"page_{int(page_number):03d}_*.png"))
    if matches:
        return matches[0]
    fallback = screenshots_dir / f"page_{int(page_number):03d}.png"
    return fallback if fallback.exists() else None


def image_dimensions(path):
    if not path:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as image:
        return [image.width, image.height]


def page_transform(page_width, page_height, image_size, bbox=None):
    if image_size:
        x_scale = image_size[0] / page_width
        y_scale = image_size[1] / page_height
    else:
        x_scale = 1
        y_scale = 1
        image_size = [round(page_width, 2), round(page_height, 2)]
    bbox = bbox or [0, 0, page_width, page_height]
    return {
        "page_width": page_width,
        "page_height": page_height,
        "bbox_left": bbox[0],
        "bbox_top": bbox[1],
        "image_width": image_size[0],
        "image_height": image_size[1],
        "x_scale": x_scale,
        "y_scale": y_scale,
    }


def default_plan_viewport(image_size):
    if not image_size:
        return None
    return [0, 0, image_size[0], image_size[1]]


def coordinate_systems(image_size, viewport):
    viewport_info = viewport if isinstance(viewport, dict) else {
        "bbox_px": viewport,
        "confidence": "low",
        "reasons": ["full-page fallback viewport"],
    }
    return {
        "image_px": {
            "origin": "top_left_full_screenshot",
            "x_axis": "right",
            "y_axis": "down",
            "image_width": image_size[0] if image_size else None,
            "image_height": image_size[1] if image_size else None,
            "units": "image_px",
        },
        "plan_px": {
            "origin": "bottom_left_plan_viewport",
            "x_axis": "right",
            "y_axis": "up",
            "plan_viewport_bbox_px": viewport_info.get("bbox_px"),
            "plan_viewport_confidence": viewport_info.get("confidence", "low"),
            "plan_viewport_reasons": viewport_info.get("reasons", []),
            "units": "plan_px",
        },
    }


def detect_main_plan_viewport(lines, curves, rects, dimensions, image_size):
    fallback = {
        "bbox_px": default_plan_viewport(image_size),
        "confidence": "low",
        "reasons": ["main plan viewport detection fallback"],
        "source": "full_page_fallback",
    }
    if not image_size:
        return fallback

    boxes = []
    for item in lines:
        box = item_bbox(item)
        if viewport_candidate_box(box, image_size, item.get("length_px", 0)):
            boxes.append(box)
    for item in curves:
        box = item_bbox(item)
        if viewport_candidate_box(box, image_size, bbox_span(box)):
            boxes.append(box)
    for item in dimensions:
        box = item_bbox(item)
        if viewport_candidate_box(box, image_size, bbox_span(box), allow_text=True):
            boxes.append(box)
    for item in rects:
        box = item_bbox(item)
        if viewport_candidate_box(box, image_size, bbox_span(box)) and not large_sheet_box(box, image_size):
            boxes.append(box)

    boxes = densest_boxes(boxes, image_size)
    if len(boxes) < 4:
        return fallback

    bbox = padded_bbox(union_bbox(boxes), image_size[0], image_size[1], 80)
    area_ratio = bbox_area(bbox) / max(1, image_size[0] * image_size[1])
    if area_ratio > 0.88 or area_ratio < 0.015:
        return fallback

    confidence = "high" if len(boxes) >= 20 and area_ratio <= 0.72 else "medium"
    return {
        "bbox_px": bbox,
        "confidence": confidence,
        "reasons": [
            "detected dense plan-like vector/text cluster",
            "ignored sheet edges and title block band",
            f"cluster candidates: {len(boxes)}",
        ],
        "source": "vector_geometry_heuristic",
    }


def viewport_candidate_box(bbox, image_size, span, allow_text=False):
    if not valid_bbox(bbox) or not image_size:
        return False
    if span < (8 if allow_text else 30):
        return False
    if near_sheet_edge(bbox, image_size) and bbox_span(bbox) > min(image_size) * 0.55:
        return False
    if in_title_block_band(bbox, image_size):
        return False
    if in_sheet_furniture_zone(bbox, image_size):
        return False
    return True


def densest_boxes(boxes, image_size):
    if not boxes:
        return []
    centers = [(box, bbox_center(box)) for box in boxes if bbox_center(box)]
    if not centers:
        return boxes
    radius_x = max(220, image_size[0] * 0.20)
    radius_y = max(180, image_size[1] * 0.22)
    best_center = None
    best_score = -1
    for _box, center in centers:
        score = sum(1 for __box, other in centers if abs(other[0] - center[0]) <= radius_x and abs(other[1] - center[1]) <= radius_y)
        if score > best_score:
            best_score = score
            best_center = center
    clustered = [
        box for box, center in centers
        if abs(center[0] - best_center[0]) <= radius_x * 1.45
        and abs(center[1] - best_center[1]) <= radius_y * 1.45
    ]
    return clustered or boxes


def apply_plan_viewport(lines, curves, rects, dimensions, viewport):
    for item in lines:
        item["points_plan_px"] = [
            image_point_to_plan_px(item.get("start_px"), viewport),
            image_point_to_plan_px(item.get("end_px"), viewport),
        ]
    for item in curves:
        item["points_plan_px"] = [image_point_to_plan_px(point, viewport) for point in item.get("points_px", [])]
    for item in rects + dimensions:
        item["bbox_plan_px"] = image_bbox_to_plan_px(item.get("bbox_px"), viewport)


def apply_viewport_scores(lines, curves, viewport_info, image_size):
    for item in lines + curves:
        result = candidate_inside_viewport_score(item, viewport_info, image_size)
        item["inside_main_plan_viewport"] = result["inside_viewport"]
        item["confidence_score"] = max(0, min(100, item.get("confidence_score", 0) + result["score_delta"]))
        item["classification_reasons"] = item.get("classification_reasons", []) + result["reasons"]
        item["confidence"] = confidence_label(item["confidence_score"])
        if item["confidence_score"] < MIN_WALL_SCORE and item.get("candidate_role_hint") != "likely_noise":
            item["candidate_role_hint"] = "vector_context"


def candidate_inside_viewport_score(item, viewport_info, image_size=None):
    viewport = viewport_info.get("bbox_px") if isinstance(viewport_info, dict) else viewport_info
    bbox = item_bbox(item)
    if not valid_bbox(bbox) or not valid_bbox(viewport):
        return {"score_delta": 0, "reasons": ["viewport score unavailable"], "inside_viewport": False}
    center = bbox_center(bbox)
    inside = point_inside_bbox(center, viewport)
    reasons = []
    delta = 0
    if inside:
        delta += 12
        reasons.append("inside main plan viewport")
    else:
        delta -= 25
        reasons.append("outside main plan viewport")
    if image_size and near_sheet_edge(bbox, image_size):
        delta -= 18
        reasons.append("near sheet edge")
    if image_size and in_title_block_band(bbox, image_size):
        delta -= 25
        reasons.append("likely title block or notes region")
    if image_size and in_sheet_furniture_zone(bbox, image_size):
        delta -= 22
        reasons.append("likely logo, legend, notes, or approval box region")
    return {"score_delta": delta, "reasons": reasons, "inside_viewport": inside}


def line_candidate(item, transform, viewport, page_number, index):
    points = item.get("pts") or []
    if len(points) >= 2:
        start = pdf_top_point_to_image_px(points[0], transform)
        end = pdf_top_point_to_image_px(points[-1], transform)
    else:
        start = pdf_top_point_to_image_px([item.get("x0"), item.get("top")], transform)
        end = pdf_top_point_to_image_px([item.get("x1"), item.get("bottom")], transform)
    if not start or not end:
        return None
    length = round(math.dist(start, end), 2)
    orientation = line_orientation(start, end)
    score = score_line(length, orientation, item.get("linewidth"))
    return {
        "candidate_id": f"P{page_number}-VLINE-{index:04d}",
        "geometry_type": "line",
        "start_px": start,
        "end_px": end,
        "points_plan_px": [image_point_to_plan_px(start, viewport), image_point_to_plan_px(end, viewport)],
        "length_px": length,
        "orientation": orientation,
        "source": "pdf_vector",
        "stroke_width": item.get("linewidth"),
        "candidate_role_hint": score["role"],
        "confidence_score": score["score"],
        "classification_reasons": score["reasons"],
        "confidence": confidence_label(score["score"]),
        "needs_review": True,
    }


def wall_band(points, bbox):
    """Centreline of a closed polygon that reads as a wall drawn as a filled band.

    Walls on architectural fitout sheets are frequently filled or hatched polygons
    rather than stroked lines, so they never appear in line_candidates. The polygon
    itself is extracted, though, and a long thin closed one is almost always a wall
    band whose centreline is the wall. This only describes the shape; deciding that a
    given band confirms a given wall is left to geometry confirmation, which also
    requires it to line up with a wall the vision model proposed.
    """
    if len(points) < 4:
        return None
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    thickness = min(width, height)
    length = max(width, height)
    if thickness <= 0 or length < MIN_WALL_BAND_LENGTH_PX:
        return None
    if thickness > MAX_WALL_BAND_THICKNESS_PX or length / thickness < MIN_WALL_BAND_ASPECT:
        return None
    if width >= height:
        middle = round((bbox[1] + bbox[3]) / 2, 2)
        start, end = [bbox[0], middle], [bbox[2], middle]
    else:
        middle = round((bbox[0] + bbox[2]) / 2, 2)
        start, end = [middle, bbox[1]], [middle, bbox[3]]
    return {
        "centreline_start_px": start,
        "centreline_end_px": end,
        "thickness_px": round(thickness, 2),
        "length_px": round(length, 2),
    }


def curve_candidate(item, transform, viewport, page_number, index):
    points = curve_points(item, transform)
    if len(points) < 2:
        return None
    bbox = bbox_from_points(points)
    score = score_curve(points, bbox, item.get("linewidth"))
    band = wall_band(points, bbox)
    return {
        "candidate_id": f"P{page_number}-VCURVE-{index:04d}",
        "geometry_type": "curve_polyline",
        "points_px": points,
        "points_plan_px": [image_point_to_plan_px(point, viewport) for point in points],
        "point_count": len(points),
        "bbox_px": bbox,
        "source": "pdf_vector",
        "stroke_width": item.get("linewidth"),
        "candidate_role_hint": "possible_wall_band" if band else score["role"],
        "wall_band": band,
        "confidence_score": score["score"],
        "classification_reasons": score["reasons"] + (["closed long thin polygon; reads as a filled wall band"] if band else []),
        "confidence": confidence_label(score["score"]),
        "needs_review": True,
    }


def rect_candidate(item, transform, viewport, page_number, index):
    left = item.get("x0")
    right = item.get("x1")
    top = item.get("top")
    bottom = item.get("bottom")
    if not all(isinstance(value, (int, float)) for value in [left, right, top, bottom]):
        return None
    bbox_px = [
        round((left - transform["bbox_left"]) * transform["x_scale"], 2),
        round((top - transform["bbox_top"]) * transform["y_scale"], 2),
        round((right - transform["bbox_left"]) * transform["x_scale"], 2),
        round((bottom - transform["bbox_top"]) * transform["y_scale"], 2),
    ]
    return {
        "candidate_id": f"P{page_number}-VRECT-{index:04d}",
        "geometry_type": "rect",
        "bbox_px": bbox_px,
        "bbox_plan_px": image_bbox_to_plan_px(bbox_px, viewport),
        "source": "pdf_vector",
        "confidence": "low",
        "needs_review": True,
    }


def dimension_text_candidates(words, transform, viewport, page_number, marker_bboxes=()):
    candidates = []
    converted_words = image_words(words, transform)
    for index, word in enumerate(words, 1):
        text = word.get("text", "").strip()
        value = dimension_value(text)
        if value is None:
            continue
        bbox_px = [
            round((word["x0"] - transform["bbox_left"]) * transform["x_scale"], 2),
            round((word["top"] - transform["bbox_top"]) * transform["y_scale"], 2),
            round((word["x1"] - transform["bbox_left"]) * transform["x_scale"], 2),
            round((word["bottom"] - transform["bbox_top"]) * transform["y_scale"], 2),
        ]
        candidate = {
            "candidate_id": f"P{page_number}-VDIMTXT-{index:04d}",
            "text_seen": text,
            "value_mm": value,
            "bbox_px": bbox_px,
            "bbox_plan_px": image_bbox_to_plan_px(bbox_px, viewport),
            "source": "pdf_text_layer",
            "confidence": "medium",
            "needs_review": True,
        }
        candidate.update(classify_numeric_annotation(text, bbox_px, converted_words, marker_bboxes) or {})
        candidates.append(candidate)
    return candidates


def image_words(words, transform):
    """Convert PDF text boxes once for nearby-number/callout checks."""
    result = []
    for word in words:
        if not word.get("text"):
            continue
        result.append(
            {
                "text": word["text"].strip(),
                "bbox_px": [
                    round((word["x0"] - transform["bbox_left"]) * transform["x_scale"], 2),
                    round((word["top"] - transform["bbox_top"]) * transform["y_scale"], 2),
                    round((word["x1"] - transform["bbox_left"]) * transform["x_scale"], 2),
                    round((word["bottom"] - transform["bbox_top"]) * transform["y_scale"], 2),
                ],
            }
        )
    return result


def dimension_value(text):
    clean = text.replace(",", "").strip()
    if not clean.isdigit():
        return None
    value = int(clean)
    if 100 <= value <= 50000:
        return value
    return None


def score_line(length, orientation, stroke_width):
    score = 0
    reasons = []
    if length >= 160:
        score += 35
        reasons.append("long line")
    elif length >= 80:
        score += 24
        reasons.append("medium line")
    elif length >= 35:
        score += 12
        reasons.append("short but usable line")
    else:
        reasons.append("very short line")

    if orientation in ["horizontal", "vertical"]:
        score += 16
        reasons.append("orthogonal plan-like direction")
    elif orientation == "angled":
        score += 10
        reasons.append("angled plan-like direction")

    if isinstance(stroke_width, (int, float)):
        if stroke_width >= 0.45:
            score += 24
            reasons.append("heavier stroke")
        elif stroke_width >= 0.2:
            score += 10
            reasons.append("normal drawing stroke")
        else:
            score -= 8
            reasons.append("very thin stroke")

    role = "possible_wall_or_dimension" if score >= MIN_WALL_SCORE else "vector_context"
    if length < MIN_LINE_LENGTH_PX:
        role = "likely_noise"
    return {"score": max(0, min(100, score)), "role": role, "reasons": reasons}


def score_curve(points, bbox, stroke_width):
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    span = max(width, height)
    score = 0
    reasons = []
    if span >= 160:
        score += 35
        reasons.append("large curve span")
    elif span >= 70:
        score += 22
        reasons.append("medium curve span")
    else:
        reasons.append("small curve")

    if len(points) >= 4:
        score += 18
        reasons.append("multiple curve points")

    if isinstance(stroke_width, (int, float)):
        if stroke_width >= 0.35:
            score += 18
            reasons.append("heavier curve stroke")
        elif stroke_width < 0.1:
            score -= 6
            reasons.append("very thin curve stroke")

    role = "possible_curved_wall_or_symbol" if score >= MIN_WALL_SCORE else "vector_context"
    return {"score": max(0, min(100, score)), "role": role, "reasons": reasons}


def confidence_label(score):
    if score >= 75:
        return "high"
    if score >= MIN_WALL_SCORE:
        return "medium"
    return "low"


def pdf_top_point_to_image_px(point, transform):
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    x, top = point[0], point[1]
    if not isinstance(x, (int, float)) or not isinstance(top, (int, float)):
        return None
    return [
        round((x - transform["bbox_left"]) * transform["x_scale"], 2),
        round((top - transform["bbox_top"]) * transform["y_scale"], 2),
    ]


def curve_points(item, transform):
    points = []
    for point in item.get("pts", []) or []:
        points.append(pdf_top_point_to_image_px(point, transform))
    return thin_points([point for point in points if point])


def thin_points(points, max_points=24):
    if len(points) <= max_points:
        return points
    step = max(1, math.ceil(len(points) / max_points))
    thinned = points[::step]
    if thinned[-1] != points[-1]:
        thinned.append(points[-1])
    return thinned


def image_point_to_plan_px(point, viewport):
    if not valid_point(point) or not valid_bbox(viewport):
        return None
    left, _top, _right, bottom = viewport
    return [round(point[0] - left, 2), round(bottom - point[1], 2)]


def image_bbox_to_plan_px(bbox, viewport):
    if not valid_bbox(bbox) or not valid_bbox(viewport):
        return None
    left, top, right, bottom = bbox
    p0 = image_point_to_plan_px([left, bottom], viewport)
    p1 = image_point_to_plan_px([right, top], viewport)
    return [p0[0], p0[1], p1[0], p1[1]] if p0 and p1 else None


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)


def valid_bbox(bbox):
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(value, (int, float)) for value in bbox)
        and bbox[2] > bbox[0]
        and bbox[3] > bbox[1]
    )


def line_orientation(start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if abs(dx) >= abs(dy) * 4:
        return "horizontal"
    if abs(dy) >= abs(dx) * 4:
        return "vertical"
    return "angled"


def bbox_from_points(points):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def item_bbox(item):
    if not isinstance(item, dict):
        return None
    if item.get("geometry_type") == "line" or (item.get("start_px") and item.get("end_px")):
        return line_bbox(item.get("start_px"), item.get("end_px"))
    if item.get("bbox_px"):
        return item.get("bbox_px")
    if item.get("points_px"):
        return bbox_from_points(item.get("points_px"))
    return None


def line_bbox(start, end):
    if not valid_point(start) or not valid_point(end):
        return None
    left = min(start[0], end[0])
    top = min(start[1], end[1])
    right = max(start[0], end[0])
    bottom = max(start[1], end[1])
    if right == left:
        right += 1
    if bottom == top:
        bottom += 1
    return [left, top, right, bottom]


def union_bbox(boxes):
    boxes = [box for box in boxes if valid_bbox(box)]
    return [min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)]


def padded_bbox(bbox, width, height, pad):
    return [
        round(max(0, bbox[0] - pad), 2),
        round(max(0, bbox[1] - pad), 2),
        round(min(width, bbox[2] + pad), 2),
        round(min(height, bbox[3] + pad), 2),
    ]


def bbox_center(bbox):
    if not valid_bbox(bbox):
        return None
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def bbox_area(bbox):
    if not valid_bbox(bbox):
        return 0
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def bbox_span(bbox):
    if not valid_bbox(bbox):
        return 0
    return max(bbox[2] - bbox[0], bbox[3] - bbox[1])


def point_inside_bbox(point, bbox):
    return valid_point(point) and valid_bbox(bbox) and bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def near_sheet_edge(bbox, image_size):
    if not valid_bbox(bbox) or not image_size:
        return False
    margin_x = image_size[0] * 0.035
    margin_y = image_size[1] * 0.035
    return bbox[0] <= margin_x or bbox[1] <= margin_y or bbox[2] >= image_size[0] - margin_x or bbox[3] >= image_size[1] - margin_y


def in_title_block_band(bbox, image_size):
    if not valid_bbox(bbox) or not image_size:
        return False
    center = bbox_center(bbox)
    return bool(center and center[1] >= image_size[1] * 0.82)


def in_sheet_furniture_zone(bbox, image_size):
    if not valid_bbox(bbox) or not image_size:
        return False
    center = bbox_center(bbox)
    if not center:
        return False
    width, height = image_size
    x, y = center
    top_logo_or_approval = y <= height * 0.30 and (x <= width * 0.22 or x >= width * 0.72)
    bottom_notes_or_title = y >= height * 0.70 and x >= width * 0.68
    left_notes_band = x <= width * 0.16 and y <= height * 0.65
    return top_logo_or_approval or bottom_notes_or_title or left_notes_band


def large_sheet_box(bbox, image_size):
    if not valid_bbox(bbox) or not image_size:
        return False
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width >= image_size[0] * 0.82 or height >= image_size[1] * 0.82


def create_vector_overlays(pages, screenshots_dir, overlays_dir):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return [{"status": "skipped", "reason": "Pillow is not installed"}]

    screenshots_dir = Path(screenshots_dir)
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    overlays = []

    for page in pages:
        image_name = Path(page.get("image", "")).name
        image_path = screenshots_dir / image_name
        if not image_name or not image_path.exists():
            overlays.append({"page": page.get("page"), "status": "missing_image", "image": image_name})
            continue

        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw_bbox(draw, page.get("plan_viewport", {}).get("bbox_px"), "orange", 5)
        for item in page.get("line_candidates", [])[:300]:
            draw_line(draw, item.get("start_px"), item.get("end_px"), "red", 2)
        for item in page.get("curve_candidates", [])[:180]:
            draw_polyline(draw, item.get("points_px"), "deepskyblue", 3)
        for item in page.get("dimension_candidates", [])[:80]:
            draw_bbox(draw, item.get("bbox_px"), "yellow", 2)

        output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_vector_overlay.png"
        image.save(output)
        overlays.append({"page": page.get("page"), "status": "created", "path": str(output)})
    return overlays


def draw_line(draw, start, end, color, width):
    if valid_point(start) and valid_point(end):
        draw.line([tuple(start), tuple(end)], fill=color, width=width)


def draw_polyline(draw, points, color, width):
    if isinstance(points, list) and len(points) >= 2 and all(valid_point(point) for point in points):
        draw.line([tuple(point) for point in points], fill=color, width=width)


def draw_bbox(draw, bbox, color, width):
    if valid_bbox(bbox):
        draw.rectangle(bbox, outline=color, width=width)


def main():
    parser = argparse.ArgumentParser(description="Extract vector wall key-point candidates from reviewed PDF pages.")
    parser.add_argument("ai_input")
    parser.add_argument("--output", help="Output path; defaults to vector_geometry.json beside ai_input.json")
    parser.add_argument("--screenshots-dir", help="Folder containing high-resolution packet screenshots")
    parser.add_argument("--overlays-dir", help="Folder where vector overlay PNGs should be saved")
    args = parser.parse_args()

    output = create_vector_geometry(args.ai_input, args.output, args.screenshots_dir, args.overlays_dir)
    print(f"Vector geometry created: {output}")


if __name__ == "__main__":
    main()
