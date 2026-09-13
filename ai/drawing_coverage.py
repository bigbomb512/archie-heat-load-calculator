"""Drawing-set register and conservative thermal-evidence coverage audit."""

from datetime import datetime, timezone
import hashlib
import json
import re


DATE_LIKE_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?$")
DRAWING_LABEL_RE = re.compile(
    r"(?:dwg\s*(?:no|number)?|drawing\s*(?:no|number)?|sheet\s*(?:no|number)?)"
    r"\s*[:#-]?\s*([A-Z]{0,4}[- ]?\d{1,4}[A-Z]?)\b",
    re.I,
)
TITLE_NUMBER_RE = re.compile(
    r"\b(?:dimension|floor\s+finish|reflected\s+ceiling|storefront|shopfront|"
    r"rendered?|internal|external|building)\s+(?:plan|elevation|section|image|view)"
    r"\s+([A-Z]{0,4}[- ]?\d{2,4}[A-Z]?)\b",
    re.I,
)
TITLE_SCALE_NUMBER_RE = re.compile(
    r"\b(?:dimension|floor\s+finish|reflect(?:ed|ive)\s+ceiling|service\s+plan|storefront|"
    r"shopfront|internal|external|building)\s+(?:plan|elevation|section|image|view)"
    r"[\s\S]{0,300}?\b([A-Z]{0,4}[- ]?\d{2,4}[A-Z]?)\s+SCALE(?:\s*\d)?\b",
    re.I,
)
SERVICE_SCALE_NUMBER_RE = re.compile(
    r"\bservice\s+plan(?:\s*[-/]\s*[A-Za-z]+)?[\s\S]{0,300}?"
    r"\b([A-Z]{0,4}[- ]?\d{2,4}[A-Z]?)\s+SCALE(?:\s*\d)?\b",
    re.I,
)


