#!/usr/bin/env python3

import argparse
import json
import math
from pathlib import Path

from ai.ai_packet import load_json
from ai.geometry_review import normalise_vision


# Confirmation tolerance, in screenshot pixels. Kept on the order of a wall thickness
# (a wall band is ~21px on a 1:40 sheet at 180dpi). At the previous 55px this was ~310mm
# at that scale, wide enough to confirm a wall against the dimension line annotating it.
MAX_SNAP_DISTANCE_PX = 30
MAX_ANGLE_DELTA_DEGREES = 8
MIN_LENGTH_RATIO = 0.70
MIN_SPAN_CONFIRMATION_SCORE = 70
EXPERIMENTAL_CORRIDOR_WIDTHS_PX = [30, 60, 120, 240]
MAX_EXPERIMENTAL_ALTERNATIVES = 5
MIN_EXPERIMENTAL_SNAP_SCORE = 45


def create_geometry_confirmation(vision_path, vector_geometry_path, output_path=None, overlays_dir=None, screenshots_dir=None, candidate_review_path=None, page_scales=None):
    vision_path = Path(vision_path)
    vector_geometry_path = Path(vector_geometry_path)
    vision = load_json(vision_path)
    vector_geometry = load_json(vector_geometry_path)
    candidate_review = load_json(Path(candidate_review_path)) if candidate_review_path else None
    vision = normalise_vision(vision, candidate_review)
    output_path = Path(output_path or vision_path.with_name("geometry_confirmation.json"))
    overlays_dir = Path(overlays_dir or vision_path.with_name("geometry_confirmation_overlays"))
    screenshots_dir = Path(screenshots_dir) if screenshots_dir else None

    vector_pages = {
        page.get("page"): page
        for page in vector_geometry.get("geometry_key_points", {}).get("pages", [])
    }
    pages = [
        confirm_page(page, vector_pages.get(page.get("page"), {}), candidate_review)
        for page in layered_pages(vision)
    ]
    scale_conversions = page_scale_conversions(pages, page_scales)
    cad_ready_geometry = cad_geometry(pages, scale_conversions)
    overlays = create_overlays(pages, screenshots_dir, overlays_dir)

    output = {
        "source_vision_response": str(vision_path),
        "source_vector_geometry": str(vector_geometry_path),
        "status": overall_status(pages),
        "pages": pages,
        "scale_conversions": scale_conversions,
        "cad_ready_geometry": cad_ready_geometry,
        "overlays": overlays,
        "summary": {
            "page_count": len(pages),
            "vision_wall_count": sum(len(page.get("wall_confirmations", [])) for page in pages),
            "vector_confirmed_wall_count": sum(1 for page in pages for wall in page.get("wall_confirmations", []) if wall.get("status") in {"vector_confirmed", "scale_calibrated", "cad_ready_candidate"}),
            "experimental_vector_snap_wall_count": sum(1 for page in pages for wall in page.get("wall_confirmations", []) if wall.get("status") == "experimental_vector_snap"),
            "cad_ready_candidate_count": len(cad_ready_geometry),
        },
        "note": "Geometry confirmation snaps vision-layered wall evidence to PDF vectors when possible. CAD-ready candidates still need project-level engineering review before final export.",
    }
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    return output_path


def layered_pages(vision):
    pages = vision.get("result", {}).get("layered_geometry", {}).get("pages", [])
    return pages if isinstance(pages, list) else []


def confirm_page(layered_page, vector_page, candidate_review=None):
    annotation_ids = annotation_candidate_ids(candidate_review, layered_page.get("page"))
    vector_lines = usable_vector_lines(vector_page, annotation_ids)
    vector_dimension_lines = usable_vector_dimension_lines(vector_page)
    vector_curves = usable_vector_curves(vector_page)
    wall_bands = usable_wall_bands(vector_page)
    wall_confirmations = []
    for wall in layered_walls(layered_page):
        wall_confirmations.append(confirm_wall(layered_page, wall, vector_lines, vector_curves, wall_bands))

    dimension_links = []
    by_wall_id = {wall["wall_id"]: wall for wall in wall_confirmations}
    for link in layered_page.get("dimension_wall_links", []):
        dimension_links.append(confirm_dimension_link(layered_page, link, by_wall_id, vector_dimension_lines))

    return {
        "page": layered_page.get("page"),
        "image": layered_page.get("image", ""),
        "page_role": layered_page.get("page_role", ""),
        "plan_viewport_bbox_px": layered_page.get("plan_viewport_bbox_px"),
        "wall_confirmations": wall_confirmations,
        "dimension_link_confirmations": dimension_links,
        "vector_curve_candidates": vector_curves[:40],
        "status": page_status(wall_confirmations, dimension_links),
    }


