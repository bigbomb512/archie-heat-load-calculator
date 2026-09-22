"""Stage 12 product artifacts: audit history, health, packages and archives.

This module deliberately does not import the calculation or PDF-extraction
engines.  It consumes their persisted artifacts and fingerprints, so product
features cannot change calculation behaviour.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import io
import json
from pathlib import Path, PurePosixPath
import re
import time
from zipfile import ZIP_DEFLATED, ZipFile


SCHEMA_VERSION = 1
PACKAGE_VERSION = "stage12-package-v1"
ARCHIVE_VERSION = "stage12-archive-v1"
AUDIT_VERSION = "stage12-audit-v1"
_SAFE_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_SUMMARY_CACHE = {}
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_exception_id(source_fingerprint, *, scope="project", affected_id="project", category="project", field=""):
    """Return an order-independent identifier for one review exception."""
    key = {
        "source_fingerprint": str(source_fingerprint or ""),
        "scope": str(scope or "project"),
        "affected_id": str(affected_id or "project"),
        "category": str(category or "project"),
        "field": str(field or ""),
    }
    return "exc_" + fingerprint(key)[:20]


def normalise_exception(item, *, source_fingerprint="", default_category="project"):
    """Convert an input/readiness issue into the shared exception contract."""
    item = dict(item or {})
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    affected_id = item.get("affected_id") or item.get("room_id") or item.get("zone_id") or item.get("floor_id") or "project"
    status = str(item.get("status") or item.get("readiness_status") or "blocked")
    severity = str(item.get("severity") or ("blocking" if status in {"blocked", "conflict", "stale"} else "warning"))
    category = str(item.get("category") or item.get("kind") or default_category)
    field = str(item.get("field") or item.get("input_id") or item.get("target") or item.get("artifact") or "")
    return {
        "exception_id": item.get("exception_id") or stable_exception_id(source_fingerprint, scope=item.get("scope", "project"), affected_id=affected_id, category=category, field=field),
        "code": item.get("code") or category,
        "scope": item.get("scope", "project"),
        "affected_id": affected_id,
        "category": category,
        "field": field,
        "severity": severity,
        "status": status,
        "source_artifact": item.get("source_artifact") or item.get("artifact") or "",
        "artifact": item.get("artifact") or item.get("source_artifact") or "",
        "source_page": item.get("source_page") or source.get("page") or item.get("page"),
        "source_drawing": item.get("source_drawing") or source.get("drawing_number") or item.get("drawing_number") or "",
        "excerpt": item.get("excerpt") or source.get("excerpt") or "",
        "crop_url": item.get("crop_url") or item.get("source_crop") or "",
        "current_value": item.get("current_value", item.get("value")),
        "competing_values": item.get("competing_values") or item.get("conflicts") or [],
        "reason": item.get("reason") or item.get("message") or "Review this unresolved input.",
        "message": item.get("message") or item.get("reason") or "Review this unresolved input.",
        "remediation": item.get("remediation") or "Resolve the cited input before continuing.",
        "reviewer_decision": item.get("reviewer_decision") or item.get("decision") or "pending",
        "decision_history": item.get("decision_history") or [],
        "retryable": bool(item.get("retryable", False)),
        "changed_sources": item.get("changed_sources") or [],
    }


def normalise_exceptions(items, *, source_fingerprint="", default_category="project", limit=None):
    rows = [normalise_exception(item, source_fingerprint=source_fingerprint, default_category=default_category) for item in (items or [])]
    rows.sort(key=lambda row: (0 if row["severity"] == "blocking" else 1, row["category"], row["affected_id"], row["exception_id"]))
    if limit is not None:
        rows = rows[:max(0, int(limit))]
    return rows


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json(path, default=None):
    path = Path(path)
    if not path.exists():
        return deepcopy(default if default is not None else {})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return deepcopy(default if default is not None else {})


def _artifact_path(root, value):
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def append_audit_event(root, *, action, target, actor="local_user", result="success", previous_fingerprint="", new_fingerprint="", related_fingerprint="", affected_ids=None, error_code="", remediation=""):
    """Append one tamper-evident event without copying raw artifact contents."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "audit_log.jsonl"
    previous_hash = ""
    if path.exists():
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            if line.strip():
                try:
                    previous_hash = json.loads(line).get("event_hash", "")
                except ValueError:
                    previous_hash = ""
                break
    sequence = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) + 1 if path.exists() else 1
    event = {
        "schema_version": AUDIT_VERSION,
        "event_id": "evt_" + hashlib.sha256(f"{time.time_ns()}:{action}:{target}".encode()).hexdigest()[:16],
        "timestamp": _now(),
        "actor": str(actor or "local_user"),
        "action": str(action),
        "target": str(target),
        "previous_fingerprint": str(previous_fingerprint or ""),
        "new_fingerprint": str(new_fingerprint or ""),
        "related_fingerprint": str(related_fingerprint or ""),
        "affected_ids": sorted({str(item) for item in (affected_ids or []) if item}),
        "result": str(result),
        "error_code": str(error_code or ""),
        "remediation": str(remediation or ""),
        "previous_event_hash": previous_hash,
        "sequence": sequence,
    }
    event["event_hash"] = fingerprint(event)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
    (root / "audit_log_head.json").write_text(json.dumps({"event_count": sequence, "event_hash": event["event_hash"]}, indent=2), encoding="utf-8")
    return event


