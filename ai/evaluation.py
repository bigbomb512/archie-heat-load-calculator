#!/usr/bin/env python3

"""Compare a private drawing-packet run against compact expected facts."""

import json
from pathlib import Path

from ai.geometry_review import normalise_vision


PHYSICAL_WALL_CLASSES = {"outer_boundary_wall", "internal_partition"}


def evaluate_packet(case_path, packet_dir):
    case = load_json(case_path)
    packet_dir = Path(packet_dir)
    files = packet_files(packet_dir)
    ai_input = load_optional(files["ai_input"])
    matcher = load_optional(files["dimension_wall_matches"])
    vector = load_optional(files["vector_geometry"])
    candidate_review = load_optional(files["candidate_review"])
    vision = normalise_vision(load_optional(files["vision_response"]), candidate_review)
    confirmation = load_optional(files["geometry_confirmation"])

    results = []
    expectations = case.get("expectations", {})
    evaluate_packet_facts(results, expectations, ai_input)
    evaluate_vector_facts(results, expectations, vector, matcher)
    evaluate_vision_facts(results, expectations, vision)
    evaluate_confirmation_facts(results, expectations, confirmation)

    return {
        "case_id": case.get("case_id", Path(case_path).stem),
        "description": case.get("description", ""),
        "mode": "report_only",
        "packet_folder": str(packet_dir),
        "available_layers": {name: path.exists() for name, path in files.items()},
        "results": results,
        "scorecard": scorecard(results),
    }


def packet_files(packet_dir):
    return {
        "ai_input": packet_dir / "ai_input.json",
        "vector_geometry": packet_dir / "vector_geometry.json",
        "dimension_wall_matches": packet_dir / "dimension_wall_matches.json",
        "candidate_review": packet_dir / "candidate_review.json",
        "vision_response": packet_dir / "vision_response.json",
        "geometry_confirmation": packet_dir / "geometry_confirmation.json",
    }


def evaluate_packet_facts(results, expected, ai_input):
    pages = expected.get("pages", {})
    if pages:
        if not ai_input:
            add_missing_layer(results, "packet", "page expectations", "ai_input.json")
        else:
            actual = page_sets(ai_input)
            for kind, expected_pages in pages.items():
                expected_set = set(expected_pages)
                if kind == "excluded":
                    unexpected = sorted(expected_set & actual["all"])
                    add_result(results, "packet", f"excluded pages: {sorted(expected_set)}", "passed" if not unexpected else "failed", expected_pages, unexpected, "pages must not be selected as AI context")
                else:
                    missing = sorted(expected_set - actual.get(kind, set()))
                    add_result(results, "packet", f"{kind} pages: {sorted(expected_set)}", "passed" if not missing else "failed", expected_pages, missing, "required pages must be retained in their expected role")

    floors = expected.get("floors", [])
    if floors:
        if not ai_input:
            add_missing_layer(results, "packet", "floor expectations", "ai_input.json")
        else:
            actual_floors = ai_input.get("building_model", {}).get("floors", [])
            for item in floors:
                label = item.get("label", "")
                expected_pages = set(item.get("source_pages", []))
                floor = next((candidate for candidate in actual_floors if candidate.get("label") == label), None)
                actual_pages = set((floor or {}).get("source_pages", []))
                missing = sorted(expected_pages - actual_pages)
                add_result(results, "packet", f"floor: {label}", "passed" if floor and not missing else "failed", item, {"found": bool(floor), "missing_pages": missing}, "floor label and supporting pages must remain grouped")


