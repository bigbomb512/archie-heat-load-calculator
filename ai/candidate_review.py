#!/usr/bin/env python3

import argparse
import json
import subprocess
from pathlib import Path

from ai.ai_packet import load_json


MAX_LINES_PER_PAGE = 90
MAX_CURVES_PER_PAGE = 35
MAX_DIMENSIONS_PER_PAGE = 80
INITIAL_DIMENSION_ZONES_PER_PAGE = 4
MAX_ADAPTIVE_DIMENSION_ZONES_PER_PAGE = 12
MAX_RAW_VECTORS_PER_CROP = 12
CROP_IMAGE_SCALE = 2
CROP_RENDER_DPI = 300


def create_candidate_review(vector_geometry_path, dimension_wall_matches_path=None, output_path=None, overlays_dir=None, screenshots_dir=None, crop_dpi=CROP_RENDER_DPI):
    vector_geometry_path = Path(vector_geometry_path)
    vector_geometry = load_json(vector_geometry_path)
    dimension_wall_matches = load_json(dimension_wall_matches_path) if dimension_wall_matches_path else {}
    output_path = Path(output_path or vector_geometry_path.with_name("candidate_review.json"))
    overlays_dir = Path(overlays_dir or vector_geometry_path.with_name("candidate_overlays"))
    screenshots_dir = Path(screenshots_dir) if screenshots_dir else vector_geometry_path.with_name("chatgpt_packet") / "screenshots"

    pages = [
        build_page_review(page, matches_for_page(dimension_wall_matches, page.get("page")))
        for page in vector_geometry.get("geometry_key_points", {}).get("pages", [])
    ]
    overlays = create_candidate_overlays(
        pages,
        screenshots_dir,
        overlays_dir,
        vector_geometry.get("source_pdf"),
        crop_dpi,
    )
    output = {
        "source_vector_geometry": str(vector_geometry_path),
        "source_dimension_wall_matches": str(dimension_wall_matches_path or ""),
        "pages": pages,
        "overlays": overlays,
        "crop_render_dpi": crop_dpi,
        "prompt": vision_prompt(),
        "note": "Candidate review is a focused vision-classification packet. It shortlists machine candidates so the vision model verifies, rejects, or corrects them.",
    }
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def matches_for_page(dimension_wall_matches, page_number):
    for page in dimension_wall_matches.get("pages", []):
        if page.get("page") == page_number:
            return page
    return {}


def build_page_review(page, match_page):
    dimensions, unknown_numeric_annotations = review_dimensions(page, match_page)
    line_roles = classified_line_candidates(page, match_page)
    curves = classified_curve_candidates(page)
    crops = crop_regions(page, line_roles, curves, dimensions)
    add_crop_evidence(crops, line_roles, curves, dimensions)
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "plan_role": page.get("plan_role", ""),
        "image": page.get("image", ""),
        "plan_viewport": page.get("plan_viewport", {}),
        "coordinate_systems": page.get("coordinate_systems", {}),
        "crops": crops,
        "candidate_ids": candidate_ids(line_roles, curves, dimensions),
        "wall_candidates": [item for item in line_roles if item["review_role"] == "likely_wall"][:MAX_LINES_PER_PAGE],
        "dimension_line_candidates": [item for item in line_roles if item["review_role"] == "likely_dimension_line"][:MAX_LINES_PER_PAGE],
        "witness_line_candidates": [item for item in line_roles if item["review_role"] == "likely_witness_line"][:MAX_LINES_PER_PAGE],
        "curve_candidates": curves[:MAX_CURVES_PER_PAGE],
        "dimension_text_candidates": dimensions,
        "unknown_numeric_annotations": unknown_numeric_annotations,
        "major_dimension_candidates": [
            item for item in dimensions if item.get("dimension_category") == "major_boundary"
        ],
        "dimension_span_candidates": match_page.get("dimension_span_candidates", [])[:MAX_DIMENSIONS_PER_PAGE],
        "dimension_summary": match_page.get("summary", {}),
        "rejected_or_context_candidates": [item for item in line_roles if item["review_role"] not in {"likely_wall", "likely_dimension_line", "likely_witness_line"}][:120],
    }


