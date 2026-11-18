"""Quick fixes: correct what the drawing reading got wrong without reading the drawings again.

One screen lists every room (its name, use, area, people and whether it is cooled) and every room's windows (glass,
inside blinds, an awning or eave over them). Saving applies each change through the route that already exists for it,
so the calculation reads it the same way it reads the other answers:

- use        -> the room-use override (room_use_resolution_service, as the Rooms tab's use list)
- area       -> the room's typed area (job_service.save_area_override, as the Rooms tab's area form)
- people     -> the internal-gains occupancy override (need_answers_service, kind "occupancy")
- glass      -> glazing_answers.json for the room's windows (need_answers_service, kind "glazing"), or removed
- cooled     -> quick_fixes.json "rooms"; calculation_service uses it for the room selection of every calculation
- name       -> quick_fixes.json "rooms": the name shown on screens and in the report. It is a display name: the
                drawing's label stays the room's identity, so earlier answers keyed by it still apply, and the room's
                use and people are set in their own columns.
- blinds and shading -> quick_fixes.json "windows", given to the room's windows when the calculation's proposal is
                prepared (apply_windows), where the glass's inside-blind credit (IAC) and the awning's shadow are worked.

Saving then updates the load; the drawings aren't read again.
"""
import json
import os
from pathlib import Path
import time

FILE = "quick_fixes.json"
NAME_LIMIT = 80
PEOPLE_RANGE = (0, 5000)
SHADE_KINDS = ("awning", "verandah", "eave", "balcony", "canopy")
BLINDS = {
    "none": ("No inside blinds", None),
    "venetian_light": ("Venetian blinds, light colour", {"kind": "venetian", "colour": "light"}),
    "venetian_medium": ("Venetian blinds, medium colour", {"kind": "venetian", "colour": "medium"}),
    "roller_white": ("Roller blind, white and opaque", {"kind": "roller", "colour": "white", "opacity": "opaque"}),
    "roller_dark": ("Roller blind, dark and opaque", {"kind": "roller", "colour": "dark", "opacity": "opaque"}),
    "roller_translucent": ("Roller blind, translucent", {"kind": "roller", "colour": "light", "opacity": "translucent"}),
}
GLASS_PRESETS = {
    "nathers_hinged": {"label": "Aluminium single clear, hinged or awning (NatHERS default)", "u_value_w_m2k": 6.7, "shgc": 0.57,
                       "source": "NatHERS default window ALM-001-01 A (NatHERS fact sheet; NSW Planning Portal BASIX default window data)"},
    "nathers_sliding": {"label": "Aluminium single clear, fixed or sliding (NatHERS default)", "u_value_w_m2k": 6.7, "shgc": 0.70,
                        "source": "NatHERS default window ALM-002-01 A (NatHERS fact sheet; NSW Planning Portal BASIX default window data)"},
}


def _root(project):
    return Path(project["review_dir"])


def read(root):
    path = Path(root) / FILE
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    return {"rooms": dict(stored.get("rooms") or {}), "windows": dict(stored.get("windows") or {})}


def _write(root, stored):
    path = Path(root) / FILE
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps({"schema_version": 1, **stored}, indent=2), encoding="utf-8")
    os.replace(staging, path)


def cooled_choices(root):
    """{room key: bool} for rooms an operator marked cooled or not cooled."""
    return {key: row["cooled"] for key, row in read(root)["rooms"].items() if isinstance(row.get("cooled"), bool)}


def display_names(root):
    """{room key: name} for rooms an operator renamed."""
    return {key: row["name"] for key, row in read(root)["rooms"].items() if row.get("name")}