def layered_walls(page):
    walls = []
    for key in ["outer_boundary_walls", "internal_partitions"]:
        for wall in page.get(key, []):
            item = dict(wall)
            item["wall_layer"] = key
            walls.append(item)
    return walls


def annotation_candidate_ids(candidate_review, page_number):
    """Ids candidate_review has already classified as dimension or witness lines.

    That classification exists but was previously unused here, so a line known to be a
    dimension line was still offered as a wall snap target.
    """
    for page in (candidate_review or {}).get("pages", []):
        if page.get("page") != page_number:
            continue
        ids = set()
        for key in ("dimension_line_candidates", "witness_line_candidates"):
            ids.update(item.get("candidate_id") for item in page.get(key, []) if item.get("candidate_id"))
        return ids
    return set()


def usable_vector_lines(page, annotation_ids=None):
    lines = []
    annotation_ids = annotation_ids or set()
    viewport = page.get("plan_viewport", {}).get("bbox_px")
    for line in confirmation_lines(page):
        start = line.get("start_px")
        end = line.get("end_px")
        if not valid_point(start) or not valid_point(end):
            continue
        if line.get("candidate_id") in annotation_ids:
            continue
        if line.get("candidate_role_hint") == "likely_noise":
            continue
        if line.get("inside_main_plan_viewport") is False:
            continue
        if line.get("stroke_width", 0) is not None and line.get("stroke_width", 0) <= 0.08:
            continue
        if thin_long_line(line):
            continue
        if valid_bbox(viewport) and not point_inside_bbox(midpoint(start, end), viewport):
            continue
        lines.append(line)
    return lines


def usable_vector_dimension_lines(page):
    lines = []
    viewport = page.get("plan_viewport", {}).get("bbox_px")
    for line in confirmation_lines(page):
        start = line.get("start_px")
        end = line.get("end_px")
        if not valid_point(start) or not valid_point(end):
            continue
        if line.get("candidate_role_hint") == "likely_noise":
            continue
        if thick_long_line(line):
            continue
        if valid_bbox(viewport) and not line_intersects_bbox(start, end, expand_bbox(viewport, 160)):
            continue
        lines.append(line)
    return lines


def thin_long_line(line):
    """Long hairline strokes are annotation (dimension lines, leaders), not wall fabric.

    The threshold covers observed dimension-line stroke widths, which sit at 0.24 on the
    Studio Hiyaku sets. At the previous 0.12 they passed as wall snap targets, so walls
    were confirmed against the dimension line that annotated them.
    """
    width = line.get("stroke_width", 0)
    length = line.get("length_px", 0) or 0
    return width is not None and width <= 0.30 and length >= 90


def thick_long_line(line):
    width = line.get("stroke_width", 0)
    length = line.get("length_px", 0) or 0
    return width is not None and width >= 0.45 and length >= 90


def usable_wall_bands(page):
    """Filled wall bands, as centreline segments that can be matched like lines."""
    bands = []
    viewport = page.get("plan_viewport", {}).get("bbox_px")
    for curve in confirmation_curves(page):
        band = curve.get("wall_band")
        if not band:
            continue
        if curve.get("inside_main_plan_viewport") is False:
            continue
        start, end = band.get("centreline_start_px"), band.get("centreline_end_px")
        if not valid_point(start) or not valid_point(end):
            continue
        if valid_bbox(viewport) and not point_inside_bbox(midpoint(start, end), viewport):
            continue
        bands.append(dict(curve, start_px=start, end_px=end, band=band))
    return bands


def usable_vector_curves(page):
    curves = []
    viewport = page.get("plan_viewport", {}).get("bbox_px")
    for curve in confirmation_curves(page):
        points = curve.get("points_px", [])
        if len(points) < 3:
            continue
        if curve.get("candidate_role_hint") == "likely_noise":
            continue
        if curve.get("inside_main_plan_viewport") is False:
            continue
        if valid_bbox(viewport) and not point_inside_bbox(bbox_center(curve.get("bbox_px")), viewport):
            continue
        curves.append(curve)
    return curves


def confirmation_lines(page):
    return page.get("confirmation_line_candidates") or page.get("line_candidates", [])


def confirmation_curves(page):
    return page.get("confirmation_curve_candidates") or page.get("curve_candidates", [])


