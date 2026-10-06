#!/usr/bin/env python3
"""Score autonomous AI task results (Card P) against answer keys.

Usage:
    PYTHONPATH=. python3 tools/evaluate_autonomous_tasks.py \
        evaluations/autonomous/caseA.json=/path/to/caseA_determinations.json [more pairs]

Each determinations file holds the values one AI run applied for that case.
Reports go to the ignored output/evaluations/ folder.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path

from ai.autonomous_task_scoring import AUTO_APPLY_BAR, score_case, summarise


ROOT = Path(__file__).resolve().parents[1]
ACCURACY_PATH = ROOT / "evaluations" / "autonomous" / "accuracy.json"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_markdown(case_reports, summary, bar):
    lines = ["# Autonomous AI task scorecard", "", f"Auto-apply bar: {bar:.0%} correct per task.", "",
             "| Task | Correct | Wrong | Missing | Accuracy | From fallback | Auto-apply |", "|---|---|---|---|---|---|---|"]
    if any(report.get("not_applicable", {}).get("P5_roof") for report in case_reports):
        lines += ["", "P5 coverage excludes cases where every answer-key room was answered by the contractor/reviewer; those cases are not applicable to AI coverage."]
    for task, row in summary.items():
        note = " (small sample)" if row["small_sample"] else ""
        lines.append(f"| {task} | {row['correct']} | {row['wrong']} | {row['missing']} | {row['accuracy']:.0%}{note} | "
                     f"{row['from_fallback']} | {'yes' if row['auto_apply'] else 'no'} |")
    for report in case_reports:
        lines += ["", f"## {report['case_id']}"]
        for task, rooms in report.get("not_ai_determined", {}).items():
            lines.append(f"- {task} skipped for contractor/reviewer answer(s): {', '.join(rooms)} (not AI-determined).")
        for task, rooms in report.get("not_applicable", {}).items():
            lines.append(f"- {task} not applicable for AI coverage: all answer-key rooms have contractor/reviewer answers ({', '.join(rooms)}).")
        for task, items in report["tasks"].items():
            for item in items:
                lines.append(f"- {task} · {item['item']}: **{item['status']}**{' (fallback)' if item['fallback'] else ''} — {item['detail']}")
    return "\n".join(lines) + "\n"


def _contains_stand_in(value):
    if isinstance(value, dict):
        return value.get("stand_in") is True or any(_contains_stand_in(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_stand_in(item) for item in value)
    return False


def _answer_key_paths():
    result = {}
    for path in sorted((ROOT / "evaluations" / "autonomous").glob("case*.json")):
        key = _read(path)
        case_id = key.get("case_id", path.stem)
        if case_id in result:
            raise ValueError(f"Duplicate canonical answer-key case ID: {case_id}.")
        result[case_id] = path.resolve()
    return result


def _recordable_accuracy(reports, summary, bar):
    """Return the trusted accuracy registry, or raise if this isn't a full real evaluation."""
    expected = {}
    for path in sorted((ROOT / "evaluations" / "autonomous").glob("case*.json")):
        key = _read(path)
        for task in key.get("tasks", {}):
            if task == "P5_roof" and any(report.get("case_id") == key.get("case_id", path.stem)
                                         and report.get("not_applicable", {}).get("P5_roof")
                                         for report in reports):
                continue
            expected.setdefault(task, set()).add(key.get("case_id", path.stem))

    scored_cases = {}
    for report in reports:
        for task, items in report.get("tasks", {}).items():
            if items:
                scored_cases.setdefault(task, set()).add(report.get("case_id", ""))

    rows = {}
    for task, case_ids in expected.items():
        seen = scored_cases.get(task, set())
        missing = sorted(case_ids - seen)
        summary_row = summary.get(task, {})
        scored = int(summary_row.get("scored", 0))
        if missing:
            raise ValueError(f"Cannot record {task}: answer-key case(s) not scored: {', '.join(missing)}.")
        if scored < 10:
            raise ValueError(f"Cannot record {task}: requires at least 10 scored items; found {scored}.")
        correct = int(summary_row.get("correct", 0))
        accuracy = correct / scored
        rows[task] = {"accuracy": accuracy, "scored": scored, "correct": correct,
                      "wrong": int(summary_row.get("wrong", 0)),
                      "missing": int(summary_row.get("missing", 0)),
                      "auto_apply": accuracy >= bar, "case_ids": sorted(case_ids)}
    return {"schema_version": 1, "recorded_at": datetime.now().astimezone().isoformat(),
            "bar": bar, "tasks": rows}


