import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import ai_preliminary_service, job_files, job_plan_service, job_service

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]


def _trace(room_id, label, status, source="ai_determined", **extra):
    return {"room_id": room_id, "room_label": label, "page": 1, "points_image_px": SQUARE,
            "declaration_source": source, "calibration": {"status": status}, **extra}


class JobPlanServiceTests(unittest.TestCase):
    def plan(self, candidates, traces=(), traced_areas=None, uses=(), base=None, final=None, status=None,
             tasks=(), stale=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "room_use_resolution.json").write_text(json.dumps({"records": list(uses)}))
        project = {"id": "j", "review_dir": str(root)}
        status = {"phase1_complete": True, "results_ready": False, "result_stale": False,
                  "area_overrides": [], **(status or {})}
        model = {"room_scope": {"candidates": candidates},
                 "phase1_base_room_load_report": base or {},
                 "phase1_base_room_load_report_stale": stale,
                 "hourly_ai_preliminary_load_report": final or {}}
        g = job_plan_service.reviewer_room_geometry_service
        with patch.object(job_plan_service.job_service, "status", return_value=status), \
             patch.object(job_plan_service.ai_preliminary_service, "get", return_value=model), \
             patch.object(g, "current_artifact_input", return_value={"records": list(traces)}), \
             patch.object(g, "current_traced_areas", return_value=traced_areas or {}), \
             patch.object(g, "_paths", return_value={}), \
             patch.object(g, "_page_context", return_value=[{"page": 1, "image_width_px": 100, "image_height_px": 100}]), \
             patch("backend.autonomous_tasks_service._all_current", return_value=list(tasks)):
            result = job_plan_service.get(SimpleNamespace(), project)
        return result, {row["room_id"]: row for row in result["rooms"]}

    def test_outline_line_style_follows_how_the_scale_was_checked(self):
        candidates = [{"key": key, "label": key.title(), "include": True, "status": "calculated"}
                      for key in ("shop", "bar", "store", "office")]
        traces = [_trace("shop", "Shop", "agreed"),
                  _trace("bar", "Bar", "declared_scale_rejected"),
                  _trace("store", "Store", "declared_scale_only"),
                  _trace("office", "Office", "", source="reviewer")]
        result, _ = self.plan(candidates, traces)
        styles = {row["room_id"]: row["line_style"] for row in result["outlines"]}
        self.assertEqual(styles, {"shop": "solid", "bar": "solid", "store": "dashed", "office": "solid"})

    def test_area_basis_labels_area_only_estimates_and_remainders(self):
        candidates = [{"key": "a", "label": "A", "include": True, "status": "calculated"},
                      {"key": "b", "label": "B", "include": True, "status": "calculated"},
                      {"key": "c", "label": "C", "include": True, "status": "calculated", "area_m2": 9,
                       "area_origin": "pdf_evidence"},
                      {"key": "d", "label": "D", "include": True, "status": "calculated", "area_m2": 30,
                       "area_origin": "reviewer_traced"},
                      {"key": "e", "label": "E", "include": True, "status": "calculated", "area_m2": 20,
                       "area_origin": "ai_determined"}]
        traced = {"a": {"area_m2": 12, "area_source": "AI estimate from the plan"},
                  "b": {"area_m2": 30, "area_source": "tenancy area remainder"}}
        _, rooms = self.plan(candidates, traced_areas=traced)
        self.assertEqual([rooms[key]["area_basis"] for key in "abcde"],
                         ["estimate", "remainder", "printed", "measured", "ai_outline"])
        self.assertEqual(rooms["a"]["area_label"], "AI estimate from the plan")

    def test_room_kind_comes_from_the_room_use_scope_not_the_name(self):
        candidates = [{"key": "shop", "label": "Shop", "include": True, "status": "calculated"}]
        uses = [{"room_id": "cold", "original_label": "Store 2", "space_scope": "refrigeration_process"},
                {"room_id": "shop", "original_label": "Shop", "space_scope": "comfort_hvac"}]
        traced = {"cold": {"area_m2": 7.5, "area_source": "ai_determined", "calibration_status": "agreed"}}
        result, rooms = self.plan(candidates, [_trace("cold", "Store 2", "agreed")], traced, uses)
        self.assertEqual((rooms["cold"]["kind"], rooms["cold"]["state"], rooms["cold"]["area_m2"]),
                         ("refrigeration", "sized_separately", 7.5))
        self.assertFalse(rooms["cold"]["included"])
        self.assertEqual(rooms["shop"]["kind"], "comfort")
        self.assertEqual(result["outlines"][0]["kind"], "refrigeration")

    def test_room_without_an_area_is_being_found_then_typed_never_left_out(self):
        bar = {"key": "bar", "label": "Bar", "include": False, "status": "no_area", "level": "Unassigned level"}
        waiting = [{"task": "P0_room_sizes", "status": "waiting_for_reply", "reply_attempts": []}]
        _, rooms = self.plan([bar], tasks=waiting)
        self.assertEqual(rooms["bar"]["state"], "finding_area")
        self.assertEqual(rooms["bar"]["level"], "")
        _, rooms = self.plan([bar], tasks=[])
        self.assertEqual(rooms["bar"]["state"], "needs_area")

    def test_phase1_load_shows_only_from_a_current_unblocked_result_that_included_the_room(self):
        candidates = [{"key": "shop", "label": "Shop", "include": True, "status": "calculated", "area_m2": 50},
                      {"key": "bar", "label": "Bar", "include": True, "status": "calculated", "area_m2": 10}]
        base = {"room_peaks": [{"room_id": "m1", "name": "Shop", "design_total_kw": 2.5},
                               {"room_id": "m2", "name": "Bar", "design_total_kw": 0.4}],
                "phase1_coverage": {"rooms_included": ["Shop"]}}
        _, rooms = self.plan(candidates, base=base)
        self.assertEqual((rooms["shop"]["phase1_kw"], rooms["shop"]["w_m2"], rooms["shop"]["state"]), (2.5, 50.0, "done"))
        self.assertEqual((rooms["bar"]["phase1_kw"], rooms["bar"]["state"]), (None, "finding_surfaces"))
        _, rooms = self.plan(candidates, base={**base, "blocked_reasons": ["No wall resolved."]})
        self.assertIsNone(rooms["shop"]["phase1_kw"])
        _, rooms = self.plan(candidates, base=base, stale=True)
        self.assertEqual((rooms["shop"]["phase1_kw"], rooms["shop"]["state"]), (None, "finding_surfaces"))

    def test_final_room_load_needs_a_current_full_result(self):
        candidates = [{"key": "shop", "label": "Shop", "include": True, "status": "calculated", "area_m2": 50}]
        final = {"room_peaks": [{"room_id": "m1", "name": "Shop", "design_total_kw": 9.1}]}
        _, rooms = self.plan(candidates, final=final, status={"results_ready": True})
        self.assertEqual((rooms["shop"]["final_kw"], rooms["shop"]["final_w_m2"]), (9.1, 182.0))
        _, rooms = self.plan(candidates, final=final, status={"results_ready": True, "result_stale": True})
        self.assertIsNone(rooms["shop"]["final_kw"])


