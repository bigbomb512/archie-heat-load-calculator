"""A long job for one project, run in a server thread with its progress in a JSON file.

The workspace starts a job, polls its status, and can be closed or reloaded meanwhile.
Used by page_preparation_service and calculation_service.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import traceback
import uuid
import fcntl
from contextlib import contextmanager, nullcontext
from functools import wraps


class JobCancelled(BaseException):
    """Control flow: ordinary provider error handlers must not swallow cancellation."""


_context = threading.local()


def checkpoint():
    check = getattr(_context, "check", None)
    if check:
        check()


def current_check():
    return getattr(_context, "check", None) or (lambda: None)


def guarded_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        guard = getattr(_context, "guard", None)
        with guard() if guard else nullcontext():
            checkpoint()
            return function(*args, **kwargs)
    return wrapped


# Every step's file uses its own status words ("needs_review", "interrupted", "queued"...); step_state turns any of them
# into one of five, so the screens, the recovery buttons and the prerequisite checks agree on what a word means.
_ACTIVE = frozenset({"queued", "running", "working", "reading", "in_progress", "pending"})
_DONE = frozenset({"done", "complete", "completed", "confirmed", "ready", "needs_review", "provisional"})
_FAILED = frozenset({"failed", "error", "blocked", "interrupted"})


def step_state(status):
    """waiting, active, stopping, done or failed, for any step's own status word."""
    status = str(status or "").lower()
    return ("stopping" if status == "stopping" else "active" if status in _ACTIVE else "done" if status in _DONE
            else "failed" if status in _FAILED else "waiting")


def recovery(state, reason=""):
    kind = step_state(state)
    if kind == "done":
        return {"action": None, "label": "", "reason": reason}
    if kind == "stopping":
        return {"action": None, "label": "Restarting…", "reason": reason or "Waiting for in-flight work to finish or time out; saved results are kept."}
    if kind == "active":
        return {"action": "restart", "label": "Restart this step", "reason": reason}
    return {"action": "resume", "label": "Try again" if state in {"failed", "error", "interrupted", "stale"} else "Check and resume", "reason": reason}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.stage")
    stage.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(stage, path)


def write_json(path, value):
    guard = getattr(_context, "guard", None)
    if guard:
        with guard():
            _write_json(path, value)
    else:
        checkpoint()
        _write_json(path, value)


