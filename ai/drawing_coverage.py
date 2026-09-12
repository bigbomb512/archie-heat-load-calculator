"""Drawing-set register and conservative thermal-evidence coverage audit."""

from datetime import datetime, timezone
import hashlib
import json
import re


def source_fingerprint(ai_input):
    """Fingerprint the source packet, excluding derived coverage fields."""
    source = {
        "source_pdf": ai_input.get("source_pdf", ""),
        "drawing_set": ai_input.get("drawing_set", {}),
        "confirmed_pages": ai_input.get("confirmed_pages", {}),
        "page_triage": ai_input.get("page_triage", {}),
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def build_drawing_coverage(ai_input):
    pages = enrich_page_levels(ai_input.get("drawing_set", {}).get("pages", []), ai_input)
    levels = build_levels(pages)
    exceptions = coverage_exceptions(levels, pages)
    page_roles = classify_page_roles(pages, ai_input)
    return {
        "version": 3,
        "source_pdf": ai_input.get("source_pdf", ""),
        "source_fingerprint": source_fingerprint(ai_input),
        "generated_from": "ai_input.json",
        "generated_at": timestamp(),
        # Keep a complete page register under the explicit ``pages`` key as
        # well as the legacy sheet_register name.  Consumers can therefore
        # distinguish an indexed packet from a roles-only derived artifact.
        "pages": [dict(page) for page in pages],
        "sheet_register": [sheet_entry(page) for page in pages],
        "page_roles": page_roles,
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


def classify_page_roles(pages, ai_input):
    """Produce conservative, proposal-only page roles from available evidence."""
    triage = {}
    raw_triage = ai_input.get("page_triage", {})
    for item in raw_triage.get("pages", []) if isinstance(raw_triage, dict) else []:
        if isinstance(item, dict) and item.get("page") is not None:
            triage[item["page"]] = item
    result = []
    for page in pages:
        structured_text = str(page.get("structured_content", {}).get("markdown", ""))
        text = " ".join(str(page.get(key, "")) for key in ("title", "drawing_number", "detected_type", "plan_role", "thermal_role"))
        text = (text + " " + structured_text).lower()
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
        elif any(term in title for term in ("elevation", "section")):
            role = "elevation_or_section"
            evidence.append("explicit elevation/section title")
        elif any(term in title for term in ("window schedule", "door schedule", "glazing schedule", "opening schedule")):
            role = "opening_schedule"
            evidence.append("explicit opening schedule title")
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
            "source": {"page": page.get("page"), "kind": "reviewed_pdf_page"},
        })
    return result


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
