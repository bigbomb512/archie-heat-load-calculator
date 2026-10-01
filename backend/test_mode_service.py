"""Isolated, loopback-only end-to-end test runs for the contractor workflow."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from backend.vision_extraction_service import _atomic_json


_LOCK = threading.RLock()
_RUNNING: dict[str, threading.Thread] = {}
RUN_NAMESPACE = "test_runs"
_TEST_WORKFLOW_VERSION = "4"
_TEST_REPORT_LABEL = "TEST RUN — AI preliminary estimate — not engineering reviewed or validated"


class _RunWeb:
    """Small adapter required by project services, scoped to one run."""

    def __init__(self, web, root):
        self._web = web
        self._root = Path(root)

    def safe_link(self, _path):
        # Test-run artifacts are summarized in the run response; regular
        # project artifact routes must not be able to address this workspace.
        return ""

    def update_project(self, _project):
        return None

    def workflow_required_artifacts(self, project, paths=None):
        return self._web.workflow_required_artifacts(project, paths)


def enabled(configuration=None):
    if configuration is None:
        from backend import security
        configuration = security.config()
    return configuration.environment == "test" and os.environ.get("ARCHIE_TEST_MODE") == "1"


def _root(web):
    root = (Path(web.WEB_REVIEW) / RUN_NAMESPACE).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _assert_allowed(web, *, client_host="127.0.0.1"):
    from backend import security
    configuration = security.config()
    if not enabled(configuration):
        raise PermissionError("Local test mode is not enabled.")
    if client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise PermissionError("Local test mode accepts loopback requests only.")
    if not web.is_loopback_bind():
        raise PermissionError("Local test mode requires a loopback server bind.")


def _fixture_project(web):
    projects = web.load_projects()
    configured = os.environ.get("ARCHIE_TEST_FIXTURE_PROJECT_ID", "").strip()
    if configured:
        project = projects.get(configured)
        if not project:
            raise ValueError("The configured local test fixture project was not found.")
        candidates = [project]
    else:
        candidates = [item for item in projects.values()
                      if "butcher buffet" in str(item.get("name", "")).casefold()]
        candidates.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    for project in candidates:
        pdf = Path(project.get("pdf", ""))
        if project.get("analysed") and pdf.is_file() and Path(project.get("review_dir", "")).is_dir():
            return project
    raise ValueError("No saved local full-building PDF fixture is available. Set ARCHIE_TEST_FIXTURE_PROJECT_ID to an analysed project with its PDF.")


def _fingerprint(project):
    digest = hashlib.sha256()
    digest.update(_TEST_WORKFLOW_VERSION.encode("ascii"))
    pdf = Path(project["pdf"])
    with pdf.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(str(project.get("analysis_version", "")).encode())
    source_root = Path(project["review_dir"])
    for path in sorted(source_root.glob("*.json"), key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    fixture_path = Path(__file__).parent / "fixtures" / "test_workspace_full_building.json"
    digest.update(fixture_path.read_bytes())
    return digest.hexdigest()


def _public_run(run):
    if not isinstance(run, dict):
        return {}
    result = {key: deepcopy(run.get(key)) for key in (
        "run_id", "status", "scenario", "fixture_name", "fixture_fingerprint",
        "started_at", "updated_at", "finished_at", "current_stage", "stages",
        "coverage", "provisional_count", "blocked_count", "excluded_count",
        "report_status", "report_label", "report_summary", "error", "remediation",
        "retryable", "fixture_notice", "downstream",
        "skills_summary",
    ) if key in run}
    return result


def _read_run(root, run_id):
    if not run_id or not run_id.replace("-", "").isalnum():
        raise ValueError("Test run was not found.")
    run_dir = (root / run_id).resolve()
    try:
        run_dir.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("Test run was not found.") from error
    path = run_dir / "run.json"
    if not path.is_file():
        raise ValueError("Test run was not found.")
    return run_dir, json.loads(path.read_text(encoding="utf-8"))


def status(web, *, client_host="127.0.0.1"):
    _assert_allowed(web, client_host=client_host)
    root = _root(web)
    try:
        fixture = _fixture_project(web)
        fingerprint = _fingerprint(fixture)
        fixture_name = str(fixture.get("name", "Local drawing fixture"))
    except (OSError, ValueError) as error:
        return {"enabled": True, "available": False, "fixture_name": "", "message": str(error), "active_run": None}
    latest = None
    for path in sorted(root.glob("*/run.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if candidate.get("fixture_fingerprint") == fingerprint:
            latest = candidate
            break
    fixture_definition = json.loads((Path(__file__).parent / "fixtures" / "test_workspace_full_building.json").read_text(encoding="utf-8"))
    return {"enabled": True, "available": True, "fixture_name": fixture_name,
            "fixture_warning": fixture_definition.get("warning", ""),
            "fixture_fingerprint": fingerprint, "active_run": _public_run(latest) if latest else None}


def _write_stage(run_dir, run, stage, status_value="complete", **extra):
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    run["current_stage"] = stage
    run["updated_at"] = now
    rows = run.setdefault("stages", [])
    found = next((row for row in rows if row.get("id") == stage), None)
    update = {"id": stage, "status": status_value, "updated_at": now, **extra}
    if found is None:
        rows.append(update)
    else:
        found.update(update)
    _atomic_json(run_dir / "run.json", run)


def _clone_source(fixture, workspace, copied_pdf):
    source_root = Path(fixture["review_dir"])
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fixture["pdf"], copied_pdf)
    # Copy local inputs and evidence, but omit packages, caches, and old reports
    # that are not inputs to a fresh run.
    excluded = {
        "report_packages", "calculator_input_sets", "ai_preliminary_input_sets",
        "manual_vision_handoff", "chatgpt_packet", "ai_preliminary_run.json",
        "ai_preliminary_input_set.json", "hourly_ai_preliminary_load_report.json",
    }
    for child in source_root.iterdir():
        if child.name in excluded or child.name.endswith(".lock"):
            continue
        destination = workspace / child.name
        if child.is_file() and child.suffix.lower() == ".json":
            shutil.copy2(child, destination)
        elif child.is_dir() and child.name in {"reasoning_packet", "thumbnails"}:
            shutil.copytree(child, destination, dirs_exist_ok=True)


def _prepare_codex_skill_inputs(project, run):
    """Record test-only AI consent and point the skill runner at copied PDF pages."""
    from ai.vision_extraction import select_page_groups
    from backend.skill_workflow_service import _project_paths, _read

    paths = _project_paths(project)
    ai_input = _read(paths["ai_input"], {})
    coverage = _read(paths["coverage"], {})
    groups = select_page_groups(ai_input, coverage)
    workspace = paths["root"]
    selected_ids = [row["group_id"] for row in groups]
    _atomic_json(workspace / "vision_extraction_settings.json", {
        "schema_version": 1, "owner_opt_in": True, "selected_group_ids": selected_ids,
        "provider": "Codex CLI test run", "model": "Codex CLI configured model",
        "test_run_only": True,
    })
    page_images = {row.get("page"): row for row in ai_input.get("source_files", {}).get("page_images", [])
                   if isinstance(row, dict)}
    manifest_groups = []
    for group in groups:
        members = []
        for page in group.get("pages", []):
            number = page.get("page")
            image = page_images.get(number, {})
            relative = str(image.get("path", ""))
            image_path = Path(relative)
            if image_path.is_absolute():
                try:
                    relative = image_path.resolve().relative_to(Path(project["source_review_dir"]).resolve()).as_posix()
                except (OSError, ValueError):
                    relative = ""
            copied_image = (workspace / relative).resolve() if relative else None
            if copied_image is None or workspace.resolve() not in copied_image.parents or not copied_image.is_file():
                continue
            members.append({**page, "image_path": str(copied_image)})
        if members:
            manifest_groups.append({**group, "pages": members})
    run_dir = workspace / "vision_extraction_runs" / "codex-test"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "request_manifest.json"
    _atomic_json(manifest_path, {"schema_version": 1, "run_id": "codex-test", "groups": manifest_groups,
                                 "test_run_only": True})
    _atomic_json(workspace / "vision_extraction_job.json", {
        "schema_version": 1, "run_id": "codex-test", "status": "completed",
        "manifest_path": str(manifest_path), "test_run_only": True,
    })
    test_audit_path = workspace / "test_run.json"
    audit = json.loads(test_audit_path.read_text(encoding="utf-8"))
    audit["synthetic_local_consent"].update({
        "external_provider": True, "provider": "Codex CLI", "scope": "selected_project_pdf_pages_and_skill_context",
        "external_research": False, "live_lookup": False,
    })
    _atomic_json(test_audit_path, audit)
    if not manifest_groups:
        raise RuntimeError("The isolated workspace has no rendered evidence pages for Codex.")
    run["fixture_notice"] = (
        "Codex AI test: selected pages from this isolated project copy are sent to the signed-in Codex service. "
        "No external research is enabled; all proposals remain draft-only."
    )
    return len(manifest_groups)


def _inject_test_geometry_fixture(project, run):
    """Seed deterministic synthetic room candidates before local inference starts."""
    if not project.get("test_run"):
        raise PermissionError("Synthetic fixture geometry is available only in an isolated test run.")
    fixture_path = Path(__file__).parent / "fixtures" / "test_workspace_full_building.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    workspace = Path(project["review_dir"])
    page = int(fixture["plan_page"])
    scale = float(fixture["scale_mm_per_px"])
    geometry_candidates = []
    for definition in fixture.get("rooms", []):
        x1, y1, x2, y2 = [float(value) for value in definition["rectangle_px"]]
        if x2 <= x1 or y2 <= y1:
            raise ValueError("The deterministic room fixture contains invalid boundary geometry.")
        polygon = [[x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1]]
        derived_area = ((x2 - x1) * (y2 - y1) * scale * scale) / 1_000_000.0
        if abs(derived_area - float(definition["area_m2"])) > 1e-9:
            raise ValueError("The deterministic room fixture area does not match its boundary operands.")
        fixture_note = fixture["warning"]
        geometry_candidates.append({
            "room_geometry_id": f"{fixture['fixture_id']}:{definition['label'].casefold().replace(' ', '-')}",
            "label": definition["label"], "level_name": fixture["level_name"],
            "page": page, "source_pages": [page], "boundary_points_px": polygon,
            "scale_mm_per_px": scale, "scale_source": "test_fixture_only",
            "area_m2": derived_area,
            "formula": "test_fixture_polygon_area_px2 × (test_fixture_mm_per_px²) ÷ 1,000,000",
            "operands": {"polygon_area_px2": (x2 - x1) * (y2 - y1), "mm_per_px": scale},
            "fixture_id": fixture["fixture_id"],
            "preliminary_profile_id": definition["preliminary_profile_id"],
            "space_scope": definition["space_scope"],
            "confidence": "high", "confidence_score": float(definition["confidence"]),
            "room_label_bbox": [x1 + (x2 - x1) * 0.25, y1 + (y2 - y1) * 0.25,
                                x1 + (x2 - x1) * 0.75, y1 + (y2 - y1) * 0.75],
            "unresolved_fields": [], "conflicts": [], "test_fixture_only": True,
            "rationale": fixture_note,
        })
    vision_path = workspace / "vision_response.json"
    vision = json.loads(vision_path.read_text(encoding="utf-8")) if vision_path.exists() else {}
    result = vision.setdefault("result", {})
    geometry_review = result.setdefault("geometry_review", {})
    geometry_pages = geometry_review.setdefault("pages", [])
    page_row = next((row for row in geometry_pages if isinstance(row, dict) and row.get("page") == page), None)
    if page_row is None:
        page_row = {"page": page, "page_role": "main_floor_plan"}
        geometry_pages.append(page_row)
    page_row["room_geometry_candidates"] = geometry_candidates
    vision["test_fixture_only"] = True
    vision["test_fixture_warning"] = fixture["warning"]
    _atomic_json(vision_path, vision)
    fixture_audit_path = workspace / "test_run.json"
    fixture_audit = json.loads(fixture_audit_path.read_text(encoding="utf-8"))
    fixture_audit["geometry_fixture"] = {"fixture_id": fixture["fixture_id"], "warning": fixture["warning"],
                                          "room_count": len(geometry_candidates)}
    _atomic_json(fixture_audit_path, fixture_audit)
    run["fixture_notice"] = fixture["warning"]


def _inject_test_system_candidates(project, scenario):
    """Add isolated fixture domain proposals before model-input assembly."""
    from backend import ai_preliminary_service
    from ai import ai_preliminary

    workspace = Path(project["review_dir"])
    paths = ai_preliminary_service._paths(project)
    run_path = paths["run"]
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    proposal = run_data.get("local_room_inference_proposal") or {}
    if isinstance(proposal, list):
        proposal = {"rooms": proposal}
    if not isinstance(proposal, dict):
        raise RuntimeError("The local room-inference proposal is unavailable for the test fixture.")
    building = json.loads(paths["building"].read_text(encoding="utf-8"))
    vision = json.loads(paths["vision"].read_text(encoding="utf-8"))
    geometry = ai_preliminary_service._preliminary_geometry(paths)
    active_rooms = ai_preliminary._space_rows(building, vision, proposal.get("rooms", []), geometry)
    target = next((row for row in active_rooms if row.get("name") == "Bar"), None)
    if not target:
        raise RuntimeError("The deterministic AHU fixture room was not found in draft geometry.")
    room_id, zone_id = f"room-{target['key']}", f"zone-{target['key']}"
    definition = json.loads((Path(__file__).parent / "fixtures" / "test_workspace_full_building.json").read_text(encoding="utf-8"))
    source = "Test-only deterministic fixture — not project or engineering evidence."
    citation = {"page": None, "reference": "local test fixture", "excerpt": "Synthetic system values used only to exercise draft assembly."}
    schedule = {day: [1.0] * 24 for day in ("weekday", "saturday", "sunday", "holiday")}
    candidate = deepcopy(definition["ahu_candidate"])
    candidate.update({"served_zone_ids": [zone_id], "served_room_ids": [room_id], "origin": "controlled_fallback",
                      "source": source, "evidence": [citation], "rationale": "Deterministic local fixture only."})
    for row in candidate.get("airflow_records", []):
        row.update({"zone_id": zone_id, "origin": "controlled_fallback", "source": source, "evidence": [citation], "schedule": schedule})
    for collection in ("fans", "duct_effects", "coils"):
        for row in candidate.get(collection, []):
            row.update({"origin": "controlled_fallback", "source": source, "evidence": [citation]})
    if scenario == "blocked_ahu_plant":
        candidate["served_zone_ids"] = [zone_id, "zone-unknown-fixture"]
    plant = deepcopy(definition["plant_candidate"])
    plant.update({"origin": "controlled_fallback", "source": source, "evidence": [citation]})
    for circuit in plant.get("circuits", []):
        circuit.update({"plant_id": plant["plant_id"], "origin": "controlled_fallback", "source": source, "evidence": [citation], "schedule": schedule})
        for collection in ("pumps", "pipe_effects"):
            for row in circuit.get(collection, []):
                row.update({"origin": "controlled_fallback", "source": source, "evidence": [citation], "schedule": schedule})
    if scenario == "blocked_ahu_plant":
        plant["served_ahu_ids"] = ["ahu-unknown-fixture"]
        plant["circuits"][0]["served_ahu_ids"] = ["ahu-unknown-fixture"]
    envelope = deepcopy(definition.get("envelope", {}))
    for row in envelope.get("surfaces", []) + envelope.get("openings", []):
        row.update({"source": source, "excerpt": f"Test fixture only: {row.get('excerpt', '')}", "confidence": 0.9})
    proposal["surfaces"] = envelope.get("surfaces", [])
    proposal["openings"] = envelope.get("openings", [])
    proposal["air_side_candidates"] = [candidate]
    proposal["plant_candidates"] = [plant]
    run_data["local_room_inference_proposal"] = proposal
    run_data["test_fixture_domains_only"] = True
    _atomic_json(run_path, run_data)


def _wait_room_inference(web, project, timeout=180):
    from backend import room_inference_service
    adapter = _RunWeb(web, project["review_dir"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = room_inference_service.get(adapter, project)
        if state.get("status") not in {"queued", "running"}:
            return state
        time.sleep(0.1)
    raise TimeoutError("Room inference exceeded the local test-run time limit.")


def _wait_skill_workflow(web, project, timeout=1800):
    from backend import skill_workflow_service
    adapter = _RunWeb(web, project["review_dir"])
    deadline = time.monotonic() + timeout
    terminal = {"completed", "needs_review", "failed", "blocked", "stale"}
    while time.monotonic() < deadline:
        state = skill_workflow_service.get(adapter, project)
        if state.get("status") in terminal:
            return state
        time.sleep(0.5)
    raise TimeoutError("Codex skill workflow exceeded the local test-run time limit.")


def _resolve_model_inputs(adapter, project, timeout=120):
    """Reuse the automatic post-inference resolver, waiting out its project lock."""
    from backend import model_input_resolution_service
    deadline = time.monotonic() + timeout
    while True:
        try:
            return model_input_resolution_service.post(adapter, project, {"action": "resolve"})
        except model_input_resolution_service.ModelInputResolutionError as error:
            if error.code != "resolver_concurrent" or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _report_summary(report):
    if not isinstance(report, dict) or not report:
        return {}
    summary = {key: report.get(key) for key in (
        "status", "label", "peak_total_kw", "peak_hour", "included_scope_only",
        "scope_summary", "safety_factor", "safety_allowance_kw", "final_design_total_kw",
    ) if key in report}
    provenance = report.get("provenance", {}) if isinstance(report.get("provenance", {}), dict) else {}
    components = provenance.get("governing_components", [])
    peak_hour = summary.get("peak_hour")
    if peak_hour is None and isinstance(report.get("project_peak"), dict):
        peak_hour = report["project_peak"].get("hour")
    if peak_hour is not None:
        governing = [row for row in components if isinstance(row, dict) and row.get("hour") == peak_hour]
        if governing:
            components = governing
    summary["provenance_components"] = [{
        "entity_id": row.get("entity_id", ""), "hour": row.get("hour"),
        "component": row.get("component", ""), "load_kw": row.get("load_kw"),
        "sources": [{
            "origin": ref.get("origin", ""), "formula": ref.get("formula", ""),
            "citation": (ref.get("citation") or {}).get("citation", "") if isinstance(ref.get("citation"), dict) else "",
            "page": ((ref.get("citation") or {}).get("page") if isinstance(ref.get("citation"), dict) else None),
        } for ref in row.get("provenance", [])[:2]],
    } for row in components[:12] if isinstance(row, dict)]
    return summary


def _stamp_test_report(path):
    """Mark a draft report as belonging to the isolated local test workspace."""
    if not path.exists():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    report["label"] = _TEST_REPORT_LABEL
    report["test_run"] = True
    _atomic_json(path, report)
    return report


def _run_worker(web, run_id, source_project, fingerprint, scenario):
    root = _root(web)
    run_dir = root / run_id
    run_path = run_dir / "run.json"
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
        workspace = run_dir / "workspace"
        pdf = run_dir / "source.pdf"
        _write_stage(run_dir, run, "copy_fixture", "running")
        _clone_source(source_project, workspace, pdf)
        _write_stage(run_dir, run, "copy_fixture", "complete")
        project = {"id": run_id, "name": run["fixture_name"], "pdf": str(pdf), "pages": source_project.get("pages", 0),
                   "analysed": False, "review_dir": str(workspace), "test_run": True,
                   "source_project_id": source_project["id"], "source_review_dir": source_project["review_dir"],
                   "created_at": run["started_at"]}
        adapter = _RunWeb(web, workspace)
        _atomic_json(workspace / "test_run.json", {
            "run_id": run_id, "fixture_fingerprint": fingerprint, "scenario": scenario,
            "synthetic_local_consent": {"recorded_at": run["started_at"], "scope": "local_fixture_processing",
                                        "external_provider": False, "external_research": False, "live_lookup": False},
            "label": "TEST RUN — AI preliminary estimate — not engineering reviewed or validated",
        })

        _write_stage(run_dir, run, "analyse_pdf", "running")
        result = web.create_review_packet(pdf, workspace, include_structure=True)
        packet = web.load_json(result["packet"])
        spatial_path = web.create_spatial_ocr(result["packet"], workspace / "spatial_ocr.json")
        ai_input = web.build_ai_packet(packet, spatial_ocr=dict(web.load_json(spatial_path), source=str(spatial_path)))
        (workspace / "ai_input.json").write_text(json.dumps(ai_input, indent=2), encoding="utf-8")
        project.update({"analysed": True, "review_dir": str(workspace), "packet": result["packet"],
                        "pages": result["kept_count"], "relevant": result["primary_count"]})
        web._rebuild_evidence_chain(project)
        _write_stage(run_dir, run, "analyse_pdf", "complete", pages=int(project.get("pages", 0)))

        codex_run = scenario == "codex_all_skills"
        if scenario not in {"missing_geometry", "codex_all_skills"}:
            _write_stage(run_dir, run, "test_fixture_geometry", "running")
            _inject_test_geometry_fixture(project, run)
            _write_stage(run_dir, run, "test_fixture_geometry", "complete", rooms=5,
                         notice=run.get("fixture_notice", ""))
        elif codex_run:
            _write_stage(run_dir, run, "test_fixture_geometry", "skipped",
                         remediation="No synthetic geometry is injected; Codex must propose PDF-supported boundaries.")
        else:
            _write_stage(run_dir, run, "test_fixture_geometry", "skipped",
                         remediation="This scenario intentionally uses only geometry found in the source PDF.")
        _write_stage(run_dir, run, "room_inference", "running")
        from backend import room_inference_service
        room_inference_service.post(adapter, project, {"action": "start"})
        inference = _wait_room_inference(web, project)
        if inference.get("status") != "completed":
            raise RuntimeError(inference.get("remediation", ["Room inference did not complete."])[0])
        _write_stage(run_dir, run, "room_inference", "complete", candidate_count=inference.get("candidate_count", 0))

        if scenario not in {"missing_geometry", "codex_all_skills"}:
            _inject_test_system_candidates(project, scenario)

        if codex_run:
            if os.environ.get("ARCHIE_TEST_CODEX") != "1":
                raise PermissionError("Enable the loopback-only Codex test mode before starting this scenario.")
            image_groups = _prepare_codex_skill_inputs(project, run)
            _write_stage(run_dir, run, "archie_skills", "running", parent_skills=10, subskills=44,
                         selected_page_groups=image_groups, provider="Codex CLI", synthetic_domain_values=0)
            from backend import skill_workflow_service
            skill_workflow_service.post(adapter, project, {"action": "start"})
            skills = _wait_skill_workflow(web, project)
            manifest = json.loads((workspace / "skill_workflow_run.json").read_text(encoding="utf-8"))
            subskills = manifest.get("subskills", {})
            counts = {}
            for item in subskills.values():
                status_value = item.get("status", "unknown")
                counts[status_value] = counts.get(status_value, 0) + 1
            run["skills_summary"] = {"parent_count": 10, "subskill_count": len(subskills),
                                     "status_counts": counts, "workflow_status": skills.get("status", "unknown"),
                                     "provider": "Codex CLI", "external_research": False}
            _write_stage(run_dir, run, "archie_skills", "complete" if skills.get("status") in {"completed", "needs_review"} else "needs_review",
                         **run["skills_summary"])

        _write_stage(run_dir, run, "model_inputs", "running")
        from backend import ai_preliminary_service
        if scenario == "resolver_failure":
            raise RuntimeError("Test scenario injected a model-input resolver failure.")
        resolved = _resolve_model_inputs(adapter, project)
        required = resolved.get("required_artifacts", [])
        _write_stage(run_dir, run, "model_inputs", "complete", required_artifacts=len(required),
                     provisional_count=sum(bool(item.get("provisional")) for item in required),
                     blocked_count=sum(not item.get("exists") for item in required))

        if scenario in {"missing_geometry", "codex_all_skills"}:
            geometry = json.loads((workspace / "geometry_resolution.json").read_text(encoding="utf-8"))
            proofs = geometry.get("room_geometry_proofs", [])
            eligible = [row for row in proofs if row.get("status") in {"ai_estimated", "geometry_confirmed"} and isinstance(row.get("area_m2"), (int, float)) and row["area_m2"] > 0]
            if scenario == "missing_geometry" and not eligible:
                raise RuntimeError("Test scenario stopped at rooms and geometry: the source PDF did not produce a validated positive room area.")
        if codex_run:
            missing = [item for item in required if not item.get("exists")]
            if missing:
                items = [{"key": item.get("key", ""), "target_id": item.get("target_id", ""),
                          "remediation": item.get("remediation", "Review this artifact in its advanced editor.")}
                         for item in missing]
                _write_stage(run_dir, run, "cooling_calculation", "blocked", missing_artifacts=items,
                             remediation="Calculation was not run because required artifacts are unavailable.")
                run.update({"status": "blocked", "current_stage": "cooling_calculation", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "report_status": "not_calculated",
                            "report_label": "", "error": "Required draft calculation artifacts are missing.",
                            "remediation": "Resolve the listed artifacts, then rerun the Codex skills walkthrough.", "retryable": True})
                _atomic_json(run_path, run)
                return

        _write_stage(run_dir, run, "cooling_calculation", "running")
        if scenario == "stale_dependency":
            run_file = workspace / "ai_preliminary_run.json"
            if run_file.exists():
                changed = json.loads(run_file.read_text(encoding="utf-8"))
                changed["test_stale_injection"] = uuid.uuid4().hex
                _atomic_json(run_file, changed)
        calculation = ai_preliminary_service.post(adapter, project, {"action": "calculate"})
        report = calculation.get("hourly_ai_preliminary_load_report", {})
        if not report:
            report = json.loads((workspace / "hourly_ai_preliminary_load_report.json").read_text(encoding="utf-8"))
        if report.get("review_status") in {"review_ready", "validated"} or report.get("status") == "review_ready":
            raise RuntimeError("Local test mode refused to activate a reviewed or validated report.")
        report["label"] = "TEST RUN — AI preliminary estimate — not engineering reviewed or validated"
        report["test_run"] = True
        _atomic_json(workspace / "hourly_ai_preliminary_load_report.json", report)
        _write_stage(run_dir, run, "cooling_calculation", "complete", report_status=report.get("status", "draft"))

        # Resolve and materialize optional downstream system summaries after
        # the room report exists. Their missing operands remain visible.
        from backend import ahu_resolution_service, plant_resolution_service
        _write_stage(run_dir, run, "ahu_resolution", "running")
        ahu_resolution_service.post(adapter, project, {"action": "resolve"})
        ahu_result = ahu_resolution_service.post(adapter, project, {"action": "materialize_preliminary"})
        ahu_report = _stamp_test_report(workspace / "hourly_ai_preliminary_ahu_load_report.json")
        _write_stage(run_dir, run, "ahu_resolution", "complete" if not ahu_result.get("preliminary_model", {}).get("blocked_reason") else "needs_review",
                     remediation=ahu_result.get("preliminary_model", {}).get("blocked_reason", ""))
        _write_stage(run_dir, run, "plant_resolution", "running")
        plant_resolution_service.post(adapter, project, {"action": "resolve"})
        plant_result = plant_resolution_service.post(adapter, project, {"action": "materialize_preliminary"})
        plant_report = _stamp_test_report(workspace / "hourly_ai_preliminary_plant_load_report.json")
        _write_stage(run_dir, run, "plant_resolution", "complete" if not plant_result.get("preliminary_model", {}).get("blocked_reason") else "needs_review",
                     remediation=plant_result.get("preliminary_model", {}).get("blocked_reason", ""))
        if scenario == "blocked_ahu_plant":
            run.setdefault("downstream", {})["scenario_note"] = "Test-only scenario: AHU/plant ownership conflicts are intentionally injected after the cooling report; the room subtotal remains separate."

        register_path = workspace / "model_input_resolution.json"
        register = web.load_json(register_path) if register_path.exists() else {}
        records = register.get("records", [])
        run["coverage"] = _coverage(workspace, records, inference.get("candidate_count", 0))
        assumptions = {(row.get("target_id", ""), row.get("target", "")) for row in records if row.get("status") in {"provisional", "needs_review"}}
        for artifact_name, collections in (("thermal_surface_resolution.json", ("surfaces",)),
                                           ("ahu_resolution.json", ("systems", "airflow_records", "fans", "duct_effects", "coils")),
                                           ("plant_resolution.json", ("systems", "circuits", "pumps", "pipe_effects"))):
            path = workspace / artifact_name
            if path.exists():
                artifact = json.loads(path.read_text(encoding="utf-8"))
                for key in collections:
                    for row in artifact.get(key, []):
                        if isinstance(row, dict) and row.get("status") in {"provisional", "needs_review"}:
                            component = row.get("surface_id", row.get("ahu_id", row.get("plant_id", row.get("record_id", ""))))
                            assumptions.add((component, row.get("status", "")))
        run["provisional_count"] = len(assumptions)
        run["blocked_count"] = sum(row.get("status") == "blocked" for row in records)
        run["excluded_count"] = sum(row.get("status") == "excluded" for row in records)
        run["report_status"] = report.get("status", "draft")
        run["report_label"] = report.get("label", "TEST RUN — AI preliminary estimate — not engineering reviewed or validated")
        run["report_summary"] = _report_summary(report)
        run["downstream"] = {
            "ahu_status": ahu_result.get("status", "needs_review"),
            "ahu_blocked_reason": ahu_result.get("preliminary_model", {}).get("blocked_reason", ""),
            "ahu_peak": ahu_report.get("included_scope_peak", {}),
            "ahu_report_label": ahu_report.get("label", ""),
            "plant_status": plant_result.get("status", "needs_review"),
            "plant_blocked_reason": plant_result.get("preliminary_model", {}).get("blocked_reason", ""),
            "plant_peak": plant_report.get("included_scope_peak", {}),
            "plant_report_label": plant_report.get("label", ""),
        }
        run.update({"status": "completed", "current_stage": "completed", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "error": "", "retryable": False,
                    "fixture_notice": run.get("fixture_notice", "")})
        _atomic_json(run_path, run)
    except Exception as error:
        try:
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run.update({"status": "failed", "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "error": _safe_error(error),
                        "remediation": "Retry the walkthrough. If it fails again, open the named advanced editor in the failed stage.",
                        "retryable": True})
            failed = run.get("current_stage", "workflow")
            _write_stage(run_dir, run, failed, "failed", error_code="test_stage_failed", remediation=run["remediation"])
        except Exception:
            pass
    finally:
        with _LOCK:
            _RUNNING.pop(run_id, None)


def _safe_error(error):
    text = " ".join(str(error).split())
    # Service exceptions can contain host paths; keep browser details generic.
    if "/" in text or "\\" in text or len(text) > 280:
        return "A workflow stage could not complete. Open its remediation and retry."
    return text or "A workflow stage could not complete."


def _coverage(workspace, records, room_count):
    def rows(names, keys):
        for name in names:
            path = workspace / name
            if path.exists():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for key in keys:
                    if isinstance(value.get(key), list):
                        return value[key]
        return []
    surfaces = rows(("thermal_surface_resolution.json", "geometry_resolution.json"), ("surfaces", "records"))
    openings = rows(("opening_register.json", "window_scan.json"), ("openings", "records", "sightings"))
    air_systems = rows(("ahu_resolution.json",), ("systems",))
    plant_systems = rows(("plant_resolution.json",), ("systems",))
    geometry = rows(("geometry_resolution.json",), ("room_geometry_proofs", "rooms", "records"))
    geometry_document = json.loads((workspace / "geometry_resolution.json").read_text(encoding="utf-8")) if (workspace / "geometry_resolution.json").exists() else {}
    if not surfaces:
        surfaces = geometry_document.get("thermal_surface_ledger", {}).get("surfaces", [])
    input_set_path = workspace / "ai_preliminary_input_set.json"
    input_set = json.loads(input_set_path.read_text(encoding="utf-8")) if input_set_path.exists() else {}
    surface_summary = input_set.get("surface_summary", {}) if isinstance(input_set, dict) else {}
    proposal_openings = input_set.get("proposal", {}).get("openings", []) if isinstance(input_set.get("proposal", {}), dict) else []
    if not openings:
        openings = proposal_openings
    room_resolved = sum(row.get("status") in {"ai_estimated", "geometry_confirmed", "resolved", "provisional"} for row in geometry)
    surface_total = max(len(surfaces), int(surface_summary.get("discovered", 0) or 0))
    opening_total = max(len(openings), int(surface_summary.get("openings_discovered", 0) or 0))
    surface_resolved = max(sum(row.get("status") in {"resolved", "provisional", "included", "ai_estimated"} for row in surfaces), int(surface_summary.get("included", 0) or 0))
    opening_resolved = max(sum(row.get("status") in {"resolved", "provisional", "included", "ai_estimated"} for row in openings), int(surface_summary.get("openings_included", 0) or 0))
    return {"rooms": {"total": max(room_count, len(geometry)), "resolved": room_resolved},
            "surfaces": {"total": surface_total, "resolved": surface_resolved},
            "openings": {"total": opening_total, "resolved": opening_resolved},
            "air_systems": {"total": len(air_systems), "resolved": sum(row.get("status") in {"resolved", "provisional"} for row in air_systems)},
            "plant_systems": {"total": len(plant_systems), "resolved": sum(row.get("status") in {"resolved", "provisional"} for row in plant_systems)},
            "model_values": {"total": len(records), "resolved": sum(row.get("status") == "resolved" for row in records)}}


def run(web, *, scenario="complete", client_host="127.0.0.1"):
    _assert_allowed(web, client_host=client_host)
    if scenario not in {"complete", "missing_geometry", "resolver_failure", "stale_dependency", "blocked_ahu_plant", "codex_all_skills"}:
        raise ValueError("Unknown local test scenario.")
    if scenario == "codex_all_skills" and os.environ.get("ARCHIE_TEST_CODEX") != "1":
        raise PermissionError("Codex skill test mode is not enabled on this local server.")
    fixture = _fixture_project(web)
    fingerprint = _fingerprint(fixture)
    root = _root(web)
    with _LOCK:
        for run_id, thread in list(_RUNNING.items()):
            if thread.is_alive():
                run_dir, current = _read_run(root, run_id)
                if current.get("fixture_fingerprint") == fingerprint and current.get("scenario") == scenario:
                    return {"run": _public_run(current), "deduplicated": True}
        for path in sorted(root.glob("*/run.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if current.get("fixture_fingerprint") == fingerprint and current.get("scenario") == scenario and current.get("status") == "completed":
                return {"run": _public_run(current), "deduplicated": False, "reused": True}
        run_id = "test-" + uuid.uuid4().hex
        run_dir = root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        fixture_notice = json.loads((Path(__file__).parent / "fixtures" / "test_workspace_full_building.json").read_text(encoding="utf-8")).get("warning", "")
        if scenario == "codex_all_skills":
            fixture_notice = (
                "Codex AI test: selected pages from an isolated copy of this project PDF will be sent to the signed-in Codex service. "
                "No external research or live lookups are enabled; all proposals remain draft-only."
            )
        current = {"run_id": run_id, "status": "running", "scenario": scenario,
                  "fixture_name": str(fixture.get("name", "Local drawing fixture")),
                  "fixture_fingerprint": fingerprint, "started_at": now, "updated_at": now,
                  "stages": [], "coverage": {}, "report_status": "not_calculated",
                  "report_label": "TEST RUN — AI preliminary estimate — not engineering reviewed or validated",
                  "fixture_notice": fixture_notice}
        _atomic_json(run_dir / "run.json", current)
        thread = threading.Thread(target=_run_worker, args=(web, run_id, deepcopy(fixture), fingerprint, scenario), daemon=True)
        _RUNNING[run_id] = thread
        thread.start()
    return {"run": _public_run(current), "deduplicated": False, "reused": False}


def get_run(web, run_id, *, client_host="127.0.0.1"):
    _assert_allowed(web, client_host=client_host)
    _run_dir, run_data = _read_run(_root(web), run_id)
    return {"run": _public_run(run_data)}


def reset(web, run_id="", *, client_host="127.0.0.1"):
    _assert_allowed(web, client_host=client_host)
    root = _root(web)
    if run_id:
        with _LOCK:
            thread = _RUNNING.get(run_id)
            if thread and thread.is_alive():
                raise RuntimeError("A running test cannot be reset. Wait for it to finish first.")
        run_dir, _run_data = _read_run(root, run_id)
        shutil.rmtree(run_dir)
    else:
        raise ValueError("Choose the local test run to reset.")
    return {"reset": True, "run_id": run_id}
