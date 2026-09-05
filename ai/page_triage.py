#!/usr/bin/env python3

"""Validate the first vision pass: page purpose and floor grouping."""

import json
from pathlib import Path

from ai.ai_packet import load_json


ALLOWED_DISPOSITIONS = {"core_geometry", "support_context", "exclude"}
ALLOWED_PAGE_ROLES = {
    "main_floor_plan", "supporting_geometry_plan", "reflected_ceiling_plan",
    "existing_hvac_plan", "reference_context", "detail_context", "3d_render",
}


def confirmed_page_numbers(ai_input):
    pages = []
    for bucket in ai_input.get("confirmed_pages", {}).values():
        pages.extend(item.get("page") for item in bucket)
    return {page for page in pages if page is not None}


def triage_pages(vision):
    result = vision.get("result", {}) if isinstance(vision, dict) else {}
    triage = result.get("page_triage", {}) if isinstance(result, dict) else {}
    pages = triage.get("pages", []) if isinstance(triage, dict) else []
    return pages if isinstance(pages, list) else []


def validate_page_triage(vision, ai_input):
    confirmed = confirmed_page_numbers(ai_input)
    pages = triage_pages(vision)
    issues = []
    decisions = []
    seen = set()

    for item in pages:
        page = item.get("page")
        if page in seen:
            issues.append(issue(page, "page", "page was triaged more than once"))
            continue
        seen.add(page)
        if page not in confirmed:
            issues.append(issue(page, "page", "page is not in the confirmed review selection"))
            continue
        if item.get("disposition") not in ALLOWED_DISPOSITIONS:
            issues.append(issue(page, "disposition", "must be core_geometry, support_context, or exclude"))
        if item.get("page_role") not in ALLOWED_PAGE_ROLES:
            issues.append(issue(page, "page_role", "is not an allowed page role"))
        if not isinstance(item.get("evidence"), list) or not item["evidence"]:
            issues.append(issue(page, "evidence", "requires at least one visible-evidence statement"))
        if item.get("disposition") != "exclude" and not str(item.get("floor_label", "")).strip():
            issues.append(issue(page, "floor_label", "is required for included pages; use needs_confirmation when unreadable"))
        decisions.append(dict(item))

    for page in sorted(confirmed - seen):
        issues.append(issue(page, "page", "every confirmed page needs a triage decision"))

    return {
        "status": "valid" if not issues else "validation_issues",
        "issue_count": len(issues),
        "issues": issues,
        "decisions": decisions,
    }


def validate_page_triage_file(path, ai_input_path, output_path=None):
    path = Path(path)
    report = validate_page_triage(load_json(path), load_json(ai_input_path))
    output_path = Path(output_path or path.with_name("page_triage_validation.json"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


def issue(page, field, message):
    return {"page": page, "field": field, "message": message}
