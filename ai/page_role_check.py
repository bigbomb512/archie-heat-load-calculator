"""Person check of the page-role answer sheets: what the check page shows, and how answers update a sheet.

The check page (tools/page_role_check.py) shows, per set, the pages an answer sheet lists for each
fact, with Right / Wrong marks, pages the person adds, a level name per main plan, and notes.
apply_answers() turns those answers into an updated answer sheet. A sheet becomes confirmed only
when the person ticked "This set is checked" and marked every listed page Right or Wrong.
"""

from copy import deepcopy

from ai.page_role_evaluation import FACT_KEYS, RCP_TYPES, app_page_roles, main_plan_pages, validate_answer_sheet

MARKS = {"right", "wrong"}


def case_view(sheet, packet, page_count):
    """What the check page needs for one set: the sheet's facts and the app's current picks."""
    sheet = validate_answer_sheet(sheet)
    roles = app_page_roles(packet)
    return {
        "case_id": sheet["case_id"],
        "description": sheet.get("description", ""),
        "confirmed": bool(sheet.get("confirmed")),
        "notes": list(sheet.get("notes") or []),
        "page_count": int(page_count),
        "facts": {key: list(sheet["pages"].get(key, [])) for key in FACT_KEYS},
        "levels": {str(page): name for page, name in (sheet.get("levels") or {}).items()},
        "app": {
            "main_plan": main_plan_pages(roles),
            "rcp": sorted(page for page, row in roles.items() if row.get("type") in RCP_TYPES
                          and row.get("group") in {"primary", "reference"}),
            "discarded": sorted(page for page, row in roles.items() if row.get("group") == "discarded"),
            "titles": {str(page): row.get("title", "") for page, row in roles.items() if row.get("title")},
        },
    }


def _pages(values, page_count, label):
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list of page numbers.")
    for value in values:
        if type(value) is not int or not 1 <= value <= page_count:
            raise ValueError(f"{label}: page {value!r} is not in this set (1-{page_count}).")
    return values


def apply_answers(sheet, answer, page_count, checked_on):
    """Return (updated sheet, list of problems). Problems leave the sheet unconfirmed; they don't raise.

    answer = {"checked": bool, "marks": {fact: {"<page>": "right"|"wrong"}}, "added": {fact: [page]},
              "levels": {"<page>": "Ground"}, "notes": "free text"}
    """
    sheet = deepcopy(validate_answer_sheet(sheet))
    answer = answer if isinstance(answer, dict) else {}
    marks = answer.get("marks") or {}
    added = answer.get("added") or {}
    problems = []
    pages = {}
    for key in FACT_KEYS:
        listed = list(sheet["pages"].get(key, []))
        fact_marks = marks.get(key) or {}
        for page, mark in fact_marks.items():
            if mark not in MARKS:
                raise ValueError(f"{key} p{page}: mark must be right or wrong.")
        unmarked = [page for page in listed if fact_marks.get(str(page)) not in MARKS]
        if unmarked:
            problems.append(f"{key}: pages {unmarked} not marked Right or Wrong")
        kept = [page for page in listed if fact_marks.get(str(page)) != "wrong"]
        extra = _pages(added.get(key, []), page_count, f"{key} added")
        result = sorted(set(kept) | set(extra))
        if result or key in sheet["pages"]:
            pages[key] = result
    overlap = sorted(set(pages.get("geometry", [])) & set(pages.get("not_geometry", [])))
    if overlap:
        problems.append(f"pages {overlap} are both a main plan and never-a-main-plan; fix one")
        pages["not_geometry"] = [page for page in pages["not_geometry"] if page not in overlap]
    sheet["pages"] = pages
    levels = {}
    for page, name in (answer.get("levels") or {}).items():
        name = " ".join(str(name).split())[:40]
        if name and str(page).isdigit() and int(page) in pages.get("geometry", []):
            levels[str(page)] = name
    if levels:
        sheet["levels"] = dict(sorted(levels.items(), key=lambda item: int(item[0])))
    else:
        sheet.pop("levels", None)
    note = " ".join(str(answer.get("notes") or "").split())
    if note:
        sheet.setdefault("notes", []).append(f"Person check {checked_on}: {note}")
    if not answer.get("checked"):
        problems.append("not ticked as checked")
    sheet["confirmed"] = not problems
    if sheet["confirmed"]:
        sheet["confirmed_on"] = checked_on
    else:
        sheet.pop("confirmed_on", None)
    validate_answer_sheet(sheet)
    return sheet, problems
