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
        self.project = {"id": "p1", "review_dir": self.tmp.name, "reasoning_packet": "prepared"}
        self.app = App(self.project)
        from backend import phase1_supervisor_service as supervisor
        (Path(self.tmp.name) / supervisor.JOB_FILE).write_text(json.dumps({"status": "done",
            "source_fingerprint": supervisor.source_fingerprint(self.project)}))

    def finish_review(self, status="needs_review", scope="phase1_envelope", run_id="r1"):
        (Path(self.tmp.name) / "skill_workflow_run.json").write_text(
            json.dumps({"run_id": run_id, "scope": scope, "status": status}))
        # A settled model check follows the review; fingerprint its actual input run.
        from backend import phase1_supervisor_service as supervisor
        path = Path(self.tmp.name) / supervisor.JOB_FILE
        if path.exists():
            checked = json.loads(path.read_text())
            checked["source_fingerprint"] = supervisor.source_fingerprint(self.project)
            path.write_text(json.dumps(checked))

    def run_trigger(self, base_report=None, stale=False, run_id="r1"):
        model = {"phase1_base_room_load_report": base_report or {"status": "not_calculated"},
                 "phase1_base_room_load_report_stale": stale}
        with patch.object(calculation_service, "phase1_readiness", return_value={"ready": True}), patch.object(ai_preliminary_service, "get", return_value=model), \
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

    def test_finished_skill_review_starts_supervision_before_calculation(self):
        from backend import phase1_supervisor_service as supervisor
        self.finish_review()
        (Path(self.tmp.name) / supervisor.JOB_FILE).unlink()
        with patch.object(supervisor, "start", return_value={"status": "running"}) as check, \
                patch.object(calculation_service, "start") as calculate:
            result = service.start_phase1_after_review(self.app, self.project, "r1")
        self.assertIsNone(result)
        check.assert_called_once()
        calculate.assert_not_called()

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
        with patch.object(calculation_service, "phase1_readiness", return_value={"ready": True}), patch.object(ai_preliminary_service, "get", side_effect=RuntimeError("model unreadable")), \
                patch.object(calculation_service, "start") as start, self.assertLogs(service.LOGGER, "ERROR"):
            self.assertIsNone(service.start_phase1_after_review(self.app, self.project, "r1"))
        start.assert_not_called()

    def test_the_review_worker_calls_it_when_it_finishes(self):
        self.finish_review(run_id="other")                   # the worker returns at once: not its run
        with patch.object(service, "start_phase1_after_review") as trigger, \
                patch.object(service, "load_subskill_registry", return_value={"subskills": []}):
            service._run_worker(self.app, self.project, "r1", "fp", {"enabled_skill_ids": []})
        trigger.assert_called_once_with(self.app, self.project, "r1")


