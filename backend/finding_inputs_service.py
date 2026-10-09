"""Accepted PDF review findings that set calculation inputs, the way an answered need does.

- occupancy_seating  a room's seat or occupant count -> that room's number of people;
- lighting_evidence  light fittings -> the room's lighting load, the total of its accepted fittings (quantity x W);
                     a fitting without a quantity or wattage is recorded but not counted, and the summary says so;
- equipment_evidence an equipment item -> the matching item of the equipment list the calculation reads (by code,
                     else by name), accepted with the drawing's quantity and rating, or a typical rating until a
                     spec sheet is in (labelled as typical on the Results tab).

A finding names its room in the app's format ("room-use:<level>:<label>"); when it names none, or a zone the app
doesn't know, the operator picks the room on accepting. Press Calculate afterwards to update the result.
"""

import re

from ai.equipment_heat import proposal as heat_proposal
from ai.internal_gains_resolution import _room_id
from ai.page_extraction import normalise_name

INPUT_FINDINGS = {"occupancy_seating": "occupancy", "lighting_evidence": "lighting", "equipment_evidence": "equipment"}


def applies(finding):
    return finding.get("subskill_id") in INPUT_FINDINGS and finding.get("field") == INPUT_FINDINGS[finding["subskill_id"]]


def rooms(web, project):
    from backend import need_answers_service
    try:
        return need_answers_service.options(web, project).get("rooms", [])
    except Exception:
        return []


def mapped_room(value, room_rows):
    """The app room a finding names, or None (no room, or a zone the app doesn't know)."""
    room_id = value.get("room_id") if isinstance(value, dict) else None
    return next((row for row in room_rows if room_id and _room_id(row["label"], row["level"]) == room_id), None)


def _pick_room(value, room_label, room_rows):
    if room_label:
        match = next((row for row in room_rows if row["label"].casefold() == str(room_label).casefold()), None)
        if not match:
            raise ValueError("Choose one of the job's rooms.")
        return match
    match = mapped_room(value, room_rows)
    if not match:
        raise ValueError("This finding doesn't name one of the job's rooms. Choose the room it applies to.")
    return match


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def apply(web, project, finding, value, room_label, reviewer, accepted_lighting=()):
    """Set the input an accepted finding gives. Returns {"applied", "summary", "room"}; raises ValueError when the
    finding needs a room it doesn't name. accepted_lighting = [(value, room label)] of the room's other accepted
    fittings, so the room's lighting is their total with this one."""
    from backend import need_answers_service
    kind = INPUT_FINDINGS[finding["subskill_id"]]
    room = _pick_room(value, room_label, rooms(web, project))
    note = f"From an accepted PDF review finding ({finding['id']})"
    if kind == "occupancy":
        count = _number(value.get("count"))
        if not count:
            return {"applied": False, "room": room["label"], "summary": "Recorded; it has no count, so the room's people are unchanged."}
        need_answers_service._internal_gains_override(web, project, room, "occupancy_count", count, reviewer, note)
        return {"applied": True, "room": room["label"], "summary": f"{room['label']}: {count:g} people."}
    if kind == "lighting":
        fittings = [*[(other, label) for other, label in accepted_lighting if label == room["label"]], (value, room["label"])]
        counted = [(other, _number(other.get("quantity")) * _number(other.get("wattage_w"))) for other, _ in fittings
                   if _number(other.get("quantity")) and _number(other.get("wattage_w"))]
        unrated = [other for other, _ in fittings if not (_number(other.get("quantity")) and _number(other.get("wattage_w")))]
        missing = (f" {len(unrated)} accepted fitting type(s) here have no quantity or wattage yet and aren't counted."
                   if unrated else "")
        if not counted:
            return {"applied": False, "room": room["label"],
                    "summary": f"Recorded; no accepted fitting in {room['label']} has both a quantity and a wattage yet, so its lighting is unchanged."}
        total = sum(watts for _, watts in counted)
        need_answers_service._internal_gains_override(web, project, room, "lighting_load_w", total, reviewer, note)
        return {"applied": True, "room": room["label"],
                "summary": f"{room['label']} lighting: {total:g} W from {len(counted)} accepted fitting type(s).{missing}"}
    return _apply_equipment(web, project, value, room, reviewer)


def _code(value):
    """The printed code an equipment finding carries (e.g. "E06" from "pdf-…:004:E06"), or ""."""
    tail = str(value.get("equipment_id") or "").rsplit(":", 1)[-1]
    return tail.replace(" ", "").upper() if re.fullmatch(r"[A-Za-z]{1,3}-?\d{1,3}[A-Za-z]?", tail) else ""


def _apply_equipment(web, project, value, room, reviewer):
    from backend import page_extraction_service
    listed = page_extraction_service.status(web, project, "equipment_appliances").get("findings", [])
    code, name = _code(value), normalise_name(value.get("name"))
    match = next((row for row in listed if code and str(row["value"].get("code", "")).replace(" ", "").upper() == code), None)
    match = match or next((row for row in listed if name and normalise_name(row["value"].get("name")) == name), None)
    if not match:
        return {"applied": False, "room": room["label"],
                "summary": "Recorded; it isn't in the equipment list read from the pages, so it isn't counted. Answer its rating in the needs list."}
    # Name, code and size come from the page reading; a rating or factor entered earlier (e.g. from a spec sheet) is
    # kept unless this finding gives one.
    earlier = match.get("decided_value") or {}
    item = {**match["value"], "room": room["label"]}
    if _number(value.get("quantity")):
        item["quantity"] = int(value["quantity"])
    typical = heat_proposal(item)
    if typical.get("not_equipment"):
        return {"applied": False, "room": room["label"], "summary": f"Recorded; not counted as equipment heat: {typical['not_equipment']}."}
    if _number(value.get("rated_input_w")):
        item["rated_input_w"], rating = float(value["rated_input_w"]), "rating from the drawings"
    elif _number(earlier.get("rated_input_w")):
        item["rated_input_w"], rating = float(earlier["rated_input_w"]), "rating entered earlier"
    else:
        item["rated_input_w"] = typical["rated_input_w"]
        rating = "printed rating" if typical.get("rated_source") == "printed on the drawings" else "typical rating"
    factor = next((number for number in (value.get("heat_to_space_factor"), earlier.get("heat_to_space_factor"))
                   if isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number <= 1), None)
    item["heat_to_space_factor"] = float(factor) if factor is not None else typical["heat_to_space_factor"]
    if not item["rated_input_w"] or item["heat_to_space_factor"] is None:
        return {"applied": False, "room": room["label"],
                "summary": "Recorded; it has no rating or heat-to-room factor yet, so it isn't counted. Answer its rating in the needs list."}
    page_extraction_service.review(web, project, {"kind": "equipment_appliances", "finding_id": match["id"], "decision": "accepted",
                                                  "value": item, "reviewer": reviewer})
    label = f"{item.get('code') + ' ' if item.get('code') else ''}{item.get('name', '')}".strip()
    return {"applied": True, "room": room["label"],
            "summary": f"{label} in {room['label']}: {item.get('quantity') or 1} × {item['rated_input_w']:g} W ({rating}) × {item['heat_to_space_factor']:g} to the room."}
