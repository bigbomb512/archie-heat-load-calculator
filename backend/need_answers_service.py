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
- construction     -> the U-value of the external walls or the exposed roof, for one room or for every room
                      (construction_answers.json): a construction from the imported handbook table
                      (ai/construction_u_values.py) or a U-value typed from another source
- unconditioned_temperature -> the design temperature of the unconditioned space beyond walls marked "Unconditioned
                      space" on the Walls tab, for one room or every room (unconditioned_answers.json); without it those
                      walls are not counted and the Results tab says so
- exhaust          -> a room's kitchen exhaust rate (L/s) and how the exhausted air is replaced (exhaust_answers.json,
                      passed to the calculation model; see EXHAUST_METHODS)
- opening_hours    -> the hours a room (or every room) is open on weekdays, Saturdays and Sundays/holidays
                      (hours_answers.json): its people, lighting, equipment and outside-air schedule
- boundary         -> what is beyond a wall: recorded, and set on the Walls tab, which is what the calculation reads
                      (a need names a wall in words; matching it to one traced wall is left to the operator)
Other kinds are kept as notes with their source,
and the list says they are not used by the calculation yet. Press Calculate afterwards to update the result.
"""

import json
import os
from pathlib import Path
import time

from ai.equipment_heat import printed_watts, proposal as heat_proposal

GLAZING_FILE = "glazing_answers.json"
CONSTRUCTION_FILE = "construction_answers.json"
CONSTRUCTION_SURFACES = {"wall": "walls", "roof": "exposed roof"}   # walls: outside, and to unconditioned spaces
UNCONDITIONED_FILE = "unconditioned_answers.json"
UNCONDITIONED_RANGE = (10.0, 60.0)     # °C: from a cool basement to a sun-baked roof-top plant room
EXHAUST_FILE = "exhaust_answers.json"
# How the air a kitchen hood exhausts is replaced. "through_space" is the default (user decision 2026-10-08), also
# used, and labelled as assumed, when the method isn't known.
EXHAUST_METHODS = {
    "through_space": "Replaced through the air-conditioned space (no dedicated make-up air)",
    "untempered_makeup": "Dedicated make-up air at the hood, not cooled",
    "tempered_makeup": "A separately cooled (tempered) make-up air unit",
}
EXHAUST_RANGE_LPS = (10.0, 20000.0)
HOURS_FILE = "hours_answers.json"
HOURS_DAYS = (("weekday", "Weekdays"), ("saturday", "Saturday"), ("sunday", "Sunday and holidays"))
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
    "construction": {"label": "Wall or roof construction (U-value)", "unit": "W/m²K", "room": "optional"},
    "unconditioned_temperature": {"label": "Temperature of an unconditioned space beyond a wall", "unit": "°C", "room": "optional"},
    "exhaust": {"label": "Kitchen exhaust rate and make-up air", "unit": "L/s", "room": True},
    "opening_hours": {"label": "Opening hours", "unit": "", "room": "optional"},
}
NOTE_KINDS = {"boundary": "What is beyond a wall (set on the Walls tab)", "other": "Other"}
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
            "above": [{"id": key, "label": label} for key, label in job_service.ABOVE_CHOICES.items() if key],
            "exhaust_methods": [{"id": key, "label": label} for key, label in EXHAUST_METHODS.items()],
            "constructions": _construction_choices()}


def _construction_choices():
    """The imported handbook's wall and roof constructions as answer options (empty lists without the table)."""
    from ai import construction_u_values
    table = construction_u_values.load_table()
    return {surface: construction_u_values.choices(table, surface) for surface in CONSTRUCTION_SURFACES}


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
    if kind == "boundary":
        return {"applied": False, "summary": "Recorded. Set the walls on the Walls tab: that is what the calculation uses.", "open_tab": "walls"}
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
    if kind == "construction":
        return _save_construction(project, data, found["rooms"], reviewer)
    if kind == "unconditioned_temperature":
        return _save_unconditioned(project, data, found["rooms"], reviewer)
    if kind == "exhaust":
        return _save_exhaust(project, data, found["rooms"], reviewer)
    if kind == "opening_hours":
        return _save_hours(project, data, found["rooms"], reviewer)
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


