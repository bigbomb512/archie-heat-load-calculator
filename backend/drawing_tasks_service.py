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
Claude models are the exception: they answer in the image's own pixels even when asked for the grid, so they are asked
for pixels and their replies are not converted (answers_in_pixels).

Started after the equipment reading; the skills start when it finishes, so the room skills see the traced rooms.
Every call is logged in the job's ai_usage.jsonl under "drawing:<task>" with its cost.
"""

import json
import hashlib
import re
import tempfile
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from PIL import Image

from backend import ai_provider
from backend.job_runner import BackgroundJob, now, read_json, write_json, checkpoint, _write_json

JOB_FILE = "drawing_tasks_job.json"
STEPS = [("build", "Preparing the drawing tasks"), ("answer", "The AI is answering the drawing tasks"),
         ("skills", "Starting the skills")]
MAX_ROUNDS = 16         # a successful retry can unlock several further rounds without another click
MAX_ATTEMPTS = 2        # a refused reply is tried once more, told why it was refused
# Connection failures on this many calls in a row mean the connection itself is broken (on Butcher Buffet,
# 2026-10-17, all 24 calls failed the same way): the run stops calling instead of spending a call on every task.
MAX_CONNECTION_FAILURES_IN_A_ROW = 3
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-drawing-tasks",
                     "The drawing tasks stopped before they finished (the server was restarted). Retry to finish them.")


# What each drawing task is doing, in the words the Drawings screen shows.
TASK_LABELS = {
    "P0_dimensions": "Reading the scale from a printed dimension",
    "P0_scale_read": "Reading the printed scale",
    "P0_scale_objects": "Estimating the scale from standard objects",
    "P0_wall_styles": "Telling the wall styles apart",
    "P0_room_names": "Finding the room names",
    "P0_room_outlines": "Tracing the room outlines",
    "P0_tenancy_outline": "Outlining the tenancy",
    "P0_outline_corners": "Matching outlines to the drawn corners",
    "P0_unnamed_space": "Checking for floor with no room",
    "P0_room_sizes": "Finding the room sizes",
    "S1_printed_areas": "Reading printed room areas",
    "S3_title_transcription": "Reading the title block",
    "P1_site": "Reading the site details",
    "P2_north": "Finding north",
    "P3_boundaries": "Working out what each wall faces",
    "P7_ceiling_heights": "Reading the ceiling heights",
    "P3_surroundings": "Reading what stands outside each outside wall",
    "P4_storefront_wall": "Finding which wall is the shopfront",
    "P4_openings": "Finding the shopfront openings",
    "P5_roof": "Working out what is above",
    "P6_kitchen": "Listing the kitchen equipment",
    "P8_glazing_certificate": "Reading the window glass from the energy certificate",
}


def _seconds_since(stamp):
    try:
        return max(0, round((datetime.now().astimezone() - datetime.fromisoformat(stamp)).total_seconds()))
    except (TypeError, ValueError):
        return None


def _not_needed_row(row, task_records):
    """A drawing question that wasn't needed, in the screen's words. A way of finding the scale that gave nothing
    usable is a fallback (the next way is used), not "not needed"; its reason is a plain sentence, never the checker's
    instruction to the AI (records saved before 2026-11-18 have that in block_reason)."""
    fallback = row["task"] in task_records.SCALE_FALLBACK_TASKS and bool((row.get("applied_value") or {}).get("fallback_required"))
    reason = task_records.scale_fallback_reason(row["task"], row.get("target")) if fallback else row.get("block_reason", "")
    return {"task": row["task"], "target": row["target"], "status": "not_needed", "fallback": fallback,
            "label": TASK_LABELS.get(row["task"], "Drawing check"), "reason": reason}


def status(web, project):
    job = _JOB.status(project)
    # A supervisor or manual reply may have repaired a task since this job finished.
    # Reconcile the response only; retain the original run's failure history on disk.
    from backend import autonomous_tasks_service as task_records
    job["not_needed_tasks"] = [_not_needed_row(row, task_records)
                               for row in task_records._all_current(Path(project["review_dir"])) if row.get("status") == "not_needed"]
    job["not_answered_tasks"] = [{"task": row["task"], "target": row["target"], "status": "not_answered",
                                  "label": TASK_LABELS.get(row["task"], "Drawing check"),
                                  "reason": ((row.get("reply_attempts") or [{}])[-1].get("reason") or row.get("block_reason") or "")[:300]}
                                 for row in task_records._all_current(Path(project["review_dir"])) if task_records._not_answered(row)]
    if job.get("status") == "done" and job.get("unresolved"):
        from backend import autonomous_tasks_service as tasks
        unresolved = []
        for failure in job["unresolved"]:
            current = tasks._current_task(Path(project["review_dir"]), failure["task"], failure["target"]) or {}
            if current.get("status") == "not_needed" or (current.get("status") in {"applied", "applied_fallback", "below_accuracy_bar"} and current.get("applied_value")):
                continue
            last = (current.get("reply_attempts") or [{}])[-1]
            if last.get("outcome") == "rejected":
                failure = {**failure, "kind": "answer_rejected",
                           "message": "The reply did not pass the drawing checks; completed tasks are kept."}
            unresolved.append(failure)
        job["unresolved"] = unresolved
    if job.get("provider_limited"):
        job["warning"] = "The drawing provider's usage limit was reached. Unanswered tasks are saved; retry after the limit resets."
    if job.get("status") == "done" and (job.get("unresolved") or job.get("provider_limited")):
        from backend.job_runner import recovery
        job["recovery"] = recovery("failed", job.get("warning") or "Some drawing tasks could not be resolved. Retry unfinished tasks; completed drawing tasks are kept.")
    elif job.get("status") in {"done", "failed", "interrupted", "none"} and _waiting(Path(project["review_dir"])):
        # A question added after the last run (a later step or a program update made it) has nothing to send it:
        # offer to resume rather than leave the job at the drawings stage (Butcher Buffet 2026-10-26). No AI call is
        # started from here.
        from backend.job_runner import recovery
        count = len(_waiting(Path(project["review_dir"])))
        job["waiting_tasks"] = count
        job["recovery"] = {**recovery("failed"), "action": "resume", "label": "Check and resume",
                           "reason": f"{count} drawing question{'s' if count != 1 else ''} added after the last run "
                                     f"{'are' if count != 1 else 'is'} waiting to be sent."}
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
            result = answer_all(web, current, step, retry_not_answered=action == "retry")
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


def _waiting(root, retry_not_answered=False):
    """Questions still to send. One the checker refused twice is settled as not answered and isn't resent by a run or a
    resume, only when the operator deliberately retries (action "retry")."""
    from backend import autonomous_tasks_service
    return [record for record in autonomous_tasks_service._all_current(root)
            if record.get("status") == "waiting_for_reply" and record.get("prompt")
            and not autonomous_tasks_service.north_waiting_on_others(record)
            and (retry_not_answered or not autonomous_tasks_service._not_answered(record))]


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


def _retry_smaller_tenancy(provider, prompt, images):
    """One retry of a timed-out whole-floor outline with the plan image at most 1024 px. The question asks for points
    in image pixels, so the reply's points are scaled back to the full-size image the packet's transform is for."""
    with tempfile.TemporaryDirectory(prefix="archie-tenancy-retry-") as folder:
        smaller, factor = [], 1.0
        for index, path in enumerate(images):
            target = Path(folder) / f"image-{index}.png"
            with Image.open(path) as opened:
                image = opened.copy()
                width = image.width
                image.thumbnail((1024, 1024))
                image.save(target)
            if index == 0:
                factor = width / image.width if image.width else 1.0
            smaller.append(str(target))
        reply, raw = provider.propose(prompt, image_paths=smaller)
    if factor == 1.0:
        return reply, raw
    from ai.autonomous_tasks import parse_json_reply
    try:
        value = parse_json_reply(reply)
    except ValueError:
        return reply, raw              # refused by the outline check as usual
    if isinstance(value.get("points_px"), list):
        value["points_px"] = [[float(point[0]) * factor, float(point[1]) * factor]
                              if isinstance(point, (list, tuple)) and len(point) >= 2
                              and all(isinstance(v, (int, float)) for v in point[:2]) else point
                              for point in value["points_px"]]
    if isinstance(raw, dict):
        raw = {**raw, "retried_smaller_image": {"scale_back": round(factor, 4)}}
    return json.dumps(value), raw


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
NO_GRID_TASKS = {"P0_room_sizes", "P0_outline_corners"}   # the corner question answers in corner numbers


def answers_in_pixels(provider):
    """True for a model that answers in the image's own pixels whatever the prompt asks: Claude. On the 2026.08.06 set
    (2026-11-18) Claude Haiku 5.5 was asked for the 0-1000 grid on a 1,536 x 1,106 plan and answered in pixels (its own
    note put the house's east wall at "x≈1022", and one corner was at x 1024, off the grid); converting those as grid
    values moved every point right by half again and down by a tenth, so the "Living" sketch landed on the east wing's
    bedrooms (78.2 m²) and the "Bedroom" sketch on the kitchen. The Turramurra tenancy reached x 1789 on a 1,536-px
    image the same way. Such a model is asked for pixels (the tasks' own wording) and its reply is not converted."""
    model = str(getattr(provider, "model", "") or "").casefold()
    name = str(getattr(provider, "name", "") or "").casefold()
    return "claude" in model or name == "anthropic"


def uses_grid(task, sizes, provider=None):
    if task in NO_GRID_TASKS or (provider is not None and answers_in_pixels(provider)):
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


def _px_values(value, key=""):
    if isinstance(value, dict):
        return [number for name, item in value.items() for number in _px_values(item, name)]
    if isinstance(value, list):
        if key.endswith("_px"):
            return [item for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)] + \
                [number for item in value if isinstance(item, (list, dict)) for number in _px_values(item, key)]
        return [number for item in value for number in _px_values(item, key)]
    return []


def grid_to_pixels(reply, size):
    """to_pixels for a reply asked on the 0-1000 grid. A reply with a *_px value beyond 1000 can't be on that grid (the
    model answered in pixels anyway), so it is left as it is rather than stretched off the image."""
    if not size:
        return reply
    if any(number > 1000 for number in _px_values(reply)):
        return reply
    return to_pixels(reply, size)


def _prompt(record, grid=False):
    """The task's prompt (asking for grid coordinates when they will be converted); on a second try, with the reason
    the last reply was refused."""
    prompt = record["prompt"] + (GRID_NOTE if grid else "")
    last = (record.get("reply_attempts") or [{}])[-1]
    if last.get("outcome") == "rejected" and last.get("reason"):
        return (f"{prompt}\n\nYour previous reply was refused: {last['reason']} "
                "Reply again, following the format above exactly (JSON only).")
    return prompt


def answer_all(web, project, step=lambda *args: None, provider_for=None, retry_not_answered=False):
    """Answer the waiting drawing tasks in rounds. Returns {"answered", "refused", "rounds", "calls"}.

    provider_for(task) gives the provider for a task (tests pass a fake); by default ai_provider.get("drawing:<task>").
    retry_not_answered: the operator's deliberate retry also asks again the questions refused twice before.

    The calculation evidence (about half a minute to rebuild on a house set) isn't rebuilt after every round: a round
    that applied answers marks it stale, a question that reads the traced areas rebuilds it first, and it is rebuilt
    once at the end (also when the run stops on an error, so completed answers reach it; not when it is cancelled)."""
    from backend import autonomous_tasks_service
    from backend.job_runner import JobCancelled

    def build_evidence():
        if web is not None:
            from backend.phase1_supervisor_service import _build_calculation_evidence
            _build_calculation_evidence(web, project)

    with autonomous_tasks_service.deferred_evidence_rebuild(build_evidence) as evidence:
        try:
            result = _answer_rounds(web, project, step, provider_for, retry_not_answered)
        except JobCancelled:
            raise
        except BaseException:
            try:
                autonomous_tasks_service.ensure_evidence_current()
            except Exception as error:       # the run's own error is the one to report
                print(f"Couldn't rebuild the calculation evidence after the drawing tasks stopped: {error}", flush=True)
            raise
        autonomous_tasks_service.ensure_evidence_current()
    result["evidence_rebuilds"] = evidence["builds"]
    return result


def _answer_rounds(web, project, step, provider_for, retry_not_answered):
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
        waiting = [record for record in _waiting(root, retry_not_answered) if tried.get(record["run_id"], 0) < MAX_ATTEMPTS]
        if not waiting:
            break
        step("answer", f"The AI is answering {len(waiting)} drawing task{'s' if len(waiting) != 1 else ''} (round {rounds})")
        # All prompts in a round describe the same saved drawing state. Fetch them concurrently,
        # then publish in the stable task order below on this job thread.
        from backend.job_runner import _context, current_check
        check = current_check()
        def fetch(number, record):
            checkpoint()
            current = autonomous_tasks_service._current_task(root, record["task"], record["target"])
            if current and (current.get("run_id") != record["run_id"] or current.get("status") != "waiting_for_reply"):
                return {"record": record, "skipped": True}
            tried[record["run_id"]] = tried.get(record["run_id"], 0) + 1
            images = _images(root, record)
            sizes = _sizes(images)
            provider = ai_provider.recorded(provider_for(record["task"]), root, f"drawing:{record['task']}")
            grid = uses_grid(record["task"], sizes, provider)
            prompt = _prompt(record, grid)
            key = hashlib.sha256(json.dumps([prompt, getattr(provider, "model", ""),
                [hashlib.sha256(Path(image).read_bytes()).hexdigest() for image in images]]).encode()).hexdigest()
            cached_path = root / "drawing_reply_cache" / f"{key}.json"
            cached = read_json(cached_path)
            if "reply" in cached:
                return {"record": record, "reply": cached["reply"], "raw": cached.get("raw", {}),
                        "provider": provider, "images": images, "sizes": sizes, "key": key, "cached": True}
            previous = getattr(_context, "check", None)
            _context.check = check
            try:
                checkpoint()
                try:
                    reply, raw = provider.propose(prompt, image_paths=images)
                except ai_provider.ProviderTimeout:
                    if record["task"] != "P0_tenancy_outline" or not images:
                        raise
                    reply, raw = _retry_smaller_tenancy(provider, prompt, images)
                _write_json(cached_path, {"reply": reply, "raw": raw})
                return {"record": record, "reply": reply, "raw": raw, "provider": provider,
                        "images": images, "sizes": sizes, "key": key, "cached": False}
            finally:
                _context.check = previous

        fetched = {}
        provider_limited = None
        # Saved before the calls, so the screen can say what the AI is answering and for how long.
        single = waiting[0] if len(waiting) == 1 else None
        _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                  current_task=single["task"] if single else None,
                  current_task_label=(TASK_LABELS.get(single["task"], "A drawing task") if single
                                      else f"{len(waiting)} drawing questions at once"),
                  current_target=str(single.get("target") or "") if single else None, task_number=1,
                  tasks_in_round=len(waiting), tasks_done=0, tasks_remaining=len(waiting),
                  request_started_at=now(), last_progress_at=now(), last_problem=problem)
        with ThreadPoolExecutor(max_workers=ai_provider.parallel_calls()) as pool:
            futures = {pool.submit(fetch, number, record): (number, record)
                       for number, record in enumerate(waiting, start=1)}
            pending = set(futures)
            while pending and provider_limited is None:
                checkpoint()
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    number, record = futures[future]
                    try:
                        fetched[record["run_id"]] = future.result()
                    except ai_provider.UsageLimitReached as error:
                        provider_limited = error
                        for queued in pending:
                            queued.cancel()
                        break
                    except Exception as error:
                        fetched[record["run_id"]] = {"record": record, "error": error, "number": number}
        if provider_limited is None:
            limited_results = [item["error"] for item in fetched.values()
                              if isinstance(item.get("error"), ai_provider.UsageLimitReached)]
            if limited_results:
                provider_limited = limited_results[0]
        if provider_limited:
            raise provider_limited
        if not fetched:
            break

        for number, record in enumerate(waiting, start=1):
            checkpoint()
            result = fetched.get(record["run_id"], {"record": record, "skipped": True})
            if result.get("skipped"):
                continue
            if result.get("error"):
                error = result["error"]
                calls += 1
                if record["task"] == "P0_tenancy_outline" and isinstance(error, ai_provider.ProviderTimeout):
                    current = autonomous_tasks_service._current_task(root, record["task"], record["target"])
                    if current and current.get("run_id") == record["run_id"]:
                        current.update(status="below_accuracy_bar", source="timeout_fallback",
                            applied_value={"note": "Whole-floor outline timed out at both image sizes; room-specific area routes continue without tenancy clipping."},
                            block_reason="The whole-floor outline could not be read within the provider limit.", finished_at=now())
                        autonomous_tasks_service._write(autonomous_tasks_service._record_file(root, current), current)
                        refused += 1
                        continue
                if isinstance(error, ai_provider.InvalidProviderReply):
                    archive_investigation_failure(project, record, error, model_note="Drawing task")
                    refused += 1
                    failures[record["run_id"]] = {"task": record["task"], "target": str(record.get("target") or ""),
                        "label": TASK_LABELS.get(record["task"], "A drawing task"), "kind": "answer_rejected",
                        "message": "The reply did not pass the drawing checks; completed tasks are kept."}
                    continue
                kind = failure_kind(error)
                if kind != "connection":
                    # A call that failed (timeout, service error) counts as an attempt: one that fails twice settles as
                    # not answered rather than waiting for ever. A broken connection concludes nothing and isn't counted.
                    archive_investigation_failure(project, record, error, model_note="Drawing task")
                failure = {"task": record["task"], "target": str(record.get("target") or ""),
                           "label": TASK_LABELS.get(record["task"], "A drawing task"), "kind": kind,
                           "message": failure_message(record, kind, error, again=tried[record["run_id"]] < MAX_ATTEMPTS)}
                failures[record["run_id"]] = failure
                problem = failure["message"]
                # Count connection failures in deterministic completion order (apply order).
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
            images, sizes = result["images"], result["sizes"]
            provider, reply = result["provider"], result["reply"]
            grid = uses_grid(record["task"], sizes, provider)
            if result["cached"]:
                reused_replies += 1
            else:
                calls += 1
            cache_path = root / "drawing_reply_cache" / f"{result['key']}.json"
            # Saved before the call, so the screen can say which task the AI is on and for how long.
            _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                      current_task=record["task"], current_task_label=TASK_LABELS.get(record["task"], "A drawing task"),
                      current_target=str(record.get("target") or ""), task_number=number,
                      tasks_in_round=len(waiting), tasks_done=number - 1, tasks_remaining=len(waiting) - number + 1,
                      request_started_at=now(), last_progress_at=now(), last_problem=problem)
            connection_failures_in_a_row = 0
            failures.pop(record["run_id"], None)
            if reply is not None:
                size = _grid_size(record["task"], reply, sizes) if grid else None
                if size:
                    reply = grid_to_pixels(reply, size)
            try:
                autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": record["task"],
                                                                 "target": record["target"], "reply": json.dumps(reply),
                                                                 "model_note": f"AI provider ({getattr(provider, 'model', '') or 'default'})",
                                                                 "defer": True})
            except Exception:
                cache_path.unlink(missing_ok=True)
                raise
            after = autonomous_tasks_service._current_task(root, record["task"], record["target"]) or {}
            if autonomous_tasks_service.north_waiting_on_others(after):
                answered += 1           # its answer is in; north is worked out when the other pages have answered
            elif after.get("run_id") == record["run_id"] and after.get("status") == "waiting_for_reply":
                refused += 1
                cache_path.unlink(missing_ok=True)
                failures[record["run_id"]] = {"task": record["task"], "target": str(record.get("target") or ""),
                    "label": TASK_LABELS.get(record["task"], "A drawing task"), "kind": "answer_rejected",
                    "message": "The reply did not pass the drawing checks; completed tasks are kept."}
            elif after.get("status") != "waiting_for_reply":
                answered += 1
            _progress(root, answered=answered, refused=refused, rounds=rounds, calls=calls,
                      tasks_done=number, tasks_remaining=len(waiting) - number,
                      request_started_at=None, last_progress_at=now())
        if any((autonomous_tasks_service._current_task(root, row["task"], row["target"]) or {}).get("status")
               in {"applied", "applied_fallback", "below_accuracy_bar"} for row in waiting):
            autonomous_tasks_service._export_determinations(root)
            if web is not None and any(row["task"] == "S1_printed_areas" for row in waiting):
                from backend import room_use_resolution_service
                room_use_resolution_service.post(web, project, {"action": "resolve"})
            # Marked stale; rebuilt when a question reads the traced areas, and once at the end (answer_all).
            autonomous_tasks_service.evidence_changed(web, project)
            # The questions are rebuilt once, by the next round's run_all (or after the last round, below).
            from backend import productization
            productization.record_change_if_fingerprint_changed(root, action="autonomous_drawing_round_applied",
                target=f"round-{rounds}", previous_fingerprint="",
                new_fingerprint=hashlib.sha256("|".join(sorted(row["run_id"] for row in waiting)).encode()).hexdigest(),
                affected_ids=[record["run_id"] for record in waiting])
    else:
        # The round limit ran out: the last round's answers still unlock their next questions (rebuilt once).
        autonomous_tasks_service.post(web, project, {"action": "run_all"})
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
    if isinstance(error, (ValueError, ai_provider.InvalidProviderReply)):
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


