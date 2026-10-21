"""Recovery must not overlap workers, lose cached work, or unlock calculation early."""
import json
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import calculation_service, ai_preliminary_service, skill_workflow_service
from backend.job_runner import BackgroundJob, JobCancelled, read_json, write_json, _write_json


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "test", "review_dir": str(self.root), "reasoning_packet": "prepared"}
        self.job = BackgroundJob("test_job.json", [("read", "Reading")], "test-recovery", "Interrupted")

    def wait(self):
        deadline = time.monotonic() + 3
        while self.job.key(self.project) in self.job.running and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertNotIn(self.job.key(self.project), self.job.running)
        return self.job.status(self.project)

    def test_restart_waits_reuses_reply_and_suppresses_old_publication(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls, workers, publications = [], [], []
        cache, published = self.root / "reply-cache.json", self.root / "published.json"

        def work(web, project_id, step):
            workers.append(threading.get_ident())
            if not cache.exists():
                calls.append("provider")
                entered.set()
                self.assertTrue(release.wait(2))
                _write_json(cache, {"reply": "paid-for evidence"})
            # Ordinary publication is guarded, even if the service forgot a checkpoint.
            write_json(published, read_json(cache))
            publications.append("published")
            return {}

        first = self.job.start(None, self.project, {}, work)
        self.assertTrue(entered.wait(1))
        for _ in range(5):
            stopping = self.job.start(None, self.project, {}, work, action="restart")
            self.assertEqual(stopping["job_id"], first["job_id"])
            self.assertEqual(stopping["status"], "stopping")
        self.assertFalse(published.exists())
        self.assertEqual(len(workers), 1)
        release.set()
        done = self.wait()
        self.assertEqual(done["status"], "done")
        self.assertNotEqual(first["job_id"], done["job_id"])
        self.assertEqual((len(workers), len(calls), len(publications)), (2, 1, 1))
        self.assertEqual(read_json(published)["reply"], "paid-for evidence")

    def test_late_duplicate_restart_does_not_cancel_the_replacement(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            entered.set()
            release.wait(2)
            return {}
        current = self.job.start(None, self.project, {}, work)
        self.assertTrue(entered.wait(1))
        duplicate = self.job.request_restart(self.project, expected_run_id="earlier-run")
        self.assertTrue(duplicate["deduplicated"])
        self.assertEqual(duplicate["job_id"], current["job_id"])
        self.assertEqual(duplicate["status"], "running")
        self.assertFalse(read_json(self.job.path(self.project)).get("restart_requested"))
        release.set()
        self.wait()

    def test_resume_joins_active_run_and_alias_folder_deduplicates(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            entered.set()
            release.wait(2)
            return {}
        first = self.job.start(None, self.project, {}, work)
        self.assertTrue(entered.wait(1))
        joined = self.job.start(None, {**self.project, "id": "alias"}, {}, work, action="resume")
        self.assertTrue(joined["deduplicated"])
        self.assertEqual(first["job_id"], joined["job_id"])
        self.assertEqual(joined["status"], "running")
        release.set()
        self.wait()

    def test_orphaned_restart_is_resumed_after_server_restart(self):
        _write_json(self.job.path(self.project), {"job_id": "orphan", "status": "running", "pid": -1,
                                                "restart_requested": True})
        self.assertEqual(self.job.status(self.project)["recovery"]["label"], "Try again")
        self.job.start(None, self.project, {}, lambda *args: {}, action="resume")
        self.assertEqual(self.wait()["status"], "done")

    def test_stalled_restart_remains_pending_without_a_second_worker(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def work(*args):
            entered.set()
            release.wait(2)
            return {}
        self.job.start(None, self.project, {}, work)
        self.assertTrue(entered.wait(1))
        self.job.start(None, self.project, {}, work, action="restart")
        current = read_json(self.job.path(self.project))
        _write_json(self.job.path(self.project), {**current, "restart_requested_at": time.time()-700})
        status = self.job.status(self.project)
        self.assertTrue(status["restart_stalled"])
        self.assertIsNone(status["recovery"]["action"])
        release.set()
        self.wait()

    def test_restart_request_is_honoured_by_another_server_process(self):
        script = r"""
import sys, time
from pathlib import Path
from backend.job_runner import BackgroundJob, write_json, _write_json, read_json
root = Path(sys.argv[1])
project = {"id": "child", "review_dir": str(root)}
job = BackgroundJob("test_job.json", [("read", "Reading")], "child", "Interrupted")
count = [0]
def work(web, project_id, step):
    count[0] += 1
    if count[0] == 1:
        (root / "entered").touch()
        deadline = time.monotonic() + 5
        while not (root / "release").exists() and time.monotonic() < deadline:
            time.sleep(.005)
    write_json(root / "published.json", {"run": count[0]})
    return {}
job.start(None, project, {}, work)
deadline = time.monotonic() + 7
while job.running and time.monotonic() < deadline:
    time.sleep(.005)
print(count[0])
"""
        child = subprocess.Popen([sys.executable, "-c", script, str(self.root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        deadline = time.monotonic() + 2
        while not (self.root / "entered").exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue((self.root / "entered").exists())
        stopped = self.job.request_restart(self.project)
        self.assertEqual(stopped["status"], "stopping")
        self.assertTrue(self.job.start(None, self.project, {}, lambda *args: self.fail("Overlapping worker"))["deduplicated"])
        (self.root / "release").touch()
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, error)
        self.assertEqual(output.strip(), "2")
        self.assertEqual(read_json(self.root / "published.json"), {"run": 2})

    def test_skill_restart_drains_inflight_tasks_without_publishing_and_keeps_scope(self):
        skills = skill_workflow_service
        catalog = skills.load_catalog()
        run = skills._new_manifest(catalog, "fp", "pdf_review")
        _write_json(self.root / "skill_workflow_run.json", run)
        _write_json(self.root / "ai_input.json", {})
        skills._RUNNING.add(self.project["id"])
        self.addCleanup(skills._RUNNING.discard, self.project["id"])
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        replacements = []
        original_post = skills.post
        def execute(*args):
            entered.set()
            release.wait(2)
            return {"input_fingerprint": "fp", "status": "provisional"}
        def post(web, project, data):
            if data.get("action") == "retry":
                replacements.append(data)
                return {}
            return original_post(web, project, data)
        def response(web, project):
            state = read_json(self.root / "skill_workflow_run.json")
            return {"status": "stopping" if state.get("restart_requested") else state["status"]}
        with patch.object(skills, "_scoped_subskill_ids", return_value={"sheet_identity"}), \
                patch.object(skills, "_execute_subskill", side_effect=execute), \
                patch.object(skills, "_case_file_route", return_value=False), \
                patch.object(skills, "_response", side_effect=response), \
                patch.object(skills, "_persist_skill_attempt") as publish, \
                patch.object(skills, "start_phase1_after_review") as calculate, \
                patch.object(skills, "post", side_effect=post):
            worker = threading.Thread(target=skills._run_worker, args=(None, self.project, run["run_id"], "fp", catalog), daemon=True)
            worker.start()
            self.assertTrue(entered.wait(1))
            for _ in range(3):
                state = skills.post(None, self.project, {"action": "restart", "scope": "phase1_envelope"})
                self.assertEqual(state["status"], "stopping")
            self.assertEqual(replacements, [])
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive(), "Skill restart deadlocked")
            publish.assert_not_called()
            calculate.assert_not_called()
        self.assertEqual(replacements, [{"action": "retry", "scope": "phase1_envelope"}])


    def test_cancelled_skill_reply_is_cached_and_rechecked_without_another_call(self):
        skills = skill_workflow_service
        task = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "surface_area")
        cancelled = threading.Event()
        def check():
            if cancelled.is_set():
                raise JobCancelled()
        def provider(*args):
            cancelled.set()
            return {"status": "provisional", "proposal_fields": {"surfaces": []}}
        with patch.object(skills, "_shared_evidence_packet", return_value=({}, [])), \
                patch.object(skills, "_case_file_route", return_value=True), \
                patch.object(skills, "_worth_a_call", return_value=True), \
                patch.object(skills, "_GROUP_OF", {}), \
                patch.object(skills, "_reused_proposal", return_value=None), \
                patch.object(skills, "_call_case_file_provider", side_effect=provider) as calls:
            first = skills._run_checked_subskill(check, task, self.project, {}, "source-a")
            with self.assertRaises(JobCancelled):
                check()
            cancelled.clear()
            cached = skills._run_checked_subskill(check, task, self.project, {}, "source-a")
            self.assertEqual(first["proposal_fields"], cached["proposal_fields"])
            self.assertTrue(cached["_attempt_record"]["raw_record"]["recovered_reply"])
            self.assertEqual(calls.call_count, 1)
            skills._run_checked_subskill(check, task, self.project, {}, "source-b")
            self.assertEqual(calls.call_count, 2, "Changed evidence must invalidate the cached reply")


    def test_rejected_skill_reply_is_not_reused_forever_on_retry(self):
        skills = skill_workflow_service
        catalog = skills.load_catalog()
        _write_json(self.root / "vision_extraction_settings.json", {"owner_opt_in": True})
        with patch.object(skills, "_scoped_subskill_ids", return_value={"sheet_identity"}), \
                patch.object(skills, "_shared_evidence_packet", return_value=({}, [])), \
                patch.object(skills, "_case_file_route", return_value=False), \
                patch.object(skills, "_consented_page_ids", return_value={1}), \
                patch.object(skills, "SKILL_PROVIDER_FACTORY", lambda model: None), \
                patch.object(skills, "_call_skill_provider", return_value={"status": "provisional", "proposal_fields": {"sheets": "invalid"}}) as provider:
            for _ in range(2):
                run = skills._new_manifest(catalog, "source-a", "phase1_envelope")
                _write_json(self.root / "skill_workflow_run.json", run)
                skills._run_worker(None, self.project, run["run_id"], "source-a", catalog)
                self.assertEqual(read_json(self.root / "skill_workflow_run.json")["status"], "failed")
                self.assertEqual(list((self.root / "skill_reply_cache").glob("*.json")), [])
            # Each new Phase 1 run retries once through its targeted supervisor after
            # the baseline reply is rejected; the rejected reply is not a cache hit.
            self.assertEqual(provider.call_count, 2)


    def test_initial_status_endpoints_work_before_the_upload_has_a_review_folder(self):
        from backend import web_app
        project = {"id": "new-upload", "name": "Plan.pdf", "pages": 38}
        request = SimpleNamespace(path="/api/status?project_id=new-upload")
        with patch.object(web_app, "project_by_id", return_value=project), \
                patch.object(web_app, "WEB_REVIEW", self.root), \
                patch.object(web_app, "update_project") as persist:
            for endpoint in [web_app.api_calculation_status, web_app.api_page_reading_status,
                             web_app.api_drawing_tasks_status, web_app.api_page_extraction_status,
                             web_app.api_skill_workflow]:
                reply = endpoint(request)
                self.assertIn("recovery", reply)
                self.assertNotEqual(reply.get("status"), "running")
            persist.assert_not_called()
        self.assertFalse((self.root / project["id"]).exists())



class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "r", "review_dir": str(self.root), "reasoning_packet": "ready"}
        self.model = {"room_scope": {"candidates": [
            {"key": "shop", "model_room_id": "shop", "status": "calculated", "area_m2": 40},
            {"key": "store", "status": "no_area", "area_m2": None}]}}
        self.get = patch.object(ai_preliminary_service, "get", return_value=self.model).start()
        self.addCleanup(patch.stopall)
        _write_json(self.root / "skill_workflow_run.json", {"run_id": "r1", "scope": "phase1_envelope", "status": "failed"})
        _write_json(self.root / "ai_preliminary_input_set.json", {"material": {"hourly_load_model": {"rooms": [
            {"room_id": "shop", "cooling_load": {"envelope_surfaces": [{"surface_id": "wall"}]}}]}}})

        from backend import phase1_supervisor_service as supervisor
        _write_json(self.root / supervisor.JOB_FILE, {"status": "done", "source_fingerprint": supervisor.source_fingerprint(self.project)})

    def test_screenshot_combination_cannot_calculate_even_after_a_long_wait(self):
        _write_json(self.root / "page_inventory_job.json", {"status": "running", "started_at": "2000-01-01"})
        _write_json(self.root / "skill_workflow_run.json", {"status": "blocked"})
        state = calculation_service.status(None, self.project)
        self.assertFalse(state["readiness"]["ready"])
        self.assertFalse(state["offer_start"])
        self.assertIn("reading the pages", " ".join(state["readiness"]["reasons"]))
        with self.assertRaisesRegex(ValueError, "Waiting for reading"):
            calculation_service.start(None, self.project, {"phase": "phase1_base_room"})
        self.assertFalse((self.root / "calculation_job.json").exists())

    def test_partial_evidence_can_calculate_and_missing_rooms_stay_exceptions(self):
        state = calculation_service.phase1_readiness(None, self.project)
        self.assertTrue(state["ready"])
        self.assertEqual(state["usable_rooms"], ["shop"])
        self.assertEqual(state["exceptions"], ["store"])
        self.assertFalse(calculation_service.status(None, self.project)["offer_start"])

    def test_area_without_envelope_is_not_ready_and_internal_room_is_valid(self):
        path = self.root / "ai_preliminary_input_set.json"
        inputs = read_json(path)
        inputs["material"]["hourly_load_model"]["rooms"][0]["cooling_load"] = {}
        _write_json(path, inputs)
        self.assertFalse(calculation_service.phase1_readiness(None, self.project)["ready"])
        inputs["material"]["hourly_load_model"]["rooms"][0]["cooling_load"] = {"envelope_not_applicable": True}
        _write_json(path, inputs)
        self.assertTrue(calculation_service.phase1_readiness(None, self.project)["ready"])

    def test_any_pending_restart_blocks_calculation(self):
        _write_json(self.root / "drawing_tasks_job.json", {"status": "done", "restart_requested": True})
        self.assertFalse(calculation_service.phase1_readiness(None, self.project)["ready"])

    def test_duplicate_manual_calculation_reuses_a_current_result(self):
        self.model["phase1_base_room_load_report"] = {"status": "draft", "room_peaks": [{"name": "Shop"}]}
        self.model["phase1_base_room_load_report_stale"] = False
        result = calculation_service.start(None, self.project, {"phase": "phase1_base_room"})
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["status"], "done")
        self.assertFalse((self.root / "calculation_job.json").exists())

    def test_automatic_start_failure_offers_manual_recovery_only_for_current_review(self):
        with patch.object(calculation_service, "start", side_effect=RuntimeError("Could not launch worker")):
            self.assertIsNone(calculation_service.maybe_start_phase1(object(), self.project))
        self.assertTrue(calculation_service.status(None, self.project)["offer_start"])
        manifest = read_json(self.root / "skill_workflow_run.json")
        _write_json(self.root / "skill_workflow_run.json", {**manifest, "run_id": "r2"})
        self.assertFalse(calculation_service.status(None, self.project)["offer_start"])

    def test_startup_recovers_missed_handoff_once_but_does_not_retry_interrupted_calculation(self):
        class Web:
            def load_projects(_self):
                return {"r": self.project, "alias": {**self.project, "id": "alias"}}
        with patch.object(calculation_service, "maybe_start_phase1") as recover:
            calculation_service.reconcile_finished_reviews(Web())
            recover.assert_called_once()
        _write_json(self.root / "calculation_job.json", {"status": "running", "pid": -1})
        with patch.object(calculation_service, "maybe_start_phase1") as recover:
            calculation_service.reconcile_finished_reviews(Web())
            recover.assert_not_called()



if __name__ == "__main__":
    unittest.main()
