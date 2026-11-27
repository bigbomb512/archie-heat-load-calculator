"""Replay P0 room tracing and dwelling window placement on an APFS job clone.

Dry run is the default. Later P0 questions depend on earlier answers, so its
call count describes the packets available now, not an unknowable final total.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import autonomous_tasks_service as tasks, ai_provider, drawing_tasks_service as drawing
from backend import house_windows_service
from scripts import eval_room_tracing, eval_window_placement

REVIEW_ROOT = Path(__file__).resolve().parents[1] / "output" / "web_review"
P0_TASKS = {"P0_dimensions", "P0_scale_read", "P0_scale_objects", "P0_wall_styles",
            "P0_room_names", "P0_tenancy_outline", "P0_room_outlines",
            "P0_unnamed_space", "P0_outline_corners"}
OTHER_BUILDERS = ("_north_packets", "_p0_size_packets", "_s1_packets", "_p3_packets",
                  "_p3_surroundings_packets", "_p7_packets", "_p4_packets",
                  "_p6_kitchen_packets", "_p8_packets")


def _inside(path, parent):
    return path == parent or parent in path.parents


def _snapshot(root):
    return {str(path.relative_to(root)): path.stat().st_mtime_ns for path in root.rglob("*") if path.is_file()}


class _CopyWeb:
    """Stand-in for the web app on a disposable copy: the job record is not saved anywhere, and links stay local."""

    @staticmethod
    def update_project(project, removed=()):
        return project

    @staticmethod
    def safe_link(path):
        return str(path)


def _safe_paths(job, out):
    review = REVIEW_ROOT.resolve()
    job, out = job.resolve(), out.expanduser().resolve()
    if job.parent != review or not job.is_dir():
        raise ValueError("--job must be one existing directory directly under output/web_review")
    if _inside(out, review):
        raise ValueError("--out must be outside output/web_review")
    if out.exists():
        raise ValueError(f"--out already exists: {out}")
    return job, out


def _clone(job, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", "-c", "-R", str(job), str(out)], check=True)
    # Existing job artifacts may hold absolute references back to the original.
    # Rewrite text references in the disposable copy before calling project code.
    for path in out.rglob("*.json"):
        if path.is_symlink():
            raise ValueError(f"Symlink in job copy: {path}")
        content = path.read_bytes()
        old = str(job).encode()
        if old in content:
            path.write_bytes(content.replace(old, str(out).encode()))
    for path in out.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink in job copy: {path}")


def _refresh_names(root):
    # A saved answer was based on the old room-name list. Its dependent sketch
    # answers must also be regenerated from the current page/text label reader.
    for task in ("P0_room_names", "P0_tenancy_outline", "P0_room_outlines",
                 "P0_unnamed_space", "P0_outline_corners"):
        shutil.rmtree(root / "ai_tasks" / task, ignore_errors=True)


def _unspaced(name):
    """'R O B E' (a text layer that letter-spaces a label) as 'Robe'; other names unchanged."""
    words = str(name).split()
    return "".join(words).title() if len(words) > 2 and all(len(word) <= 2 for word in words) else str(name)


def _merge_names(missing, known, printed):
    """One name per room: a printed label (whose text layer may read 'EN S U ITE' or 'KITCHEN') is asked under the name the
    room list already uses for it, and only when that room isn't already asked. Two spellings of one room in a question
    let the model answer for a name outside the list, which fails the whole page's reply."""
    key = eval_room_tracing._base
    missing = [_unspaced(name) for name in missing]
    known = [_unspaced(name) for name in known or []]
    canonical = {}
    for name in [*(known or []), *missing]:
        canonical.setdefault(key(name), name)
    asked = {key(name): name for name in missing}
    for label in sorted(printed):
        base = key(label)
        if base and base not in asked:
            asked[base] = canonical.get(base) or _unspaced(" ".join(str(label).replace("/", " ").split())).title()
    return sorted(asked.values())