def evaluate_case_excluding_contractor_answers(key, determinations, private=None):
    """Score P5 only when its applied roof values were AI-determined."""
    scoring_key = key
    ai_determinations = determinations
    excluded_rooms = []
    all_contractor_answered = False
    if key.get("tasks", {}).get("P5_roof") and isinstance(determinations.get("P5_roof"), list):
        contractor_rows = [row for row in determinations["P5_roof"]
                           if isinstance(row, dict) and str(row.get("source", "")).casefold() in {"reviewer", "contractor"}]
        if contractor_rows:
            contractor_rooms = {" ".join(str(row.get("room", "")).casefold().split()) for row in contractor_rows}
            ai_roofs = [row for row in determinations["P5_roof"] if row not in contractor_rows]
            ai_determinations = {**determinations, "P5_roof": ai_roofs}
            roof_key = key["tasks"]["P5_roof"]
            expected_rooms = roof_key.get("rooms", [])
            excluded_rooms = [room.get("room", "unknown room") for room in contractor_rows]
            remaining_rooms = [room for room in expected_rooms
                               if " ".join(str(room.get("room", "")).casefold().split()) not in contractor_rooms]
            all_contractor_answered = bool(expected_rooms) and not remaining_rooms
            if all_contractor_answered:
                scoring_key = {**key, "tasks": {task: task_key for task, task_key in key.get("tasks", {}).items()
                                                 if task != "P5_roof"}}
            else:
                scoring_key = {**key, "tasks": {**key["tasks"], "P5_roof": {**roof_key, "rooms": remaining_rooms}}}
    report = score_case(scoring_key, ai_determinations, private)
    if excluded_rooms:
        report["not_ai_determined"] = {"P5_roof": excluded_rooms}
    if all_contractor_answered:
        report["not_applicable"] = {"P5_roof": excluded_rooms}
    return report


def main():
    parser = argparse.ArgumentParser(description="Score autonomous AI task results against answer keys.")
    parser.add_argument("pairs", nargs="+", help="answer_key.json=determinations.json")
    parser.add_argument("--bar", type=float, default=AUTO_APPLY_BAR)
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    parser.add_argument("--record", action="store_true",
                        help="record auto-apply accuracy only after full answer-key coverage, real determinations, and >=10 items per task")
    args = parser.parse_args()
    reports = []
    case_ids = set()
    canonical_keys = _answer_key_paths() if args.record else {}
    for pair in args.pairs:
        key_path, _, result_path = pair.partition("=")
        key = _read(key_path)
        if not key_path or not result_path:
            parser.error("Each input must be answer_key.json=determinations.json")
        if key.get("case_id") in case_ids:
            parser.error(f"Duplicate answer-key case: {key.get('case_id')}")
        case_ids.add(key.get("case_id"))
        determinations = _read(result_path)
        if args.record and _contains_stand_in(determinations):
            parser.error(f"Refusing --record: determinations for {key.get('case_id')} are marked stand_in.")
        if args.record and Path(key_path).resolve() != canonical_keys.get(key.get("case_id")):
            parser.error(f"Refusing --record: {key.get('case_id')} must use its checked-in canonical answer key.")
        private_path = ROOT / key["private_facts_file"] if key.get("private_facts_file") else None
        private = _read(private_path) if private_path and private_path.exists() else None
        reports.append(evaluate_case_excluding_contractor_answers(key, determinations, private))
    summary = summarise(reports, args.bar)
    if args.record:
        try:
            accuracy = _recordable_accuracy(reports, summary, args.bar)
        except ValueError as error:
            parser.error(str(error))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = output_dir / f"autonomous-tasks-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps({"bar": args.bar, "summary": summary, "cases": reports}, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(reports, summary, args.bar), encoding="utf-8")
    for task, row in summary.items():
        print(f"{task}: {row['accuracy']:.0%} ({row['correct']}/{row['scored']}) · auto-apply {'yes' if row['auto_apply'] else 'no'}")
    print(f"Report: {base.with_suffix('.md')}")
    if args.record:
        ACCURACY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCURACY_PATH.write_text(json.dumps(accuracy, indent=2) + "\n", encoding="utf-8")
        print(f"Recorded auto-apply accuracy: {ACCURACY_PATH}")


if __name__ == "__main__":
    main()