def add_crop_evidence(crops, lines, curves, dimensions):
    for crop in crops:
        bbox = crop.get("bbox_px")
        if not valid_bbox(bbox):
            continue
        vectors = [item.get("candidate_id") for item in lines + curves if item_intersects_bbox(item, bbox)]
        dimension_lines = [item.get("candidate_id") for item in lines if item.get("review_role") == "likely_dimension_line" and item_intersects_bbox(item, bbox)]
        witnesses = [item.get("candidate_id") for item in lines if item.get("review_role") == "likely_witness_line" and item_intersects_bbox(item, bbox)]
        dimension_ids = [item.get("candidate_id") for item in dimensions if bbox_intersects(item.get("bbox_px"), bbox)]
        crop["raw_vector_ids"] = unique_ids(crop.get("raw_vector_ids", []) + vectors, MAX_RAW_VECTORS_PER_CROP)
        crop["dimension_line_candidate_ids"] = unique_ids(crop.get("dimension_line_candidate_ids", []) + dimension_lines, 12)
        crop["witness_line_candidate_ids"] = unique_ids(crop.get("witness_line_candidate_ids", []) + witnesses, 12)
        crop["dimension_candidate_ids"] = unique_ids(crop.get("dimension_candidate_ids", []) + dimension_ids, 6)


def item_intersects_bbox(item, bbox):
    if item.get("geometry_type") == "curve_polyline":
        return bbox_intersects(item.get("bbox_px"), bbox)
    return bbox_intersects(line_bbox(item.get("line_start_px") or item.get("start_px"), item.get("line_end_px") or item.get("end_px")), bbox)


def unique_ids(items, limit):
    return list(dict.fromkeys(item for item in items if item))[:limit]


def review_dimensions(page, match_page):
    categories = dimension_categories(match_page)
    dimensions = []
    unknown = []
    for item in page.get("dimension_candidates", [])[:MAX_DIMENSIONS_PER_PAGE]:
        review_item = dict(item)
        if review_item.get("annotation_kind") == "detail_or_sheet_reference" or review_item.get("dimension_eligibility") == "ineligible":
            continue
        if review_item.get("annotation_kind") == "unknown_numeric" or review_item.get("dimension_eligibility") == "vision_review":
            unknown.append(review_item)
            continue
        category = categories.get(review_item.get("candidate_id")) or review_dimension_category(review_item)
        review_item["dimension_category"] = category
        review_item["dimension_priority"] = review_dimension_priority(review_item)
        dimensions.append(review_item)
    return sorted(dimensions, key=lambda item: item["dimension_priority"], reverse=True), unknown


def dimension_categories(match_page):
    categories = {}
    summary = match_page.get("summary", {})
    for key in ["major_boundary_dimensions", "local_fixture_dimensions", "unassigned_dimensions"]:
        for item in summary.get(key, []):
            candidate_id = item.get("candidate_id")
            if candidate_id and item.get("dimension_category"):
                categories[candidate_id] = item["dimension_category"]
    return categories


def review_dimension_category(item):
    value = item.get("value_mm") or 0
    context = " ".join(
        str(item.get(key, "")) for key in ["text_seen", "context", "nearby_text"]
    ).lower()
    if any(word in context for word in ["overall", "c.o.s", "cos", "boundary", "lease", "setout", "set out"]):
        return "major_boundary"
    if value <= 1500:
        return "local_fixture"
    return "setout_or_opening"


def review_dimension_priority(item):
    value = item.get("value_mm") or 0
    category = item.get("dimension_category")
    if category == "major_boundary":
        return 400000 + value
    if category == "setout_or_opening":
        return 200000 + value
    return value


