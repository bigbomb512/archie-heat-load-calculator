"""Small, independent Turramurra outline review cases (pages 8, 9 and 25)."""

import unittest
from unittest.mock import patch

from shapely.geometry import LineString, box

from ai import room_outline
from backend import autonomous_tasks_service as service


class TurramurraOutlineChecks(unittest.TestCase):
    def test_selected_plan_level_wins_over_other_page_readings(self):
        with patch.object(service, "_p0_main_geometry_pages", return_value=[
                {"page": 8, "level_name": "Ground Floor"}, {"page": 9, "level_name": "First Floor"},
                {"page": 25, "level_name": "Basement"}]), \
             patch.object(service, "_page_level", return_value="Ground Floor"):
            self.assertEqual(service._outline_plan_level("/tmp", 9), "First Floor")
            self.assertEqual(service._outline_plan_level("/tmp", 25), "Basement")

    def test_skewed_wall_boundary_and_open_side(self):
        walls = [LineString(edge) for edge in [((0, 0), (200, 0)), ((200, 0), (200, 160)),
                                             ((200, 160), (0, 160)), ((0, 160), (0, 0))]]
        square = [(0, 0), (200, 0), (200, 160), (0, 160)]
        skewed = [(0, 0), (200, 0), (150, 160), (45, 160)]
        self.assertEqual(room_outline.outline_wall_review(square, walls, 20), "")
        self.assertIn("drawn walls", room_outline.outline_wall_review(skewed, walls, 20))
        self.assertEqual(room_outline.outline_wall_review(square, walls[:3], 20, "west side open to Living"), "")
        self.assertEqual(room_outline.outline_wall_review(square, [], 20), "")

    def test_printed_label_swap_and_ambiguity(self):
        points = [(0, 0), (100, 0), (100, 100), (0, 100)]
        labels = [("Bedroom 5", (50, 50))]
        self.assertEqual(room_outline.outline_printed_label(points, "Bedroom", labels, ["Bedroom 5"]),
                         ("Bedroom 5", ""))
        self.assertEqual(room_outline.outline_printed_label(points, "Bedroom 5", labels, ["Bedroom 5"]),
                         ("Bedroom 5", ""))
        self.assertIn("review", room_outline.outline_printed_label(points, "Media", [("Family", (50, 50))],
                                                                     ["Media", "Family"])[1])
        self.assertIn("review", room_outline.outline_printed_label(points, "Media",
                         [("Family", (30, 50)), ("Living", (70, 50))], ["Family", "Living"])[1])

    def test_trace_id_comes_only_from_plan_level(self):
        record = {"task": "P0_room_outlines", "packet": {"page": 9,
                  "calibration": {"status": "agreed", "mm_per_px": 10}}}
        polygon = box(0, 0, 100, 100)
        rooms = [{"label": "Bedroom", "level_name": "Ground Floor", "room_id": "ground"},
                 {"label": "Bedroom", "level_name": "First Floor", "room_id": "first"}]
        with patch.object(service, "_page_level", return_value="First Floor"), \
             patch.object(service, "_number_separate_house_bedrooms", side_effect=lambda _r, _rec, rows, _mm: rows), \
             patch.object(service, "_number_separate_repeated_rooms", side_effect=lambda _r, _rec, rows, _mm: rows), \
             patch.object(service.reviewer_room_geometry_service, "_rooms", return_value=rooms), \
             patch.object(service, "_inferred_room_data", return_value=[]):
            rows = service._p0_trace_rows("/tmp", record, [{"label": "Bedroom", "polygon": polygon}])
            self.assertEqual(rows[0]["room_id"], "first")
            self.assertEqual(rows[0]["level_name"], "First Floor")
            self.assertEqual(service._p0_trace_rows("/tmp", record,
                             [{"label": "Kitchen", "polygon": polygon}]), [])


if __name__ == "__main__":
    unittest.main()
