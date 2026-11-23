"""The "Ask the architect" list (user request 2026-11-23): the facts the drawings don't settle, ranked by how much each
could move the cooling load and whether it would change the unit, ready to send to the architect.

The list is built from the assumption range (assumption_range_service): an unknown the range has worked out carries
its swing in kW and the units it could lead to. Each item can be marked "asked" (kept in architect_questions.json with
who and when); it is "answered" once its answer is in the job (window glass, a wall or roof construction, a confirmed
north), whichever screen it was typed on. Answering here goes through the same routes as the other answer screens:
need_answers_service for glass and constructions, the north declaration of reviewer_room_geometry_service for north.
The load isn't recalculated by an answer; the job's to-do list asks for the update as it does after any answer.
"""

import json
import os
import time
from pathlib import Path

from backend import assumption_range_service

FILE = "architect_questions.json"
NAME_LIMIT = 80


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path, value):
    staging = Path(path).with_name(f".{Path(path).name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(staging, path)


def _fmt(kw):
    return f"{kw:.1f}"


def _why(row):
    """One line on what the answer could do to the load, from the range's own numbers only."""
    cases = [case for case in row.get("cases") or [] if case.get("status") == "calculated"]
    if not cases:
        return ""
    parts = [f"{case['label']}: {_fmt(case['kw'])} kW ({case['delta_kw']:+.1f})" for case in cases]
    unit = next((case["unit"] for case in cases if case.get("changes_unit")), "")
    return "; ".join(parts) + (f". This could change the unit to {unit}." if unit else ".")


def get(project, *, start=True):
    root = Path(project["review_dir"])
    asked = (_read(root / FILE).get("asked") or {})
    report = _read(assumption_range_service.ai_preliminary_service._paths(project)["report"])
    unknowns = {row["key"]: row for row in assumption_range_service.open_unknowns(root, report)}
    rng = assumption_range_service.get(project, start=start)
    ranged = {row["key"]: row for row in rng.get("unknowns") or []} if rng.get("status") == "calculated" else {}
    items = []
    for case_row in assumption_range_service.load_cases().get("unknowns", []):
        key = case_row["key"]
        unknown = unknowns.get(key)
        if not unknown or not (unknown["open"] or unknown["answered"] or key in ranged):
            continue
        swing = ranged.get(key, {})
        status = "answered" if unknown["answered"] else "asked" if key in asked else "open"
        items.append({"id": key, "label": case_row["label"], "question": case_row["question"],
                      "assumed": case_row.get("assumed", ""), "answer_kind": case_row.get("answer_kind", ""),
                      "status": status, "asked": asked.get(key),
                      "swing_kw": swing.get("swing_kw"), "low_kw": swing.get("low_kw"), "high_kw": swing.get("high_kw"),
                      "changes_unit": bool(swing.get("changes_unit")), "why": _why(swing)})
    # Unit-changing first, then the larger swing, then those the range hasn't sized; answered ones last.
    items.sort(key=lambda row: (row["status"] == "answered", not row["changes_unit"], row["swing_kw"] is None,
                                -(row["swing_kw"] or 0)))
    open_items = [row for row in items if row["status"] != "answered"]
    from ai import construction_u_values
    table = construction_u_values.load_table()
    rows = table if isinstance(table, list) else (table or {}).get("rows") or (table or {}).get("constructions") or []
    constructions = {surface: [{"id": row.get("id") or row.get("construction_id"), "name": row["name"],
                                "u_value_w_m2k": row["u_value_w_m2k"]} for row in rows if row.get("surface") == surface]
                     for surface in ("wall", "roof")}
    return {"items": items, "range": rng, "open_count": len(open_items), "constructions": constructions,
            "unit_changing_open": sum(1 for row in open_items if row["changes_unit"]),
            "email": email(project, open_items, rng)}


def email(project, items, rng):
    """A plain email to the architect: the open questions in order, each with why it matters."""
    name = project.get("name") or "the project"
    lines = [f"Hi,", "",
             f"We're working out the air-conditioning load for {name}. A few things aren't shown on the drawings we "
             "have, and they change the size of the system. Could you let us know:", ""]
    for number, row in enumerate(items, 1):
        lines.append(f"{number}. {row['question']}")
        if row.get("swing_kw") is not None:
            lines.append(f"   (Could move the load by up to {_fmt(row['swing_kw'])} kW"
                         + (" and change the unit size." if row["changes_unit"] else ".") + ")")
    if rng.get("status") == "calculated" and rng.get("low_kw") is not None:
        lines += ["", f"At the moment the load is {_fmt(rng['saved_kw'])} kW and could be anywhere from "
                      f"{_fmt(rng['low_kw'])} to {_fmt(rng['high_kw'])} kW depending on these answers."]
    lines += ["", "A marked-up drawing, a specification or the NatHERS certificate is fine.", "", "Thanks"]
    subject = f"Questions about {name} for the air-conditioning load"
    return {"subject": subject, "body": "\n".join(lines)}


def _reviewer(data):
    return " ".join(str(data.get("reviewer") or "").split())[:NAME_LIMIT] or "Operator"


def post(web, project, data):
    """mark_asked / unmark (ids), or answer (id with the answer's fields). Returns the list as get() does."""
    root = Path(project["review_dir"])
    action = str(data.get("action") or "")
    valid = {row["key"] for row in assumption_range_service.load_cases().get("unknowns", [])}
    if action in {"mark_asked", "unmark"}:
        ids = [str(value) for value in data.get("ids") or []]
        if any(key not in valid for key in ids):
            raise ValueError("Choose valid questions to mark.")
        if not ids:
            raise ValueError("Choose which questions to mark.")
        stored = _read(root / FILE)
        asked = stored.setdefault("asked", {})
        for key in ids:
            if action == "mark_asked":
                asked[key] = {"by": _reviewer(data), "at": time.time()}
            else:
                asked.pop(key, None)
        _write(root / FILE, stored)
        return {**get(project, start=False), "summary": f"{len(ids)} question(s) marked "
                                                         + ("asked." if action == "mark_asked" else "not asked.")}
    if action == "answer":
        summary = _answer(web, project, data)
        return {**get(project, start=False), "summary": summary}
    raise ValueError("Unknown action.")


def _answer(web, project, data):
    from backend import need_answers_service
    key = str(data.get("id") or "")
    source = " ".join(str(data.get("source") or "architect").split())[:80]
    reviewer = _reviewer(data)
    if key == "windows":
        result = need_answers_service._save_glazing(project, {"u_value_w_m2k": data.get("u_value_w_m2k"),
                                                              "shgc": data.get("shgc"), "value": data.get("value"),
                                                              "source": source}, [], reviewer)
        return result["summary"]
    if key in {"wall", "roof"}:
        result = need_answers_service._save_construction(project, {"surface": key, "construction_id": data.get("construction_id"),
                                                                   "u_value_w_m2k": data.get("u_value_w_m2k"),
                                                                   "value": data.get("value"), "source": source}, [], reviewer)
        return result["summary"]
    if key == "north":
        return _answer_north(web, project, data, reviewer)
    raise ValueError("Choose which question this answers.")


def _answer_north(web, project, data, reviewer):
    """North confirmed as read ("as_read"), the other way round ("reversed"), or a typed plan-up bearing ("bearing"),
    declared by the reviewer on every plan page that has a north."""
    from backend import reviewer_room_geometry_service
    choice = str(data.get("choice") or "")
    north = _read(Path(project["review_dir"]) / "reviewer_room_geometry.json").get("page_north") or {}
    pages = sorted((int(page), row) for page, row in north.items()
                   if str(page).isdigit() and isinstance(row, dict) and isinstance(row.get("plan_up_azimuth_deg"), (int, float)))
    if not pages:
        raise ValueError("No plan page has a north yet: set it on the drawing first.")
    if choice == "bearing":
        bearing = data.get("plan_up_azimuth_deg")
        if type(bearing) not in (int, float) or not 0 <= bearing < 360:
            raise ValueError("Enter the direction the top of the plan faces, from 0 to under 360 degrees.")
    elif choice not in {"as_read", "reversed"}:
        raise ValueError("Say whether north is as read, the other way round, or give a bearing.")
    for page, row in pages:
        value = (float(data["plan_up_azimuth_deg"]) if choice == "bearing"
                 else (row["plan_up_azimuth_deg"] + (180 if choice == "reversed" else 0)) % 360)
        reviewer_room_geometry_service.post(web, project, {"action": "declare_north", "page": page, "reviewer": reviewer,
                                                           "declaration_source": "reviewer", "plan_up_azimuth_deg": value})
    words = {"as_read": "confirmed as read", "reversed": "turned the other way round",
             "bearing": f"set to a plan-up bearing of {float(data.get('plan_up_azimuth_deg') or 0):g}°"}[choice]
    return f"North {words} on {len(pages)} plan page(s)."
