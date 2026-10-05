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

from ai import reviewer_room_geometry, site_location_resolution


TASKS = {
    "P1_site": {"id": "site_identification", "inputs": ["site_location_resolution.infer_pdf_context"],
                "budget_chars": 4000, "media": "text", "prompt_builder": "build_site_packet",
                "reply_validator": "validate_site_reply", "apply_function": "_apply_p1", "fallback": None},
    "P2_north": {"id": "north_arrow", "inputs": ["full_resolution_plan_renders"], "transport": "manual_chatgpt",
                 "max_crops_per_page": 3, "max_crop_px": 1024, "prompt_builder": "north_prompt",
                 "reply_validator": "validate_north_reply", "apply_function": "_apply_p2", "fallback": None},
    "P5_roof": {"id": "roof_exposure", "inputs": ["drawing_facts", "P1_site", "current_room_traces"],
                "budget_chars": 2000, "max_crops": 1, "prompt_builder": "roof_prompt",
                "reply_validator": "validate_roof_reply", "apply_function": "_apply_p5", "fallback": None},
}


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

