"""House windows from the text layer placed on traced rooms (no AI); shops are left alone."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import autonomous_tasks_service, house_windows_service, reviewer_room_geometry_service


def word(text, x, y):
    return {"text": text, "x0": x, "x1": x + 4.5 * len(text), "top": y, "bottom": y + 6}


SCHEDULE = [word("Window", 217, 45), word("Schedule", 247, 45),
            word("Level", 69, 63), word("Mark", 129, 63), word("Width", 172, 63), word("Height", 212, 63),
            word("Head", 248, 63), word("Height", 269, 63), word("Description", 333, 63), word("Count", 421, 63),
            word("Ground", 44, 189), word("Floor", 73, 189), word("W4", 132, 189), word("1600", 174, 189), word("600", 216, 189),
            word("1600", 261, 189), word("Sliding", 301, 189), word("Window", 327, 189), word("1", 429, 189),
            word("Ground", 44, 213), word("Floor", 73, 213), word("W6", 132, 213), word("5000", 173, 213), word("2400", 214, 213),
            word("2400", 261, 213), word("Operable", 321, 213), word("Window", 355, 213), word("1", 429, 213)]
# Plan page 9, 1 pt = 10 mm (image px = 2 × pt, 5 mm/px). The bedroom is 600 × 400 pt (6 m × 4 m): W4's tag stands
# by the top wall, W6's (5 m wide) by the bottom wall; W99 has no schedule row.
PLAN = [word("BED", 300, 200), word("W4", 150, 18), word("W6", 300, 392), word("W99", 590, 200)]
TRACE = {"trace_id": "bed-1", "room_id": "room-use:ground:bed", "room_label": "Bed 1", "level_name": "Ground Floor",
         "page": 9, "points_image_px": [[0, 0], [1200, 0], [1200, 800], [0, 800], [0, 0]],
         "calibration": {"status": "agreed", "mm_per_px": 5}, "edges": [{"index": 3, "boundary": "internal"}],
         "roof": "unknown", "openings": [{"opening_id": "kept", "edge_index": 0, "declaration_source": "reviewer"}]}


def job(folder, dwelling=True):
    root = Path(folder)
    (root / "room_use_resolution.json").write_text(json.dumps({
        "dwelling_context": {"is_dwelling": dwelling, "evidence": []},
        "records": [{"room_id": TRACE["room_id"], "space_scope": "comfort_hvac"}]}))
    (root / "page_inventory.json").write_text(json.dumps({"pages": {"20": {"page_type": "schedule", "title": "Window & Door Schedule"},
                                                                    "9": {"page_type": "floor_plan", "title": "Ground Floor"}}}))
    (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
        {"page": 9, "coordinate_systems": {"image_px": {"image_width": 2000, "image_height": 1400}}}]}}))
    (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(root / "missing.pdf")}))
    return root


def read_page(number):
    return (1000, 700, SCHEDULE if number == 20 else PLAN)


class HouseWindowsServiceTests(unittest.TestCase):
    def run_apply(self, root, ceiling=2700):
        writes = []
        with patch.object(reviewer_room_geometry_service, "current_records", return_value=[TRACE]), \
             patch.object(reviewer_room_geometry_service, "post", side_effect=lambda _web, _project, body, **_kwargs: writes.append(body)), \
             patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=ceiling):
            result = house_windows_service.apply(None, {"id": "house"}, root, read_page=read_page)
        return result, writes

    def test_the_calculation_inputs_are_rebuilt_once_after_all_the_rooms(self):
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            with patch.object(reviewer_room_geometry_service, "current_records", return_value=[TRACE]), \
                 patch.object(reviewer_room_geometry_service, "post") as post, \
                 patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700), \
                 patch("backend.phase1_supervisor_service._build_calculation_evidence") as build:
                house_windows_service.apply(object(), {"id": "house"}, root, read_page=read_page)
            self.assertTrue(post.call_args.kwargs["_defer_evidence_rebuild"])
            build.assert_called_once()

    def test_windows_go_on_the_walls_their_tags_stand_against_and_those_walls_become_outside(self):
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            result, writes = self.run_apply(root)
            self.assertEqual(len(writes), 1)
            body = writes[0]
            self.assertEqual(body["declaration_source"], "drawing_text")
            new = [row for row in body["openings"] if row.get("declaration_source") == "drawing_text"]
            self.assertEqual(sorted((row["mark"], row["edge_index"]) for row in new), [("W4", 0), ("W6", 2)])
            w4 = next(row for row in new if row["mark"] == "W4")
            self.assertEqual((w4["width_m"], w4["sill_height_m"], w4["head_height_m"], w4["elevation_page"]), (1.6, 1.0, 1.6, 20))
            self.assertEqual(w4["glazing_choice"], "residential")
            # The reviewer's own opening is kept; the walls holding the windows are now outside, the inside wall untouched.
            self.assertIn("kept", [row["opening_id"] for row in body["openings"]])
            edges = {row["index"]: row["boundary"] for row in body["edges"]}
            self.assertEqual((edges[0], edges[2], edges[3]), ("external", "external", "internal"))
            self.assertEqual(body["edge_sources"], {"0": "drawing_text", "2": "drawing_text"})
            # W99 has no schedule row: listed, not guessed. The result is saved for the job.
            self.assertIn({"mark": "W99", "page": 9, "reason": "no window schedule row with this mark"}, result["unplaced"])
            self.assertEqual(json.loads((root / "house_windows.json").read_text())["windows_in_schedule"], 2)

    def test_a_window_taller_than_the_room_is_listed_not_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            result, writes = self.run_apply(job(folder), ceiling=2000)
            new = [row["mark"] for row in writes[0]["openings"] if row.get("declaration_source") == "drawing_text"]
            self.assertEqual(new, ["W4"])
            self.assertTrue(any(row.get("mark") == "W6" and "above the room's ceiling" in row["reason"] for row in result["unplaced"]))

    def test_unchanged_inputs_are_not_placed_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            self.run_apply(root)
            _result, writes = self.run_apply(root)
            self.assertEqual(writes, [])

    def test_a_shop_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            result, writes = self.run_apply(job(folder, dwelling=False))
            self.assertIsNone(result)
            self.assertEqual(writes, [])
            self.assertFalse((Path(folder) / "house_windows.json").exists())


class SavedOpeningTests(unittest.TestCase):
    def test_the_trace_save_accepts_a_drawing_text_window_and_keeps_its_mark_and_evidence(self):
        trace = dict(TRACE, edges=[{"index": 0, "boundary": "external"}])
        opening = {"opening_id": "text-W4", "edge_index": 0, "width_m": 1.6, "sill_height_m": 1.0, "head_height_m": 1.6,
                   "elevation_page": 20, "glazing_choice": "residential", "shading_category": "unshaded", "mark": "W4",
                   "declaration_source": "drawing_text", "ai_run_id": "house-windows-x", "evidence": ["W4 1600 600 1600"],
                   "assumptions": []}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700):
            paths = reviewer_room_geometry_service._paths({"review_dir": folder})
            saved = reviewer_room_geometry_service._validated_trace_openings(
                {"openings": [opening]}, trace, paths, {"room_id": TRACE["room_id"], "label": "Bed 1", "level_name": "Ground Floor"},
                trace["calibration"])
        self.assertEqual(saved[0]["declaration_source"], "drawing_text")
        self.assertEqual((saved[0]["mark"], saved[0]["evidence"], saved[0]["ai_run_id"]), ("W4", ["W4 1600 600 1600"], "house-windows-x"))


if __name__ == "__main__":
    unittest.main()
