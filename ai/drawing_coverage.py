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
        "version": 2,
        "source_pdf": ai_input.get("source_pdf", ""),
        "source_fingerprint": source_fingerprint(ai_input),
        "generated_from": "ai_input.json",
        "generated_at": timestamp(),
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
        text = " ".join(str(page.get(key, "")) for key in ("title", "drawing_number", "detected_type", "plan_role", "thermal_role")).lower()
        rooms = page.get("rooms", []) or []
        triage_item = triage.get(page.get("page"), {})
        role = triage_item.get("page_role") or page.get("plan_role")
        evidence = []
        confidence = float(page.get("confidence", 0) or 0)
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
        if not role:
            if any(term in text for term in ("reflected ceiling", "rcp", "ceiling plan")):
                role, evidence = "reflected_ceiling_plan", ["title/role contains reflected-ceiling terminology"]
            elif any(term in text for term in ("lighting", "services", "electrical", "hydraulic")):
                role, evidence = "services_or_lighting_plan", ["title/role contains services or lighting terminology"]
            elif any(term in text for term in ("elevation", "section")):
                role, evidence = "elevation_or_section", ["title/role contains elevation or section terminology"]
            elif any(term in text for term in ("detail", "schedule", "legend")):
                role, evidence = "detail", ["title/role contains detail or schedule terminology"]
            elif page.get("detected_type") == "floor_plan" or "plan" in text or rooms:
                role, evidence = "supporting_geometry_plan", ["page classified as plan or contains room records"]
            else:
                role, evidence = "reference", ["no calculation-page role could be established"]
        else:
            evidence = ["existing page-triage or plan-role proposal"]
        level = page.get("level_name") or triage_item.get("floor_label", "")
        ambiguous = not level and role in {"main_floor_plan", "supporting_geometry_plan", "reflected_ceiling_plan", "services_or_lighting_plan"}
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
            "source": {"page": page.get("page"), "kind": "reviewed_pdf_page"},
        })
    return result


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
