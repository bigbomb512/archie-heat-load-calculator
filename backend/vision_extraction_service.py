"""Local, opt-in orchestration for evidence-only OpenAI vision extraction.

The service deliberately owns job state instead of using provider background
mode.  That keeps requests non-persistent (``store: false``), makes a restart
an explicit interruption, and leaves a local audit trail for every request.
"""

import base64
import json
import os
from pathlib import Path
import threading
import urllib.error
import urllib.request
import uuid

from ai.building_evidence import build_building_evidence
from ai.drawing_coverage import source_fingerprint
from ai.evidence_fusion import build_evidence_fusion
from ai.geometry_review import normalise_vision
from ai.thermal_model import build_thermal_evidence, build_thermal_model
from ai.vision_extraction import (
    build_ranked_context, empty_settings, estimate, extraction_schema, file_hash, select_page_groups,
    timestamp, validate_provider_output, validate_settings,
    vision_response_from_extraction,
)
from backend import draft_service
from ai.chatgpt_packet import render_high_res_page


LOCK = threading.RLock()
THREADS = {}
# Tests replace this with a fake.  Production uses OpenAIResponsesProvider.
PROVIDER_FACTORY = None


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + ".stage")
    stage.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(stage, path)


def _paths(project):
    root = Path(project["review_dir"])
    return {
        "root": root,
        "settings": root / "vision_extraction_settings.json",
        "job": root / "vision_extraction_job.json",
        "runs": root / "vision_extraction_runs",
        "ai_input": root / "ai_input.json",
        "coverage": root / "drawing_coverage.json",
    }


def _cost_per_group():
    raw = os.environ.get("ARCHIE_VISION_ESTIMATED_COST_PER_GROUP_AUD", "").strip()
    try:
        value = float(raw)
        return value if value > 0 else None
    except ValueError:
        return None


def _settings(paths):
    return validate_settings(_read(paths["settings"], empty_settings()))


def _job(paths):
    return _read(paths["job"], {})


def _recover_interrupted(paths):
    job = _job(paths)
    if job.get("status") in {"queued", "running", "cancel_requested"}:
        job.update({"status": "interrupted", "finished_at": timestamp(),
                    "error": "The server restarted before this local job completed. Retry deliberately; no request was resumed."})
        _atomic_json(paths["job"], job)
    return job


def _available(ai_input, coverage):
    return select_page_groups(ai_input, coverage)


def _context_selection(ai_input, coverage):
    return build_ranked_context(ai_input, coverage)


def _response(web, project):
    paths = _paths(project)
    paths["root"].mkdir(parents=True, exist_ok=True)
    job = _recover_interrupted(paths)
    ai_input = _read(paths["ai_input"], {})
    coverage = _read(paths["coverage"], {})
    groups = _available(ai_input, coverage) if ai_input else []
    context_selection = _context_selection(ai_input, coverage) if ai_input else {}
    settings = _settings(paths)
    return {
        "id": project["id"], "settings": settings, "available_groups": groups,
        "context_selection": context_selection,
        "estimate": estimate(settings, groups, _cost_per_group()), "job": job,
        "provider_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "model": settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5"),
        "artifact_links": _artifact_links(web, paths["root"]),
    }


def _artifact_links(web, root):
    names = ("vision_response.json", "building_evidence.json", "thermal_evidence.json",
             "thermal_model.json", "architect_evidence_fusion.json", "calculator_draft.json")
    return {name: web.safe_link(root / name) for name in names if (root / name).exists()}


def get(web, project):
    with LOCK:
        return _response(web, project)


def _save_settings(paths, raw):
    current = _settings(paths)
    incoming = dict(raw or {})
    current.update({key: incoming[key] for key in empty_settings() if key in incoming})
    current["updated_at"] = timestamp()
    current = validate_settings(current)
    _atomic_json(paths["settings"], current)
    return current


def _assert_startable(settings, groups, summary):
    if not settings.get("owner_opt_in"):
        raise ValueError("Project-owner opt-in is required before sending selected drawing pages to OpenAI.")
    if settings.get("max_budget_aud") is None:
        raise ValueError("Enter a maximum project-run budget before starting AI extraction.")
    if not summary["group_count"]:
        raise ValueError("Select at least one available evidence group.")
    if not summary["estimate_available"]:
        raise ValueError("Set ARCHIE_VISION_ESTIMATED_COST_PER_GROUP_AUD on the server before starting; Archie will not guess spend.")
    if not summary["within_budget"]:
        raise ValueError("The estimated extraction cost exceeds the approved project-run budget.")
    if not os.environ.get("OPENAI_API_KEY") and PROVIDER_FACTORY is None:
        raise ValueError("OPENAI_API_KEY is not configured on this server. No request was started.")


