"""Read-only answer card and ranked work list for a job."""

import json
import math
import re
from pathlib import Path

from ai.room_use_resolution import is_kitchen_only
from backend import (calculation_service, contractor_report, drawing_tasks_service,
                     job_plan_service, phase1_supervisor_service, result_status_service)


STEPS = ("drawings", "rooms", "operating", "results", "details")
SEVERITY = {"blocks": 0, "changes_number": 1, "check": 2}
OUTDOOR = ("balcony", "porch", "deck", "terrace", "alfresco", "verandah", "veranda", "patio",
           "driveway", "carport", "garage", "fence", "pool", "courtyard", "yard", "garden", "void", "roof")
WET = ("ensuite", "bath", "toilet", "wc", "powder", "laundry")
STATE_REASON = {"finding_surfaces": "Its walls and windows weren't worked out from the drawings, so it has no load yet.",
                "finding_area": "Its area is still being found from the drawings.",
                "needs_use": "Its room use needs checking.",
                "left_out": "It was left out of the calculation."}


def _number(value):
    """Accept only finite saved numbers, including a legitimate zero load."""
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _join(names):
    names = [str(name) for name in names]
    return " and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " + names[-1]


def _plural(count, one, many):
    return f"{count} {one if count == 1 else many}"


def _plain_count(text):
    """Result-status wording ("1 item(s).") without the (s)."""
    return re.sub(r"\b(\d+) (\w+)\(s\)", r"\1 \2s", re.sub(r"\b1 (\w+)\(s\)", r"1 \1", str(text or "")))


def _words(value, words):
    return any(re.search(r"\b" + re.escape(word) + r"\b", str(value or ""), re.I) for word in words)


def _name(room):
    return str(room.get("display_name") or room.get("name") or "Room")


def _area(room):
    return _number(room.get("area_m2"))


def _area_text(value):
    return f"{value:.1f}"


def _room_data(room):
    use = room.get("use") or {}
    return {"room_id": room.get("room_id"), "name": _name(room), "level": room.get("level") or "",
            "area_m2": _area(room), "use_id": use.get("taxonomy_id"),
            "use_label": use.get("taxonomy_label"), "cooled": bool(room.get("included") or room.get("kind") == "comfort")}


def _link(step, label):
    return {"type": "link", "step": step, "label": label}


def _item(ident, kind, severity, title, detail, step, room=None, impact=None, actions=()):
    return {"id": ident, "kind": kind, "severity": severity, "title": title, "detail": detail,
            "step": step, "room": _room_data(room) if room else None, "impact": impact,
            "actions": list(actions)}


def _impact(room, field):
    kw = _number(room.get(field))
    return {"kw": round(kw, 2), "text": f"Adds {kw:.1f} kW to the total now"} if kw is not None else None


def _peak(report):
    return (report or {}).get("included_scope_peak") or {}


def _display_rooms(plan):
    rooms = [row for row in plan.get("rooms", []) if isinstance(row, dict)]
    joined = plan.get("joined_rooms") or []
    member_ids = {room_id for join in joined for room_id in join.get("member_ids", [])}
    shown = [row for row in rooms if row.get("room_id") not in member_ids]
    for join in joined:
        primary = next((row for row in rooms if row.get("room_id") == join.get("member_ids", [None])[0]), {})
        shown.append({**primary, "room_id": join["id"], "name": join["name"], "display_name": join["name"],
                      "area_m2": join.get("area_m2"), "final_kw": join.get("final_kw"),
                      "phase1_kw": join.get("phase1_kw"), "join_id": join["id"],
                      "included": join.get("cooled"), "kind": "comfort" if join.get("cooled") else "not_cooled",
                      "use": join.get("use") or primary.get("use")})
    return shown


