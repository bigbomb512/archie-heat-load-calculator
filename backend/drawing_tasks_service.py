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
import hashlib
import re
from datetime import datetime
from pathlib import Path

from PIL import Image

from backend import ai_provider
from backend.job_runner import BackgroundJob, now, read_json, write_json, checkpoint, _write_json

JOB_FILE = "drawing_tasks_job.json"
STEPS = [("build", "Preparing the drawing tasks"), ("answer", "The AI is answering the drawing tasks"),
         ("skills", "Starting the skills")]
MAX_ROUNDS = 8          # scale -> names -> outlines -> walls/openings -> kitchen: five stages, with room to spare
MAX_ATTEMPTS = 2        # a refused reply is tried once more, told why it was refused
# Connection failures on this many calls in a row mean the connection itself is broken (on Butcher Buffet,
# 2026-10-17, all 24 calls failed the same way): the run stops calling instead of spending a call on every task.
MAX_CONNECTION_FAILURES_IN_A_ROW = 3
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-drawing-tasks",
                     "The drawing tasks stopped before they finished (the server was restarted). Retry to finish them.")


# What each drawing task is doing, in the words the Drawings screen shows.
TASK_LABELS = {
    "P0_dimensions": "Reading the scale from a printed dimension",
    "P0_wall_styles": "Telling the wall styles apart",
    "P0_room_names": "Finding the room names",
    "P0_room_outlines": "Tracing the room outlines",
    "P0_room_sizes": "Finding the room sizes",
    "S1_printed_areas": "Reading printed room areas",
    "S3_title_transcription": "Reading the title block",
    "P1_site": "Reading the site details",
    "P2_north": "Finding north",
    "P3_boundaries": "Working out what each wall faces",
    "P4_openings": "Finding the shopfront openings",
    "P5_roof": "Working out what is above",
    "P6_kitchen": "Listing the kitchen equipment",
}


