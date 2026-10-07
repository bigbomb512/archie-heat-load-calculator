#!/usr/bin/env python3
"""Runtime room-use findings enter resolvers only after current-run acceptance."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import skill_workflow_service
from backend.ai_preliminary_service import _proposal_for_resolution


class AcceptedSkillBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archie-skill-bridge-")
        self.root = Path(self.temp.name)
        self.run_id = "run-1"
        self.rooms = [
            {"kind": "room", "room_id": "room-use:unassigned-level:shop", "label": "Shop", "level_name": "Unassigned level", "page": 20},
            {"kind": "room", "room_id": "room-use:unassigned-level:bar", "label": "Bar", "level_name": "Unassigned level", "page": 20},
            {"kind": "room", "room_id": "room-use:unassigned-level:kitchen", "label": "Kitchen", "level_name": "Unassigned level", "page": 20},
        ]
        self.run_path = self.root / "ai_preliminary_run.json"
        self.run_path.write_text(json.dumps({"local_room_inference_proposal": {"rooms": self.rooms}}))
        for name, value in {
            "ai_input.json": {"drawing_set": {"pages": [{"page": 20, "title": "Plan"}] }},
            "drawing_coverage.json": {"page_roles": [{"page": 20, "proposed_role": "main_floor_plan"}]},
            "spatial_ocr.json": {"pages": []}, "vector_geometry.json": {"pages": []},
            "vision_response.json": {}, "vision_extraction_settings.json": {"owner_opt_in": True, "selected_group_ids": []},
        }.items():
            (self.root / name).write_text(json.dumps(value))
        self.skill_dir = self.root / "skill_workflow_runs" / self.run_id / "proposals"
        self.skill_dir.mkdir(parents=True)
        self.identity = {
            "citations": [{"citation_id": "c19", "physical_pdf_page": 19, "drawing_identity": "H509 / 201 / B",
                           "title": "PROPOSED FLOOR LAYOUT", "excerpt_or_crop": "140-seat schedule"}],
            "proposal_fields": {"rooms": [
                {"room_id": self.rooms[0]["room_id"], "original_label": "Shop", "taxonomy_id": "dining", "evidence_page_ids": [19]},
                {"room_id": self.rooms[1]["room_id"], "original_label": "Bar", "taxonomy_id": "dining", "evidence_page_ids": [19]},
                {"room_id": self.rooms[2]["room_id"], "original_label": "Kitchen", "taxonomy_id": "kitchen", "evidence_page_ids": [19]},
            ]},
            "inferences": [
                {"field": f"rooms[{self.rooms[0]['room_id']}].taxonomy_id", "value": "dining", "method": "Hospitality furniture.", "confidence": .9},
                {"field": f"rooms[{self.rooms[1]['room_id']}].taxonomy_id", "value": {"taxonomy_id": "dining", "boundary_status": "Functional zone; independent room separation unresolved"}, "method": "Functional area only.", "confidence": .8},
                {"field": f"rooms[{self.rooms[2]['room_id']}].taxonomy_id", "value": "kitchen", "method": "Cooking fixtures and notes.", "confidence": .97},
            ],
            "unresolved_fields": [{"field": f"rooms[{self.rooms[0]['room_id']}].original_label", "reason": "No visible source label."}],
            "observations": [{"citation_ids": ["c19"], "detail": "Drawing 201 seating schedule lists 140 seats."}],
        }
        (self.skill_dir / "room_identity_use.json").write_text(json.dumps(self.identity))
        (self.skill_dir / "room_boundaries_areas.json").write_text(json.dumps({"proposal_fields": {"geometry_candidates": []}}))
        self.source_fingerprint = skill_workflow_service._source_fingerprint(
            skill_workflow_service._project_paths({"id": "bridge-test", "review_dir": str(self.root)}),
            skill_workflow_service.load_catalog())
        (self.root / "skill_workflow_run.json").write_text(json.dumps({"run_id": self.run_id,
            "scope": "pdf_review", "status": "needs_review", "source_fingerprint": self.source_fingerprint,
            "subskills": {"room_identity_use": {"status": "needs_review"}}}))
        self.paths = {"root": self.root, "run": self.run_path}

    def tearDown(self):
        self.temp.cleanup()

    def _decision(self, index, status, value=None, *, source_fingerprint=None, run_id=None):
        path = self.root / "skill_review_decisions.json"
        data = json.loads(path.read_text()) if path.exists() else {"decisions": {}}
        key = f"room_identity_use:rooms:{index}"
        data["decisions"][key] = {"status": status, "value": value if value is not None else self.identity["proposal_fields"]["rooms"][index],
            "run_id": run_id or self.run_id, "source_fingerprint": source_fingerprint or self.source_fingerprint}
        path.write_text(json.dumps(data))

    def _proposal(self):
        return _proposal_for_resolution(self.paths)

    def test_unaccepted_findings_leave_room_use_identical_to_no_skill_run(self):
        manifest = self.root / "skill_workflow_run.json"
        baseline = json.dumps(self._proposal(), sort_keys=True)
        self.assertNotIn("room_use_category", self._proposal()["rooms"][0])
        self.assertEqual(json.dumps(self._proposal(), sort_keys=True), baseline)
        manifest.unlink()
        no_run = json.dumps(self._proposal(), sort_keys=True)
        manifest.write_text(json.dumps({"run_id": self.run_id, "status": "needs_review", "source_fingerprint": self.source_fingerprint}))
        self.assertEqual(json.dumps(self._proposal(), sort_keys=True), no_run)

    def test_accepted_finding_is_used(self):
        self._decision(0, "accepted")
        shop = self._proposal()["rooms"][0]
        self.assertEqual(shop["room_use_category"], "dining")
        self.assertEqual(shop["evidence"][-1]["page"], 19)

    def test_edited_accepted_finding_replaces_the_proposed_value(self):
        edited = {**self.identity["proposal_fields"]["rooms"][0], "taxonomy_id": "office"}
        self._decision(0, "accepted", edited)
        self.assertEqual(self._proposal()["rooms"][0]["room_use_category"], "office")

    def test_rejected_finding_is_ignored(self):
        self._decision(0, "rejected")
        self.assertNotIn("room_use_category", self._proposal()["rooms"][0])

    def test_acceptance_is_ignored_after_pdf_fingerprint_changes(self):
        self._decision(0, "accepted")
        (self.root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 20, "title": "Revised plan"}]}}))
        self.assertNotIn("room_use_category", self._proposal()["rooms"][0])


if __name__ == "__main__":
    unittest.main()
