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

    def test_unassigned_coverage_placeholder_is_review_item_not_fact(self):
        building = {"levels": [{"id": "level-unassigned_level", "name": "Unassigned level", "evidence": []}]}
        fusion = build_evidence_fusion(self.ai, self.coverage, building)
        self.assertFalse(any(fact.get("category") == "floor" for fact in fusion["facts"]))
        self.assertTrue(any(item["affected_id"] == "level-unassigned_level" for item in fusion["review_items"]))

    def test_unique_dimensioned_elevation_opening_activates_after_plan_match(self):
        ai = {"source_pdf": "architect.pdf", "drawing_set": {"pages": [
            {"page": 1, "drawing_number": "202", "title": "Dimension Plan"},
            {"page": 2, "drawing_number": "300", "title": "Shopfront Elevation"},
        ]}}
        coverage = {"page_roles": [
            {"page": 1, "proposed_role": "main_floor_plan", "geometry_eligible": True},
            {"page": 2, "proposed_role": "opening_elevation", "opening_geometry_eligible": True},
        ]}
        building = {"openings": [
            {"id": "plan-w01", "tag": "W01", "kind": "window_or_glazing", "evidence": [{"page": 1, "excerpt": "W01"}]},
            {"id": "elevation-w01", "tag": "W01", "kind": "window_or_glazing",
             "dimensions": {"width_mm": 1200.0, "height_mm": 2100.0, "unit": "mm"},
             "geometry": {"direct_dimension": True, "unique_target": False},
             "evidence": [{"page": 2, "excerpt": "W01 1200 x 2100 mm"}]},
        ]}
        fusion = build_evidence_fusion(ai, coverage, building)
        fact = next(row for row in fusion["facts"] if row["source"].get("page") == 2)
        self.assertEqual(fact["activation_status"], "active")
        self.assertEqual(fact["activation_basis"], "direct_dimension_unique_plan_tag")
        self.assertTrue(any(row["kind"] == "plan_elevation_opening_match" for row in fusion["relationships"]))

    def test_ambiguous_dimensioned_opening_stays_proposed(self):
        ai = {"source_pdf": "architect.pdf", "drawing_set": {"pages": [
            {"page": 1, "drawing_number": "202", "title": "Dimension Plan"},
            {"page": 2, "drawing_number": "300", "title": "Shopfront Elevation"},
        ]}}
        coverage = {"page_roles": [
            {"page": 1, "proposed_role": "main_floor_plan", "geometry_eligible": True},
            {"page": 2, "proposed_role": "opening_elevation", "opening_geometry_eligible": True},
        ]}
        building = {"openings": [
            {"id": "plan-w01-a", "tag": "W01", "kind": "window_or_glazing", "evidence": [{"page": 1, "excerpt": "W01"}]},
            {"id": "plan-w01-b", "tag": "W01", "kind": "window_or_glazing", "evidence": [{"page": 1, "excerpt": "W01"}]},
            {"id": "elevation-w01", "tag": "W01", "kind": "window_or_glazing",
             "dimensions": {"width_mm": 1200.0, "height_mm": 2100.0, "unit": "mm"},
             "geometry": {"direct_dimension": True, "unique_target": False},
             "evidence": [{"page": 2, "excerpt": "W01 1200 x 2100 mm"}]},
        ]}
        fusion = build_evidence_fusion(ai, coverage, building)
        fact = next(row for row in fusion["facts"] if row["source"].get("page") == 2)
        self.assertEqual(fact["activation_status"], "proposed")
        self.assertTrue(any(row["kind"] == "opening_plan_mapping_ambiguous" for row in fusion["conflicts"]))

    def test_unitless_opening_dimension_and_storefront_boundary_remain_blocked(self):
        ai = {"source_pdf": "architect.pdf", "drawing_set": {"pages": [
            {"page": 2, "drawing_number": "300", "title": "Storefront Elevation"},
        ]}}
        coverage = {"page_roles": [{"page": 2, "proposed_role": "opening_elevation", "opening_geometry_eligible": True}]}
        building = {
            "openings": [{"id": "opening-1", "tag": "", "kind": "unresolved_opening_geometry",
                          "dimensions": {"width": 1200.0, "height": 1800.0, "unit": ""},
                          "geometry": {"direct_dimension": True, "unit_status": "missing"},
                          "evidence": [{"page": 2, "excerpt": "1200 x 1800"}]}],
            "surfaces": [{"id": "surface-1", "kind": "opening_parent_surface", "surface_label": "storefront",
                          "geometry": None, "evidence": [{"page": 2, "excerpt": "Storefront Elevation"}]}],
        }
        fusion = build_evidence_fusion(ai, coverage, building)
        fact = next(row for row in fusion["facts"] if row["category"] == "opening")
        self.assertEqual(fact["activation_status"], "proposed")
        self.assertTrue(any(row.get("field") == "dimension_unit" for row in fusion["review_items"]))
        self.assertTrue(any(row.get("field") == "boundary_method" for row in fusion["review_items"]))


if __name__ == "__main__":
    unittest.main()
