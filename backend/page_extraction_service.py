"""Pass 2 of the PDF review on the server: read the values of one kind of information from every page pass 1 flagged.

For each extractor (ai/page_extraction.py), every page whose pass 1 reading lists that kind is rendered at 150 dpi
and sent to the AI on its own (large sheets as overlapping sections), through the same Codex CLI route as pass 1.
Replies are cached by image and extractor version, so a retry reads only what failed. Items are merged across
pages into findings, each checked against the page's text layer where it has one.

Findings are proposals. The operator accepts, edits or rejects each one; nothing here changes a calculation input.
A decision belongs to the run it was made on (same pass 1 readings, page images and extractor version) and is
ignored once those change.

Files in the job folder: page_extraction_job.json (progress), page_extraction.json (findings per kind),
page_extraction_decisions.json (the operator's decisions), page_extraction/ (images, page text, AI replies).
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import threading

from ai import equipment_heat
from ai.page_extraction import EXTRACTORS, build_prompt, merge, normalise_name, tiles, validate_reply
from backend import ai_provider, page_inventory_service as pass1
from backend.job_runner import BackgroundJob, now, read_json, write_json

JOB_FILE = "page_extraction_job.json"
RESULT_FILE = "page_extraction.json"
DECISIONS_FILE = "page_extraction_decisions.json"
WORK_DIR = "page_extraction"
DPI = 150
STEPS = [("pages", "Choosing the pages"), ("read", "Reading values"), ("merge", "Merging across pages")]
_JOB = BackgroundJob(JOB_FILE, STEPS, "archie-page-extraction",
                     "Reading values stopped before it finished (the server was restarted). Retry to read the rest.")
_RUNNING = _JOB.running


def png_size(path):
    with open(path, "rb") as handle:
        header = handle.read(24)
    return struct.unpack(">II", header[16:24])


def flagged_pages(project, kind):
    """Pages whose pass 1 reading lists this kind of information."""
    result = read_json(Path(project["review_dir"]) / pass1.RESULT_FILE)
    return sorted(int(page) for page, row in (result.get("pages") or {}).items()
                  if any(item.get("kind") == kind for item in row.get("information", [])))


def page_images(pdf, folder, pages, dpi=DPI):
    """{(page, section): (image path, section count)} at dpi; large pages as overlapping crops."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    signature = f"{pass1.pdf_signature(pdf)}|{dpi}"
    marker = read_json(folder / "rendered.json")
    if marker.get("signature") != signature:
        for old in folder.glob("*.png"):
            old.unlink()
        marker = {"signature": signature}
    result = {}
    for page in pages:
        whole = folder / f"page_{page:03d}.png"
        if not whole.is_file():
            subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(page), "-l", str(page), "-r", str(dpi),
                            str(pdf), str(whole.with_suffix(""))], check=True, capture_output=True, timeout=600)
        sections = tiles(*png_size(whole))
        if len(sections) == 1:
            result[(page, 0)] = (whole, 1)
            continue
        for index, (x0, y0, x1, y1) in enumerate(sections):
            crop = folder / f"page_{page:03d}_s{index}.png"
            if not crop.is_file():
                subprocess.run(["pdftoppm", "-png", "-singlefile", "-f", str(page), "-l", str(page), "-r", str(dpi),
                                "-x", str(x0), "-y", str(y0), "-W", str(x1 - x0), "-H", str(y1 - y0),
                                str(pdf), str(crop.with_suffix(""))], check=True, capture_output=True, timeout=600)
            result[(page, index)] = (crop, len(sections))
    write_json(folder / "rendered.json", marker)
    return result


def page_texts(pdf, folder, pages):
    """The PDF's own text layer per page (empty for scans), kept to check readings against."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    texts = {}
    for page in pages:
        path = folder / f"page_{page:03d}.txt"
        if not path.is_file():
            result = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
                                    capture_output=True, text=True, timeout=300)
            path.write_text(result.stdout if result.returncode == 0 else "", encoding="utf-8")
        texts[page] = path.read_text(encoding="utf-8")
    return texts


def _cache_key(image, extractor, model):
    digest = hashlib.sha256(Path(image).read_bytes()).hexdigest()
    return hashlib.sha256(f"{digest}|{extractor['version']}|{model}".encode()).hexdigest()[:32]


def read_sections(extractor, images, cache_dir, provider, workers=pass1.WORKERS, on_done=None):
    """Read every section (cached). Returns ([(page, section, items)], {"page.section": reason}, calls)."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = getattr(provider, "model", "") or "default"
    readings, failures, calls = [], {}, 0
    lock = threading.Lock()

    stopped = threading.Event()

    def one(key, image, count):
        page, section = key
        path = cache_dir / f"{_cache_key(image, extractor, model)}.json"
        cached = read_json(path)
        if "items" in cached:
            return key, cached["items"], None, False
        if stopped.is_set():  # the plan's usage limit was hit: further calls would fail the same way
            return key, None, stopped.reason, False
        try:
            reply, raw = provider.propose(build_prompt(extractor, page, section, count), image_paths=[image])
            items = validate_reply(extractor, reply)
        except pass1.UsageLimitReached as error:
            stopped.reason = str(error)
            stopped.set()
            return key, None, str(error), True
        except Exception as error:
            return key, None, " ".join(str(error).split())[:400] or error.__class__.__name__, True
        write_json(path, {"page": page, "section": section, "extractor": extractor["version"], "model": model,
                          "read_at": now(), "items": items, "raw": raw})
        return key, items, None, True

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, key, image, count) for key, (image, count) in sorted(images.items())]
        for future in as_completed(futures):
            (page, section), items, failure, called = future.result()
            with lock:
                calls += int(called)
                if items is None:
                    failures[f"{page}.{section}"] = failure
                else:
                    readings.append((page, section, items))
                if on_done:
                    on_done(len(readings) + len(failures), len(images))
    return sorted(readings, key=lambda row: (row[0], row[1])), failures, calls