# Why a cooled room isn't in the load yet, in the operator's words, by its state on the job plan (job_plan_service
# _room_state). One list, shown the same on every screen: the summary card, Rooms and Results (2026-11-24: they had
# said "5 rooms", "3 rooms" and "16 rooms" for the same job).
NOT_COUNTED_REASONS = {
    "needs_area": ("needs_area", "needs an area typed in"),
    "finding_area": ("finding_area", "area still being read from the drawings"),
    "needs_use": ("needs_use", "needs its use chosen"),
    "finding_surfaces": ("finding_surfaces", "walls and windows still being worked out"),
    "left_out": ("left_out", "left out of the room selection"),
}
NOT_COUNTED_GROUPS = (("needs_area", "need an area", "needs an area"),
                      ("needs_use", "need their use chosen", "needs its use chosen"),
                      ("finding_area", "have their area still being read", "has its area still being read"),
                      ("finding_surfaces", "have walls still being worked out", "has walls still being worked out"),
                      ("left_out", "were left out of the room selection", "was left out of the room selection"),
                      ("not_in_result", "aren't in the latest calculation", "isn't in the latest calculation"))


def _not_counted(rooms, field):
    """The cooled rooms not in the load, each with why: [{room_id, name, why, reason}] and a one-line breakdown."""
    rows = []
    for room in rooms:
        if not (room.get("kind") == "comfort" or room.get("included")):
            continue
        if room.get("included") and _number(room.get(field)) is not None:
            continue
        why, reason = NOT_COUNTED_REASONS.get(room.get("state"), ("not_in_result", "not in the latest calculation; update the load"))
        rows.append({"room_id": str(room.get("room_id") or ""), "name": str(room.get("display_name") or room.get("name") or "A room"),
                     "why": why, "reason": reason})
    parts = []
    for key, many, one in NOT_COUNTED_GROUPS:
        count = sum(row["why"] == key for row in rows)
        if count:
            parts.append(f"{count} {one if count == 1 else many}")
    return rows, ", ".join(parts)


def _answer(plan, calculation, failed):
    status = plan.get("status") or {}
    final, base = _peak(plan.get("final_report")), _peak(plan.get("phase1_report"))
    final_kw = _number(final.get("final_design_total_kw", final.get("design_total_kw")))
    base_kw = _number(base.get("design_total_kw", base.get("final_design_total_kw")))
    busy = calculation.get("status") in {"queued", "running", "stopping"}
    if busy:
        state = "working"
    elif final_kw is not None:
        state = "stale" if status.get("result_stale") else "current"
    elif base_kw is not None:
        state = "building_only"
    else:
        state = "none"
    peak = final if final_kw is not None else base if base_kw is not None else {}
    total = final_kw if final_kw is not None else base_kw
    rooms = _display_rooms(plan)
    field = "final_kw" if final_kw is not None else "phase1_kw"
    counted = sum(bool(row.get("included")) and _number(row.get(field)) is not None for row in rooms)
    not_counted, not_counted_text = _not_counted(rooms, field) if state != "none" else ([], "")
    notes = {"none": "Not calculated yet.", "working": "The load is being calculated.",
             "building_only": "People, lights and appliances are not included yet.",
             "current": "The saved calculation is current.",
             "stale": "Out of date: rooms were changed after it was calculated."}
    suggestion = None
    if state in {"current", "stale"} and total is not None:
        try:
            suggestion = f'{contractor_report.ducted(total, dwelling=bool(status.get("dwelling")))["suggested"]} ducted'
        except Exception:
            failed.append("contractor_report")
    return {"state": state, "headline": "Building-only load (before people, lights and appliances)"
            if final_kw is None and base_kw is not None else "Cooling load", "total_kw": total,
            "sensible_kw": _number(peak.get("sensible_kw")), "latent_kw": _number(peak.get("latent_kw")),
            "month": str(peak.get("month") or peak.get("representative_month") or ""),
            "hour": peak.get("display_hour", peak.get("hour")) if type(peak.get("display_hour", peak.get("hour"))) is int else None,
            "suggested_system": suggestion, "rooms_counted": counted,
            "rooms_found": sum(bool(row.get("included") or row.get("kind") == "comfort") for row in rooms),
            "rooms_not_counted": not_counted, "not_counted_text": not_counted_text,
            "note": notes[state]}


