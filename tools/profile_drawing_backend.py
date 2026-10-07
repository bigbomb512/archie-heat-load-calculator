#!/usr/bin/env python3
"""Opt-in local CPU and timing profile for drawing-analysis backend operations.

Examples:
  PYTHONPATH=. python3 tools/profile_drawing_backend.py upload-analysis --pdf plan.pdf
  PYTHONPATH=. python3 tools/profile_drawing_backend.py run-all --project-id PROJECT_ID
  PYTHONPATH=. python3 tools/profile_drawing_backend.py p0-apply --project-id PROJECT_ID \
      --task P0_dimensions --target page-1-dimension-1 --reply-file reply.json

Saved projects are copied to a temporary review directory before task
operations, so profiling never applies a reply to the user's project. Copies
are removed after the run unless --keep-copy is given.
"""

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch
try:
    import resource
except ImportError:  # pragma: no cover - not available on Windows
    resource = None

ROOT = Path(__file__).resolve().parents[1]
COPY_MARKER = ".archie-profile-copy.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import autonomous_tasks_service, page_analysis_cache
from backend import web_app


class _IsolatedWeb:
    """Delegate app helpers while suppressing project-index writes."""

    def __getattr__(self, name):
        return getattr(web_app, name)

    @staticmethod
    def update_project(_project):
        return None


def _clone_project(project, temporary_root):
    cloned = dict(project)
    original_review = Path(project["review_dir"])
    destination = Path(temporary_root) / "review"
    shutil.copytree(original_review, destination)
    cloned["review_dir"] = str(destination)
    return cloned