def request_investigation(project, record, provider_for=None, check=None):
    """Request/cache one targeted reply. Publication is serialized by the supervisor after ownership checks."""
    from backend.job_runner import _context, current_check
    previous_check = getattr(_context, "check", None)
    _context.check = check or current_check()
    try:
        checkpoint()
        root = Path(project["review_dir"])
        images = _images(root, record)
        sizes = _sizes(images)
        provider = (provider_for or (lambda task: ai_provider.get(f"drawing:{task}")))(record["task"])
        grid = uses_grid(record["task"], sizes, provider)
        prompt = _prompt(record, grid)
        key = hashlib.sha256(json.dumps([prompt, getattr(provider, "model", ""),
            [hashlib.sha256(Path(image).read_bytes()).hexdigest() for image in images]]).encode()).hexdigest()
        cached_path = root / "drawing_reply_cache" / f"{key}.json"
        cached = read_json(cached_path)
        reused = "reply" in cached
        if reused:
            reply, raw = cached["reply"], cached.get("raw", {})
        else:
            reply, raw = ai_provider.recorded(provider, root, f"supervisor:{record['task']}").propose(prompt, image_paths=images)
            # A cancelled caller may reuse the paid answer. Domain publication still requires ownership below.
            _write_json(cached_path, {"reply": reply, "raw": raw})
        checkpoint()
        size = _grid_size(record["task"], reply, sizes) if grid else None
        return {"reply": grid_to_pixels(reply, size), "raw": raw,
                "cache_path": str(cached_path), "reused": reused}
    finally:
        _context.check = previous_check