def confirm_wall(page, wall, vector_lines, vector_curves, wall_bands=None):
    vision_start = wall.get("line_start_px")
    vision_end = wall.get("line_end_px")
    base = {
        "wall_id": wall.get("wall_id"),
        "page": page.get("page"),
        "classification": wall.get("classification", ""),
        "wall_layer": wall.get("wall_layer", ""),
        "vision_start_px": vision_start,
        "vision_end_px": vision_end,
        "confidence": wall.get("confidence", "low"),
        "source_candidate_ids": wall.get("source_candidate_ids", []),
        "status": "vision_estimated",
        "snap_reasons": [],
    }
    if not valid_point(vision_start) or not valid_point(vision_end):
        base["status"] = "review_only"
        base["snap_reasons"].append("vision wall has missing coordinates")
        return base

    if wall.get("source") == "image_proposed":
        return experimental_wall_snap(base, wall, vector_lines, vector_curves, wall_bands or [])

    # A filled wall band is stronger evidence than a stroked line, so it is tried first:
    # on sheets that draw walls as polygons, the only nearby stroked lines are annotation.
    source_ids = set(wall.get("source_candidate_ids", []))
    band_match = best_line_match(vision_start, vision_end, matching_sources(wall_bands or [], source_ids))
    if band_match:
        item = dict(base)
        item.update(
            {
                "status": "vector_confirmed",
                "geometry_type": "wall_band_centreline",
                "matched_vector_candidate_id": band_match["candidate_id"],
                "snapped_start_px": band_match["start_px"],
                "snapped_end_px": band_match["end_px"],
                "wall_thickness_px": (band_match.get("band") or {}).get("thickness_px"),
                "snap_score": band_match["score"],
                "snap_metrics": band_match["metrics"],
                "snap_reasons": band_match["reasons"] + ["matched the centreline of a filled wall band"],
            }
        )
        return item

    line_match = best_group_line_match(vision_start, vision_end, matching_sources(vector_lines, source_ids))
    if line_match:
        item = dict(base)
        item.update(
            {
                "status": "vector_confirmed",
                "matched_vector_candidate_id": line_match["candidate_id"],
                "component_vector_ids": line_match.get("component_vector_ids", [line_match["candidate_id"]]),
                "snapped_start_px": line_match["start_px"],
                "snapped_end_px": line_match["end_px"],
                "snap_score": line_match["score"],
                "snap_metrics": line_match["metrics"],
                "snap_reasons": line_match["reasons"],
            }
        )
        return item

    curve_match = best_curve_match(wall, matching_sources(vector_curves, source_ids))
    if curve_match:
        item = dict(base)
        item.update(
            {
                "status": "vector_confirmed",
                "geometry_type": "curve_polyline",
                "matched_vector_candidate_id": curve_match.get("candidate_id"),
                "snapped_points_px": curve_match.get("points_px", []),
                "snap_score": curve_match["score"],
                "snap_metrics": curve_match.get("metrics", {}),
                "snap_reasons": ["vision curve aligns with PDF vector curve; preserve it as a polyline"],
            }
        )
        return item

    base["snap_reasons"].append("no nearby parallel vector wall found")
    return base


def experimental_wall_snap(base, wall, vector_lines, vector_curves, wall_bands):
    """Choose the strongest nearby vector for a screenshot-proposed wall.

    This is deliberately a Vision Lab result: it records competing evidence rather
    than claiming that a local match is final CAD geometry.
    """
    start, end = base["vision_start_px"], base["vision_end_px"]
    alternatives = []
    final_width = None
    for width in EXPERIMENTAL_CORRIDOR_WIDTHS_PX:
        local_lines = [line for line in vector_lines if line_near_segment(line.get("start_px"), line.get("end_px"), start, end, width)]
        local_bands = [band for band in wall_bands if line_near_segment(band.get("start_px"), band.get("end_px"), start, end, width)]
        local_curves = [curve for curve in vector_curves if curve_near_segment(curve, start, end, width)]
        alternatives = ranked_experimental_matches(start, end, local_bands, local_lines, local_curves, width)
        if alternatives:
            final_width = width
            break

    base["experimental_search"] = {
        "corridor_widths_px": EXPERIMENTAL_CORRIDOR_WIDTHS_PX,
        "selected_corridor_width_px": final_width,
        "searched_vector_line_count": len(vector_lines),
        "searched_wall_band_count": len(wall_bands),
        "searched_curve_count": len(vector_curves),
    }
    base["alternatives"] = alternatives[:MAX_EXPERIMENTAL_ALTERNATIVES]
    if not alternatives:
        base["snap_reasons"].append("no eligible PDF vector found inside the maximum experimental corridor")
        return base

    selected = alternatives[0]
    base.update(
        {
            "status": "experimental_vector_snap",
            "geometry_type": selected["geometry_type"],
            "matched_vector_candidate_id": selected["candidate_id"],
            "component_vector_ids": selected.get("component_vector_ids", [selected["candidate_id"]]),
            "snapped_start_px": selected.get("start_px"),
            "snapped_end_px": selected.get("end_px"),
            "snapped_points_px": selected.get("points_px", []),
            "wall_thickness_px": selected.get("wall_thickness_px"),
            "snap_score": selected["score"],
            "snap_metrics": selected["metrics"],
            "snap_reasons": selected["reasons"] + ["experimental local vector snap; not final CAD geometry"],
        }
    )
    return base


