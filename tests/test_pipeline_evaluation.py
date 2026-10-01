#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.drawing_coverage import source_fingerprint
from ai import calculator_inputs
from ai import ai_preliminary
from ai.pipeline_evaluation import (
    ALLOWED_STATES,
    compare_scorecards,
    evaluate_project_pipeline,
    render_portfolio_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"PASS - {name}")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_project(root, *, reviewed=True, later=True):
    packet = {
        "drawing_set": {"pages": [{"page": 1}, {"page": 2}]},
        "page_triage": {"pages": [{"page": 1, "role": "floor_plan"}, {"page": 2, "role": "schedule"}]},
        "review_status": {"human_reviewed": reviewed},
        "confirmed_pages": {},
    }
    write_json(root / "ai_input.json", packet)
    coverage = {
        "version": 5, "method_versions": {"level_classification": 2}, "source_fingerprint": source_fingerprint(packet),
        "pages": [{"page": 1, "level_status": "confirmed_by_text", "level_candidates": [{"kind": "building_level"}]},
                  {"page": 2, "level_status": "missing", "level_candidates": [{"kind": "finish_or_tag_code"}]}], "page_roles": [
            {"page": 1, "role": "floor_plan"}, {"page": 2, "role": "schedule"}],
        "levels": [{"level_name": "Ground Floor", "status": "confirmed"}],
        "coverage_exceptions": [], "status": "coverage_ready_for_engineer_review",
    }
    write_json(root / "drawing_coverage.json", coverage)
    if not later:
        return packet
    write_json(root / "building_evidence.json", {
        "version": 1, "source_fingerprint": source_fingerprint(packet),
        "spaces": [{"name": "Dining", "source_pages": [1]}],
        "levels": [{"name": "Ground Floor"}], "surfaces": [], "openings": [],
        "constructions": [], "lighting": [], "equipment": [],
    })
    write_json(root / "architect_evidence_fusion.json", {
        "schema_version": 2, "source_fingerprint": source_fingerprint(packet), "pages": [{}, {}],
        "entities": [{}, {}], "relationships": [], "conflicts": [], "review_items": [],
    })
    write_json(root / "calculation_input_evidence.json", {
        "schema_version": 1, "input_artifact_fingerprints": {},
        "candidates": [{"category": "occupancy", "status": "proposed", "page": 2}],
        "issues": [], "categories": {"occupancy": {"count": 1}},
    })
    write_json(root / "room_inference_job.json", {
        "schema_version": 1, "status": "completed", "candidate_count": 1,
    })
    write_json(root / "room_use_resolution.json", {"schema_version": 1, "records": [{"status": "resolved"}]})
    write_json(root / "internal_gains_resolution.json", {"schema_version": 1, "records": [{"status": "needs_review"}], "schedules": []})
    write_json(root / "model_input_resolution.json", {"schema_version": 1, "records": [{"resolution_status": "needs_review"}], "review_queue": [{}], "dependency_fingerprints": {}})
    write_json(root / "calculator_draft.json", {
        "schema_version": 2, "status": "review_required",
        "candidates": {"floors": [{}], "zones": [], "rooms": [{}], "room_inputs": []}, "review_items": [{}],
        "source_fingerprints": {},
    })
    input_snapshot = {
        "schema_version": 2, "input_fingerprint": "fixture-snapshot", "status": "blocked",
        "resolved_inputs": [], "included_room_ids": [], "issues": [{"status": "blocked"}], "source_fingerprints": {},
    }
    input_snapshot["snapshot_path"] = "calculator_input_sets/fixture-snapshot.json"
    write_json(root / "calculator_input_sets" / "fixture-snapshot.json", input_snapshot)
    write_json(root / "calculator_input_set.json", {
        "schema_version": 2, "input_fingerprint": "fixture-snapshot",
        "snapshot_path": "fixture-snapshot.json", "status": "blocked", "created_at": "2026-10-01T00:00:00Z",
        "source_fingerprints": {},
    })
    write_json(root / "hourly_load_report.json", {
        "status": "blocked", "readiness": {"status": "blocked", "issues": [
            {"reason": "infiltration assessment: unresolved path"}, {"reason": "safety factor: not reviewed"}]},
    })
    return packet


def snapshot(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
            for path in root.rglob("*") if path.is_file()}


