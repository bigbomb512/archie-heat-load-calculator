"""Put an answer from the "What we need to find" list into the calculation, through the input route that already exists.

Each item on the list has a kind. Kinds with an existing input route go straight into the calculation:
- room_area        -> the room's typed area (job_service.save_area_override)
- ceiling_height   -> the room's typed ceiling height (job_service.save_height_override)
- occupancy        -> the room's number of people (internal-gains override)
- lighting_load    -> the room's lighting load in watts (internal-gains override)
- equipment_rating -> that equipment item accepted with its rated power (page_extraction_service.review)
- roof_above       -> the job's answer to what is above the tenancy (job_service, applied to every roof question)
- glazing          -> the glass's U-value and SHGC for one room's windows, or for every window (glazing_answers.json,
                      applied to the windows when the calculation's proposal is prepared)
Other kinds (opening hours, exhaust, a wall's boundary, anything else) are kept as notes with their source,
and the list says they are not used by the calculation yet. Press Calculate afterwards to update the result.
"""

import json
import os
from pathlib import Path
import time

from ai.equipment_heat import printed_watts, proposal as heat_proposal

GLAZING_FILE = "glazing_answers.json"
U_RANGE = (0.5, 7.0)       # W/m²K: from triple glazing to single clear glass
SHGC_RANGE = (0.05, 0.95)

APPLIED_KINDS = {
    "room_area": {"label": "Room area", "unit": "m²", "room": True},
    "ceiling_height": {"label": "Ceiling height", "unit": "m", "room": True},
    "occupancy": {"label": "Number of people", "unit": "people", "room": True},
    "lighting_load": {"label": "Lighting load", "unit": "W", "room": True},
    "equipment_rating": {"label": "Equipment rated power", "unit": "W", "room": True, "equipment": True},
    "roof_above": {"label": "What is above the tenancy", "unit": "", "room": False, "choice": True},
    "glazing": {"label": "Glass performance (U-value and SHGC)", "unit": "", "room": "optional"},
}
NOTE_KINDS = {"opening_hours": "Opening hours", "exhaust": "Kitchen exhaust or airflow",
              "boundary": "What is beyond a wall, floor or ceiling", "other": "Other"}
KINDS = {**{key: row["label"] for key, row in APPLIED_KINDS.items()}, **NOTE_KINDS}


def options(web, project):
    """What the answer form offers: kinds, rooms (name and level), equipment items and the roof choices."""
    from backend import ai_preliminary_service, job_service, page_extraction_service
    try:
        rooms = ai_preliminary_service._proposal_for_resolution(ai_preliminary_service._paths(project), equipment=False).get("rooms", [])
    except Exception:
        rooms = []
    room_rows = sorted({(str(row.get("label")), str(row.get("level_name") or "Unassigned level"))
                        for row in rooms if isinstance(row, dict) and row.get("label")})
    equipment = []
    try:
        current = page_extraction_service.status(web, project, "equipment_appliances")
        equipment = [{"id": row["id"], "label": f"{row['value'].get('code') + ' ' if row['value'].get('code') else ''}{row['value'].get('name', '')}"}
                     for row in current.get("findings", [])]
    except Exception:
        equipment = []
    return {"kinds": [{"id": key, "label": label, "applied": key in APPLIED_KINDS, "unit": APPLIED_KINDS.get(key, {}).get("unit", "")}
                      for key, label in KINDS.items()],
            "rooms": [{"label": label, "level": level} for label, level in room_rows],
            "equipment": equipment,
            "above": [{"id": key, "label": label} for key, label in job_service.ABOVE_CHOICES.items() if key]}


def _number(value, unit_label):
    if isinstance(value, str):
        value = value.strip().replace(",", "")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter the {unit_label} as a number.") from None
    if number <= 0:
        raise ValueError(f"The {unit_label} must be above 0.")
    return number


def _room(data, rooms):
    label = " ".join(str(data.get("room") or "").split())
    match = next((row for row in rooms if row["label"].casefold() == label.casefold()), None)
    if not match:
        raise ValueError("Choose the room this answer is for.")
    return match


def _internal_gains_override(web, project, room, field, value, reviewer, note):
    from ai.internal_gains_resolution import _room_id
    from backend import internal_gains_resolution_service
    payload = {"action": "apply_override", "room_id": _room_id(room["label"], room["level"]), "field": field,
               "value": value, "reviewer": reviewer, "note": note}
    try:
        internal_gains_resolution_service.post(web, project, payload)
    except ValueError as error:
        if "stale" not in str(error).lower():
            raise
        internal_gains_resolution_service.post(web, project, {"action": "resolve"})  # bring it up to date, then apply
        internal_gains_resolution_service.post(web, project, payload)