def _build(web, project):
    # Keep the same run_all entry point used by page_preparation_service. Its
    # unrelated builders are scoped out; windows run once after live P0 settles.
    root = Path(project["review_dir"])
    # Printed labels outside the saved traces, or inside a mismatched/merged
    # trace, still need a sketch even if an area was inferred previously.
    coverage = eval_room_tracing.evaluate(root)
    ask = {}
    for page in coverage["pages"]:
        names = set(page["untraced_names"])
        for trace in page["traces"]:
            if trace["merged"] or trace["name_mismatch"] or trace["implausible_area"]:
                names.update(trace["printed_names"])
        ask[page["page"]] = names
    original_missing = tasks._p0_outline_room_names

    def missing_printed(root, names_record, area_count, requested_rooms=None, *, inferred_rooms=None, dwelling=None):
        names, missing = original_missing(root, names_record, area_count, requested_rooms,
                                          inferred_rooms=inferred_rooms, dwelling=dwelling)
        page = (names_record or {}).get("packet", {}).get("page")
        if page is None and inferred_rooms:
            page = next((row.get("page") for row in inferred_rooms if row.get("page")), None)
        # The flagged printed names join only a page's first outline question. Re-deriving them from the traces each
        # round made the asked list alternate as each answer changed the traces, so the page never settled.
        answered = tasks._current_task(root, "P0_room_outlines", f"page-{page}")
        extra = set() if answered and answered.get("status") not in {"waiting_for_reply", None} else ask.get(page, set())
        return names, _merge_names(missing, names, extra)

    with ExitStack() as stack:
        stack.enter_context(patch.object(tasks, "_p0_outline_room_names", side_effect=missing_printed))
        for name in OTHER_BUILDERS:
            stack.enter_context(patch.object(tasks, name, return_value=[]))
        stack.enter_context(patch.object(tasks, "_north_packets", return_value=[]))
        stack.enter_context(patch.object(tasks, "_refresh_site_tasks", return_value=None))
        stack.enter_context(patch.object(tasks, "_refresh_roof_tasks", return_value=None))
        stack.enter_context(patch.object(tasks, "_finish_north", return_value=None))
        stack.enter_context(patch.object(tasks, "_settle_unselected_pages", return_value=None))
        stack.enter_context(patch.object(tasks, "_add_main_plan_linework", return_value=[]))
        stack.enter_context(patch.object(house_windows_service, "apply", return_value=None))
        tasks.run_all(web, project)


def _refresh_room_use(web, project, root):
    """What the app does after drawing answers: export them, re-resolve each room's use, rebuild the evidence. Window
    placement only hosts windows on rooms resolved as cooled, so new traces without this get none (run 4, 26 Nov:
    29 of 32 traces had no room-use record and 0 windows were placed; 12 after this). No AI call."""
    from backend import calculation_extraction_service, room_use_resolution_service
    tasks._export_determinations(root)
    room_use_resolution_service.post(web, project, {"action": "resolve"})
    calculation_extraction_service.post(web, project, {"action": "build"})


def _waiting(root):
    return [row for row in drawing._waiting(root) if row.get("task") in P0_TASKS]


def _summary(root):
    rows = []
    for record in _waiting(root):
        packet = record.get("packet") or {}
        images = drawing._images(root, record)
        rows.append({"page": packet.get("page"), "task": record["task"],
                     "target": record["target"], "room_names": packet.get("room_names") or
                     ([packet["room"]] if packet.get("room") else []),
                     "images": len(images)})
    rows.sort(key=lambda row: (row["page"] or 0, row["task"], row["target"]))
    return {"packets": rows, "provider_calls_available_now": len(rows),
            "provider_calls_total": None,
            "call_count_note": "Later packets and validation retries depend on live replies; an exact total cannot be known in dry run."}


