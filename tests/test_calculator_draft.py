#!/usr/bin/env python3

"""Focused checks for the proposal-only evidence-to-calculator bridge."""

from copy import deepcopy
import unittest

from ai.calculator_draft import apply_calculator_draft, build_calculator_draft
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from ai.envelope import empty_envelope_library, empty_envelope_model


EVIDENCE = [{"page": 3, "kind": "reviewed_pdf_text", "excerpt": "Ground floor Shop A · AREA 42 m²"}]


def source_data():
    return {
        "thermal": {"zones": [{"name": "Shop A", "ceiling_height_mm": 3000, "status": "direct"}]},
        "building": {
            "levels": [],
            "spaces": [{"id": "spaces-3-1", "name": "Shop A", "area": "42 m²", "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
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
        self.assertEqual(data, original, "Building a draft must not mutate its source artifacts.")
        return draft

    def test_builds_stable_cited_topology_and_missing_review_items(self):
        draft = self.build()
        floor = draft["candidates"]["floors"][0]
        room = draft["candidates"]["rooms"][0]
        zone = draft["candidates"]["zones"][0]
        self.assertEqual(floor["value"]["floor_id"], "floor_ground")
        self.assertEqual(room["value"]["zone_id"], zone["value"]["zone_id"])
        self.assertEqual(room["citations"][0]["page"], 3)
        self.assertTrue(any(item["item_id"].startswith("schedule_required:") for item in draft["review_items"]))
        self.assertFalse(draft["candidates"]["schedules"], "Schedules need complete cited 24-hour profiles.")

    def test_apply_is_explicit_idempotent_and_does_not_activate_envelope(self):
        draft = self.build()
        decisions = {item["candidate_id"]: {"decision": "accept"} for key in ("floors", "zones", "rooms") for item in draft["candidates"][key]}
        outcome = apply_calculator_draft(
            draft, decisions, empty_hourly_load_model(), empty_schedule_library(),
            empty_envelope_library(), empty_envelope_model(), "requirements-r1",
        )
        model = outcome["hourly_load_model"]
        self.assertEqual(len(model["floors"]), 1)
        self.assertEqual(len(model["zones"]), 1)
        self.assertEqual(len(model["rooms"]), 1)
        self.assertEqual(model["rooms"][0]["verification_status"], "confirmed")
        self.assertFalse(outcome["envelope_model"]["active_for_calculation"])
        self.assertTrue(outcome["changed"]["hourly_load_model"])
        repeated = apply_calculator_draft(
            draft, decisions, model, outcome["schedule_library"], outcome["envelope_library"], outcome["envelope_model"], "requirements-r1",
        )
        self.assertFalse(repeated["changed"]["hourly_load_model"])
        self.assertEqual(len(repeated["summary"]["already_present"]), 3)

    def test_existing_authored_record_is_never_overwritten(self):
        draft = self.build()
        floor = draft["candidates"]["floors"][0]
        model = empty_hourly_load_model()
        model["floors"] = [{"floor_id": floor["value"]["floor_id"], "name": "Authored floor", "elevation_m": 0, "verification_status": "confirmed", "source": "Engineer authored", "citations": []}]
        outcome = apply_calculator_draft(draft, {floor["candidate_id"]: {"decision": "accept"}}, model)
        self.assertEqual(outcome["hourly_load_model"]["floors"][0]["name"], "Authored floor")
        self.assertEqual(len(outcome["summary"]["skipped_conflicts"]), 1)

    def test_incomplete_construction_stays_outside_envelope_library(self):
        draft = self.build()
        construction = next(item for item in draft["candidates"]["envelope"] if item["kind"] == "construction")
        outcome = apply_calculator_draft(draft, {construction["candidate_id"]: {"decision": "accept"}})
        self.assertFalse(outcome["changed"]["envelope_library"])
        self.assertFalse(outcome["envelope_library"]["constructions"])
        self.assertTrue(any(item["candidate_id"] == construction["candidate_id"] for item in outcome["summary"]["unresolved"]))


if __name__ == "__main__":
    unittest.main()