class TakeInDownstreamTests(unittest.TestCase):
    """Later steps rebuild the review's dependency files from its own results; that must not make it stale. Only what
    the run's skills read counts, and only a real change to it (or to their instructions)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = {"id": "p2", "review_dir": self.tmp.name}
        self.write("room_use_resolution.json", {"records": [{"room_id": "r-store", "taxonomy_id": "store"}]})
        self.write("site_location_resolution.json", {"status": "proposed", "address": "139A Burns Road"})
        self.write("geometry_resolution.json", {"room_geometry_proofs": [{"room_id": "r-store", "area_m2": 7.8}]})
        proposals = self.root / "skill_workflow_runs" / "r1" / "proposals"
        proposals.mkdir(parents=True)
        for skill, names in {"room_identity_use": ["room_use_resolution.json"],
                             "address_confirmation": ["site_location_resolution.json"],
                             "sheet_identity": ["drawing_coverage.json"]}.items():
            (proposals / f"{skill}.json").write_text(json.dumps({"evidence_artifact_names": names}))
        manifest = {"run_id": "r1", "scope": "phase1_envelope", "status": "needs_review",
                    "subskills": {"room_identity_use": {"status": "needs_review"}, "address_confirmation": {"status": "needs_review"},
                                  "sheet_identity": {"status": "needs_review"}, "surface_area": {"status": "not_in_scope"}}}
        service._record_dependencies(service._project_paths(self.project), manifest)
        self.write("skill_workflow_run.json", manifest)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def stale(self):
        manifest = json.loads((self.root / "skill_workflow_run.json").read_text())
        return bool(service._dependency_changes(service._project_paths(self.project), manifest)
                    or service._instruction_changes(manifest))

    def test_only_the_files_the_skills_read_are_fingerprinted(self):
        manifest = json.loads((self.root / "skill_workflow_run.json").read_text())
        self.assertEqual(sorted(manifest["dependency_fingerprints"]), ["room_use_resolution.json", "site_location_resolution.json"])
        self.assertEqual(manifest["dependency_basis"], service.DEPENDENCY_BASIS)
        self.assertEqual(sorted(manifest["instruction_fingerprints"]), ["address_confirmation", "room_identity_use", "sheet_identity"])

    def test_a_later_step_rewriting_geometry_or_rebuilding_room_uses_keeps_the_review_current(self):
        # Turramurra (2026-11-18): the empty-room check rewrote geometry_resolution.json and the review went stale.
        self.write("geometry_resolution.json", {"room_geometry_proofs": [{"room_id": "r-store", "area_m2": 5.8}]})
        self.write("room_use_resolution.json", {"records": [{"room_id": "r-store", "taxonomy_id": "store", "status": "rebuilt"}]})
        self.write("model_input_resolution.json", {"records": ["rebuilt by the calculation"]})
        self.assertFalse(self.stale())

    def test_a_typed_override_or_a_changed_consumed_file_still_counts(self):
        self.write("room_use_resolution.json", {"records": [{"room_id": "r-store", "override": {"taxonomy_id": "office"}}]})
        self.assertTrue(self.stale())
        self.assertTrue(service.take_in_downstream(self.project, "r1"))
        self.assertFalse(self.stale())
        self.write("site_location_resolution.json", {"status": "confirmed", "address": "141 Burns Road"})
        self.assertTrue(self.stale())

    def test_a_changed_skill_instruction_makes_it_stale(self):
        manifest = json.loads((self.root / "skill_workflow_run.json").read_text())
        manifest["instruction_fingerprints"]["room_identity_use"] = "an older instruction"
        self.write("skill_workflow_run.json", manifest)
        self.assertTrue(self.stale())

    def test_the_status_reports_stale_only_for_real_changes(self):
        class Web:
            def safe_link(self, path):
                return str(path)
        self.write("vision_extraction_settings.json", {"owner_opt_in": True})
        with patch.object(service, "_orphaned", return_value=False):
            self.write("geometry_resolution.json", {"room_geometry_proofs": []})
            self.assertEqual(service._response(Web(), self.project)["status"], "needs_review")
            self.write("site_location_resolution.json", {"status": "confirmed"})
            reply = service._response(Web(), self.project)
        self.assertEqual(reply["status"], "stale")
        self.assertIn("site_location_resolution.json", " ".join(reply.get("stale_reasons", [])))

    def test_a_run_saved_with_the_old_seventeen_file_fingerprints_ignores_files_its_skills_did_not_read(self):
        manifest = json.loads((self.root / "skill_workflow_run.json").read_text())
        manifest.pop("dependency_basis")
        manifest.pop("instruction_fingerprints")
        manifest["dependency_fingerprints"] = {name: service._fingerprint(service._read(self.root / name, {}))
                                               for name in service._DOMAIN_ARTIFACTS}
        self.write("skill_workflow_run.json", manifest)
        self.write("geometry_resolution.json", {"room_geometry_proofs": []})
        self.write("room_use_resolution.json", {"records": ["rebuilt"]})
        self.assertFalse(self.stale())
        self.write("site_location_resolution.json", {"status": "confirmed"})
        self.assertTrue(self.stale())

    def test_another_run_or_an_unfinished_one_is_left_alone(self):
        self.write("site_location_resolution.json", {"status": "changed"})
        self.assertFalse(service.take_in_downstream(self.project, "r0"))
        manifest = json.loads((self.root / "skill_workflow_run.json").read_text())
        self.write("skill_workflow_run.json", {**manifest, "status": "running"})
        self.assertFalse(service.take_in_downstream(self.project, "r1"))
        self.write("skill_workflow_run.json", manifest)
        self.assertTrue(self.stale())


if __name__ == "__main__":
    unittest.main()
