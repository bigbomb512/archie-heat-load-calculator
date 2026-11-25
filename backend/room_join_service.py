"""Reversible display grouping for drawing pieces that remain separate thermal zones."""

import hashlib
import json
import math
import os
from pathlib import Path
import time

from ai.room_use_resolution import base_label
from backend import productization, quick_fix_service

FILE = "room_joins.json"


def read(root):
    try:
        data = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("joins", []) if isinstance(data, dict) and isinstance(data.get("joins"), list) else []


def _write(root, joins):
    path = Path(root) / FILE
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"version": 1, "joins": joins}, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _artifact(root, name):
    try:
        return json.loads((Path(root) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record(root, name, room_id):
    return next((row for row in _artifact(root, name).get("records", [])
                 if isinstance(row, dict) and row.get("room_id") == room_id), {})


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("People must be a finite non-negative number.") from None
    if isinstance(value, bool) or not math.isfinite(number) or number < 0 or number > 5000:
        raise ValueError("People must be between 0 and 5000.")
    return round(number)


def _apply_internal(web, project, room_id, field, value, reviewer, note):
    from backend import internal_gains_resolution_service
    data = {"action": "apply_override", "room_id": room_id, "field": field, "value": value,
            "reviewer": reviewer, "note": note}
    try:
        internal_gains_resolution_service.post(web, project, data)
    except ValueError as error:
        if "stale" not in str(error).lower():
            raise
        internal_gains_resolution_service.post(web, project, {"action": "resolve"})
        internal_gains_resolution_service.post(web, project, data)
    if field == "equipment":
        internal_gains_resolution_service.post(web, project, {"action": "resolve"})


def _clear_internal(web, project, room_id, field):
    from backend import internal_gains_resolution_service
    data = {"action": "clear_override", "room_id": room_id, "field": field}
    try:
        internal_gains_resolution_service.post(web, project, data)
    except ValueError as error:
        if "stale" not in str(error).lower():
            raise
        internal_gains_resolution_service.post(web, project, {"action": "resolve"})
        internal_gains_resolution_service.post(web, project, data)
    internal_gains_resolution_service.post(web, project, {"action": "resolve"})


def _restore_use(web, project, room_id, previous):
    from backend import room_use_resolution_service
    data = {"action": "apply_override", "room_id": room_id, "taxonomy_id": previous["taxonomy_id"],
            "reviewer": previous["reviewer"], "note": previous.get("note", "")} if previous else {
                "action": "clear_override", "room_id": room_id}
    try:
        room_use_resolution_service.post(web, project, data)
    except ValueError as error:
        if "stale" not in str(error).lower():
            raise
        room_use_resolution_service.post(web, project, {"action": "resolve"})
        room_use_resolution_service.post(web, project, data)


def join(web, project, data, reviewer):
    from backend import need_answers_service
    root = Path(project["review_dir"])
    ids = data.get("room_ids")
    if not isinstance(ids, list) or len(ids) < 2 or any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("Choose at least two distinct rooms.")
    screen = quick_fix_service.get(web, project)
    rooms = {row["room_id"]: row for row in screen["rooms"]}
    if any(room_id not in rooms for room_id in ids):
        raise ValueError("Every room must belong to this job.")
    if len({rooms[room_id]["level"] for room_id in ids}) != 1:
        raise ValueError("Joined rooms must be on the same level.")
    existing = read(root)
    if any(room_id in group.get("member_ids", []) for group in existing for room_id in ids):
        raise ValueError("A room is already joined.")
    if "cooled" in data and type(data["cooled"]) is not bool:
        raise ValueError("Cooled must be yes or no.")
    people = _number(data["people"]) if "people" in data else None
    primary_id = max(ids, key=lambda room_id: (rooms[room_id].get("area_m2") or 0, -ids.index(room_id)))
    primary = rooms[primary_id]
    use = data.get("use", primary.get("use_id"))
    if use and use not in screen["uses"]:
        raise ValueError("Choose a use from the list.")
    cooled = data.get("cooled", primary["cooled"])
    name = " ".join(str(data.get("name") or base_label(primary["label"])).split())
    if not name or len(name) > quick_fix_service.NAME_LIMIT:
        raise ValueError("Give the joined room a name of at most 80 characters.")
    join_id = "join:" + hashlib.sha256((project["id"] + "\0" + "\0".join(sorted(ids)) + "\0" + str(time.time_ns())).encode()).hexdigest()[:12]
    fixes = quick_fix_service.read(root)["rooms"]
    previous = {}
    for room_id in ids:
        gains = _record(root, "internal_gains_resolution.json", room_id)
        use_record = _record(root, "room_use_resolution.json", room_id)
        previous[room_id] = {
            "quick_fix": fixes.get(room_id),
            "use_override": use_record.get("override"), "has_use_record": bool(use_record),
            "occupancy_override": (gains.get("override") or {}).get("occupancy_count"),
            "equipment_override": (gains.get("override") or {}).get("equipment"),
            "typical_fixed_equipment": any(item.get("origin") == "typical_home_value" for item in gains.get("equipment", []) if isinstance(item, dict)),
            "has_gains_record": bool(gains),
        }
    record = {"id": join_id, "name": name, "level": primary["level"], "member_ids": ids,
              "primary_id": primary_id, "created_by": reviewer, "created_at": time.time(), "previous": previous}
    changes = [{"room_id": room_id, "cooled": cooled,
                **({"use": use} if use and previous[room_id]["has_use_record"] else {})} for room_id in ids]
    applied = quick_fix_service.save(web, project, {"rooms": changes}, reviewer)["applied"]
    for room_id in ids:
        if not previous[room_id]["has_gains_record"]:
            applied.append(f"{rooms[room_id]['label']} has no internal-gains record yet; its occupancy cannot be changed until resolved.")
            continue
        value = people if room_id == primary_id and people is not None else (primary.get("people") if room_id == primary_id else 0)
        if value is None:
            value = 0
        need_answers_service.apply(web, project, {"kind": "occupancy", "room": rooms[room_id]["label"],
                                                  "value": value, "reviewer": reviewer,
                                                  "source": f"room join {join_id}"})
        applied.append(f"{rooms[room_id]['label']}: {value:g} people in joined room.")
        if room_id != primary_id and previous[room_id]["typical_fixed_equipment"]:
            _apply_internal(web, project, room_id, "equipment", [], reviewer, f"Set by room join {join_id} to avoid repeating fixed typical appliances.")
            applied.append(f"{rooms[room_id]['label']}: fixed typical appliances counted once in the joined room.")
    old_fp = productization.fingerprint({"version": 1, "joins": existing})
    _write(root, [*existing, record])
    productization.append_audit_event(root, action="room_joined", target=FILE, actor=reviewer,
                                      previous_fingerprint=old_fp, new_fingerprint=productization.fingerprint({"version": 1, "joins": [*existing, record]}),
                                      affected_ids=ids)
    return {"join": record, "applied": applied}


def split(web, project, join_id, reviewer):
    root = Path(project["review_dir"])
    joins = read(root)
    record = next((row for row in joins if row.get("id") == join_id), None)
    if record is None:
        raise ValueError("Joined room does not exist.")
    kept_occupancy = []
    for room_id in record["member_ids"]:
        prior = record["previous"][room_id]
        if prior.get("has_use_record"):
            _restore_use(web, project, room_id, prior.get("use_override"))
        current = (_record(root, "internal_gains_resolution.json", room_id).get("override") or {}).get("occupancy_count")
        if current and f"room join {join_id}" not in str(current.get("note", "")):
            kept_occupancy.append(room_id)
        else:
            if prior.get("has_gains_record") and current:
                _clear_internal(web, project, room_id, "occupancy_count")
            override = prior.get("occupancy_override")
            if override:
                _apply_internal(web, project, room_id, "occupancy_count", override["value"],
                                override.get("reviewer") or reviewer, override.get("note", ""))
        if prior.get("typical_fixed_equipment"):
            equipment = (_record(root, "internal_gains_resolution.json", room_id).get("override") or {}).get("equipment")
            if equipment and f"room join {join_id}" in str(equipment.get("note", "")):
                _clear_internal(web, project, room_id, "equipment")
                old_equipment = prior.get("equipment_override")
                if old_equipment:
                    _apply_internal(web, project, room_id, "equipment", old_equipment["value"],
                                    old_equipment.get("reviewer") or reviewer, old_equipment.get("note", ""))
    fixes = quick_fix_service.read(root)
    for room_id in record["member_ids"]:
        prior = record["previous"][room_id]["quick_fix"]
        if prior is None:
            fixes["rooms"].pop(room_id, None)
        else:
            fixes["rooms"][room_id] = prior
    quick_fix_service._write(root, fixes)
    remaining = [row for row in joins if row.get("id") != join_id]
    _write(root, remaining)
    productization.append_audit_event(root, action="room_split", target=FILE, actor=reviewer,
                                      previous_fingerprint=productization.fingerprint({"version": 1, "joins": joins}),
                                      new_fingerprint=productization.fingerprint({"version": 1, "joins": remaining}),
                                      affected_ids=record["member_ids"])
    applied = [f"Split {record['name']} into {len(record['member_ids'])} rooms and restored their previous answers."]
    if kept_occupancy:
        applied.append(f"Kept later occupancy answers for {', '.join(kept_occupancy)}.")
    return {"applied": applied}


def api(web, project, data, reviewer):
    from backend import calculation_service
    if calculation_service._JOB.status(project).get("status") in {"queued", "running", "stopping"}:
        raise ValueError("A calculation is running. Wait for it to finish, then change the rooms.")
    if data.get("action") == "join":
        return join(web, project, data, reviewer)
    if data.get("action") == "split":
        return split(web, project, data.get("join_id"), reviewer)
    raise ValueError("Action must be join or split.")
