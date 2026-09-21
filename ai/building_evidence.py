"""Reusable, cited building evidence extracted from a reviewed drawing set."""

import re

from ai.drawing_coverage import source_fingerprint, timestamp


EQUIPMENT_WORDS = {
    "refrigeration": ["fridge", "freezer", "display fridge", "cool room"],
    "cooking": ["oven", "cooktop", "range", "grill", "fryer", "boiler"],
    "appliance": ["ice maker", "dishwasher", "microwave", "coffee machine"],
}


OPENING_TAG = re.compile(r"\b([WD]\d{1,3}[A-Z]?)\b", re.I)
DIMENSION_PAIR = re.compile(
    r"\b(?P<width>\d{3,5}(?:\.\d+)?)\s*(?:mm)?\s*(?:x|×|by)\s*"
    r"(?P<height>\d{3,5}(?:\.\d+)?)(?:\s*(?P<unit>mm))?\b",
    re.I,
)
OPENING_PARENT = re.compile(
    r"\b(?P<label>shopfront|storefront|frontage)\b[^\n\r0-9]{0,80}?"
    r"(?P<width>\d{3,5}(?:\.\d+)?)\s*mm\b",
    re.I,
)
OPENING_CONTEXT = ("window", "glazing", "glass", "door", "roller shutter", "opening", "frame", "storefront", "shopfront")
NON_OPENING_CONTEXT = ("tile", "signage", "sign", "menu board", "panel", "grout", "skirting")


def build_building_evidence(ai_input, drawing_coverage=None, spatial_ocr=None, vision_response=None):
    pages = source_pages(ai_input, spatial_ocr or {}, drawing_coverage or {})
    result = empty_evidence(ai_input)
    result["source_fingerprint"] = source_fingerprint(ai_input)
    result["generated_from"] = "ai_input.json"
    result["generated_at"] = timestamp()
    for page in pages:
        add_spaces(result, page)
        add_surfaces(result, page)
        add_openings(result, page)
        add_constructions(result, page)
        add_lighting(result, page)
        add_equipment(result, page)
    add_levels(result, drawing_coverage or {})
    add_vision_entities(result, vision_response or {}, pages)
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
        "source_fingerprint": source_fingerprint(ai_input),
        "generated_from": "ai_input.json",
        "generated_at": timestamp(),
        "spaces": [], "levels": [], "surfaces": [], "openings": [], "constructions": [],
        "lighting": [], "equipment": [], "cross_sheet_links": [], "exceptions": [], "sources": {},
    }


def source_pages(ai_input, spatial_ocr, drawing_coverage=None):
    ocr_text = {}
    for page in spatial_ocr.get("pages", []):
        excerpts = [item.get("text_excerpt", "") for item in page.get("title_blocks", [])]
        excerpts.extend(item.get("text", "") for item in page.get("word_samples", []) if item.get("text"))
        ocr_text[page.get("page")] = "\n".join(excerpts)
    pages = []
    drawing_pages = ai_input.get("drawing_set", {}).get("pages", [])
    if not drawing_pages:
        drawing_pages = ai_input.get("confirmed_pages", {}).get("floor_plans", []) + ai_input.get("confirmed_pages", {}).get("reference_pages", [])
    for page in drawing_pages:
        coverage_role = next((item for item in (drawing_coverage or {}).get("page_roles", [])
                              if item.get("page") == page.get("page")), {})
        pages.append({
            "page": page.get("page"), "drawing_number": page.get("drawing_number", ""), "level_name": page.get("level_name", ""),
            "classification": page.get("sheet_classification", page.get("detected_type", "other")),
            "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
            "title": page.get("title", ""), "rooms": page.get("rooms", []),
            "proposed_role": coverage_role.get("proposed_role", ""),
            "authority_status": coverage_role.get("authority_status", ""),
            "reference_only": coverage_role.get("proposed_role") in {"reference", "detail"},
            "opening_geometry_eligible": bool(coverage_role.get("opening_geometry_eligible")),
            "page_role": coverage_role.get("proposed_role", ""),
            "capabilities": coverage_role.get("capabilities", []),
            "page_group": coverage_role.get("page_group", ""),
            "visual_available": coverage_role.get("visual_available", False),
            "text_available": coverage_role.get("text_available", bool(page.get("structured_content", {}).get("markdown"))),
            "vector_available": coverage_role.get("vector_available", False),
            "room_labels": next((item.get("room_label_candidates", []) for item in spatial_ocr.get("pages", [])
                                  if item.get("page") == page.get("page")), []),
            "text": page.get("structured_content", {}).get("markdown", "") + "\n" + ocr_text.get(page.get("page"), ""),
        })
    return pages


