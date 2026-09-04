"""Drawing-set register and conservative thermal-evidence coverage audit."""


def build_drawing_coverage(ai_input):
    pages = ai_input.get("drawing_set", {}).get("pages", [])
    levels = build_levels(pages)
    exceptions = coverage_exceptions(levels, pages)
    return {
        "version": 1,
        "source_pdf": ai_input.get("source_pdf", ""),
        "sheet_register": [sheet_entry(page) for page in pages],
        "levels": levels,
        "cross_sheet_links": cross_sheet_links(levels),
        "coverage_exceptions": exceptions,
        "status": "review_required" if exceptions else "coverage_ready_for_engineer_review",
    }


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
        "source": {"page": page.get("page"), "kind": "reviewed_pdf_page"},
    }


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
    all_roles = {page.get("thermal_role") for page in pages}
    for level in levels:
        roles = set(level["pages_by_thermal_role"])
        if "primary_geometry" in roles and "surface_confirmation" not in roles:
            exceptions.append(issue(level["level_name"], "surface_views_missing", "A plan is present but no elevation, section, or RCP is linked to confirm surfaces and exposure."))
        if "primary_geometry" in roles and not level["proposed_purpose"]:
            exceptions.append(issue(level["level_name"], "floor_purpose_missing", "No apparent floor purpose was found; confirm its use before load assumptions are supplied."))
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