def evaluate_vector_facts(results, expected, vector, matcher):
    dimensions = expected.get("major_dimensions", [])
    if dimensions:
        if not matcher:
            add_missing_layer(results, "vector", "major dimensions", "dimension_wall_matches.json")
        else:
            by_page = {page.get("page"): page for page in matcher.get("pages", [])}
            for item in dimensions:
                page = by_page.get(item.get("page"), {})
                found = major_dimension_values(page)
                value = item.get("value_mm")
                add_result(results, "vector", f"major dimension {value} on page {item.get('page')}", "passed" if value in found else "failed", item, sorted(found), "major written dimensions must remain visible as machine evidence")

    forbidden = expected.get("forbidden_wall_regions", [])
    if forbidden:
        if not vector:
            add_missing_layer(results, "vector", "forbidden vector regions", "vector_geometry.json")
        else:
            pages = {page.get("page"): page for page in vector.get("geometry_key_points", {}).get("pages", [])}
            for item in forbidden:
                page = pages.get(item.get("page"), {})
                offenders = [wall.get("candidate_id") for wall in page.get("wall_candidates", []) if geometry_in_bbox(wall, item.get("bbox_px"))]
                add_result(results, "vector", f"forbidden vector region: {item.get('label', 'unnamed')}", "passed" if not offenders else "failed", item, offenders, "raw vector wall candidates must stay outside title-block, notes, and fixture regions")


def evaluate_vision_facts(results, expected, vision):
    walls = expected.get("minimum_physical_walls", [])
    forbidden = expected.get("forbidden_wall_regions", [])
    curves = expected.get("curves", [])
    links = expected.get("dimension_links", [])
    if not any([walls, forbidden, curves, links]):
        return
    layered_pages = vision.get("result", {}).get("layered_geometry", {}).get("pages", []) if vision else []
    if not layered_pages:
        add_missing_layer(results, "vision", "wall and dimension expectations", "vision_response.json")
        return
    by_page = {page.get("page"): page for page in layered_pages}

    for item in walls:
        page = by_page.get(item.get("page"), {})
        actual = physical_walls(page)
        minimum = item.get("minimum", 0)
        add_result(results, "vision", f"minimum physical walls on page {item.get('page')}", "passed" if len(actual) >= minimum else "failed", minimum, len(actual), "only classified physical walls count")

    for item in forbidden:
        page = by_page.get(item.get("page"), {})
        offenders = [wall.get("wall_id") for wall in physical_walls(page) if geometry_in_bbox(wall, item.get("bbox_px"))]
        add_result(results, "vision", f"forbidden classified-wall region: {item.get('label', 'unnamed')}", "passed" if not offenders else "failed", item, offenders, "fixtures, counters, notes, and title blocks cannot be physical walls")

    for item in curves:
        page = by_page.get(item.get("page"), {})
        curved = [wall for wall in physical_walls(page) if wall.get("geometry_type") == "curve_polyline" or len(wall.get("points_px", [])) >= 3]
        minimum = item.get("minimum", 1)
        add_result(results, "vision", f"curve polyline on page {item.get('page')}", "passed" if len(curved) >= minimum else "failed", minimum, len(curved), "curved walls must stay as point chains rather than straight lines")

    for item in links:
        page = by_page.get(item.get("page"), {})
        walls_by_id = {wall.get("wall_id"): wall for wall in physical_walls(page)}
        matching = [link for link in page.get("dimension_wall_links", []) if link.get("value_mm") == item.get("value_mm")]
        valid = any(link.get("target_wall_id") in walls_by_id for link in matching)
        add_result(results, "vision", f"dimension link {item.get('value_mm')} on page {item.get('page')}", "passed" if valid else "failed", item, [link.get("target_wall_id") for link in matching], "major dimensions may only link to classified physical walls")


def evaluate_confirmation_facts(results, expected, confirmation):
    required = expected.get("confirmation", {})
    if not required:
        return
    if not confirmation:
        add_missing_layer(results, "confirmation", "geometry confirmation expectations", "geometry_confirmation.json")
        return
    pages = confirmation.get("pages", [])
    for status, minimum in required.get("minimum_status_counts", {}).items():
        count = sum(1 for page in pages for link in page.get("dimension_link_confirmations", []) if link.get("status") == status)
        add_result(results, "confirmation", f"minimum {status} links", "passed" if count >= minimum else "failed", minimum, count, "confirmation status must meet the expected evidence gate")


