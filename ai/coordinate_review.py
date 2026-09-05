#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

from ai.ai_packet import load_json
from ai.geometry_review import normalise_vision
from ai.vision_validator import validate_vision, vision_pages


def create_coordinate_review(vision_path, output_path=None, screenshots_dir=None, overlays_dir=None, candidate_review_path=None):
    vision_path = Path(vision_path)
    vision = load_json(vision_path)
    candidate_review = load_json(candidate_review_path) if candidate_review_path else None
    vision = normalise_vision(vision, candidate_review)
    validation = validate_vision(vision, candidate_review)
    result = vision.get("result", {})
    overlays = create_overlays(result, screenshots_dir, overlays_dir)
    conversions = scale_conversions(result, validation)

    output = {
        "source": str(vision_path),
        "provider": vision.get("provider", ""),
        "model": vision.get("model", ""),
        "coordinate_systems": coordinate_systems(result),
        "wall_candidates": with_status(add_plan_coordinates_to_items(result, "wall_candidates"), validation),
        "dimension_candidates": with_status(add_plan_coordinates_to_items(result, "dimension_candidates"), validation),
        "room_label_candidates": with_status(add_plan_coordinates_to_items(result, "room_label_candidates"), validation),
        "opening_candidates": with_status(add_plan_coordinates_to_items(result, "opening_candidates"), validation),
        "fixed_obstacle_candidates": with_status(add_plan_coordinates_to_items(result, "fixed_obstacle_candidates"), validation),
        "proposed_wall_dimension_links": wall_dimensions_with_status(result, validation),
        "scale_conversions": conversions,
        "provisional_cad_geometry": provisional_cad_geometry(result, conversions),
        "overlays": overlays,
        "validation": validation,
        "note": "Coordinate evidence is review material only. Do not use for CAD or calculations until validated and human-confirmed.",
    }
    output_path = Path(output_path or vision_path.with_name("coordinate_review.json"))
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def coordinate_systems(result):
    systems = []
    for page in vision_pages(result):
        system = page.get("coordinate_system", {})
        viewport = page.get("plan_viewport_bbox_px")
        systems.append(
            {
                "page": page.get("page"),
                "image": page.get("image", ""),
                "image_px": {
                    "origin": "top_left_full_screenshot",
                    "x_axis": "right",
                    "y_axis": "down",
                    "image_width": system.get("image_width"),
                    "image_height": system.get("image_height"),
                    "units": system.get("units", "image_px"),
                },
                "plan_px": {
                    "origin": "bottom_left_plan_viewport",
                    "x_axis": "right",
                    "y_axis": "up",
                    "plan_viewport_bbox_px": viewport,
                    "plan_viewport_confidence": page.get("plan_viewport_confidence", ""),
                    "plan_viewport_uncertainties": page.get("plan_viewport_uncertainties", []),
                    "units": "plan_px",
                },
            }
        )
    return systems


def collect_candidates(result, key):
    candidates = []
    for page in vision_pages(result):
        for item in page.get(key, []):
            candidates.append(dict(item, page=page.get("page"), image=page.get("image", "")))
    return candidates


def collect_wall_dimensions(result):
    links = []
    for page in vision_pages(result):
        for item in page.get("wall_dimensions", []):
            links.append(dict(item, page=page.get("page"), image=page.get("image", "")))
    return links


def wall_dimensions_with_status(result, validation):
    usable_ids = set(validation.get("usable_measurement_ids", []))
    links = []
    for item in add_plan_coordinates_to_wall_dimensions(result):
        link = dict(item)
        link["approval_status"] = approval_status(link, validation, usable_ids)
        link["site_confirm_required"] = has_cos_note(link)
        links.append(link)
    return links


def add_plan_coordinates_to_items(result, key):
    candidates = []
    for page in vision_pages(result):
        viewport = page.get("plan_viewport_bbox_px")
        for item in page.get(key, []):
            converted = dict(item, page=page.get("page"), image=page.get("image", ""))
            converted.update(plan_coordinate_fields(item, viewport))
            candidates.append(converted)
    return candidates


