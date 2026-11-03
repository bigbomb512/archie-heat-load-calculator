"""One reader for the room proposal used by every downstream resolver.

The proposal is the detected room list (local room inference) or, failing that,
a saved manual placeholder proposal. Reviewer-added rooms are stored with the
reviewer traces (reviewer_room_geometry.json) so they survive re-detection and
re-analysis, and are merged in here so every consumer sees the same rooms. So are rooms the room-naming step named by
their use because the drawings print no name for them (ai_named_rooms.json; for example an unlabelled dining area).
"""

from copy import deepcopy
import json
from pathlib import Path

from ai import reviewer_room_geometry
from ai.room_use_resolution import room_identity


def base_proposal(run):
    """Detected or placeholder proposal, normalised to {"rooms": [...]}."""
    run = run if isinstance(run, dict) else {}
    proposal = (run.get("local_room_inference_proposal") or run.get("manual_placeholder_proposal")
                or run.get("manual_placeholder_entities") or {})
    if isinstance(proposal, list):
        proposal = {"rooms": proposal}
    proposal = deepcopy(proposal) if isinstance(proposal, dict) else {}
    if not isinstance(proposal.get("rooms"), list):
        proposal["rooms"] = []
    return proposal


def reviewer_rooms(root):
    path = Path(root) / "reviewer_room_geometry.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    try:
        return reviewer_room_geometry.validate_artifact(raw).get("rooms", [])
    except ValueError:
        return []


USE_NAMED_ROOMS_FILE = "ai_named_rooms.json"


def save_use_named_rooms(root, task, page, level_name, labels, run_id, named_on_plan=False):
    """Record the rooms one drawing step named by their use on one plan page (replacing that step's earlier answer for
    the page; the enclosed-area naming and the open-plan outlines keep theirs separately)."""
    path = Path(root) / USE_NAMED_ROOMS_FILE
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    pages = saved.get("pages") if isinstance(saved.get("pages"), dict) else {}
    pages[f"{task}:page-{int(page)}"] = [{"label": label, "level_name": level_name or "Unassigned level", "page": int(page), "run_id": run_id,
                                          **({"named_on_plan": True} if named_on_plan else {})}
                        for label in labels]
    path.write_text(json.dumps({"schema_version": 1, "pages": {key: rows for key, rows in pages.items() if rows}}, indent=2),
                    encoding="utf-8")


def use_named_rooms(root):
    try:
        saved = json.loads((Path(root) / USE_NAMED_ROOMS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pages = saved.get("pages") if isinstance(saved, dict) and isinstance(saved.get("pages"), dict) else {}
    return [row for rows in pages.values() if isinstance(rows, list) for row in rows
            if isinstance(row, dict) and row.get("label") and type(row.get("page")) is int]


def proposal_room(row):
    """A reviewer-added room in the proposal-room shape detection produces."""
    page = row.get("page") if isinstance(row.get("page"), int) else None
    evidence = [{"page": page, "reference": "Reviewer-added room", "excerpt": row["label"],
                 "reviewer": row.get("reviewer", "")}] if page else []
    return {"kind": "room", "room_id": row["room_id"], "label": row["label"], "level_name": row["level_name"],
            "page": page, "source_pages": [page] if page else [], "evidence": evidence,
            "source": "reviewer_added", "reviewer_added": True, "confidence": "reviewer"}


def room_proposal(run, root):
    """Base proposal plus reviewer-added rooms and rooms found in scanned plans."""
    proposal = base_proposal(run)
    existing = {room_identity(row.get("label", ""), row.get("level_name") or "Unassigned level")
                for row in proposal["rooms"] if isinstance(row, dict)}
    for row in reviewer_rooms(root):
        if row["room_id"] not in existing:
            proposal["rooms"].append(proposal_room(row))
            existing.add(row["room_id"])
    for row in use_named_rooms(root):
        identity = room_identity(row["label"], row["level_name"])
        if identity in existing:
            continue
        on_plan = row.get("named_on_plan") is True
        proposal["rooms"].append({"kind": "room", "room_id": identity, "label": row["label"], "level_name": row["level_name"],
            "page": row["page"], "source_pages": [row["page"]], "source": "named_on_plan" if on_plan else "named_by_use",
            "confidence": 0.6,
            "evidence": [{"page": row["page"], "reference": ("Room name drawn on the plan, read by the page reading" if on_plan else
                                                             "Room named by its use: the drawings print no name for this space"),
                          "excerpt": row["label"]}]})
        existing.add(identity)
    tasks = Path(root) / "ai_tasks" / "S1_printed_areas"
    for pointer in tasks.glob("*/current.json"):
        try:
            current = json.loads(pointer.read_text(encoding="utf-8"))
            record = json.loads((pointer.parent / current["run_path"] / "record.json").read_text(encoding="utf-8"))
        except (OSError, KeyError, json.JSONDecodeError):
            continue
        if record.get("status") not in {"applied", "below_accuracy_bar"}:
            continue
        page = record.get("packet", {}).get("page")
        for area in record.get("applied_value", {}).get("rooms", []):
            if not isinstance(area, dict) or not area.get("label"):
                continue
            label = str(area["label"]).strip()
            level = str(area.get("level_name") or "Unassigned level").strip()
            identity = room_identity(label, level)
            citation = {"page": page, "reference": "Printed room area read from image",
                        "excerpt": str(area.get("printed_text") or label)}
            existing_room = next((row for row in proposal["rooms"] if isinstance(row, dict)
                                  and room_identity(row.get("label", ""), row.get("level_name") or "Unassigned level") == identity), None)
            if existing_room is None:
                proposal["rooms"].append({"kind": "room", "room_id": identity, "label": label,
                    "level_name": level, "page": page, "source_pages": [page] if type(page) is int else [],
                    "evidence": [citation], "area_m2": area.get("area_m2"),
                    "area_origin": "printed (read from image)",
                    "area_quality_label": area.get("quality_label", record.get("quality_label", "AI-determined")),
                    "area_source": "printed (read from image)", "confidence": 0.9})
                existing.add(identity)
            else:
                existing_room.update({"area_m2": area.get("area_m2"),
                    "area_origin": "printed (read from image)",
                    "area_quality_label": area.get("quality_label", record.get("quality_label", "AI-determined")),
                    "area_source": "printed (read from image)", "page": page})
                evidence = existing_room.setdefault("evidence", [])
                if citation not in evidence:
                    evidence.append(citation)
                if type(page) is int:
                    existing_room["source_pages"] = sorted(set(existing_room.get("source_pages", [])) | {page})
    return proposal