def read_audit_events(root, *, action="", target="", affected_id="", limit=200):
    path = Path(root) / "audit_log.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if action and event.get("action") != action:
            continue
        if target and event.get("target") != target:
            continue
        if affected_id and affected_id not in event.get("affected_ids", []):
            continue
        events.append(event)
    return events[-max(1, min(int(limit), 1000)):]


def validate_audit_chain(root):
    previous = ""
    for event in read_audit_events(root, limit=100000):
        supplied = event.get("event_hash", "")
        canonical = dict(event)
        canonical.pop("event_hash", None)
        if event.get("previous_event_hash", "") != previous or supplied != fingerprint(canonical):
            return False
        previous = supplied
    head_path = Path(root) / "audit_log_head.json"
    head = _json(head_path, {}) if head_path.exists() else None
    if head is not None and (head.get("event_count") != len(read_audit_events(root, limit=100000)) or head.get("event_hash", "") != previous):
        return False
    return True


def _pdf_escape(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _minimal_pdf(lines):
    """Create a dependency-free, readable PDF from the same package summary."""
    lines = [str(line)[:110] for line in lines][:56]
    commands = ["BT", "/F1 10 Tf", "45 750 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -13 Td")
        commands.append(f"({_pdf_escape(line)}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    output.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _report_summary(report, report_type):
    scenarios = report.get("scenario_results", [])
    readiness = report.get("readiness", {})
    scope = report.get("scope_summary", {})
    peak = report.get("project_peak") or report.get("included_scope_peak") or {}
    return {
        "report_type": report_type,
        "status": report.get("status", "unknown"),
        "readiness_status": readiness.get("status", report.get("status", "unknown")),
        "scenario_count": len(scenarios),
        "scenario_results": scenarios,
        "active_room_ids": scope.get("active_room_ids", []),
        "included_room_ids": scope.get("included_room_ids", []),
        "blocked_rooms": scope.get("blocked_rooms", []),
        "complete_scope": bool(scope.get("complete_scope")),
        "peak": peak,
        "blocked_reasons": report.get("blocked_reasons", []),
        "excluded_components": report.get("excluded_components", report.get("known_exclusions", [])),
        "readiness_issues": readiness.get("issues", []),
        "input_fingerprints": report.get("input_fingerprints", {}),
        "input_artifacts": report.get("input_artifacts", {}),
        "method_gate_statuses": report.get("advanced_envelope_methods", {}),
        "citations": report.get("citations", report.get("source_register", [])),
    }


def cached_report_summary(report, report_type):
    """Cache only derived display data; calculation artifacts remain authoritative."""
    key = (report_type, fingerprint(report))
    if key not in _SUMMARY_CACHE:
        _SUMMARY_CACHE[key] = _report_summary(deepcopy(report), report_type)
    return deepcopy(_SUMMARY_CACHE[key])


def _package_html(summary, report, artifact_rows):
    title = "Archie " + summary["report_type"].replace("_", " ").title()
    rows = "".join(f"<tr><th>{html.escape(str(key))}</th><td><pre>{html.escape(json.dumps(value, indent=2, ensure_ascii=False))}</pre></td></tr>" for key, value in summary.items())
    issues = "".join(f"<li>{html.escape(str(item.get('reason', item) if isinstance(item, dict) else item))}</li>" for item in summary["readiness_issues"] + summary["blocked_reasons"])
    artifacts = "".join(f"<li>{html.escape(row['path'])} — {row['sha256']}</li>" for row in artifact_rows)
    return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>{html.escape(title)}</title><style>body{{font:14px system-ui,sans-serif;max-width:1000px;margin:32px auto;color:#17242b}}h1{{margin-bottom:4px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd6da;padding:8px;text-align:left;vertical-align:top}}th{{width:220px;background:#f2f6f7}}pre{{white-space:pre-wrap;margin:0}}.warning{{background:#fff4d6;padding:12px;border-left:4px solid #c98c00}}small{{color:#52636b}}</style></head><body><h1>{html.escape(title)}</h1><small>Generated by {PACKAGE_VERSION}. This package preserves cited project results; it is not CAMEL+ validation.</small><div class=\"warning\"><b>Scope:</b> {html.escape('Complete project scope' if summary['complete_scope'] else 'Included-scope subtotal only')}</div><h2>Summary</h2><table>{rows}</table><h2>Issues and exclusions</h2><ul>{issues or '<li>None recorded</li>'}</ul><h2>Artifact fingerprints</h2><ul>{artifacts}</ul><h2>Audit data</h2><p>Report fingerprint: <code>{html.escape(str(report.get('_report_fingerprint', '')))}</code><br>Input snapshot: <code>{html.escape(str(report.get('input_fingerprints', {}).get('calculator_input_set_fingerprint', '')))}</code></p></body></html>"""


def build_report_package(project, paths, report_type, report, *, current=True, stale_reasons=None, artifact_paths=None):
    root = Path(project["review_dir"])
    stale_reasons = list(stale_reasons or [])
    report_copy = deepcopy(report)
    report_copy.pop("_report_fingerprint", None)
    report_hash = fingerprint(report_copy)
    artifact_paths = artifact_paths or []
    rows = []
    for path in artifact_paths:
        path = Path(path)
        if not path.exists() or not path.is_file():
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        rows.append({"path": relative, "sha256": file_hash(path), "size_bytes": path.stat().st_size})
    summary = cached_report_summary(report, report_type)
    if stale_reasons:
        summary["stale_reasons"] = stale_reasons
    snapshot_fingerprint = report.get("input_fingerprints", {}).get("calculator_input_set_fingerprint", "")
    package_fingerprint = fingerprint({"version": PACKAGE_VERSION, "report_type": report_type, "report": report_hash, "snapshot": snapshot_fingerprint, "artifacts": rows})
    package_root = root / "report_packages" / package_fingerprint
    package_root.mkdir(parents=True, exist_ok=True)
    report_copy["_report_fingerprint"] = report_hash
    html_body = _package_html(summary, report_copy, rows)
    html_path = package_root / "report.html"
    pdf_path = package_root / "report.pdf"
    manifest_path = package_root / "manifest.json"
    if not html_path.exists():
        html_path.write_text(html_body, encoding="utf-8")
    if not pdf_path.exists():
        pdf_lines = ["Archie " + report_type, "Dependency-free fallback PDF; HTML is canonical.", f"Status: {summary['status']}", f"Complete scope: {summary['complete_scope']}", f"Report fingerprint: {report_hash}", f"Input snapshot: {snapshot_fingerprint}"]
        for scenario in summary.get("scenario_results", []):
            pdf_lines.append(f"Scenario: {scenario.get('title', scenario.get('scenario_id', 'unknown'))} · {scenario.get('status', 'unknown')}")
            for room in scenario.get("rooms", []):
                peak = room.get("peak", {})
                pdf_lines.append(f"Room {room.get('room_id', 'unknown')}: {room.get('status', 'unknown')} · {peak.get('design_total_kw', peak.get('peak_kw', '—'))} kW")
        pdf_lines.extend(f"Issue: {item.get('reason', item)}" for item in summary["readiness_issues"])
        pdf_path.write_bytes(_minimal_pdf(pdf_lines))
    artifact_root = package_root / "artifacts"
    source_by_relative = {}
    for source in artifact_paths:
        source = Path(source)
        try:
            source_by_relative[source.relative_to(root).as_posix()] = source
        except ValueError:
            continue
    for row in rows:
        source = source_by_relative.get(row["path"])
        if source is None or not source.exists() or not source.is_file():
            continue
        target = artifact_root / row["path"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    manifest = {
        "schema_version": SCHEMA_VERSION, "package_version": PACKAGE_VERSION,
        "package_fingerprint": package_fingerprint, "report_type": report_type,
        "report_fingerprint": report_hash, "calculator_input_set_fingerprint": snapshot_fingerprint,
        "source_artifacts": rows, "generated_at": _now(), "current_report": bool(current and not stale_reasons),
        "stale_reasons": stale_reasons, "renderer": "minimal-pdf-fallback-v1", "pdf_sha256": file_hash(pdf_path),
        "files": ["manifest.json", "report.html", "report.pdf"] + [f"artifacts/{row['path']}" for row in rows],
    }
    if manifest_path.exists():
        manifest = _json(manifest_path, manifest)
    else:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"package_fingerprint": package_fingerprint, "manifest": manifest, "artifact_url": str(package_root), "html_path": str(html_path), "pdf_path": str(pdf_path)}


def _archive_candidates(root):
    root = Path(root)
    allowed_suffixes = {".json", ".jsonl", ".md", ".html"}
    excluded_parts = {"uploads", "images", "screenshots", "thumbnails", "overlays", "manual_vision_handoff"}
    paths = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        if any(part in excluded_parts for part in path.relative_to(root).parts):
            continue
        paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def export_project(project):
    root = Path(project["review_dir"])
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": file_hash(path), "size_bytes": path.stat().st_size} for path in _archive_candidates(root)]
    artifact_names = {row["path"] for row in rows}
    manifest = {
        "schema_version": SCHEMA_VERSION, "archive_version": ARCHIVE_VERSION, "project_id": project["id"],
        "project_name": project.get("name", ""), "created_at": _now(), "source_pdf": project.get("pdf", ""),
        "source_references": [project.get("pdf", "")] if project.get("pdf") else [],
        "source_pdf_included": False, "excluded_sources": ["private_pdf", "rendered_page_images", "thumbnails", "overlays", "large_ai_packets"],
        "artifacts": rows, "historical_reports": sorted(name for name in artifact_names if name.endswith("_report.json")),
        "historical_snapshots": sorted(name for name in artifact_names if name.startswith("calculator_input_sets/")),
        "historical_packages": sorted(name for name in artifact_names if name.startswith("report_packages/")),
        "current_snapshot": _json(root / "calculator_input_set.json", {}),
        "compatibility": "archie-stage12", "note": "Private PDFs and rendered page images are intentionally excluded.",
    }
    archive_hash = fingerprint({"manifest": manifest, "artifacts": rows})
    output = root / f"project_export_{archive_hash}.zip"
    if not output.exists():
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.writestr("project_manifest.json", json.dumps({**manifest, "archive_fingerprint": archive_hash}, indent=2))
            for row in rows:
                archive.write(root / row["path"], row["path"])
    return {"archive_fingerprint": archive_hash, "archive_path": str(output), "manifest": {**manifest, "archive_fingerprint": archive_hash}}


def import_project(archive_bytes, destination_root, new_project_id):
    destination_root = Path(destination_root)
    destination_root.mkdir(parents=True, exist_ok=True)
    if not isinstance(archive_bytes, (bytes, bytearray)) or len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ValueError("Project archive exceeds the permitted compressed size.")
    if not isinstance(new_project_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", new_project_id):
        raise ValueError("Destination project identifier is invalid.")
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = archive.namelist()
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Project archive contains too many files.")
        expanded = sum(info.file_size for info in infos)
        if expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("Project archive exceeds the permitted expanded size.")
        for info in infos:
            is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
            ratio = info.file_size / max(1, info.compress_size)
            if is_symlink or ratio > MAX_COMPRESSION_RATIO:
                raise ValueError("Project archive contains an unsafe compressed member.")
        if any(not name or name.startswith("/") or ".." in PurePosixPath(name).parts or not _SAFE_MEMBER.fullmatch(name) for name in names):
            raise ValueError("Project archive contains an unsafe path.")
        if "project_manifest.json" not in names:
            raise ValueError("Project archive is missing project_manifest.json.")
        manifest = json.loads(archive.read("project_manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("archive_version") != ARCHIVE_VERSION:
            raise ValueError("Project archive has an unsupported manifest version.")
        rows = {}
        declared_rows = manifest.get("artifacts", [])
        for row in declared_rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise ValueError("Project archive contains an invalid artifact declaration.")
            artifact_name = row["path"]
            if artifact_name == "project_manifest.json" or not _SAFE_MEMBER.fullmatch(artifact_name) or ".." in PurePosixPath(artifact_name).parts:
                raise ValueError("Project archive contains an unsafe artifact declaration.")
            if artifact_name in rows:
                raise ValueError(f"Project archive declares duplicate artifact {artifact_name}.")
            rows[artifact_name] = row
        allowed_names = {"project_manifest.json", *rows}
        if set(names) != allowed_names:
            raise ValueError("Project archive contains undeclared files.")
        for name, row in rows.items():
            if name not in names:
                raise ValueError(f"Project archive is missing declared artifact {name}.")
            if hashlib.sha256(archive.read(name)).hexdigest() != row.get("sha256"):
                raise ValueError(f"Project archive hash mismatch for {name}.")
        root = (destination_root / new_project_id).resolve()
        try:
            root.relative_to(destination_root.resolve())
        except ValueError as error:
            raise ValueError("Destination project is outside the permitted storage root.") from error
        if root.exists():
            raise ValueError("The destination project already exists.")
        root.mkdir(parents=True)
        (root / "project_manifest.json").write_bytes(archive.read("project_manifest.json"))
        for name in rows:
            output = root / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(name))
    source_available = bool(manifest.get("source_pdf_included"))
    return {
        "project_id": new_project_id,
        "project_name": manifest.get("project_name", "Imported project"),
        "review_dir": str(root),
        "source_pdf": manifest.get("source_pdf", ""),
        "source_available": source_available,
        "imported_artifacts": sorted(rows),
        "skipped_artifacts": [],
        "invalid_artifacts": [],
        "unavailable_sources": [] if source_available else [manifest.get("source_pdf", "")] if manifest.get("source_pdf") else [],
        "archive_manifest": manifest,
    }


def project_health(project, paths, *, stale_report_names=None):
    root = Path(project.get("review_dir", "")) if project.get("review_dir") else None
    if not root or not root.exists():
        issues = [{"code": "project_not_analysed", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "message": "Analyse a drawing set before using calculation artifacts.", "remediation": "Upload and analyse the architect PDF."}]
        return {"status": "blocked", "issues": issues, "normalized_exceptions": normalise_exceptions(issues, source_fingerprint="project_not_analysed"), "recovery_actions": ["analyse_pdf"]}
    required = {"design_requirements": paths.get("requirements"), "schedule_library": paths.get("schedules"), "design_day_scenarios": paths.get("scenarios"), "hourly_load_model": paths.get("model")}
    issues = [{"code": "missing_artifact", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": name, "message": f"{name} is missing.", "remediation": "Create or save the artifact before calculating."} for name, path in required.items() if not path or not Path(path).exists()]
    for name, path in paths.items():
        if not path or not Path(path).is_file() or Path(path).suffix.lower() != ".json":
            continue
        try:
            json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            issues.append({"code": "artifact_invalid", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": name, "message": f"{name} is not valid JSON.", "remediation": "Restore or rewrite the artifact before calculating."})
    if (root / ".calculator-draft-transaction.json").exists():
        issues.append({"code": "interrupted_transaction", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": ".calculator-draft-transaction.json", "message": "An interrupted calculator-draft transaction was found.", "remediation": "Recover the staged transaction before editing further."})
    source_pdf = _json(root / "ai_input.json", {}).get("source_pdf", project.get("pdf", ""))
    if source_pdf and not Path(source_pdf).exists():
        issues.append({"code": "source_unavailable", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": "source_pdf", "message": "The private source PDF is not available at its recorded path.", "remediation": "Restore the source file or reanalyse the project."})
    snapshots = paths.get("calculator_input_set")
    if snapshots and Path(snapshots).exists():
        pointer = _json(snapshots, {})
        if not pointer.get("input_fingerprint"):
            issues.append({"code": "snapshot_invalid", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": "calculator_input_set.json", "message": "The current snapshot pointer is incomplete.", "remediation": "Assemble calculator inputs again."})
    for name, path in paths.items():
        if "method_gate" in name and path and Path(path).is_file():
            gate = _json(path, {})
            if str(gate.get("approval_status", gate.get("status", "placeholder"))).lower() != "approved":
                issues.append({"code": "method_gate_inactive", "severity": "warning", "scope": "project", "affected_id": project.get("id", "project"), "artifact": name, "message": f"{name} is not approved.", "remediation": "Approve the method gate or keep the method excluded from complete-scope results."})
    for name in stale_report_names or []:
        issues.append({"code": "report_stale", "severity": "blocking", "scope": "project", "affected_id": project.get("id", "project"), "artifact": name, "message": f"{name} is stale.", "remediation": "Refresh the changed inputs and recalculate."})
    status = "blocked" if any(item["severity"] == "blocking" for item in issues) else ("attention_required" if issues else "healthy")
    source_fingerprint = fingerprint({"project": project.get("id"), "issues": issues})
    return {"status": status, "issues": issues, "normalized_exceptions": normalise_exceptions(issues, source_fingerprint=source_fingerprint), "recovery_actions": sorted({item["remediation"] for item in issues})}