def _seconds_since(stamp):
    try:
        return max(0, round((datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()))
    except (TypeError, ValueError):
        return None


def status(web, project):
    job = _JOB.status(project)
    if job.get("provider_limited"):
        job["warning"] = "The drawing provider's usage limit was reached. Unanswered tasks are saved; retry after the limit resets."
    if job.get("status") == "done" and (job.get("unresolved") or job.get("provider_limited")):
        from backend.job_runner import recovery
        job["recovery"] = recovery("failed", job.get("warning") or "Some drawing tasks could not be resolved. Retry unfinished tasks; completed drawing tasks are kept.")
    # Ages worked out here, so the screen doesn't depend on the browser's clock.
    if job.get("status") in {"queued", "running"}:
        job["request_seconds"] = _seconds_since(job.get("request_started_at")) if job.get("request_started_at") else None
        job["seconds_since_progress"] = _seconds_since(job.get("last_progress_at"))
        job["request_limit_s"] = ai_provider.drawing_timeout_s()
    return job


def start(web, project, then_skills=True, action="start", expected_run_id=None):
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
        checkpoint()
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

    return _JOB.start(web, project, {"answered": 0, "refused": 0, "rounds": 0, "last_progress_at": now(),
                                     "request_limit_s": ai_provider.drawing_timeout_s()}, work, action=action, expected_run_id=expected_run_id)


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
    failures = {}              # run id -> the last failed call of a task (it stays unanswered unless a later call works)
    answered = refused = calls = rounds = reused_replies = 0
    problem = ""
    connection_failures_in_a_row = 0
    connection_stopped = False
    for rounds in range(1, MAX_ROUNDS + 1):
        if connection_stopped:
            break
        step("build")
        autonomous_tasks_service.post(web, project, {"action": "run_all"})
        waiting = [record for record in _waiting(root) if tried.get(record["run_id"], 0) < MAX_ATTEMPTS]
        if not waiting:
            break
        step("answer", f"The AI is answering {len(waiting)} drawing task{'s' if len(waiting) != 1 else ''} (round {rounds})")
        for number, record in enumerate(waiting, start=1):
            checkpoint()
            tried[record["run_id"]] = tried.get(record["run_id"], 0) + 1
            images = _images(root, record)
            sizes = _sizes(images)
            grid = uses_grid(record["task"], sizes)
            # Saved before the call, so the screen can say which task the AI is on and for how long.
            _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                      current_task=record["task"], current_task_label=TASK_LABELS.get(record["task"], "A drawing task"),
                      current_target=str(record.get("target") or ""), task_number=number,
                      tasks_in_round=len(waiting), tasks_done=number - 1, tasks_remaining=len(waiting) - number + 1,
                      request_started_at=now(), last_progress_at=now(), last_problem=problem)
            try:
                provider = ai_provider.recorded(provider_for(record["task"]), root, f"drawing:{record['task']}")
                prompt = _prompt(record, grid)
                key = hashlib.sha256(json.dumps([prompt, getattr(provider, "model", ""),
                    [hashlib.sha256(Path(image).read_bytes()).hexdigest() for image in images]]).encode()).hexdigest()
                cached_path = root / "drawing_reply_cache" / f"{key}.json"
                cached = read_json(cached_path)
                if "reply" in cached:
                    reply, _raw = cached["reply"], cached.get("raw", {})
                    reused_replies += 1
                else:
                    checkpoint()
                    reply, _raw = provider.propose(prompt, image_paths=images)
                    _write_json(cached_path, {"reply": reply, "raw": _raw})
                    calls += 1
                checkpoint()
            except ai_provider.UsageLimitReached:
                raise
            except Exception as error:  # a failed call or an unreadable reply: tried again next round, up to MAX_ATTEMPTS
                calls += 1
                kind = failure_kind(error)
                failure = {"task": record["task"], "target": str(record.get("target") or ""),
                           "label": TASK_LABELS.get(record["task"], "A drawing task"), "kind": kind,
                           "message": failure_message(record, kind, error, again=tried[record["run_id"]] < MAX_ATTEMPTS)}
                failures[record["run_id"]] = failure
                problem = failure["message"]
                connection_failures_in_a_row = connection_failures_in_a_row + 1 if kind == "connection" else 0
                connection_stopped = connection_failures_in_a_row >= MAX_CONNECTION_FAILURES_IN_A_ROW
                if connection_stopped:
                    problem = (f"The connection to the AI service failed on {connection_failures_in_a_row} calls in a "
                               "row, so the drawing tasks stopped calling it. Nothing was concluded about the drawings; "
                               "try again when the connection works.")
                _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                          tasks_done=number, tasks_remaining=len(waiting) - number,
                          request_started_at=None, last_progress_at=now(), last_problem=problem)
                if connection_stopped:
                    break
                continue
            connection_failures_in_a_row = 0
            failures.pop(record["run_id"], None)
            size = _grid_size(record["task"], reply, sizes) if grid else None
            if size:
                reply = to_pixels(reply, size)
            try:
                autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": record["task"],
                                                             "target": record["target"], "reply": json.dumps(reply),
                                                             "model_note": f"AI provider ({getattr(provider, 'model', '') or 'default'})"})
            except Exception:
                cached_path.unlink(missing_ok=True)
                raise
            after = autonomous_tasks_service._current_task(root, record["task"], record["target"]) or {}
            if after.get("run_id") == record["run_id"] and after.get("status") == "waiting_for_reply":
                refused += 1
                cached_path.unlink(missing_ok=True)
                failures[record["run_id"]] = {"task": record["task"], "target": str(record.get("target") or ""),
                    "label": TASK_LABELS.get(record["task"], "A drawing task"), "kind": "answer_rejected",
                    "message": "The reply did not pass the drawing checks; completed tasks are kept."}
            else:
                answered += 1
            _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                      tasks_done=number, tasks_remaining=len(waiting) - number,
                      request_started_at=None, last_progress_at=now())
    # Tasks whose calls failed and that are still unanswered: listed with why, never read as a finding.
    unresolved = [failure for run_id, failure in failures.items()
                  if (autonomous_tasks_service._current_task(root, failure["task"], failure["target"]) or {}).get("run_id") == run_id
                  and (autonomous_tasks_service._current_task(root, failure["task"], failure["target"]) or {}).get("status") == "waiting_for_reply"]
    not_tried = len([record for record in _waiting(root) if record["run_id"] not in tried]) if connection_stopped else 0
    _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls, current_task=None,
              current_task_label=None, current_target=None, request_started_at=None, last_progress_at=now(),
              unresolved=unresolved, connection_stopped=connection_stopped, not_tried=not_tried)
    return {"answered": answered, "refused": refused, "rounds": rounds, "calls": calls, "reused_replies": reused_replies, "unresolved": unresolved,
            "connection_stopped": connection_stopped, "not_tried": not_tried}


def failure_kind(error):
    """connection, timeout, service_error or unreadable_reply: what went wrong with a call, not with the drawing."""
    if isinstance(error, ai_provider.ProviderTimeout):
        return "timeout"
    if isinstance(error, (ai_provider.ProviderTransportError, ConnectionError)) or (
            isinstance(error, OSError) and not isinstance(error, TimeoutError)):
        return "connection"
    if isinstance(error, ValueError):
        return "unreadable_reply"
    return "service_error"


def failure_message(record, kind, error, again):
    """One plain sentence for the Drawings screen. A failed call says nothing about the drawing, and the text says so."""
    where = f"{TASK_LABELS.get(record['task'], 'A drawing task')} ({_target_text(record)})"
    if kind == "connection":
        what = f"{where} couldn't get an answer: the connection to the AI service failed. Nothing was concluded about the drawing."
    elif kind == "timeout":
        what = f"{where} got no reply within {error.seconds} seconds. Nothing was concluded about the drawing."
    elif kind == "unreadable_reply":
        what = f"{where}: the AI's reply couldn't be read."
    else:
        what = f"{where}: the AI service returned an error. Nothing was concluded about the drawing."
    if again:
        return f"{what} It will be tried once more."
    return f"{what} {'It was left for a person to check.' if kind == 'unreadable_reply' else 'Try again to send it again.'}"


def _target_text(record):
    """Where a task looks, for the screen: "page 20" (from targets such as "page-20-dimension-1")."""
    match = re.search(r"page-(\d+)", str(record.get("target") or ""))
    return f"page {match.group(1)}" if match else "the drawing set"


def _progress(root, **fields):
    """Merge live progress into the running job's file (left alone when no run is in progress)."""
    path = root / JOB_FILE
    job = read_json(path)
    if job and job.get("status") in {"queued", "running"}:
        job.update(fields)
        write_json(path, job)
