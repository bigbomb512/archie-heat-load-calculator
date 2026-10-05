#!/usr/bin/env python3
"""Focused tests for the Card P bounded manual-task workflow."""

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai import autonomous_tasks, reviewer_room_geometry
from backend import autonomous_tasks_service


class AutonomousTaskTests(unittest.TestCase):
    def test_site_cross_check_ignores_page_and_folds_case_and_whitespace(self):
        packet = {"rule_based_top_candidate": {"text": "TENANCY MZ01, M38, MELROSE CENTRAL", "page": 20}}
        reply = {"site": {"text": "tenancy   mz01, m38, melrose central", "page": 1}}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, reply)["status"], "agrees")

    def test_site_cross_check_accepts_contained_real_project_excerpt_and_rejects_other_centre(self):
        full_line = "PROJECT NAME: BUTCHER'S BUFFET TENANCY MZ01,M38, MELROSE CENTRAL PROJECT ADDRESS"
        page_twenty = "TENANCY MZ01,M38, MELROSE CENTRAL"
        packet = {"excerpts": [{"page": 1, "text": full_line}],
                  "rule_based_top_candidate": {"text": page_twenty, "page": 20}}
        for ai_text in ("MELROSE CENTRAL", full_line, page_twenty):
            validated = autonomous_tasks.validate_site_reply(packet, json.dumps({
                "site": {"text": ai_text, "page": 1, "kind": "tenancy_in_centre"},
                "consultant_addresses": [],
            }))
            self.assertEqual(autonomous_tasks.site_cross_check(packet, validated)["status"], "agrees")
        result = autonomous_tasks.site_cross_check(
            {"rule_based_top_candidate": {"text": page_twenty, "page": 20}},
            {"site": {"text": "TENANCY MZ01,M38, DIFFERENT CENTRE", "page": 1}},
        )
        self.assertEqual(result["status"], "disagrees")

    def test_site_reply_must_quote_supplied_page_excerpt(self):
        packet = {"excerpts": [{"page": 2, "text": "TENANCY G12, HARBOUR CENTRE"}]}
        checked = autonomous_tasks.validate_site_reply(packet, json.dumps({
            "site": {"text": "TENANCY G12, HARBOUR CENTRE", "page": 2, "kind": "tenancy_in_centre"},
            "consultant_addresses": [],
        }))
        self.assertEqual(checked["site"]["page"], 2)
        with self.assertRaisesRegex(ValueError, "exact substring"):
            autonomous_tasks.validate_site_reply(packet, json.dumps({
                "site": {"text": "Harbour Centre, Sydney NSW 2000", "page": 2, "kind": "street_address"},
                "consultant_addresses": [],
            }))

    def test_site_cross_check_exposes_conflict_against_rule_candidate(self):
        packet = {"rule_based_top_candidate": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 1}}
        ai_result = {"site": {"text": "42 TEST STREET", "page": 2, "kind": "street_address"}}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, ai_result)["status"], "disagrees")
        ai_result["site"] = {"text": "TENANCY K2, CENTRAL ARCADE", "page": 20, "kind": "tenancy_in_centre"}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, ai_result)["status"], "agrees")

    def test_site_conflict_retries_once_then_uses_rule_candidate_as_assumed_fallback(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = {"text": "TENANCY K2, CENTRAL ARCADE", "page": 1, "kind": "tenancy_in_centre", "confidence": .8}
            record = {"packet": {"rule_based_top_candidate": candidate, "excerpts": [
                {"page": 1, "text": candidate["text"]}, {"page": 2, "text": "42 TEST STREET"}]},
                "prompt": "Site prompt", "retry_count": 0, "run_id": "run-1", "accuracy": {"accuracy": .7},
                "quality_label": "AI-determined (below accuracy bar)", "reply_hash": "hash", "input_fingerprint": "fp"}
            conflicting = {"site": {"text": "42 TEST STREET", "page": 2, "kind": "street_address"}, "consultant_addresses": []}
            retry = autonomous_tasks_service._apply_p1(root, {}, record, conflicting)
            self.assertEqual(retry["status"], "waiting_for_reply")
            self.assertEqual(retry["retry_count"], 1)
            self.assertIn("Cross-check conflict", retry["prompt"])
            fallback = autonomous_tasks_service._apply_p1(root, {}, retry, conflicting)
            self.assertEqual(fallback["source"], "ai_fallback")
            self.assertEqual(fallback["status"], "applied_fallback")
            self.assertEqual(fallback["applied_value"]["site_text"], candidate["text"])

    def test_north_reply_maps_crop_coordinates_to_page_and_computes_bearing(self):
        packet = {"page": 4, "crops": [{"crop_index": 0, "width_px": 100, "height_px": 100,
                    "page_bbox": [200, 300, 400, 400]}]}
        result = autonomous_tasks.validate_north_reply(packet, json.dumps({
            "found": True, "crop_index": 0, "tail_px": [50, 70], "tip_px": [50, 20],
            "labelled_north": True, "description": "Arrow points up",
        }))
        self.assertEqual(result["tail_page_px"], [400.0, 580.0])
        self.assertEqual(result["tip_page_px"], [400.0, 380.0])
        self.assertAlmostEqual(result["plan_up_azimuth_deg"], 0)
        with self.assertRaisesRegex(ValueError, "inside the selected crop"):
            autonomous_tasks.validate_north_reply(packet, json.dumps({"found": True, "crop_index": 0,
                "tail_px": [101, 70], "tip_px": [50, 20]}))

    def test_north_consensus_requires_tolerance_or_strict_majority(self):
        self.assertEqual(autonomous_tasks.north_consensus([(1, 0), (2, 2)]) ["status"], "agreed")
        self.assertEqual(autonomous_tasks.north_consensus([(1, 0), (2, 90)]) ["status"], "disagreement")
        majority = autonomous_tasks.north_consensus([(1, 0), (2, 2), (3, 90)])
        self.assertEqual(majority["status"], "majority")
        self.assertAlmostEqual(majority["winner"], 1)

    def test_roof_requires_explicit_drawing_evidence_and_has_no_building_type_fallback(self):
        self.assertIsNone(autonomous_tasks.TASKS["P5_roof"]["fallback"])
        self.assertFalse(autonomous_tasks_service._has_explicit_roof_evidence({"facts": []}))
        self.assertFalse(autonomous_tasks_service._has_explicit_roof_evidence({"facts": [{"text": "Level 2"}]}))
        self.assertTrue(autonomous_tasks_service._has_explicit_roof_evidence({"facts": [{"text": "ROOF OVER TENANCY"}]}))

    def test_roof_reply_must_quote_drawing_fact(self):
        packet = {"facts": [{"page": 9, "text": "ROOF OVER TENANCY"}], "site_name": "", "tenancy_prefix": "",
                  "room": {"level_name": "Level 1"}}
        self.assertEqual(autonomous_tasks.validate_roof_reply(packet, json.dumps({"roof": "exposed", "evidence": "ROOF OVER TENANCY"}))["roof"], "exposed")
        with self.assertRaisesRegex(ValueError, "quoted drawing fact"):
            autonomous_tasks.validate_roof_reply(packet, json.dumps({"roof": "exposed", "evidence": "probably exposed"}))

    def test_roof_answer_unknown_and_no_evidence_needs_contractor_answer(self):
        packet = {"facts": [], "room": {"room_id": "shop", "room_label": "Shop"}}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            record = {"task": "P5_roof", "target": "shop", "packet": packet, "accuracy": {"auto_apply": True}}
            result = autonomous_tasks_service._apply_p5(
                None, {"review_dir": temporary}, root, record, {"roof": "unknown", "evidence": ""})
            self.assertEqual(result["status"], "needs_contractor_answer")
            self.assertIn("Is there a floor or another tenancy", result["block_reason"])

    def test_run_all_surfaces_no_evidence_roof_question_in_contractor_labels(self):
        with TemporaryDirectory() as temporary:
            project = {"id": "roof-no-evidence", "review_dir": temporary}
            packet = {"facts": [], "room": {"room_id": "shop", "room_label": "Shop"}}
            with patch.object(autonomous_tasks_service, "_site_packet", return_value=({}, "", {})), \
                 patch.object(autonomous_tasks_service, "_north_packets", return_value=[]), \
                 patch.object(autonomous_tasks_service, "_roof_packets", return_value=[("shop", packet, "roof prompt", [])]):
                autonomous_tasks_service.run_all(SimpleNamespace(), project)
            record = autonomous_tasks_service._current_task(Path(temporary), "P5_roof", "shop")
            self.assertEqual(record["status"], "needs_contractor_answer")
            label = autonomous_tasks_service.get_labels(project)["tasks"][0]
            self.assertEqual(label["question"], "Is there a floor or another tenancy directly above this shop, or is it the roof?")
            self.assertEqual(label["room_label"], "Shop")

    def test_contractor_roof_answers_use_reviewer_classification_and_not_sure_stays_unknown(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            project = {"id": "roof-project", "review_dir": temporary}
            web = SimpleNamespace()
            record = autonomous_tasks_service._create_run(root, "P5_roof", "shop", {
                "room": {"room_id": "shop", "room_label": "Shop"}, "facts": []}, "roof prompt")
            record.update({"status": "needs_contractor_answer", "block_reason": autonomous_tasks_service.ROOF_CONTRACTOR_QUESTION})
            autonomous_tasks_service._update_record(root, record)
            calls = []
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"artifact": artifact}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body: calls.append(body)):
                result = autonomous_tasks_service._answer_roof(web, project, root, {"task": "P5_roof", "target": "shop", "answer": "not_sure"})
                self.assertEqual(calls[-1]["roof"], "unknown")
                self.assertEqual(calls[-1]["reviewer"], "Answered by the contractor")
                saved = autonomous_tasks_service._current_task(root, "P5_roof", "shop")
                self.assertEqual(saved["status"], "contractor_answered_not_sure")
                self.assertEqual(saved["applied_value"], {})
                self.assertEqual(result["tasks"][0]["status"], "contractor_answered_not_sure")
                labels = autonomous_tasks_service.get_labels(project)["tasks"]
                self.assertEqual(labels[0]["status"], "contractor_answered_not_sure")
                self.assertIn("not assessed", labels[0]["message"])

    def test_contractor_roof_answer_records_floor_above_as_reviewer_declaration(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            project = {"id": "roof-answer", "review_dir": temporary}
            record = autonomous_tasks_service._create_run(root, "P5_roof", "shop", {"room": {"room_label": "Shop"}}, "prompt")
            record["status"] = "needs_contractor_answer"
            autonomous_tasks_service._update_record(root, record)
            calls = []
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"artifact": artifact}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body: calls.append(body)):
                autonomous_tasks_service._answer_roof(SimpleNamespace(), project, root, {
                    "task": "P5_roof", "target": "shop", "answer": "floor_tenancy_above"})
            self.assertEqual(calls[0]["action"], "classify_envelope")
            self.assertEqual(calls[0]["roof"], "not_exposed")
            self.assertEqual(calls[0]["reviewer"], "Answered by the contractor")
            self.assertTrue(calls[0]["confirm_roof"])
            saved = autonomous_tasks_service._current_task(root, "P5_roof", "shop")
            self.assertEqual(saved["status"], "applied")
            self.assertEqual(saved["applied_value"]["label"], "Answered by the contractor")
            exported = json.loads((root / "ai_task_determinations.json").read_text())
            self.assertEqual(exported["P5_roof"][0]["source"], "reviewer")

    def test_run_reused_when_inputs_match_and_new_run_created_when_they_change(self):
        with TemporaryDirectory() as temporary, patch.object(autonomous_tasks_service, "_accuracy", return_value={
                "accuracy": 0.9, "scored": 5, "auto_apply": True, "report": "test.json"}):
            root = Path(temporary)
            first = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": []}, "Prompt")
            same = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": []}, "Prompt")
            changed = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": [{"page": 1}]}, "Prompt")
            self.assertEqual(first["run_id"], same["run_id"])
            self.assertNotEqual(first["run_id"], changed["run_id"])
            self.assertTrue((root / "ai_tasks/P1_site/project/runs" / first["run_id"] / "reply.json").parent.is_dir())

    def test_accuracy_registry_is_required_and_requires_ten_scored_items(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "accuracy.json"
            with patch.object(autonomous_tasks_service, "ACCURACY_PATH", path):
                self.assertFalse(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])
                path.write_text(json.dumps({"tasks": {"P1_site": {"accuracy": 1.0, "scored": 9}}}))
                self.assertFalse(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])
                path.write_text(json.dumps({"tasks": {"P1_site": {"accuracy": .9, "scored": 10}}}))
                self.assertTrue(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])

    def test_accuracy_recorder_rejects_standins_partial_cases_and_small_samples(self):
        tool_path = Path(__file__).resolve().parents[1] / "tools" / "evaluate_autonomous_tasks.py"
        spec = importlib.util.spec_from_file_location("evaluate_autonomous_tasks_test", tool_path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        self.assertTrue(evaluator._contains_stand_in({"P1_site": {"stand_in": True}}))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = root / "evaluations" / "autonomous"
            keys.mkdir(parents=True)
            for number in range(10):
                (keys / f"case{number}.json").write_text(json.dumps({"case_id": f"case{number}",
                    "tasks": {"P1_site": {"site_must_contain": ["site"]}}}))
            evaluator.ROOT = root
            summary = {"P1_site": {"correct": 10, "wrong": 0, "missing": 0, "scored": 10}}
            complete = [{"case_id": f"case{number}", "tasks": {"P1_site": [{"status": "correct"}]}}
                        for number in range(10)]
            self.assertTrue(evaluator._recordable_accuracy(complete, summary, .85)["tasks"]["P1_site"]["auto_apply"])
            with self.assertRaisesRegex(ValueError, "case9"):
                evaluator._recordable_accuracy(complete[:-1], summary, .85)
            with self.assertRaisesRegex(ValueError, "at least 10"):
                evaluator._recordable_accuracy(complete, {"P1_site": {**summary["P1_site"], "scored": 9}}, .85)

    def test_record_cli_refuses_stand_in_marker(self):
        tool_path = Path(__file__).resolve().parents[1] / "tools" / "evaluate_autonomous_tasks.py"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "case.json"
            determination = root / "determinations.json"
            key.write_text(json.dumps({"case_id": "standin", "tasks": {"P1_site": {}}}), encoding="utf-8")
            determination.write_text(json.dumps({"stand_in": True, "P1_site": {"site_text": "sample"}}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(tool_path), f"{key}={determination}", "--record",
                                     "--output-dir", str(root / "reports")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("marked stand_in", result.stderr)

    def test_manual_reply_attempts_are_archived_without_overwriting_rejected_reply(self):
        with TemporaryDirectory() as temporary, patch.object(autonomous_tasks_service, "_accuracy", return_value={
                "accuracy": 0.7, "scored": 3, "auto_apply": False, "report": "test.json"}), \
             patch.object(autonomous_tasks_service.productization, "record_change_if_fingerprint_changed"):
            root = Path(temporary)
            project = {"id": "test-project", "review_dir": str(root)}
            web = SimpleNamespace()
            packet = {"excerpts": [{"page": 3, "text": "TENANCY K2, CENTRAL ARCADE"}],
                      "rule_based_top_candidate": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 3,
                                                    "kind": "tenancy_in_centre", "confidence": .8}}
            autonomous_task_service_record = autonomous_tasks_service._create_run(
                root, "P1_site", "project", packet, "Site prompt")
            bad = autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": "P1_site", "target": "project",
                "reply": "not json", "model_note": "stand-in"})
            record = autonomous_tasks_service._current_task(root, "P1_site", "project")
            attempts = sorted((root / "ai_tasks/P1_site/project/runs" / autonomous_task_service_record["run_id"] / "reply_attempts").glob("*.json"))
            self.assertEqual(len(attempts), 1)
            first_reply = json.loads(attempts[0].read_text(encoding="utf-8"))
            self.assertEqual(first_reply["outcome"], "rejected")
            good = json.dumps({"site": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 3, "kind": "tenancy_in_centre"}, "consultant_addresses": []})
            autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": "P1_site", "target": "project",
                "reply": good, "model_note": "test", "stand_in": True})
            attempts = sorted((root / "ai_tasks/P1_site/project/runs" / autonomous_task_service_record["run_id"] / "reply_attempts").glob("*.json"))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(json.loads(attempts[0].read_text(encoding="utf-8"))["raw_reply"], "not json")
            self.assertEqual(json.loads(attempts[1].read_text(encoding="utf-8"))["raw_reply"], good)
            self.assertEqual(autonomous_tasks_service._current_task(root, "P1_site", "project")["status"], "below_accuracy_bar")
            self.assertTrue(json.loads((root / "ai_tasks/determinations.json").read_text())["stand_in"])

    def test_roof_packets_only_include_explicit_comfort_scope_traces(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "comfort", "space_scope": "comfort_hvac"},
                {"room_id": "process", "space_scope": "refrigeration_process"},
                {"room_id": "excluded", "space_scope": "not_a_room"},
                {"room_id": "unresolved", "space_scope": "unresolved_scope"},
            ]}), encoding="utf-8")
            traces = [{"room_id": room_id, "room_label": room_id.title(), "level_name": "Ground", "page": 1,
                       "calibration": {"status": "agreed"}} for room_id in ("comfort", "process", "excluded", "unresolved")]
            with patch.object(autonomous_tasks_service, "_load_inputs", return_value=({}, {}, {})), \
                 patch.object(autonomous_tasks_service, "_latest_site", return_value={}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"review_dir": root}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=traces), \
                 patch.object(autonomous_tasks, "build_roof_facts", side_effect=lambda _a, _s, room: {"room": room}), \
                 patch.object(autonomous_tasks, "roof_prompt", return_value="prompt"):
                packets = autonomous_tasks_service._roof_packets(root)
            self.assertEqual([row[0] for row in packets], ["comfort"])

    def test_ai_declaration_metadata_validates_and_legacy_fields_remain_absent(self):
        north = {"1": {"page": 1, "plan_up_azimuth_deg": 0, "source": "reviewer_typed_page_up_bearing",
                        "reviewer": "Archie AI", "declared_at": "now", "declaration_source": "ai_determined", "ai_run_id": "run-1"}}
        checked = reviewer_room_geometry.validate_artifact({"records": [], "page_north": north})
        self.assertEqual(checked["page_north"]["1"]["declaration_source"], "ai_determined")
        self.assertEqual(reviewer_room_geometry.validate_artifact({"records": []}).keys(), {"schema_version", "records", "fingerprint"})
        with self.assertRaisesRegex(ValueError, "run ID"):
            reviewer_room_geometry.validate_artifact({"records": [], "page_north": {"1": {
                **north["1"], "ai_run_id": ""}}})


if __name__ == "__main__":
    unittest.main()