def _room_items(rooms, field):
    items = []
    missing = [room for room in rooms if room.get("state") == "needs_area"]
    large_robes = [room for room in rooms if _words(room.get("name"), ("robe", "wir", "walk-in"))
                   and (_area(room) or 0) > 10]
    small_beds = [room for room in rooms if (_words(room.get("name"), ("bed", "bedroom"))
                  or (room.get("use") or {}).get("taxonomy_id") == "house_bedroom") and _area(room) is not None
                  and _area(room) < 6]
    for room in rooms:
        rid, name, area = str(room.get("room_id") or room.get("name")), _name(room), _area(room)
        not_counted = None
        kind, state = room.get("kind"), room.get("state")
        cooled = bool(room.get("included") or kind == "comfort")
        use_id = str((room.get("use") or {}).get("taxonomy_id") or "")
        if state == "needs_area":
            match = next((other for other in rooms if other is not room and other.get("name") == room.get("name")
                          and other.get("level") and _area(other) is not None), None)
            detail = "This room needs an area before it can be counted."
            if match:
                detail += f" {match['name']} on the {match['level']} is {_area_text(_area(match))} m²; if this is the same room, mark it not cooled."
            items.append(_item(f"area:{rid}", "room_area", "blocks", f"Type the area of {name}", detail, "rooms", room,
                               actions=({"type": "area", "label": "Save area"},
                                        {"type": "cooled", "value": False, "label": "Not a cooled room"})))
        elif cooled and state not in {"done", "not_cooled"}:
            not_counted = len(items)
            items.append(_item(f"not-counted:{rid}", "room_not_counted", "blocks", f"{name} isn't in the total yet",
                               STATE_REASON.get(state, "Its load has not been calculated yet."), "rooms", room,
                               actions=(_link("rooms", "Check the room"), {"type": "cooled", "value": False, "label": "Not cooled"})))
        if cooled and (use_id in {"outdoor", "garage"} or _words(room.get("name"), OUTDOOR)):
            items.append(_item(f"outdoor:{rid}", "outdoor_cooled", "changes_number",
                               f"{name} is counted as a cooled room", "Check whether this space needs air-conditioning.",
                               "rooms", room, _impact(room, field), ({"type": "cooled", "value": False, "label": "Not cooled"},)))
        reason = ""
        if room in small_beds:
            reason = "A bedroom is usually 9 m² or more; the label may be on a small piece of the room."
            partner = next((other for other in large_robes if other.get("level") == room.get("level")), None)
            if partner:
                reason += f" {_name(partner)} ({_area_text(_area(partner))} m²) on the {partner.get('level') or 'same level'} may be the master bedroom."
        elif room in large_robes:
            reason = "It may be a bedroom."
            partner = next((other for other in small_beds if other.get("level") == room.get("level")), None)
            if partner:
                reason += f" {_name(partner)} ({_area_text(_area(partner))} m²) on the {partner.get('level') or 'same level'} may be part of this room."
        elif _words(room.get("name"), WET) and area is not None and area > 20:
            reason = "This is large for a wet area; check the label and outline."
        elif cooled and area is not None and area < 1.5:
            reason = "This is very small for a cooled room; check the label and outline."
        if reason and not_counted is not None:
            # One row per room: a room left out of the total carries its size doubt and the fixes for it.
            row = items[not_counted]
            row["title"] = f"{name} ({_area_text(area)} m²) isn't in the total yet"
            row["detail"] += " " + reason
            row["actions"] = row["actions"][:1] + [{"type": "rename", "label": "Rename"},
                                                   {"type": "use", "label": "Change use"}] + row["actions"][1:]
        elif reason:
            title = (f"{name} is only {_area_text(area)} m²" if room in small_beds or (cooled and area < 1.5)
                     else f"{name} is {_area_text(area)} m², large for a robe" if room in large_robes
                     else f"{name} is {_area_text(area)} m², large for this room")
            items.append(_item(f"size:{rid}", "room_size", "changes_number" if cooled and _number(room.get(field)) is not None else "check",
                               title, reason, "rooms", room, _impact(room, field) if cooled else None,
                               ({"type": "rename", "label": "Rename"}, {"type": "use", "label": "Change use"},
                                {"type": "cooled", "value": not cooled, "label": "Not cooled" if cooled else "Cool it"})))
        excluded = OUTDOOR + WET + ("store", "storage", "plant", "robe", "wir", "walk-in")
        if kind == "not_cooled" and is_kitchen_only(room.get("name")):
            # A commercial kitchen isn't cooled (user decision 2026-11-22; a house's kitchen is, 2026-11-23); one open
            # to a cooled room is cooled by that room's system anyway, so the exception is named rather than left.
            items.append(_item(f"kitchen-not-cooled:{rid}", "kitchen_not_cooled", "check", f"{name} isn't cooled",
                               "Kitchens aren't air-conditioned: the exhaust hood draws the cooled air straight out. "
                               "If this kitchen is open to a cooled living or dining area with no wall between, cool it: "
                               "that area's system cools it anyway.", "rooms", room,
                               actions=({"type": "cooled", "value": True, "label": "Cool it (open plan)"},)))
        elif kind == "not_cooled" and area is not None and area >= 12 and not (
                _words(room.get("name"), excluded) or _words(use_id.replace("_", " "), excluded)):
            items.append(_item(f"large-not-cooled:{rid}", "large_not_cooled", "check",
                               f"{name} ({_area_text(area)} m²) isn't cooled", "Check it shouldn't be air-conditioned.",
                               "rooms", room, actions=({"type": "cooled", "value": True, "label": "Cool it"},
                                                       {"type": "use", "label": "Change use"})))
    groups = {}
    for room in rooms:
        if room.get("join_id"):
            continue
        match = re.fullmatch(r"(.+?)\s*\((\d+)\)", str(room.get("name") or ""))
        base = match.group(1).strip() if match else str(room.get("name") or "").strip()
        groups.setdefault((str(room.get("level") or "").casefold(), base.casefold()), []).append(room)
    for (_level, _base), group in groups.items():
        if len(group) < 2 or not any(re.search(r"\(\d+\)$", str(row.get("name") or "")) for row in group):
            continue
        base = re.sub(r"\s*\(\d+\)$", "", str(group[0].get("name") or ""))
        area = sum(_area(row) or 0 for row in group)
        items.append(_item(f"split:{_level}:{_base}", "split_room", "check",
                           f"{base} was read as {len(group)} pieces ({_area_text(area)} m² together)",
                           "Check whether they are one room or several.", "rooms", actions=(
                               {"type": "join", "room_ids": [row["room_id"] for row in group], "name": base,
                                "label": "Join into one room"}, _link("rooms", "Check on the plan"))))
    return items