def classified_line_candidates(page, match_page):
    linked_matches = match_page.get("dimension_wall_matches", [])
    matched_dimension_line_ids = {
        item.get("dimension_line_candidate_id")
        for item in linked_matches
        if item.get("dimension_line_candidate_id")
    }
    matched_wall_source_ids = {
        item.get("source_candidate_id")
        for item in match_page.get("wall_segments", [])
        if item.get("source_candidate_id")
    }
    dimensions = page.get("dimension_candidates", [])
    lines = []
    for line in page.get("line_candidates", []):
        item = dict(line)
        role, reasons = review_role_for_line(item, dimensions, matched_dimension_line_ids, matched_wall_source_ids)
        item["review_role"] = role
        item["review_reasons"] = reasons
        lines.append(item)
    return sorted(lines, key=review_sort_key)


def review_role_for_line(line, dimensions, matched_dimension_line_ids, matched_wall_source_ids):
    line_id = line.get("candidate_id")
    length = line.get("length_px", 0) or 0
    width = line.get("stroke_width", 0) or 0
    reasons = []
    if line_id in matched_dimension_line_ids or near_dimension_text(line, dimensions):
        return "likely_dimension_line", ["near or used by dimension text"]
    if length <= 180 and width <= 0.35:
        return "likely_witness_line", ["short thin perpendicular/reference candidate"]
    if line_id in matched_wall_source_ids and width >= 0.2:
        return "likely_wall", ["selected by vector wall shortlist", "normal/heavy stroke"]
    if line.get("confidence_score", 0) >= 70 and width >= 0.35:
        return "likely_wall", ["high vector score", "heavy stroke"]
    if width <= 0.12 and length >= 100:
        return "border_or_dimension_context", ["thin long line"]
    if length < 30:
        return "noise", ["very short line"]
    return "symbol_or_fixture", ["not enough evidence for wall"]


def classified_curve_candidates(page):
    curves = []
    for curve in page.get("curve_candidates", []):
        item = dict(curve)
        if item.get("confidence_score", 0) >= 55 and item.get("point_count", 0) >= 4:
            item["review_role"] = "curve_wall_candidate"
            item["review_reasons"] = ["large multi-point curve"]
        else:
            item["review_role"] = "curve_context_candidate"
            item["review_reasons"] = ["curve needs vision classification"]
        curves.append(item)
    return sorted(curves, key=lambda item: item.get("confidence_score", 0), reverse=True)


def near_dimension_text(line, dimensions):
    start = line.get("start_px")
    end = line.get("end_px")
    if not valid_point(start) or not valid_point(end):
        return False
    for text in dimensions:
        center = bbox_center(text.get("bbox_px"))
        if center and point_line_distance(center, start, end) <= 55:
            return True
    return False


def crop_regions(page, lines, curves, dimensions):
    boxes = []
    for item in lines[:120]:
        boxes.append(line_bbox(item.get("start_px"), item.get("end_px")))
    for item in curves[:40]:
        boxes.append(item.get("bbox_px"))
    for item in dimensions[:80]:
        boxes.append(item.get("bbox_px"))
    image = page.get("coordinate_systems", {}).get("image_px", {})
    width = image.get("image_width") or 0
    height = image.get("image_height") or 0
    if not boxes or not width or not height:
        return []
    viewport = main_viewport(page)
    main = viewport or padded_bbox(union_bbox([box for box in boxes if valid_bbox(box)]), width, height, 80)
    label = "main_plan_viewport" if viewport else "main_plan_candidates"
    crops = [{"crop_id": f"P{page.get('page')}-CROP-001", "label": label, "bbox_px": main}]
    dimension_zones = dimension_zone_crops(page.get("page"), dimensions, width, height)
    crops.extend(dimension_zones)
    if len(crops) < 5:
        crops.extend(dimension_band_crops(page.get("page"), lines, main, width, height, len(crops)))
    return crops


