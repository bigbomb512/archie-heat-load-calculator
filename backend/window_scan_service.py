"""User-triggered, resumable all-page window scan alongside general vision."""

import base64
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request
import uuid

from ai.drawing_coverage import source_fingerprint
from ai.vision_extraction import file_hash, timestamp
from ai.window_scan import POLICY_VERSION, fingerprint, normalize_sightings, page_batches, resolve_clusters
from backend.vision_extraction_service import _atomic_json, _page_image

LOCK = threading.RLock()
PROVIDER_FACTORY = None
REVIEW_TYPES = {"opening", "glazing_system"}
EXPOSURES = {"external", "internal", "unresolved"}
GLASS_AREA_BASES = {"explicit_glass_area", "reviewed_frame_fraction"}


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _paths(project):
    root = Path(project["review_dir"])
    return {"root": root, "ai_input": root / "ai_input.json", "settings": root / "vision_extraction_settings.json",
            "job": root / "window_scan_job.json", "runs": root / "window_scan_runs",
            "register": root / "window_scan_register.json", "reviews": root / "window_scan_reviews.json"}


def _estimate(ai_input):
    batches = page_batches(ai_input) if ai_input else []
    raw = os.environ.get("ARCHIE_WINDOW_SCAN_COST_PER_BATCH_AUD", "").strip()
    try:
        rate = float(raw)
        if rate <= 0: rate = None
    except ValueError:
        rate = None
    return {"page_count": sum(len(batch["pages"]) for batch in batches), "batch_count": len(batches),
            "request_count": len(batches) + (1 if batches else 0),
            "estimated_cost_aud": round((len(batches) + 1) * rate, 2) if rate else None,
            "estimate_available": rate is not None}


def get(web, project):
    paths = _paths(project)
    ai_input = _read(paths["ai_input"], {})
    job = _read(paths["job"], {})
    if job.get("status") in {"queued", "running", "cancel_requested"} and not any(
        thread.name == job.get("run_id") and thread.is_alive() for thread in threading.enumerate()
    ):
        job.update(status="interrupted", finished_at=timestamp(), error="Server restarted. Retry the same immutable manifest.")
        _atomic_json(paths["job"], job)
    register = _read(paths["register"], {})
    display_register = deepcopy(register)
    reviews = _read(paths["reviews"], {"openings": {}})
    review_rows = reviews.get("openings", {}) if reviews.get("source_fingerprint") == register.get("source_fingerprint") else {}
    for opening in display_register.get("openings", []):
        review = review_rows.get(opening.get("opening_id"), {})
        if review.get("scan_fingerprint") == register.get("fingerprint"):
            opening["review"] = deepcopy(review)
            for key in ("review_type", "system_name", "owner_room_id", "owner_zone_id", "host_wall_id", "level_name",
                        "width_m", "height_m", "quantity", "external_exposure", "opening_coverage", "review_mode"):
                if key in review:
                    opening[key] = deepcopy(review[key])
            opening["status"] = "reviewed" if review.get("review_mode") == "engineering_reviewed" else "ai_estimated"
        for sighting in opening.get("sightings", []):
            thumbnail = paths["root"] / "thumbnails" / f"page_{sighting['page']:03d}.png"
            if thumbnail.is_file():
                sighting["page_preview_url"] = web.safe_link(thumbnail)
    current_source = source_fingerprint(ai_input) if ai_input else ""
    return {"id": project["id"], "estimate": _estimate(ai_input), "job": job,
            "source_fingerprint": current_source, "register_status": "stale" if register and register.get("source_fingerprint") != current_source else "current" if register else "not_scanned",
            "register": display_register, "reviews": reviews,
            "register_url": web.safe_link(paths["register"]) if paths["register"].exists() else "",
            "provider_configured": bool(os.environ.get("OPENAI_API_KEY") or PROVIDER_FACTORY)}