def _process_alive(pid):
    """True when a process with this id is running (another server process may own a job)."""
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class BackgroundJob:
    """One job file per project; at most one run per folder across local server processes."""

    def __init__(self, file_name, steps, thread_name, interrupted_message):
        self.file_name = file_name
        self.steps = list(steps)  # [(key, label)]
        self.labels = dict(self.steps)
        self.thread_name = thread_name
        self.interrupted_message = interrupted_message
        self.lock = threading.RLock()
        self.running = set()
        self._held_locks = threading.local()
        self._project_locks = {}

    def path(self, project):
        return Path(project["review_dir"]) / self.file_name

    def key(self, project):
        """Runs are tracked per job file (the project folder), not per project ID: two project
        entries can point at the same folder, and must not start two runs over one job file."""
        return str(self.path(project).resolve())

    @contextmanager
    def mutation_lock(self, project):
        # The file lock also serializes requests from another local server process.
        key = self.key(project)
        with self.lock:
            project_lock = self._project_locks.setdefault(key, threading.RLock())
        with project_lock:
            held = getattr(self._held_locks, "keys", set())
            if key in held:
                yield
                return
            lock_path = self.path(project).with_suffix(".lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                self._held_locks.keys = held | {key}
                try:
                    yield
                finally:
                    self._held_locks.keys = held
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def status(self, project):
        job = read_json(self.path(project))
        if not job:
            return {"id": project["id"], "status": "none", "step_state": "waiting", "recovery": recovery("none")}
        alive = (self.key(project) in self.running
                 or (job.get("pid") != os.getpid() and _process_alive(job.get("pid"))))
        if job.get("status") in {"queued", "running"} and not alive:
            job = {**job, "status": "interrupted", "error": self.interrupted_message}
        if alive and job.get("restart_requested"):
            job = {**job, "status": "stopping"}
            age = time.time() - job.get("restart_requested_at", time.time())
            job["restart_stalled"] = age > job.get("request_limit_s", 600)
        reason = ("The previous request is taking longer than its expected timeout. Restart is still pending; no replacement has started."
                  if job.get("restart_stalled") else "")
        return {"id": project["id"], **job, "step_state": step_state(job.get("status")),
                "recovery": recovery(job.get("status"), reason)}

    def request_restart(self, project, expected_run_id=None):
        """Signal an existing worker without revalidating its now-unavailable inputs/provider."""
        with self.mutation_lock(project):
            current = read_json(self.path(project))
            if expected_run_id and current.get("job_id") != expected_run_id:
                return {**self.status(project), "deduplicated": True}
            alive = self.key(project) in self.running or (current.get("pid") != os.getpid() and _process_alive(current.get("pid")))
            if current.get("status") not in {"queued", "running"} or not alive:
                return None
            if not current.get("restart_requested"):
                current.update(restart_requested=True, restart_requested_at=time.time())
                _write_json(self.path(project), current)
            return {**self.status(project), "deduplicated": True}

    def _create(self, project, fields):
        first = self.steps[0][0]
        job = {"schema_version": 1, "job_id": uuid.uuid4().hex, "status": "running", "step": first,
               "step_label": self.labels[first], "started_at": now(), "pid": os.getpid(), **fields}
        _write_json(self.path(project), job)
        self.running.add(self.key(project))
        return job

    def start(self, web, project, fields, work, action="start", expected_run_id=None):
        checkpoint()
        with self.mutation_lock(project):
            current = read_json(self.path(project))
            if action == "restart" and expected_run_id and current.get("job_id") != expected_run_id:
                return {**self.status(project), "deduplicated": True}
            if current.get("status") in {"queued", "running"} and (
                    self.key(project) in self.running
                    or (current.get("pid") != os.getpid() and _process_alive(current.get("pid")))):
                if action == "restart" and not current.get("restart_requested"):
                    current.update(restart_requested=True, restart_requested_at=time.time())
                    _write_json(self.path(project), current)
                return {**self.status(project), "deduplicated": True}
            job = self._create(project, fields)
        threading.Thread(target=self._run, args=(web, project, job["job_id"], fields, work),
                         daemon=True, name=self.thread_name).start()
        return self.status(project)

    def _run(self, web, project, job_id, fields, work):
        started = time.monotonic()
        path = self.path(project)

        def check():
            job = read_json(path)
            if job.get("job_id") != job_id or job.get("restart_requested"):
                raise JobCancelled()

        @contextmanager
        def guard():
            with self.mutation_lock(project):
                check()
                yield

        def step(key, label=None):
            with guard():
                job = read_json(path)
                job.update(step=key, step_label=label or self.labels[key])
                _write_json(path, job)

        _context.check, _context.guard = check, guard
        outcome = {}
        try:
            extra = work(web, project["id"], step) or {}
            check()
            outcome = {"status": "done", **extra}
        except JobCancelled:
            outcome = {"status": "interrupted"}
        except Exception as error:
            traceback.print_exc()
            outcome = {"status": "failed", "error": str(error) or error.__class__.__name__}
        finally:
            _context.check = _context.guard = None
            replacement = None
            with self.mutation_lock(project):
                job = read_json(path)
                if job.get("job_id") == job_id:
                    if job.get("restart_requested"):
                        replacement = self._create(project, fields)
                    else:
                        job.update(outcome, finished_at=now(), seconds=round(time.monotonic() - started, 1))
                        _write_json(path, job)
                        self.running.discard(self.key(project))
                else:
                    self.running.discard(self.key(project))
            if replacement:
                threading.Thread(target=self._run, args=(web, project, replacement["job_id"], fields, work),
                                 daemon=True, name=self.thread_name).start()

            elif outcome.get("status") == "done" and self.file_name in {
                    "new_job_job.json", "page_preparation_job.json", "page_inventory_job.json",
                    "page_extraction_job.json", "drawing_tasks_job.json", "phase1_supervisor_job.json"} and web is not None:
                try:
                    from backend.calculation_service import maybe_start_phase1
                    maybe_start_phase1(web, project)
                except Exception:
                    traceback.print_exc()
