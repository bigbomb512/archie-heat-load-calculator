"""Packet builders and strict reply validators for Card P tasks.

This module deliberately contains no transport. The operator copies each
bounded packet to ChatGPT and pastes its JSON reply into the local workspace.
"""

from copy import deepcopy
import hashlib
import json
import math
import re
from pathlib import Path

from ai import reviewer_room_geometry, site_location_resolution, room_outline, kitchen_equipment


TASKS = {
    "P0_dimensions": {"id": "room_geometry_dimensions", "inputs": ["plan_page", "dimension_line_candidates"], "budget_chars": 1500, "media": "image", "prompt_builder": "build_p0_packets", "reply_validator": "validate_dimension_reply", "apply_function": "_apply_p0_dimension", "fallback": None},
    "P0_wall_styles": {"id": "room_geometry_wall_styles", "inputs": ["plan_page_styles"], "budget_chars": 3000, "media": "image", "prompt_builder": "build_p0_packets", "reply_validator": "validate_wall_style_batch_reply", "apply_function": "_apply_p0_wall_styles", "fallback": None},
    "P0_room_names": {"id": "room_geometry_room_names", "inputs": ["calibrated_enclosed_areas", "room_registry"], "budget_chars": 4000, "media": "image", "prompt_builder": "build_p0_packets", "reply_validator": "validate_p0_room_names", "apply_function": "_apply_p0_room_names", "fallback": None},
    "P0_room_outlines": {"id": "room_geometry_outlines", "inputs": ["rooms_without_area", "plan_page"], "budget_chars": 3000, "media": "image", "prompt_builder": "build_p0_packets", "reply_validator": "validate_p0_outlines", "apply_function": "_apply_p0_outlines", "fallback": None},
    "P3_boundaries": {"id": "boundary_classification", "inputs": ["current_room_trace", "boundary_evidence"], "budget_chars": 1500, "media": "image", "prompt_builder": "build_boundary_packets", "reply_validator": "validate_boundary_reply", "apply_function": "_apply_p3", "fallback": "tenancy_boundary"},
    "P4_openings": {"id": "storefront_openings", "inputs": ["storefront_elevation", "current_room_trace"], "budget_chars": 2000, "media": "image", "prompt_builder": "build_opening_packets", "reply_validator": "validate_opening_reply", "apply_function": "_apply_p4", "fallback": "opening_dimensions"},
    "P1_site": {"id": "site_identification", "inputs": ["site_location_resolution.infer_pdf_context"],
                "budget_chars": 4000, "media": "text", "prompt_builder": "build_site_packet",
                "reply_validator": "validate_site_reply", "apply_function": "_apply_p1", "fallback": None},
    "P2_north": {"id": "north_arrow", "inputs": ["full_resolution_plan_renders"], "transport": "manual_chatgpt",
                 "max_crops_per_page": 3, "max_crop_px": 1024, "prompt_builder": "north_prompt",
                 "reply_validator": "validate_north_reply", "apply_function": "_apply_p2", "fallback": None},
    "P5_roof": {"id": "roof_exposure", "inputs": ["drawing_facts", "P1_site", "current_room_traces"],
                "budget_chars": 2000, "max_crops": 1, "prompt_builder": "roof_prompt",
                "reply_validator": "validate_roof_reply", "apply_function": "_apply_p5", "fallback": None},
    "P6_kitchen": {"id": "kitchen_equipment", "inputs": ["current_kitchen_trace", "kitchen_elevation_pages"],
                   "budget_chars": kitchen_equipment.BUDGET_CHARS, "max_images": 3,
                   "prompt_builder": "kitchen_equipment.build_packet", "reply_validator": "kitchen_equipment.validate_reply",
                   "apply_function": "_apply_p6", "fallback": None},
}