def apply(web, project, data):
    """Apply one answer. Returns {"applied": bool, "summary": text}; raises ValueError for an unusable answer."""
    from backend import job_service, page_extraction_service
    kind = data.get("kind") or "other"
    if kind not in KINDS:
        raise ValueError("Choose what kind of answer this is.")
    reviewer = " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator"
    note = f"From the What-we-need-to-find list ({data.get('source', '')})"
    if kind not in APPLIED_KINDS:
        return {"applied": False, "summary": f"Kept as a note ({KINDS[kind].lower()}); not used by the calculation yet."}
    found = options(web, project)
    if kind == "roof_above":
        choice = str(data.get("value") or "")
        if choice not in {row["id"] for row in found["above"]}:
            raise ValueError("Choose what is above the tenancy.")
        job_service.save_job_setup(project, {"above": choice, "edited_by": reviewer})
        job_service.apply_roof_answer(web, project)
        label = next(row["label"] for row in found["above"] if row["id"] == choice)
        return {"applied": True, "summary": f"Above the tenancy: {label}."}
    if kind == "glazing":
        return _save_glazing(project, data, found["rooms"], reviewer)
    room = _room(data, found["rooms"])
    where = room["label"]
    if kind == "room_area":
        area = _number(data.get("value"), "area in m²")
        job_service.save_area_override(project, {"label": room["label"], "level_name": room["level"], "area_m2": area, "edited_by": reviewer})
        return {"applied": True, "summary": f"{where} area set to {area:g} m²."}
    if kind == "ceiling_height":
        metres = _number(data.get("value"), "ceiling height in metres")
        job_service.save_height_override(project, {"label": room["label"], "level_name": room["level"],
                                                   "ceiling_height_mm": round(metres * 1000), "edited_by": reviewer})
        return {"applied": True, "summary": f"{where} ceiling height set to {metres:g} m."}
    if kind == "occupancy":
        people = _number(data.get("value"), "number of people")
        _internal_gains_override(web, project, room, "occupancy_count", people, reviewer, note)
        return {"applied": True, "summary": f"{where}: {people:g} people."}
    if kind == "lighting_load":
        watts = _number(data.get("value"), "lighting load in watts")
        _internal_gains_override(web, project, room, "lighting_load_w", watts, reviewer, note)
        return {"applied": True, "summary": f"{where} lighting load set to {watts:g} W."}
    # equipment_rating: accept that equipment item with this rated power, in this room.
    finding_id = str(data.get("equipment_id") or "")
    current = page_extraction_service.status(web, project, "equipment_appliances")
    finding = next((row for row in current.get("findings", []) if row["id"] == finding_id), None)
    if not finding:
        raise ValueError("Choose the equipment item this rating is for.")
    text = str(data.get("value") or "")
    watts = printed_watts(text) or _number(text, "rated power in watts (or kW with its unit)")
    value = {**finding["value"], **(finding.get("decided_value") or {}), "room": room["label"], "rated_input_w": watts}
    if value.get("heat_to_space_factor") in (None, ""):
        value["heat_to_space_factor"] = heat_proposal(value)["heat_to_space_factor"]
    if value["heat_to_space_factor"] is None:
        raise ValueError("This item has no heat-to-room factor; set it in the equipment list.")
    page_extraction_service.review(web, project, {"kind": "equipment_appliances", "finding_id": finding_id, "decision": "accepted",
                                                  "value": value, "reviewer": reviewer})
    name = f"{value.get('code') + ' ' if value.get('code') else ''}{value.get('name', '')}"
    return {"applied": True, "summary": f"{name} in {where}: {watts:g} W each × {value.get('quantity') or 1} × {value['heat_to_space_factor']:g} to the room."}


def _in_range(value, low, high, label):
    number = _number(value, label)
    if not low <= number <= high:
        raise ValueError(f"The {label} must be between {low:g} and {high:g}.")
    return round(number, 3)


def _save_glazing(project, data, rooms, reviewer):
    """Glass performance for one room's windows (room given) or for every window (no room)."""
    u_value = _in_range(data.get("u_value_w_m2k"), *U_RANGE, "U-value (W/m²K)")
    shgc = _in_range(data.get("shgc"), *SHGC_RANGE, "SHGC")
    room = _room(data, rooms)["label"] if str(data.get("room") or "").strip() else ""
    root = Path(project["review_dir"])
    path = root / GLAZING_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"rooms": {}, "all": None}
    record = {"u_value_w_m2k": u_value, "shgc": shgc, "glass": " ".join(str(data.get("value") or "").split())[:160],
              "source": str(data.get("source") or ""), "by": reviewer, "at": time.time()}
    if room:
        stored.setdefault("rooms", {})[room] = record
    else:
        stored["all"] = record
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(staging, path)
    target = f"{room}'s windows" if room else "every window"
    return {"applied": True, "summary": f"Glass for {target}: U {u_value:g} W/m²K, SHGC {shgc:g}"
                                        + (f" ({record['glass']})" if record["glass"] else "") + "."}


def apply_glazing(root, proposal):
    """Give each window the answered glass performance: its room's answer, else the answer for every window.

    Windows without an answer keep the preliminary assumption-pack glazing."""
    path = Path(root) / GLAZING_FILE
    if not path.is_file():
        return proposal
    stored = json.loads(path.read_text(encoding="utf-8"))
    rooms = {str(label).casefold(): row for label, row in (stored.get("rooms") or {}).items()}
    for opening in proposal.get("openings", []) if isinstance(proposal, dict) else []:
        if not isinstance(opening, dict):
            continue
        answer = rooms.get(str(opening.get("owner_room_label", "")).casefold()) or stored.get("all")
        if not answer:
            continue
        opening["u_value_w_m2k"], opening["shgc"] = answer["u_value_w_m2k"], answer["shgc"]
        opening["glazing_source"] = (f"Answered by the operators ({answer.get('source') or 'source not given'})"
                                     + (f": {answer['glass']}" if answer.get("glass") else ""))
        opening["assumptions"] = [item for item in opening.get("assumptions", []) if item != "preliminary_glazing_profile"]
        opening["rationale"] = "Window geometry as entered; glass U-value and SHGC from the operators' answer."
    return proposal