def page_sets(ai_input):
    confirmed = ai_input.get("confirmed_pages", {})
    geometry = {item.get("page") for item in confirmed.get("floor_plans", [])}
    rcp = {item.get("page") for item in confirmed.get("reflected_ceiling_plans", [])}
    context = {item.get("page") for item in confirmed.get("existing_hvac_or_services_plans", [])}
    context.update(item.get("page") for item in confirmed.get("reference_pages", []))
    return {"geometry": geometry, "rcp_context": rcp, "support_context": context, "all": geometry | rcp | context}


def major_dimension_values(page):
    summary = page.get("summary", {})
    values = {item.get("value_mm") for item in summary.get("major_boundary_dimensions", [])}
    values.update(item.get("value_mm") for item in page.get("dimension_wall_matches", []) if item.get("dimension_kind") in {"overall", "boundary", "setout", "major"})
    return {value for value in values if isinstance(value, (int, float))}


def physical_walls(page):
    return [wall for key in PHYSICAL_WALL_CLASSES for wall in page.get(f"{key}s", [])]


def geometry_in_bbox(item, bbox):
    if not valid_bbox(bbox):
        return False
    points = item.get("points_px", []) or [item.get("line_start_px") or item.get("start_px"), item.get("line_end_px") or item.get("end_px")]
    points = [point for point in points if valid_point(point)]
    if any(point_in_bbox(point, bbox) for point in points):
        return True
    return any(segment_intersects_bbox(start, end, bbox) for start, end in zip(points, points[1:]))


def point_in_bbox(point, bbox):
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def segment_intersects_bbox(start, end, bbox):
    steps = max(1, int(max(abs(end[0] - start[0]), abs(end[1] - start[1])) / 8))
    return any(point_in_bbox([start[0] + (end[0] - start[0]) * index / steps, start[1] + (end[1] - start[1]) * index / steps], bbox) for index in range(steps + 1))


def valid_bbox(value):
    return isinstance(value, list) and len(value) == 4 and all(isinstance(item, (int, float)) for item in value) and value[0] <= value[2] and value[1] <= value[3]


def valid_point(value):
    return isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value)


def add_missing_layer(results, stage, name, filename):
    add_result(results, stage, name, "not_evaluated", "available", "missing", f"{filename} was not present in this packet")


def add_result(results, stage, name, status, expected, actual, detail):
    results.append({"stage": stage, "name": name, "status": status, "expected": expected, "actual": actual, "detail": detail})


def scorecard(results):
    stages = {}
    for item in results:
        stage = stages.setdefault(item["stage"], {"passed": 0, "failed": 0, "not_evaluated": 0, "manual_review_needed": 0})
        stage[item["status"]] = stage.get(item["status"], 0) + 1
    for values in stages.values():
        scored = values["passed"] + values["failed"]
        values["accuracy_percent"] = round(values["passed"] * 100 / scored, 1) if scored else None
    totals = {"passed": 0, "failed": 0, "not_evaluated": 0, "manual_review_needed": 0}
    for values in stages.values():
        for key in totals:
            totals[key] += values.get(key, 0)
    scored = totals["passed"] + totals["failed"]
    totals["accuracy_percent"] = round(totals["passed"] * 100 / scored, 1) if scored else None
    return {"stages": stages, "totals": totals}


def render_markdown(report):
    lines = [f"# Evaluation: {report['case_id']}", "", "Report-only Vision Lab result.", "", "## Scorecard", "", "| Stage | Passed | Failed | Not evaluated | Accuracy |", "| --- | ---: | ---: | ---: | ---: |"]
    for stage, values in report["scorecard"]["stages"].items():
        accuracy = "-" if values["accuracy_percent"] is None else f"{values['accuracy_percent']}%"
        lines.append(f"| {stage} | {values['passed']} | {values['failed']} | {values['not_evaluated']} | {accuracy} |")
    lines.extend(["", "## Results", ""])
    for item in report["results"]:
        lines.append(f"- **{item['status']}** `{item['stage']}`: {item['name']} - {item['detail']}")
    return "\n".join(lines) + "\n"


def load_optional(path):
    return load_json(path) if path.exists() else {}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
