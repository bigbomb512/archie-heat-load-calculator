"""Read-only prerequisite checks shared by recovery controls and their endpoints."""
from pathlib import Path

from backend.job_runner import read_json, recovery

ACTIVE = {"running", "queued", "stopping", "reading", "in_progress"}
TERMINAL = {"done", "completed", "needs_review", "failed", "blocked", "interrupted", "provisional"}
JOBS = [
    ("preparation", "new_job_job.json", "preparing the drawing set"),
    ("preparation", "page_preparation_job.json", "preparing the drawings"),
    ("pages", "page_inventory_job.json", "reading the pages"),
    ("equipment", "page_extraction_job.json", "reading equipment"),
    ("rooms", "drawing_tasks_job.json", "tracing rooms and walls"),
    ("envelope", "skill_workflow_run.json", "reviewing walls, roof, floor and windows"),
]


def stage_blocker(project, kind):
    root = Path(project["review_dir"])
    if not project.get("reasoning_packet") and kind != "preparation":
        return "Prepare the drawing set first."
    for stage, filename, label in JOBS:
        if stage == kind:
            break
        job = read_json(root / filename)
        if job.get("status") in ACTIVE or job.get("restart_requested"):
            return f"Waiting for {label} to finish."
    if kind == "equipment":
        # The inventory service owns the actual artifact name.
        from backend import page_inventory_service
        if not read_json(root / page_inventory_service.RESULT_FILE).get("pages"):
            return "Read some drawing pages first."
    if kind == "rooms":
        from backend import page_inventory_service
        if not read_json(root / page_inventory_service.RESULT_FILE).get("pages"):
            return "Read the drawing pages first."
    return ""


def blocked_status(current, reason):
    return {**current, "recovery": {**recovery(current.get("status", current.get("state", "none"))), "reason": reason}}