def main():
    case_files = sorted((ROOT / "evaluations" / "cases").glob("*.json"))
    case_bytes = {path.name: path.read_bytes() for path in case_files}
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        complete = base / "complete"
        complete.mkdir()
        packet = make_project(complete)
        before = snapshot(complete)
        report = evaluate_project_pipeline(complete, project_id="project-1")
        check("all supported persisted stages are present", all(
            report["stages"][name]["status"] == "present" for name in (
                "page_triage", "page_review", "drawing_coverage", "building_evidence",
                "evidence_fusion", "calculation_input_evidence", "room_inference",
                "room_use_resolution", "internal_gains_resolution", "model_input_resolution",
                "calculator_draft", "calculator_inputs", "calculation_readiness")),
            True)
        check("real pointer loads its referenced calculator snapshot",
              report["stages"]["calculator_inputs"]["counts"]["issues_by_status"], {"blocked": 1})
        fresh_calc = base / "fresh-calculator-inputs"
        fresh_calc.mkdir()
        make_project(fresh_calc)
        evidence = json.loads((fresh_calc / "calculation_input_evidence.json").read_text())
        evidence_fingerprint = calculator_inputs._fingerprint(calculator_inputs._stable(evidence))
        write_json(fresh_calc / "calculation_input_evidence.json", evidence)
        snapshot_path = fresh_calc / "calculator_input_sets" / "fixture-snapshot.json"
        stored_snapshot = json.loads(snapshot_path.read_text())
        stored_snapshot["source_fingerprints"] = {"calculation_input_evidence": evidence_fingerprint}
        write_json(snapshot_path, stored_snapshot)
        pointer = json.loads((fresh_calc / "calculator_input_set.json").read_text())
        pointer["source_fingerprints"] = stored_snapshot["source_fingerprints"]
        write_json(fresh_calc / "calculator_input_set.json", pointer)
        current_calc = evaluate_project_pipeline(fresh_calc)
        check("unchanged real-format input evidence uses producer fingerprint and stays current",
              current_calc["stages"]["calculator_inputs"]["freshness"], "current")
        mismatch = dict(pointer, input_fingerprint="other-snapshot")
        write_json(fresh_calc / "calculator_input_set.json", mismatch)
        mismatch_report = evaluate_project_pipeline(fresh_calc)
        check("pointer/snapshot disagreement is an explicit error",
              mismatch_report["stages"]["calculator_inputs"]["status"], "error")
        check("stage states use the declared state set",
              {stage["status"] for stage in report["stages"].values()} <= ALLOWED_STATES, True)
        check("readiness is stored, not recalculated", report["stages"]["calculation_readiness"]["reason"].startswith("stored readiness only"), True)
        check("blocker reasons are grouped by leading phrase", report["blocker_groups"], {"infiltration assessment": 1, "safety factor": 1})
        check("page and category counts come from artifacts", report["stages"]["drawing_coverage"]["counts"]["pages"], 2)
        check("scorecard counts level evidence by classification kind",
              report["stages"]["drawing_coverage"]["counts"]["level_candidates_by_kind"],
              {"building_level": 1, "finish_or_tag_code": 1})
        check("scorecard reports page level statuses",
              report["stages"]["drawing_coverage"]["counts"]["page_level_status_counts"],
              {"confirmed_by_text": 1, "missing": 1})
        check("scorecard retains each page level status",
              report["stages"]["drawing_coverage"]["counts"]["page_level_statuses"],
              [{"page": 1, "status": "confirmed_by_text"}, {"page": 2, "status": "missing"}])
        check("scorecard does not change project files", snapshot(complete), before)

        gated = base / "gated"
        gated.mkdir()
        make_project(gated, reviewed=False, later=False)
        gated_report = evaluate_project_pipeline(gated, project_id="project-2")
        check("unreviewed packet stops at page review gate", gated_report["stages"]["page_review"]["status"], "gated")
        check("missing downstream work is reported gated", gated_report["stages"]["calculator_draft"]["status"], "gated")

        absent = base / "absent"
        absent.mkdir()
        write_json(absent / "ai_input.json", {"review_status": {"human_reviewed": True}, "drawing_set": {"pages": []}})
        absent_report = evaluate_project_pipeline(absent)
        check("missing downstream artifact is absent after review", absent_report["stages"]["evidence_fusion"]["status"], "absent")

        no_drawing = base / "no-drawing"
        no_drawing.mkdir()
        no_drawing_report = evaluate_project_pipeline(no_drawing)
        check("missing review packet does not gate absent downstream artifacts",
              no_drawing_report["stages"]["calculator_inputs"]["status"], "absent")
        check("missing review packet has absent readiness stage",
              no_drawing_report["stages"]["calculation_readiness"]["status"], "absent")

        stale = base / "stale"
        stale.mkdir()
        make_project(stale, later=False)
        coverage = json.loads((stale / "drawing_coverage.json").read_text())
        coverage["source_fingerprint"] = "old-upstream-fingerprint"
        write_json(stale / "drawing_coverage.json", coverage)
        stale_report = evaluate_project_pipeline(stale)
        check("upstream fingerprint mismatch is stale", stale_report["stages"]["drawing_coverage"]["status"], "stale")
        coverage["version"] = 4
        write_json(stale / "drawing_coverage.json", coverage)
        old_method_report = evaluate_project_pipeline(stale)
        check("pre-classifier coverage requires one-time rebuild",
              old_method_report["stages"]["drawing_coverage"]["status"], "stale")
        coverage["version"] = 5
        coverage.pop("method_versions", None)
        write_json(stale / "drawing_coverage.json", coverage)
        missing_method_report = evaluate_project_pipeline(stale)
        check("coverage without the required level method version is stale",
              missing_method_report["stages"]["drawing_coverage"]["status"], "stale")
        coverage["version"] = 99
        write_json(stale / "drawing_coverage.json", coverage)
        version_report = evaluate_project_pipeline(stale)
        check("unknown artifact schema is an explicit error", version_report["stages"]["drawing_coverage"]["status"], "error")

        unknown = base / "unknown"
        unknown.mkdir()
        make_project(unknown, later=False)
        write_json(unknown / "building_evidence.json", {"version": 1, "spaces": [], "levels": []})
        unknown_report = evaluate_project_pipeline(unknown)
        check("missing provenance stays unknown instead of current", unknown_report["stages"]["building_evidence"]["freshness"], "unknown")

        malformed = base / "malformed"
        malformed.mkdir()
        make_project(malformed, later=False)
        (malformed / "architect_evidence_fusion.json").write_text("{bad", encoding="utf-8")
        malformed_report = evaluate_project_pipeline(malformed)
        check("malformed artifact reports filename as error", malformed_report["stages"]["evidence_fusion"]["status"], "error")
        check("malformed error names artifact", "architect_evidence_fusion.json" in malformed_report["stages"]["evidence_fusion"]["reason"], True)

        expected = {"expectations": {
            "floors_expected": [{"label": "Ground Floor"}, {"label": "Upper Floor"}],
            "room_labels_expected": [{"label": "Dining", "page": 1}, {"label": "Office", "page": 9}],
            "input_candidates_expected": [{"category": "occupancy", "page": 2}, {"category": "people", "page": 8}],
        }}
        expected_report = evaluate_project_pipeline(complete, case=expected)
        checks = [row["status"] for row in expected_report["expectations"]]
        check("verified optional expectation outcomes pass/fail", checks, ["passed", "failed", "passed", "failed", "passed", "failed"])
        mixed_values = base / "mixed-values"
        mixed_values.mkdir()
        packet = make_project(mixed_values)
        evidence = json.loads((mixed_values / "calculation_input_evidence.json").read_text())
        evidence["candidates"] = [{"category": "area", "value": 12}, {"category": "label", "value": None}]
        write_json(mixed_values / "calculation_input_evidence.json", evidence)
        mixed_expectation = evaluate_project_pipeline(mixed_values, case={"expectations": {
            "room_labels_expected": [{"label": "nonexistent"}]}})
        check("room-label expectations tolerate scalar and null values",
              mixed_expectation["expectations"][0]["status"], "failed")
        fallback = base / "manual-proposal-fallback"
        fallback.mkdir()
        make_project(fallback)
        run_data = {"manual_placeholder_proposal": {"rooms": [{"room_id": "r1", "name": "Dining"}]}}
        write_json(fallback / "ai_preliminary_run.json", run_data)
        building_data = json.loads((fallback / "building_evidence.json").read_text())
        vision_data = {}
        proposal_fp = ai_preliminary.fingerprint(run_data["manual_placeholder_proposal"])
        write_json(fallback / "room_use_resolution.json", {
            "schema_version": 1, "records": [{"status": "resolved"}],
            "source_fingerprints": {
                "building_evidence": ai_preliminary.fingerprint(building_data),
                "vision_response": ai_preliminary.fingerprint(vision_data),
                "ai_preliminary_proposal": proposal_fp,
            },
        })
        fallback_report = evaluate_project_pipeline(fallback)
        check("room-use freshness follows service manual-proposal fallback order",
              fallback_report["stages"]["room_use_resolution"]["freshness"], "current")
        absent_expected = evaluate_project_pipeline(absent, case={"expectations": {
            "floors_expected": [{"label": "Ground"}], "input_candidates_expected": [{"category": "occupancy", "page": 1}]}})
        check("expectations for absent stages are not evaluated", [row["status"] for row in absent_expected["expectations"]], ["not_evaluated", "not_evaluated"])
        check("empty expectations create no rows", evaluate_project_pipeline(complete, case={"expectations": {}})["expectations"], [])

        portfolio = {"projects": [report]}
        unchanged = compare_scorecards(portfolio, portfolio)
        check("comparison with itself has zero changes", unchanged, [])
        changed = json.loads(json.dumps(portfolio))
        changed["projects"][0]["stages"]["room_inference"]["status"] = "absent"
        check("comparison detects a worse stage", compare_scorecards(portfolio, changed)[0]["change"], "worsened")
        blockers_before = json.loads(json.dumps(portfolio))
        blockers_after = json.loads(json.dumps(portfolio))
        blockers_before["projects"][0]["stages"]["calculation_readiness"]["counts"]["blockers"] = 2
        blockers_after["projects"][0]["stages"]["calculation_readiness"]["counts"]["blockers"] = 9
        blocker_change = next(row for row in compare_scorecards(blockers_before, blockers_after)
                              if row["stage"] == "calculation_readiness")
        check("increased blocker count is worsened", blocker_change["change"], "worsened")
        from tools.evaluate_portfolio import _project_id
        forward_ids = [_project_id(path) for path in (complete, gated)]
        reversed_ids = [_project_id(path) for path in (gated, complete)]
        check("default pseudonymous project ID mapping survives argument reordering",
              reversed_ids, list(reversed(forward_ids)))
        markdown = render_portfolio_markdown({"projects": [report], "comparison": unchanged})
        check("portfolio Markdown explains report-only limits", "not extraction or calculation accuracy" in markdown, True)
        check("portfolio Markdown shows per-project level evidence coverage",
              "page 1: confirmed_by_text" in markdown and "building_level" in markdown, True)
        set_changed = render_portfolio_markdown({"projects": [report], "comparison": [],
                                                 "comparison_project_ids": {"added": ["project-new"], "removed": ["project-old"]}})
        check("comparison Markdown warns about changed project IDs",
              "Project set changed" in set_changed and "project-old" in set_changed, True)

        output = base / "portfolio-output"
        command = [sys.executable, str(ROOT / "tools" / "evaluate_portfolio.py"),
                   str(complete), str(gated), "--output-dir", str(output)]
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
        check("portfolio CLI completes without errors", result.returncode, 0)
        json_reports = list(output.glob("*.json"))
        md_reports = list(output.glob("*.md"))
        check("portfolio CLI writes JSON and Markdown", (len(json_reports), len(md_reports)), (1, 1))
        saved = json.loads(json_reports[0].read_text(encoding="utf-8"))
        check("portfolio report uses stable pseudonymous IDs", [row["project_id"] for row in saved["projects"]],
              [_project_id(complete), _project_id(gated)])
        check("portfolio report omits scanned project paths", str(complete) not in json_reports[0].read_text(encoding="utf-8"), True)
        self_compare = subprocess.run(command + ["--compare", str(json_reports[0])],
                                      cwd=ROOT, env=env, text=True, capture_output=True)
        check("comparison CLI accepts saved scorecard", self_compare.returncode, 0)
        compared_reports = sorted(output.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        check("CLI self-comparison records zero changes",
              json.loads(compared_reports[-1].read_text(encoding="utf-8"))["comparison"], [])

        upload = base / "same-project-new-upload"
        upload.mkdir()
        make_project(upload)
        label_output = base / "label-output"
        labeled_first = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "evaluate_portfolio.py"), str(complete),
             "--label", f"{complete}=caseA", "--output-dir", str(label_output)],
            cwd=ROOT, env=env, text=True, capture_output=True)
        check("explicit project label is accepted", labeled_first.returncode, 0)
        first_json = next(label_output.glob("*.json"))
        labeled_second = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "evaluate_portfolio.py"), str(upload),
             "--label", f"{upload}=caseA", "--compare", str(first_json), "--output-dir", str(label_output)],
            cwd=ROOT, env=env, text=True, capture_output=True)
        check("same project label compares separate timestamped uploads", labeled_second.returncode, 0)
        latest_label_report = max(label_output.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        labeled_report = json.loads(latest_label_report.read_text(encoding="utf-8"))
        check("explicit ID avoids false add/remove and maps the same project",
              ([row["project_id"] for row in labeled_report["projects"]],
               labeled_report["comparison_project_ids"], labeled_report["comparison"]),
              (["caseA"], {"added": [], "removed": []}, []))

    check("existing evaluation case files remain byte-for-byte unchanged",
          {path.name: path.read_bytes() for path in case_files}, case_bytes)


if __name__ == "__main__":
    main()
