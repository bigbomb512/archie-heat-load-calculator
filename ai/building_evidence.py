"""Reusable, cited building evidence extracted from a reviewed drawing set."""

import re


EQUIPMENT_WORDS = {
    "refrigeration": ["fridge", "freezer", "display fridge", "cool room"],
    "cooking": ["oven", "cooktop", "range", "grill", "fryer", "boiler"],
    "appliance": ["ice maker", "dishwasher", "microwave", "coffee machine"],
}


def build_building_evidence(ai_input, drawing_coverage=None, spatial_ocr=None, vision_response=None):
    pages = source_pages(ai_input, spatial_ocr or {})
    result = empty_evidence(ai_input)
    for page in pages:
        add_spaces(result, page)
        add_surfaces(result, page)
        add_openings(result, page)
        add_constructions(result, page)
        add_lighting(result, page)
        add_equipment(result, page)
    add_levels(result, drawing_coverage or {})
    deduplicate(result)
    result["cross_sheet_links"] = proposed_links(result, pages)
    result["exceptions"] = exceptions(result, drawing_coverage or {})
    result["sources"] = {
        "drawing_coverage": bool(drawing_coverage),
        "spatial_ocr": bool(spatial_ocr),
        "vision_response": bool(vision_response),
        "manual_vision_note": "Vision may propose cited visual relationships; all inferred relationships require engineer review.",
    }
    return result


def empty_evidence(ai_input):
    return {
        "version": 1,
        "source_pdf": ai_input.get("source_pdf", ""),
        "spaces": [], "levels": [], "surfaces": [], "openings": [], "constructions": [],
        "lighting": [], "equipment": [], "cross_sheet_links": [], "exceptions": [], "sources": {},
    }


def source_pages(ai_input, spatial_ocr):
    ocr_text = {}
    for page in spatial_ocr.get("pages", []):
        ocr_text[page.get("page")] = "\n".join(item.get("text_excerpt", "") for item in page.get("title_blocks", []))
    pages = []
    drawing_pages = ai_input.get("drawing_set", {}).get("pages", [])
    if not drawing_pages:
        drawing_pages = ai_input.get("confirmed_pages", {}).get("floor_plans", []) + ai_input.get("confirmed_pages", {}).get("reference_pages", [])
    for page in drawing_pages:
        pages.append({
            "page": page.get("page"), "level_name": page.get("level_name", ""),
            "classification": page.get("sheet_classification", page.get("detected_type", "other")),
            "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
            "title": page.get("title", ""), "rooms": page.get("rooms", []),
            "text": page.get("structured_content", {}).get("markdown", "") + "\n" + ocr_text.get(page.get("page"), ""),
        })
    return pages


def source(page, excerpt):
    return {"page": page["page"], "kind": "reviewed_pdf_text", "sheet_classification": page["classification"], "excerpt": excerpt}


def record(result, family, page, value, status="direct", **extra):
    entries = result[family]
    item = {"id": f"{family}-{page['page']}-{len(entries) + 1}", "status": status, "confidence": "high" if status == "direct" else "medium", "evidence": [source(page, extra.pop("excerpt", page["title"]))]}
    item.update(value)
    item.update(extra)
    entries.append(item)


def add_spaces(result, page):
    for room in page["rooms"]:
        label = room.get("name", "").strip()
        if label:
            record(result, "spaces", page, {"name": label, "area": room.get("area", ""), "level_name": page["level_name"]}, excerpt=label)
    for match in re.finditer(r"(?:\b([A-Za-z][A-Za-z0-9 .-]{1,40})\s+)?AREA\s*[:.]?\s*(\d+(?:\.\d+)?)\s*(m²|m2)\b", page["text"], re.I):
        label = (match.group(1) or page["title"] or "Proposed space").strip()
        record(result, "spaces", page, {"name": label, "area": match.group(2) + " " + match.group(3), "level_name": page["level_name"]}, excerpt=match.group(0))


def add_levels(result, coverage):
    for level in coverage.get("levels", []):
        status = level.get("purpose_status", "missing")
        result["levels"].append({
            "id": "level-" + slug(level.get("level_name", "unassigned")), "name": level.get("level_name", ""),
            "proposed_purpose": level.get("proposed_purpose", ""), "status": status,
            "conditioned_status": level.get("conditioned_status", "unknown"),
            "evidence": level.get("purpose_evidence", []),
        })