def _other_items(drawing, supervisor, result, rooms, status):
    items = []
    unresolved = drawing.get("unresolved") or []
    if unresolved:
        names = []
        for row in unresolved:
            target = str(row.get("target") or "")
            match = re.search(r"room[-_]([a-z0-9-]+?)(?:[-_]page[-_]\d+)?$", target, re.I)
            name = match.group(1).replace("-", " ").title() if match else str(row.get("label") or "room")
            if name not in names:
                names.append(name)
        title = "Couldn't trace the outline of " + _join(names)
        action = (drawing.get("recovery") or {}).get("action") or "resume"
        items.append(_item("drawing-task", "drawing_task", "check", title,
                           "They use the area found from the drawings' dimensions instead.", "drawings",
                           actions=({"type": "retry", "endpoint": "/api/drawing-tasks", "body": {"action": action}, "label": "Try again"},)))
    gaps = supervisor.get("exceptions") or supervisor.get("gaps") or []
    room_by_name = {str(row.get("name") or "").casefold(): row for row in rooms}
    roof = []
    fix = bool(supervisor.get("recovery_project_id") or (supervisor.get("recovery") or {}).get("action"))
    for gap in gaps:
        kind, name = str(gap.get("kind") or ""), str(gap.get("room") or "")
        if kind == "roof":
            if name and name not in roof:
                roof.append(name)
        elif kind == "room_area" and not any(row.get("state") == "needs_area" and str(row.get("name")).casefold() == name.casefold() for row in rooms):
            items.append(_item(f"gap-area:{name.casefold()}", "model_gap", "check", f"Check {name or 'the room'}'s area",
                               str(gap.get("message") or "Check the area on the drawings."), "rooms",
                               room_by_name.get(name.casefold()), actions=(_link("fix" if fix else "rooms", "Check the area"),)))
    if roof:
        items.append(_item("gap-roof", "model_gap", "check", "Check what is above " + _join(roof),
                           "The roof plan couldn't be lined up with these floors, so they are taken as under the storey above.",
                           "rooms", actions=(_link("fix" if fix else "rooms", "Check the roof"),)))
    findings = 0
    for row in result.get("items") or []:
        kind, topic = row.get("kind"), str(row.get("topic") or "")
        if kind == "to_find" and "PDF review" in topic:
            match = re.search(r"\d+", str(row.get("text") or ""))
            findings += int(match.group()) if match else 1
        elif "Equipment" in topic and kind in {"to_find", "placeholder"}:
            title = ("Equipment has no room" if "no room" in topic.casefold()
                     else "Equipment needs a heat value" if "no value" in topic.casefold()
                     else "Check the equipment values" if kind == "placeholder"
                     else "Check the equipment")
            items.append(_item(f"equipment:{topic.casefold()}", "equipment", "changes_number" if kind == "to_find" else "check",
                               title, _plain_count(row.get("text") or "Equipment details need checking."), "operating",
                               actions=(_link("operating", "See the equipment"),)))
        elif kind == "not_set" and "site" in topic.casefold():
            items.append(_item("site", "site", "check", "Confirm the site address",
                               str(row.get("text") or "The address needs checking."), "details",
                               actions=(_link("details", "Check the address"),)))
        elif kind == "assumed" and "weather" in topic.casefold():
            pick = status.get("weather_ai_pick") or status.get("weather_location") or "the selected location"
            items.append(_item("weather", "weather", "check",
                               f"Confirm the weather location ({pick}, picked automatically)",
                               "Check that this location suits the site.", "details", actions=(_link("details", "Check the weather"),)))
    if findings:
        items.append(_item("drawing-findings", "drawing_findings", "check",
                           f"{findings} drawing findings haven't been reviewed", "Review the findings from the drawings.",
                           "drawings", actions=(_link("drawings", "Review findings"),)))
    return items


