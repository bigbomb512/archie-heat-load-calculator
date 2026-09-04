#!/usr/bin/env python3

import argparse
import json
import math
import re
from pathlib import Path

from ai.ai_packet import load_json


MAX_MATCHES_PER_PAGE = 140
MAX_REJECTIONS_PER_PAGE = 180
BOUNDARY_DIMENSION_VALUES = {8075, 8205, 6585, 5535, 5655, 3650, 3530, 1810, 950, 670}
LOCAL_FIXTURE_DIMENSION_VALUES = {600, 800, 880, 1000, 1200, 1500}
BOUNDARY_DIMENSION_WORDS = ["overall", "c.o.s", "cos", "boundary", "lease", "setout", "set out"]
DEFAULT_RENDER_DPI = 180


def page_scale_denominator(scale_candidates):
    """Pick the denominator of the scale that governs the main plan on a sheet.

    A sheet often carries several scales: the plan plus detail/enlargement callouts.
    The plan is always the most zoomed-out drawing, so it has the largest denominator
    (1:40 plan alongside 1:5 joint details). Taking the first or the most common value
    picks a detail callout instead.
    """
    best = 0
    for item in scale_candidates or []:
        text = item.get("text", "") if isinstance(item, dict) else item
        match = re.search(r"1\s*[:/]\s*(\d+)", str(text or ""))
        if match:
            best = max(best, int(match.group(1)))
    return best or None


def page_mm_per_px(scale_candidates, render_dpi=DEFAULT_RENDER_DPI):
    """Exact millimetres per screenshot pixel from render dpi and drawing scale.

    A pixel is 25.4/dpi millimetres on the sheet, and the sheet is 1:denominator, so a
    pixel is (25.4/dpi) * denominator millimetres on the building. This is exact and does
    not need to be inferred from dimension-line lengths, which overshoot their stated
    values and bias the estimate low.
    """
    denominator = page_scale_denominator(scale_candidates)
    if not denominator or not render_dpi:
        return None
    return (25.4 / float(render_dpi)) * denominator


def scale_candidates_by_page(vector_geometry):
    """Per-page scale candidates, read from the ai_input packet the geometry came from."""
    source = vector_geometry.get("source_ai_input")
    if not source or not Path(source).exists():
        return {}
    pages = load_json(Path(source)).get("spatial_ocr", {}).get("pages", [])
    return {page.get("page"): page.get("scale_candidates", []) for page in pages if page.get("page")}


def create_dimension_wall_matches(vector_geometry_path, output_path=None, overlays_dir=None, screenshots_dir=None, render_dpi=DEFAULT_RENDER_DPI):
    vector_geometry_path = Path(vector_geometry_path)
    vector_geometry = load_json(vector_geometry_path)
    output_path = Path(output_path or vector_geometry_path.with_name("dimension_wall_matches.json"))
    overlays_dir = Path(overlays_dir or vector_geometry_path.with_name("dimension_match_overlays"))
    screenshots_dir = Path(screenshots_dir) if screenshots_dir else vector_geometry_path.with_name("chatgpt_packet") / "screenshots"

    scale_candidates = scale_candidates_by_page(vector_geometry)
    pages = [
        match_page_dimensions(
            page,
            mm_per_px=page_mm_per_px(scale_candidates.get(page.get("page")), render_dpi),
        )
        for page in vector_geometry.get("geometry_key_points", {}).get("pages", [])
    ]
    output = {
        "source_vector_geometry": str(vector_geometry_path),
        "pages": pages,
        "overlays": create_match_overlays(pages, screenshots_dir, overlays_dir),
        "note": "Dimension spans are neutral evidence. The vision review creates wall links after identifying physical walls.",
    }
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def match_page_dimensions(page, mm_per_px=None):
    lines = page.get("line_candidates", [])
    dimensions, unknown_numeric_annotations = usable_dimension_texts(page)
    dimension_lines = classify_dimension_lines(lines, dimensions)
    rejections = []
    spans = []
    for text in dimensions:
        for dim_line in ranked_dimension_lines(text, dimension_lines)[:3]:
            witness = witness_lines_for(dim_line, lines)
            span = measured_span(dim_line, witness)
            spans.append(dimension_span(page, text, dim_line, witness, span, mm_per_px))
        if not any(item.get("dimension_candidate_id") == text.get("candidate_id") for item in spans):
            rejections.append(
                {
                    "dimension_candidate_id": text.get("candidate_id", ""),
                    "text_seen": text.get("text_seen", ""),
                    "reason": "no clear vector dimension line; retain as visual dimension evidence",
                    "status": "unmatched",
                }
            )

    spans = dedupe_dimension_spans(spans)
    categories = dimension_category_summary(dimensions, [], rejections)
    return {
        "page": page.get("page"),
        "image": page.get("image", ""),
        "plan_role": page.get("plan_role", ""),
        "coordinate_systems": page.get("coordinate_systems", {}),
        "dimension_lines": dimension_lines[:220],
        "dimension_span_candidates": spans[:MAX_MATCHES_PER_PAGE],
        "rule_verified": [],
        "rule_candidates": [],
        "dimension_wall_matches": [],
        "rejected_or_unmatched": rejections[:MAX_REJECTIONS_PER_PAGE],
        "summary": {
            "dimension_text_count": len(dimensions),
            "dimension_line_count": len(dimension_lines),
            "dimension_span_count": len(spans),
            "major_boundary_dimensions": categories["major_boundary_dimensions"],
            "local_fixture_dimensions": categories["local_fixture_dimensions"],
            "unassigned_dimensions": categories["unassigned_dimensions"],
            "unknown_numeric_annotations": unknown_numeric_annotations[:80],
            "machine_extraction_gaps": machine_extraction_gaps(page, categories),
        },
    }