def dimension_zone_crops(page_number, dimensions, width, height):
    major = [item for item in dimensions if item.get("dimension_category") == "major_boundary" and valid_bbox(item.get("bbox_px"))]
    zones = []
    for item in major:
        zone = padded_bbox(item["bbox_px"], width, height, 220)
        zones = merge_dimension_zone(zones, zone, item)
    crops = []
    ordered = sorted(zones, key=lambda item: (-item["priority"], item["bbox_px"][1]))
    for index, zone in enumerate(ordered[:MAX_ADAPTIVE_DIMENSION_ZONES_PER_PAGE], start=2):
        crops.append(
            {
                "crop_id": f"P{page_number}-CROP-{index:03d}",
                "label": "major_dimension_zone",
                "bbox_px": zone["bbox_px"],
                "coordinate_system": "image_px",
                "candidate_ids": zone["candidate_ids"],
                "dimension_values_mm": zone["dimension_values_mm"],
                "dimension_categories": ["major_boundary"],
                "adaptive": index > INITIAL_DIMENSION_ZONES_PER_PAGE + 1,
            }
        )
    return crops


def merge_dimension_zone(zones, bbox, item):
    for zone in zones:
        if boxes_overlap_or_touch(zone["bbox_px"], bbox) and len(zone["candidate_ids"]) < 3:
            zone["bbox_px"] = union_bbox([zone["bbox_px"], bbox])
            zone["candidate_ids"].append(item.get("candidate_id", ""))
            zone["dimension_values_mm"].append(item.get("value_mm"))
            zone["priority"] = max(zone["priority"], item.get("dimension_priority", 0))
            return zones
    zones.append(
        {
            "bbox_px": bbox,
            "candidate_ids": [item.get("candidate_id", "")],
            "dimension_values_mm": [item.get("value_mm")],
            "priority": item.get("dimension_priority", 0),
        }
    )
    return zones


def boxes_overlap_or_touch(first, second):
    return not (
        first[2] < second[0]
        or second[2] < first[0]
        or first[3] < second[1]
        or second[3] < first[1]
    )


def bbox_intersects(first, second):
    if not valid_bbox(first) or not valid_bbox(second):
        return False
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def dimension_band_crops(page_number, lines, viewport, width, height, crop_count):
    bands = []
    for line in lines:
        if not likely_dimension_band(line, viewport):
            continue
        bbox = padded_bbox(line_bbox(line.get("start_px"), line.get("end_px")), width, height, 150)
        bands = merge_dimension_band(bands, bbox, line, line_orientation(line))
    crops = []
    for index, band in enumerate(sorted(bands, key=lambda item: item["length_px"], reverse=True)[: 5 - crop_count], start=crop_count + 1):
        crops.append(
            {
                "crop_id": f"P{page_number}-CROP-{index:03d}",
                "label": "dimension_band_candidate",
                "bbox_px": band["bbox_px"],
                "coordinate_system": "image_px",
                "dimension_line_candidate_ids": band["candidate_ids"],
                "dimension_values_mm": [],
                "evidence_source": "vector_dimension_band",
            }
        )
    return crops


def likely_dimension_band(line, viewport):
    start = line.get("start_px")
    end = line.get("end_px")
    if not valid_point(start) or not valid_point(end) or not valid_bbox(viewport):
        return False
    if (line.get("stroke_width") or 0) > 0.3 or (line.get("length_px") or 0) < 240:
        return False
    left, top, right, bottom = viewport
    horizontal = abs(end[0] - start[0]) >= abs(end[1] - start[1])
    if horizontal:
        overlap = interval_overlap_ratio(sorted([start[0], end[0]]), [left, right])
        return overlap >= 0.45 and top - 260 <= start[1] <= bottom + 260
    overlap = interval_overlap_ratio(sorted([start[1], end[1]]), [top, bottom])
    return overlap >= 0.45 and left - 260 <= start[0] <= right + 260


def interval_overlap_ratio(first, second):
    overlap = max(0, min(first[1], second[1]) - max(first[0], second[0]))
    length = max(1, first[1] - first[0])
    return overlap / length


def merge_dimension_band(bands, bbox, line, orientation):
    for band in bands:
        if band["orientation"] == orientation and boxes_overlap_or_touch(band["bbox_px"], bbox):
            band["bbox_px"] = union_bbox([band["bbox_px"], bbox])
            band["candidate_ids"].append(line.get("candidate_id", ""))
            band["length_px"] = max(band["length_px"], line.get("length_px", 0))
            return bands
    bands.append(
        {
            "bbox_px": bbox,
            "candidate_ids": [line.get("candidate_id", "")],
            "length_px": line.get("length_px", 0),
            "orientation": orientation,
        }
    )
    return bands