def _manifest(paths, ai_input, model, estimate):
    run_id = "windows-" + uuid.uuid4().hex
    run_dir = paths["runs"] / run_id
    batches = []
    for batch in page_batches(ai_input):
        pages = []
        for source in batch["pages"]:
            number = source["page"]
            image = _page_image(ai_input, number, run_dir / "pages" / f"page-{number}.png")
            pages.append({"page": number, "drawing_number": source.get("drawing_number", ""),
                          "title": source.get("title", ""), "image_path": str(image),
                          "image_sha256": file_hash(image), "source_page_fingerprint": fingerprint(source)})
        batches.append({"batch_id": batch["batch_id"], "pages": pages})
    manifest = {"schema_version": 1, "policy_version": POLICY_VERSION, "run_id": run_id,
                "created_at": timestamp(), "source_fingerprint": source_fingerprint(ai_input),
                "model": model, "estimate": estimate, "batches": batches,
                "scan_prompt": SCAN_PROMPT, "match_prompt": MATCH_PROMPT,
                "prompt_fingerprint": fingerprint([POLICY_VERSION, SCAN_PROMPT, MATCH_PROMPT])}
    manifest["fingerprint"] = fingerprint(manifest)
    _atomic_json(run_dir / "manifest.json", manifest)
    return manifest


SCAN_PROMPT = ("Inspect every supplied PDF page for apparent windows and glazed openings, including plans, elevations, "
               "schedules, details, proposed renders, and actual-condition photos. Return JSON object with sightings array. "
               "Each sighting: page, bbox [left,top,right,bottom] in supplied image pixels, view_type "
               "(plan/elevation/section/schedule/render/photo/detail/other), appearance_status "
               "(proposed_design/existing_condition/unknown), tag, level_name, landmarks array, geometry_clues, "
               "facade_clue, room_clue, host_wall_clue, revision, source_excerpt, confidence 0..1. "
               "Return no sightings for pages without windows. Do not invent U-values, SHGC, dimensions, north, or ownership.")
MATCH_PROMPT = ("Compare window sightings across view types. Return JSON object with clusters array. "
                "Each cluster has member_sighting_ids, match_reason, mirrored_view boolean, competing_matches array. "
                "Use tag AND spatial context: opening order, doors, columns, mullions, proportions, level, revision. "
                "Do not merge a repeated tag, mirrored view, obsolete render, or different-level sighting unless independently supported. "
                "Leave unmatched sightings out of clusters; they will remain visible. No inferred room, host wall, azimuth, U-value or SHGC.")

SIGHTING_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sightings"],
    "properties": {"sightings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["page", "bbox", "view_type", "appearance_status", "tag", "level_name", "landmarks",
                     "geometry_clues", "facade_clue", "room_clue", "host_wall_clue", "revision", "source_excerpt", "confidence"],
        "properties": {"page": {"type": "integer"}, "bbox": {"type": "array", "items": {"type": "number"}},
            "view_type": {"type": "string", "enum": ["plan", "elevation", "section", "schedule", "render", "photo", "detail", "other"]},
            "appearance_status": {"type": "string", "enum": ["proposed_design", "existing_condition", "unknown"]},
            "tag": {"type": "string"}, "level_name": {"type": "string"}, "landmarks": {"type": "array", "items": {"type": "string"}},
            **{key: {"type": "string"} for key in ("geometry_clues", "facade_clue", "room_clue", "host_wall_clue", "revision", "source_excerpt")},
            "confidence": {"type": "number"}}}}}}
MATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["clusters"],
    "properties": {"clusters": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["member_sighting_ids", "match_reason", "mirrored_view", "competing_matches"],
        "properties": {"member_sighting_ids": {"type": "array", "items": {"type": "string"}},
                       "match_reason": {"type": "string"}, "mirrored_view": {"type": "boolean"},
                       "competing_matches": {"type": "array", "items": {"type": "string"}}}}}}}