def add_plan_coordinates_to_wall_dimensions(result):
    links = []
    for page in vision_pages(result):
        viewport = page.get("plan_viewport_bbox_px")
        for item in page.get("wall_dimensions", []):
            converted = dict(item, page=page.get("page"), image=page.get("image", ""))
            converted.update(plan_coordinate_fields(item, viewport))
            links.append(converted)
    return links


def plan_coordinate_fields(item, viewport):
    fields = {}
    point_fields = [
        "line_start_px",
        "line_end_px",
        "dimension_line_start_px",
        "dimension_line_end_px",
        "arrowhead_start_px",
        "arrowhead_end_px",
        "measured_span_start_px",
        "measured_span_end_px",
        "target_wall_start_px",
        "target_wall_end_px",
        "centre_px",
    ]
    bbox_fields = ["bbox_px", "dimension_text_bbox_px"]
    for field in point_fields:
        if field in item:
            fields[field.replace("_px", "_plan_px")] = image_point_to_plan_px(item.get(field), viewport)
    for field in bbox_fields:
        if field in item:
            fields[field.replace("_px", "_plan_px")] = image_bbox_to_plan_px(item.get(field), viewport)
    if "witness_lines_px" in item:
        fields["witness_lines_plan_px"] = [
            [image_point_to_plan_px(line[0], viewport), image_point_to_plan_px(line[1], viewport)]
            for line in item.get("witness_lines_px", [])
            if isinstance(line, list) and len(line) == 2
        ]
    if "points_px" in item:
        fields["points_plan_px"] = [
            image_point_to_plan_px(point, viewport)
            for point in item.get("points_px", [])
            if valid_point(point)
        ]
    if "radius_px" in item:
        fields["radius_plan_px"] = item.get("radius_px")
    return fields


def image_point_to_plan_px(point, viewport):
    if not valid_point(point) or not valid_bbox_values(viewport):
        return None
    left, _top, _right, bottom = viewport
    return [round(point[0] - left, 2), round(bottom - point[1], 2)]


def image_bbox_to_plan_px(bbox, viewport):
    if not isinstance(bbox, list) or len(bbox) != 4 or not valid_bbox_values(viewport):
        return None
    left, top, right, bottom = bbox
    p0 = image_point_to_plan_px([left, bottom], viewport)
    p1 = image_point_to_plan_px([right, top], viewport)
    if not p0 or not p1:
        return None
    return [p0[0], p0[1], p1[0], p1[1]]


def with_status(items, validation):
    return [
        dict(item, approval_status="validator_passed" if not item_issues(item, validation) else "vision_candidate")
        for item in items
    ]


def approval_status(item, validation, usable_ids):
    if item.get("human_decision") == "approved":
        return "overlay_approved"
    if item.get("human_decision") == "rejected":
        return "rejected"
    if item.get("measurement_id") in usable_ids:
        return "validator_passed"
    return "vision_candidate"


def item_issues(item, validation):
    item_id = item.get("measurement_id") or item.get("candidate_id")
    if not item_id:
        return []
    return [
        issue
        for issue in validation.get("issues", [])
        if issue.get("measurement_id") == item_id
    ]


def has_cos_note(item):
    if item.get("site_confirm_required") is True:
        return True
    text = " ".join(str(item.get(key, "")) for key in ["measurement_text", "text_seen", "applies_to"])
    text += " " + " ".join(str(value) for value in item.get("uncertainties", []))
    return "c.o.s" in text.lower() or "cos" in text.lower() or "site confirmation" in text.lower()


def scale_conversions(result, validation):
    conversions = []
    usable_ids = set(validation.get("usable_measurement_ids", []))
    for link in collect_wall_dimensions(result):
        if link.get("measurement_id") not in usable_ids:
            continue
        pixel_length = line_length(link.get("dimension_line_start_px"), link.get("dimension_line_end_px"))
        value_mm = link.get("value_mm")
        if not pixel_length or not isinstance(value_mm, (int, float)):
            continue
        conversions.append(
            {
                "page": link.get("page"),
                "image": link.get("image", ""),
                "source_measurement_id": link.get("measurement_id"),
                "source_value_mm": value_mm,
                "source_pixel_length": pixel_length,
                "mm_per_px": value_mm / pixel_length,
                "approval_status": "provisional",
                "site_confirm_required": has_cos_note(link),
            }
        )
    return conversions