def line_orientation(line):
    start = line.get("start_px")
    end = line.get("end_px")
    return "horizontal" if abs(end[0] - start[0]) >= abs(end[1] - start[1]) else "vertical"


def create_candidate_overlays(pages, screenshots_dir, overlays_dir, source_pdf=None, crop_dpi=CROP_RENDER_DPI):
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
        source = screenshots_dir / image_name
        if not image_name or not source.exists():
            overlays.append({"page": page.get("page"), "status": "missing_image", "image": image_name})
            continue
        base_image = Image.open(source).convert("RGB")
        for kind in ["walls", "dimensions", "fixtures_or_joinery", "all_candidates"]:
            image = base_image.copy()
            draw_candidates(ImageDraw.Draw(image), page, kind)
            output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_{kind}_overlay.png"
            image.save(output)
            overlays.append({"page": page.get("page"), "status": "created", "kind": kind, "path": str(output)})
        overlays.extend(create_crop_images(base_image, page, overlays_dir, source_pdf, crop_dpi))
    return overlays


def draw_candidates(draw, page, kind="all_candidates"):
    colors = {
        "wall_candidates": "lime",
        "dimension_line_candidates": "deepskyblue",
        "witness_line_candidates": "orange",
        "curve_candidates": "magenta",
    }
    draw_bbox(draw, main_viewport(page), "orange", 5)
    label_at(draw, bbox_corner(main_viewport(page)), "main_plan_viewport", "orange")
    if kind in {"walls", "all_candidates"}:
        draw_candidate_group(draw, page, "wall_candidates", colors["wall_candidates"], 90)
    if kind == "all_candidates":
        draw_candidate_group(draw, page, "wall_candidates", colors["wall_candidates"], 90)
        draw_candidate_group(draw, page, "curve_candidates", colors["curve_candidates"], 45)
    if kind in {"dimensions", "all_candidates"}:
        draw_candidate_group(draw, page, "dimension_line_candidates", colors["dimension_line_candidates"], 90)
        draw_candidate_group(draw, page, "witness_line_candidates", colors["witness_line_candidates"], 90)
        draw_dimension_texts(draw, page)
    if kind in {"fixtures_or_joinery", "all_candidates"}:
        for item in page.get("rejected_or_context_candidates", [])[:120]:
            color = "gray" if kind == "all_candidates" else "dodgerblue"
            label = short_id(item.get("candidate_id"))
            draw_line(draw, item.get("start_px"), item.get("end_px"), color, 2)
            label_at(draw, item.get("start_px"), label, color)
    if kind == "all_candidates":
        for crop in page.get("crops", []):
            draw_bbox(draw, crop.get("bbox_px"), "white", 4)
            label_at(draw, bbox_corner(crop.get("bbox_px")), crop.get("crop_id", ""), "white")


def draw_candidate_group(draw, page, key, color, limit):
    for item in page.get(key, [])[:limit]:
        label = short_id(item.get("candidate_id") or item.get("wall_id"))
        if item.get("geometry_type") == "curve_polyline":
            draw_polyline(draw, item.get("points_px"), color, 3)
            label_at(draw, item.get("points_px", [[0, 0]])[0], label, color)
        else:
            draw_line(draw, item.get("start_px"), item.get("end_px"), color, 3)
            label_at(draw, item.get("start_px"), label, color)


def draw_dimension_texts(draw, page):
    for item in page.get("dimension_text_candidates", [])[:80]:
        color = "yellow" if item.get("dimension_category") != "local_fixture" else "gold"
        draw_bbox(draw, item.get("bbox_px"), color, 2)
        label_at(draw, bbox_corner(item.get("bbox_px")), f"{short_id(item.get('candidate_id'))}:{item.get('value_mm', '')}", color)