def usable_dimension_texts(page):
    dimensions = []
    unknown = []
    image_size = image_size_for(page)
    viewport = plan_viewport(page)
    for text in page.get("dimension_candidates", []):
        value = text.get("value_mm")
        center = bbox_center(text.get("bbox_px"))
        if not isinstance(value, (int, float)) or value < 400:
            continue
        if center and image_size and center[1] > image_size[1] * 0.82 and not point_inside_bbox(center, viewport):
            continue
        item = dict(text)
        kind = item.get("annotation_kind", "written_dimension")
        eligibility = item.get("dimension_eligibility", "eligible")
        if kind == "detail_or_sheet_reference" or eligibility == "ineligible":
            continue
        if kind == "unknown_numeric" or eligibility == "vision_review":
            unknown.append(item)
            continue
        item["dimension_category"] = dimension_category(item)
        item["dimension_priority"] = dimension_priority(item)
        dimensions.append(item)
    return sorted(dimensions, key=lambda item: item["dimension_priority"], reverse=True), unknown


def dimension_category(text):
    value = text.get("value_mm")
    seen = f"{text.get('text_seen', '')} {text.get('context', '')} {text.get('nearby_text', '')}".lower()
    if any(word in seen for word in BOUNDARY_DIMENSION_WORDS):
        return "major_boundary"
    if value in BOUNDARY_DIMENSION_VALUES:
        return "major_boundary"
    if value in LOCAL_FIXTURE_DIMENSION_VALUES or value <= 1500:
        return "local_fixture"
    return "setout_or_opening"


def dimension_priority(text):
    category = text.get("dimension_category") or dimension_category(text)
    value = text.get("value_mm") or 0
    if category == "major_boundary":
        return 400000 + value
    if category == "setout_or_opening":
        return 200000 + value
    return 1


def dimension_category_summary(dimensions, matched, rejections):
    matched_ids = {item.get("dimension_text_candidate_id") for item in matched}
    rejected_ids = {item.get("dimension_candidate_id") for item in rejections}
    major = []
    local = []
    unassigned = []
    for item in dimensions:
        compact = compact_dimension(item)
        if item.get("dimension_category") == "major_boundary":
            major.append(compact)
        elif item.get("dimension_category") == "local_fixture":
            local.append(compact)
        if item.get("candidate_id") not in matched_ids:
            compact["status"] = "unassigned" if item.get("candidate_id") in rejected_ids else "candidate_not_selected"
            unassigned.append(compact)
    return {
        "major_boundary_dimensions": major[:40],
        "local_fixture_dimensions": local[:40],
        "unassigned_dimensions": unassigned[:80],
    }


def compact_dimension(item):
    return {
        "candidate_id": item.get("candidate_id", ""),
        "text_seen": item.get("text_seen", ""),
        "value_mm": item.get("value_mm"),
        "dimension_category": item.get("dimension_category", ""),
        "bbox_px": item.get("bbox_px"),
        "unit_assumption": "mm",
    }


def machine_extraction_gaps(page, categories):
    gaps = []
    if page.get("plan_role") == "main_floor_plan" and not categories["major_boundary_dimensions"]:
        gaps.append(
            {
                "type": "missing_major_boundary_dimensions",
                "message": "No major boundary dimensions were found in the PDF text layer. The vision model must inspect the high-resolution screenshot for visible overall/setout dimensions.",
                "action": "Return screenshot-visible dimensions in vision_response.json with source set to screenshot_visible.",
            }
        )
    return gaps


