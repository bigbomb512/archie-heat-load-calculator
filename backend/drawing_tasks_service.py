"""The drawing tasks answered by the AI provider: the scale from a printed dimension, room names, room outlines,
north, what each wall faces, shopfront openings and kitchen equipment (the Card P tasks in
backend.autonomous_tasks_service).

Each task already has a bounded prompt with cropped images and a checker that refuses a reply it can't verify; until
now a person carried the prompt to ChatGPT and pasted the reply back. Here the provider (OpenRouter by default)
answers them, in rounds, because the tasks build on each other: the scale and the wall styles come first, then the
room names, then the outlines, then the walls and openings of each traced room. Each round builds whatever tasks are
now possible, answers every task waiting for a reply, and stops when nothing new is waiting. A refused reply is tried
once more with the reason it was refused; after that the task waits for a person (the manual route still works).
Results are applied exactly as a pasted reply would be: as AI-determined proposals that a person can confirm or redo.

Coordinates: vision models answer on their own 0–1000 grid over an image whatever its size (Gemini read a 1,536-px
plan as 0–1000, which made every room about 45 % of its area). So when the AI answers, the prompt asks for that grid
and the reply's pixel fields (*_px) are converted to the image's pixels before the checker sees them: by the one
image's size, or for the north arrow by the size of the crop it chose. Where neither applies, nothing is converted.

Started after the equipment reading; the skills start when it finishes, so the room skills see the traced rooms.
Every call is logged in the job's ai_usage.jsonl under "drawing:<task>" with its cost.
"""

import json
from pathlib import Path

from PIL import Image

from backend import ai_provider
from backend.job_runner import BackgroundJob, read_json, write_json

JOB_FILE = "drawing_tasks_job.json"
STEPS = [("build", "Preparing the drawing tasks"), ("answer", "The AI is answering the drawing tasks"),
         ("skills", "Starting the skills")]
MAX_ROUNDS = 8          # scale -> names -> outlines -> walls/openings -> kitchen: five stages, with room to spare
MAX_ATTEMPTS = 2        # a refused reply is tried once more, told why it was refused
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-drawing-tasks",
                     "The drawing tasks stopped before they finished (the server was restarted). Retry to finish them.")


def status(web, project):
    job = _JOB.status(project)
    if job.get("provider_limited"):
        job["warning"] = "The drawing provider's usage limit was reached. Unanswered tasks are saved; retry after the limit resets."
    return job


def start(web, project, then_skills=True):
    """Start the drawing tasks (or join the run already going). Raises ValueError when no AI provider is set up."""
    if not ai_provider.configured():
        raise ValueError("No AI provider is set up on this server, so the drawing tasks can't run.")

    def work(web, project_id, step):
        current = web.project_by_id(project_id) if web is not None else project
        try:
            result = answer_all(web, current, step)
        except ai_provider.UsageLimitReached as error:
            # Stop further provider calls, but let the independent skill review
            # continue from page readings, vectors, and saved drawing evidence.
            result = {"answered": 0, "refused": len(_waiting(Path(current["review_dir"]))),
                      "rounds": 0, "calls": 0, "provider_limited": True,
                      "warning": str(error)[:500]}
        if not result.get("provider_limited"):
            # Phase 1 runs without a person: when nobody has chosen the weather location, the AI picks it from the
            # site details (one small text call, labelled in the result; user decision 2026-10-15).
            from backend import weather_pick_service
            try:
                weather_pick_service.pick(web, current)
            except Exception:  # the generic design day (labelled) is used instead
                pass
        if then_skills and web is not None:
            step("skills")
            from backend import skill_workflow_service
            try:
                skill_workflow_service.start_after_reading(web, current)
            except ValueError:
                pass
        return result

    return _JOB.start(web, project, {"answered": 0, "refused": 0, "rounds": 0}, work)


def _waiting(root):
    from backend import autonomous_tasks_service
    return [record for record in autonomous_tasks_service._all_current(root)
            if record.get("status") == "waiting_for_reply" and record.get("prompt")]


def _images(root, record):
    from backend import autonomous_tasks_service
    run_dir = autonomous_tasks_service._task_dir(root, record["task"], record["target"]) / "runs" / record["run_id"]
    return [str(path) for path in sorted(run_dir.glob("image-*.png"))]


GRID_NOTE = ("\n\nCoordinates: give every x, y (every *_px value) on a 0-1000 grid across the image you are "
             "describing (image 1 when the task says to draw on image 1): x 0 = left edge, 1000 = right edge; y 0 = top edge, 1000 = bottom edge, whatever the image's "
             "size in pixels.")


