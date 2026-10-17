import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend import new_job_service


def _wait_until_finished(web, project, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = new_job_service._JOB.status(new_job_service._with_folder(web, project))
        if job.get("status") not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    raise AssertionError("the new-job run did not finish")


class NewJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = {"p1": {"id": "p1", "name": "Shop fit-out.pdf", "analysed": False}}
        self.analysed = threading.Event()
        sheets = [{"page": 1, "type": "cover", "relevant": False, "selected_by_default": False},
                  {"page": 2, "type": "floor_plan", "plan_role": "main_floor_plan", "relevant": True},
                  {"page": 3, "type": "reflected_ceiling_plan", "relevant": True},
                  {"page": 4, "type": "elevation", "relevant": False, "selected_by_default": True}]

        def analyse_project(project):
            self.projects[project["id"]] = {**project, "analysed": True, "packet": "packet.json",
                                            "review_dir": str(Path(self.tmp.name) / project["id"]), "pages": 4}
            self.analysed.set()
            return self.projects[project["id"]]

        self.web = SimpleNamespace(
            WEB_REVIEW=self.tmp.name,
            project_by_id=lambda project_id: dict(self.projects[project_id]),
            analyse_project=analyse_project,
            analysis_response=lambda project: {"sheets": sheets})

    def test_default_pages_follow_the_workspace_choice(self):
        pages = new_job_service.default_pages(self.web, self.projects["p1"])
        self.assertEqual([(row["page"], row["decision"]) for row in pages],
                         [(2, "Confirm as floor plan"), (3, "Confirm as RCP"), (4, "Keep as reference")])

    def test_start_reads_the_pdf_then_prepares_the_default_pages(self):
        with patch.object(new_job_service.page_preparation_service, "start",
                          return_value={"job_id": "prep-1"}) as prepare, \
             patch.object(new_job_service.page_preparation_service, "status", return_value={"status": "none"}):
            new_job_service.start(self.web, self.projects["p1"])
            job = _wait_until_finished(self.web, self.projects["p1"])
        self.assertTrue(self.analysed.is_set())
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["preparation_job_id"], "prep-1")
        sent = prepare.call_args.args[2]
        self.assertEqual([row["page"] for row in sent["pages"]], [2, 3, 4])
        self.assertEqual(sent["requested_by"], "New job")

    def test_an_analysed_job_is_not_read_again_and_a_prepared_one_is_left_alone(self):
        self.projects["p1"] = {**self.projects["p1"], "analysed": True, "packet": "packet.json"}
        with patch.object(new_job_service.page_preparation_service, "start", return_value={}) as prepare, \
             patch.object(new_job_service.page_preparation_service, "status", return_value={"status": "none"}):
            new_job_service.start(self.web, self.projects["p1"])
            _wait_until_finished(self.web, self.projects["p1"])
            self.assertFalse(self.analysed.is_set())
            self.assertEqual(prepare.call_count, 1)
            ready = {**self.projects["p1"], "reasoning_packet": "packet.json"}
            result = new_job_service.start(self.web, ready)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual((result["state"], result["drawings_ready"]), ("done", True))

    def test_a_failed_reading_is_reported_with_its_reason(self):
        def broken(project):
            raise RuntimeError("The PDF could not be opened.")
        self.web.analyse_project = broken
        with patch.object(new_job_service.page_preparation_service, "status", return_value={"status": "none"}):
            new_job_service.start(self.web, self.projects["p1"])
            _wait_until_finished(self.web, self.projects["p1"])
            result = new_job_service.status(self.web, self.projects["p1"])
        self.assertEqual(result["state"], "failed")
        self.assertIn("could not be opened", result["error"])

    def test_status_says_running_while_page_preparation_runs(self):
        with patch.object(new_job_service.page_preparation_service, "status",
                          return_value={"status": "running", "step_label": "Preparing the drawings"}):
            result = new_job_service.status(self.web, {**self.projects["p1"], "analysed": True, "packet": "p"})
        self.assertEqual((result["state"], result["step_label"]), ("running", "Preparing the drawings"))


if __name__ == "__main__":
    unittest.main()