def source_fingerprint(ai_input):
    """Fingerprint the source packet, excluding derived coverage fields."""
    source = {
        "source_pdf": ai_input.get("source_pdf", ""),
        "drawing_set": ai_input.get("drawing_set", {}),
        "confirmed_pages": ai_input.get("confirmed_pages", {}),
        "page_triage": ai_input.get("page_triage", {}),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def evidence_fingerprint(ai_input, spatial_ocr=None, vector_geometry=None):
    """Fingerprint the derived evidence inputs used for classification."""
    return hashlib.sha256(json.dumps({
        "source": source_fingerprint(ai_input),
        "spatial_ocr": spatial_ocr or {},
        "vector_geometry": vector_geometry or {},
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def build_drawing_coverage(ai_input, spatial_ocr=None, vector_geometry=None):
    pages = enrich_page_levels(ai_input.get("drawing_set", {}).get("pages", []), ai_input)
    levels = build_levels(pages)
    exceptions = coverage_exceptions(levels, pages)
    page_roles = classify_page_roles(pages, ai_input, spatial_ocr or {}, vector_geometry or {})
    page_relationships = relate_pages(page_roles, pages)
    related_by_page = {}
    for relationship in page_relationships:
        related_by_page.setdefault(relationship["from_page"], []).append(relationship)
        related_by_page.setdefault(relationship["to_page"], []).append({
            **relationship, "from_page": relationship["to_page"], "to_page": relationship["from_page"]
        })
    for role in page_roles:
        role["related_pages"] = related_by_page.get(role.get("page"), [])
    return {
        "version": 4,
        "source_pdf": ai_input.get("source_pdf", ""),
        "source_fingerprint": source_fingerprint(ai_input),
        "evidence_fingerprint": evidence_fingerprint(ai_input, spatial_ocr, vector_geometry),
        "generated_from": "ai_input.json",
        "evidence_sources": {
            "spatial_ocr": bool(spatial_ocr),
            "vector_geometry": bool(vector_geometry),
            "structured_pdf_text": True,
        },
        "generated_at": timestamp(),
        # Keep a complete page register under the explicit ``pages`` key as
        # well as the legacy sheet_register name.  Consumers can therefore
        # distinguish an indexed packet from a roles-only derived artifact.
        "pages": [dict(page) for page in pages],
        "sheet_register": [sheet_entry(page) for page in pages],
        "page_roles": page_roles,
        "page_relationships": page_relationships,
        "levels": levels,
        "cross_sheet_links": cross_sheet_links(levels),
        "coverage_exceptions": exceptions,
        "status": "review_required" if exceptions else "coverage_ready_for_engineer_review",
    }


def enrich_page_levels(pages, ai_input):
    triage = {}
    raw = ai_input.get("page_triage", {})
    for item in raw.get("pages", []) if isinstance(raw, dict) else []:
        if isinstance(item, dict) and item.get("page") is not None:
            triage[item["page"]] = item
    enriched = []
    for page in pages:
        row = dict(page)
        triage_item = triage.get(page.get("page"), {})
        if not row.get("level_name") and triage_item.get("floor_label"):
            row["level_name"] = triage_item["floor_label"]
        enriched.append(row)
    return enriched


def sheet_entry(page):
    return {
        "page": page.get("page"),
        "title": page.get("title", ""),
        "drawing_number": page.get("drawing_number", ""),
        "sheet_classification": page.get("sheet_classification", page.get("detected_type", "other")),
        "thermal_role": page.get("thermal_role", "not_calculation_evidence"),
        "level_name": page.get("level_name", ""),
        "confidence": page.get("confidence", 0),
        "human_decision": page.get("confirmed_decision", ""),
        "classification_evidence": page.get("classification_evidence", ""),
        "capabilities": page.get("capabilities", []),
        "visual_available": page.get("visual_available", False),
        "text_available": page.get("text_available", False),
        "vector_available": page.get("vector_available", False),
        "page_group": page.get("page_group", ""),
        "source": {"page": page.get("page"), "kind": "reviewed_pdf_page"},
    }


def classify_page_roles(pages, ai_input, spatial_ocr=None, vector_geometry=None):
    """Produce conservative, proposal-only page roles from available evidence."""
    spatial_ocr = spatial_ocr or {}
    vector_geometry = vector_geometry or {}
    ocr_by_page = {row.get("page"): row for row in spatial_ocr.get("pages", [])}
    vector_by_page = {row.get("page"): row for row in (vector_geometry.get("geometry_key_points", {}) or {}).get("pages", [])}
    triage = {}
    raw_triage = ai_input.get("page_triage", {})
    for item in raw_triage.get("pages", []) if isinstance(raw_triage, dict) else []:
        if isinstance(item, dict) and item.get("page") is not None:
            triage[item["page"]] = item
    result = []
    for page in pages:
        ocr = ocr_by_page.get(page.get("page"), {})
        vector = vector_by_page.get(page.get("page"), {})
        identity = page_identity_candidates(page, ocr)
        structured_text = str(page.get("structured_content", {}).get("markdown", ""))
        title_block_text = " ".join(str(item.get("text_excerpt", "")) for item in ocr.get("title_blocks", []))
        ocr_text = " ".join(str(item.get("text", "")) for item in ocr.get("word_samples", []))
        text = " ".join(str(page.get(key, "")) for key in ("title", "drawing_number", "detected_type", "plan_role", "thermal_role"))
        # Use title-block/OCR text for identity and role detection, but keep
        # boilerplate notes out of capability scoring. Otherwise phone numbers,
        # dates, and generic wall/floor notes make unrelated sheets look like
        # calculation evidence.
        semantic_text = (
            str(page.get("title", "")) + " "
            + str(page.get("detected_type", "")) + " "
            + str(page.get("sheet_classification", "")) + " "
            + structured_text.split("General Notes:", 1)[0]
        ).lower()
        text = (text + " " + structured_text + " " + title_block_text + " " + ocr_text).lower()
        rooms = page.get("rooms", []) or []
        triage_item = triage.get(page.get("page"), {})
        role = triage_item.get("page_role") or page.get("plan_role")
        evidence = []
        confidence = float(page.get("confidence", 0) or 0)
        # Explicit sheet titles outrank stale visual-triage labels. A plan,
        # for example, can be initially kept as reference context when a
        # title block is flattened by PDF extraction.
        title = str(page.get("title", "")).strip().casefold()
        detected_type = str(page.get("detected_type", "")).casefold()
        # Explicit sheet titles and detected sheet types outrank inherited
        # reference_context labels.  The source packet's old triage often
        # flattened titles such as "Internal Elevation" into reference pages.
        if any(term in title for term in ("shopfront elevation", "storefront elevation", "window elevation", "door elevation")):
            role = "opening_elevation"
            evidence.append("explicit opening-elevation title")
        elif detected_type in {"render_or_photo", "perspective_or_3d"} or any(term in title for term in ("3d", "perspective", "render", "isometric")):
            role = "3d_render"
            evidence.append("render/perspective classification or title")
        elif ((title == "dimension plan" or page.get("drawing_number") == "202" and "dimension" in title)
              and detected_type not in {"cover_or_drawing_list", "render_or_photo"}
              and page.get("plan_role") != "reference_context"):
            role = "main_floor_plan"
            evidence.append("explicit dimension-plan title")
        elif any(term in title for term in ("reflective ceiling", "reflected ceiling", "rcp")):
            role = "reflected_ceiling_plan"
            evidence.append("explicit reflected-ceiling title")
        elif any(term in title for term in ("service plan", "lighting plan", "electrical plan", "hydraulic plan")):
            role = "services_or_lighting_plan"
            evidence.append("explicit service/lighting title")
        elif any(term in title for term in ("waiter station", "banquette", "skirting", "plan section", "infinity mirror")):
            role = "detail"
            evidence.append("explicit interior/detail title")
        elif any(term in title for term in ("elevation", "section")):
            role = "elevation_or_section"
            evidence.append("explicit elevation/section title")
        elif any(term in title for term in ("window schedule", "door schedule", "glazing schedule", "opening schedule")):
            role = "opening_schedule"
            evidence.append("explicit opening schedule title")
        elif detected_type in {"architect_lighting_plan", "architect_electrical_plan", "services_plan", "services_or_lighting_plan"}:
            role = "services_or_lighting_plan"
            evidence.append("structured sheet type identifies service/lighting plan")
        elif detected_type in {"material_or_finish_schedule", "equipment_schedule", "lighting_schedule", "opening_schedule"}:
            role = "reference"
            evidence.append("structured sheet type identifies schedule/detail context")
        elif (detected_type in {"elevation", "section"}
              and any(term in text for term in ("shopfront elevation", "storefront elevation", "window elevation", "door elevation"))):
            role = "opening_elevation"
            evidence.append("opening-elevation terminology in extracted page text")
        role_aliases = {
            "detail_plan": "supporting_geometry_plan",
            "uncertain_top_down_context": "supporting_geometry_plan",
            "reference_context": "reference",
            "visual_context": "reference",
            "hvac_or_rcp_legend": "reflected_ceiling_plan",
        }
        if role in role_aliases:
            role = role_aliases[role]
            evidence.append("normalised existing plan-role label")
        # Recover strong type/title evidence that was hidden behind a generic
        # reference_context proposal in the legacy packet.
        if role == "reference":
            if detected_type in {"elevation", "section"}:
                role = "elevation_or_section"
                evidence.append("detected elevation/section type overrides reference context")
            elif detected_type == "floor_plan":
                role = "supporting_geometry_plan"
                evidence.append("detected floor-plan type overrides reference context")
        if not role:
            if any(term in text for term in ("reflected ceiling", "rcp", "ceiling plan")):
                role, evidence = "reflected_ceiling_plan", ["title/role contains reflected-ceiling terminology"]
            elif any(term in text for term in ("lighting", "services", "electrical", "hydraulic")):
                role, evidence = "services_or_lighting_plan", ["title/role contains services or lighting terminology"]
            elif any(term in text for term in ("shopfront elevation", "storefront elevation", "window elevation", "door elevation")):
                role, evidence = "opening_elevation", ["title/role identifies an opening elevation"]
            elif any(term in text for term in ("elevation", "section")):
                role, evidence = "elevation_or_section", ["title/role contains elevation or section terminology"]
            elif any(term in text for term in ("detail", "schedule", "legend")):
                role, evidence = "detail", ["title/role contains detail or schedule terminology"]
            elif page.get("detected_type") == "floor_plan" or "plan" in text or rooms:
                role, evidence = "supporting_geometry_plan", ["page classified as plan or contains room records"]
            else:
                role, evidence = "reference", ["no calculation-page role could be established"]
        else:
            if not evidence:
                evidence = ["existing page-triage or plan-role proposal"]
        level = page.get("level_name") or triage_item.get("floor_label", "")
        ambiguous = not level and role in {"main_floor_plan", "supporting_geometry_plan", "reflected_ceiling_plan", "services_or_lighting_plan", "opening_elevation", "elevation_or_section"}
        if triage_item.get("disposition") == "exclude" or role == "exclude":
            authority = "excluded"
        elif ambiguous or role in {"supporting_geometry_plan", "reference"}:
            authority = "ambiguous" if ambiguous else "proposed"
        else:
            authority = "proposed"
        result.append({
            "page": page.get("page"), "drawing_number": page.get("drawing_number", ""),
            "title": page.get("title", ""), "proposed_role": role,
            "level_name": level, "confidence": confidence,
            "classification_evidence": evidence,
            "source_fingerprint": source_fingerprint({"page": page}),
            "authority_status": authority,
            "geometry_eligible": role in {"main_floor_plan", "supporting_geometry_plan"} and authority != "excluded",
            # An elevation cannot establish a room boundary, but a specifically
            # titled opening elevation can directly evidence an opening's
            # dimensions. Keep that capability separate from plan geometry.
            "opening_geometry_eligible": role == "opening_elevation" and authority != "excluded",
            "visual_crosscheck_eligible": role == "3d_render" and authority != "excluded",
            "reference_only": role in {"reference", "detail", "3d_render"},
            "capabilities": page_capabilities(role, text),
            "visual_available": bool(page.get("thumbnail_path") or page.get("image") or page.get("vision_triage") or page.get("rendered_image")),
            "text_available": bool(structured_text.strip() or page.get("written_dimensions") or page.get("ceiling_constraints") or page.get("hvac_terms")),
            "vector_available": role in {"main_floor_plan", "supporting_geometry_plan", "reflected_ceiling_plan", "services_or_lighting_plan", "opening_elevation", "elevation_or_section"},
            "page_group": page_group_key(role, level, page.get("drawing_number", "")),
            "review_required": authority in {"ambiguous", "proposed"},
            "identity": identity,
            "resolved_drawing_number": identity.get("selected_drawing_number", ""),
            "drawing_number_status": identity.get("status", "missing"),
            "capability_map": capability_map(role, semantic_text, page, ocr, vector),
            "relevance": page_relevance(role, semantic_text, page, ocr, vector),
            "selection": page_selection(role, authority, semantic_text, page, ocr, vector, identity),
            "selection_reasons": selection_reasons(role, semantic_text, page, ocr, vector, identity),
            "source": {"page": page.get("page"), "kind": "reviewed_pdf_page"},
        })
    return result


def _clean_drawing_candidate(value):
    value = re.sub(r"\s+", "", str(value or "").upper()).strip("-:#")
    if not value or DATE_LIKE_RE.match(value):
        return ""
    # A bare date fragment such as 2602 is not a sheet number unless a title
    # block or explicit drawing label supports it.
    if re.fullmatch(r"\d{4,8}", value) and value.startswith(("19", "20")):
        return ""
    return value


def page_identity_candidates(page, ocr=None):
    """Extract sheet identity without trusting the flattened legacy title.

    The physical PDF page remains the stable identity. Drawing-number and
    title candidates are evidence with provenance; conflicts are retained.
    """
    ocr = ocr or {}
    candidates = []
    title_candidates = []

    def add_number(raw, source, confidence, excerpt=""):
        value = _clean_drawing_candidate(raw)
        if value and not any(item["value"] == value for item in candidates):
            candidates.append({"value": value, "source": source, "confidence": confidence, "excerpt": excerpt[:240]})

    def add_title(raw, source, confidence):
        value = re.sub(r"\s+", " ", str(raw or "")).strip(" :-")
        if value and not any(item["value"].casefold() == value.casefold() for item in title_candidates):
            title_candidates.append({"value": value, "source": source, "confidence": confidence})

    structured = str((page.get("structured_content") or {}).get("markdown", ""))
    legacy = str(page.get("drawing_number", ""))
    for block in ocr.get("title_blocks", []):
        excerpt = str(block.get("text_excerpt", ""))
        for match in DRAWING_LABEL_RE.finditer(excerpt):
            add_number(match.group(1), "title_block_ocr", "high", excerpt)
        for match in TITLE_NUMBER_RE.finditer(excerpt):
            add_number(match.group(1), "title_block_ocr", "high", excerpt)
        for match in TITLE_SCALE_NUMBER_RE.finditer(excerpt):
            add_number(match.group(1), "title_block_ocr", "high", excerpt)
        for match in SERVICE_SCALE_NUMBER_RE.finditer(excerpt):
            add_number(match.group(1), "title_block_ocr", "high", excerpt)
    for text, source, confidence in ((structured, "structured_pdf_text", "high"), (str(page.get("title", "")), "page_title", "medium")):
        for match in DRAWING_LABEL_RE.finditer(text):
            add_number(match.group(1), source, confidence, text)
        for match in TITLE_NUMBER_RE.finditer(text):
            add_number(match.group(1), source, confidence, text)
        for match in TITLE_SCALE_NUMBER_RE.finditer(text):
            add_number(match.group(1), source, confidence, text)
        for match in SERVICE_SCALE_NUMBER_RE.finditer(text):
            add_number(match.group(1), source, confidence, text)
    if legacy:
        add_number(legacy, "legacy_page_metadata", "low", legacy)

    add_title(page.get("title", ""), "page_title", "medium")
    for block in ocr.get("title_blocks", []):
        excerpt = str(block.get("text_excerpt", ""))
        for match in re.finditer(r"\b(dimension plan|floor finish plan|reflected ceiling plan|storefront elevation|shopfront elevation|internal elevation|external elevation|building section|rendered image|3d perspective)\b", excerpt, re.I):
            add_title(match.group(1), "title_block_ocr", "high")

    # Prefer explicit title-block candidates. A unique explicit candidate is
    # confirmed; multiple explicit values remain ambiguous.
    high = [item for item in candidates if item["confidence"] == "high"]
    comparable = lambda value: re.sub(r"^[A-Z](?=\d)", "", str(value))
    values = {comparable(item["value"]) for item in high} or {comparable(item["value"]) for item in candidates}
    status = "missing" if not candidates else "proposed"
    selected = ""
    if len(values) == 1:
        matching = high or candidates
        selected = sorted(matching, key=lambda item: (len(item["value"]), item["value"]))[0]["value"]
        status = "confirmed" if high else "proposed"
    elif len(values) > 1:
        status = "ambiguous"
    return {
        "page": page.get("page"),
        "selected_drawing_number": selected,
        "drawing_number_candidates": candidates,
        "title_candidates": title_candidates,
        "status": status,
        "stable_page_identity": f"pdf-page-{page.get('page')}",
        "title_block_excerpts": [item.get("text_excerpt", "")[:240] for item in ocr.get("title_blocks", [])[:10]],
    }


def _capability(support, confidence, evidence, primary=False, reason=""):
    return {"support": support, "confidence": confidence, "evidence": sorted(set(evidence)),
            "primary_calculation_eligible": bool(primary), "reason": reason}


def capability_map(role, text, page, ocr=None, vector=None):
    ocr = ocr or {}
    vector = vector or {}
    lower = text.casefold()
    has_dims = bool(ocr.get("dimension_candidates") or vector.get("dimension_candidates"))
    has_lines = bool(vector.get("line_candidates") or vector.get("curve_candidates"))
    has_openings = bool(vector.get("openings")) or any(term in lower for term in ("window", "glazing", "storefront", "shopfront", "door", "opening"))
    result = {}
    if str(page.get("detected_type", "")).casefold() in {"cover_or_drawing_list", "cover", "drawing_list"}:
        return {"reference_context": _capability("cross_check", "high", ["page_register"], False, "Cover/drawing-list page is identity context, not calculation evidence.")}
    if role in {"main_floor_plan", "primary_geometry_plan"}:
        result["primary_room_geometry"] = _capability("direct", "high", ["sheet_role", "structured_text", "vector_geometry"], True, "Primary plan role with room/dimension geometry context.")
        result["room_labels"] = _capability("direct", "medium", ["sheet_role", "ocr"], True, "Plan labels can be spatially linked to room regions.")
        result["dimensions"] = _capability("direct" if has_dims else "supporting", "high" if has_dims else "medium", ["dimension_candidates" if has_dims else "sheet_role"], True, "Dimension evidence is available for plan binding." if has_dims else "Plan may contain dimensions but none were machine-linked.")
        if has_openings:
            result["opening_geometry"] = _capability("supporting", "medium", ["text", "vector_geometry"], False, "Plan opening context requires tag or geometry binding.")
    elif role == "supporting_geometry_plan":
        result["supporting_room_geometry"] = _capability("supporting", "high", ["sheet_role", "structured_text"], False, "Supporting plan can corroborate room and finish boundaries.")
        result["room_labels"] = _capability("supporting", "medium", ["ocr", "structured_text"], False, "Labels require a primary geometry witness.")
        if has_dims:
            result["dimensions"] = _capability("supporting", "medium", ["dimension_candidates"], False, "Dimensions are supporting until bound to a room.")
        if "insulat" in lower or "floor" in lower:
            result["thermal_boundary_context"] = _capability("supporting", "medium", ["structured_text"], False, "Finish or insulation notes provide boundary context only.")
    if role in {"reflected_ceiling_plan", "services_or_lighting_plan"}:
        result["ceiling_height"] = _capability("direct", "high" if re.search(r"\bCH\s*[:=]|ceiling|AFFL", lower) else "medium", ["sheet_role", "structured_text", "ocr"], False, "Ceiling annotations require room allocation.")
        result["ceiling_type"] = _capability("direct", "medium", ["structured_text", "ocr"], False, "Ceiling material/type evidence is non-geometric context.")
        result["lighting"] = _capability("direct", "high" if any(term in lower for term in ("lighting", "led", "luminaire", "fixture")) else "medium", ["sheet_role", "structured_text", "vector_geometry"], False, "Lighting/service evidence requires room allocation and load basis.")
        result["equipment"] = _capability("supporting", "medium", ["structured_text"], False, "Service pages can identify equipment but not heat-to-space load.")
    if role in {"opening_elevation", "opening_schedule"}:
        result["opening_geometry"] = _capability("direct", "high", ["sheet_role", "structured_text", "vector_geometry"], False, "Opening dimensions may be used only when uniquely linked.")
        result["opening_tags"] = _capability("direct", "high" if re.search(r"\bW\d|\bD\d|window|door|glazing", lower) else "medium", ["structured_text", "ocr"], False, "Opening tags require plan/elevation reconciliation.")
        result["vertical_levels"] = _capability("direct", "medium", ["structured_text", "vector_geometry"], False, "Elevation levels are vertical evidence only.")
    if role in {"elevation_or_section", "elevation", "section"}:
        result["vertical_levels"] = _capability("direct", "high", ["sheet_role", "structured_text", "vector_geometry"], False, "Sections/elevations support vertical relationships.")
        if has_openings:
            result["opening_geometry"] = _capability("supporting", "medium", ["structured_text", "vector_geometry"], False, "Opening evidence requires a compatible plan witness.")
        result["surface_relationships"] = _capability("supporting", "medium", ["sheet_role", "structured_text"], False, "Surface relationships require room/level binding.")
    if role == "3d_render":
        result["3d_cross_check"] = _capability("cross_check", "high", ["sheet_role", "page_image"], False, "Render is visual consistency evidence only.")
        result["opening_geometry"] = _capability("cross_check", "medium", ["page_image"], False, "Render can challenge opening presence but cannot provide scale.")
    if any(term in lower for term in ("occupancy", "operating hours", "outside air", "ventilation", "setpoint", "weekday", "saturday", "sunday", "holiday")):
        result["occupancy_or_schedule"] = _capability("direct", "medium", ["structured_text", "table"], False, "Explicit schedule values require room allocation and completeness.")
    if any(term in lower for term in ("material", "construction", "insulation", "wall", "roof", "floor", "u-value", "thermal")):
        result["construction_reference"] = _capability("supporting", "medium", ["structured_text", "table"], False, "Construction evidence does not itself activate U-values.")
    if any(term in lower for term in ("external wall", "shopfront", "storefront", "adjacent", "adjoining", "boundary", "mall")):
        result["thermal_boundary_context"] = _capability("supporting", "medium", ["structured_text", "sheet_role"], False, "Boundary method remains a separate reviewed input.")
    if not result:
        result["reference_context"] = _capability("cross_check", "low", ["page_register"], False, "No direct calculation capability was identified.")
    return result


def page_relevance(role, text, page, ocr=None, vector=None):
    capabilities = capability_map(role, text, page, ocr, vector)
    scores = {key: 0.0 for key in ("room_geometry", "openings_windows", "vertical_heights", "ceiling_lighting", "equipment", "occupancy_schedules", "construction_boundaries", "3d_cross_check")}
    mapping = {
        "primary_room_geometry": "room_geometry", "supporting_room_geometry": "room_geometry", "room_labels": "room_geometry", "dimensions": "room_geometry",
        "opening_geometry": "openings_windows", "opening_tags": "openings_windows", "vertical_levels": "vertical_heights", "surface_relationships": "construction_boundaries",
        "ceiling_height": "ceiling_lighting", "ceiling_type": "ceiling_lighting", "lighting": "ceiling_lighting", "equipment": "equipment",
        "occupancy_or_schedule": "occupancy_schedules", "construction_reference": "construction_boundaries", "thermal_boundary_context": "construction_boundaries", "3d_cross_check": "3d_cross_check",
    }
    base = {"direct": 0.85, "supporting": 0.45, "cross_check": 0.55}
    for capability, details in capabilities.items():
        target = mapping.get(capability)
        if target:
            scores[target] = max(scores[target], base.get(details["support"], 0.25))
    # Do not let title-block phone numbers or unassigned OCR numerics inflate
    # relevance. Role evidence already establishes the baseline; vector/OCR
    # candidates only provide a small boost when they are semantically useful.
    usable_ocr_dims = [item for item in (ocr or {}).get("dimension_candidates", [])
                       if item.get("annotation_kind") not in {"unknown_numeric", "phone", "date"}
                       and item.get("dimension_eligibility") not in {"excluded", "title_block"}]
    if usable_ocr_dims and role in {"main_floor_plan", "supporting_geometry_plan", "opening_elevation", "elevation_or_section"}:
        scores["room_geometry"] = min(1.0, scores["room_geometry"] + 0.05)
    if vector and vector.get("openings"):
        scores["openings_windows"] = min(1.0, scores["openings_windows"] + 0.05)
    return {key: round(value, 2) for key, value in scores.items()}


def page_selection(role, authority, text, page, ocr=None, vector=None, identity=None):
    relevance = page_relevance(role, text, page, ocr, vector)
    highest = max(relevance.values(), default=0.0)
    if role == "3d_render":
        return "cross_check_context" if highest >= 0.45 else "ranked_exception"
    if (identity or {}).get("status") == "ambiguous":
        return "ranked_exception"
    if highest >= 0.70:
        return "primary_context"
    if highest >= 0.30:
        return "supporting_context"
    return "reference_only"


def selection_reasons(role, text, page, ocr=None, vector=None, identity=None):
    lower = text.casefold()
    reasons = []
    if identity and identity.get("status") == "ambiguous":
        reasons.append("conflicting page identity candidates")
    if role in {"main_floor_plan", "primary_geometry_plan"}:
        reasons.append("plan role with room/dimension context")
    if role in {"reflected_ceiling_plan", "services_or_lighting_plan"}:
        reasons.append("ceiling/service/lighting evidence")
    if role in {"opening_elevation", "opening_schedule"}:
        reasons.append("opening/elevation evidence")
    if role in {"elevation_or_section", "elevation", "section"}:
        reasons.append("vertical and surface evidence")
    if role == "3d_render":
        reasons.append("visual cross-check evidence only")
    if ocr and ocr.get("dimension_candidates"):
        reasons.append("OCR dimension candidates")
    if vector and (vector.get("dimension_candidates") or vector.get("openings")):
        reasons.append("vector dimension/opening evidence")
    if any(term in lower for term in ("schedule", "occupancy", "outside air", "lighting", "insulation", "glazing")):
        reasons.append("explicit calculation-related text")
    return list(dict.fromkeys(reasons)) or ["page retained for complete discovery register"]


def _shared_room_tokens(page):
    tokens = set()
    for room in page.get("rooms", []) or []:
        tokens.update(re.findall(r"[a-z0-9]+", str(room.get("name", "")).casefold()))
    text = str(page.get("title", "")) + " " + str((page.get("structured_content") or {}).get("markdown", ""))
    for term in ("cool room", "freezer room", "kitchen", "bar", "shopfront", "storefront"):
        if term in text.casefold():
            tokens.add(term)
    return {token for token in tokens if len(token) > 2}


def relate_pages(page_roles, pages):
    page_by_number = {page.get("page"): page for page in pages}
    relationships = []
    for index, left in enumerate(page_roles):
        left_page = page_by_number.get(left.get("page"), {})
        left_tokens = _shared_room_tokens(left_page)
        for right in page_roles[index + 1:]:
            right_page = page_by_number.get(right.get("page"), {})
            right_tokens = _shared_room_tokens(right_page)
            basis = []
            if left_tokens and right_tokens and left_tokens & right_tokens:
                basis.append("shared room/surface terminology")
            left_identity = left.get("identity", {})
            right_identity = right.get("identity", {})
            left_numbers = {item.get("value") for item in left_identity.get("drawing_number_candidates", []) if item.get("confidence") == "high"}
            right_numbers = {item.get("value") for item in right_identity.get("drawing_number_candidates", []) if item.get("confidence") == "high"}
            if left_numbers & right_numbers:
                basis.append("shared explicit drawing reference")
            compatible = {"main_floor_plan", "primary_geometry_plan", "supporting_geometry_plan", "reflected_ceiling_plan", "services_or_lighting_plan", "opening_elevation", "elevation_or_section", "3d_render"}
            if left.get("proposed_role") in compatible and right.get("proposed_role") in compatible and (left.get("level_name") or right.get("level_name") or left.get("page") in {20, 21, 22, 26}):
                if {left.get("proposed_role"), right.get("proposed_role")} != {"3d_render"}:
                    basis.append("compatible architect evidence roles")
            if not basis:
                continue
            confidence = "high" if len(basis) >= 2 else "medium"
            relationships.append({"from_page": left.get("page"), "to_page": right.get("page"), "basis": basis, "confidence": confidence})
    return relationships


def page_group_key(role, level, drawing_number=""):
    """Stable grouping key for cross-page evidence, independent of array order."""
    family = {
        "main_floor_plan": "plan",
        "supporting_geometry_plan": "plan_support",
        "reflected_ceiling_plan": "ceiling_service",
        "services_or_lighting_plan": "ceiling_service",
        "opening_elevation": "openings",
        "opening_schedule": "openings",
        "elevation_or_section": "vertical",
        "3d_render": "3d_crosscheck",
        "detail": "details",
        "reference": "reference",
    }.get(role, role or "unclassified")
    return "|".join((str(drawing_number or "unknown"), str(level or "unassigned"), family))


def page_capabilities(role, text=""):
    """Describe what a sheet can prove without treating every page as geometry."""
    capabilities = {
        "main_floor_plan": ["primary_room_geometry", "room_labels", "dimensions", "openings", "surface_relationships"],
        "supporting_geometry_plan": ["supporting_geometry", "room_labels", "fitout_boundaries", "dimensions", "openings"],
        "reflected_ceiling_plan": ["ceiling_height", "ceiling_type", "lighting_context", "service_constraints"],
        "services_or_lighting_plan": ["lighting_context", "service_constraints", "ceiling_height"],
        "opening_elevation": ["opening_geometry", "vertical_levels", "surface_relationships", "opening_tags"],
        "opening_schedule": ["opening_tags", "opening_dimensions", "construction_references", "glazing_references"],
        "elevation_or_section": ["vertical_geometry", "ceiling_height", "surface_relationships", "opening_context"],
        "3d_render": ["visual_crosscheck", "opening_presence", "level_relationships", "conflict_detection"],
        "detail": ["construction_references", "fixture_context", "opening_context"],
        "reference": ["context_only"],
    }.get(role, ["context_only"])
    lower = text.casefold()
    if "window" in lower or "glazing" in lower:
        capabilities.append("opening_context")
    if "lighting" in lower or "led" in lower:
        capabilities.append("lighting_context")
    return sorted(set(capabilities))


def build_levels(pages):
    grouped = {}
    for page in pages:
        label = page.get("level_name") or "Unassigned level"
        grouped.setdefault(label, []).append(page)
    return [level_entry(label, grouped[label]) for label in sorted(grouped, key=level_sort_key)]


def level_entry(label, pages):
    purpose, status, evidence = infer_floor_purpose(pages)
    roles = {}
    for page in pages:
        roles.setdefault(page.get("thermal_role", "not_calculation_evidence"), []).append(page.get("page"))
    rooms = []
    for page in pages:
        rooms.extend(page.get("rooms", []))
    return {
        "level_name": label,
        "proposed_purpose": purpose,
        "purpose_status": status,
        "purpose_evidence": evidence,
        "conditioned_status": "unknown",
        "conditioned_status_reason": "Engineer confirmation required; drawings alone do not prove operating conditions.",
        "spaces": unique_rooms(rooms),
        "page_numbers": [page.get("page") for page in pages],
        "pages_by_thermal_role": roles,
    }


def infer_floor_purpose(pages):
    text = " ".join(
        " ".join([page.get("title", ""), page.get("level_name", ""), " ".join(room.get("name", "") for room in page.get("rooms", []))])
        for page in pages
    ).lower()
    candidates = [
        ("food retail / food preparation", ["shop", "retail", "kitchen", "food", "cafe"]),
        ("office", ["office", "workstation", "meeting"]),
        ("residential dwelling", ["bedroom", "living", "dwelling"]),
        ("car parking / garage", ["car park", "parking", "garage"]),
        ("plant / services", ["plant", "mechanical", "services"]),
    ]
    for purpose, words in candidates:
        if any(word in text for word in words):
            page = next((item for item in pages if any(word in (item.get("title", "") + " " + " ".join(room.get("name", "") for room in item.get("rooms", []))).lower() for word in words)), pages[0])
            return purpose, "inferred", [{"page": page.get("page"), "kind": "reviewed_pdf_text", "excerpt": page.get("title", "")}]
    return "", "missing", []


def unique_rooms(rooms):
    result, seen = [], set()
    for room in rooms:
        key = (room.get("name", ""), room.get("area", ""))
        if key not in seen:
            seen.add(key)
            result.append(room)
    return result


def cross_sheet_links(levels):
    links = []
    for level in levels:
        for role, pages in level["pages_by_thermal_role"].items():
            if role != "not_calculation_evidence":
                links.append({"level_name": level["level_name"], "thermal_role": role, "pages": pages})
    return links


def coverage_exceptions(levels, pages):
    exceptions = []
    if not pages:
        exceptions.append(issue("project", "drawing_pages_missing", "The source packet contains no drawing pages for coverage analysis."))
        return exceptions
    all_roles = {page.get("thermal_role") for page in pages}
    page_roles = classify_page_roles(pages, {"page_triage": {"pages": []}})
    if not any(item["proposed_role"] in {"main_floor_plan", "supporting_geometry_plan"} for item in page_roles):
        exceptions.append(issue("project", "geometry_page_missing", "No plan page was identified as a geometry source; room topology remains blocked."))
    for level in levels:
        roles = set(level["pages_by_thermal_role"])
        if "primary_geometry" in roles and "surface_confirmation" not in roles:
            exceptions.append(issue(level["level_name"], "surface_views_missing", "A plan is present but no elevation, section, or RCP is linked to confirm surfaces and exposure."))
        if "primary_geometry" in roles and not level["proposed_purpose"]:
            exceptions.append(issue(level["level_name"], "floor_purpose_missing", "No apparent floor purpose was found; confirm its use before load assumptions are supplied."))
        if level["level_name"] == "Unassigned level":
            exceptions.append(issue(level["level_name"], "floor_identity_missing", "Plan evidence exists but no source-backed floor identity is available."))
    if "primary_geometry" in all_roles and "site_orientation_or_shading" not in all_roles:
        exceptions.append(issue("project", "site_context_missing", "No site/orientation plan was identified; do not infer solar orientation or surrounding shading."))
    return exceptions


def issue(level_name, item_id, question):
    return {"item_id": item_id + "-" + level_name.lower().replace(" ", "_"), "level_name": level_name, "status": "missing", "question": question}


def level_sort_key(label):
    value = label.lower()
    if "basement" in value:
        return -1
    if "ground" in value or "main" in value:
        return 0
    if "roof" in value:
        return 999
    return 100