ROBE_NAME = re.compile(r"\b(?:robes?|wir|walk[\s-]*in(?:\s+robe)?)\b", re.I)
BEDROOM_NAME = re.compile(r"\b(?:bed(?:room)?s?|master)\b", re.I)


def _robe_items(plan, rooms):
    """A robe or walk-in robe that shares a wall with exactly one bedroom is part of that bedroom: its floor, walls and
    windows count with it and it gets no outlet (house research, 2026-11-28). Asked as a join, recommended."""
    from shapely.geometry import Polygon
    shapes = {}
    for outline in plan.get("outlines") or []:
        points = outline.get("points") or []
        if len(points) >= 3:
            try:
                shapes.setdefault(outline.get("room_id"), []).append((outline.get("page"), Polygon(points).buffer(0)))
            except (TypeError, ValueError):
                continue
    by_bedroom = {}
    for robe in rooms:
        if robe.get("join_id") or not ROBE_NAME.search(str(robe.get("name") or "")) or robe.get("room_id") not in shapes:
            continue
        touching = []
        for bed in rooms:
            if (bed is robe or bed.get("join_id") or not BEDROOM_NAME.search(str(bed.get("name") or ""))
                    or ROBE_NAME.search(str(bed.get("name") or "")) or bed.get("kind") != "comfort"
                    or str(bed.get("level") or "").casefold() != str(robe.get("level") or "").casefold()):
                continue
            shared = 0.0
            for page, robe_shape in shapes[robe["room_id"]]:
                for bed_page, bed_shape in shapes.get(bed.get("room_id"), []):
                    if page != bed_page or robe_shape.is_empty or bed_shape.is_empty:
                        continue
                    # Walls are drawn between the outlines: "sharing a wall" is the robe's outline within a wall's
                    # thickness of the bedroom's, along at least a third of the robe's side.
                    reach = 0.08 * math.sqrt(bed_shape.area)
                    shared = max(shared, robe_shape.boundary.intersection(bed_shape.buffer(reach)).length
                                 / max(1e-9, math.sqrt(robe_shape.area)))
            if shared >= 0.33:
                touching.append(bed)
        if len(touching) != 1:
            continue
        bed = touching[0]
        # A "robe" bigger than its "bedroom" means the names are swapped (house18: "Wir" 28.6 m², "Master bedroom"
        # 3.6 m²); joining would make the bedroom a robe. Not offered.
        if (_area(robe) or 0) >= (_area(bed) or float("inf")):
            continue
        by_bedroom.setdefault(bed["room_id"], (bed, []))[1].append(robe)
    items = []
    for bed, robes in by_bedroom.values():
        names = _join([_name(robe) for robe in robes])
        items.append(_item(f"robe:{bed['room_id']}", "robe_join", "check",
                           f"{names} open{'s' if len(robes) == 1 else ''} off {_name(bed)}",
                           "A robe is part of the bedroom it opens off: its floor, walls and windows count with the "
                           "bedroom, and it gets no outlet of its own.", "rooms", robes[0],
                           # The joined room keeps the bedroom's use and stays cooled, whichever piece is larger.
                           actions=({"type": "join", "room_ids": [bed["room_id"], *[robe["room_id"] for robe in robes]],
                                     "name": str(bed.get("name") or ""), "use": (bed.get("use") or {}).get("taxonomy_id") or None, "cooled": True,
                                     "label": f"Join {'it' if len(robes) == 1 else 'them'} into {_name(bed)}"},
                                    _link("rooms", "Check on the plan"))))
    return items


