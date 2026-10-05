"""Reviewer confirmation of which rooms make up an AI preliminary draft load.

Room detection can propose rooms that are not real (text from notes, renders or
schedules) or leave out real rooms whose use is unresolved. A draft cooling
number is therefore only calculated from a room list a reviewer has confirmed.
The confirmation is bound to a fingerprint of the candidate list, so any change
to the rooms, their areas, scope or use requires a new confirmation.
"""

from copy import deepcopy
import hashlib
import json

from ai.room_use_resolution import room_identity


SCHEMA_VERSION = 1
CONFIRMED, NOT_CONFIRMED, STALE = "confirmed", "not_confirmed", "stale"
COMFORT_SCOPES = {"comfort_hvac", "comfort_hvac_with_process_exception"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def empty_confirmation():
    return {"schema_version": SCHEMA_VERSION, "candidate_fingerprint": "", "reviewer": "", "note": "",
            "confirmed_at": "", "rows": []}


def _pages(evidence):
    return sorted({item.get("page") for item in evidence or [] if isinstance(item, dict) and isinstance(item.get("page"), int)})


def _room_use_ids(room_use):
    """Map (label, level) to the room-use record ID so overrides target it."""
    index = {}
    for row in (room_use or {}).get("records", []):
        if isinstance(row, dict) and row.get("room_id"):
            label = str(row.get("original_label", "")).strip().casefold()
            level = str(row.get("level_name", "") or "Unassigned level").strip().casefold()
            index[(label, level)] = row["room_id"]
    return index


def candidates(input_set, room_use=None):
    """Rows a reviewer must review: calculated rooms, rooms with no use, and
    comfort rooms that have no area yet.

    Rooms without an area are listed (status "no_area") so a reviewer can see
    they are missing from the total; they cannot be included until traced.
    Refrigeration/process rooms and reviewer-declared "not a room" detections
    are not offered: they cannot enter the comfort total from this list.
    """
    material = (input_set or {}).get("material", {}) if isinstance(input_set, dict) else {}
    model = material.get("hourly_load_model", {}) if isinstance(material, dict) else {}
    zones = {row.get("zone_id"): row for row in model.get("zones", []) if isinstance(row, dict)}
    floors = {row.get("floor_id"): row for row in model.get("floors", []) if isinstance(row, dict)}
    ledger = [row for row in (input_set or {}).get("materialized_fields", []) if isinstance(row, dict)]
    area_rows = {row.get("room_id"): row for row in ledger if row.get("field") == "area_m2"}
    scope_rows = {row.get("room_id"): row for row in ledger if row.get("field") == "space_scope"}
    use_ids = _room_use_ids(room_use)
    rows = {}
    for room in model.get("rooms", []):
        if not isinstance(room, dict) or not room.get("room_id"):
            continue
        name = str(room.get("name", "")).strip()
        floor = floors.get(zones.get(room.get("zone_id"), {}).get("floor_id"), {})
        level = str(floor.get("name", "") or "Unassigned level")
        ledger_area = area_rows.get(room["room_id"], {})
        area = room.get("area_m2")
        key = use_ids.get((name.casefold(), level.casefold())) or room_identity(name, level)
        rows[key] = {
            "key": key, "model_room_id": room["room_id"], "label": name, "level": level,
            "area_m2": round(float(area), 2) if isinstance(area, (int, float)) else None,
            "area_origin": str(ledger_area.get("origin", "")),
            "area_quality_label": str(ledger_area.get("quality_label", "")),
            "source_pages": _pages(ledger_area.get("evidence", [])),
            "scope": str(scope_rows.get(room["room_id"], {}).get("value", "")),
            "status": "calculated",
        }
    for space in (input_set or {}).get("excluded_spaces", []):
        if not isinstance(space, dict) or space.get("scope") not in COMFORT_SCOPES | {"unresolved_scope"}:
            continue
        if space.get("scope") in COMFORT_SCOPES:
            name = str(space.get("room_name", "")).strip()
            level = str(space.get("level", "") or "Unassigned level")
            key = use_ids.get((name.casefold(), level.casefold())) or room_identity(name, level)
            if name and key not in rows and not str(space.get("reason", "")).startswith("Excluded by reviewer"):
                rows[key] = {
                    "key": key, "model_room_id": "", "label": name, "level": level, "area_m2": None,
                    "area_origin": "", "source_pages": _pages(space.get("evidence", [])),
                    "scope": str(space.get("scope")), "status": "no_area",
                    "reason": "No traced or printed area yet; this room is not in the total.",
                }
            continue
        name = str(space.get("room_name", "")).strip()
        level = str(space.get("level", "") or "Unassigned level")
        key = use_ids.get((name.casefold(), level.casefold())) or room_identity(name, level)
        if not name or key in rows:
            continue
        rows[key] = {
            "key": key, "model_room_id": "", "label": name, "level": level, "area_m2": None,
            "area_origin": "", "source_pages": _pages(space.get("evidence", [])),
            "scope": "unresolved_scope", "status": "needs_use",
            "reason": str(space.get("reason", "")),
        }
    return sorted(rows.values(), key=lambda row: (row["label"].casefold(), row["level"].casefold(), row["key"]))


def candidate_fingerprint(rows):
    return fingerprint([{key: row.get(key) for key in ("key", "label", "level", "area_m2", "scope", "status")}
                        for row in rows])


def state(input_set, confirmation, room_use=None):
    rows = candidates(input_set, room_use)
    current = candidate_fingerprint(rows)
    confirmation = confirmation if isinstance(confirmation, dict) and confirmation.get("candidate_fingerprint") else None
    if not confirmation:
        status = NOT_CONFIRMED
    elif confirmation.get("candidate_fingerprint") != current:
        status = STALE
    else:
        status = CONFIRMED
    decisions = {row.get("key"): row for row in (confirmation or {}).get("rows", []) if isinstance(row, dict)}
    for row in rows:
        decision = decisions.get(row["key"]) if status == CONFIRMED else None
        row["include"] = (bool(decision.get("include")) if decision
                          else row["status"] in {"calculated", "needs_use"})
        row["exclude_reason"] = str(decision.get("reason", "")) if decision else ""
    return {"status": status, "candidate_fingerprint": current, "candidates": rows,
            "confirmation": deepcopy(confirmation) if confirmation else None}


def confirm(input_set, room_use, data, now):
    """Validate a reviewer's submission and return the confirmation artifact."""
    if not isinstance(data, dict):
        raise ValueError("Room confirmation must be an object.")
    reviewer = str(data.get("reviewer", "")).strip()
    if not reviewer:
        raise ValueError("Enter a reviewer name before confirming the room list.")
    rows = candidates(input_set, room_use)
    if not rows:
        raise ValueError("There are no rooms to confirm; resolve model inputs first.")
    current = candidate_fingerprint(rows)
    if data.get("candidate_fingerprint") and data["candidate_fingerprint"] != current:
        raise ValueError("The room list changed while you were reviewing it; review the updated list and confirm again.")
    submitted = data.get("rows")
    if not isinstance(submitted, list):
        raise ValueError("Room confirmation rows must be a list.")
    by_key = {}
    for row in submitted:
        if not isinstance(row, dict) or not str(row.get("key", "")).strip():
            raise ValueError("Each confirmed room needs its key.")
        by_key[str(row["key"])] = row
    known = {row["key"] for row in rows}
    unknown = sorted(set(by_key) - known)
    if unknown:
        raise ValueError("Unknown room in confirmation: " + ", ".join(unknown))
    missing = [row["label"] for row in rows if row["key"] not in by_key]
    if missing:
        raise ValueError("Include or exclude every room before confirming: " + ", ".join(missing))
    output = []
    for row in rows:
        decision = by_key[row["key"]]
        include = bool(decision.get("include"))
        if include and row["status"] == "needs_use":
            raise ValueError(f"Choose a use for {row['label']} (or exclude it) before confirming the room list.")
        if include and row["status"] == "no_area":
            raise ValueError(f"Trace {row['label']} before including it in the room list.")
        reason = str(decision.get("reason", "")).strip()
        output.append({"key": row["key"], "label": row["label"], "level": row["level"], "area_m2": row["area_m2"],
                       "area_origin": row["area_origin"], "area_quality_label": row.get("area_quality_label", ""), "source_pages": row["source_pages"],
                       "status": row["status"], "include": include,
                       "reason": "" if include else (reason or "Excluded by reviewer")})
    if not any(row["status"] == "calculated" for row in output):
        raise ValueError("Trace at least one room before confirming the room list.")
    if not any(row["include"] for row in output):
        raise ValueError("Include at least one room before confirming the room list.")
    return {"schema_version": SCHEMA_VERSION, "candidate_fingerprint": current, "reviewer": reviewer,
            "note": str(data.get("note", "")).strip(), "confirmed_at": now, "rows": output}


def apply(input_set, confirmation, room_use=None):
    """Return a copy of the input set limited to the confirmed rooms.

    Raises ValueError when the room list has not been confirmed or has changed
    since confirmation.
    """
    current = state(input_set, confirmation, room_use)
    if current["status"] == NOT_CONFIRMED:
        raise ValueError("Confirm the room list before calculating the draft load.")
    if current["status"] == STALE:
        raise ValueError("The room list changed since it was confirmed; confirm it again before calculating.")
    result = deepcopy(input_set)
    decisions = {row["key"]: row for row in confirmation.get("rows", [])}
    removed = {}
    for row in current["candidates"]:
        decision = decisions.get(row["key"], {})
        if decision.get("include"):
            continue
        reason = "Excluded by reviewer: " + str(decision.get("reason") or "Excluded by reviewer")
        if row["model_room_id"]:
            removed[row["model_room_id"]] = reason
            result.setdefault("excluded_spaces", []).append({
                "room_name": row["label"], "level": row["level"], "scope": row["scope"] or "comfort_hvac",
                "reason": reason, "evidence": []})
        else:
            for space in result.get("excluded_spaces", []):
                if (isinstance(space, dict) and space.get("scope") == "unresolved_scope"
                        and str(space.get("room_name", "")).strip().casefold() == row["label"].casefold()):
                    space["reason"] = reason
    model = result["material"]["hourly_load_model"]
    kept_rooms = [room for room in model.get("rooms", []) if room.get("room_id") not in removed]
    if not kept_rooms:
        raise ValueError("No confirmed rooms remain in the draft model; include at least one room.")
    used_zones = {room.get("zone_id") for room in kept_rooms}
    model["rooms"] = kept_rooms
    model["zones"] = [zone for zone in model.get("zones", []) if zone.get("zone_id") in used_zones]
    for room_id, reason in removed.items():
        result.setdefault("exclusions", []).append({"room_id": room_id, "component": "room scope", "reason": reason})
    result["materialized_fields"] = [row for row in result.get("materialized_fields", [])
                                     if not (isinstance(row, dict) and row.get("room_id") in removed)]
    result["review_queue"] = [row for row in result.get("review_queue", [])
                              if not (isinstance(row, dict) and row.get("room_id") in removed)]
    confirmed_rooms = [{key: row[key] for key in ("label", "level", "area_m2", "area_origin", "area_quality_label", "source_pages")}
                       for row in current["candidates"] if decisions.get(row["key"], {}).get("include")]
    summary = {"reviewer": confirmation.get("reviewer", ""), "confirmed_at": confirmation.get("confirmed_at", ""),
               "candidate_fingerprint": confirmation.get("candidate_fingerprint", "")}
    return result, confirmed_rooms, summary