def create_crop_images(image, page, overlays_dir, source_pdf=None, crop_dpi=CROP_RENDER_DPI):
    source_image, source_metadata = render_source_page(source_pdf, page.get("page"), overlays_dir, crop_dpi)
    source_image = source_image or image
    source_width, source_height = source_image.size
    image_width, image_height = image.size
    created = []
    for crop in page.get("crops", []):
        bbox = [int(round(value)) for value in crop.get("bbox_px", [])]
        if not valid_bbox(bbox):
            continue
        render_bbox = map_bbox(bbox, image_width, image_height, source_width, source_height)
        cropped = source_image.crop(tuple(render_bbox))
        if source_metadata["source"] != "source_pdf" and CROP_IMAGE_SCALE > 1:
            from PIL import Image

            cropped = cropped.resize(
                (cropped.width * CROP_IMAGE_SCALE, cropped.height * CROP_IMAGE_SCALE),
                resample=Image.Resampling.LANCZOS,
            )
        output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_{crop['crop_id'].lower()}_{safe_name(crop['label'])}.png"
        cropped.save(output)
        created.append(
            {
                "page": page.get("page"),
                "status": "created",
                "kind": "crop",
                "crop_id": crop["crop_id"],
                "path": str(output),
                "source_bbox_px": bbox,
                "render_bbox_px": render_bbox,
                "coordinate_system": "image_px",
                "render_scale": (
                    round(source_width / image_width, 4)
                    if source_metadata["source"] == "source_pdf" and image_width
                    else CROP_IMAGE_SCALE
                ),
                "render_dpi": source_metadata["render_dpi"],
                "render_source": source_metadata["source"],
                "source_pdf_page": page.get("page") if source_metadata["source"] == "source_pdf" else None,
                "image_width": cropped.width,
                "image_height": cropped.height,
                "candidate_ids": crop.get("candidate_ids", []),
                "dimension_values_mm": crop.get("dimension_values_mm", []),
            }
        )
    return created