def source(page, excerpt):
    return {"page": page["page"], "kind": "reviewed_pdf_text", "sheet_classification": page["classification"],
            "drawing_number": page.get("drawing_number", ""), "excerpt": excerpt}


def record(result, family, page, value, status="direct", **extra):
    entries = result[family]
    item = {"id": f"{family}-{page['page']}-{len(entries) + 1}", "status": status,
            "confidence": "high" if status == "direct" else "medium",
            "extraction_method": extra.pop("extraction_method", "structured_pdf"),
            "evidence": [source(page, extra.pop("excerpt", page["title"]))]}
    item.update(value)
    item.update(extra)
    entries.append(item)


def add_spaces(result, page):
    for room in page["rooms"]:
        label = room.get("name", "").strip()
        if label:
            geometry = room.get("geometry") or room.get("polygon") or room.get("boundary_reference")
            record(result, "spaces", page, {"name": label, "area": room.get("area", ""), "level_name": page["level_name"],
                    "geometry_status": "geometry_confirmed" if geometry else "geometry_review_required",
                    "geometry_reference": geometry, "unresolved_fields": [] if geometry and room.get("area") else ["geometry" if not geometry else "area"]},
                    excerpt=label + (" · " + str(room["area"]) if room.get("area") else ""), extraction_method="source_room_record")
    for match in re.finditer(r"(?:\b([A-Za-z][A-Za-z0-9 .-]{1,40})\s+)?AREA\s*[:.]?\s*(\d+(?:\.\d+)?)\s*(m²|m2)\b", page["text"], re.I):
        label = (match.group(1) or page["title"] or "Proposed space").strip()
        if any(item.get("name", "").casefold() == label.casefold()
               and str(item.get("area", "")).replace("m²", "").replace("m2", "").strip() == match.group(2)
               for item in result["spaces"] if item.get("level_name") == page["level_name"]):
            continue
        record(result, "spaces", page, {"name": label, "area": match.group(2) + " " + match.group(3), "level_name": page["level_name"],
                "geometry_status": "geometry_review_required", "geometry_reference": None, "unresolved_fields": ["geometry"]},
                excerpt=match.group(0), extraction_method="explicit_area_text")
    known_room_terms = ("shop", "kitchen", "bar", "dining", "cool room", "freezer", "storage", "toilet", "office", "staff", "entry", "service", "room")
    non_room_terms = ("legend", "symbol", "tile", "tiles", "grout", "joint", "colour", "color", "coated", "concealed", "services", "floor", "location")
    existing = {item.get("name", "").casefold() for item in result["spaces"] if item.get("level_name") == page["level_name"]}
    # Reflected-ceiling and service/electrical sheets are useful supporting
    # evidence for ceilings, lighting and equipment, but their legends and
    # notes routinely contain room-like phrases.  Do not promote those OCR
    # fragments to room identities; room topology must come from geometry
    # plans or an explicit source-room record.
    if (page.get("reference_only")
            or page.get("proposed_role") in {"reference", "detail", "services_or_lighting_plan",
                                              "reflected_ceiling_plan", "architect_lighting_plan",
                                              "architect_electrical_plan", "opening_elevation",
                                              "opening_schedule", "elevation_or_section", "3d_render"}
            or page.get("classification") in {"reflected_ceiling_plan", "architect_lighting_plan",
                                                "architect_electrical_plan"}):
        return
    for candidate in page.get("room_labels", []):
        label = re.sub(r"\s+", " ", str(candidate.get("text", ""))).strip(" .:-")
        if (candidate.get("status") not in {"possible_room_or_area_label", "room_label"}
                or len(label) < 3 or len(label) > 45 or not any(term in label.casefold() for term in known_room_terms)
                or any(term in label.casefold() for term in non_room_terms)
                or label.casefold() in existing):
            continue
        record(result, "spaces", page, {"name": label, "area": "", "level_name": page["level_name"], "status": "inferred",
                "geometry_status": "label_detected", "geometry_reference": None,
                "unresolved_fields": ["floor", "geometry", "area"]}, status="inferred", excerpt=label,
               extraction_method="spatial_ocr_room_label")
        existing.add(label.casefold())


def add_levels(result, coverage):
    for level in coverage.get("levels", []):
        status = level.get("purpose_status", "missing")
        result["levels"].append({
            "id": "level-" + slug(level.get("level_name", "unassigned")), "name": level.get("level_name", ""),
            "proposed_purpose": level.get("proposed_purpose", ""), "status": status,
            "conditioned_status": level.get("conditioned_status", "unknown"),
            "evidence": level.get("purpose_evidence", []),
        })


