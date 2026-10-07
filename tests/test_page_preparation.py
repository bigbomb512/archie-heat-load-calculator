#!/usr/bin/env python3
"""Server-side page preparation: one background job instead of browser-driven steps."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from backend import autonomous_tasks_service, page_preparation_service as service

PAGES = [{"page": 20, "detected_type": "floor_plan", "decision": "Confirm as floor plan"},
         {"page": 22, "detected_type": "reflected_ceiling_plan", "decision": "Confirm as RCP"}]


class FakeWeb:
    def __init__(self, root, fail_on=None, gate=None):
        self.project = {"id": "job", "name": "Corner cafe.pdf", "review_dir": str(root), "packet": "x"}
        self.calls, self.fail_on, self.gate = [], fail_on, gate

    def project_by_id(self, project_id):
        assert project_id == "job"
        return dict(self.project)

    def save_page_decisions(self, project, data):
        if self.gate:
            self.gate.wait(5)
        self.calls.append(("pages", [row["page"] for row in data["pages"]]))
        if self.fail_on == "pages":
            raise ValueError("Page 20 could not be read.")
        return {}

    def start_without_ai_evidence(self, project):
        self.calls.append(("drawings",))
        if self.fail_on == "drawings":
            return {"has_reasoning_packet": False}
        return {"has_reasoning_packet": True}


def wait_until_finished(project, seconds=5):
    deadline = time.monotonic() + seconds
    while service._JOB.key(project) in service._RUNNING and time.monotonic() < deadline:
        time.sleep(0.01)
    return service.status(None, project)


class PagePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        service._RUNNING.clear()
        self.temp.cleanup()

    def test_all_steps_run_on_the_server_in_order_and_the_job_reports_done(self):
        web = FakeWeb(self.root)
        with patch.object(autonomous_tasks_service, "run_all", side_effect=lambda w, p: web.calls.append(("checks",))):
            started = service.start(web, web.project, {"pages": PAGES, "requested_by": "Sam"})
            self.assertEqual(started["status"], "running")
            done = wait_until_finished(web.project)
        self.assertEqual(web.calls, [("pages", [20, 22]), ("drawings",), ("checks",)])
        self.assertEqual(done["status"], "done")
        self.assertEqual((done["pages"], done["requested_by"], done["steps"]), ([20, 22], "Sam", ["pages", "drawings", "checks"]))
        self.assertIn("seconds", done)
        saved = json.loads((self.root / service.JOB_FILE).read_text())
        self.assertEqual(saved["status"], "done")

    def test_a_failed_step_is_reported_with_its_reason_and_the_later_steps_do_not_run(self):
        web = FakeWeb(self.root, fail_on="pages")
        with patch.object(autonomous_tasks_service, "run_all") as run_all:
            service.start(web, web.project, {"pages": PAGES})
            failed = wait_until_finished(web.project)
        self.assertEqual((failed["status"], failed["step"], failed["error"]), ("failed", "pages", "Page 20 could not be read."))
        run_all.assert_not_called()
        web = FakeWeb(self.root, fail_on="drawings")
        with patch.object(autonomous_tasks_service, "run_all") as run_all:
            service.start(web, web.project, {"pages": PAGES})
            failed = wait_until_finished(web.project)
        self.assertEqual((failed["status"], failed["step"]), ("failed", "drawings"))
        self.assertIn("could not be prepared", failed["error"])
        run_all.assert_not_called()

    def test_a_second_start_while_running_joins_the_running_job(self):
        gate = threading.Event()
        web = FakeWeb(self.root, gate=gate)
        with patch.object(autonomous_tasks_service, "run_all"):
            first = service.start(web, web.project, {"pages": PAGES})
            second = service.start(web, web.project, {"pages": PAGES[:1]})
            self.assertTrue(second["deduplicated"])
            self.assertEqual(second["job_id"], first["job_id"])
            gate.set()
            done = wait_until_finished(web.project)
        self.assertEqual(done["status"], "done")
        self.assertEqual([call for call in web.calls if call[0] == "pages"], [("pages", [20, 22])])

    def test_a_job_left_running_by_a_server_restart_is_reported_as_interrupted(self):
        project = FakeWeb(self.root).project
        (self.root / service.JOB_FILE).write_text(json.dumps({"job_id": "old", "status": "running", "step": "drawings"}))
        state = service.status(None, project)
        self.assertEqual(state["status"], "interrupted")
        self.assertIn("Start it again", state["error"])
        self.assertEqual(service.status(None, {**project, "review_dir": str(self.root / "none")})["status"], "none")

    def test_page_rows_are_checked_and_the_checks_step_can_be_skipped(self):
        web = FakeWeb(self.root)
        for bad in ([], None, [{"page": 0}], [{"page": "20"}], [{"page": 20}, {"page": 20}], ["x"]):
            with self.assertRaisesRegex(ValueError, "page"):
                service.start(web, web.project, {"pages": bad})
        with patch.object(autonomous_tasks_service, "run_all") as run_all:
            service.start(web, web.project, {"pages": PAGES, "run_checks": False})
            done = wait_until_finished(web.project)
        run_all.assert_not_called()
        self.assertEqual((done["status"], done["steps"]), ("done", ["pages", "drawings"]))


if __name__ == "__main__":
    unittest.main()
