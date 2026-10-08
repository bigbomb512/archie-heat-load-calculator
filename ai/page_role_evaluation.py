"""Score the page finder's page roles against a person-checked answer sheet.

An answer sheet (evaluations/page_roles/*.json) holds a few certain facts about one
architect set, by page number:

- geometry: pages that are the tenancy's main floor plan. At least one of them must be
  chosen as a main plan (several are listed when more than one would do).
- rcp: reflected ceiling plans. Each must be recognised as one and kept.
- must_keep: pages the heat load needs (plans, ceiling plans, elevations, sections).
  None may be discarded.
- not_geometry: pages that must never be chosen as the main plan (covers, details,
  joinery, site or context plans, schedules).

The app's answer comes from the analysis packet (packet.json): primary, reference,
kept and discarded pages with their type and plan role. Scoring is report-only.
"""

from ai.page_inventory import INFORMATION_KINDS

MAIN_ROLES = {"main_floor_plan"}
RCP_TYPES = {"reflected_ceiling_plan"}
FACT_KEYS = ("geometry", "rcp", "must_keep", "not_geometry")
INFORMATION_STATUSES = {"not_started", "drafted_from_ai", "confirmed_blind", "confirmed_from_draft"}


def validate_answer_sheet(sheet, page_count=None):
    if not isinstance(sheet, dict) or not str(sheet.get("case_id", "")).strip():
        raise ValueError("An answer sheet needs a case_id.")
    pages = sheet.get("pages")
    if not isinstance(pages, dict):
        raise ValueError("An answer sheet needs a pages object.")
    for key in pages:
        if key not in FACT_KEYS:
            raise ValueError(f"Unknown page fact {key!r}; use {', '.join(FACT_KEYS)}.")
        values = pages[key]
        if not isinstance(values, list) or any(type(value) is not int or value < 1 for value in values):
            raise ValueError(f"{key} must be a list of page numbers.")
    if set(pages.get("geometry", [])) & set(pages.get("not_geometry", [])):
        raise ValueError("A page can't be both geometry and not_geometry.")
    status = sheet.get("information_status")
    if status is not None and (not isinstance(status, str) or status not in INFORMATION_STATUSES):
        raise ValueError("information_status must be not_started, drafted_from_ai, confirmed_blind or confirmed_from_draft.")
    information = sheet.get("information")
    if information is not None:
        if not isinstance(information, dict):
            raise ValueError("information must be an object mapping page numbers to lists of kinds.")
        for page_key, kinds in information.items():
            if not isinstance(page_key, str) or not page_key.isdigit() or str(int(page_key)) != page_key or int(page_key) < 1:
                raise ValueError(f"Invalid information page key {page_key!r}.")
            if page_count is not None and int(page_key) > page_count:
                raise ValueError(f"Information page {page_key} is not in this set (1-{page_count}).")
            if not isinstance(kinds, list):
                raise ValueError(f"information page {page_key} must contain a list of kinds.")
            for kind in kinds:
                if kind not in INFORMATION_KINDS:
                    raise ValueError(f"Unknown information kind {kind!r}; use {', '.join(INFORMATION_KINDS)}.")
        if status in {"confirmed_blind", "confirmed_from_draft"} and page_count is not None:
            expected = {str(page) for page in range(1, page_count + 1)}
            if set(information) != expected:
                raise ValueError("A confirmed information key must include every page in the set.")
    elif status in {"confirmed_blind", "confirmed_from_draft"}:
        raise ValueError("A confirmed information key needs information for every page.")
    return sheet


def app_page_roles(packet):
    """Per page: group (primary/reference/kept/discarded), type and plan role, from packet.json."""
    roles = {}
    # Later groups win: primary > reference > discarded > kept. A discarded page is also listed in
    # kept_pages (tagged "not calculation evidence"); it still counts as discarded.
    for group in ("kept_pages", "discarded_pages", "reference_pages", "primary_pages"):
        for row in packet.get(group) or []:
            if not isinstance(row, dict) or type(row.get("page")) is not int:
                continue
            current = roles.setdefault(row["page"], {"page": row["page"]})
            current["group"] = group.removesuffix("_pages")
            current["type"] = row.get("type")
            current["plan_role"] = row.get("plan_role")
            current["title"] = row.get("title") or ""
    return roles