def ranked_experimental_matches(start, end, wall_bands, vector_lines, vector_curves, corridor_width):
    matches = []
    for band in wall_bands:
        score = line_match_score(start, end, band.get("start_px"), band.get("end_px"), corridor_width)
        if score["score"] >= MIN_EXPERIMENTAL_SNAP_SCORE:
            matches.append(experimental_match(band, score, "wall_band_centreline", corridor_width, wall_thickness=(band.get("band") or {}).get("thickness_px")))
    for line in vector_lines:
        score = line_match_score(start, end, line.get("start_px"), line.get("end_px"), corridor_width)
        if score["score"] >= MIN_EXPERIMENTAL_SNAP_SCORE:
            matches.append(experimental_match(line, score, "line", corridor_width))
    merged = merged_vector_group(vector_lines, start, end)
    if merged:
        score = line_match_score(start, end, merged.get("start_px"), merged.get("end_px"), corridor_width)
        if score["score"] >= MIN_EXPERIMENTAL_SNAP_SCORE:
            matches.append(experimental_match(merged, score, "line", corridor_width))
    for curve in vector_curves:
        curve_match = experimental_curve_match(start, end, curve, corridor_width)
        if curve_match:
            matches.append(curve_match)
    matches.sort(key=lambda item: (-item["score"], item["candidate_id"]))
    for index, item in enumerate(matches):
        if index:
            item["rejection_reasons"] = ["lower experimental snap score than the selected candidate"]
    return matches[:MAX_EXPERIMENTAL_ALTERNATIVES]


def experimental_match(item, score, geometry_type, corridor_width, wall_thickness=None):
    return {
        "candidate_id": item.get("candidate_id"),
        "component_vector_ids": item.get("component_vector_ids", [item.get("candidate_id")]),
        "geometry_type": geometry_type,
        "start_px": item.get("start_px"),
        "end_px": item.get("end_px"),
        "wall_thickness_px": wall_thickness,
        "score": score["score"],
        "metrics": score,
        "reasons": score["reasons"] + [f"inside {corridor_width}px experimental search corridor"],
        "rejection_reasons": [],
        "corridor_width_px": corridor_width,
    }


def experimental_curve_match(start, end, curve, corridor_width):
    points = curve.get("points_px", [])
    if len(points) < 3 or not curve_near_segment(curve, start, end, corridor_width):
        return None
    distance = point_bbox_distance(midpoint(start, end), curve.get("bbox_px"))
    endpoint_error = endpoint_alignment_error(start, end, points[0], points[-1])
    score = max(0, round(100 - distance * 0.35 - endpoint_error * 0.25, 2))
    if score < MIN_EXPERIMENTAL_SNAP_SCORE:
        return None
    return {
        "candidate_id": curve.get("candidate_id"),
        "component_vector_ids": [curve.get("candidate_id")],
        "geometry_type": "curve_polyline",
        "points_px": points,
        "score": score,
        "metrics": {"midpoint_distance_px": round(distance, 2), "endpoint_distance_px": round(endpoint_error, 2)},
        "reasons": ["vision wall aligns with a nearby PDF vector curve", f"inside {corridor_width}px experimental search corridor"],
        "rejection_reasons": [],
        "corridor_width_px": corridor_width,
    }