def _answer(web, project, max_calls, report):
    root = Path(project["review_dir"])
    tried = {}
    for _round in range(drawing.MAX_ROUNDS):
        _build(web, project)
        waiting = [row for row in _waiting(root) if tried.get(row["run_id"], 0) < drawing.MAX_ATTEMPTS]
        if not waiting:
            break
        for record in waiting:
            if report["calls_made"] >= max_calls:
                report["stopped_reason"] = f"Provider call cap of {max_calls} reached."
                return
            current = tasks._current_task(root, record["task"], record["target"])
            if not current or current["run_id"] != record["run_id"] or current["status"] != "waiting_for_reply":
                continue
            tried[record["run_id"]] = tried.get(record["run_id"], 0) + 1
            images = drawing._images(root, record)
            sizes = drawing._sizes(images)
            provider = ai_provider.recorded(ai_provider.get(f"drawing:{record['task']}"), root,
                                            f"drawing:{record['task']}")
            grid = drawing.uses_grid(record["task"], sizes, provider)
            prompt = drawing._prompt(record, grid)
            report["calls_made"] += 1
            try:
                reply, _raw = provider.propose(prompt, image_paths=images)
                if reply is not None and grid:
                    size = drawing._grid_size(record["task"], reply, sizes)
                    if size:
                        reply = drawing.grid_to_pixels(reply, size)
                tasks.post(web, project, {"action": "validate_apply", "task": record["task"],
                                          "target": record["target"], "reply": json.dumps(reply),
                                          "model_note": f"AI provider ({getattr(provider, 'model', '') or 'default'})",
                                          "defer": True})
            except Exception as error:
                report.setdefault("call_errors", []).append({"task": record["task"],
                    "target": record["target"], "reason": str(error)})
                if ai_provider.usage_limit_message(error):
                    report["stopped_reason"] = f"Provider usage limit: {error}"
                    return
    else:
        report["stopped_reason"] = f"P0 did not settle in {drawing.MAX_ROUNDS} rounds."


def _outcomes(root):
    result = []
    for row in tasks._all_current(root):
        if row.get("task") not in {"P0_room_outlines", "P0_outline_corners", "P0_unnamed_space"}:
            continue
        packet = row.get("packet") or {}
        reasons = {item.get("room"): item.get("reason") for item in
                   (row.get("applied_value") or {}).get("not_drawn", []) if isinstance(item, dict)}
        for room in packet.get("room_names") or ([packet["room"]] if packet.get("room") else []):
            status = row.get("status")
            outcome = ("rejected" if room in reasons or status in {"blocked", "failed"} else
                       "accepted" if status == "applied" else "needs_review")
            attempts = row.get("reply_attempts") or []
            reason = reasons.get(room) or row.get("block_reason") or (attempts[-1].get("reason") if attempts else "")
            if not reason and status == "below_accuracy_bar":
                reason = (row.get("applied_value") or {}).get("note") or "Below the automatic accuracy bar."
            result.append({"page": packet.get("page"), "room": room, "task": row["task"],
                           "outcome": outcome, "reason": reason})
    return result


def run(job, out, *, live=False, max_calls=60):
    job, out = _safe_paths(Path(job), Path(out))
    if max_calls < 0:
        raise ValueError("--max-calls must be nonnegative")
    before = _snapshot(job)
    report = {"mode": "live" if live else "dry_run", "calls_made": 0}
    try:
        _clone(job, out)
        project = {"id": out.name, "review_dir": str(out)}
        _refresh_names(out)
        web = _CopyWeb()
        _build(web, project)
        report["summary"] = _summary(out)
        if live:
            _answer(web, project, max_calls, report)
            if not report.get("stopped_reason"):
                _refresh_room_use(web, project, out)
                report["window_placement"] = house_windows_service.apply(web, project, out)
        report["room_outcomes"] = _outcomes(out)
        report["eval_room_tracing"] = eval_room_tracing.evaluate(out)
        report["eval_window_placement"] = eval_window_placement.evaluate(out)
        return report
    finally:
        if out.is_dir():
            (out / "retrace_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        after = _snapshot(job)
        if before != after:
            changed = sorted(set(before) ^ set(after) | {name for name in before.keys() & after.keys()
                        if before[name] != after[name]})
            raise RuntimeError(f"Source job changed during retrace: {changed[:20]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Build packets only (default)")
    mode.add_argument("--live", action="store_true", help="Call the configured provider on the copy")
    parser.add_argument("--max-calls", type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(run(args.job, args.out, live=args.live, max_calls=args.max_calls), indent=2, default=str))


if __name__ == "__main__":
    main()