def _write_copy_marker(profile_copy, project_id):
    marker = {"project_id": project_id, "created_at": datetime.now(timezone.utc).isoformat()}
    (Path(profile_copy) / COPY_MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def _copy_marker(project_copy, project_id=None, require_review=True):
    project_copy = Path(project_copy)
    try:
        marker = json.loads((project_copy / COPY_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (not isinstance(marker, dict) or not isinstance(marker.get("project_id"), str)
            or not marker.get("project_id") or not isinstance(marker.get("created_at"), str)
            or (require_review and not (project_copy / "review").is_dir())
            or (project_id is not None and marker["project_id"] != project_id)):
        return None
    return marker


def _remove_owned_copy(project_copy, project_id):
    if _copy_marker(project_copy, project_id=project_id, require_review=False) is None:
        raise ValueError(f"Refusing to remove an unmarked or unrelated profile copy: {project_copy}")
    shutil.rmtree(project_copy)


def _profile(operation, cache_root=None, code_fingerprint=None):
    cache_scope = (page_analysis_cache.operation(cache_root, code_fingerprint=code_fingerprint)
                   if cache_root else nullcontext(None))
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    child_start = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    with cache_scope as cache:
        result = operation()
        wall_seconds = time.perf_counter() - wall_start
        cpu_seconds = time.process_time() - cpu_start
        if resource and child_start:
            child_end = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_seconds += (child_end.ru_utime + child_end.ru_stime
                            - child_start.ru_utime - child_start.ru_stime)
        stats = cache.stats if cache is not None else {}
    return {"wall_seconds": round(wall_seconds, 4), "process_cpu_seconds": round(cpu_seconds, 4),
            "page_analysis_counts": stats, "result": result}


def _json_summary(value):
    if isinstance(value, dict):
        return {key: value[key] for key in ("id", "pages", "relevant", "kept_count", "thumbnail_count") if key in value}
    return value


def _upload_page_counts(review_dir):
    review_dir = Path(review_dir)
    try:
        packet = json.loads((review_dir / "packet.json").read_text(encoding="utf-8"))
        spatial = json.loads((review_dir / "spatial_ocr.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    structured = {row.get("page") for row in packet.get("structured_pages", []) if isinstance(row, dict)}
    spatial_pages = {row.get("page") for row in spatial.get("pages", []) if isinstance(row, dict)}
    rendered = {row.get("page") for row in packet.get("thumbnails", []) if isinstance(row, dict)}
    page_numbers = sorted(number for number in structured | spatial_pages | rendered if type(number) is int)
    return {str(number): {"structured_extract_passes": int(number in structured),
                          "spatial_analysis_passes": int(number in spatial_pages),
                          "thumbnail_renders": int(number in rendered)}
            for number in page_numbers}


def _run_upload_analysis(project, review_dir, isolated_web):
    from backend import room_inference_service
    result = web_app.analyse_project(project, review_dir=review_dir, app=isolated_web,
                                     persist_project=False)
    # Upload analysis queues local room inference. Include that local CPU work
    # in the profile and let its writes finish before the temporary workspace
    # is removed. Provider-backed work remains governed by normal opt-in.
    job_path = Path(review_dir) / "room_inference_job.json"
    deadline = time.monotonic() + 300
    while True:
        if not job_path.exists() and project["id"] not in room_inference_service._RUNNING:
            break
        try:
            status = json.loads(job_path.read_text(encoding="utf-8")).get("status")
        except (OSError, json.JSONDecodeError):
            status = "queued"
        if status not in {"queued", "running"} and project["id"] not in room_inference_service._RUNNING:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Local room inference did not finish within 300 seconds.")
        time.sleep(0.05)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="phase", required=True)
    upload = subparsers.add_parser("upload-analysis", help="Profile PDF upload analysis in a temporary review directory.")
    upload.add_argument("--pdf", required=True, type=Path)

    for phase in ("run-all", "p0-apply"):
        command = subparsers.add_parser(phase)
        command.add_argument("--project-id", required=True)
        command.add_argument("--cold-cache", action="store_true",
                             help="Use a fresh isolated project copy and an empty page-analysis cache.")
        command.add_argument("--keep-copy", action="store_true",
                             help="Keep the isolated copy after profiling and print its path and size.")
        command.add_argument("--copy-path", type=Path,
                             help="Reuse a copy previously printed by --keep-copy.")
        command.add_argument("--uncached-page-analysis", action="store_true",
                             help="Bypass the page cache for a baseline comparison (task phases only).")
        if phase == "p0-apply":
            command.add_argument("--task", required=True)
            command.add_argument("--target", required=True)
            command.add_argument("--reply-file", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.phase == "upload-analysis":
        source = args.pdf.resolve()
        if not source.is_file():
            parser.error(f"PDF not found: {source}")
        with tempfile.TemporaryDirectory(prefix="archie-profile-upload-", ignore_cleanup_errors=True) as temporary:
            review_dir = Path(temporary) / "review"
            project = {"id": "profile", "pdf": str(source), "review_dir": str(review_dir)}
            report = _profile(lambda: _run_upload_analysis(project, review_dir, _IsolatedWeb()), review_dir)
            report["upload_page_counts"] = _upload_page_counts(review_dir)
    else:
        try:
            project = web_app.project_by_id(args.project_id)
        except (ValueError, KeyError) as error:
            parser.error(str(error))
        original_root = Path(project.get("review_dir", ""))
        if not original_root.is_dir():
            parser.error("The selected project has no existing review directory.")
        copy_key = hashlib.sha256(f"{args.phase}:{args.project_id}".encode("utf-8")).hexdigest()[:20]
        default_copy = Path(tempfile.gettempdir()) / f"archie-profile-copy-{copy_key}"
        if args.copy_path and args.cold_cache:
            parser.error("--copy-path cannot be combined with --cold-cache; cold-cache always creates a fresh copy.")
        if args.copy_path:
            profile_copy = args.copy_path.expanduser().resolve()
            if _copy_marker(profile_copy, project_id=args.project_id) is None:
                parser.error(f"--copy-path must be a marked profile copy for project {args.project_id} with a review/ folder: {profile_copy}")
        elif args.cold_cache and args.keep_copy:
            profile_copy = default_copy
        elif args.keep_copy:
            profile_copy = default_copy
        else:
            profile_copy = None
        if (args.keep_copy and not args.copy_path and profile_copy.exists()
                and _copy_marker(profile_copy, project_id=args.project_id) is None):
            # Never write into a coincidentally named, unmarked directory.
            profile_copy = Path(tempfile.gettempdir()) / f"archie-profile-copy-{copy_key}-{uuid.uuid4().hex[:8]}"

        temporary_copy = None
        if profile_copy is None:
            temporary_copy = tempfile.TemporaryDirectory(prefix="archie-profile-copy-", ignore_cleanup_errors=True)
            profile_copy = Path(temporary_copy.name)
            _write_copy_marker(profile_copy, args.project_id)
        try:
            review_copy = profile_copy / "review"
            if args.cold_cache or not review_copy.is_dir():
                if profile_copy.exists():
                    if _copy_marker(profile_copy, project_id=args.project_id, require_review=False) is None:
                        if args.copy_path:
                            parser.error(f"Refusing to replace the --copy-path directory: {profile_copy}")
                        # A deterministic keep-copy path may already belong to
                        # the user. Leave it untouched and create elsewhere.
                        if args.keep_copy:
                            profile_copy = Path(tempfile.gettempdir()) / f"archie-profile-copy-{copy_key}-{uuid.uuid4().hex[:8]}"
                        else:
                            parser.error(f"Refusing to replace an unmarked profile copy: {profile_copy}")
                    else:
                        _remove_owned_copy(profile_copy, args.project_id)
                profile_copy.mkdir(parents=True, exist_ok=True)
                _write_copy_marker(profile_copy, args.project_id)
                clone = _clone_project(project, profile_copy)
            else:
                clone = dict(project)
                clone["review_dir"] = str(review_copy)
            isolated_web = _IsolatedWeb()
            code_fingerprint = autonomous_tasks_service._page_analysis_code_fingerprint()
            if args.phase == "run-all":
                action = lambda: autonomous_tasks_service.run_all(isolated_web, clone)
            else:
                if not args.reply_file.is_file():
                    parser.error(f"Reply file not found: {args.reply_file}")
                reply = args.reply_file.read_text(encoding="utf-8")
                data = {"action": "validate_apply", "task": args.task, "target": args.target, "reply": reply}
                action = lambda: autonomous_tasks_service.post(isolated_web, clone, data)
            if args.uncached_page_analysis:
                with patch.object(page_analysis_cache, "get_context",
                                  side_effect=lambda _root, _page, builder, **_: builder()):
                    report = _profile(action, clone["review_dir"], code_fingerprint=code_fingerprint)
            else:
                report = _profile(action, clone["review_dir"], code_fingerprint=code_fingerprint)
            if args.keep_copy or args.copy_path:
                size_bytes = sum(path.stat().st_size for path in profile_copy.rglob("*") if path.is_file())
                report["profile_copy"] = {"path": str(profile_copy), "size_bytes": size_bytes}
        finally:
            if temporary_copy is not None:
                temporary_copy.cleanup()

    report["result"] = _json_summary(report.get("result"))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