class OpenAIWindowProvider:
    def __init__(self, model):
        self.model = model
        self.scan_prompt = SCAN_PROMPT
        self.match_prompt = MATCH_PROMPT

    def _request(self, prompt, pages=None, schema=None, name="window_scan"):
        content = [{"type": "input_text", "text": prompt}]
        for page in pages or []:
            encoded = base64.b64encode(Path(page["image_path"]).read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"})
        payload = {"model": self.model, "store": False, "input": [{"role": "user", "content": content}],
                   "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}
        request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(), method="POST",
                                         headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"Window scan provider returned HTTP {error.code}.") from error
        output = body.get("output_text")
        if not output:
            output = next((part.get("text") for row in body.get("output", []) for part in row.get("content", [])
                           if part.get("type") in {"output_text", "text"}), None)
        if not output:
            raise ValueError("Window scan provider returned no JSON output.")
        return json.loads(output)

    def scan(self, batch):
        return self._request(self.scan_prompt + "\nPage manifest: " + json.dumps([
            {key: page[key] for key in ("page", "drawing_number", "title", "image_sha256")}
            for page in batch["pages"]]), batch["pages"], SIGHTING_SCHEMA, "window_sightings")

    def match(self, sightings):
        # The matching pass compares the complete normalized visual descriptions;
        # original page crops remain available for targeted human review.
        return self._request(self.match_prompt + "\nSightings: " + json.dumps(sightings), schema=MATCH_SCHEMA, name="window_matches")


def _provider(model):
    return PROVIDER_FACTORY(model) if PROVIDER_FACTORY else OpenAIWindowProvider(model)


def _job(paths):
    return _read(paths["job"], {})


def _validate_glazing_system_review(review, opening):
    """Validate the extra evidence required for an untagged shopfront system.

    This deliberately validates provenance and grouping only. Window thermal
    properties remain the responsibility of the reviewed envelope library.
    """
    review_type = review.get("review_type", "opening")
    if review_type not in REVIEW_TYPES:
        raise ValueError("Opening review type must be opening or glazing_system.")
    if review_type != "glazing_system":
        return
    if not isinstance(review.get("system_name"), str) or not review["system_name"].strip():
        raise ValueError("A glazing system needs a descriptive system name.")
    if not isinstance(review.get("includes_glazed_doors"), bool):
        raise ValueError("A glazing system must explicitly state whether glazed doors are included.")
    exposure = review.get("external_exposure", "unresolved")
    if exposure not in EXPOSURES or exposure == "unresolved":
        raise ValueError("A glazing system needs a reviewed external or internal exposure decision.")
    members = review.get("member_sighting_ids", opening.get("sighting_ids", []))
    if not isinstance(members, list) or not members or len(members) != len(set(members)):
        raise ValueError("A glazing system needs one or more unique member sightings.")
    known_members = set(opening.get("sighting_ids", []))
    if not set(members).issubset(known_members):
        raise ValueError("Glazing-system members must belong to the selected opening cluster.")
    coverage = review.get("opening_coverage")
    if not isinstance(coverage, dict) or coverage.get("status") not in {"complete", "incomplete"}:
        raise ValueError("A glazing system needs a complete or incomplete host-wall opening-coverage decision.")
    if not coverage.get("source") or not isinstance(coverage.get("citations"), list) or any(
        not isinstance(citation, dict) or not citation.get("reference") for citation in coverage["citations"]
    ):
        raise ValueError("Opening-coverage decisions need a cited source.")
    glass_area_basis = review.get("glass_area_basis", "reviewed_frame_fraction")
    if glass_area_basis not in GLASS_AREA_BASES:
        raise ValueError("Glass area must use explicit glass area or a reviewed frame fraction.")
    if glass_area_basis == "explicit_glass_area":
        value = review.get("explicit_glass_area_m2")
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("Explicit glass-area basis needs a positive glass area in m².")
    elif review.get("explicit_glass_area_m2") not in (None, ""):
        raise ValueError("Explicit glass area is only valid when it is the selected glass-area basis.")


def _run(web, project_id, manifest):
    paths = _paths(web.project_by_id(project_id))
    run_dir = paths["runs"] / manifest["run_id"]
    attempts_path = run_dir / "attempts.json"
    attempts = _read(attempts_path, [])
    try:
        provider = _provider(manifest["model"])
        if isinstance(provider, OpenAIWindowProvider):
            provider.scan_prompt = manifest["scan_prompt"]
            provider.match_prompt = manifest["match_prompt"]
        sightings = []
        for batch in manifest["batches"]:
            if _job(paths).get("status") == "cancel_requested":
                _atomic_json(paths["job"], {**_job(paths), "status": "cancelled", "finished_at": timestamp()})
                return
            result_path = run_dir / f"{batch['batch_id']}.json"
            if result_path.exists():
                normalized = _read(result_path, [])
            else:
                attempt = {"batch_id": batch["batch_id"], "started_at": timestamp(),
                           "attempt_number": 1 + sum(row.get("batch_id") == batch["batch_id"] for row in attempts),
                           "model": manifest["model"], "prompt_fingerprint": manifest["prompt_fingerprint"]}
                attempts.append(attempt)
                _atomic_json(attempts_path, attempts)
                try:
                    output = provider.scan(batch)
                    normalized = normalize_sightings(output, batch["pages"], manifest["source_fingerprint"],
                                                      "openai" if not PROVIDER_FACTORY else "test_provider",
                                                      manifest["model"], manifest["prompt_fingerprint"])
                    _atomic_json(result_path, normalized)
                    attempt.update(status="completed", finished_at=timestamp(), result_sha256=file_hash(result_path))
                except Exception as error:
                    attempt.update(status="failed", finished_at=timestamp(), error=str(error)[:300])
                    raise
                finally:
                    _atomic_json(attempts_path, attempts)
            sightings.extend(normalized)
            _atomic_json(paths["job"], {**_job(paths), "status": "running", "completed_batches": len([
                row for row in manifest["batches"] if (run_dir / f"{row['batch_id']}.json").exists()])})
        match_path = run_dir / "matches.json"
        if match_path.exists():
            matches = _read(match_path, None)
        else:
            match_attempt = {"batch_id": "cross_view_matching", "started_at": timestamp(),
                             "attempt_number": 1 + sum(row.get("batch_id") == "cross_view_matching" for row in attempts),
                             "model": manifest["model"], "prompt_fingerprint": manifest["prompt_fingerprint"]}
            attempts.append(match_attempt)
            _atomic_json(attempts_path, attempts)
            try:
                matches = provider.match(sightings)
                match_attempt.update(status="completed", finished_at=timestamp())
            except Exception as error:
                match_attempt.update(status="failed", finished_at=timestamp(), error=str(error)[:300])
                raise
            finally:
                _atomic_json(attempts_path, attempts)
        if not isinstance(matches, dict) or not isinstance(matches.get("clusters"), list):
            raise ValueError("Window matching must return a clusters array.")
        if not match_path.exists(): _atomic_json(match_path, matches)
        register = resolve_clusters(sightings, matches["clusters"], manifest["source_fingerprint"])
        register.update({"manifest_fingerprint": manifest["fingerprint"], "prompt_fingerprint": manifest["prompt_fingerprint"],
                         "pages_inspected": sorted(page["page"] for batch in manifest["batches"] for page in batch["pages"]),
                         "batch_results": [{"batch_id": batch["batch_id"], "result_sha256": file_hash(run_dir / f"{batch['batch_id']}.json")}
                                           for batch in manifest["batches"]]})
        register["fingerprint"] = fingerprint({key: value for key, value in register.items() if key != "fingerprint"})
        # A changed PDF cannot receive a scan from an older immutable manifest.
        current = _read(paths["ai_input"], {})
        if source_fingerprint(current) != manifest["source_fingerprint"]:
            raise ValueError("PDF source changed during scan; start a new scan.")
        _atomic_json(paths["register"], register)
        _atomic_json(paths["job"], {**_job(paths), "status": "completed", "finished_at": timestamp(),
                                    "completed_batches": len(manifest["batches"]), "error": ""})
    except Exception as error:
        _atomic_json(paths["job"], {**_job(paths), "status": "failed", "finished_at": timestamp(),
                                    "error": str(error)[:500]})


def post(web, project, data):
    with LOCK:
        paths = _paths(project)
        action = data.get("action", "estimate")
        if action == "estimate": return get(web, project)
        if action == "review_opening":
            register = _read(paths["register"], {})
            ai_input = _read(paths["ai_input"], {})
            if register.get("source_fingerprint") != source_fingerprint(ai_input):
                raise ValueError("Window scan is stale; rescan the changed PDF before reviewing openings.")
            opening_id = data.get("opening_id", "")
            opening = next((row for row in register.get("openings", []) if row.get("opening_id") == opening_id), None)
            if not opening:
                raise ValueError("Opening ID is not in the current all-page register.")
            review = data.get("review", {})
            if not isinstance(review, dict):
                raise ValueError("Opening review must be an object.")
            required = ("owner_room_id", "owner_zone_id", "host_wall_id", "level_name", "source", "citations")
            if any(not review.get(key) for key in required):
                raise ValueError("Opening review needs exact room, zone, host wall, level, source and citations.")
            if (not isinstance(review["citations"], list) or any(
                not isinstance(citation, dict) or not citation.get("reference") for citation in review["citations"]
            )):
                raise ValueError("Opening review citations need explicit references.")
            model = _read(paths["root"] / "hourly_load_model.json", {})
            rooms = {room.get("room_id"): room for room in model.get("rooms", [])}
            if rooms and (review["owner_room_id"] not in rooms or rooms[review["owner_room_id"]].get("zone_id") != review["owner_zone_id"]):
                raise ValueError("Opening room and zone must match the current hourly-model topology.")
            if review.get("facade", "") not in {"", "N", "NE", "E", "SE", "S", "SW", "W", "NW"}:
                raise ValueError("Opening façade must be a supported compass label or remain unresolved.")
            for key in ("width_m", "height_m"):
                value = review.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError("Opening review needs positive width, height and quantity.")
            if type(review.get("quantity")) is not int or review["quantity"] < 1:
                raise ValueError("Opening review quantity must be a positive integer.")
            _validate_glazing_system_review(review, opening)
            if opening.get("status") == "conflict" and not review.get("conflict_resolution"):
                raise ValueError("Resolve repeated tags, revisions or competing matches explicitly.")
            mode = review.get("review_mode", "preliminary_ai_estimate")
            if mode not in {"preliminary_ai_estimate", "engineering_reviewed"}:
                raise ValueError("Unsupported opening review mode.")
            if mode == "engineering_reviewed":
                page_types = {sighting.get("view_type") for sighting in opening.get("sightings", [])}
                page_ids = {sighting.get("page") for sighting in opening.get("sightings", [])}
                if len(page_ids) < 2 or "plan" not in page_types or not page_types.intersection({"elevation", "section", "schedule"}):
                    raise ValueError("Engineering-reviewed opening mapping needs distinct plan and elevation/section/schedule sightings.")
            reviews = _read(paths["reviews"], {"schema_version": 1, "source_fingerprint": register["source_fingerprint"], "openings": {}})
            if reviews.get("source_fingerprint") != register["source_fingerprint"]:
                reviews = {"schema_version": 1, "source_fingerprint": register["source_fingerprint"], "openings": {}}
            reviews["openings"][opening_id] = {**review, "reviewed_at": timestamp(),
                                                 "scan_fingerprint": register["fingerprint"]}
            reviews["fingerprint"] = fingerprint({key: value for key, value in reviews.items() if key != "fingerprint"})
            _atomic_json(paths["reviews"], reviews)
            return get(web, project)
        if action == "cancel":
            job = _job(paths)
            if job.get("status") not in {"queued", "running"}:
                raise ValueError("No window scan is running.")
            _atomic_json(paths["job"], {**job, "status": "cancel_requested"})
            return get(web, project)
        if action not in {"start", "retry"}:
            raise ValueError("Window scan action must be estimate, start, retry or cancel.")
        ai_input = _read(paths["ai_input"], {})
        estimate = _estimate(ai_input)
        settings = _read(paths["settings"], {})
        if not settings.get("owner_opt_in") or not data.get("confirm_all_pages"):
            raise ValueError("Explicit project-owner consent for all-page AI window analysis is required.")
        budget = settings.get("max_budget_aud")
        if not estimate["estimate_available"] or type(budget) not in (int, float) or estimate["estimated_cost_aud"] > budget:
            raise ValueError("A configured per-batch cost estimate and sufficient approved AUD budget are required.")
        if not (os.environ.get("OPENAI_API_KEY") or PROVIDER_FACTORY):
            raise ValueError("Window scan provider is not configured.")
        previous = _job(paths)
        if previous.get("status") in {"queued", "running", "cancel_requested"}:
            raise ValueError("A window scan is already running.")
        model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")
        manifest = None
        if action == "retry" and previous.get("status") in {"failed", "cancelled", "interrupted"}:
            path = paths["runs"] / previous.get("run_id", "") / "manifest.json"
            if path.exists(): manifest = _read(path, None)
            if manifest and (manifest["source_fingerprint"] != source_fingerprint(ai_input) or manifest["model"] != model):
                raise ValueError("PDF or model changed; start a new all-page scan rather than retrying old batches.")
        if manifest is None: manifest = _manifest(paths, ai_input, model, estimate)
        job = {"schema_version": 1, "run_id": manifest["run_id"], "status": "queued", "started_at": timestamp(),
               "finished_at": "", "completed_batches": 0, "total_batches": len(manifest["batches"]),
               "manifest_fingerprint": manifest["fingerprint"], "error": ""}
        _atomic_json(paths["job"], job)
        thread = threading.Thread(name=manifest["run_id"], target=_run, args=(web, project["id"], manifest), daemon=True)
        thread.start()
        return get(web, project)