def _save_construction(project, data, rooms, reviewer):
    """The U-value of the external walls or the exposed roof: for one room (room given) or every room (no room).

    Either a construction from the imported handbook table (construction_id) or a U-value typed from another source."""
    from ai import construction_u_values
    surface = str(data.get("surface") or "")
    if surface not in CONSTRUCTION_SURFACES:
        raise ValueError("Choose whether this is the walls or the roof.")
    chosen = str(data.get("construction_id") or "")
    if chosen:
        row = construction_u_values.find(construction_u_values.load_table(), chosen)
        if not row or row["surface"] != surface:
            raise ValueError("Choose one of the listed constructions for this surface.")
        u_value, construction = row["u_value_w_m2k"], row["name"]
        reference = f"{construction_u_values.SOURCE}, {row['section'].lower()}"
    else:
        u_value = _in_range(data.get("u_value_w_m2k"), *construction_u_values.U_RANGE, "U-value (W/m²K)")
        construction = " ".join(str(data.get("value") or "").split())[:160]
        if not construction:
            raise ValueError("Describe the construction the U-value is for.")
        reference = ""
    room = _room(data, rooms)["label"] if str(data.get("room") or "").strip() else ""
    path = Path(project["review_dir"]) / CONSTRUCTION_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    record = {"u_value_w_m2k": u_value, "construction_id": chosen, "construction": construction, "reference": reference,
              "source": str(data.get("source") or ""), "by": reviewer, "at": time.time()}
    target = stored.setdefault(surface, {"all": None, "rooms": {}})
    if room:
        target.setdefault("rooms", {})[room] = record
    else:
        target["all"] = record
    _write_answers(path, stored)
    where = f"{room}'s {CONSTRUCTION_SURFACES[surface]}" if room else f"every room's {CONSTRUCTION_SURFACES[surface]}"
    return {"applied": True, "summary": f"{where[0].upper()}{where[1:]}: {construction}, U {u_value:g} W/m²K"
                                        + (" (AIRAH Technical Handbook)." if chosen else ".")}


def apply_constructions(root, proposal):
    """Give each external wall and exposed roof the answered U-value: its room's answer, else the answer for every room.

    Surfaces without an answer keep the assumption pack's preliminary U-value."""
    path = Path(root) / CONSTRUCTION_FILE
    if not path.is_file():
        return proposal
    stored = json.loads(path.read_text(encoding="utf-8"))
    for surface in proposal.get("surfaces", []) if isinstance(proposal, dict) else []:
        if not isinstance(surface, dict) or surface.get("physical_type") not in CONSTRUCTION_SURFACES:
            continue
        answers = stored.get(surface["physical_type"]) or {}
        rooms = {str(label).casefold(): row for label, row in (answers.get("rooms") or {}).items()}
        answer = rooms.get(str(surface.get("owner_room_label", "")).casefold()) or answers.get("all")
        if not answer:
            continue
        surface["u_value_w_m2k"] = answer["u_value_w_m2k"]
        surface["construction_id"] = answer.get("construction_id") or f"answered-{surface['physical_type']}"
        surface["construction_source"] = (f"Answered by the operators ({answer.get('source') or 'source not given'}): "
                                          f"{answer.get('construction', '')}" + (f"; {answer['reference']}" if answer.get("reference") else ""))
    return proposal


def _save_unconditioned(project, data, rooms, reviewer):
    """The design temperature of the unconditioned space beyond a room's (or every room's) unconditioned walls."""
    temperature = _in_range(data.get("value"), *UNCONDITIONED_RANGE, "temperature (°C)")
    room = _room(data, rooms)["label"] if str(data.get("room") or "").strip() else ""
    path = Path(project["review_dir"]) / UNCONDITIONED_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"all": None, "rooms": {}}
    record = {"temperature_c": temperature, "space": " ".join(str(data.get("note") or "").split())[:160],
              "source": str(data.get("source") or ""), "by": reviewer, "at": time.time()}
    if room:
        stored.setdefault("rooms", {})[room] = record
    else:
        stored["all"] = record
    _write_answers(path, stored)
    where = f"{room}'s" if room else "Every room's"
    return {"applied": True, "summary": f"{where} walls to unconditioned spaces: {temperature:g} °C beyond them."}


def unconditioned_temperature(root, room_label):
    """The answered temperature (°C) and its record beyond a room's unconditioned walls: its own answer, else the
    answer for every room; (None, None) when not answered."""
    path = Path(root) / UNCONDITIONED_FILE
    if not path.is_file():
        return None, None
    stored = json.loads(path.read_text(encoding="utf-8"))
    rooms = {str(label).casefold(): row for label, row in (stored.get("rooms") or {}).items()}
    record = rooms.get(str(room_label or "").casefold()) or stored.get("all")
    return (float(record["temperature_c"]), record) if record else (None, None)


def _write_answers(path, stored):
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(staging, path)


