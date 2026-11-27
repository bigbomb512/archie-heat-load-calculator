"""Synthetic polygon checks for the read-only room trace review."""
import unittest

from shapely.geometry import Polygon

from scripts.room_trace_review import FIELDS, _regressions, flag_page


def trace(name, points, scale=100):
    return {"trace_id": name, "room_label": name, "points_image_px": points,
            "calibration": {"mm_per_px": scale}}


class RoomTraceReviewTests(unittest.TestCase):
    def test_flags_and_outside_labels(self):
        rows = [trace("Kitchen", [(0, 0), (100, 0), (100, 100), (0, 100)]),
                trace("Dining", [(80, 0), (180, 0), (180, 100), (80, 100)]),
                trace("Tiny", [(200, 0), (210, 0), (210, 10), (200, 10)])]
        labels = [{"name": "Kitchen", "x": 20, "y": 20},
                  {"name": "Study", "x": 40, "y": 40},
                  {"name": "Garage", "x": 300, "y": 20}]
        findings, outside = flag_page(rows, labels, Polygon([(0, 0), (150, 0), (150, 150), (0, 150)]))
        self.assertIn("multi_name", findings[0]["flags"])
        self.assertIn("overlap", findings[0]["flags"])
        self.assertIn("overlap", findings[1]["flags"])
        self.assertIn("outside_extent", findings[1]["flags"])
        self.assertIn("implausible_area", findings[2]["flags"])
        self.assertEqual([row["name"] for row in outside], ["Garage"])

    def test_area_limits_and_overlap_boundary(self):
        rows = [trace("A", [(0, 0), (100, 0), (100, 100), (0, 100)]),
                trace("B", [(99.5, 0), (199.5, 0), (199.5, 100), (99.5, 100)]),
                trace("Huge", [(300, 0), (600, 0), (600, 300), (300, 300)])]
        findings, _ = flag_page(rows, [], None)
        self.assertNotIn("overlap", findings[0]["flags"])  # exactly 0.5 m²
        self.assertIn("implausible_area", findings[2]["flags"])  # 900 m²
        self.assertNotIn("outside_extent", findings[2]["flags"])

    def test_baseline_regressions_follow_metric_direction(self):
        before = dict.fromkeys(FIELDS, 10)
        after = dict(before, labels_outside=11, placed_windows=9, trace_parts=11)
        self.assertEqual(_regressions(after, before), ["labels_outside", "placed_windows"])


if __name__ == "__main__":
    unittest.main()