def provisional_cad_geometry(result, conversions):
    by_page = {item["page"]: item for item in conversions}
    geometry = []
    for wall in add_plan_coordinates_to_items(result, "wall_candidates"):
        conversion = by_page.get(wall.get("page"))
        if not conversion:
            continue
        start = to_mm(wall.get("line_start_plan_px"), conversion["mm_per_px"])
        end = to_mm(wall.get("line_end_plan_px"), conversion["mm_per_px"])
        if start and end:
            geometry.append(
                {
                    "candidate_id": wall.get("candidate_id"),
                    "page": wall.get("page"),
                    "source_units": "plan_px",
                    "cad_units": "mm",
                    "start_mm": start,
                    "end_mm": end,
                    "approval_status": "provisional",
                    "source_measurement_id": conversion["source_measurement_id"],
                    "note": "Converted from pixel coordinates. Do not use for final CAD until overlay-approved.",
                }
            )
    return geometry


def line_length(start, end):
    if not valid_point(start) or not valid_point(end):
        return None
    return math.dist(start, end)


def to_mm(point, mm_per_px):
    if not valid_point(point):
        return None
    return [round(point[0] * mm_per_px, 2), round(point[1] * mm_per_px, 2)]


def create_overlays(result, screenshots_dir, overlays_dir):
    if not screenshots_dir or not overlays_dir:
        return []

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return [{"status": "skipped", "reason": "Pillow is not installed"}]

    screenshots_dir = Path(screenshots_dir)
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    overlays = []

    for page in vision_pages(result):
        image_name = page.get("image", "")
        image_path = screenshots_dir / Path(image_name).name
        if not image_path.exists():
            overlays.append({"page": page.get("page"), "status": "missing_image", "image": image_name})
            continue

        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw_bbox(draw, page.get("plan_viewport_bbox_px"), "orange", 6)
        for wall in page.get("wall_candidates", []):
            draw_line(draw, wall.get("line_start_px"), wall.get("line_end_px"), "red", 5)
        for item in page.get("dimension_candidates", []):
            draw_bbox(draw, item.get("bbox_px"), "blue", 3)
        for link in page.get("wall_dimensions", []):
            draw_line(draw, link.get("dimension_line_start_px"), link.get("dimension_line_end_px"), "blue", 4)
            draw_line(draw, link.get("target_wall_start_px"), link.get("target_wall_end_px"), "lime", 6)
            draw_bbox(draw, link.get("dimension_text_bbox_px"), "yellow", 3)

        output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_coordinate_overlay.png"
        image.save(output)
        overlays.append({"page": page.get("page"), "status": "created", "path": str(output)})
    return overlays


def draw_line(draw, start, end, color, width):
    if valid_point(start) and valid_point(end):
        draw.line([tuple(start), tuple(end)], fill=color, width=width)


def draw_bbox(draw, bbox, color, width):
    if isinstance(bbox, list) and len(bbox) == 4:
        draw.rectangle(bbox, outline=color, width=width)


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)


def valid_bbox_values(bbox):
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(value, (int, float)) for value in bbox)
        and bbox[2] > bbox[0]
        and bbox[3] > bbox[1]
    )


def main():
    parser = argparse.ArgumentParser(description="Create coordinate_review.json from a vision model JSON response.")
    parser.add_argument("vision", help="Path to reviewed vision JSON, such as a saved ChatGPT response")
    parser.add_argument("--output", help="Output path; defaults to coordinate_review.json beside the input")
    parser.add_argument("--screenshots-dir", help="Optional folder containing packet screenshots for overlay generation")
    parser.add_argument("--overlays-dir", help="Optional folder where coordinate overlay PNGs should be saved")
    parser.add_argument("--candidate-review", help="Optional candidate_review.json for candidate ID validation")
    args = parser.parse_args()

    output = create_coordinate_review(args.vision, args.output, args.screenshots_dir, args.overlays_dir, args.candidate_review)
    print(f"Coordinate review created: {output}")


if __name__ == "__main__":
    main()
