#!/usr/bin/env python3

import unittest

from ai.evidence_fusion import build_evidence_fusion


class EvidenceFusionTests(unittest.TestCase):
    def setUp(self):
        self.ai = {"source_pdf": "architect.pdf", "drawing_set": {"pages": [
            {"page": 1, "drawing_number": "202", "title": "Dimension Plan", "sheet_classification": "floor_plan", "rooms": []},
            {"page": 2, "drawing_number": "203", "title": "Floor Finish Plan", "sheet_classification": "floor_plan", "rooms": []},
            {"page": 3, "drawing_number": "205", "title": "Service Plan - Lighting", "sheet_classification": "floor_plan", "rooms": []},
            {"page": 4, "drawing_number": "003", "title": "Material Schedule", "sheet_classification": "schedule", "rooms": []},
            {"page": 5, "drawing_number": "300", "title": "3D Perspective", "sheet_classification": "perspective_or_3d", "rooms": []},
        ]}}
        self.coverage = {"page_roles": [
            {"page": 1, "proposed_role": "main_floor_plan", "geometry_eligible": True, "authority_status": "ambiguous", "confidence": 0.9},
            {"page": 2, "proposed_role": "supporting_geometry_plan", "geometry_eligible": True, "authority_status": "proposed", "confidence": 0.8},
            {"page": 3, "proposed_role": "services_or_lighting_plan", "geometry_eligible": False, "reference_only": True, "authority_status": "proposed", "confidence": 0.8},
            {"page": 4, "proposed_role": "reference", "geometry_eligible": False, "reference_only": True, "authority_status": "proposed", "confidence": 0.6},
            {"page": 5, "proposed_role": "3d_render", "geometry_eligible": False, "reference_only": True, "authority_status": "proposed", "confidence": 0.6},
        ]}

    def test_all_pages_and_roles_are_retained(self):
        fusion = build_evidence_fusion(self.ai, self.coverage, {})
        self.assertEqual(len(fusion["pages"]), 5)
        self.assertEqual(fusion["pages"][4]["proposed_role"], "3d_reference")
        self.assertTrue(fusion["pages"][3]["reference_only"])

    def test_room_without_geometry_is_review_only(self):
        building = {"spaces": [{"id": "space-1", "name": "Kitchen", "level_name": "", "geometry_status": "label_detected", "unresolved_fields": ["floor", "geometry"], "evidence": [{"page": 1, "excerpt": "Kitchen"}]}]}
        fusion = build_evidence_fusion(self.ai, self.coverage, building)
        self.assertEqual(fusion["entities"][0]["kind"], "room")
        self.assertTrue(any(item["affected_id"] == fusion["entities"][0]["entity_id"] for item in fusion["review_items"]))

    def test_entity_ids_are_stable_when_records_reordered(self):
        building = {"openings": [
            {"id": "window-1", "tag": "W01", "kind": "window_or_glazing", "evidence": [{"page": 1, "excerpt": "W01"}]},
            {"id": "window-2", "tag": "W02", "kind": "window_or_glazing", "evidence": [{"page": 1, "excerpt": "W02"}]},
        ]}
        first = build_evidence_fusion(self.ai, self.coverage, building)
        second = build_evidence_fusion(self.ai, self.coverage, {"openings": list(reversed(building["openings"]))})
        self.assertEqual({x["entity_id"] for x in first["entities"]}, {x["entity_id"] for x in second["entities"]})
        self.assertEqual(first["fingerprint"], second["fingerprint"])


if __name__ == "__main__":
    unittest.main()
