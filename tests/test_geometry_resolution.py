#!/usr/bin/env python3

import unittest

from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution, polygon_area, polygon_is_simple
from ai.calculation_extraction import extract_calculation_input_evidence


class GeometryResolutionTests(unittest.TestCase):
    def geometry_fixture(self, *, supporting_witness=True):
        ai = {"source_pdf": "other-project.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Level 1 Dimension Plan", "detected_type": "floor_plan", "drawing_number": "A-101", "level_name": "Level 1", "structured_content": {"markdown": ""}},
            {"page": 2, "title": "Level 1 Floor Finish Plan", "detected_type": "floor_plan", "drawing_number": "A-102", "level_name": "Level 1", "structured_content": {"markdown": ""}},
        ]}}
        building = {"spaces": [{
            "id": "room-store", "name": "Store", "level_name": "Level 1", "geometry_status": "label_detected",
            "evidence": ([{"page": 1, "excerpt": "Store"}, {"page": 2, "excerpt": "Store", "geometry_reference": "finish-region-store"}]
                         if supporting_witness else [{"page": 1, "excerpt": "Store"}]),
        }]}
        ocr = {"pages": [{"page": 1, "room_label_candidates": [{"text": "Store", "status": "room_label", "bbox": [40, 20, 60, 30]}]}]}
        lines = [
            {"candidate_id": "W1", "candidate_role_hint": "possible_wall", "start_px": [0, 0], "end_px": [100, 0]},
            {"candidate_id": "W2", "candidate_role_hint": "possible_wall", "start_px": [100, 0], "end_px": [100, 50]},
            {"candidate_id": "W3", "candidate_role_hint": "possible_wall", "start_px": [100, 50], "end_px": [0, 50]},
            {"candidate_id": "W4", "candidate_role_hint": "possible_wall", "start_px": [0, 50], "end_px": [0, 0]},
        ]
        vector = {"geometry_key_points": {"pages": [{"page": 1, "line_candidates": lines, "dimension_candidates": []}]}}
        matches = {"pages": [{"page": 1, "dimension_span_candidates": [{"dimension_candidate_id": "D1", "value_mm": 10000}], "dimension_wall_links": [{"dimension_candidate_id": "D1", "target_wall_id": "W1", "value_mm": 10000, "status": "matched"}]}]}
        return ai, build_drawing_coverage(ai), building, ocr, vector, matches

    def test_closed_polygon_validation(self):
        square = [[0, 0], [10, 0], [10, 5], [0, 5], [0, 0]]
        self.assertEqual(polygon_area(square), 50)
        self.assertTrue(polygon_is_simple(square))
        self.assertIsNone(polygon_area([[0, 0], [10, 0], [0, 0]]))

    def test_all_page_roles_are_capability_mapped(self):
        ai = {"source_pdf": "drawing.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Dimension Plan", "detected_type": "floor_plan", "drawing_number": "202", "structured_content": {"markdown": ""}},
            {"page": 2, "title": "Storefront Elevation", "detected_type": "architectural_detail_noise", "drawing_number": "202", "structured_content": {"markdown": ""}},
            {"page": 3, "title": "Reflective Ceiling Plan", "detected_type": "reflected_ceiling_plan", "drawing_number": "202", "structured_content": {"markdown": ""}},
            {"page": 4, "title": "3D Perspective", "detected_type": "render_or_photo", "drawing_number": "202", "structured_content": {"markdown": ""}},
        ]}}
        coverage = build_drawing_coverage(ai)
        roles = {row["page"]: row for row in coverage["page_roles"]}
        self.assertEqual(roles[2]["proposed_role"], "opening_elevation")
        self.assertIn("opening_geometry", roles[2]["capabilities"])
        self.assertIn("ceiling_height", roles[3]["capabilities"])
        self.assertTrue(roles[4]["visual_crosscheck_eligible"])
        self.assertIn("visual_crosscheck", roles[4]["capabilities"])
        self.assertEqual(len(coverage["pages"]), 4)

    def test_3d_is_crosscheck_only_and_room_geometry_stays_blocked(self):
        ai = {"source_pdf": "drawing.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Dimension Plan", "detected_type": "floor_plan", "drawing_number": "202", "structured_content": {"markdown": ""}},
            {"page": 2, "title": "3D Render", "detected_type": "render_or_photo", "drawing_number": "202", "structured_content": {"markdown": ""}},
        ]}}
        coverage = build_drawing_coverage(ai)
        building = {"spaces": [{"id": "room-1", "name": "Shop", "geometry_status": "label_detected", "unresolved_fields": ["geometry"], "evidence": [{"page": 1, "excerpt": "Shop"}]}]}
        result = build_geometry_resolution(ai, coverage, building)
        self.assertTrue(any(row["kind"] == "3d_visual_crosscheck" and row["primary_dimension_source"] is False for row in result["relationships"]))
        self.assertTrue(any(row["affected_id"] == "room-1" and row["field"] == "geometry" for row in result["review_items"]))

    def test_geometry_contract_exposes_entities_and_page_identity(self):
        ai = {"source_pdf": "other-project.pdf", "drawing_set": {"pages": [
            {"page": 4, "title": "Level 1 Dimension Plan", "drawing_number": "A-101", "structured_content": {"markdown": ""}},
            {"page": 9, "title": "Level 2 Dimension Plan", "drawing_number": "A-201", "structured_content": {"markdown": ""}},
        ]}}
        coverage = build_drawing_coverage(ai)
        building = {"levels": [
            {"id": "l1", "name": "Level 1", "evidence": [{"page": 4}]},
            {"id": "l2", "name": "Level 2", "evidence": [{"page": 9}]},
        ], "spaces": [
            {"id": "room-l1", "name": "Storage", "level_name": "Level 1", "area_m2": 12, "geometry_status": "label_detected", "evidence": [{"page": 4, "excerpt": "Storage 12 m2"}]},
            {"id": "room-l2", "name": "Storage", "level_name": "Level 2", "area_m2": 12, "geometry_status": "label_detected", "evidence": [{"page": 9, "excerpt": "Storage 12 m2"}]},
        ]}
        result = build_geometry_resolution(ai, coverage, building)
        self.assertEqual(result["summary"]["page_count"], 2)
        self.assertEqual(len([row for row in result["entities"] if row["kind"] == "area"]), 2)
        self.assertEqual(len([row for row in result["conflicts"] if row["kind"] == "same_level_duplicate_room"]), 0)
        self.assertTrue(all("entity_id" in row and "source" in row for row in result["entities"]))

    def test_same_level_duplicate_room_is_a_conflict(self):
        ai = {"source_pdf": "other-project.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Plan", "drawing_number": "A-101", "structured_content": {"markdown": ""}},
            {"page": 2, "title": "Plan Revision", "drawing_number": "A-101", "structured_content": {"markdown": ""}},
        ]}}
        coverage = build_drawing_coverage(ai)
        building = {"spaces": [
            {"id": "room-a", "name": "Office", "level_name": "Level 1", "evidence": [{"page": 1}]},
            {"id": "room-b", "name": "Office", "level_name": "Level 1", "evidence": [{"page": 2}]},
        ]}
        result = build_geometry_resolution(ai, coverage, building)
        self.assertTrue(any(row["kind"] == "same_level_duplicate_room" for row in result["conflicts"]))

    def test_calibrated_closed_loop_with_independent_witness_activates_area(self):
        ai, coverage, building, ocr, vector, matches = self.geometry_fixture()
        result = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        proof = next(row for row in result["entities"] if row["kind"] == "room_geometry_proof")
        area = next(row for row in result["entities"] if row["kind"] == "area" and row["extraction_method"] == "derived_room_area")
        self.assertEqual(proof["geometry_status"], "geometry_confirmed")
        self.assertEqual(area["value"]["area_m2"], 50.0)
        self.assertEqual(area["value"]["derivation"]["formula"], "shoelace_area_px2 × (mm_per_px²) ÷ 1,000,000")

        evidence = extract_calculation_input_evidence(ai, coverage, ocr, vector, building=building, dimension_matches=matches)
        derived = [row for row in evidence["candidates"] if row["category"] == "area" and row["extraction_method"] == "derived_room_area"]
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0]["status"], "active")
        self.assertEqual(derived[0]["value"], 50.0)
        self.assertTrue(derived[0]["derivation"])
        self.assertEqual(derived[0]["geometry_proof_id"], proof["entity_id"])
        self.assertTrue(derived[0]["room_geometry_entity_id"])

    def test_closed_loop_without_second_witness_stays_blocked(self):
        ai, coverage, building, ocr, vector, matches = self.geometry_fixture(supporting_witness=False)
        result = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        proof = next(row for row in result["entities"] if row["kind"] == "room_geometry_proof")
        self.assertEqual(proof["geometry_status"], "geometry_review_required")
        self.assertIn("independent_geometry_witness", proof["unresolved_fields"])
        self.assertFalse(any(row["kind"] == "area" and row["extraction_method"] == "derived_room_area" for row in result["entities"]))

    def test_geometry_ids_survive_vector_reordering(self):
        ai, coverage, building, ocr, vector, matches = self.geometry_fixture()
        first = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        vector["geometry_key_points"]["pages"][0]["line_candidates"].reverse()
        second = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        first_ids = [row["entity_id"] for row in first["entities"] if row["kind"] in {"room_geometry_proof", "area"}]
        second_ids = [row["entity_id"] for row in second["entities"] if row["kind"] in {"room_geometry_proof", "area"}]
        self.assertEqual(first_ids, second_ids)

    def test_source_geometry_change_changes_the_evidence_fingerprint(self):
        ai, coverage, building, ocr, vector, matches = self.geometry_fixture()
        first = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        vector["geometry_key_points"]["pages"][0]["line_candidates"][0]["end_px"] = [90, 0]
        second = build_geometry_resolution(ai, coverage, building, ocr, vector, dimension_matches=matches)
        self.assertNotEqual(first["evidence_fingerprint"], second["evidence_fingerprint"])
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "geometry_confirmed" for row in second["entities"]))


if __name__ == "__main__":
    unittest.main()
