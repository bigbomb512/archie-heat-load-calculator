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
        self.assertEqual([row["page"] for row in case["page_index"]], [1, 5, 20, 26])
        self.assertEqual([(row["page"], row["what"]) for row in case["readings"]],
                         [(5, "E06 Combi oven x1"), (5, "E21 UB fridge x10"), (20, "Combi oven under hood")])
        self.assertEqual(case["extracted"]["equipment_appliances"][0]["value"]["code"], "E06")
        self.assertEqual(case["attached_pages"], [5, 20])                 # most relevant readings first
        self.assertEqual([path.name for path in images], ["p-05.png", "p-20.png"])
        glazing, _ = skill_case_file.build("glazing_properties", self.project)
        self.assertEqual([row["page"] for row in glazing["readings"]], [26])
        needs, needs_images = skill_case_file.build("information_needs", self.project)
        self.assertEqual((len(needs["readings"]), needs_images), (6, []))  # every kind, as text only

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


class RunTests(unittest.TestCase):
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
