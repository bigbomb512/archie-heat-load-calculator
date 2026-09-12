#!/usr/bin/env python3

import unittest

from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution, polygon_area, polygon_is_simple


class GeometryResolutionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