def calibration_from_dimensions(dimensions, declared_mm_per_px=None, tolerance=0.02):
    """Build P0 calibration from printed dimensions, requiring independent agreement."""
    valid = []
    for row in dimensions:
        points = row.get("points_image_px")
        value = row.get("value_mm")
        if (not isinstance(points, list) or len(points) != 2 or
                not all(isinstance(point, list) and len(point) == 2 for point in points) or
                any(type(coordinate) not in (int,float) or not math.isfinite(coordinate) for point in points for coordinate in point) or
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
            continue
        span = math.dist(points[0], points[1])
        if span > 0:
            valid.append({"points_image_px": points, "value_mm": float(value), "mm_per_px": float(value) / span,
                          "printed_text": str(row.get("printed_text", ""))})
    selected = []
    if declared_mm_per_px and valid:
        # Use the same scale check as the standalone room-outline workflow.
        from ai import room_outline
        accepted, rejected = [], []
        for row in valid:
            try:
                result = room_outline.calibration_from_dimension(row["value_mm"],
                    math.dist(*row["points_image_px"]), float(declared_mm_per_px), tolerance)
                accepted.append({**row, **result})
            except ValueError:
                rejected.append(row)
        if accepted:
            status, rate = _agree_dimensions(accepted, tolerance) if len(accepted) > 1 else ("agreed", accepted[0]["mm_per_px"])
            if status == "agreed":
                selected = accepted
        elif len(valid) >= 2:
            status, rate = _agree_dimensions(valid, tolerance)
            if status == "agreed":
                status = "declared_scale_rejected"
                selected = valid
        else:
            raise ValueError("The printed dimension does not agree with the declared scale within 2%; another dimension is required.")
    elif len(valid) >= 2:
        status, rate = _agree_dimensions(valid, tolerance)
        if status == "agreed":
            selected = valid
    else:
        raise ValueError("Without a declared scale, at least two printed dimensions agreeing within 2% are required.")
    if status != "agreed" and status != "declared_scale_rejected":
        raise ValueError("Printed dimensions disagree by more than 2%; P0 cannot calibrate this page.")
    selected = selected[:2]
    return {"source": "ai_read_printed_dimension", "status": status, "mm_per_px": rate,
            "dimension_points_image_px": deepcopy(selected[0]["points_image_px"]),
            "dimension_value_mm": selected[0]["value_mm"], "printed_text": selected[0]["printed_text"],
            "scale_consistency_tolerance": tolerance,
            **({"second_dimension": deepcopy(selected[1])} if len(selected) > 1 else {})}


def _agree_dimensions(rows, tolerance):
    if len(rows) < 2:
        return "unresolved", None
    first, second = rows[:2]
    difference = abs(first["mm_per_px"] - second["mm_per_px"]) / ((first["mm_per_px"] + second["mm_per_px"]) / 2)
    if difference > tolerance:
        return "unresolved", None
    return "agreed", (first["mm_per_px"] + second["mm_per_px"]) / 2


def validate_dimension_reply(packet, reply):
    value = parse_json_reply(reply)
    number = value.get("value_mm")
    if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
        raise ValueError("value_mm must be a positive printed dimension in millimetres, or the reply is unreadable.")
    printed = str(value.get("printed_text", "")).strip()
    if not printed:
        raise ValueError("Quote the printed dimension text used to read the value.")
    printed_digits = re.sub(r"\D", "", printed)
    value_digits = str(int(number)) if float(number).is_integer() else re.sub(r"\D", "", str(number))
    if value_digits and value_digits not in printed_digits:
        raise ValueError("The quoted printed text does not contain the returned dimension value.")
    return {"value_mm": float(number), "printed_text": printed[:120],
            "points_image_px": deepcopy(packet["line"]["tick_centres_image_px"])}


def validate_p0_room_names(packet, reply):
    value = parse_json_reply(reply)
    from ai import room_outline as ro
    names, splits = ro.validate_naming_reply(value, packet["area_count"], packet["room_names"], packet["transform"])
    return {"names": names, "splits": splits}


def validate_p0_outlines(packet, reply):
    value = parse_json_reply(reply)
    return room_outline.validate_outline_reply(value, packet["room_names"], packet["transform"])


def validate_wall_style_batch_reply(packet, reply):
    """Validate one complete legend batch; an explicit empty batch is allowed."""
    value = parse_json_reply(reply)
    if not isinstance(value.get("wall_styles"), list):
        raise ValueError("Wall-style reply must contain a wall_styles list.")
    if value["wall_styles"]:
        return {"wall_style_ids": room_outline.validate_wall_style_reply(value, packet.get("summary", [])),
                "uncertain": value.get("uncertain", [])}
    uncertain = value.get("uncertain", [])
    if not isinstance(uncertain, list) or any(type(index) is not int or index < 1 or index > len(packet.get("summary", [])) for index in uncertain):
        raise ValueError("An empty wall-style batch must have a valid uncertain list.")
    return {"wall_style_ids": [], "uncertain": uncertain,
            "notes": str(value.get("notes", "No wall styles in this legend batch."))[:300]}


def validate_boundary_reply(packet, reply):
    value = parse_json_reply(reply)
    edges = value.get("runs", value.get("edges"))
    if not isinstance(edges, list):
        raise ValueError("Boundary reply must contain a runs list.")
    allowed = room_outline_boundary_values()
    expected = {int(row["index"]): row for row in packet.get("edges", [])}
    result, seen = [], set()
    for row in edges:
        if not isinstance(row, dict):
            raise ValueError("Boundary reply contains an invalid or repeated edge index.")
        index = row.get("index")
        if type(row.get("run_number")) is int:
            index = row["run_number"] - 1
        elif type(row.get("edge_number")) is int:
            index = row["edge_number"] - 1
        if type(index) is not int or index not in expected or index in seen:
            raise ValueError("Boundary reply contains an invalid or repeated run number.")
        boundary = row.get("boundary")
        if boundary not in allowed:
            raise ValueError("Boundary must be external, mall, adjacent_tenancy, internal or unknown.")
        evidence = str(row.get("evidence", "")).strip()
        shared_question_runs = packet.get("shared_run_indices", packet.get("shared_edges", []))
        if boundary != "unknown" and index not in shared_question_runs and not evidence:
            raise ValueError(f"Run {index + 1} needs quoted evidence for a non-unknown classification.")
        storefront_index = packet.get("storefront_run_index", packet.get("storefront_edge_index"))
        if index == storefront_index and boundary in {"adjacent_tenancy", "internal"}:
            raise ValueError("The run matching the storefront elevation width cannot be internal or adjacent_tenancy.")
        result.append({"index": index, "boundary": boundary, "evidence": evidence[:300]})
        seen.add(index)
    if seen != set(expected):
        raise ValueError("Classify every numbered edge, including unknown edges.")
    return {"edges": result}


def room_outline_boundary_values():
    return {"external", "mall", "adjacent_tenancy", "internal", "unknown"}


def validate_opening_reply(packet, reply):
    value = parse_json_reply(reply)
    panels = value.get("panels")
    if not isinstance(panels, list):
        raise ValueError("Opening reply must contain a panels list.")
    text_layer = [str(item) for item in packet.get("text_layer", [])]
    edge_width = float(packet.get("edge_length_mm", 0))
    total = value.get("total_width_mm")
    total_text = str(value.get("total_width_text") or "")
    if type(total) not in (int, float) or total <= 0:
        raise ValueError("A positive printed shopfront total width is required.")
    if not total_text and not packet.get("allow_vector_outline_read"):
        raise ValueError("Quote the printed total width from the elevation text layer.")
    if total_text and text_layer and not packet.get("allow_vector_outline_read") and not any(total_text.casefold() in text.casefold() for text in text_layer):
        raise ValueError("The printed total width text is not present in the elevation text layer.")
    if abs(total - edge_width) / edge_width > .02 if edge_width > 0 else True:
        raise ValueError("The printed elevation total must match the traced storefront edge within 2%.")
    result, width_sum = [], 0.0
    ceiling = float(packet.get("ceiling_height_mm") or 0)
    excluded = deepcopy(value.get("excluded", []))
    for index, panel in enumerate(panels):
        if not isinstance(panel, dict):
            raise ValueError("Every glazed panel must be an object.")
        width, sill, head = panel.get("width_mm"), panel.get("sill_mm"), panel.get("head_mm")
        source = str(panel.get("source", "printed_text"))
        printed = panel.get("printed_text") if isinstance(panel.get("printed_text"), list) else []
        if source == "read_from_image":
            if not packet.get("allow_vector_outline_read"):
                raise ValueError("Vision-read dimensions are allowed only for drawings with outlined dimension text.")
        elif not text_layer or not printed or any(not any(str(text).casefold() in line.casefold() for line in text_layer) for text in printed):
            raise ValueError("Every dimension must quote text found in the elevation text layer or be marked read_from_image.")
        if width is None:
            excluded.append({"label": str(panel.get("label", f"Panel {index + 1}")),
                             "why": "Panel width is not printed; panel was not applied."})
            continue
        if type(width) not in (int, float) or width <= 0:
            raise ValueError("Each applied glazing panel needs a positive printed width.")
        if sill is None:
            sill, sill_assumption = 0.0, "Assumed glass to floor"
        else:
            sill_assumption = ""
        if head is None:
            head, head_assumption = ceiling, "Assumed glass to ceiling"
        else:
            head_assumption = ""
        if type(sill) not in (int, float) or type(head) not in (int, float) or head <= sill or head <= 0:
            raise ValueError("Glazing head height must be above its sill height.")
        capped = head > ceiling > 0
        if capped:
            head = ceiling
            head_assumption = "Head above ceiling; capped at ceiling height"
        result.append({"panel_index": index + 1, "width_mm": float(width), "sill_mm": float(sill),
                       "head_mm": float(head), "source": source, "printed_text": printed,
                       "sill_assumption": sill_assumption, "head_assumption": head_assumption})
        width_sum += float(width)
    if width_sum > total + 1e-6:
        raise ValueError("Glazed panel widths cannot exceed the printed total width.")
    if packet.get("allow_vector_outline_read") and abs(width_sum - total) / total > .02:
        raise ValueError("Vision-read panel widths must sum to the printed total within 2%.")
    return {"total_width_mm": float(total), "total_width_text": total_text, "panels": result,
            "excluded": excluded}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def parse_json_reply(reply):
    text = str(reply or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Reply must be valid JSON: {error.msg}.") from error
    if not isinstance(value, dict):
        raise ValueError("Reply must be a JSON object.")
    return value


def build_site_packet(ai_input, spatial_ocr, building_evidence):
    context = site_location_resolution.infer_pdf_context(ai_input, spatial_ocr, building_evidence)
    excerpts = []
    for group in ("address_candidates", "site_name_candidates", "excluded_address_candidates"):
        for row in context.get(group, []):
            source = row.get("source", {})
            excerpt = source.get("excerpt", "")
            if excerpt:
                excerpts.append({"page": source.get("page"), "drawing_number": source.get("drawing_number", ""),
                                 "text": excerpt, "clue_type": group})
    unique = {(row["page"], row["text"]): row for row in excerpts}
    excerpts = sorted(unique.values(), key=lambda row: (row["page"] or 0, row["clue_type"], row["text"]))
    candidates = []
    for group, kind in (("site_name_candidates", "tenancy_in_centre"), ("address_candidates", "street_address")):
        for row in context.get(group, []):
            source = row.get("source", {})
            text = source.get("excerpt", "")
            if text:
                candidates.append({"text": text, "page": source.get("page"), "kind": kind,
                                   "confidence": float(row.get("confidence") or 0), "candidate_id": row.get("candidate_id", "")})
    top_candidate = max(candidates, key=lambda row: row["confidence"]) if candidates else None
    packet = {"task": "P1_site", "excerpts": excerpts, "rule_based_top_candidate": top_candidate}
    prompt = (
        "Here are short excerpts from the title blocks and notes of one drawing set, each with its page number. "
        "Decide which excerpt names the project site (a street address, or a tenancy in a named centre or building), "
        "and which addresses belong to consultants (architect, engineer, builder offices). Use only the excerpts given; "
        "do not add a suburb, postcode or state that is not printed. If no excerpt names the site, return \"site\": null.\n"
        'JSON only: {"site": {"text": string, "page": int, "kind": "street_address"|"tenancy_in_centre"} | null, '
        '"consultant_addresses": [{"text": string, "page": int, "why": string}]}\n\n'
        + json.dumps(packet["excerpts"], ensure_ascii=False, separators=(",", ":"))
    )
    return packet, prompt


def validate_site_reply(packet, reply):
    value = parse_json_reply(reply)
    excerpts = packet.get("excerpts", [])
    site = value.get("site")
    if site is not None:
        if not isinstance(site, dict) or site.get("kind") not in {"street_address", "tenancy_in_centre"}:
            raise ValueError("site must be null or an object with a supported kind.")
        text, page = str(site.get("text", "")).strip(), site.get("page")
        if not text or type(page) is not int or not any(row.get("page") == page and text in row.get("text", "") for row in excerpts):
            raise ValueError("Site text must be an exact substring of a supplied excerpt on the cited page.")
    consultants = value.get("consultant_addresses", [])
    if not isinstance(consultants, list):
        raise ValueError("consultant_addresses must be a list.")
    checked_consultants = []
    for row in consultants:
        if not isinstance(row, dict) or not str(row.get("why", "")).strip():
            raise ValueError("Each consultant address needs text, page and a reason.")
        text, page = str(row.get("text", "")).strip(), row.get("page")
        if not text or type(page) is not int or not any(item.get("page") == page and text in item.get("text", "") for item in excerpts):
            raise ValueError("Consultant address text must be an exact substring of a supplied excerpt on the cited page.")
        if site and text == str(site.get("text", "")).strip() and page == site.get("page"):
            raise ValueError("The site excerpt cannot also be classified as a consultant address.")
        checked_consultants.append({"text": text, "page": page, "why": str(row["why"]).strip()[:300]})
    return {"site": deepcopy(site), "consultant_addresses": checked_consultants}


def site_cross_check(packet, validated):
    candidate = packet.get("rule_based_top_candidate")
    site = validated.get("site")
    if not candidate:
        return {"status": "unavailable", "reason": "No rule-based site candidate was produced."}
    normalise = lambda value: " ".join(str(value or "").casefold().split())
    ai_text, candidate_text = normalise((site or {}).get("text")), normalise(candidate.get("text"))
    agrees = bool(site and ai_text and candidate_text and (ai_text in candidate_text or candidate_text in ai_text))
    return {"status": "agrees" if agrees else "disagrees",
            "rule_based_candidate": {key: candidate.get(key) for key in ("text", "page", "kind", "confidence")},
            "ai_site": deepcopy(site)}


def plan_pages(ai_input):
    pages = (ai_input or {}).get("drawing_set", {}).get("pages", [])
    selected = []
    for row in pages:
        if not isinstance(row, dict):
            continue
        role = str(row.get("plan_role", "")).casefold()
        kind = str(row.get("type", row.get("sheet_classification", ""))).casefold()
        if "floor_plan" in kind or role in {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "enlarged_plan"}:
            selected.append(row)
    return sorted({row.get("page"): row for row in selected if type(row.get("page")) is int}.values(), key=lambda row: row["page"])


def _page_screenshot(root, page):
    packet = Path(root) / "chatgpt_packet" / "screenshots"
    matches = sorted(packet.glob(f"page_{int(page):03d}_*.png"))
    if not matches:
        fallback = packet / f"page_{int(page):03d}.png"
        if fallback.is_file():
            return fallback
        return None
    return matches[0]


def north_crop_boxes(width, height):
    """Three disjoint title-block/corner crops in full-page pixel coordinates."""
    return [
        (round(width * .62), round(height * .62), width, height),
        (0, 0, round(width * .38), round(height * .38)),
        (round(width * .62), 0, width, round(height * .38)),
    ]


def crop_to_page(point, crop):
    """Map resized crop pixels back to page pixels using its saved transform."""
    x, y = point
    box = crop["page_bbox"]
    return [box[0] + float(x) * box[2] / crop["width_px"],
            box[1] + float(y) * box[3] / crop["height_px"]]


def make_north_crops(root, page, output_dir):
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("North-arrow crop generation requires the bundled Pillow image library.") from error
    source = _page_screenshot(root, page)
    if not source:
        raise ValueError(f"No full-resolution page render is available for page {page}; the north task is blocked.")
    crops, rows = [], []
    with Image.open(source) as original:
        image = original.convert("RGB")
        width, height = image.size
        for index, box in enumerate(north_crop_boxes(width, height)):
            left, top, right, bottom = box
            region = image.crop(box)
            scale = min(1.0, 1024.0 / max(region.size))
            size = (max(1, round(region.width * scale)), max(1, round(region.height * scale)))
            if size != region.size:
                region = region.resize(size)
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / f"crop-{index + 1}.png"
            region.save(target, format="PNG", optimize=True)
            crop = {"crop_index": index, "file": target.name,
                    "page_bbox": [left, top, right - left, bottom - top],
                    "width_px": size[0], "height_px": size[1],
                    "source_width_px": width, "source_height_px": height}
            crops.append(crop)
            rows.append(target)
    return crops, rows


def north_prompt(packet):
    return (
        "These images are crops of one architectural plan page. Find the north arrow (an arrow, a needle in a circle, "
        "or an N with a pointer). Give the pixel position of its tail and tip in the crop where you see it. If there is "
        "no north arrow, or you cannot tell which end is the tip, return \"found\": false. Do not use the sheet orientation, "
        "text direction or building shape. Coordinates are pixels in the attached crop; choose only a listed crop_index.\n"
        'JSON only: {"found": bool, "crop_index": int|null, "tail_px": [x,y]|null, "tip_px": [x,y]|null, '
        '"labelled_north": bool, "description": string}\n\n'
        + json.dumps({"page": packet["page"], "crops": [{key: crop[key] for key in ("crop_index", "width_px", "height_px")} for crop in packet["crops"]]}, separators=(",", ":"))
    )


def validate_north_reply(packet, reply):
    value = parse_json_reply(reply)
    if type(value.get("found")) is not bool:
        raise ValueError("found must be true or false.")
    if not value["found"]:
        if any(value.get(field) is not None for field in ("crop_index", "tail_px", "tip_px")):
            raise ValueError("When found is false, crop_index, tail_px and tip_px must be null.")
        return {"found": False, "description": str(value.get("description", ""))[:300], "labelled_north": bool(value.get("labelled_north"))}
    index = value.get("crop_index")
    crop = next((row for row in packet.get("crops", []) if row.get("crop_index") == index), None)
    if crop is None:
        raise ValueError("crop_index must identify one supplied crop.")
    points = []
    for name in ("tail_px", "tip_px"):
        point = value.get(name)
        if (not isinstance(point, list) or len(point) != 2 or
                any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in point)):
            raise ValueError(f"{name} must be a finite [x, y] crop-pixel pair.")
        if not (0 <= point[0] < crop["width_px"] and 0 <= point[1] < crop["height_px"]):
            raise ValueError(f"{name} must lie inside the selected crop.")
        points.append(point)
    if math.dist(*points) < 2:
        raise ValueError("North-arrow tail and tip must be distinct points at least 2 crop pixels apart.")
    page_points = [crop_to_page(point, crop) for point in points]
    bearing = reviewer_room_geometry.page_up_bearing_from_north_arrow(page_points)
    return {"found": True, "crop_index": index, "tail_px": points[0], "tip_px": points[1],
            "tail_page_px": page_points[0], "tip_page_px": page_points[1], "plan_up_azimuth_deg": bearing,
            "labelled_north": bool(value.get("labelled_north")), "description": str(value.get("description", ""))[:300]}


def angular_difference(a, b):
    return abs((float(a) - float(b) + 180) % 360 - 180)


def north_consensus(values, tolerance=5.0):
    """Return a winning angle only when a strict majority forms a 5° cluster."""
    rows = [(int(page), float(angle)) for page, angle in values]
    if not rows:
        return {"status": "no_north", "winner": None, "pages": []}
    best = []
    for candidate in rows:
        cluster = [row for row in rows if angular_difference(candidate[1], row[1]) <= tolerance]
        if len(cluster) > len(best):
            best = cluster
    if len(rows) == 1 or len(best) > len(rows) / 2:
        # Circular mean avoids a 359°/0° split.
        x = sum(math.cos(math.radians(row[1])) for row in best)
        y = sum(math.sin(math.radians(row[1])) for row in best)
        return {"status": "agreed" if len(best) == len(rows) else "majority", "winner": math.degrees(math.atan2(y, x)) % 360,
                "pages": [row[0] for row in best], "conflict_pages": [row[0] for row in rows if row not in best]}
    return {"status": "disagreement", "winner": None, "pages": [row[0] for row in best],
            "conflict_pages": [row[0] for row in rows if row not in best]}


def build_roof_facts(ai_input, site_determination, room):
    pages = (ai_input or {}).get("drawing_set", {}).get("pages", [])
    terms = re.compile(r"\b(?:slab\s+over|roof\s+over|level\s+above|apartments?|residences?|podium|stand[- ]alone|single[- ]storey|ground\s+floor|level\s+\d+)\b", re.I)
    facts = []
    levels = set()
    tenancy_prefix = ""
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = page.get("page")
        title = str(page.get("title", ""))
        level = str(page.get("level_name", ""))
        if level:
            levels.add(level)
        text = str(page.get("structured_content", {}).get("markdown", ""))
        if title:
            text = title + "\n" + text
        for match in terms.finditer(text):
            excerpt = text[max(0, match.start() - 100):min(len(text), match.end() + 160)].strip()
            facts.append({"page": number, "text": excerpt[:260]})
        tenancy = re.search(r"\b(?:TENANCY|SHOP|SUITE)\s+([A-Z]\d{1,4})\b", text, re.I)
        if tenancy and not tenancy_prefix:
            tenancy_prefix = tenancy.group(1).upper()
    fact_text = "\n".join(row["text"] for row in facts)
    names = (site_determination or {}).get("site", {}) if isinstance(site_determination, dict) else {}
    site_name = names.get("text", "") if isinstance(names, dict) else ""
    site_kind = names.get("kind", "") if isinstance(names, dict) else ""
    site_name = site_name or (site_determination or {}).get("site_text", "")
    site_kind = site_kind or (site_determination or {}).get("site_kind", "")
    room_level = str((room or {}).get("level_name", ""))
    return {"levels_present": sorted(levels), "facts": facts[:20], "site_name": str(site_name),
            "site_kind": str(site_kind), "tenancy_prefix": tenancy_prefix,
            "room": {key: (room or {}).get(key) for key in ("room_id", "room_label", "level_name", "page")},
            "mentions": {term: bool(re.search(term, fact_text, re.I)) for term in
                         ("slab over", "roof over", "level above", "apartments", "residences", "podium")}}


def roof_prompt(packet):
    return (
        "From these drawing facts, is the roof directly above this tenancy exposed to the sky? Answer exposed, not_exposed "
        "or unknown, quoting the fact you used. Do not guess from the business type.\n"
        'JSON only: {"roof": "exposed"|"not_exposed"|"unknown", "evidence": string}\n\n'
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    )


def validate_roof_reply(packet, reply):
    value = parse_json_reply(reply)
    if value.get("roof") not in {"exposed", "not_exposed", "unknown"}:
        raise ValueError("roof must be exposed, not_exposed or unknown.")
    evidence = str(value.get("evidence", "")).strip()
    if value["roof"] != "unknown":
        sources = [str(row.get("text", "")) for row in packet.get("facts", []) if isinstance(row, dict)]
        explicit = any(re.search(
            r"\b(?:roof|slab|floor|tenancy|level)\b.{0,60}\b(?:above|over|below|beneath|directly)\b|"
            r"\b(?:above|over|below|beneath|directly)\b.{0,60}\b(?:roof|slab|floor|tenancy|level)\b",
            source, re.I) for source in sources)
        if not evidence or not any(evidence in source for source in sources) or not explicit:
            raise ValueError("A roof determination needs a quoted drawing fact that explicitly establishes what is above the tenancy.")
    return {"roof": value["roof"], "evidence": evidence[:300]}