def main_plan_pages(roles):
    return sorted(page for page, row in roles.items()
                  if row.get("group") == "primary" and row.get("plan_role") in MAIN_ROLES)


def score(sheet, packet):
    sheet = validate_answer_sheet(sheet)  # page-role facts only; page contents are scored by ai.page_inventory_scoring
    roles = app_page_roles(packet)
    main = set(main_plan_pages(roles))
    facts = []

    def add(fact, page, passed, detail):
        facts.append({"fact": fact, "page": page, "passed": bool(passed), "detail": detail})

    def describe(page):
        row = roles.get(page)
        if not row:
            return "not in the packet"
        return f"{row.get('group')} · {row.get('type') or '-'}/{row.get('plan_role') or '-'}"

    pages = sheet["pages"]
    if pages.get("geometry"):
        hit = sorted(main & set(pages["geometry"]))
        add("geometry", pages["geometry"], hit,
            f"main plan chosen: {sorted(main) or 'none'}" + ("" if hit else f"; expected one of {pages['geometry']}"))
    for page in pages.get("rcp", []):
        row = roles.get(page, {})
        add("rcp", page, row.get("type") in RCP_TYPES and row.get("group") != "discarded", describe(page))
    for page in pages.get("must_keep", []):
        row = roles.get(page, {})
        add("must_keep", page, bool(row) and row.get("group") != "discarded", describe(page))
    for page in pages.get("not_geometry", []):
        add("not_geometry", page, page not in main, describe(page))
    passed = sum(1 for fact in facts if fact["passed"])
    return {"case_id": sheet["case_id"], "confirmed": sheet.get("confirmed") is True,
            "main_plan_pages": sorted(main), "facts": facts,
            "totals": {"passed": passed, "failed": len(facts) - passed, "facts": len(facts),
                       "accuracy_percent": round(100.0 * passed / len(facts), 1) if facts else None}}


def summarise(reports):
    """Totals across cases, per fact kind, and the share of cases whose main plan is right."""
    by_fact = {}
    for report in reports:
        for fact in report["facts"]:
            row = by_fact.setdefault(fact["fact"], {"passed": 0, "facts": 0})
            row["facts"] += 1
            row["passed"] += int(fact["passed"])
    for row in by_fact.values():
        row["accuracy_percent"] = round(100.0 * row["passed"] / row["facts"], 1) if row["facts"] else None
    passed = sum(report["totals"]["passed"] for report in reports)
    total = sum(report["totals"]["facts"] for report in reports)
    return {"cases": len(reports), "confirmed_cases": sum(1 for report in reports if report["confirmed"]),
            "passed": passed, "facts": total,
            "accuracy_percent": round(100.0 * passed / total, 1) if total else None, "by_fact": by_fact}


def render_markdown(reports, summary):
    lines = ["# Page-role scorecard", "",
             f"{summary['cases']} sets ({summary['confirmed_cases']} confirmed by a person) · "
             f"{summary['passed']} of {summary['facts']} facts right ({summary['accuracy_percent']}%)", "",
             "| Fact | Right | Of | % |", "|---|---|---|---|"]
    for fact, row in sorted(summary["by_fact"].items()):
        lines.append(f"| {fact} | {row['passed']} | {row['facts']} | {row['accuracy_percent']} |")
    for report in reports:
        lines += ["", f"## {report['case_id']}{'' if report['confirmed'] else ' (draft, not yet confirmed)'}",
                  f"Main plan chosen: {report['main_plan_pages'] or 'none'} · {report['totals']['passed']}/{report['totals']['facts']}", ""]
        for fact in report["facts"]:
            lines.append(f"- {'✓' if fact['passed'] else '✗'} {fact['fact']} p{fact['page']}: {fact['detail']}")
    return "\n".join(lines) + "\n"