def confirm_dimension_link(page, link, walls_by_id, vector_lines):
    wall = walls_by_id.get(link.get("target_wall_id"))
    item = {
        "measurement_id": link.get("measurement_id"),
        "dimension_id": link.get("dimension_id"),
        "page": page.get("page"),
        "value_mm": link.get("value_mm"),
        "target_wall_id": link.get("target_wall_id"),
        "dimension_line_start_px": link.get("dimension_line_start_px"),
        "dimension_line_end_px": link.get("dimension_line_end_px"),
        "target_wall_start_px": link.get("target_wall_start_px"),
        "target_wall_end_px": link.get("target_wall_end_px"),
        "confidence": link.get("confidence", "low"),
        "site_confirm_required": site_confirm_required(link),
        "status": "vision_estimated",
        "reasons": [],
    }
    dim_match = best_line_match(link.get("dimension_line_start_px"), link.get("dimension_line_end_px"), vector_lines)
    item["dimension_evidence"] = {
        "dimension_vector_found": bool(dim_match),
        "witness_line_count": len(link.get("witness_lines_px", [])),
        "arrow_or_tick_evidence": bool(link.get("arrowhead_start_px") or link.get("arrowhead_end_px") or link.get("witness_lines_px")),
        "measured_span_present": bool(link.get("measured_span_start_px") and link.get("measured_span_end_px")),
    }
    if dim_match:
        item["matched_dimension_vector_id"] = dim_match["candidate_id"]
        item["dimension_snap_score"] = dim_match["score"]
        item["dimension_snap_metrics"] = dim_match["metrics"]
    if not wall:
        item["status"] = "review_only"
        item["reasons"].append("target wall is not present in layered wall confirmations")
        return item
    item["wall_confirmation_status"] = wall.get("status")
    if wall.get("status") not in {"vector_confirmed", "scale_calibrated", "cad_ready_candidate", "experimental_vector_snap"}:
        item["status"] = "review_only"
        item["reasons"].append("target wall is not vector-confirmed")
        return item
    if not isinstance(item["value_mm"], (int, float)):
        item["status"] = "review_only"
        item["reasons"].append("dimension value is missing")
        return item
    span_score = span_confirmation_score(
        link.get("measured_span_start_px") or link.get("target_wall_start_px"),
        link.get("measured_span_end_px") or link.get("target_wall_end_px"),
        wall.get("snapped_start_px"),
        wall.get("snapped_end_px"),
    )
    item["span_confirmation"] = span_score
    if not dim_match:
        item["status"] = "review_only"
        item["reasons"].append("dimension line is not vector-confirmed")
        return item
    if span_score["score"] < MIN_SPAN_CONFIRMATION_SCORE:
        item["status"] = "review_only"
        item["reasons"].append("measured span does not confirm the selected vector wall")
        return item
    item["combined_snap_score"] = round((dim_match["score"] + span_score["score"] + wall.get("snap_score", 0)) / 3, 2)
    if wall.get("status") == "experimental_vector_snap":
        item["status"] = "review_only" if item["site_confirm_required"] else "experimental_vector_snap"
        item["reasons"].append("dimension and wall agree with experimental vector evidence")
        if item["site_confirm_required"]:
            item["reasons"].append("dimension requires site confirmation")
        return item
    if item["site_confirm_required"]:
        item["status"] = "review_only"
        item["reasons"].append("dimension requires site confirmation")
        return item
    if link.get("confidence") == "high":
        item["status"] = "cad_ready_candidate"
        item["reasons"].append("high-confidence direct dimension, vector span, and vector wall agree")
    else:
        item["status"] = "scale_calibrated"
        item["reasons"].append("dimension can calibrate scale but needs review before CAD use")
    return item


def best_line_match(start, end, vector_lines):
    if not valid_point(start) or not valid_point(end):
        return None
    best = None
    for line in vector_lines:
        score = line_match_score(start, end, line.get("start_px"), line.get("end_px"))
        if score["score"] < 70:
            continue
        candidate = dict(line, score=score["score"], reasons=score["reasons"], metrics=score)
        if not best or candidate["score"] > best["score"]:
            best = candidate
    return best


def matching_sources(candidates, source_ids):
    selected = [item for item in candidates if item.get("candidate_id") in source_ids]
    return selected or candidates


def best_group_line_match(start, end, vector_lines):
    candidates = list(vector_lines)
    merged = merged_vector_group(vector_lines)
    if merged:
        candidates.append(merged)
    return best_line_match(start, end, candidates)


def merged_vector_group(lines, vision_start=None, vision_end=None):
    if len(lines) < 2:
        return None
    if vision_start and vision_end:
        angle = line_angle(vision_start, vision_end)
        lines = [line for line in lines if valid_point(line.get("start_px")) and valid_point(line.get("end_px")) and angle_difference(angle, line_angle(line["start_px"], line["end_px"])) <= MAX_ANGLE_DELTA_DEGREES]
    points = [point for line in lines for point in [line.get("start_px"), line.get("end_px")] if valid_point(point)]
    if len(points) < 2:
        return None
    start, end = max(
        ((first, second) for index, first in enumerate(points) for second in points[index + 1:]),
        key=lambda pair: point_distance(*pair),
    )
    return {
        "candidate_id": "+".join(item.get("candidate_id", "") for item in lines),
        "component_vector_ids": [item.get("candidate_id") for item in lines],
        "start_px": start,
        "end_px": end,
    }


def best_curve_match(wall, curves):
    start, end = wall.get("line_start_px"), wall.get("line_end_px")
    if not valid_point(start) or not valid_point(end):
        return None
    wall_mid = midpoint(start, end)
    best = None
    for curve in curves:
        distance = point_bbox_distance(wall_mid, curve.get("bbox_px"))
        if distance > MAX_SNAP_DISTANCE_PX:
            continue
        points = curve.get("points_px", [])
        endpoint_error = endpoint_alignment_error(start, end, points[0], points[-1]) if len(points) >= 2 else MAX_SNAP_DISTANCE_PX
        score = max(0, 100 - distance - endpoint_error * 0.5)
        if not best or score > best["score"]:
            best = dict(curve, score=score)
    return best