def apply_windows(root, proposal):
    """Give each window its room's answered inside blind and awning or eave (or the answer for every window)."""
    windows = read(root)["windows"]
    if not windows:
        return proposal
    rooms = {str(label).casefold(): row for label, row in windows.items() if label != "*"}
    for opening in proposal.get("openings", []) if isinstance(proposal, dict) else []:
        if not isinstance(opening, dict):
            continue
        # The room's own answer wins field by field over the answer for every window.
        from ai.room_use_resolution import base_label
        label = str(opening.get("owner_room_label", ""))
        own = rooms.get(label.casefold()) or rooms.get(base_label(label).casefold())   # an answer for the merged name
        answer = {**(windows.get("*") or {}), **(own or {})}
        if "blinds" in answer:
            opening["shading_basis"] = "user_edit"
            shade = BLINDS.get(answer["blinds"], ("", None))[1]
            if shade:
                opening["internal_shade"] = {**shade, "evidence": ["Entered on the quick-fix screen"]}
            else:
                opening.pop("internal_shade", None)
        if "shade" in answer:
            opening["shading_basis"] = "user_edit"
            if answer["shade"]:
                # Its shadow is worked from its depth hour by hour (ai_preliminary); the shading category stays as it
                # was, so the shade isn't counted twice.
                opening["shade"] = {**answer["shade"], "evidence": ["Entered on the quick-fix screen"]}
            else:
                opening.pop("shade", None)
    return proposal


# ----- reading the screen ----------------------------------------------------------------------------------------