def _page_image(ai_input, page, target):
    images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])}
    image = images.get(page)
    if not image:
        raise ValueError(f"No rendered source image is available for page {page}.")
    rendered = render_high_res_page(ai_input, image, target, 180)
    if not rendered.get("ok") or not target.exists():
        raise ValueError(f"Could not render page {page} for extraction: {rendered.get('reason', 'unknown rendering failure')}")
    return target


def _manifest(paths, ai_input, groups, settings, summary):
    run_id = "vision-" + uuid.uuid4().hex
    run_dir = paths["runs"] / run_id
    pages_dir = run_dir / "pages"
    selected_ids = set(summary["selected_group_ids"])
    selected = [group for group in groups if group["group_id"] in selected_ids]
    manifest_groups = []
    for group in selected:
        members = []
        for page in group["pages"]:
            image_path = _page_image(ai_input, page["page"], pages_dir / f"page-{page['page']}.png")
            members.append({**page, "image_path": str(image_path), "image_sha256": file_hash(image_path)})
        manifest_groups.append({**group, "pages": members})
    context_selection = _context_selection(ai_input, _read(paths["coverage"], {}))
    manifest = {
        "schema_version": 1, "run_id": run_id, "created_at": timestamp(),
        "source_fingerprint": source_fingerprint(ai_input), "settings": settings,
        "estimate": summary, "groups": manifest_groups,
        "context_selection": context_selection,
        "main_context_pages": context_selection.get("main_context_pages", []),
        "exception_pages": context_selection.get("exception_pages", []),
        "selection_policy_version": context_selection.get("policy_version", ""),
        "selection_fingerprint": context_selection.get("fingerprint", ""),
    }
    _atomic_json(run_dir / "request_manifest.json", manifest)
    return manifest, run_dir


class OpenAIResponsesProvider:
    def __init__(self, api_key, model):
        self.api_key, self.model = api_key, model

    def extract(self, group):
        content = [{"type": "input_text", "text": _prompt(group)}]
        for page in group["pages"]:
            encoded = base64.b64encode(Path(page["image_path"]).read_bytes()).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"})
        payload = {
            "model": self.model, "store": False,
            "instructions": "Extract only architect-drawing topology and geometry. Never infer loads, U-values, weather, occupancy, schedules, or thermal performance. Cite only supplied pages and return the strict JSON schema.",
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "architect_geometry_extraction", "strict": True, "schema": extraction_schema()}},
        }
        request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("OpenAI request failed with HTTP " + str(error.code)) from error
        text = body.get("output_text")
        if not text:
            for item in body.get("output", []):
                for part in item.get("content", []):
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        break
        if not isinstance(text, str):
            raise ValueError("OpenAI returned no structured text output.")
        return json.loads(text), body


def _prompt(group):
    pages = [{key: value for key, value in page.items() if key in {
        "page", "drawing_number", "drawing_number_candidates", "title", "role", "level_name",
        "structured_text", "capability_map", "relevance", "selection", "selection_reasons", "related_pages",
    }} for page in group["pages"]]
    return ("Selected ranked evidence group:\n" + json.dumps({"group_id": group["group_id"], "pages": pages}, indent=2)
            + "\nReturn exactly one group object inside the required groups array. Cite supplied page and drawing identity candidates."
            + " Dates are never drawing numbers; embedded detail scales never calibrate the main plan; 3D pages are cross-check only."
            + " Legends, title blocks, schedules, and generic notes do not create rooms. Do not guess."
            + " For room geometry, provide boundary_points_px or an ordered wall_ids sequence, dimension_ids, level_name,"
            + " and an independent_witness_page when visibly supported; otherwise leave geometry proposed or unresolved.")


def _provider(settings):
    model = settings.get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")
    if PROVIDER_FACTORY is not None:
        return PROVIDER_FACTORY(model)
    return OpenAIResponsesProvider(os.environ["OPENAI_API_KEY"], model)


def _cancel_requested(paths, run_id):
    job = _job(paths)
    return job.get("run_id") == run_id and job.get("status") == "cancel_requested"


def _update_job(paths, run_id, **changes):
    job = _job(paths)
    if job.get("run_id") != run_id:
        return job
    job.update(changes)
    _atomic_json(paths["job"], job)
    return job