def add_surfaces(result, page):
    text = page["text"]
    patterns = [
        ("adjacent_space", r"(?:adjoining|adjacent)\s+([A-Za-z0-9 .-]{2,50})"),
        ("external_boundary", r"\b(external wall|outside|outdoor|street frontage|shopfront)\b"),
        ("roof_or_ceiling", r"\b(roof|ceiling void|roof void|exposed ceiling)\b"),
        ("ground_floor", r"\b(ground slab|floor to ground|slab on ground)\b"),
    ]
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            label = re.sub(r"\s+", " ", match.group(1)).strip(" .:-")
            record(result, "surfaces", page, {"kind": kind, "adjacency": label, "level_name": page["level_name"], "geometry": None}, excerpt=match.group(0))


def add_openings(result, page):
    text = page["text"]
    for match in re.finditer(r"\b([WD]\d{1,3}[A-Z]?)\b", text, re.I):
        tag = match.group(1).upper()
        kind = "window_or_glazing" if tag.startswith("W") else "door"
        record(result, "openings", page, {"tag": tag, "kind": kind, "level_name": page["level_name"], "dimensions": None, "performance": None}, excerpt=tag)
    for word, kind in [("glazing", "window_or_glazing"), ("window", "window_or_glazing"), ("door", "door")]:
        if re.search(r"\b" + word + r"\b", text, re.I):
            record(result, "openings", page, {"tag": "", "kind": kind, "level_name": page["level_name"], "dimensions": None, "performance": None}, excerpt=word)


def add_constructions(result, page):
    for line in page["text"].splitlines():
        if not re.search(r"\b(wall|roof|floor|glazing|window)\b", line, re.I):
            continue
        if not re.search(r"\b(type|construction|insulation|cladding|glass|glazing|u-value|r-value)\b", line, re.I):
            continue
        kind = "roof" if re.search(r"\broof\b", line, re.I) else "glazing" if re.search(r"\b(glazing|window|glass)\b", line, re.I) else "wall_or_floor"
        record(result, "constructions", page, {"kind": kind, "reference": line.strip(), "thermal_performance": None}, excerpt=line.strip())


def add_lighting(result, page):
    values = {(int(qty), int(watts)) for watts, qty in re.findall(r"(\d+)\s*W[^\n]{0,180}?QTY\s*[:.]?\s*(\d+)", page["text"], re.I)}
    for qty, watts in values:
        record(result, "lighting", page, {"fixture_tag": "", "quantity": qty, "nominal_watts_each": watts, "connected_w": qty * watts, "level_name": page["level_name"]}, excerpt=f"{watts}W QTY: {qty}")


def add_equipment(result, page):
    text = page["text"]
    for kind, words in EQUIPMENT_WORDS.items():
        for word in words:
            if re.search(r"\b" + re.escape(word) + r"\b", text, re.I):
                record(result, "equipment", page, {"name": word, "kind": kind, "quantity": 1, "model": "", "watts": None, "duty": None, "level_name": page["level_name"]}, excerpt=word)


def proposed_links(result, pages):
    links, seen = [], set()
    families = ["spaces", "surfaces", "openings", "constructions", "lighting", "equipment"]
    for family in families:
        for item in result[family]:
            page = item["evidence"][0]["page"]
            level = item.get("level_name", "")
            related = [candidate for candidate in pages if candidate["page"] != page and (not level or candidate["level_name"] == level) and candidate["thermal_role"] != "not_calculation_evidence"]
            for candidate in related[:4]:
                key = (item["id"], candidate["page"])
                if key not in seen:
                    seen.add(key)
                    links.append({"id": f"link-{len(links) + 1}", "evidence_id": item["id"], "page": candidate["page"], "status": "inferred", "confidence": "medium", "reason": "Same detected level and thermal drawing role; confirm relationship."})
    return links


def exceptions(result, coverage):
    items = []
    if result["spaces"] and not result["surfaces"]:
        items.append(issue("surface_evidence_missing", "No adjacency or exposure evidence was extracted for the detected spaces."))
    if result["openings"] and not result["constructions"]:
        items.append(issue("opening_construction_missing", "Openings were found but no construction or glazing specification was extracted."))
    if not result["lighting"]:
        items.append(issue("lighting_unknown", "No lighting schedule with quantity and nominal wattage was extracted."))
    if result["equipment"]:
        items.append(issue("equipment_heat_unknown", "Equipment categories were found, but appliance heat-to-space values and duty remain unknown."))
    items.extend(coverage.get("coverage_exceptions", []))
    return items


def issue(item_id, question):
    return {"item_id": item_id, "status": "missing", "question": question}


def deduplicate(result):
    for family in ["spaces", "surfaces", "openings", "constructions", "lighting", "equipment"]:
        seen, unique = set(), []
        for item in result[family]:
            key = tuple(sorted((key, str(value)) for key, value in item.items() if key not in {"id", "evidence", "confidence"}))
            if key not in seen:
                seen.add(key)
                unique.append(item)
        result[family] = unique


def slug(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or "unassigned"