class OpenInformationNeedsTests(unittest.TestCase):
    def test_counts_only_unanswered_undecided_needs_of_the_current_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(job_service.open_information_needs(root), 0)
            (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": "r1"}))
            proposals = root / "skill_workflow_runs" / "r1" / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "information_needs.json").write_text(json.dumps(
                {"proposal_fields": {"needs": [{"target": "Shop"}, {"target": "Bar"}, {"target": "Kitchen"}]}}))
            self.assertEqual(job_service.open_information_needs(root), 3)
            (root / "information_answers.json").write_text(json.dumps({"answers": {"information_needs:needs:0": {}}}))
            (root / "skill_review_decisions.json").write_text(json.dumps(
                {"decisions": {"information_needs:needs:1": {"status": "rejected"}}}))
            self.assertEqual(job_service.open_information_needs(root), 1)


class CachedFreshnessTests(unittest.TestCase):
    def test_source_fingerprints_are_worked_out_again_after_any_saved_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text("{}")
            paths = {"root": root}
            calls = []
            with patch.object(ai_preliminary_service, "_sources", side_effect=lambda _: calls.append(1) or {"x": str(len(calls))}):
                first = ai_preliminary_service._current_sources(paths)
                again = ai_preliminary_service._current_sources(paths)
                staged = root / "a.json.stage"
                staged.write_text('{"changed": true}')
                os.replace(staged, root / "a.json")
                changed = ai_preliminary_service._current_sources(paths)
            self.assertEqual((first, again, changed, len(calls)), ({"x": "1"}, {"x": "1"}, {"x": "2"}, 2))

    def test_signature_covers_nested_json_and_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir()
            (root / "runs" / "p.json").write_text("{}")
            pdf = root / "set.pdf"
            pdf.write_bytes(b"%PDF")
            before = job_files.signature(root, [str(pdf)])
            pdf.write_bytes(b"%PDF-changed")
            self.assertNotEqual(before, job_files.signature(root, [str(pdf)]))
            self.assertIn(str(root / "runs" / "p.json"), {row[0] for row in before})


if __name__ == "__main__":
    unittest.main()