def span_confirmation_score(span_start, span_end, wall_start, wall_end):
    if not all(valid_point(point) for point in [span_start, span_end, wall_start, wall_end]):
        return {"score": 0, "reasons": ["missing measured span or snapped wall"]}
    score = line_match_score(span_start, span_end, wall_start, wall_end)
    coverage = span_coverage(span_start, span_end, wall_start, wall_end)
    if coverage >= 0.85:
        score["score"] = min(100, score["score"] + 15)
        score["reasons"].append("measured span covers the wall")
    else:
        score["score"] = max(0, score["score"] - 30)
        score["reasons"].append("measured span only partially covers the wall")
    score["coverage"] = round(coverage, 3)
    return score


def span_coverage(span_start, span_end, wall_start, wall_end):
    wall_length = line_length(wall_start, wall_end)
    if not wall_length:
        return 0
    values = sorted([
        projection_ratio(span_start, wall_start, wall_end),
        projection_ratio(span_end, wall_start, wall_end),
    ])
    return min(1, max(0, min(1, values[1]) - max(0, values[0])))


def line_match_score(start, end, candidate_start, candidate_end, snap_distance=MAX_SNAP_DISTANCE_PX):
    if not all(valid_point(point) for point in [start, end, candidate_start, candidate_end]):
        return {"score": 0, "reasons": ["missing point"]}
    angle_delta = angle_difference(line_angle(start, end), line_angle(candidate_start, candidate_end))
    if angle_delta > MAX_ANGLE_DELTA_DEGREES:
        return {"score": 0, "reasons": ["angle mismatch"]}
    length_ratio = min(line_length(start, end), line_length(candidate_start, candidate_end)) / max(line_length(start, end), line_length(candidate_start, candidate_end))
    midpoint_distance = point_distance(midpoint(start, end), midpoint(candidate_start, candidate_end))
    endpoint_distance = min(
        (point_distance(start, candidate_start) + point_distance(end, candidate_end)) / 2,
        (point_distance(start, candidate_end) + point_distance(end, candidate_start)) / 2,
    )
    score = 100
    reasons = ["roughly parallel to vision line"]
    if length_ratio >= MIN_LENGTH_RATIO:
        reasons.append("similar line length")
    else:
        score -= 28
        reasons.append("line length differs")
    if midpoint_distance <= snap_distance:
        reasons.append("midpoints are close")
    else:
        score -= min(35, midpoint_distance * 0.4)
        reasons.append("midpoints are not close")
    if endpoint_distance <= snap_distance:
        reasons.append("endpoints are close")
    else:
        score -= min(35, endpoint_distance * 0.35)
        reasons.append("endpoint distance needs review")
    return {
        "score": round(max(0, min(100, score)), 2),
        "reasons": reasons,
        "angle_delta_degrees": round(angle_delta, 2),
        "length_ratio": round(length_ratio, 3),
        "midpoint_distance_px": round(midpoint_distance, 2),
        "endpoint_distance_px": round(endpoint_distance, 2),
    }


def page_scale_conversions(pages, page_scales=None):
    """Millimetres per pixel for each page, exact where possible.

    A scale derived from render dpi and the drawing scale is exact. Back-computing it
    from a dimension link is not: the drawn dimension line overshoots its stated value,
    and the link may target a wall centreline while the value describes its faces. Both
    push the estimate low by roughly one percent, which is enough to matter once the
    geometry is used for setting out. Derived conversions are still emitted afterwards,
    so the comparison stays visible.
    """
    conversions = []
    page_scales = page_scales or {}
    for page in pages:
        exact = page_scales.get(page.get("page"))
        if exact:
            statuses = {link.get("status") for link in page.get("dimension_link_confirmations", [])}
            conversions.append(
                {
                    "page": page.get("page"),
                    "source_measurement_id": "",
                    "source": "drawing_scale_and_render_dpi",
                    "mm_per_px": round(exact, 6),
                    "status": "cad_ready_candidate" if "cad_ready_candidate" in statuses else "scale_calibrated",
                    "site_confirm_required": False,
                }
            )
        for link in page.get("dimension_link_confirmations", []):
            if link.get("status") not in {"scale_calibrated", "cad_ready_candidate"}:
                continue
            px_length = line_length(link.get("measured_span_start_px"), link.get("measured_span_end_px"))
            if not px_length:
                px_length = line_length(link.get("dimension_line_start_px"), link.get("dimension_line_end_px"))
            value = link.get("value_mm")
            if not px_length or not isinstance(value, (int, float)):
                continue
            conversions.append(
                {
                    "page": page.get("page"),
                    "source_measurement_id": link.get("measurement_id"),
                    "source_value_mm": value,
                    "source_pixel_length": px_length,
                    "source": "measured_from_dimension_link",
                    "mm_per_px": round(value / px_length, 6),
                    "status": "scale_calibrated" if (link.get("status") == "scale_calibrated" or exact) else "cad_ready_candidate",
                    "site_confirm_required": link.get("site_confirm_required", False),
                }
            )
    return conversions