def _glass_items(root):
    """One item for the windows left off the load: glass is about half a house's cooling peak, so missing glass is the
    largest error a traced plan can carry (house research, 2026-11-28)."""
    from ai import house_windows
    try:
        saved = json.loads((root / "house_windows.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    coverage = (saved.get("glass_coverage") if isinstance(saved, dict) else None) or {}
    causes = coverage.get("by_cause") or {}
    count = int(coverage.get("lost_windows") or 0)
    if not count:
        return []
    lost = float(coverage.get("lost_m2") or 0)
    parts = [f"{row['windows']} ({row['m2']:.1f} m²): {house_windows.LOSS_CAUSES.get(cause, cause)}"
             for cause, row in sorted(causes.items(), key=lambda item: -float(item[1].get("m2") or 0))]
    title = (f"{_plural(count, 'window', 'windows')} ({lost:.1f} m² of glass) "
             f"{'isn' if count == 1 else 'aren'}'t in the load")
    return [_item("glass-missing", "glass_missing", "changes_number", title,
                  "; ".join(parts) + f". {float(coverage.get('in_load_m2') or 0):.1f} m² of glass is in the load.",
                  "rooms", actions=(_link("rooms", "Check on the plan"),))]


def get(web, project):
    """Gather each source independently; one failed source does not hide the rest."""
    failed = []

    def gather(name, call):
        try:
            return call() or {}
        except Exception:
            failed.append(name)
            return {}

    plan = gather("job_plan_service", lambda: job_plan_service.get(web, project))
    if project.get("review_dir"):
        result = gather("result_status_service", lambda: result_status_service.gather(web, project))
        drawing = gather("drawing_tasks_service", lambda: drawing_tasks_service.status(web, project))
        supervisor = gather("phase1_supervisor_service", lambda: phase1_supervisor_service.status(web, project))
        calculation = gather("calculation_service", lambda: calculation_service._JOB.status(project))
    else:
        result = drawing = supervisor = calculation = {}
    answer = _answer(plan, calculation, failed)
    from backend import result_label_service
    result_label = gather("result_label_service", lambda: result_label_service.label(project, status=plan.get("status") or {})) if project.get("review_dir") else {}
    rooms = _display_rooms(plan)
    field = "final_kw" if _number(_peak(plan.get("final_report")).get("final_design_total_kw",
                     _peak(plan.get("final_report")).get("design_total_kw"))) is not None else "phase1_kw"
    todos = _room_items(rooms, field) + _other_items(drawing, supervisor, result, rooms, plan.get("status") or {})
    if project.get("review_dir"):
        todos += _glass_items(Path(project["review_dir"]))
    todos += _robe_items(plan, rooms)
    if answer["state"] == "stale":
        phase = "full" if (plan.get("status") or {}).get("phase2_complete") else "phase1_base_room"
        todos.append(_item("stale", "update_load", "blocks", "The load is out of date",
                           "Rooms were changed after this load was calculated.", "results",
                           actions=({"type": "recalculate", "phase": phase, "label": "Update the load"},)))
    unique = {row["id"]: row for row in todos}
    todos = sorted(unique.values(), key=lambda row: (SEVERITY[row["severity"]],
                   0 if row["kind"] == "update_load" else 1,
                   -(row["impact"]["kw"] if row["impact"] else -1),
                   -(row["room"]["area_m2"] if row["room"] and row["room"]["area_m2"] is not None else -1),
                   row["title"].casefold()))
    counts = {"total": len(todos), **{key: sum(row["severity"] == key for row in todos) for key in SEVERITY},
              "by_step": {step: sum(row["step"] == step for row in todos) for step in STEPS}}
    cooled = sum(row.get("kind") == "comfort" or row.get("included") for row in rooms)
    unchecked = counts["check"]
    uncounted = max(0, cooled - answer["rooms_counted"])
    if answer["total_kw"] is None:
        level, text = "none", "No load has been calculated yet."
    elif counts["blocks"] or counts["changes_number"] or uncounted:
        # The to-do button carries the count of things to check; this line says what the number covers.
        level = "partial"
        text = (f"Partial: {_plural(uncounted, 'cooled room isn\'t', 'cooled rooms aren\'t')} counted yet"
                + (f" ({answer['not_counted_text']})." if uncounted and answer.get("not_counted_text") else ".")
                if uncounted else "Partial: some inputs could still change the number.")
    else:
        level = "complete"
        text = f"Complete: all {_plural(answer['rooms_counted'], 'cooled room', 'cooled rooms')} counted."
    if any(row.get("kind") == "typical" for row in result.get("items") or []):
        text += " Uses typical values for people, lights and equipment."
    return {"project_id": str(project.get("id") or ""), "answer": answer,
            "result_label": result_label,
            "completeness": {"level": level, "text": text}, "todos": todos, "counts": counts,
            "uses": plan.get("room_use_options") or {}, "sources_failed": failed}
