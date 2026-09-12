"""Draft API persistence, optimistic concurrency and recoverable file batches."""

from copy import deepcopy
from functools import wraps
from pathlib import Path
import base64
import json
import os
import threading

from ai.calculator_draft import (
    DraftConflict, apply_calculator_draft, build_calculator_draft,
    check_revision, empty_calculator_draft, fingerprint, save_review, timestamp,
)
from ai.evidence_fusion import build_evidence_fusion
from ai.hourly_loads import hourly_model_summary, room_static_missing

LOCK = threading.RLock()
SOURCE_FILES = {name: name + ".json" for name in ("thermal_model", "thermal_evidence", "building_evidence", "drawing_coverage")}
TARGET_FILES = {name: name + ".json" for name in ("hourly_load_model", "schedule_library", "envelope_library", "envelope_model")}
ALLOWED_FILES = set(TARGET_FILES.values()) | {"calculator_draft.json"}


def serialized(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with LOCK:
            return function(*args, **kwargs)
    return locked


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else deepcopy(default or {})


def atomic_bytes(path, content):
    stage = path.with_name(path.name + ".draft-stage")
    with stage.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(stage, path)


def recover(root):
    journal = root / ".calculator-draft-transaction.json"
    if not journal.exists():
        return
    originals = read(journal)["originals"]
    if set(originals) - ALLOWED_FILES:
        raise ValueError("Invalid draft transaction journal; restore the project backup.")
    for name, content in originals.items():
        path = root / name
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_bytes(path, base64.b64decode(content))
    journal.unlink()


def commit(root, artifacts):
    """Readers share LOCK; a durable undo journal recovers process interruption."""
    if set(artifacts) - ALLOWED_FILES:
        raise ValueError("Unsupported transaction artifact.")
    originals = {name: base64.b64encode((root / name).read_bytes()).decode() if (root / name).exists() else None for name in artifacts}
    contents = {name: json.dumps(value, indent=2, allow_nan=False).encode() for name, value in artifacts.items()}
    journal = root / ".calculator-draft-transaction.json"
    atomic_bytes(journal, json.dumps({"originals": originals}).encode())
    try:
        for name, content in contents.items():
            atomic_bytes(root / name, content)
    except Exception:
        recover(root)
        raise
    journal.unlink()


def snapshots(root):
    return {name: fingerprint(read(root / filename)) for name, filename in {**SOURCE_FILES, **TARGET_FILES, "design_requirements": "design_requirements.json"}.items()}


def freshness(root, draft):
    expected = draft.get("source_fingerprints", {})
    return draft.get("schema_version") == 2 and all(expected.get(name) == fingerprint(read(root / filename)) for name, filename in SOURCE_FILES.items())


def response(web, project, draft):
    root = Path(project["review_dir"])
    if draft.get("schema_version") != 2:
        status = "legacy_review_required"
    elif not draft.get("revision"):
        status = "not_built"
    else:
        status = "current" if freshness(root, draft) else "stale"
    return {"id": project["id"], "calculator_draft": draft,
            "artifact_url": web.safe_link(root / "calculator_draft.json") if (root / "calculator_draft.json").exists() else "",
            "status": status,
            "artifact_links": {name: web.safe_link(root / file) for name, file in {**SOURCE_FILES, **TARGET_FILES, "architect_evidence_fusion": "architect_evidence_fusion.json"}.items() if (root / file).exists()}}


@serialized
def get(web, project):
    root = Path(project["review_dir"])
    recover(root)
    return response(web, project, read(root / "calculator_draft.json", empty_calculator_draft()))


@serialized
def post(web, project, data):
    root = Path(project["review_dir"])
    recover(root)
    path = root / "calculator_draft.json"
    draft = read(path, empty_calculator_draft())
    action = data.get("action", "build")
    if action == "build":
        if not all((root / SOURCE_FILES[name]).exists() for name in ("thermal_model", "building_evidence")):
            raise ValueError("Build thermal-model and building evidence first.")
        sources = {name: read(root / file) for name, file in SOURCE_FILES.items()}
        fusion_path = root / "architect_evidence_fusion.json"
        fusion = read(fusion_path, {})
        if not fusion or fusion.get("source_fingerprint") != sources["drawing_coverage"].get("source_fingerprint"):
            fusion = build_evidence_fusion(read(root / "ai_input.json", {"source_pdf": sources["building_evidence"].get("source_pdf", "")}), sources["drawing_coverage"], sources["building_evidence"],
                                           read(root / "spatial_ocr.json", {}), read(root / "vector_geometry.json", {}), read(root / "vision_response.json", {}),
                                           read(root / "dimension_wall_matches.json", {}), read(root / "geometry_confirmation.json", {}))
            atomic_bytes(fusion_path, json.dumps(fusion, indent=2, allow_nan=False).encode())
        draft = build_calculator_draft(sources["thermal_model"], sources["building_evidence"], sources["drawing_coverage"], draft,
            {name: web.safe_link(root / file) for name, file in SOURCE_FILES.items() if (root / file).exists()}, sources["thermal_evidence"], fusion)
        commit(root, {path.name: draft})
        return response(web, project, draft)
    check_revision(draft, data.get("expected_revision"))
    if not freshness(root, draft):
        raise DraftConflict("Source evidence changed. Rebuild the draft before reviewing or applying it.", "source_conflict")
    if action == "save_review":
        draft = save_review(draft, data.get("decisions", {}), data.get("expected_revision"))
        commit(root, {path.name: draft})
        return response(web, project, draft)
    if action not in {"preview_apply", "apply"}:
        raise ValueError("Action must be build, save_review, preview_apply, or apply.")
    current = snapshots(root)
    preview_token = fingerprint({"draft_revision": draft["revision"], "decisions": draft["decisions"], "inputs": current})
    if action == "apply" and data.get("preview_token") != preview_token:
        raise DraftConflict("Calculator inputs or review decisions changed. Preview the changes again.", "preview_conflict")
    requirements = read(root / "design_requirements.json")
    target_values = {name: read(root / TARGET_FILES[name]) for name in TARGET_FILES}
    outcome = apply_calculator_draft(draft, None, *(target_values[name] for name in TARGET_FILES),
                                    source_requirements_updated_at=requirements.get("updated_at", ""))
    summary = outcome["summary"]
    changed = [name for name, flag in outcome["changed"].items() if flag]
    if action == "apply" and changed:
        report_path = root / "hourly_load_report.json"
        if report_path.exists():
            summary["reports_marked_stale"] = ["hourly_load_report.json"]
        draft = deepcopy(draft)
        draft["revision"] += 1
        draft["updated_at"] = timestamp()
        draft["apply_summary"] = summary
        draft["application_receipts"].append({"applied_at": timestamp(), "draft_revision": data["expected_revision"],
            "reviewed_candidates": {cid: row["candidate_fingerprint"] for cid, row in draft["decisions"].items() if row["decision"] in {"accept", "edit"}},
            "affected_targets": changed,
            "previous_fingerprints": current, "new_fingerprints": {name: fingerprint(outcome[name]) for name in changed},
            "previous_artifact_revisions": {name: target_values[name].get("revision", target_values[name].get("updated_at", "")) for name in changed},
            "new_artifact_revisions": {name: outcome[name].get("revision", outcome[name].get("updated_at", "")) for name in changed},
            "outcomes": summary})
        commit(root, {**{TARGET_FILES[name]: outcome[name] for name in changed}, path.name: draft})
    readiness = hourly_model_summary(outcome["hourly_load_model"], requirements)
    for room in outcome["hourly_load_model"]["rooms"]:
        for missing in room_static_missing(room):
            readiness.setdefault("issues", []).append({"scope": "room", "status": "blocked", "affected_id": room["room_id"],
                "reason": "Supply reviewed " + missing, "source_artifact": "hourly_load_model.json"})
    # Model readiness never asserts that a report is review-ready.
    readiness["status"] = "blocked" if any(i["status"] == "blocked" for i in readiness.get("issues", [])) else "review_required"
    return {**response(web, project, draft), "preview_token": preview_token, "preview": summary, "apply_summary": summary,
            "changed_artifacts": changed, "readiness": readiness, "status": "preview" if action == "preview_apply" else "applied"}