def _sizes(paths):
    sizes = []
    for path in paths:
        with Image.open(path) as image:
            sizes.append(image.size)
    return sizes


# Tasks whose coordinates are always on the first image; the images after it are only for reading (e.g. the sheets
# that print the room names for the outline task).
FIRST_IMAGE_TASKS = {"P0_room_outlines"}


def _grid_size(task, reply, sizes):
    """The (width, height) a reply's grid coordinates map onto, or None when it can't be told."""
    if len(sizes) == 1 or (task in FIRST_IMAGE_TASKS and sizes):
        return sizes[0]
    index = reply.get("crop_index") if isinstance(reply, dict) else None
    if task == "P2_north" and type(index) is int and 0 <= index < len(sizes):
        return sizes[index]
    return None


# Tasks whose replies carry no coordinates: no grid note in the prompt.
NO_GRID_TASKS = {"P0_room_sizes"}


def uses_grid(task, sizes):
    if task in NO_GRID_TASKS:
        return False
    return len(sizes) == 1 or (task == "P2_north" and len(sizes) > 1) or (task in FIRST_IMAGE_TASKS and bool(sizes))


def to_pixels(value, size, key=""):
    """The reply with every *_px point (or list of points) taken from the 0-1000 grid to the image's pixels."""
    if isinstance(value, dict):
        return {name: to_pixels(item, size, name) for name, item in value.items()}
    if isinstance(value, list):
        is_point = len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
        if key.endswith("_px") and is_point:
            return [round(value[0] * size[0] / 1000, 1), round(value[1] * size[1] / 1000, 1)]
        return [to_pixels(item, size, key) for item in value]
    return value


def _prompt(record, grid=False):
    """The task's prompt (asking for grid coordinates when they will be converted); on a second try, with the reason
    the last reply was refused."""
    prompt = record["prompt"] + (GRID_NOTE if grid else "")
    last = (record.get("reply_attempts") or [{}])[-1]
    if last.get("outcome") == "rejected" and last.get("reason"):
        return (f"{prompt}\n\nYour previous reply was refused: {last['reason']} "
                "Reply again, following the format above exactly (JSON only).")
    return prompt


def answer_all(web, project, step=lambda *args: None, provider_for=None):
    """Answer the waiting drawing tasks in rounds. Returns {"answered", "refused", "rounds", "calls"}.

    provider_for(task) gives the provider for a task (tests pass a fake); by default ai_provider.get("drawing:<task>")."""
    from backend import autonomous_tasks_service
    root = Path(project["review_dir"])
    provider_for = provider_for or (lambda task: ai_provider.get(f"drawing:{task}"))
    tried = {}                 # run id -> calls made, so a task that keeps failing can't loop
    answered = refused = calls = rounds = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        step("build")
        autonomous_tasks_service.post(web, project, {"action": "run_all"})
        waiting = [record for record in _waiting(root) if tried.get(record["run_id"], 0) < MAX_ATTEMPTS
                   and len(record.get("reply_attempts") or []) < MAX_ATTEMPTS]
        if not waiting:
            break
        step("answer", f"The AI is answering {len(waiting)} drawing task{'s' if len(waiting) != 1 else ''} (round {rounds})")
        for record in waiting:
            tried[record["run_id"]] = tried.get(record["run_id"], 0) + 1
            images = _images(root, record)
            sizes = _sizes(images)
            grid = uses_grid(record["task"], sizes)
            try:
                provider = ai_provider.recorded(provider_for(record["task"]), root, f"drawing:{record['task']}")
                reply, _raw = provider.propose(_prompt(record, grid), image_paths=images)
                calls += 1
            except ai_provider.UsageLimitReached:
                raise
            except Exception:  # an unreadable reply or a failed call: tried again next round, up to MAX_ATTEMPTS
                calls += 1
                continue
            size = _grid_size(record["task"], reply, sizes) if grid else None
            if size:
                reply = to_pixels(reply, size)
            autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": record["task"],
                                                         "target": record["target"], "reply": json.dumps(reply),
                                                         "model_note": f"AI provider ({getattr(provider, 'model', '') or 'default'})"})
            after = autonomous_tasks_service._current_task(root, record["task"], record["target"]) or {}
            if after.get("run_id") == record["run_id"] and after.get("status") == "waiting_for_reply":
                refused += 1
            else:
                answered += 1
        _progress(root, answered, refused, rounds)
    return {"answered": answered, "refused": refused, "rounds": rounds, "calls": calls}


def _progress(root, answered, refused, rounds):
    path = root / JOB_FILE
    job = read_json(path)
    if job:
        job.update({"answered": answered, "refused": refused, "rounds": rounds})
        write_json(path, job)
