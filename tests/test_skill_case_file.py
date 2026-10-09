#!/usr/bin/env python3
"""Skills on their case files (ideas 1, 3 and 4): what a skill is given, how it is called, and the full run."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from backend import page_inventory_service as pass1, skill_case_file, skill_workflow_service as skills
from tests.test_skill_workflow_service import Web, empty_typed_proposal

READINGS = {
    "1": {"page_type": "cover_or_drawing_list", "title": "COVER", "level": "", "information": [
        {"kind": "location_orientation", "what": "Tenancy MZ01, Melrose Central", "evidence": "title block"}]},
    "5": {"page_type": "schedule", "title": "EQUIPMENT SCHEDULE", "level": "", "information": [
        {"kind": "equipment_appliances", "what": "E06 Combi oven x1", "evidence": "row E06"},
        {"kind": "equipment_appliances", "what": "E21 UB fridge x10", "evidence": "row E21"}]},
    "20": {"page_type": "floor_plan", "title": "DIMENSION PLAN", "level": "Ground", "information": [
        {"kind": "room_geometry", "what": "Shop 120 m2", "evidence": "label"},
        {"kind": "equipment_appliances", "what": "Combi oven under hood", "evidence": "kitchen"}]},
    "26": {"page_type": "elevation", "title": "SHOPFRONT ELEVATION", "level": "", "information": [
        {"kind": "windows_glazing", "what": "Shopfront 11,900 mm wide", "evidence": "dimension chain"}]},
}


class CaseFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
        renders = self.root / pass1.WORK_DIR / "pages"
        renders.mkdir(parents=True)
        for page in (1, 5, 20, 26):
            (renders / f"p-{page:02d}.png").write_bytes(b"png")
        (self.root / "page_extraction.json").write_text(json.dumps({"equipment_appliances": {"findings": [
            {"value": {"code": "E06", "name": "COMBI OVEN", "quantity": 1, "size": ""}, "pages": [5, 20],
             "evidence": "supported", "conflicts": {}, "text_match": True}]}}))
        self.project = {"id": "job", "review_dir": str(self.root)}

    def tearDown(self):
        self.temp.cleanup()

    def test_a_skill_gets_the_whole_index_its_own_readings_the_extracted_values_and_its_key_pages(self):
        case, images = skill_case_file.build("equipment_evidence", self.project)
        self.assertEqual([row.split(" | ")[0] for row in case["page_index"]], ["1", "5", "20", "26"])  # one line per page
        self.assertEqual([row.split(" — ")[0] for row in case["readings"]],
                         ["p5 equipment_appliances: E06 Combi oven x1", "p5 equipment_appliances: E21 UB fridge x10",
                          "p20 equipment_appliances: Combi oven under hood"])
        self.assertEqual(case["extracted"]["equipment_appliances"][0]["value"]["code"], "E06")
        self.assertEqual(case["attached_pages"], [5, 20])                 # most relevant readings first
        self.assertEqual([path.name for path in images], ["p-05.png", "p-20.png"])
        glazing, _ = skill_case_file.build("glazing_properties", self.project)
        self.assertEqual([row.split(" ")[0] for row in glazing["readings"]], ["p26"])
        needs, needs_images = skill_case_file.build("information_needs", self.project)
        self.assertEqual((len(needs["readings"]), needs_images), (6, []))  # every kind, as text only

    def test_a_skill_is_worth_a_call_only_when_a_page_shows_its_kinds(self):
        self.assertTrue(skill_case_file.worth_a_call("equipment_evidence", self.project))      # page 5 and 20
        self.assertFalse(skill_case_file.worth_a_call("plant_detection", self.project))        # no HVAC plant anywhere
        self.assertFalse(skill_case_file.worth_a_call("pump_inputs", self.project))
        self.assertTrue(skill_case_file.worth_a_call("sheet_identity", self.project))          # document mapping always runs

    def test_the_route_needs_page_reading_on_and_pages_read(self):
        self.assertTrue(skill_case_file.available(self.project))
        pass1.set_enabled(None, self.project, {"enabled": False})
        self.assertFalse(skill_case_file.available(self.project))
        pass1.set_enabled(None, self.project, {"enabled": True})
        (self.root / pass1.RESULT_FILE).write_text("{}")
        self.assertFalse(skill_case_file.available(self.project))


class FakeProvider:
    def __init__(self, error=None):
        self.prompts, self.images, self.error = [], [], error

    def propose(self, prompt, image_paths=()):
        self.prompts.append(prompt)
        self.images.append(list(image_paths))
        if self.error:
            raise self.error
        subskill_id = prompt.split("Subskill: ", 1)[1].split(" ", 1)[0]
        subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == subskill_id)
        return {"status": "needs_review", "affected_ids": [], "observations": [], "inferences": [], "citations": [],
                "confidence": None, "alternatives": [], "unresolved_fields": [], "remediation": [],
                "proposal_fields": empty_typed_proposal(subskill)}


class CallTests(CaseFileTests):
    def tearDown(self):
        skills.CASE_FILE_PROVIDER_FACTORY = None
        skills._USAGE_STOPPED.clear()
        super().tearDown()

    def subskill(self, subskill_id):
        return next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == subskill_id)

    def test_the_prompt_carries_the_playbook_and_the_case_file_and_the_key_pages_are_attached(self):
        provider = FakeProvider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        record = {"raw_record": {}}
        raw = skills._call_case_file_provider(self.subskill("equipment_evidence"), self.project, {}, {}, record)
        self.assertEqual(raw["status"], "needs_review")
        prompt = provider.prompts[0]
        self.assertIn("reconcile repeated schematic and\n  schedule sightings", prompt)   # its playbook section
        self.assertIn("### Evidence discipline", prompt)                                    # the shared rules
        self.assertIn('"case_file"', prompt)
        self.assertIn("E21 UB fridge x10", prompt)
        self.assertEqual([Path(path).name for path in provider.images[0]], ["p-05.png", "p-20.png"])
        self.assertEqual((record["provider"], record["evidence_pages"]), ("codex_case_file", [5, 20]))

    def test_after_the_usage_limit_no_more_skills_are_sent(self):
        limit = skills.SkillProviderError("codex_cli_failed", {"stderr_tail": "ERROR: You've hit your usage limit. try again at 6:01 PM."})
        provider = FakeProvider(error=limit)
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        for subskill_id in ("equipment_evidence", "lighting_evidence"):
            with self.assertRaises(skills.SkillProviderError) as raised:
                skills._call_case_file_provider(self.subskill(subskill_id), self.project, {}, {}, {"raw_record": {}})
            self.assertEqual(str(raised.exception), "skill_provider_usage_limit")
            self.assertIn("resets at 6:01 PM", raised.exception.detail)
        self.assertEqual(len(provider.prompts), 1)


class ReuseCarryTests(unittest.TestCase):
    def test_results_an_interrupted_run_never_reached_stay_reusable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "skill_workflow_runs" / "new" / "proposals").mkdir(parents=True)
            (root / "skill_workflow_runs" / "new" / "proposals" / "sheet_identity.json").write_text("{}")
            interrupted = {"run_id": "new", "status": "running",
                           "reuse_from": {"run_id": "old", "subskills": {
                               "equipment_evidence": {"run_id": "old", "input_fingerprint": "e" * 64, "status": "needs_review",
                                                      "proposal": "skill_workflow_runs/old/proposals/equipment_evidence.json"},
                               "sheet_identity": {"run_id": "old", "input_fingerprint": "s" * 64, "status": "needs_review"}}},
                           "subskills": {"equipment_evidence": {"status": "queued"},
                                         "sheet_identity": {"status": "needs_review", "input_fingerprint": "t" * 64}}}
            table = skills._reuse_table(skills._project_paths({"review_dir": folder}), interrupted)["subskills"]
        self.assertEqual(table["equipment_evidence"]["run_id"], "old")          # never reached: carried forward
        self.assertEqual(table["sheet_identity"]["input_fingerprint"], "t" * 64)  # finished in the run: its own result wins
        self.assertEqual(table["sheet_identity"]["fallback"]["input_fingerprint"], "s" * 64)  # the older one kept as fallback

    def test_a_retry_whose_inputs_match_the_fallback_reuses_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "skill_workflow_runs" / "old" / "proposals").mkdir(parents=True)
            (root / "skill_workflow_runs" / "old" / "proposals" / "schedule_evidence.json").write_text(json.dumps(
                {"status": "needs_review", "proposal_fields": {"schedules": []}}))
            (root / "skill_workflow_run.json").write_text(json.dumps({"reuse_from": {"subskills": {"schedule_evidence": {
                "run_id": "new", "input_fingerprint": "x" * 64, "proposal": "skill_workflow_runs/new/proposals/schedule_evidence.json",
                "fallback": {"run_id": "old", "input_fingerprint": "y" * 64,
                             "proposal": "skill_workflow_runs/old/proposals/schedule_evidence.json"}}}}}))
            subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "schedule_evidence")
            reused = skills._reused_proposal(subskill, {"id": "j", "review_dir": folder}, "y" * 64)
            unmatched = skills._reused_proposal(subskill, {"id": "j", "review_dir": folder}, "z" * 64)
        self.assertEqual(reused["reused_from"], "old")
        self.assertIsNone(unmatched)


class RunTests(unittest.TestCase):
    def test_a_changed_builder_rebuilds_its_skill_and_unaffected_ai_skills_are_reused(self):
        provider = FakeProvider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(skills, "_ensure_room_evidence", return_value={"artifact_names": [], "candidate_count": 0, "model_input": {}}):
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}}))
                (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": p} for p in (1, 5, 20, 26)]}))
                (root / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
                project = {"id": "rebuild-" + root.name, "review_dir": str(root)}
                wait = lambda: [time.sleep(0.05) for _ in range(600) if project["id"] in skills._RUNNING]
                skills.start_after_reading(Web(), project); wait()
                before = len(provider.prompts)
                with patch.dict(skills._CODE_BUILT_VERSIONS, {"room_boundaries_areas": "traced_rooms_v2"}):
                    skills.post(Web(), project, {"action": "retry", "scope": "pdf_review"}); wait()
                manifest = json.loads((root / "skill_workflow_run.json").read_text())
            self.assertFalse(manifest["subskills"]["room_boundaries_areas"].get("reused_from"))   # rebuilt by the new builder
            self.assertTrue(manifest["subskills"]["equipment_evidence"].get("reused_from"))       # AI result kept
            # Only skills after it are asked again (a prerequisite's fingerprint is part of each dependant's inputs).
            registry = {row["id"]: row for row in skills.load_subskill_registry()["subskills"]}
            after = {"room_boundaries_areas"}
            while True:
                more = {skill_id for skill_id, row in registry.items() if set(row["depends_on"]) & after} - after
                if not more:
                    break
                after |= more
            again = {prompt.split("Subskill: ", 1)[1].split(" ", 1)[0] for prompt in provider.prompts[before:]}
            self.assertTrue(again)
            self.assertLessEqual(again, after)
        finally:
            skills.CASE_FILE_PROVIDER_FACTORY = None

    def test_a_job_with_its_pages_read_runs_the_review_skills_in_order_ending_with_the_needs_list(self):
        provider = FakeProvider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}}))
                (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}))
                (root / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
                project = {"id": "case-file-run-" + root.name, "review_dir": str(root)}
                # Room detection runs before the room skills; it needs a real analysed job, so it is stood in for here.
                with patch.object(skills, "_ensure_room_evidence", return_value={"artifact_names": [], "candidate_count": 0, "model_input": {}}):
                    started = skills.start_after_reading(Web(), project)
                    deadline = time.monotonic() + 30
                    while project["id"] in skills._RUNNING and time.monotonic() < deadline:
                        time.sleep(0.05)
                self.assertNotEqual(started["status"], "blocked")
                manifest = json.loads((root / "skill_workflow_run.json").read_text())
            called = [prompt.split("Subskill: ", 1)[1].split(" ", 1)[0] for prompt in provider.prompts]
            self.assertIn("equipment_evidence", called)
            needs = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "information_needs")
            self.assertTrue(all(called.index(dependency) < called.index("information_needs") for dependency in needs["depends_on"]
                                if dependency in called))                 # runs after everything it depends on
            self.assertGreaterEqual(len(set(needs["depends_on"]) & set(called)), 10)
            self.assertLess(called.index("room_identity_use"), called.index("equipment_evidence"))
            self.assertEqual(manifest["subskills"]["information_needs"]["status"], "needs_review")
            self.assertNotIn("address_confirmation", called)               # confirmation stays with the operators
            # Document mapping (from the page reading) and room boundaries (from traced outlines) are built in code.
            self.assertFalse({"sheet_identity", "revision_scope", "page_relationships", "room_boundaries_areas"} & set(called))
            self.assertEqual(manifest["subskills"]["sheet_identity"]["status"], "needs_review")
            # Skills the needs list doesn't depend on aren't part of the default review and make no call.
            self.assertFalse({"plant_detection", "circuit_mapping", "pump_inputs", "airflow_deduplication", "surface_area",
                              "shading", "zone_ownership"} & set(called))
            self.assertEqual(manifest["subskills"]["plant_detection"]["status"], "not_in_scope")
        finally:
            skills.CASE_FILE_PROVIDER_FACTORY = None


if __name__ == "__main__":
    unittest.main()


class AnswerTests(unittest.TestCase):
    def test_answers_need_text_and_a_source_and_only_attach_to_listed_needs(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "answers", "review_dir": folder}
            need = {"id": "information_needs:needs:0", "subskill_id": "information_needs", "field": "needs",
                    "value": {"target": "E06 Combi oven", "field": "rated_input_w"}}
            other = {"id": "equipment_evidence:equipment:0", "subskill_id": "equipment_evidence", "field": "equipment", "value": {}}
            with patch.object(skills, "_response", side_effect=lambda web, project: {"findings": [need, other], "answers": json.loads(
                    (Path(folder) / skills.ANSWERS_FILE).read_text())["answers"] if (Path(folder) / skills.ANSWERS_FILE).exists() else {}}):
                for data, message in (({"finding_id": need["id"], "answer": "", "source": "client"}, "Type the answer"),
                                      ({"finding_id": need["id"], "answer": "18 kW", "source": "guess"}, "where the answer came from"),
                                      ({"finding_id": other["id"], "answer": "18 kW", "source": "client"}, "isn't on the current list")):
                    with self.assertRaisesRegex(ValueError, message):
                        skills.post(Web(), project, {"action": "answer_need", **data})
                state = skills.post(Web(), project, {"action": "answer_need", "finding_id": need["id"], "answer": " 18  kW ",
                                                     "source": "spec_sheet", "reviewer": "Sam"})
            saved = state["answers"][need["id"]]
            self.assertEqual((saved["answer"], saved["source"], saved["target"], saved["by"]), ("18 kW", "spec_sheet", "E06 Combi oven", "Sam"))


class ResumeTests(unittest.TestCase):
    def run_to_end(self, project):
        deadline = time.monotonic() + 30
        while project["id"] in skills._RUNNING and time.monotonic() < deadline:
            time.sleep(0.05)

    def test_a_retry_reuses_finished_skills_and_calls_only_the_failed_one_and_its_dependents(self):
        class FlakyProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.fail = {"equipment_evidence"}

            def propose(self, prompt, image_paths=()):
                subskill_id = prompt.split("Subskill: ", 1)[1].split(" ", 1)[0]
                if subskill_id in self.fail:
                    self.prompts.append(prompt)
                    raise skills.SkillProviderError("codex_cli_failed", {"stderr_tail": "network down"})
                return super().propose(prompt, image_paths)

        provider = FlakyProvider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        called = lambda prompts: [prompt.split("Subskill: ", 1)[1].split(" ", 1)[0] for prompt in prompts]
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(skills, "_ensure_room_evidence", return_value={"artifact_names": [], "candidate_count": 0, "model_input": {}}):
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}}))
                (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}))
                (root / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
                project = {"id": "resume-" + root.name, "review_dir": str(root)}
                skills.start_after_reading(Web(), project)
                self.run_to_end(project)
                first = json.loads((root / "skill_workflow_run.json").read_text())
                self.assertEqual(first["status"], "failed")
                self.assertEqual(first["subskills"]["equipment_evidence"]["status"], "failed")
                # A failed prerequisite doesn't block what depends on it: schedules still ran, told it was unavailable.
                self.assertNotEqual(first["subskills"]["schedule_evidence"]["status"], "blocked")
                self.assertEqual(skills._prompt_budget_chars("lighting_evidence", case_file=True), 120_000)
                self.assertEqual(skills._prompt_budget_chars("lighting_evidence"), 80_000)
                first_calls = len(provider.prompts)
                # The operator accepted a lighting finding on the first run.
                decision_id = "lighting_evidence:lighting:0"
                (root / "skill_review_decisions.json").write_text(json.dumps({"decisions": {decision_id: {
                    "status": "accepted", "run_id": first["run_id"], "source_fingerprint": first["source_fingerprint"]}}}))
                provider.fail.clear()
                skills.post(Web(), project, {"action": "retry"})
                self.run_to_end(project)
                second = json.loads((root / "skill_workflow_run.json").read_text())
            retried = called(provider.prompts[first_calls:])
            needs = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "information_needs")
            self.assertIn("equipment_evidence", retried)
            failed_first = {name for name, row in first["subskills"].items() if row.get("status") == "failed"}
            self.assertTrue(set(retried) <= {"equipment_evidence", "schedule_evidence", "information_needs"} | failed_first, retried)
            self.assertNotIn("room_identity_use", retried)                       # finished before: reused, no call
            self.assertEqual(second["subskills"]["room_identity_use"]["reused_from"], first["run_id"])
            self.assertEqual(second["subskills"]["lighting_evidence"]["reused_from"], first["run_id"])
            self.assertEqual(second["subskills"]["equipment_evidence"].get("reused_from", ""), "")
            self.assertNotEqual(second["subskills"]["equipment_evidence"]["status"], "failed")   # the retried skill succeeds
            self.assertTrue(skills._decision_is_current({"run_id": first["run_id"], "source_fingerprint": second["source_fingerprint"]},
                                                        second, second["source_fingerprint"], "lighting_evidence"))
            self.assertFalse(skills._decision_is_current({"run_id": first["run_id"], "source_fingerprint": second["source_fingerprint"]},
                                                         second, second["source_fingerprint"], "equipment_evidence"))
            self.assertIn("information_needs", needs["depends_on"] + ["information_needs"])
        finally:
            skills.CASE_FILE_PROVIDER_FACTORY = None


class JobContextTests(unittest.TestCase):
    def test_every_case_file_carries_the_source_identity_id_formats_known_rooms_and_room_types_where_assigned(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
            (Path(folder) / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "room-use:ground:kitchen", "original_label": "Kitchen", "level": "Ground", "taxonomy_id": "kitchen",
                 "space_scope": "comfort_hvac_with_process_exception"}]}))
            case, _ = skill_case_file.build("equipment_evidence", {"id": "j", "review_dir": folder, "pdf": "/x/Butcher Buffet.pdf"})
            rooms, _ = skill_case_file.build("room_identity_use", {"id": "j", "review_dir": folder, "pdf": "/x/Butcher Buffet.pdf"})
        job = case["job"]
        self.assertNotIn("room_types", job)                          # only the task that assigns room types gets them
        self.assertIn("kitchen", {row["taxonomy_id"] for row in rooms["job"]["room_types"]})
        self.assertTrue(job["source_document"]["source_document_id"].startswith("pdf-"))
        self.assertEqual((job["source_document"]["file_name"], job["source_document"]["page_count"]), ("Butcher Buffet.pdf", 4))
        self.assertIn("room-use:ground:kitchen", job["id_formats"]["room_id"])
        self.assertEqual(job["known_rooms"][0]["room_id"], "room-use:ground:kitchen")
        self.assertIn("Do not refuse", job["when_prerequisites_are_empty"])

    def test_with_page_reading_on_the_skills_may_see_every_page(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / pass1.RESULT_FILE).write_text(json.dumps({"pages": READINGS}))
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": page} for page in (1, 5, 20, 26)]}))
            paths = skills._project_paths({"id": "j", "review_dir": folder})
            self.assertEqual(skills._consented_page_ids(paths), {1, 5, 20, 26})
            pass1.set_enabled(None, {"id": "j", "review_dir": folder}, {"enabled": False})
            self.assertEqual(skills._consented_page_ids(paths), set())


class RecheckTests(unittest.TestCase):
    def test_a_reply_that_only_failed_checking_is_rechecked_on_retry_and_a_still_bad_one_is_asked_again(self):
        class Provider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.bad = {"lighting_evidence"}

            def propose(self, prompt, image_paths=()):
                subskill_id = prompt.split("Subskill: ", 1)[1].split(" ", 1)[0]
                reply = super().propose(prompt, image_paths)
                if subskill_id in self.bad:
                    reply["proposal_fields"] = {"lighting": "not a list"}     # fails the type check
                return reply

        provider = Provider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        called = lambda prompts: [prompt.split("Subskill: ", 1)[1].split(" ", 1)[0] for prompt in prompts]
        try:
            with tempfile.TemporaryDirectory() as folder, \
                    patch.object(skills, "_ensure_room_evidence", return_value={"artifact_names": [], "candidate_count": 0, "model_input": {}}):
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 5, "title": "EQUIPMENT SCHEDULE"}]}}))
                (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": p} for p in (1, 5, 20, 26)]}))
                (root / pass1.RESULT_FILE).write_text(json.dumps({"pages": {**READINGS, "3": {"page_type": "reflected_ceiling_plan",
                    "information": [{"kind": "lighting", "what": "12 downlights", "evidence": "legend"}]}}}))
                project = {"id": "recheck-" + root.name, "review_dir": str(root)}
                run = lambda action: (skills.post(Web(), project, {"action": action, "scope": "pdf_review"}) if action == "retry"
                                      else skills.start_after_reading(Web(), project))
                wait = lambda: [time.sleep(0.05) for _ in range(600) if project["id"] in skills._RUNNING]
                run("start"); wait()
                first = json.loads((root / "skill_workflow_run.json").read_text())
                self.assertEqual(first["subskills"]["lighting_evidence"]["status"], "failed")
                before = len(provider.prompts)
                run("retry"); wait()                                   # still bad: asked again (one new call), fails again
                self.assertEqual(called(provider.prompts[before:]).count("lighting_evidence"), 1)
                # Now pretend the checks were fixed: the stored reply is good, so the retry uses it without a call.
                provider.bad.clear()
                second = json.loads((root / "skill_workflow_run.json").read_text())
                attempt = root / second["subskills"]["lighting_evidence"]["attempt_ref"] / "raw_output.txt"
                subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "lighting_evidence")
                from tests.test_skill_workflow_service import empty_typed_proposal
                attempt.write_text(json.dumps({"status": "needs_review", "affected_ids": [], "observations": [], "inferences": [],
                    "citations": [], "confidence": None, "alternatives": [], "unresolved_fields": [], "remediation": [],
                    "proposal_fields": empty_typed_proposal(subskill)}))
                before = len(provider.prompts)
                run("retry"); wait()
                third = json.loads((root / "skill_workflow_run.json").read_text())
            self.assertNotIn("lighting_evidence", called(provider.prompts[before:]))
            self.assertEqual(third["subskills"]["lighting_evidence"]["status"], "needs_review")
        finally:
            skills.CASE_FILE_PROVIDER_FACTORY = None


class TrimTests(CaseFileTests):
    def tearDown(self):
        skills.CASE_FILE_PROVIDER_FACTORY = None
        super().tearDown()

    def test_the_duplicate_page_index_is_dropped_and_an_oversized_case_file_is_trimmed_before_blocking(self):
        provider = FakeProvider()
        skills.CASE_FILE_PROVIDER_FACTORY = lambda: provider
        subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "equipment_evidence")
        record = {"raw_record": {}}
        skills._call_case_file_provider(subskill, self.project, {}, {"page_index": [{"page": 1, "title": "x" * 500}]}, record)
        self.assertNotIn("x" * 500, provider.prompts[-1])                       # shared copy of the index left out
        # Squeeze the cap so the full case file doesn't fit but a trimmed one does.
        full = len(provider.prompts[-1])
        with patch.object(skills, "_CASE_FILE_PROMPT_BUDGET_CHARS", full - 50), patch.object(skills, "_PROMPT_BUDGET_CHARS", 1000):
            record = {"raw_record": {}}
            skills._call_case_file_provider(subskill, self.project, {}, {}, record)
        self.assertEqual(record["prompt_budget"]["case_file_trimmed"][0], "readings")
        self.assertNotIn("E21 UB fridge x10", provider.prompts[-1])
        self.assertIn('"job"', provider.prompts[-1])                            # the job section always stays
        with patch.object(skills, "_CASE_FILE_PROMPT_BUDGET_CHARS", 2000), patch.object(skills, "_PROMPT_BUDGET_CHARS", 1000):
            record = {"raw_record": {}}
            self.assertIsNone(skills._call_case_file_provider(subskill, self.project, {}, {}, record))   # nothing fits: blocked, no call
        self.assertEqual(len(provider.prompts), 2)
