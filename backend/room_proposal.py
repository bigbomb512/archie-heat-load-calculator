"""One reader for the room proposal used by every downstream resolver.

The proposal is the detected room list (local room inference) or, failing that,
a saved manual placeholder proposal. Reviewer-added rooms are stored with the
reviewer traces (reviewer_room_geometry.json) so they survive re-detection and
re-analysis, and are merged in here so every consumer sees the same rooms.
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


def proposal_room(row):
    """A reviewer-added room in the proposal-room shape detection produces."""
    page = row.get("page") if isinstance(row.get("page"), int) else None
    evidence = [{"page": page, "reference": "Reviewer-added room", "excerpt": row["label"],
                 "reviewer": row.get("reviewer", "")}] if page else []
    return {"kind": "room", "room_id": row["room_id"], "label": row["label"], "level_name": row["level_name"],
            "page": page, "source_pages": [page] if page else [], "evidence": evidence,
            "source": "reviewer_added", "reviewer_added": True, "confidence": "reviewer"}


def room_proposal(run, root):
    """Base proposal plus reviewer-added rooms not already present."""
    proposal = base_proposal(run)
    existing = {room_identity(row.get("label", ""), row.get("level_name") or "Unassigned level")
                for row in proposal["rooms"] if isinstance(row, dict)}
    for row in reviewer_rooms(root):
        if row["room_id"] not in existing:
            proposal["rooms"].append(proposal_room(row))
            existing.add(row["room_id"])
    return proposal
