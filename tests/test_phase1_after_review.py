"""Phase 1 starts on the server when the envelope review finishes, so nobody has to have the Drawings page open.
No AI is called: the calculation start and the model read are fakes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import ai_preliminary_service, calculation_service, skill_workflow_service as service


class App:
    def __init__(self, project):
        self.project = project

    def project_by_id(self, _project_id):
        return self.project


class Phase1AfterReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = {"id": "p1", "review_dir": self.tmp.name}
        self.app = App(self.project)

    def finish_review(self, status="needs_review", scope="phase1_envelope", run_id="r1"):
        (Path(self.tmp.name) / "skill_workflow_run.json").write_text(
            json.dumps({"run_id": run_id, "scope": scope, "status": status}))

    def run_trigger(self, base_report=None, stale=False, run_id="r1"):
        model = {"phase1_base_room_load_report": base_report or {"status": "not_calculated"},
                 "phase1_base_room_load_report_stale": stale}
        with patch.object(ai_preliminary_service, "get", return_value=model), \
                patch.object(calculation_service, "start", return_value={"status": "queued"}) as start:
            result = service.start_phase1_after_review(self.app, self.project, run_id)
        return result, start

    def test_a_finished_review_starts_phase1_on_the_server(self):
        for status in ("completed", "needs_review", "failed", "blocked"):
            with self.subTest(status=status):
                self.finish_review(status)
                result, start = self.run_trigger()
                self.assertEqual(result, {"status": "queued"})
                start.assert_called_once()
                self.assertEqual(start.call_args.args[2]["phase"], "phase1_base_room")

    def test_an_out_of_date_phase1_result_is_recalculated(self):
        self.finish_review()
        _result, start = self.run_trigger(base_report={"status": "done"}, stale=True)
        start.assert_called_once()

    def test_a_current_phase1_result_is_left_alone(self):
        self.finish_review()
        result, start = self.run_trigger(base_report={"status": "done"}, stale=False)
        self.assertIsNone(result)
        start.assert_not_called()

    def test_only_a_finished_phase1_envelope_review_of_this_run_starts_it(self):
        cases = [("needs_review", "pdf_review", "r1"), ("running", "phase1_envelope", "r1"),
                 ("needs_review", "phase1_envelope", "r2")]       # a newer run replaced this one
        for status, scope, saved_run in cases:
            with self.subTest(status=status, scope=scope, saved_run=saved_run):
                self.finish_review(status, scope, saved_run)
                result, start = self.run_trigger(run_id="r1")
                self.assertIsNone(result)
                start.assert_not_called()

    def test_a_failure_to_start_is_logged_not_raised(self):
        self.finish_review()
        with patch.object(ai_preliminary_service, "get", side_effect=RuntimeError("model unreadable")), \
                patch.object(calculation_service, "start") as start, self.assertLogs(service.LOGGER, "ERROR"):
            self.assertIsNone(service.start_phase1_after_review(self.app, self.project, "r1"))
        start.assert_not_called()

    def test_the_review_worker_calls_it_when_it_finishes(self):
        self.finish_review(run_id="other")                   # the worker returns at once: not its run
        with patch.object(service, "start_phase1_after_review") as trigger, \
                patch.object(service, "load_subskill_registry", return_value={"subskills": []}):
            service._run_worker(self.app, self.project, "r1", "fp", {"enabled_skill_ids": []})
        trigger.assert_called_once_with(self.app, self.project, "r1")


if __name__ == "__main__":
    unittest.main()