def _materialize(web, project, paths, manifest, validated):
    root = paths["root"]
    ai_input = _read(paths["ai_input"], {})
    coverage = _read(paths["coverage"], {})
    spatial = _read(root / "spatial_ocr.json", {})
    candidate_review = _read(root / "wall_classification_candidate_review.json", _read(root / "candidate_review.json", {}))
    vision = normalise_vision(vision_response_from_extraction(validated, manifest["source_fingerprint"], manifest["settings"].get("model") or os.environ.get("ARCHIE_VISION_MODEL", "gpt-5")), candidate_review)
    building = build_building_evidence(ai_input, coverage, spatial, vision)
    thermal_evidence = build_thermal_evidence(ai_input, spatial, vision, coverage, building)
    thermal_model = build_thermal_model(thermal_evidence)
    fusion = build_evidence_fusion(ai_input, coverage, building, spatial, _read(root / "vector_geometry.json", {}), vision,
                                   _read(root / "dimension_wall_matches.json", {}), _read(root / "geometry_confirmation.json", {}))
    for name, value in {
        "vision_response.json": vision, "building_evidence.json": building,
        "thermal_evidence.json": thermal_evidence, "thermal_model.json": thermal_model,
        "architect_evidence_fusion.json": fusion,
    }.items():
        _atomic_json(root / name, value)
    project.update({"vision_response": str(root / "vision_response.json"), "building_evidence": str(root / "building_evidence.json"),
                    "thermal_evidence": str(root / "thermal_evidence.json"), "thermal_model": str(root / "thermal_model.json"),
                    "updated_at": timestamp()})
    web.update_project(project)
    # This consumes the newly written artifacts and creates calculator_draft.json;
    # no editable hourly/envelope artifact is changed by extraction.
    draft_service.post(web, project, {"action": "build"})
    return _artifact_links(web, root)


def _run(web, project_id, run_id, manifest):
    paths = _paths(web.project_by_id(project_id))
    try:
        provider = _provider(manifest["settings"])
        raw_groups = []
        for index, group in enumerate(manifest["groups"]):
            if _cancel_requested(paths, run_id):
                _update_job(paths, run_id, status="cancelled", finished_at=timestamp())
                return
            result, raw = provider.extract(group)
            _atomic_json(Path(manifest_path(paths, run_id)) / f"raw-{group['group_id']}.json", raw)
            if not isinstance(result, dict) or not isinstance(result.get("groups"), list) or len(result["groups"]) != 1:
                raise ValueError("Provider must return exactly one strict group result per request.")
            raw_groups.extend(result["groups"])
            _update_job(paths, run_id, status="running", completed_groups=index + 1, current_group=group["group_id"])
        validated = validate_provider_output({"groups": raw_groups}, manifest["groups"])
        run_dir = Path(manifest_path(paths, run_id))
        _atomic_json(run_dir / "normalized_output.json", validated)
        project = web.project_by_id(project_id)
        links = _materialize(web, project, paths, manifest, validated)
        _update_job(paths, run_id, status="completed", completed_groups=len(manifest["groups"]), finished_at=timestamp(), artifact_links=links, error="")
    except Exception as error:
        message = str(error).replace(os.environ.get("OPENAI_API_KEY", ""), "[redacted]")
        _update_job(paths, run_id, status="failed", finished_at=timestamp(), error=message[:1000])
    finally:
        THREADS.pop(run_id, None)


def manifest_path(paths, run_id):
    return paths["runs"] / run_id


def _start(web, project, data, retry=False):
    paths = _paths(project)
    ai_input = _read(paths["ai_input"], {})
    coverage = _read(paths["coverage"], {})
    if not ai_input:
        raise ValueError("Analyse and confirm the architect packet before starting AI extraction.")
    settings = _save_settings(paths, data.get("settings", {}))
    groups = _available(ai_input, coverage)
    summary = estimate(settings, groups, _cost_per_group())
    _assert_startable(settings, groups, summary)
    previous = _recover_interrupted(paths)
    if previous.get("status") in {"queued", "running", "cancel_requested"}:
        raise ValueError("A vision extraction job is already active.")
    manifest, run_dir = _manifest(paths, ai_input, groups, settings, summary)
    job = {"schema_version": 1, "run_id": manifest["run_id"], "status": "queued", "started_at": timestamp(),
           "finished_at": "", "source_fingerprint": manifest["source_fingerprint"],
           "selection_fingerprint": manifest.get("selection_fingerprint", ""), "estimate": summary,
           "total_groups": len(manifest["groups"]), "completed_groups": 0, "current_group": "", "error": "",
           "manifest_path": str(run_dir / "request_manifest.json"), "retry_of": previous.get("run_id", "") if retry else ""}
    _atomic_json(paths["job"], job)
    thread = threading.Thread(target=_run, args=(web, project["id"], manifest["run_id"], manifest), daemon=True)
    THREADS[manifest["run_id"]] = thread
    thread.start()
    return _response(web, project)


def post(web, project, data):
    with LOCK:
        paths = _paths(project)
        action = data.get("action", "estimate")
        if action == "estimate":
            _save_settings(paths, data.get("settings", {}))
            return _response(web, project)
        if action == "start":
            return _start(web, project, data)
        if action == "retry":
            return _start(web, project, data, retry=True)
        if action == "cancel":
            job = _recover_interrupted(paths)
            if job.get("status") not in {"queued", "running"}:
                raise ValueError("There is no active vision extraction job to cancel.")
            _update_job(paths, job["run_id"], status="cancel_requested")
            return _response(web, project)
        raise ValueError("Action must be estimate, start, cancel, or retry.")
