#!/usr/bin/env python3
"""Calculate as one server-side job: answers, model, rooms, calculate; inputs prepared when missing."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from backend import ai_preliminary_service, calculation_service as service, job_service

SCOPE = {"candidate_fingerprint": "fp", "candidates": [
    {"key": "kitchen", "area_m2": 104.9, "include": True},
    {"key": "shop", "area_m2": 216.1, "include": True},
    {"key": "store", "area_m2": None, "include": False}]}


class FakeWeb:
    def __init__(self, root):
        self.project = {"id": "job", "review_dir": str(root), "reasoning_packet": "x"}
        self.updated = 0

    def project_by_id(self, project_id):
        return dict(self.project)

    def update_project(self, project):
        self.updated += 1


def finish(project, seconds=5):
    deadline = time.monotonic() + seconds
    while service._JOB.key(project) in service._RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    return service.status(None, project)


class CalculationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps({"included_scope_peak": {"final_design_total_kw": 34.4}}))
        self.web = FakeWeb(self.root)
        self.calls = []

    def tearDown(self):
        service._RUNNING.clear()
        self.temp.cleanup()

    def patches(self, assemble_errors=()):
        errors = list(assemble_errors)

        def assemble(web, project):
            self.calls.append("model")
            if errors:
                raise errors.pop(0)

        return [patch.object(ai_preliminary_service, "_assemble", side_effect=assemble),
                patch.object(ai_preliminary_service, "_room_scope_state", return_value=SCOPE),
                patch.object(ai_preliminary_service, "_confirm_room_scope",
                             side_effect=lambda paths, data: self.calls.append(("rooms", data["reviewer"], data["candidate_fingerprint"], data["rows"]))),
                patch.object(ai_preliminary_service, "_calculate", side_effect=lambda web, project: self.calls.append("calculate")),
                patch.object(job_service, "apply_roof_answer", side_effect=lambda web, project: self.calls.append("answers")),
                patch.object(service, "_resolve_inputs", side_effect=lambda web, project_id: self.calls.append("inputs"))]

    def run_job(self, data, assemble_errors=()):
        active = self.patches(assemble_errors)
        for item in active:
            item.start()
        try:
            started = service.start(self.web, self.web.project, data)
            return started, finish(self.web.project)
        finally:
            for item in active:
                item.stop()

    def test_the_steps_run_in_order_with_the_persons_room_choices_and_report_the_total(self):
        job_service.save_job_setup(self.web.project, {"above": "floor"})
        started, done = self.run_job({"include": {"shop": False, "store": True}, "reviewer": "Sam"})
        self.assertEqual(started["status"], "running")
        self.assertEqual(done["status"], "done")
        self.assertEqual((done["total_kw"], done["inputs_resolved"], done["requested_by"]), (34.4, False, "Sam"))
        self.assertEqual(self.calls[0], "answers")
        self.assertEqual(self.calls[1], "model")
        rooms = self.calls[2]
        self.assertEqual(rooms[:3], ("rooms", "Sam", "fp"))
        # Shop unticked; the store has no area, so it can't be cooled even when ticked; the kitchen keeps the default.
        self.assertEqual(rooms[3], [{"key": "kitchen", "include": True, "reason": ""},
                                    {"key": "shop", "include": False, "reason": "Not cooled"},
                                    {"key": "store", "include": False, "reason": "Not cooled"}])
        self.assertEqual(self.calls[3], "calculate")
        self.assertEqual(self.web.updated, 1)

    def test_missing_inputs_are_prepared_on_the_server_then_the_steps_run_again(self):
        _, done = self.run_job({}, assemble_errors=[ValueError("Missing required artifacts: room_use_resolution.")])
        self.assertEqual(done["status"], "done")
        self.assertTrue(done["inputs_resolved"])
        self.assertEqual([call if isinstance(call, str) else call[0] for call in self.calls],
                         ["model", "inputs", "model", "rooms", "calculate"])

    def test_other_errors_fail_the_job_with_their_reason_and_skip_the_resolver(self):
        _, failed = self.run_job({}, assemble_errors=[ValueError("Analyse the PDF before assembling an AI preliminary model.")])
        self.assertEqual((failed["status"], failed["step"]), ("failed", "model"))
        self.assertIn("Analyse the PDF", failed["error"])
        self.assertNotIn("inputs", self.calls)

    def test_no_cooled_room_with_an_area_stops_before_calculating(self):
        _, failed = self.run_job({"include": {"kitchen": False, "shop": False}})
        self.assertEqual(failed["status"], "failed")
        self.assertIn("No room has an area", failed["error"])
        self.assertNotIn("calculate", self.calls)

    def test_two_project_entries_on_one_folder_share_one_run(self):
        import threading
        gate = threading.Event()
        twin = {**self.web.project, "id": "job-copy"}            # a second entry pointing at the same folder
        active = self.patches()
        for item in active:
            item.start()
        try:
            with patch.object(job_service, "apply_roof_answer", side_effect=lambda web, project: gate.wait(5)):
                job_service.save_job_setup(self.web.project, {"above": "floor"})
                first = service.start(self.web, self.web.project, {})
                second = service.start(self.web, twin, {})
                gate.set()
                done = finish(self.web.project)
        finally:
            for item in active:
                item.stop()
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(done["status"], "done")

    def test_a_run_owned_by_another_live_server_process_is_joined_not_replaced(self):
        import os
        from backend import job_runner
        (self.root / service.JOB_FILE).write_text(json.dumps({"job_id": "other", "status": "running", "step": "model", "pid": os.getpid() + 100000}))
        with patch.object(job_runner, "_process_alive", side_effect=lambda pid: pid == os.getpid() + 100000):
            self.assertEqual(service.status(None, self.web.project)["status"], "running")
            joined = service.start(self.web, self.web.project, {})
        self.assertTrue(joined["deduplicated"])
        self.assertEqual(joined["job_id"], "other")
        with patch.object(job_runner, "_process_alive", return_value=False):
            self.assertEqual(service.status(None, self.web.project)["status"], "interrupted")

    def test_bad_room_choices_are_refused_and_a_restart_shows_interrupted(self):
        with self.assertRaisesRegex(ValueError, "true or false"):
            service.start(self.web, self.web.project, {"include": {"shop": "no"}})
        (self.root / service.JOB_FILE).write_text(json.dumps({"job_id": "old", "status": "running", "step": "calculate"}))
        self.assertEqual(service.status(None, self.web.project)["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