def cad_geometry(pages, conversions):
    by_page = {}
    for conversion in conversions:
        if conversion.get("status") == "cad_ready_candidate":
            by_page.setdefault(conversion.get("page"), conversion)
    geometry = []
    for page in pages:
        conversion = by_page.get(page.get("page"))
        if not conversion:
            continue
        ready_links = {
            link.get("target_wall_id"): link
            for link in page.get("dimension_link_confirmations", [])
            if link.get("status") == "cad_ready_candidate"
        }
        for wall in page.get("wall_confirmations", []):
            link = ready_links.get(wall.get("wall_id"))
            if wall.get("status") != "vector_confirmed" or not link:
                continue
            start = to_mm(wall.get("snapped_start_px"), conversion["mm_per_px"])
            end = to_mm(wall.get("snapped_end_px"), conversion["mm_per_px"])
            if start and end:
                geometry.append(
                    {
                        "wall_id": wall.get("wall_id"),
                        "page": page.get("page"),
                        "source_units": "image_px",
                        "cad_units": "mm",
                        "start_mm": start,
                        "end_mm": end,
                        "status": "cad_ready_candidate",
                        "source_measurement_id": link.get("measurement_id") or conversion.get("source_measurement_id", ""),
                    }
                )
    return geometry


def create_overlays(pages, screenshots_dir, overlays_dir):
    if not screenshots_dir:
        return []
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return [{"status": "skipped", "reason": "Pillow is not installed"}]

    screenshots_dir = Path(screenshots_dir)
    overlays_dir = Path(overlays_dir)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for page in pages:
        image_path = screenshots_dir / Path(page.get("image", "")).name
        if not image_path.exists():
            overlays.append({"page": page.get("page"), "status": "missing_image", "image": page.get("image", "")})
            continue
        image = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw_bbox(draw, page.get("plan_viewport_bbox_px"), "orange", 5)
        for wall in page.get("wall_confirmations", []):
            corridor = wall.get("experimental_search", {}).get("selected_corridor_width_px")
            if corridor:
                draw_line(draw, wall.get("vision_start_px"), wall.get("vision_end_px"), "gray", max(2, corridor * 2))
            draw_line(draw, wall.get("vision_start_px"), wall.get("vision_end_px"), "red", 4)
            draw_line(draw, wall.get("snapped_start_px"), wall.get("snapped_end_px"), "lime", 5)
            for alternative in wall.get("alternatives", [])[1:]:
                if alternative.get("geometry_type") == "curve_polyline":
                    draw_polyline(draw, alternative.get("points_px", []), "orange", 2)
                else:
                    draw_line(draw, alternative.get("start_px"), alternative.get("end_px"), "orange", 2)
            label_at(draw, wall.get("vision_start_px"), f"{wall.get('wall_id')} {wall.get('status')}", "yellow")
            if wall.get("snap_score") is not None:
                label_at(draw, wall.get("snapped_start_px"), f"snap {wall.get('snap_score')}", "lime")
            if wall.get("snapped_points_px"):
                draw_polyline(draw, wall.get("snapped_points_px"), "cyan", 4)
        for link in page.get("dimension_link_confirmations", []):
            draw_line(draw, link.get("dimension_line_start_px"), link.get("dimension_line_end_px"), "blue", 4)
            label_at(draw, link.get("dimension_line_start_px"), f"{link.get('value_mm')} {link.get('status')}", "blue")
        output = overlays_dir / f"page_{int(page.get('page', 0)):03d}_geometry_confirmation_overlay.png"
        image.save(output)
        overlays.append({"page": page.get("page"), "status": "created", "path": str(output)})
    return overlays


def overall_status(pages):
    if any(wall.get("status") == "cad_ready_candidate" for page in pages for wall in page.get("wall_confirmations", [])):
        return "cad_ready_candidate"
    if any(link.get("status") == "cad_ready_candidate" for page in pages for link in page.get("dimension_link_confirmations", [])):
        return "cad_ready_candidate"
    if any(link.get("status") == "scale_calibrated" for page in pages for link in page.get("dimension_link_confirmations", [])):
        return "scale_calibrated"
    if any(wall.get("status") == "vector_confirmed" for page in pages for wall in page.get("wall_confirmations", [])):
        return "vector_confirmed"
    if any(wall.get("status") == "experimental_vector_snap" for page in pages for wall in page.get("wall_confirmations", [])):
        return "experimental_vector_snap"
    if pages:
        return "vision_estimated"
    return "not_ready"