def run_fingerprint(extractor, project, images):
    """What a decision is tied to: the extractor version, pass 1's readings and the page images read."""
    pass1_result = read_json(Path(project["review_dir"]) / pass1.RESULT_FILE)
    digests = sorted((f"{page}.{section}", hashlib.sha256(Path(image).read_bytes()).hexdigest())
                     for (page, section), (image, _count) in images.items())
    return hashlib.sha256(json.dumps([extractor["version"], pass1_result.get("pages"), digests],
                                     sort_keys=True).encode()).hexdigest()[:24]


def _extractor(kind):
    if kind not in EXTRACTORS:
        raise ValueError(f"No extractor for {kind!r}. Available: {', '.join(sorted(EXTRACTORS))}.")
    return EXTRACTORS[kind]


def rooms(project):
    """The job's room names, for assigning equipment to a room."""
    from backend import ai_preliminary_service
    try:
        proposal = ai_preliminary_service._proposal_for_resolution(ai_preliminary_service._paths(project), equipment=False)
    except Exception:
        return []
    return sorted({str(row.get("label")) for row in proposal.get("rooms", []) if isinstance(row, dict) and row.get("label")})


def suggested_room(location, room_names):
    """The room whose name appears in the printed location ("KITCHEN AREA" -> "Kitchen"), else ""."""
    text = f" {normalise_name(location)} "
    matches = [name for name in room_names if normalise_name(name) and f" {normalise_name(name)} " in text]
    return max(matches, key=len) if matches else ""


def status(web, project, kind="equipment_appliances"):
    extractor = _extractor(kind)
    job = _JOB.status(project)
    problem = job.pop("error", "")  # sent as "problem": the workspace treats an "error" field as a failed request
    result = read_json(Path(project["review_dir"]) / RESULT_FILE).get(kind, {})
    decisions = read_json(Path(project["review_dir"]) / DECISIONS_FILE).get(kind, {})
    room_names = rooms(project) if kind == "equipment_appliances" else []
    findings = []
    for row in result.get("findings", []):
        decision = decisions.get(row["id"], {})
        if decision.get("run") != result.get("run"):
            decision = {}
        extra = {}
        if kind == "equipment_appliances":
            decided = decision.get("value") or {}
            extra = {"heat": equipment_heat.proposal(row["value"]),
                     "suggested_room": suggested_room(row["value"].get("location"), room_names),
                     "accepted_heat_w": equipment_heat.heat_w(decided) if decision.get("status") == "accepted" else None,
                     "in_calculation": bool(decision.get("status") == "accepted" and decided.get("room") in room_names
                                            and equipment_heat.heat_w(decided) is not None)}
        findings.append({**row, **extra, "status": decision.get("status", "proposed"), "decided_value": decision.get("value"),
                         "reviewer": decision.get("reviewer", ""), "reviewed_at": decision.get("at", "")})
    open_count = sum(1 for row in findings if row["status"] == "proposed")
    return {**job, "problem": problem, "kind": kind, "label": extractor["label"], "pages": result.get("pages", []),
            "failures": result.get("failures", {}), "findings": findings, "open": open_count,
            "run": result.get("run", ""), "read_at": result.get("read_at", ""), "rooms": room_names}