def image_size_for(page):
    systems = page.get("coordinate_systems", {})
    image = systems.get("image_px", {}) if isinstance(systems, dict) else {}
    width = image.get("image_width")
    height = image.get("image_height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return [width, height]
    return None


def plan_viewport(page):
    systems = page.get("coordinate_systems", {})
    plan = systems.get("plan_px", {}) if isinstance(systems, dict) else {}
    viewport = plan.get("plan_viewport_bbox_px")
    return viewport if valid_bbox(viewport) else None


def classify_dimension_lines(lines, dimensions):
    classified = []
    for line in lines:
        start = line.get("start_px")
        end = line.get("end_px")
        if not valid_point(start) or not valid_point(end):
            continue
        length = line_length(start, end)
        width = line.get("stroke_width", 0) or 0
        if length < 30:
            role = "noise"
        elif width >= 0.45 and length >= 80:
            role = "vector_context"
        elif has_nearby_dimension_text(line, dimensions):
            role = "dimension_line"
        elif length <= 180 and width <= 0.35:
            role = "possible_witness_line"
        elif line.get("candidate_role_hint") == "possible_wall_or_dimension":
            role = "wall_or_dimension_context"
        else:
            role = "vector_context"
        item = dict(line)
        item["vector_role"] = role
        classified.append(item)
    return [item for item in classified if item["vector_role"] in {"dimension_line", "possible_witness_line", "wall_or_dimension_context"}]


def has_nearby_dimension_text(line, dimensions):
    return any(text_line_score(text, line) >= 35 for text in dimensions)


def ranked_dimension_lines(text, lines):
    ranked = []
    for line in lines:
        score = text_line_score(text, line)
        if score >= 30:
            candidate = dict(line)
            candidate["text_line_score"] = score
            ranked.append(candidate)
    return sorted(ranked, key=lambda item: item["text_line_score"], reverse=True)


def text_line_score(text, line):
    center = bbox_center(text.get("bbox_px"))
    start = line.get("start_px")
    end = line.get("end_px")
    if not center or not valid_point(start) or not valid_point(end):
        return 0
    distance = point_line_distance(center, start, end)
    projection = projection_ratio(center, start, end)
    length = line_length(start, end)
    score = 0
    if -0.15 <= projection <= 1.15:
        score += 30
    if distance <= 55:
        score += max(12, 45 - distance * 0.8)
    elif distance <= 110:
        score += max(4, 18 - (distance - 55) * 0.25)
    if length >= 90:
        score += 12
    if line.get("vector_role") == "dimension_line":
        score += 8
    return score


def witness_lines_for(dim_line, lines):
    witnesses = []
    dim_start = dim_line.get("start_px")
    dim_end = dim_line.get("end_px")
    for line in lines:
        start = line.get("start_px")
        end = line.get("end_px")
        if not valid_point(start) or not valid_point(end):
            continue
        length = line_length(start, end)
        if length < 12 or length > 220:
            continue
        if not roughly_perpendicular(dim_start, dim_end, start, end):
            continue
        near_start = min(point_distance(start, dim_start), point_distance(end, dim_start))
        near_end = min(point_distance(start, dim_end), point_distance(end, dim_end))
        if near_start <= 38 or near_end <= 38:
            witnesses.append(
                {
                    "candidate_id": line.get("candidate_id", ""),
                    "line_start_px": start,
                    "line_end_px": end,
                    "near_dimension_start": near_start <= 38,
                    "near_dimension_end": near_end <= 38,
                }
            )
    return witnesses[:8]


def measured_span(dim_line, witnesses):
    start = dim_line.get("start_px")
    end = dim_line.get("end_px")
    if not witnesses:
        return {"start_px": start, "end_px": end, "source": "dimension_line_endpoints"}

    start_witness = next((line for line in witnesses if line["near_dimension_start"]), None)
    end_witness = next((line for line in witnesses if line["near_dimension_end"]), None)
    if not start_witness or not end_witness:
        return {"start_px": start, "end_px": end, "source": "dimension_line_endpoints_partial_witness"}
    return {
        "start_px": closest_endpoint_to(start_witness, start),
        "end_px": closest_endpoint_to(end_witness, end),
        "source": "witness_lines",
    }


def dimension_span(page, text, dim_line, witnesses, span, mm_per_px):
    """Return a detected dimension span without selecting a physical wall."""
    start = span.get("start_px")
    end = span.get("end_px")
    length = line_length(start, end) if valid_point(start) and valid_point(end) else 0
    return {
        "dimension_candidate_id": text.get("candidate_id", ""),
        "page": page.get("page"),
        "text_seen": text.get("text_seen", ""),
        "value_mm": text.get("value_mm"),
        "unit_assumption": "mm",
        "dimension_category": text.get("dimension_category", "unknown"),
        "dimension_priority": text.get("dimension_priority", 0),
        "dimension_text_bbox_px": text.get("bbox_px"),
        "dimension_line_candidate_id": dim_line.get("candidate_id", ""),
        "dimension_line_start_px": dim_line.get("start_px"),
        "dimension_line_end_px": dim_line.get("end_px"),
        "witness_lines_px": [[item["line_start_px"], item["line_end_px"]] for item in witnesses],
        "witness_line_candidate_ids": [item.get("candidate_id", "") for item in witnesses],
        "measured_span_start_px": start,
        "measured_span_end_px": end,
        "measured_span_source": span.get("source"),
        "span_length_px": round(length, 2),
        "implied_span_mm": round(length * mm_per_px) if mm_per_px and length else None,
        "site_confirm_required": has_cos(text),
        "status": "vision_review_required",
        "reason": "code detected a dimension span but does not select a wall target",
    }


def dedupe_dimension_spans(spans):
    seen = set()
    result = []
    for item in sorted(spans, key=lambda value: value.get("dimension_priority", 0), reverse=True):
        key = (item.get("dimension_candidate_id"), item.get("dimension_line_candidate_id"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def create_match_overlays(pages, screenshots_dir, overlays_dir):
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
        for span in page.get("dimension_span_candidates", []):
            draw_line(draw, span.get("dimension_line_start_px"), span.get("dimension_line_end_px"), "blue", 4)
            draw_line(draw, span.get("measured_span_start_px"), span.get("measured_span_end_px"), "orange", 4)
            draw_bbox(draw, span.get("dimension_text_bbox_px"), "yellow", 3)
        output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_dimension_match_overlay.png"
        image.save(output)
        overlays.append({"page": page.get("page"), "status": "created", "path": str(output)})
    return overlays


def has_cos(text):
    value = str(text.get("text_seen", "")).lower()
    return "c.o.s" in value or "cos" in value


def bbox_center(bbox):
    if not valid_bbox(bbox):
        return None
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def point_inside_bbox(point, bbox):
    return valid_point(point) and valid_bbox(bbox) and bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)


def valid_bbox(bbox):
    return isinstance(bbox, list) and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]


def line_length(start, end):
    return math.dist(start, end)


def point_distance(a, b):
    return math.dist(a, b)


def line_orientation(start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if abs(dx) >= abs(dy) * 4:
        return "horizontal"
    if abs(dy) >= abs(dx) * 4:
        return "vertical"
    return "angled"


def point_line_distance(point, start, end):
    length = line_length(start, end)
    if length == 0:
        return point_distance(point, start)
    return abs((end[0] - start[0]) * (start[1] - point[1]) - (start[0] - point[0]) * (end[1] - start[1])) / length


def projection_ratio(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0
    return ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denom


def roughly_perpendicular(a0, a1, b0, b1):
    diff = angle_diff(a0, a1, b0, b1)
    return abs(diff - math.pi / 2) <= math.radians(14)


def angle_diff(a0, a1, b0, b1):
    angle_a = math.atan2(a1[1] - a0[1], a1[0] - a0[0])
    angle_b = math.atan2(b1[1] - b0[1], b1[0] - b0[0])
    return abs((angle_a - angle_b + math.pi / 2) % math.pi - math.pi / 2)


def closest_endpoint_to(line, point):
    start = line["line_start_px"]
    end = line["line_end_px"]
    return start if point_distance(start, point) <= point_distance(end, point) else end


def draw_line(draw, start, end, color, width):
    if valid_point(start) and valid_point(end):
        draw.line([tuple(start), tuple(end)], fill=color, width=width)


def draw_bbox(draw, bbox, color, width):
    if valid_bbox(bbox):
        draw.rectangle(bbox, outline=color, width=width)


def main():
    parser = argparse.ArgumentParser(description="Match dimension text and dimension spans to vector wall candidates.")
    parser.add_argument("vector_geometry")
    parser.add_argument("--output", help="Output path; defaults to dimension_wall_matches.json beside vector_geometry.json")
    parser.add_argument("--screenshots-dir", help="Folder containing high-resolution packet screenshots")
    parser.add_argument("--overlays-dir", help="Folder where dimension match overlay PNGs should be saved")
    args = parser.parse_args()

    output = create_dimension_wall_matches(args.vector_geometry, args.output, args.overlays_dir, args.screenshots_dir)
    print(f"Dimension-wall matches created: {output}")


if __name__ == "__main__":
    main()
