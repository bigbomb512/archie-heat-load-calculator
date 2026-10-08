"""Pass 1 of the PDF review on the server: read every page of the job's PDF with the AI, one page per call.

The AI runs through the Codex CLI signed in with the team's ChatGPT subscription (no API key).
Each page is rendered to an image, sent on its own, and the checked reply is saved. Replies are
cached by page image and prompt version, so a retry only reads the pages that failed and a
re-analysis of the same PDF repeats nothing. Progress is in page_inventory_job.json; the
readings are in page_inventory.json.

It starts by itself after PDF analysis unless the job's page reading is switched off. If the
Codex CLI is missing or not signed in, the job is shown as blocked; if any page fails, the run
is failed (the pages that were read are kept) and Retry reads the rest.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from ai.page_inventory import PROMPT_VERSION, build_prompt, packet_from_readings, main_plan_pages, validate_reply
from backend.job_runner import BackgroundJob, now, read_json, write_json

JOB_FILE = "page_inventory_job.json"
RESULT_FILE = "page_inventory.json"
SETTINGS_FILE = "page_reading_settings.json"
WORK_DIR = "page_inventory"
DPI = 100
WORKERS = 4
PAGE_TIMEOUT_S = 300
STEPS = [("render", "Preparing page images"), ("read", "Reading pages")]
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-page-inventory",
                     "Reading the pages stopped before it finished (the server was restarted). Retry to read the rest.")
_RUNNING = _JOB.running
# Tests replace this with a fake provider: factory() -> object with propose(prompt, image_paths) -> (reply dict, raw record).
PROVIDER_FACTORY = None


class PageReadingUnavailable(RuntimeError):
    pass


class CodexCliPageReader:
    """One page per `codex exec` call, signed in with ChatGPT (read-only sandbox, nothing kept)."""

    def __init__(self, executable=None, timeout=PAGE_TIMEOUT_S):
        self.executable = executable or shutil.which("codex")
        self.timeout = timeout
        self.model = os.environ.get("ARCHIE_CODEX_MODEL", "").strip()
        if not self.executable:
            raise PageReadingUnavailable("The Codex CLI isn't installed on this computer. Install it and sign in with ChatGPT (codex login).")

    def check_signed_in(self):
        try:
            result = subprocess.run([self.executable, "login", "status"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PageReadingUnavailable(f"The Codex CLI couldn't be started: {error}") from error
        if result.returncode != 0 or "logged in" not in (result.stdout + result.stderr).lower():
            raise PageReadingUnavailable("The Codex CLI isn't signed in. Run `codex login` and sign in with ChatGPT.")

    def propose(self, prompt, image_paths=()):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="archie-page-read-") as folder:
            output = Path(folder) / "reply.json"
            command = [self.executable, "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check",
                       "--output-last-message", str(output)]
            if self.model:
                command += ["--model", self.model]
            for image in image_paths:
                command += ["--image", str(image)]
            command.append("-")
            # The model process gets no API key from this server's environment.
            env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
            try:
                result = subprocess.run(command, input=prompt, capture_output=True, text=True,
                                        timeout=self.timeout, env=env, check=False)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"No reply within {self.timeout} seconds.") from error
            reply_text = output.read_text(encoding="utf-8") if output.is_file() else ""
            raw = {"provider": "codex_cli", "model": self.model or "codex default", "exit_code": result.returncode,
                   "reply_text": reply_text[-20000:], "stderr_tail": (result.stderr or "")[-2000:]}
            if result.returncode != 0 or not reply_text.strip():
                tail = " ".join((result.stderr or result.stdout or "").split())[-300:]
                raise RuntimeError(f"Codex CLI failed (exit {result.returncode}): {tail or 'no reply'}")
            text = reply_text.strip()
            if text.startswith("```"):
                text = text.strip("`").removeprefix("json").strip()
            try:
                return json.loads(text), raw
            except json.JSONDecodeError as error:
                raise RuntimeError("The reply was not valid JSON.") from error


def _provider():
    if PROVIDER_FACTORY is not None:
        return PROVIDER_FACTORY()
    reader = CodexCliPageReader()
    reader.check_signed_in()
    return reader


def pdf_signature(pdf_path):
    stat = Path(pdf_path).stat()
    return f"{Path(pdf_path).name}|{stat.st_size}|{stat.st_mtime_ns}"


def render_pages(pdf_path, folder, dpi=DPI):
    """Render every page once per PDF version; returns {page: image path}."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "rendered.json"
    signature = f"{pdf_signature(pdf_path)}|{dpi}"
    if read_json(marker).get("signature") != signature:
        for old in folder.glob("p-*.png"):
            old.unlink()
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(folder / "p")],
                       check=True, capture_output=True, timeout=1800)
        write_json(marker, {"signature": signature})
    return {int(path.stem.split("-")[1]): path for path in sorted(folder.glob("p-*.png"))}


def _cache_key(image_path, model):
    digest = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
    return hashlib.sha256(f"{digest}|{PROMPT_VERSION}|{model}".encode()).hexdigest()[:32]


