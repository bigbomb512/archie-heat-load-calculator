"""Where a job is, worked out in one place.

STEPS is the pipeline in order, with the file each step keeps its progress in. job_runner.step_state turns any step's
own status word into waiting / active / stopping / done / failed. milestones() turns a job's facts into the stage the
screens show (drawings, phase1, phase2, results) and what needs a person. The job screen, the jobs list, the recovery
buttons and the Phase 1 start check read these instead of keeping their own lists.
"""

from pathlib import Path

from backend.job_runner import read_json, recovery, step_state

STEPS = (
    ("preparation", "new_job_job.json", "preparing the drawing set"),
    ("preparation", "page_preparation_job.json", "preparing the drawings"),
    ("pages", "page_inventory_job.json", "reading the pages"),
    ("equipment", "page_extraction_job.json", "reading equipment"),
    ("rooms", "drawing_tasks_job.json", "tracing rooms and walls"),
    ("envelope", "skill_workflow_run.json", "reviewing room names and the site"),
    ("supervisor", "phase1_supervisor_job.json", "checking the empty-room model"),
)
STAGES = ("drawings", "phase1", "phase2", "results")


def in_flight(job):
    """A step that is working or is being restarted."""
    return step_state(job.get("status")) in {"active", "stopping"} or bool(job.get("restart_requested"))


def stage_blocker(project, kind):
    """Why the step `kind` can't be resumed yet (an earlier step is still working, or has nothing to give it), or ""."""
    root = Path(project["review_dir"])
    if not project.get("reasoning_packet") and kind != "preparation":
        return "Prepare the drawing set first."
    for stage, filename, label in STEPS:
        if stage == kind:
            break
        if in_flight(read_json(root / filename)):
            return f"Waiting for {label} to finish."
    if kind in {"equipment", "rooms"}:
        # The inventory service owns the actual artifact name.
        from backend import page_inventory_service
        if not read_json(root / page_inventory_service.RESULT_FILE).get("pages"):
            return "Read some drawing pages first." if kind == "equipment" else "Read the drawing pages first."
    if kind == "supervisor":
        review = read_json(root / "skill_workflow_run.json")
        if review.get("scope") != "phase1_envelope" or step_state(review.get("status")) not in {"done", "failed"}:
            return "Finish the room and site review first."
    return ""


def blocked_status(current, reason):
    return {**current, "recovery": {**recovery(current.get("status", current.get("state", "none"))), "reason": reason}}


def milestones(*, drawings_confirmed, checks, phase1_complete, result_exists, result_stale, rooms,
               areas_still_being_found, open_needs):
    """The job's stage and what needs a person, from its facts.

    rooms: (has no area, needs a use) per listed room. A missing area isn't asked for while the AI is still finding
    areas. A result that is out of date keeps the job in Phase 2 until it is recalculated."""
    phase2_complete = bool(phase1_complete and result_exists and not result_stale)
    working = bool(checks.get("waiting") or checks.get("blocked"))
    stage = ("drawings" if not drawings_confirmed or working
             else "phase1" if not phase1_complete
             else "phase2" if not phase2_complete
             else "results")
    return {
        "stage": stage,
        "drawings_confirmed": bool(drawings_confirmed),
        "phase1_complete": bool(phase1_complete),
        "phase2_complete": phase2_complete,
        "results_ready": bool(result_exists),
        "result_stale": bool(result_exists and result_stale),
        "needs_you": {
            "drawings": int(checks.get("blocked", 0) or 0),
            "phase1": sum(1 for no_area, needs_use in rooms if (no_area and not areas_still_being_found) or needs_use),
            "phase2": int(open_needs),
            "results": 0,
        },
    }
