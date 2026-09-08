#!/usr/bin/env python3

"""Domain checks for the evidence-to-calculator bridge."""

from copy import deepcopy
import unittest

from ai.calculator_draft import DraftConflict, apply_calculator_draft, build_calculator_draft, save_review
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from ai.envelope import empty_envelope_library, empty_envelope_model


EVIDENCE = [{"page": 3, "kind": "reviewed_pdf_text", "excerpt": "Ground floor Shop A · AREA 42 m²"}]


def source_data():
    return {
        "thermal": {"zones": [{"name": "Shop A", "ceiling_height_mm": 3000, "status": "direct"}]},
        "building": {
            "source_pdf": "drawing-set.pdf", "levels": [],
            "spaces": [{"id": "spaces-3-1", "name": "Shop A", "area": "42 m²", "level_name": "Ground", "geometry": {"page": 3, "reference": "room-boundary-3-1"}, "geometry_status": "geometry_confirmed", "status": "direct", "evidence": EVIDENCE}],
            "lighting": [{"id": "lighting-3-1", "connected_w": 480, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "equipment": [{"id": "equipment-3-1", "name": "oven", "kind": "cooking", "quantity": 1, "watts": None, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "surfaces": [{"id": "surfaces-3-1", "kind": "external_boundary", "adjacency": "", "geometry": None, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "openings": [],
            "constructions": [{"id": "constructions-3-1", "kind": "roof", "reference": "Roof type R1", "thermal_performance": None, "status": "direct", "evidence": EVIDENCE}],
        },
        "coverage": {"levels": [{"level_name": "Ground", "purpose_status": "inferred", "purpose_evidence": EVIDENCE}]},
    }


class CalculatorDraftTests(unittest.TestCase):
    def build(self):
        data = source_data()
        original = deepcopy(data)
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        self.assertEqual(data, original, "Building a draft must not mutate source artifacts.")
        self.assertEqual(draft["schema_version"], 2)
        return draft

    def reviewed(self, draft, ids, decision="accept"):
        return save_review(draft, {cid: {"decision": decision, "reviewer": "ENG-1"} for cid in ids}, draft["revision"])

    def test_builds_stable_cited_topology_and_missing_review_items(self):
        data = source_data(); draft = self.build()
        rebuilt = build_calculator_draft(data["thermal"], data["building"], data["coverage"], draft)
        self.assertEqual(draft["candidates"]["rooms"][0]["candidate_id"], rebuilt["candidates"]["rooms"][0]["candidate_id"])
        self.assertEqual(draft["candidates"]["floors"][0]["citations"][0]["page"], 3)
        self.assertTrue(any("schedule" in item["reason"].lower() for item in draft["review_items"]))
        self.assertFalse(draft["candidates"]["schedules"])

    def test_approval_requires_fingerprint_and_application_is_idempotent(self):
        draft = self.build()
        ids = [item["candidate_id"] for key in ("floors", "zones", "rooms") for item in draft["candidates"][key]]
        reviewed = self.reviewed(draft, ids)
        outcome = apply_calculator_draft(reviewed, None, empty_hourly_load_model(), empty_schedule_library(), empty_envelope_library(), empty_envelope_model(), "requirements-r1")
        model = outcome["hourly_load_model"]
        self.assertEqual((len(model["floors"]), len(model["zones"]), len(model["rooms"])), (1, 1, 1))
        self.assertEqual(model["rooms"][0]["verification_status"], "confirmed")
        self.assertFalse(outcome["envelope_model"]["active_for_calculation"])
        repeated = apply_calculator_draft(reviewed, None, model, outcome["schedule_library"], outcome["envelope_library"], outcome["envelope_model"], "requirements-r1")
        self.assertFalse(repeated["changed"]["hourly_load_model"])
        self.assertEqual(len(repeated["summary"]["already_present"]), 3)

    def test_changed_evidence_invalidates_old_approval(self):
        data = source_data(); draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        floor = draft["candidates"]["floors"][0]; reviewed = self.reviewed(draft, [floor["candidate_id"]])
        data["building"]["spaces"][0]["evidence"][0]["excerpt"] = "Ground floor Shop A · AREA 45 m²"
        changed = build_calculator_draft(data["thermal"], data["building"], data["coverage"], reviewed)
        self.assertFalse(changed["decisions"])
        self.assertTrue(changed["review_history"])

    def test_existing_authored_record_is_never_overwritten(self):
        draft = self.build(); floor = draft["candidates"]["floors"][0]; reviewed = self.reviewed(draft, [floor["candidate_id"]])
        model = empty_hourly_load_model(); model["floors"] = [{"floor_id": floor["value"]["floor_id"], "name": "Authored floor", "elevation_m": 0, "verification_status": "confirmed", "source": "Engineer authored", "citations": []}]
        outcome = apply_calculator_draft(reviewed, None, model)
        self.assertEqual(outcome["hourly_load_model"]["floors"][0]["name"], "Authored floor")
        self.assertEqual(len(outcome["summary"]["skipped_conflicts"]), 1)

    def test_label_only_room_cannot_become_active_topology(self):
        data = source_data()
        data["building"]["spaces"][0].pop("geometry")
        data["building"]["spaces"][0]["geometry_status"] = "geometry_review_required"
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        ids = [item["candidate_id"] for key in ("floors", "zones", "rooms") for item in draft["candidates"][key]]
        outcome = apply_calculator_draft(self.reviewed(draft, ids))
        self.assertEqual(outcome["hourly_load_model"]["rooms"], [])
        self.assertTrue(any("geometry" in item["reason"].lower() for item in outcome["summary"]["unresolved"]))

    def test_incomplete_construction_stays_outside_envelope_library(self):
        draft = self.build(); construction = next(item for item in draft["candidates"]["envelope"] if item["kind"] == "construction")
        reviewed = self.reviewed(draft, [construction["candidate_id"]]); outcome = apply_calculator_draft(reviewed)
        self.assertFalse(outcome["changed"]["envelope_library"])
        self.assertFalse(outcome["envelope_library"]["constructions"])
        self.assertTrue(any(item["candidate_id"] == construction["candidate_id"] for item in outcome["summary"]["unresolved"]))


if __name__ == "__main__":
    unittest.main()
