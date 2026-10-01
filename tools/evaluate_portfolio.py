#!/usr/bin/env python3

"""Create a pseudonymous, read-only scorecard across saved project folders."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ai.pipeline_evaluation import compare_scorecards, evaluate_project_pipeline, render_portfolio_markdown

ROOT = Path(__file__).resolve().parents[1]


def _project_id(project):
    # Hash only the final folder name so IDs are reproducible without leaking
    # project names or local absolute paths into reports.
    token = hashlib.sha256(("archie-pipeline-scorecard-v1:" + project.name).encode("utf-8")).hexdigest()[:12]
    return f"project-{token}"


def _case_map(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("--case must be PROJECT_ID=/path/to/case.json")
        project_id, raw_path = value.split("=", 1)
        if not project_id or not raw_path or project_id in result:
            raise argparse.ArgumentTypeError("case mapping needs a unique project ID and file path")
        path = Path(raw_path)
        try:
            result[project_id] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise argparse.ArgumentTypeError(f"cannot read case for {project_id}: {exc}") from exc
    return result


def _label_map(values, projects):
    by_path = {}
    labels = set()
    project_paths = set(projects)
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("--label must be /path/to/folder=stable-id")
        raw_path, label = value.split("=", 1)
        path = Path(raw_path).expanduser().resolve()
        if path not in project_paths:
            raise argparse.ArgumentTypeError(f"--label path is not one of the project folders: {raw_path}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", label):
            raise argparse.ArgumentTypeError("labels must be 1-40 letters, numbers, dots, underscores or hyphens")
        if path in by_path or label in labels:
            raise argparse.ArgumentTypeError("each project folder and label must be unique within a run")
        by_path[path] = label
        labels.add(label)
    return by_path


def main():
    parser = argparse.ArgumentParser(description="Read-only report-only scorecard for saved evidence pipelines.")
    parser.add_argument("projects", nargs="+", help="Project review folders. Reports use pseudonymous IDs.")
    parser.add_argument("--case", action="append", default=[], metavar="PROJECT_ID=FILE",
                        help="Optional verified expectation case for an assigned stable project ID.")
    parser.add_argument("--label", action="append", default=[], metavar="FOLDER=ID",
                        help="Optional stable pseudonymous ID for a folder; reuse it across uploads of one project.")
    parser.add_argument("--compare", help="Previously saved portfolio JSON to compare against.")
    parser.add_argument("--output-dir", default=str(ROOT / "output" / "evaluations"))
    args = parser.parse_args()
    cases = _case_map(args.case)
    output_dir = Path(args.output_dir)
    # Prevent an output folder from being placed inside a scanned project.
    projects = [Path(value).expanduser().resolve() for value in args.projects]
    if any(not path.is_dir() for path in projects):
        parser.error("every project argument must be an existing directory")
    output_dir = output_dir.expanduser().resolve()
    if any(output_dir == path or path in output_dir.parents for path in projects):
        parser.error("scorecard output must be outside every scanned project folder")

    try:
        labels = _label_map(args.label, projects)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    rows = []
    project_ids = [labels.get(project, _project_id(project)) for project in projects]
    if len(set(project_ids)) != len(project_ids):
        parser.error("project IDs must be unique; use distinct --label values")
    for project, project_id in zip(projects, project_ids):
        rows.append(evaluate_project_pipeline(project, project_id=project_id, case=cases.get(project_id)))
    report = {"schema_version": 1, "mode": "report_only",
              "created_at": datetime.now(timezone.utc).isoformat(), "projects": rows}
    if args.compare:
        try:
            previous = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read comparison scorecard: {exc}")
        report["comparison"] = compare_scorecards(previous, report)
        old_ids = {row.get("project_id") for row in previous.get("projects", [])}
        new_ids = set(project_ids)
        report["comparison_project_ids"] = {
            "added": sorted(new_ids - old_ids), "removed": sorted(old_ids - new_ids),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    base = output_dir / f"portfolio-{stamp}"
    json_path, markdown_path = base.with_suffix(".json"), base.with_suffix(".md")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    markdown_path.write_text(render_portfolio_markdown(report), encoding="utf-8")
    print(f"Projects scored: {len(rows)}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    if "comparison" in report:
        print(f"Stage/count changes: {len(report['comparison'])}")
        changed_ids = report.get("comparison_project_ids", {})
        if changed_ids.get("added") or changed_ids.get("removed"):
            print("Warning: compared project set changed; added IDs: "
                  + (", ".join(changed_ids.get("added", [])) or "none")
                  + "; removed IDs: " + (", ".join(changed_ids.get("removed", [])) or "none"))


if __name__ == "__main__":
    main()