def page_status(walls, links):
    if any(link.get("status") == "cad_ready_candidate" for link in links):
        return "cad_ready_candidate"
    if any(link.get("status") == "scale_calibrated" for link in links):
        return "scale_calibrated"
    if any(wall.get("status") == "vector_confirmed" for wall in walls):
        return "vector_confirmed"
    if any(wall.get("status") == "experimental_vector_snap" for wall in walls):
        return "experimental_vector_snap"
    return "vision_estimated" if walls or links else "not_ready"


def site_confirm_required(item):
    text = " ".join(str(item.get(key, "")) for key in ["text_seen", "measurement_text", "notes"])
    values = " ".join(str(value) for value in item.get("uncertainties", []))
    return bool(item.get("site_confirm_required")) or "c.o.s" in f"{text} {values}".lower() or "cos" in f"{text} {values}".lower()


def line_angle(start, end):
    return math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))


def angle_difference(a, b):
    diff = abs((a - b + 180) % 360 - 180)
    return min(diff, 180 - diff)


def line_length(start, end):
    if not valid_point(start) or not valid_point(end):
        return None
    return math.dist(start, end)


def projection_ratio(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if not denominator:
        return 0
    return ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator


def midpoint(start, end):
    return [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2]


def point_distance(a, b):
    return math.dist(a, b)


def endpoint_alignment_error(start, end, candidate_start, candidate_end):
    if not all(valid_point(point) for point in [start, end, candidate_start, candidate_end]):
        return MAX_SNAP_DISTANCE_PX
    return min(
        (point_distance(start, candidate_start) + point_distance(end, candidate_end)) / 2,
        (point_distance(start, candidate_end) + point_distance(end, candidate_start)) / 2,
    )


def point_bbox_distance(point, bbox):
    if not valid_point(point) or not valid_bbox(bbox):
        return 999999
    x = min(max(point[0], bbox[0]), bbox[2])
    y = min(max(point[1], bbox[1]), bbox[3])
    return point_distance(point, [x, y])


def expand_bbox(bbox, padding):
    if not valid_bbox(bbox):
        return bbox
    return [bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding]


def line_intersects_bbox(start, end, bbox):
    if not all(valid_point(point) for point in [start, end]) or not valid_bbox(bbox):
        return False
    if point_inside_bbox(start, bbox) or point_inside_bbox(end, bbox):
        return True
    return point_inside_bbox(midpoint(start, end), bbox)


def line_near_segment(start, end, target_start, target_end, corridor_width):
    if not all(valid_point(point) for point in [start, end, target_start, target_end]):
        return False
    return min(
        point_to_segment_distance(start, target_start, target_end),
        point_to_segment_distance(end, target_start, target_end),
        point_to_segment_distance(target_start, start, end),
        point_to_segment_distance(target_end, start, end),
    ) <= corridor_width


def curve_near_segment(curve, start, end, corridor_width):
    points = curve.get("points_px", [])
    return any(point_to_segment_distance(point, start, end) <= corridor_width for point in points if valid_point(point))


def point_to_segment_distance(point, start, end):
    ratio = max(0, min(1, projection_ratio(point, start, end)))
    closest = [start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio]
    return point_distance(point, closest)


def bbox_center(bbox):
    if not valid_bbox(bbox):
        return None
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def point_inside_bbox(point, bbox):
    return valid_point(point) and valid_bbox(bbox) and bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def to_mm(point, mm_per_px):
    if not valid_point(point):
        return None
    return [round(point[0] * mm_per_px, 2), round(point[1] * mm_per_px, 2)]


def valid_point(point):
    return isinstance(point, list) and len(point) == 2 and all(isinstance(value, (int, float)) for value in point)


def valid_bbox(bbox):
    return isinstance(bbox, list) and len(bbox) == 4 and bbox[2] > bbox[0] and bbox[3] > bbox[1]


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
        draw.text((point[0] + 4, point[1] + 4), str(label), fill=color)


def main():
    parser = argparse.ArgumentParser(description="Confirm vision geometry against extracted PDF vector geometry.")
    parser.add_argument("vision_response")
    parser.add_argument("vector_geometry")
    parser.add_argument("--output")
    parser.add_argument("--screenshots-dir")
    parser.add_argument("--overlays-dir")
    args = parser.parse_args()
    output = create_geometry_confirmation(args.vision_response, args.vector_geometry, args.output, args.overlays_dir, args.screenshots_dir)
    print(f"Geometry confirmation created: {output}")


if __name__ == "__main__":
    main()
