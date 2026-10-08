"""Person check of the page-role answer sheets: what the check page shows, and how answers update a sheet.

The check page (tools/page_role_check.py) shows, per set, the pages an answer sheet lists for each
fact, with Right / Wrong marks, pages the person adds, a level name per main plan, and notes.
apply_answers() turns those answers into an updated answer sheet. A sheet becomes confirmed only
when the person ticked "This set is checked" and marked every listed page Right or Wrong.
"""

from copy import deepcopy

from ai.page_inventory import INFORMATION_KINDS
from ai.page_role_evaluation import FACT_KEYS, RCP_TYPES, app_page_roles, main_plan_pages, validate_answer_sheet

INFORMATION_KIND_LABELS = {
    "room_geometry": "Areas and sizes",
    "ceiling_height": "Ceiling heights",
    "materials_construction": "Materials",
    "windows_glazing": "Windows and glazing",
    "equipment_appliances": "Equipment",
    "lighting": "Lighting",
    "people_occupancy": "People",
    "operating_hours": "Hours",
    "airflow_ventilation": "Airflow",
    "hvac_plant": "HVAC plant",
    "location_orientation": "Location",
}

MARKS = {"right", "wrong"}


def case_view(sheet, packet, page_count, ai_readings=None):
    """What the check page needs for one set: the sheet's facts and the app's current picks."""
    sheet = validate_answer_sheet(sheet, page_count=page_count)
    roles = app_page_roles(packet)
    ai_information = {}
    for page, reading in (ai_readings or {}).items():
        if reading:
            ai_information[str(page)] = sorted({row.get("kind") for row in reading.get("information", [])
                                                if isinstance(row, dict) and row.get("kind") in INFORMATION_KINDS})
    return {
        "case_id": sheet["case_id"],
        "description": sheet.get("description", ""),
        "confirmed": bool(sheet.get("confirmed")),
        "notes": list(sheet.get("notes") or []),
        "page_count": int(page_count),
        "information": deepcopy(sheet.get("information") or {}),
        "information_status": sheet.get("information_status", "not_started"),
        "ai_information": ai_information,
        "has_ai_replies": bool(ai_readings),
        "information_kind_labels": INFORMATION_KIND_LABELS,
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
              "levels": {"<page>": "Ground"}, "notes": "free text",
              "information": {"<page>": [kind, ...]}, "information_nothing": {"<page>": bool},
              "information_drafted": bool, "information_checked": bool}
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
    if not answer.get("checked"):
        problems.append("not ticked as checked")
    role_problems = list(problems)
    info_values = answer.get("information")
    nothing = answer.get("information_nothing") or {}
    if not isinstance(nothing, dict):
        raise ValueError("information_nothing must map page numbers to booleans.")
    drafted = (answer.get("information_drafted") is True or
               answer.get("information_status") in {"drafted_from_ai", "confirmed_from_draft"} or
               sheet.get("information_status") in {"drafted_from_ai", "confirmed_from_draft"})
    if info_values is not None:
        if not isinstance(info_values, dict):
            raise ValueError("information must map page numbers to lists of kinds.")
        cleaned = {}
        for page_key, kinds in info_values.items():
            if not isinstance(page_key, str) or not page_key.isdigit() or str(int(page_key)) != page_key or not 1 <= int(page_key) <= page_count:
                raise ValueError(f"Information page {page_key!r} is not in this set (1-{page_count}).")
            if not isinstance(kinds, list):
                raise ValueError(f"information page {page_key} must contain a list of kinds.")
            if any(kind not in INFORMATION_KINDS for kind in kinds):
                raise ValueError(f"information page {page_key} contains an unknown kind.")
            if len(kinds) != len(set(kinds)):
                raise ValueError(f"information page {page_key} contains duplicate kinds.")
            if nothing.get(page_key) and kinds:
                raise ValueError(f"information page {page_key} can't list kinds and mark nothing at once.")
            cleaned[page_key] = list(kinds)
        for page_key, is_nothing in nothing.items():
            if not isinstance(page_key, str) or not page_key.isdigit() or str(int(page_key)) != page_key or not 1 <= int(page_key) <= page_count:
                raise ValueError(f"Information page {page_key!r} is not in this set (1-{page_count}).")
            if type(is_nothing) is not bool:
                raise ValueError(f"information_nothing page {page_key} must be true or false.")
        done_pages = {int(page) for page, kinds in cleaned.items() if kinds}
        done_pages.update(int(page) for page, value in nothing.items() if value)
        done = len(done_pages) == page_count
        if answer.get("information_checked") and not done:
            problems.append(f"page contents: {page_count - len(done_pages)} pages still need a tick or 'Nothing for heat load'")
        if answer.get("information_checked") and done:
            sheet["information"] = {str(page): cleaned.get(str(page), []) for page in range(1, page_count + 1)}
            sheet["information_status"] = "confirmed_from_draft" if drafted else "confirmed_blind"
            sheet["information_confirmed_on"] = checked_on
        elif drafted:
            sheet["information"] = cleaned
            sheet["information_status"] = "drafted_from_ai"
            sheet.pop("information_confirmed_on", None)
        else:
            sheet["information"] = cleaned
            sheet["information_status"] = "not_started"
            sheet.pop("information_confirmed_on", None)
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
    if note and not any(str(existing).startswith("Person check ") and str(existing).endswith(f": {note}") for existing in sheet.get("notes", [])):
        sheet.setdefault("notes", []).append(f"Person check {checked_on}: {note}")  # once, however often answers are applied
    sheet["confirmed"] = not role_problems
    if sheet["confirmed"]:
        sheet["confirmed_on"] = checked_on
    else:
        sheet.pop("confirmed_on", None)
    validate_answer_sheet(sheet, page_count=page_count)
    return sheet, problems
