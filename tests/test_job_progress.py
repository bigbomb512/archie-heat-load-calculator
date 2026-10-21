"""One model of where a job is: the step-state words, the stage rule, and the jobs list agreeing with the job screen."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import job_progress, job_service
from backend.job_runner import recovery, step_state


def milestones(**facts):
    base = dict(drawings_confirmed=True, checks={"waiting": 0, "blocked": 0}, phase1_complete=True, result_exists=True,
                result_stale=False, rooms=[], areas_still_being_found=False, open_needs=0)
    return job_progress.milestones(**{**base, **facts})


class StepStateTests(unittest.TestCase):
    def test_every_steps_own_words_map_to_one_of_five_states(self):
        cases = {"queued": "active", "running": "active", "reading": "active", "stopping": "stopping",
                 "done": "done", "completed": "done", "needs_review": "done", "provisional": "done",
                 "failed": "failed", "blocked": "failed", "interrupted": "failed", "error": "failed",
                 "none": "waiting", "not_started": "waiting", "stale": "waiting", "": "waiting", None: "waiting",
                 "RUNNING": "active"}
        for word, expected in cases.items():
            with self.subTest(word=word):
                self.assertEqual(step_state(word), expected)

    def test_the_recovery_button_follows_the_same_states(self):
        self.assertEqual(recovery("needs_review")["action"], None)
        self.assertEqual(recovery("queued")["action"], "restart")
        self.assertEqual(recovery("stopping")["label"], "Restarting…")
        self.assertEqual((recovery("interrupted")["action"], recovery("interrupted")["label"]), ("resume", "Try again"))
        self.assertEqual(recovery("blocked")["label"], "Check and resume")
        self.assertEqual(recovery("stale")["label"], "Try again")


class MilestoneTests(unittest.TestCase):
    def test_the_stage_follows_the_pipeline_order(self):
        self.assertEqual(milestones(drawings_confirmed=False)["stage"], "drawings")
        self.assertEqual(milestones(checks={"waiting": 2})["stage"], "drawings")       # the AI is still on the drawings
        self.assertEqual(milestones(checks={"blocked": 1})["stage"], "drawings")
        self.assertEqual(milestones(phase1_complete=False)["stage"], "phase1")
        self.assertEqual(milestones(result_exists=False)["stage"], "phase2")
        self.assertEqual(milestones()["stage"], "results")

    def test_an_out_of_date_result_is_kept_but_the_job_goes_back_to_phase2(self):
        facts = milestones(result_stale=True)
        self.assertEqual((facts["stage"], facts["results_ready"], facts["result_stale"], facts["phase2_complete"]),
                         ("phase2", True, True, False))
        self.assertFalse(milestones(result_exists=False, result_stale=True)["result_stale"])  # nothing to be out of date

    def test_needs_you_counts_missing_areas_only_once_the_ai_has_stopped_looking(self):
        rooms = [(True, False), (False, True), (False, False)]
        self.assertEqual(milestones(rooms=rooms)["needs_you"]["phase1"], 2)
        self.assertEqual(milestones(rooms=rooms, areas_still_being_found=True)["needs_you"]["phase1"], 1)
        self.assertEqual(milestones(checks={"blocked": 3}, open_needs=4)["needs_you"],
                         {"drawings": 3, "phase1": 0, "phase2": 4, "results": 0})


class StageBlockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "job", "review_dir": str(self.root), "reasoning_packet": "x"}
        (self.root / "page_inventory.json").write_text(json.dumps({"pages": {"1": {}}}))

    def test_an_earlier_step_still_working_or_restarting_blocks_a_later_one(self):
        (self.root / "page_extraction_job.json").write_text(json.dumps({"status": "running"}))
        self.assertEqual(job_progress.stage_blocker(self.project, "rooms"), "Waiting for reading equipment to finish.")
        self.assertEqual(job_progress.stage_blocker(self.project, "equipment"), "")   # only earlier steps count
        (self.root / "page_extraction_job.json").write_text(json.dumps({"status": "done", "restart_requested": True}))
        self.assertIn("reading equipment", job_progress.stage_blocker(self.project, "envelope"))
        (self.root / "page_extraction_job.json").write_text(json.dumps({"status": "failed"}))
        self.assertEqual(job_progress.stage_blocker(self.project, "envelope"), "")

    def test_nothing_runs_before_the_drawings_are_prepared(self):
        self.assertEqual(job_progress.stage_blocker({**self.project, "reasoning_packet": ""}, "rooms"),
                         "Prepare the drawing set first.")


class ListAndJobAgreeTests(unittest.TestCase):
    def test_the_jobs_list_and_the_job_screen_give_the_same_stage_for_an_out_of_date_result(self):
        # Until 2026-10-19 each had its own rule: the list called a stale result "not ready" while the job screen
        # called it ready, so the list and the job's own steps disagreed about where the job was.
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = {"id": "job", "name": "Cafe.pdf", "review_dir": folder, "reasoning_packet": "x"}
            (root / "ai_preliminary_input_set.json").write_text(json.dumps({"input_fingerprint": "now"}))
            (root / "phase1_base_room_load_report.json").write_text(json.dumps({
                "preliminary_input_set_fingerprint": "now", "phase1_coverage": {"complete": True}, "blocked_reasons": []}))
            (root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps({
                "preliminary_input_set_fingerprint": "before", "included_scope_peak": {"final_design_total_kw": 9.0}}))
            model = {"status": "stale", "room_scope": {"status": "confirmed", "candidates": []},
                     "phase1_base_room_load_report": {"phase1_coverage": {"complete": True}, "blocked_reasons": []},
                     "phase1_base_room_load_report_stale": False,
                     "hourly_ai_preliminary_load_report": {"included_scope_peak": {"final_design_total_kw": 9.0}}}
            from backend import ai_preliminary_service, autonomous_tasks_service
            with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
                    patch.object(autonomous_tasks_service, "_task_progress", return_value={"waiting": 0, "blocked": 0}), \
                    patch.object(ai_preliminary_service, "get", return_value=model):
                listed = job_service.project_list_summary(SimpleNamespace(), project)
                opened = job_service.status(SimpleNamespace(), project)
        keys = ("stage", "drawings_confirmed", "phase1_complete", "phase2_complete", "results_ready", "result_stale",
                "needs_you")
        self.assertEqual({key: listed[key] for key in keys}, {key: opened[key] for key in keys})
        self.assertEqual((opened["stage"], opened["results_ready"], opened["result_stale"]), ("phase2", True, True))


if __name__ == "__main__":
    unittest.main()
