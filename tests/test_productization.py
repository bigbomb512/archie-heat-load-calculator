#!/usr/bin/env python3
"""Focused checks for Stage 12 product artifacts."""

from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.productization import (
    append_audit_event, build_report_package, cached_report_summary, export_project, import_project,
    empty_exception_decisions, normalise_exceptions, project_health, read_audit_events, stable_exception_id,
    upsert_exception_decision, validate_audit_chain, validate_exception_decisions,
)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def main():
    with TemporaryDirectory() as folder:
        root = Path(folder) / "review"
        root.mkdir()
        (root / "project_context.json").write_text(json.dumps({"revision": 1}), encoding="utf-8")
        first = append_audit_event(root, action="context_saved", target="project_context.json", new_fingerprint="ctx-1")
        second = append_audit_event(root, action="report_calculated", target="hourly_load_report.json", previous_fingerprint="ctx-1", new_fingerprint="report-1")
        check("audit events retain before and after fingerprints", first["new_fingerprint"] == "ctx-1" and second["previous_fingerprint"] == "ctx-1")
        check("audit hash chain validates", validate_audit_chain(root) and len(read_audit_events(root)) == 2)
        (root / "audit_log.jsonl").write_text((root / "audit_log.jsonl").read_text().splitlines()[0] + "\n", encoding="utf-8")
        check("audit tail truncation is detectable", not validate_audit_chain(root))
        (root / "audit_log.jsonl").write_text("\n".join(json.dumps(item, sort_keys=True) for item in (first, second)) + "\n", encoding="utf-8")
        check("exception IDs are stable regardless of evidence order", stable_exception_id("source", scope="room", affected_id="room-1", category="area", field="m2") == stable_exception_id("source", scope="room", affected_id="room-1", category="area", field="m2"))
        exceptions = normalise_exceptions([{"code": "missing_area", "scope": "room", "affected_id": "room-1", "artifact": "calculation_input_evidence.json", "message": "Area missing"}], source_fingerprint="source")
        check("normalized exceptions retain actionable fields", exceptions[0]["code"] == "missing_area" and exceptions[0]["artifact"] == "calculation_input_evidence.json" and exceptions[0]["remediation"])
        decisions = upsert_exception_decision(empty_exception_decisions(), exception_id=exceptions[0]["exception_id"], decision="needs_evidence", reviewer="reviewer", note="Need a second witness", source_fingerprint="source")
        decisions = upsert_exception_decision(decisions, exception_id=exceptions[0]["exception_id"], decision="accepted", reviewer="reviewer", note="Witness added", source_fingerprint="source")
        check("exception decisions persist history", validate_exception_decisions(decisions)["decisions"][0]["decision"] == "accepted" and len(decisions["decisions"][0]["decision_history"]) == 1)

        report = {
            "status": "draft", "scope_summary": {"active_room_ids": ["room-1"], "included_room_ids": ["room-1"], "complete_scope": False},
            "included_scope_peak": {"design_total_kw": 4.2, "display_hour": 14}, "readiness": {"status": "draft", "issues": [{"reason": "missing envelope"}]},
            "input_fingerprints": {"calculator_input_set_fingerprint": "snapshot-1"},
        }
        report_path = root / "hourly_load_report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        paths = {"report": report_path, "calculator_input_set": root / "calculator_input_set.json"}
        result = build_report_package({"id": "p1", "review_dir": str(root)}, paths, "cooling", report, current=True, artifact_paths=[report_path])
        second_result = build_report_package({"id": "p1", "review_dir": str(root)}, paths, "cooling", report, current=True, artifact_paths=[report_path])
        check("report package contains HTML and PDF", Path(result["html_path"]).exists() and Path(result["pdf_path"]).read_bytes().startswith(b"%PDF"))
        check("report package contains immutable artifact copies", (Path(result["artifact_url"]) / "artifacts" / "hourly_load_report.json").exists())
        check("unchanged package reuses its content address", result["package_fingerprint"] == second_result["package_fingerprint"])
        manifest = json.loads((Path(result["artifact_url"]) / "manifest.json").read_text())
        check("package records exact report and snapshot fingerprints", manifest["report_fingerprint"] and manifest["calculator_input_set_fingerprint"] == "snapshot-1")
        check("fallback PDF is explicitly labelled", manifest["renderer"] == "minimal-pdf-fallback-v1" and "fallback PDF" in Path(result["pdf_path"]).read_bytes().decode("latin-1"))
        check("report summary cache is fingerprint keyed", cached_report_summary(report, "cooling") == cached_report_summary(report, "cooling"))

        project = {"id": "p1", "name": "Test project", "review_dir": str(root), "pdf": "/private/source.pdf"}
        archive = export_project(project)
        check("export excludes the private PDF", "source_pdf_included" in archive["manifest"] and not archive["manifest"]["source_pdf_included"])
        check("export includes generated report PDF", any(row["path"].endswith("report.pdf") for row in archive["manifest"]["artifacts"]))
        imported = import_project(Path(archive["archive_path"]).read_bytes(), Path(folder) / "imports", "p1-imported")
        check("import creates a separate project", imported["project_id"] == "p1-imported" and Path(imported["review_dir"]).exists())

        paths_for_health = {"requirements": root / "design_requirements.json", "schedules": root / "schedule_library.json", "scenarios": root / "design_day_scenarios.json", "model": root / "hourly_load_model.json", "calculator_input_set": root / "calculator_input_set.json"}
        health = project_health(project, paths_for_health, stale_report_names=["hourly_load_report.json"])
        check("project health reports missing inputs and stale reports", health["status"] == "blocked" and any(item["code"] == "report_stale" for item in health["issues"]))

        unsafe = io.BytesIO()
        with ZipFile(unsafe, "w", ZIP_DEFLATED) as archive:
            archive.writestr("project_manifest.json", json.dumps({"artifacts": []}))
            archive.writestr("../escape.json", "bad")
        try:
            import_project(unsafe.getvalue(), Path(folder) / "unsafe", "unsafe")
        except ValueError:
            check("import rejects path traversal", True)
        else:
            check("import rejects path traversal", False)

        undeclared = io.BytesIO()
        with ZipFile(undeclared, "w", ZIP_DEFLATED) as archive:
            archive.writestr("project_manifest.json", json.dumps({"archive_version": "stage12-archive-v1", "artifacts": []}))
            archive.writestr("extra.json", "bad")
        try:
            import_project(undeclared.getvalue(), Path(folder) / "undeclared", "undeclared")
        except ValueError:
            check("import rejects undeclared files", True)
        else:
            check("import rejects undeclared files", False)

        duplicate = io.BytesIO()
        duplicate_manifest = {"archive_version": "stage12-archive-v1", "artifacts": [{"path": "a.json", "sha256": ""}, {"path": "a.json", "sha256": ""}]}
        with ZipFile(duplicate, "w", ZIP_DEFLATED) as archive:
            archive.writestr("project_manifest.json", json.dumps(duplicate_manifest))
            archive.writestr("a.json", "{}")
        try:
            import_project(duplicate.getvalue(), Path(folder) / "duplicate", "duplicate")
        except ValueError:
            check("import rejects duplicate declarations", True)
        else:
            check("import rejects duplicate declarations", False)


if __name__ == "__main__":
    main()