def _save_exhaust(project, data, rooms, reviewer):
    """A room's kitchen exhaust rate and how its air is replaced (the default method when not given)."""
    room = _room(data, rooms)["label"]
    rate = _in_range(str(data.get("value") or "").lower().replace("l/s", ""), *EXHAUST_RANGE_LPS, "exhaust rate (L/s)")
    method = str(data.get("method") or "")
    if method and method not in EXHAUST_METHODS:
        raise ValueError("Choose how the exhausted air is replaced.")
    path = Path(project["review_dir"]) / EXHAUST_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"rooms": {}}
    stored.setdefault("rooms", {})[room] = {"lps": rate, "method": method or "through_space", "method_assumed": not method,
                                            "source": str(data.get("source") or ""), "by": reviewer, "at": time.time()}
    _write_answers(path, stored)
    how = EXHAUST_METHODS[method or "through_space"].lower()
    effect = {"through_space": "its replacement air is counted as outside air through the air conditioning",
              "untempered_makeup": "it adds no cooling load to this system",
              "tempered_makeup": "its load belongs to the make-up air unit, not this system"}[method or "through_space"]
    return {"applied": True, "summary": f"{room} exhaust {rate:g} L/s, {how}{' (assumed: method not given)' if not method else ''}; {effect}."}


def process_exhaust(root):
    """Answered kitchen exhaust per room, for the calculation model: {room name (lower case): answer}."""
    path = Path(root) / EXHAUST_FILE
    if not path.is_file():
        return {}
    stored = json.loads(path.read_text(encoding="utf-8"))
    return {str(label).casefold(): dict(row) for label, row in (stored.get("rooms") or {}).items()}


def parse_hours(text):
    """"11-22", "11:30-22", "18-2" (past midnight) or "closed" -> 24 hourly values (1 open, 0 closed).

    Whole hours: a part-hour counts as open (open from the hour it starts, until the hour it ends is over)."""
    text = " ".join(str(text or "").lower().split())
    if text in {"closed", "close", "shut", "0"}:
        return [0.0] * 24
    import re
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(?:-|–|to)\s*(\d{1,2})(?::(\d{2}))?", text)
    if not match:
        raise ValueError('Enter hours like "11-22" (24-hour clock), "18-2" past midnight, or "closed".')
    start, start_min, end, end_min = int(match[1]), int(match[2] or 0), int(match[3]), int(match[4] or 0)
    if not (0 <= start <= 24 and 0 <= end <= 24 and start_min < 60 and end_min < 60):
        raise ValueError("Hours must be between 0 and 24.")
    start, end = start % 24, (end + (1 if end_min else 0)) % 24
    if start == end:
        return [1.0] * 24          # open around the clock
    hours = range(start, end) if start < end else [*range(start, 24), *range(0, end)]
    return [1.0 if hour in set(hours) else 0.0 for hour in range(24)]


def _save_hours(project, data, rooms, reviewer):
    room = _room(data, rooms)["label"] if str(data.get("room") or "").strip() else ""
    hours = data.get("hours") if isinstance(data.get("hours"), dict) else {}
    profiles, said = {}, []
    for day, label in HOURS_DAYS:
        text = str(hours.get(day) or "").strip()
        if not text:
            raise ValueError(f"Enter the {dict(weekday='weekday', saturday='Saturday', sunday='Sunday and holiday')[day]} hours (or \"closed\").")
        profiles[day] = parse_hours(text)
        said.append(f"{label} {text}")
    profiles["holiday"] = list(profiles["sunday"])
    path = Path(project["review_dir"]) / HOURS_FILE
    stored = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"rooms": {}, "all": None}
    record = {"hours": {day: str(hours.get(day)).strip() for day, _label in HOURS_DAYS}, "profiles": profiles,
              "source": str(data.get("source") or ""), "by": reviewer, "at": time.time()}
    if room:
        stored.setdefault("rooms", {})[room] = record
    else:
        stored["all"] = record
    _write_answers(path, stored)
    return {"applied": True, "summary": f"Opening hours for {room or 'every room'}: {'; '.join(said)}."}


def apply_hours(root, proposal):
    """Give each room its answered opening hours as its schedule (a room's own answer, else the one for every room)."""
    path = Path(root) / HOURS_FILE
    if not path.is_file():
        return proposal
    stored = json.loads(path.read_text(encoding="utf-8"))
    rooms = {str(label).casefold(): row for label, row in (stored.get("rooms") or {}).items()}
    for room in proposal.get("rooms", []) if isinstance(proposal, dict) else []:
        if not isinstance(room, dict):
            continue
        answer = rooms.get(str(room.get("label", "")).casefold()) or stored.get("all")
        if answer:
            room["schedules"] = json.loads(json.dumps(answer["profiles"]))
            room["schedule_source"] = f"Opening hours answered by the operators ({answer.get('source') or 'source not given'})"
    return proposal