def _windows_by_room(root):
    """Each room's windows as the last calculation used them: count, glass area, glass, blinds and shade."""
    try:
        current = json.loads((Path(root) / "ai_preliminary_input_set.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rooms = {}
    for room in ((current.get("material") or {}).get("hourly_load_model") or {}).get("rooms") or []:
        for glass in (room.get("cooling_load") or {}).get("glazing_surfaces") or []:
            window = glass.get("window") or {}
            row = rooms.setdefault(room.get("name", ""), {"count": 0, "area_m2": 0.0, "glass": set(), "blinds": set(), "shade": set()})
            row["count"] += 1
            row["area_m2"] += float(glass.get("explicit_opening_area_m2") or 0)
            row["glass"].add(f"U {window.get('u_value_w_m2k', '?'):g}, SHGC {window.get('shgc') or window.get('solar_transmission_factor') or '?'}"
                             if isinstance(window.get("u_value_w_m2k"), (int, float)) else "not recorded")
            if glass.get("internal_shade_note"):
                row["blinds"].add(glass["internal_shade_note"])
            if glass.get("shade"):
                shade = glass["shade"]
                row["shade"].add(f"{shade.get('kind', 'shade')} {shade.get('depth_m', '?')} m deep")
    return {name: {"count": row["count"], "area_m2": round(row["area_m2"], 2), "glass": sorted(row["glass"]),
                   "blinds": sorted(row["blinds"]), "shade": sorted(row["shade"])} for name, row in rooms.items()}


def get(web, project):
    from backend import job_plan_service
    root = _root(project)
    plan = job_plan_service.get(web, project)
    fixes = read(root)
    try:
        glazing = json.loads((root / "glazing_answers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        glazing = {}
    windows = _windows_by_room(root)
    rooms = []
    for row in plan.get("rooms") or []:
        if not isinstance(row, dict) or not row.get("room_id"):
            continue
        fix = fixes["rooms"].get(row["room_id"], {})
        occupancy = row.get("occupancy") or {}
        rooms.append({
            "room_id": row["room_id"], "label": row.get("name", ""), "name": fix.get("name") or row.get("name", ""),
            "level": row.get("level") or "", "area_m2": row.get("area_m2"), "area_label": row.get("area_label", ""),
            "use_id": (row.get("use") or {}).get("taxonomy_id") or "", "use_label": (row.get("use") or {}).get("taxonomy_label") or "",
            "people": occupancy.get("value") if isinstance(occupancy, dict) else None,
            "kind": row.get("kind", ""), "can_cool": row.get("kind") == "comfort" or isinstance(fix.get("cooled"), bool),
            "cooled": fix["cooled"] if isinstance(fix.get("cooled"), bool) else bool(row.get("included")),
            "cooled_by_operator": isinstance(fix.get("cooled"), bool), "state": row.get("state", ""),
            "windows": windows.get(row.get("name", ""), {"count": 0, "area_m2": 0.0, "glass": [], "blinds": [], "shade": []}),
            "window_fix": {**fixes["windows"].get(row.get("name", ""), {}),
                           **({"glass": (glazing.get("rooms") or {}).get(row.get("name", ""))} if (glazing.get("rooms") or {}).get(row.get("name", "")) else {})},
        })
    return {"rooms": rooms, "uses": plan.get("room_use_options") or {},
            "blinds": [{"id": key, "label": label} for key, (label, _shade) in BLINDS.items()],
            "shade_kinds": list(SHADE_KINDS),
            "glass_presets": [{"id": key, **value} for key, value in GLASS_PRESETS.items()],
            "every_window": {**fixes["windows"].get("*", {}), **({"glass": glazing["all"]} if glazing.get("all") else {})},
            "status": plan.get("status") or {}}


# ----- saving ----------------------------------------------------------------------------------------------------

def _number(value, label, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Enter the {label} as a number.") from None
    if not low <= number <= high:
        raise ValueError(f"The {label} must be between {low:g} and {high:g}.")
    return number


def _check_glass(glass):
    """(u, shgc, text, source) for a glass answer, None to remove the answer."""
    if glass in (None, "", "drawings"):
        return None
    if not isinstance(glass, dict):
        raise ValueError("Choose the glass.")
    preset = GLASS_PRESETS.get(str(glass.get("preset") or ""))
    if preset:
        return preset["u_value_w_m2k"], preset["shgc"], preset["label"], preset["source"]
    from backend.need_answers_service import SHGC_RANGE, U_RANGE
    u_value = round(_number(glass.get("u_value_w_m2k"), "glass U-value (W/m²K)", *U_RANGE), 3)
    shgc = round(_number(glass.get("shgc"), "glass SHGC", *SHGC_RANGE), 3)
    source = " ".join(str(glass.get("source") or "").split())[:160]
    if not source:
        raise ValueError("Say where the glass U-value and SHGC come from (window schedule, certificate or data sheet).")
    return u_value, shgc, f"U {u_value:g}, SHGC {shgc:g}", source


def _check_shade(shade):
    """"drawings" (clear the answer), None for "no shade", or the shade."""
    if shade == "drawings":
        return "drawings"
    if shade in (None, "", "none"):
        return None
    if not isinstance(shade, dict) or str(shade.get("kind", "")).casefold() not in SHADE_KINDS:
        raise ValueError("Choose the kind of shade over the windows (awning, verandah, eave, balcony or canopy).")
    result = {"kind": str(shade["kind"]).casefold(), "depth_m": round(_number(shade.get("depth_m"), "shade depth in metres", 0.1, 10), 2)}
    if shade.get("gap_m") not in (None, ""):
        result["gap_m"] = round(_number(shade.get("gap_m"), "gap from the top of the glass to the shade in metres", 0, 5), 2)
    return result


def save(web, project, data, reviewer="Quick fixes"):
    """Apply a batch of fixes. Everything is checked before anything is saved. Returns {"applied": [text, ...]}."""
    from backend import job_service, need_answers_service
    root = _root(project)
    screen = get(web, project)
    current = {row["room_id"]: row for row in screen["rooms"]}
    uses = screen["uses"]
    room_changes = [row for row in (data.get("rooms") or []) if isinstance(row, dict)]
    window_changes = [row for row in (data.get("windows") or []) if isinstance(row, dict)]
    checked_rooms, checked_windows = [], []
    for change in room_changes:
        room = current.get(str(change.get("room_id") or ""))
        if not room:
            raise ValueError("A fix names a room that isn't in this job.")
        item = {"room": room}
        if "name" in change:
            name = " ".join(str(change["name"] or "").split())[:NAME_LIMIT]
            if not name:
                raise ValueError(f"Give {room['label']} a name.")
            item["name"] = name
        if change.get("use"):
            if str(change["use"]) not in uses:
                raise ValueError(f"Choose a use for {room['name']} from the list.")
            item["use"] = str(change["use"])
        if "area_m2" in change:
            item["area_m2"] = round(_number(change["area_m2"], f"area of {room['name']} in m²", 0.5, 20000), 2)
        if "people" in change:
            item["people"] = round(_number(change["people"], f"number of people in {room['name']}", *PEOPLE_RANGE))
        if "cooled" in change:
            if type(change["cooled"]) is not bool:
                raise ValueError("Cooled must be yes or no.")
            item["cooled"] = change["cooled"]
        checked_rooms.append(item)
    labels = {row["label"].casefold(): row["label"] for row in current.values()}
    for change in window_changes:
        target = str(change.get("room") or "")
        if target != "*" and target.casefold() not in labels:
            raise ValueError("A window fix names a room that isn't in this job.")
        item = {"room": "*" if target == "*" else labels[target.casefold()]}
        if "glass" in change:
            item["glass"] = _check_glass(change["glass"])
        if "blinds" in change:
            if change["blinds"] not in BLINDS and change["blinds"] != "drawings":
                raise ValueError("Choose the inside blinds from the list.")
            item["blinds"] = change["blinds"]
        if "shade" in change:
            item["shade"] = _check_shade(change["shade"])
        checked_windows.append(item)

    applied = []
    stored = read(root)
    for item in checked_rooms:
        room = item["room"]
        record = dict(stored["rooms"].get(room["room_id"], {}))
        record.update({"label": room["label"], "level": room["level"], "by": reviewer, "at": time.time()})
        if "name" in item:
            if item["name"] == room["label"]:
                record.pop("name", None)
            else:
                record["name"] = item["name"]
            applied.append(f"{room['label']} is shown as {item['name']}.")
        if "cooled" in item:
            record["cooled"] = item["cooled"]
            applied.append(f"{room['name']}: {'cooled' if item['cooled'] else 'not cooled'}.")
        stored["rooms"][room["room_id"]] = record
        if "use" in item:
            _apply_use(web, project, room["room_id"], item["use"], reviewer)
            applied.append(f"{room['name']} is used as {uses.get(item['use'], item['use'])}.")
        if "area_m2" in item:
            job_service.save_area_override(project, {"room_id": room["room_id"], "label": room["label"],
                                                     "level_name": room["level"], "area_m2": item["area_m2"], "edited_by": reviewer})
            applied.append(f"{room['name']} area: {item['area_m2']:g} m².")
        if "people" in item:
            need_answers_service.apply(web, project, {"kind": "occupancy", "room": room["label"], "value": item["people"],
                                                      "reviewer": reviewer, "source": "quick fixes"})
            applied.append(f"{room['name']}: {item['people']:g} people.")
    for item in checked_windows:
        where = "every window" if item["room"] == "*" else f"{item['room']}'s windows"
        record = dict(stored["windows"].get(item["room"], {}))
        if "glass" in item:
            if item["glass"] is None:
                _remove_glass(root, item["room"])
                applied.append(f"Glass for {where}: back to what the drawings give.")
            else:
                u_value, shgc, text, source = item["glass"]
                need_answers_service._save_glazing(project, {"room": "" if item["room"] == "*" else item["room"], "u_value_w_m2k": u_value,
                                                             "shgc": shgc, "value": text, "source": source},
                                                   [{"label": label} for label in labels.values()], reviewer)
                applied.append(f"Glass for {where}: {text}.")
        if "blinds" in item:
            if item["blinds"] == "drawings":
                record.pop("blinds", None)
                applied.append(f"Blinds on {where}: as the drawings give.")
            else:
                record["blinds"] = item["blinds"]
                applied.append(f"Blinds on {where}: {BLINDS[item['blinds']][0].lower()}.")
        if "shade" in item:
            if item["shade"] == "drawings":
                record.pop("shade", None)
                applied.append(f"Shade over {where}: as the drawings give.")
            else:
                record["shade"] = item["shade"]
                applied.append(f"Shade over {where}: " + (f"{item['shade']['kind']} {item['shade']['depth_m']:g} m deep." if item["shade"] else "none."))
        if record:
            stored["windows"][item["room"]] = record
        else:
            stored["windows"].pop(item["room"], None)
    _write(root, stored)
    return {"applied": applied}


def _apply_use(web, project, room_id, use, reviewer):
    from backend import room_use_resolution_service
    payload = {"action": "apply_override", "room_id": room_id, "taxonomy_id": use, "reviewer": reviewer,
               "note": "Use chosen on the quick-fix screen."}
    try:
        room_use_resolution_service.post(web, project, payload)
    except ValueError as error:
        if "stale" not in str(error).lower():
            raise
        room_use_resolution_service.post(web, project, {"action": "resolve"})   # bring it up to date, then apply
        room_use_resolution_service.post(web, project, payload)


def _remove_glass(root, room):
    path = Path(root) / "glazing_answers.json"
    if not path.is_file():
        return
    stored = json.loads(path.read_text(encoding="utf-8"))
    if room == "*":
        stored["all"] = None
    else:
        stored["rooms"] = {key: value for key, value in (stored.get("rooms") or {}).items() if key.casefold() != room.casefold()}
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staging.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(staging, path)