def apply_investigation(web, project, record, result):
    from backend import autonomous_tasks_service as tasks
    checkpoint()
    root = Path(project["review_dir"])
    current = tasks._current_task(root, record["task"], record["target"]) or {}
    if current.get("run_id") != record["run_id"]:
        return {"status": "superseded", "reason": "The drawing inputs changed while this check was running."}
    tasks.post(web, project, {"action": "validate_apply", "task": record["task"], "target": record["target"],
                            "reply": json.dumps(result["reply"]), "model_note": "Targeted drawing check"},
               defer_evidence_rebuild=True)
    after = tasks._current_task(root, record["task"], record["target"]) or {}
    if not after.get("applied_value") or after.get("status") in {"blocked", "waiting_for_reply"}:
        # Keep the reply in its run archive; a rejected answer may not be a cache hit next time.
        Path(result["cache_path"]).unlink(missing_ok=True)
    return {"status": after.get("status"), "reason": after.get("block_reason", ""), "reused": result["reused"]}


def archive_investigation_failure(project, record, error, model_note="Targeted drawing check"):
    """Keep failed targeted replies beside accepted/rejected task replies, with a specific failure reason."""
    from backend import autonomous_tasks_service as tasks
    checkpoint()
    root = Path(project["review_dir"])
    if (tasks._current_task(root, record["task"], record["target"]) or {}).get("run_id") != record["run_id"]:
        return
    raw = getattr(error, "raw_record", {}) or {}
    path = tasks._archive_reply(root, record, str(raw.get("reply_text") or ""), model_note)
    entry = read_json(path)
    rejected = isinstance(error, ai_provider.InvalidProviderReply)
    outcome = "rejected" if rejected else "provider_failed"
    reason = str(error)[:500]
    entry.update(outcome=outcome, reason=reason, raw_record=raw)
    write_json(path, entry)
    record["reply_attempts"][-1].update(outcome=outcome, reason=reason)
    if rejected:
        record.update(status="waiting_for_reply", block_reason=reason, validation={"valid": False, "error": reason})
    tasks._update_record(root, record)
    return {"status": "waiting_for_reply" if rejected else "provider_failed", "reason": reason}