def read_pages(images, cache_dir, provider, workers=WORKERS, on_page=None):
    """Read each page image (cached). Returns ({page: reading}, {page: failure reason}, calls made)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "") or "default"
    prompt = build_prompt()
    readings, failures, calls = {}, {}, 0
    lock = threading.Lock()

    def one(page, image):
        cached = read_json(cache_dir / f"{_cache_key(image, model)}.json")
        if cached.get("reading"):
            return page, cached["reading"], None, False
        try:
            reply, raw = provider.propose(prompt, image_paths=[image])
            reading = validate_reply(reply)
        except Exception as error:
            return page, None, " ".join(str(error).split())[:400] or error.__class__.__name__, True
        write_json(cache_dir / f"{_cache_key(image, model)}.json",
                   {"page": page, "prompt_version": PROMPT_VERSION, "model": model, "read_at": now(),
                    "reading": reading, "raw": raw})
        return page, reading, None, True

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, page, image) for page, image in sorted(images.items())]
        for future in as_completed(futures):
            page, reading, failure, called = future.result()
            with lock:
                calls += int(called)
                if reading:
                    readings[page] = reading
                else:
                    failures[page] = failure
                if on_page:
                    on_page(len(readings) + len(failures), len(images))
    return readings, failures, calls


def _settings(project):
    return read_json(Path(project["review_dir"]) / SETTINGS_FILE)


def enabled(project):
    return _settings(project).get("enabled", True) is not False


def summary(project):
    """The readings for the workspace: each page's type, title, level and information, plus the main plans."""
    result = read_json(Path(project["review_dir"]) / RESULT_FILE)
    readings = {int(page): row for page, row in (result.get("pages") or {}).items()}
    failures = {int(page): reason for page, reason in (result.get("failures") or {}).items()}
    page_count = int(result.get("page_count") or 0)
    pages = []
    for page in range(1, page_count + 1):
        row = readings.get(page)
        pages.append({"page": page, "status": "read" if row else "failed" if page in failures else "not_read",
                      "reason": failures.get(page, ""), **(row or {})})
    return {"page_count": page_count, "read": len(readings), "failed": len(failures),
            "main_plans": main_plan_pages(readings), "pages": pages,
            "prompt_version": result.get("prompt_version", ""), "model": result.get("model", "")}


def status(web, project):
    # The reason a run failed or is blocked is sent as "problem": the workspace treats a reply with an
    # "error" field as a failed request, which would hide the very run that needs attention.
    job = _JOB.status(project)
    problem = job.pop("error", "")
    return {**job, "problem": problem, "enabled": enabled(project), **summary(project)}


def set_enabled(web, project, data):
    write_json(Path(project["review_dir"]) / SETTINGS_FILE, {"enabled": data.get("enabled") is not False, "updated_at": now()})
    return status(web, project)


def start(web, project, data=None):
    data = data or {}
    root = Path(project["review_dir"])
    pdf = Path(str(project.get("pdf") or ""))
    if not pdf.is_file():
        raise ValueError("The job's PDF isn't available on this computer, so its pages can't be read.")
    if not enabled(project):
        raise ValueError("Page reading is switched off for this job. Switch it on to read the pages.")
    try:
        provider = _provider()
    except PageReadingUnavailable as error:
        if _JOB.status(project).get("status") not in {"queued", "running"}:
            write_json(_JOB.path(project), {"schema_version": 1, "status": "blocked", "error": str(error), "finished_at": now()})
        return status(web, project)
    fields = {"requested_by": " ".join(str(data.get("requested_by") or "").split())[:80], "pages_done": 0, "page_count": 0}

    def work(web, project_id, step):
        step("render")
        images = render_pages(pdf, root / WORK_DIR / "pages")
        job_path = _JOB.path(project)

        def progress(done, total):
            job = read_json(job_path)
            job.update({"pages_done": done, "page_count": total})
            write_json(job_path, job)

        step("read", f"Reading {len(images)} pages")
        readings, failures, calls = read_pages(images, root / WORK_DIR / "replies", provider, on_page=progress)
        write_json(root / RESULT_FILE, {
            "schema_version": 1, "prompt_version": PROMPT_VERSION, "model": getattr(provider, "model", "") or "default",
            "pdf_signature": pdf_signature(pdf), "read_at": now(), "page_count": len(images),
            "pages": {str(page): row for page, row in sorted(readings.items())},
            "failures": {str(page): reason for page, reason in sorted(failures.items())},
            "packet": packet_from_readings(readings, len(images)),
        })
        if failures:
            pages = ", ".join(str(page) for page in sorted(failures))
            first = failures[min(failures)]
            raise RuntimeError(f"{len(failures)} of {len(images)} pages couldn't be read (pages {pages}). "
                               f"First reason: {first} Retry to read them.")
        return {"pages_read": len(readings), "calls": calls}

    started = _JOB.start(web, project, fields, work)
    return {**status(web, project), "deduplicated": bool(started.get("deduplicated"))}


def start_after_analysis(web, project):
    """Start page reading after PDF analysis, unless it's switched off for this job or for this server."""
    if os.environ.get("ARCHIE_PAGE_READING", "").strip().lower() == "off" or not enabled(project):
        return None
    try:
        return start(web, project, {"requested_by": "after analysis"})
    except ValueError:
        return None