def add_vision_entities(result, vision_response, pages):
    """Merge validated automated geometry without pretending it is PDF text.

    Only the job validator can set ``auto_activate``.  One-witness output is
    still valuable evidence, but stays proposal-only in the downstream fact
    registry and calculator draft.
    """
    extraction = vision_response.get("result", {}).get("auto_extraction", {})
    page_by_number = {page.get("page"): page for page in pages}
    for row in extraction.get("entities", []):
        page = page_by_number.get(row.get("page"))
        if not page:
            continue
        active = bool(row.get("auto_activate"))
        status = "direct" if active else "inferred"
        common = {
            "extraction_method": "openai_vision_extraction",
            "excerpt": row.get("excerpt", ""),
            "ai_verified": active,
            "activation_basis": "two_independent_architect_witnesses" if active else "",
            "vision_witnesses": row.get("witnesses", []),
            "candidate_fingerprint": row.get("candidate_fingerprint", ""),
            "source_type": "vision_extraction",
            "unresolved_fields": row.get("unresolved_fields", []),
        }
        kind = row.get("kind")
        if kind == "floor":
            result["levels"].append({
                "id": "vision-level-" + row.get("candidate_fingerprint", "")[:16],
                "name": row.get("level_name") or row.get("label"), "status": status,
                "conditioned_status": "unknown", "ai_verified": active,
                "activation_basis": common["activation_basis"], "vision_witnesses": common["vision_witnesses"],
                "candidate_fingerprint": common["candidate_fingerprint"], "source_type": "vision_extraction",
                "evidence": [source(page, row.get("excerpt", ""))],
            })
        elif kind == "room":
            area = (str(row["area_m2"]) + " m²") if row.get("area_m2") else ""
            record(result, "spaces", page, {
                "name": row.get("label"), "area": area, "level_name": row.get("level_name", ""),
                "geometry_status": row.get("geometry_status", "geometry_proposed"),
                "geometry_reference": row.get("witnesses", []),
                "ceiling_height_mm": row.get("ceiling_height_mm"),
            }, status=status, **common)
        elif kind == "opening":
            dimensions = {"width_mm": row.get("width_mm"), "height_mm": row.get("height_mm"), "unit": row.get("unit", "")}
            record(result, "openings", page, {
                "tag": row.get("opening_tag") or row.get("label"), "kind": "window_or_glazing",
                "level_name": row.get("level_name", ""), "dimensions": dimensions,
                "performance": None, "geometry_status": row.get("geometry_status", "geometry_proposed"),
                "geometry": {"vision_witnesses": row.get("witnesses", []), "direct_dimension": active,
                             "unique_target": active, "auto_activation_basis": common["activation_basis"]},
            }, status=status, **common)
        elif kind == "surface":
            record(result, "surfaces", page, {
                "kind": row.get("surface_kind") or "wall", "surface_label": row.get("label"),
                "adjacency": row.get("boundary_reference", ""), "orientation": row.get("orientation", ""),
                "level_name": row.get("level_name", ""), "geometry": {"vision_witnesses": row.get("witnesses", [])},
                "geometry_status": row.get("geometry_status", "geometry_proposed"),
            }, status=status, **common)
    layered = vision_response.get("result", {}).get("layered_geometry", {})
    for layered_page in layered.get("pages", []) if isinstance(layered, dict) else []:
        page = page_by_number.get(layered_page.get("page"))
        if not page:
            continue
        for row in layered_page.get("thermal_surface_candidates", []) or []:
            if not isinstance(row, dict):
                continue
            record(result, "surfaces", page, {
                "surface_id": row.get("surface_id", ""),
                "kind": row.get("physical_type", "unresolved"),
                "surface_label": row.get("label", ""),
                "thermal_role": row.get("thermal_role", "unresolved"),
                "boundary_condition": row.get("boundary_condition", "unresolved"),
                "owner_room_id": row.get("owner_room_id", ""),
                "owner_zone_id": row.get("owner_zone_id", ""),
                "adjacent_room_id": row.get("adjacent_room_id", ""),
                "adjacent_space_id": row.get("adjacent_space_id", ""),
                "opening_ids": row.get("opening_ids", []),
                "geometry": {"boundary_points_px": row.get("boundary_points_px", []), "wall_ids": row.get("wall_ids", [])},
                "geometry_status": "geometry_proposed",
                "classification_confidence": row.get("confidence", "low"),
                "classification_assumptions": row.get("assumptions", []),
                "classification_conflicts": row.get("conflicts", []),
                "evidence_refs": row.get("evidence_refs", []),
            }, status="inferred", extraction_method="ai_thermal_surface_classification",
               excerpt=row.get("source_crop", ""), ai_verified=False,
               unresolved_fields=row.get("unresolved_fields", []), vision_witnesses=row.get("evidence_refs", []),
               source_type="vision_extraction", candidate_fingerprint=row.get("surface_id", ""))
    result.setdefault("vision_conflicts", []).extend(extraction.get("conflicts", []))
    result.setdefault("vision_missing_evidence", []).extend(extraction.get("missing_evidence", []))


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
    if not page.get("opening_geometry_eligible"):
        return
    title = str(page.get("title", "")).casefold()
    if any(label in title for label in ("shopfront", "storefront")):
        label = "shopfront" if "shopfront" in title else "storefront"
        record(result, "surfaces", page, {
            "kind": "opening_parent_surface", "surface_label": label,
            "adjacency": "", "level_name": page["level_name"], "geometry": None,
            "geometry_status": "geometry_review_required",
        }, excerpt=page["title"], extraction_method="opening_elevation_title")
    for match in OPENING_PARENT.finditer(text):
        label = match.group("label").casefold()
        width = float(match.group("width"))
        record(result, "surfaces", page, {
            "kind": "opening_parent_surface", "surface_label": label,
            "adjacency": "", "level_name": page["level_name"],
            "geometry": {
                "width_mm": width, "axis": "horizontal", "unit": "mm",
                "direct_dimension": True, "unique_target": False,
                "geometry_witness": "explicit_named_parent_dimension",
            },
            "geometry_status": "geometry_proposed",
        }, excerpt=match.group(0), extraction_method="opening_elevation_dimension")


