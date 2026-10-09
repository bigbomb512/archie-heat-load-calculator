"""Pass 1 of the PDF review on the server: read every page of the job's PDF with the AI, a few pages per call.

The AI is the provider chosen in backend.ai_provider (the Codex CLI with the team's ChatGPT sign-in by default).
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

from ai.page_inventory import PROMPT_VERSION, batch_prompt, build_prompt, packet_from_readings, main_plan_pages, validate_reply
from backend import ai_provider
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


# The provider lives in backend.ai_provider (Codex CLI, OpenAI API or Anthropic API, chosen by ARCHIE_AI_PROVIDER).
# These names are kept for callers and tests written against pass 1.
PageReadingUnavailable = ai_provider.ProviderUnavailable
UsageLimitReached = ai_provider.UsageLimitReached
CodexCliPageReader = ai_provider.CodexCliProvider
_usage_limit_message = ai_provider.usage_limit_message


def _provider(task="pass1"):
    if PROVIDER_FACTORY is not None:
        return PROVIDER_FACTORY()
    return ai_provider.get(task)


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


def batch_size():
    """Pages per call (ARCHIE_PAGE_BATCH, default 4). Each call costs about 4,600 tokens before any page; one page
    image about 2,300. Four pages per call cut a 38-page set from 38 calls to 10."""
    try:
        return max(1, min(8, int(os.environ.get("ARCHIE_PAGE_BATCH", "4"))))
    except ValueError:
        return 4


def read_pages(images, cache_dir, provider, workers=WORKERS, on_page=None, batch=None):
    """Read each page image (cached per page). Returns ({page: reading}, {page: failure reason}, calls made).

    Pages not in the cache are read a few per call (batch_size()); each page's reading is checked and cached on its
    own, so a page left out of a reply, or whose reading fails the checks, is read again on its own."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "") or "default"
    prompt = build_prompt()
    readings, failures, calls = {}, {}, 0
    lock = threading.Lock()
    stopped = threading.Event()

    def save(page, image, reading, raw, batch_pages=None):
        write_json(cache_dir / f"{_cache_key(image, model)}.json",
                   {"page": page, "prompt_version": PROMPT_VERSION, "model": model, "read_at": now(),
                    "reading": reading, "raw": raw, **({"read_with_pages": batch_pages} if batch_pages else {})})

    def one(page, image):
        if stopped.is_set():
            return [(page, None, stopped.reason)], 0
        try:
            reply, raw = provider.propose(prompt, image_paths=[image])
            reading = validate_reply(reply)
        except UsageLimitReached as error:
            stopped.reason = str(error)
            stopped.set()
            return [(page, None, str(error))], 1
        except Exception as error:
            return [(page, None, " ".join(str(error).split())[:400] or error.__class__.__name__)], 1
        save(page, image, reading, raw)
        return [(page, reading, None)], 1

    def several(pages):
        if len(pages) == 1:
            return one(pages[0], images[pages[0]])
        if stopped.is_set():
            return [(page, None, stopped.reason) for page in pages], 0
        try:
            reply, raw = provider.propose(batch_prompt(pages), image_paths=[images[page] for page in pages])
        except UsageLimitReached as error:
            stopped.reason = str(error)
            stopped.set()
            return [(page, None, str(error)) for page in pages], 1
        except Exception as error:
            if "JSON" in str(error):          # the reply itself was unreadable: read each page on its own instead
                reply, raw = {}, {}
            else:
                reason = " ".join(str(error).split())[:400] or error.__class__.__name__
                return [(page, None, reason) for page in pages], 1
        answers = reply.get("pages") if isinstance(reply, dict) and isinstance(reply.get("pages"), dict) else {}
        results, made, again = [], 1, []
        for page in pages:
            try:
                reading = validate_reply(answers.get(str(page)))
            except ValueError:
                again.append(page)
                continue
            # The call's record without the whole reply (kept once per page would repeat it): this page's part only.
            save(page, images[page], reading, {**{key: value for key, value in (raw or {}).items() if key != "reply_text"},
                                               "reply_text": json.dumps(answers.get(str(page)), ensure_ascii=False)}, pages)
            results.append((page, reading, None))
        for page in again:
            rows, extra = one(page, images[page])
            results.extend(rows)
            made += extra
        return results, made

    todo = []
    for page, image in sorted(images.items()):
        cached = read_json(cache_dir / f"{_cache_key(image, model)}.json")
        if cached.get("reading"):
            readings[page] = cached["reading"]
        else:
            todo.append(page)
    if on_page and readings:
        on_page(len(readings), len(images))
    size = batch or batch_size()
    chunks = [todo[index:index + size] for index in range(0, len(todo), size)]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(several, chunk) for chunk in chunks]
        for future in as_completed(futures):
            rows, made = future.result()
            with lock:
                calls += made
                for page, reading, failure in rows:
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
        provider = ai_provider.recorded(_provider(), root, "pass1")
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
        if not failures:
            # Every page is read: pass 2 reads the values of each kind from the pages flagged for it.
            from backend import page_extraction_service
            page_extraction_service.start_after_pass1(web, web.project_by_id(project_id) if web else project)
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