def start(web, project, data=None):
    """Read the values of every kind with an extractor (or data["kinds"]) in one run."""
    data = data or {}
    kinds = data.get("kinds") or list(EXTRACTORS)
    for kind in kinds:
        _extractor(kind)
    root = Path(project["review_dir"])
    pdf = Path(str(project.get("pdf") or ""))
    if not pdf.is_file():
        raise ValueError("The job's PDF isn't available on this computer, so its pages can't be read.")
    if not read_json(root / pass1.RESULT_FILE).get("pages"):
        raise ValueError("Read every page first (pass 1); pass 2 reads the pages it flags.")
    try:
        provider = pass1._provider("pass2")
    except pass1.PageReadingUnavailable as error:
        if _JOB.status(project).get("status") not in {"queued", "running"}:
            write_json(_JOB.path(project), {"schema_version": 1, "status": "blocked", "error": str(error), "finished_at": now()})
        return status(web, project, kinds[0])

    def work(web, project_id, step):
        job_path = _JOB.path(project)
        problems, totals = [], {}
        for kind in kinds:
            extractor = EXTRACTORS[kind]
            step("pages", f"Choosing the pages for {extractor['label'].lower()}")
            pages = flagged_pages(project, kind)
            images = page_images(pdf, root / WORK_DIR / "pages", pages)
            texts = page_texts(pdf, root / WORK_DIR / "text", pages)

            def progress(done, total):
                job = read_json(job_path)
                job.update({"sections_done": done, "section_count": total})
                write_json(job_path, job)

            step("read", f"Reading {extractor['label'].lower()} from {len(pages)} pages")
            readings, failures, calls = read_sections(extractor, images, root / WORK_DIR / "replies" / kind,
                                                      ai_provider.recorded(provider, root, f"pass2:{kind}"), on_done=progress)
            step("merge")
            pass1_pages = read_json(root / pass1.RESULT_FILE).get("pages") or {}
            findings = merge(extractor, readings, texts, {int(page): row.get("page_type") for page, row in pass1_pages.items()})
            results = read_json(root / RESULT_FILE)
            results[kind] = {"schema_version": 1, "extractor": extractor["version"],
                             "run": run_fingerprint(extractor, project, images), "read_at": now(), "pages": pages,
                             "sections": len(images), "failures": failures, "findings": findings}
            write_json(root / RESULT_FILE, results)
            totals[kind] = {"findings": len(findings), "calls": calls}
            if failures:
                pages_failed = sorted({int(key.split(".")[0]) for key in failures})
                problems.append(f"{extractor['label']}: {len(failures)} of {len(images)} page sections couldn't be read "
                                f"(pages {', '.join(map(str, pages_failed))}). First reason: {failures[min(failures)]}")
        if problems:
            raise RuntimeError(" ".join(problems) + " Retry to read them.")
        _start_skills(web, project_id)
        return {"kinds": totals}

    started = _JOB.start(web, project, {"kinds": kinds, "sections_done": 0, "section_count": 0}, work)
    return {**status(web, project, kinds[0]), "deduplicated": bool(started.get("deduplicated"))}


def review(web, project, data):
    """Accept (optionally with an edited value), or reject, one finding of the current run."""
    kind = data.get("kind", "equipment_appliances")
    _extractor(kind)
    current = status(web, project, kind)
    finding = next((row for row in current["findings"] if row["id"] == data.get("finding_id")), None)
    if not finding:
        raise ValueError("That finding isn't part of the current reading. Refresh and try again.")
    decision = data.get("decision")
    if decision not in {"accepted", "rejected"}:
        raise ValueError("Choose accept or reject.")
    value = data.get("value")
    if decision == "accepted":
        if value is None:
            if finding["conflicts"]:
                raise ValueError("The pages disagree on this item. Choose or edit the value before accepting it.")
            value = finding["value"]
        if not isinstance(value, dict) or not str(value.get("name", "")).strip():
            raise ValueError("An accepted item needs at least a name.")
        for field, low, high in (("rated_input_w", 0, None), ("heat_to_space_factor", 0, 1)):
            if value.get(field) in (None, ""):
                value[field] = None
                continue
            try:
                number = float(value[field])
            except (TypeError, ValueError):
                raise ValueError(f"{field.replace('_', ' ')} must be a number.") from None
            if number < low or (high is not None and number > high) or (field == "rated_input_w" and number == 0):
                raise ValueError("Rated power must be above 0 W." if field == "rated_input_w" else "The heat-to-room factor must be between 0 and 1.")
            value[field] = number
    root = Path(project["review_dir"])
    decisions = read_json(root / DECISIONS_FILE)
    decisions.setdefault(kind, {})[finding["id"]] = {
        "status": decision, "value": value if decision == "accepted" else None, "run": current["run"],
        "reviewer": " ".join(str(data.get("reviewer") or "").split())[:80] or "Operator", "at": now()}
    write_json(root / DECISIONS_FILE, decisions)
    return status(web, project, kind)


def accepted(project, kind="equipment_appliances"):
    """The accepted values of the current run, for whatever uses them next (the calculation is not wired yet)."""
    root = Path(project["review_dir"])
    result = read_json(root / RESULT_FILE).get(kind, {})
    decisions = read_json(root / DECISIONS_FILE).get(kind, {})
    return [{"id": row["id"], "pages": row["pages"], **decisions[row["id"]]["value"]}
            for row in result.get("findings", [])
            if decisions.get(row["id"], {}).get("status") == "accepted" and decisions[row["id"]].get("run") == result.get("run")]


def _start_skills(web, project_id):
    """The skills run last, each on its case file built from pass 1 and 2 (ideas 1 and 3)."""
    if web is None:
        return
    from backend import skill_workflow_service
    try:
        skill_workflow_service.start_after_reading(web, web.project_by_id(project_id))
    except ValueError:
        pass


def start_after_pass1(web, project):
    """Pass 1 finished: read the values of every kind that has an extractor and flagged pages, then the skills."""
    kinds = [kind for kind in EXTRACTORS if flagged_pages(project, kind)]
    if kinds:
        try:
            start(web, project, {"kinds": kinds})
            return
        except ValueError:
            pass
    _start_skills(web, project["id"])