def add_openings(result, page):
    text = page["text"]
    for match in OPENING_TAG.finditer(text):
        tag = match.group(1).upper()
        kind = "window_or_glazing" if tag.startswith("W") else "door"
        record(result, "openings", page, {"tag": tag, "kind": kind, "level_name": page["level_name"], "dimensions": None, "performance": None}, excerpt=tag)
    if page.get("opening_geometry_eligible"):
        add_opening_dimensions(result, page)
    for word, kind in [("glazing", "window_or_glazing"), ("window", "window_or_glazing"), ("door", "door")]:
        if re.search(r"\b" + word + r"\b", text, re.I):
            record(result, "openings", page, {"tag": "", "kind": kind, "level_name": page["level_name"], "dimensions": None, "performance": None}, excerpt=word)


def add_opening_dimensions(result, page):
    """Store explicitly printed elevation dimensions without guessing targets."""
    text = page["text"]
    seen, chain_values = set(), []
    for pair in DIMENSION_PAIR.finditer(text):
        excerpt = pair.group(0)
        before = text[max(0, pair.start() - 100):pair.start()]
        context = text[max(0, pair.start() - 100):pair.end() + 100].casefold()
        tag_match = list(OPENING_TAG.finditer(before))
        tag = tag_match[-1].group(1).upper() if tag_match else ""
        if not tag and (not any(word in context for word in OPENING_CONTEXT) or any(word in context for word in NON_OPENING_CONTEXT)):
            continue
        key = (tag, pair.group("width"), pair.group("height"))
        if key in seen:
            continue
        seen.add(key)
        chain_values.extend([float(pair.group("width")), float(pair.group("height"))])
        kind = "window_or_glazing" if tag.startswith("W") else "door" if tag.startswith("D") else "unresolved_opening_geometry"
        unit = (pair.group("unit") or "").lower()
        dimensions = ({"width_mm": float(pair.group("width")), "height_mm": float(pair.group("height")), "unit": "mm"}
                      if unit == "mm" else
                      {"width": float(pair.group("width")), "height": float(pair.group("height")), "unit": ""})
        record(result, "openings", page, {
            "tag": tag, "kind": kind, "level_name": page["level_name"], "performance": None,
            "dimensions": dimensions,
            "geometry": {
                "direct_dimension": True, "unique_target": False,
                "unit_status": "explicit" if unit else "missing",
                "geometry_witness": "explicit_elevation_dimension_pair",
                "source_page_role": page.get("page_role", "opening_elevation"),
            },
            "geometry_status": "geometry_proposed",
        }, excerpt=excerpt, extraction_method="opening_elevation_dimension")
    if len(chain_values) >= 2:
        record(result, "openings", page, {
            "tag": "", "kind": "unresolved_opening_geometry", "level_name": page["level_name"], "performance": None,
            "dimensions": None,
            "geometry": {
                "dimension_chain": chain_values, "unit": "", "axis": "unspecified",
                "direct_dimension": True, "unique_target": False,
                "geometry_witness": "explicit_elevation_dimension_chain",
                "source_page_role": page.get("page_role", "opening_elevation"),
            },
            "geometry_status": "geometry_review_required",
        }, excerpt=" · ".join(str(value) for value in chain_values), extraction_method="opening_elevation_dimension_chain")


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