def render_source_page(source_pdf, page_number, overlays_dir, dpi):
    """Render once per PDF page so crops contain real source detail, not upscaled pixels."""
    if not source_pdf or not isinstance(page_number, int) or not Path(source_pdf).exists():
        return None, {"source": "packet_image_fallback", "render_dpi": None}
    try:
        from PIL import Image
    except ImportError:
        return None, {"source": "packet_image_fallback", "render_dpi": None}

    cache_dir = Path(overlays_dir) / ".source_crop_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"page_{page_number:03d}_{dpi}dpi.png"
    if not target.exists():
        try:
            subprocess.run(
                [
                    "pdftoppm", "-png", "-singlefile", "-f", str(page_number), "-l", str(page_number),
                    "-r", str(dpi), str(source_pdf), str(target.with_suffix("")),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None, {"source": "packet_image_fallback", "render_dpi": None}
    return Image.open(target).convert("RGB"), {"source": "source_pdf", "render_dpi": dpi}


def map_bbox(bbox, image_width, image_height, source_width, source_height):
    if not image_width or not image_height:
        return bbox
    x_scale = source_width / image_width
    y_scale = source_height / image_height
    return [
        max(0, min(source_width, round(bbox[0] * x_scale))),
        max(0, min(source_height, round(bbox[1] * y_scale))),
        max(0, min(source_width, round(bbox[2] * x_scale))),
        max(0, min(source_height, round(bbox[3] * y_scale))),
    ]


def vision_prompt():
    return (
        "This is one combined review. Use labelled raw PDF-vector IDs as evidence, create physical wall polylines, then verify dimensions against your created wall IDs. "
        "Return strict JSON. Create IDs as P{page}-VWALL-{number}; a vector-anchored wall must cite raw vector IDs, while an image-proposed wall remains provisional. "
        "Classify only physical walls as existing wall, new solid wall, or new partition. Never make a lease line, fixture/joinery, counter, dimension/reference, annotation, or noise into a physical wall. "
        "If `dimension_summary.machine_extraction_gaps` says major boundary dimensions were missed, inspect the screenshot and add visible overall/setout dimensions to `layered_geometry.dimension_candidates` with source `screenshot_visible`. "
        "When a `dimension_band_candidate` crop has no value, read any visible dimension text from that enlarged crop before assigning it to a wall. "
        "Link dimensions to walls only when the dimension line, arrowheads/witness lines, and measured span visibly support the wall candidate. "
        "Prioritise major dimensions over repeated small fixture/setout dimensions, but keep small dimensions when they clearly describe local geometry. "
        "If unsure, reject or leave unassigned."
    )


def candidate_ids(lines, curves, dimensions):
    ids = []
    for item in lines + curves + dimensions:
        item_id = item.get("candidate_id") or item.get("wall_id")
        if item_id:
            ids.append(item_id)
    return ids


def main_viewport(page):
    viewport = page.get("plan_viewport", {}).get("bbox_px") if isinstance(page.get("plan_viewport"), dict) else None
    if valid_bbox(viewport):
        return viewport
    systems = page.get("coordinate_systems", {})
    plan = systems.get("plan_px", {}) if isinstance(systems, dict) else {}
    viewport = plan.get("plan_viewport_bbox_px")
    return viewport if valid_bbox(viewport) else None


def review_sort_key(item):
    priority = {
        "likely_wall": 0,
        "likely_dimension_line": 1,
        "likely_witness_line": 2,
        "symbol_or_fixture": 3,
        "border_or_dimension_context": 4,
        "noise": 5,
    }
    return (priority.get(item.get("review_role"), 9), -item.get("confidence_score", 0), -item.get("length_px", 0))


def line_bbox(start, end):
    if not valid_point(start) or not valid_point(end):
        return None
    left, top = min(start[0], end[0]), min(start[1], end[1])
    right, bottom = max(start[0], end[0]), max(start[1], end[1])
    # Axis-aligned PDF lines have a zero-area mathematical bbox. Give them a minimal
    # drawable extent so crop/intersection code can treat them as visible evidence.
    if left == right:
        right += 1
    if top == bottom:
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


def bbox_corner(bbox):
    if not valid_bbox(bbox):
        return [0, 0]
    return [bbox[0], bbox[1]]


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)


def valid_bbox(bbox):
    return isinstance(bbox, list) and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]


def point_line_distance(point, start, end):
    import math

    length = math.dist(start, end)
    if length == 0:
        return math.dist(point, start)
    return abs((end[0] - start[0]) * (start[1] - point[1]) - (start[0] - point[0]) * (end[1] - start[1])) / length


def draw_line(draw, start, end, color, width):
    if valid_point(start) and valid_point(end):
        draw.line([tuple(start), tuple(end)], fill=color, width=width)


def draw_polyline(draw, points, color, width):
    if isinstance(points, list) and len(points) >= 2 and all(valid_point(point) for point in points):
        draw.line([tuple(point) for point in points], fill=color, width=width)


def draw_bbox(draw, bbox, color, width):
    if valid_bbox(bbox):
        draw.rectangle(bbox, outline=color, width=width)


def label_at(draw, point, label, color):
    if valid_point(point) and label:
        draw.text((point[0] + 4, point[1] + 4), label, fill=color)


def short_id(value):
    value = str(value or "")
    return value.replace("P", "").replace("-VLINE-", "L").replace("-VCURVE-", "C").replace("-VDIMTXT-", "T")[-10:]


def safe_name(value):
    return "".join(char.lower() if char.isalnum() else "_" for char in str(value)).strip("_") or "crop"


def main():
    parser = argparse.ArgumentParser(description="Create a focused candidate review packet for manual vision classification.")
    parser.add_argument("vector_geometry")
    parser.add_argument("--dimension-wall-matches")
    parser.add_argument("--output")
    parser.add_argument("--screenshots-dir")
    parser.add_argument("--overlays-dir")
    args = parser.parse_args()

    output = create_candidate_review(
        args.vector_geometry,
        args.dimension_wall_matches,
        args.output,
        args.overlays_dir,
        args.screenshots_dir,
    )
    print(f"Candidate review created: {output}")


if __name__ == "__main__":
    main()
