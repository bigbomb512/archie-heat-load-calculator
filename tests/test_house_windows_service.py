"""House windows from the text layer placed on traced rooms (no AI); shops are left alone."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import ai_preliminary_service, autonomous_tasks_service, house_windows_service, reviewer_room_geometry_service


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
    def test_untraced_label_candidates_exclude_outdoor_and_retain_bedroom(self):
        spatial = {"standalone_text_items": [
            {"text": "BEDROOM", "bbox": [10, 10, 60, 20]},
            {"text": "3", "bbox": [62, 10, 70, 20]},
            {"text": "BALCONY", "bbox": [10, 40, 65, 50]},
        ]}
        rows = house_windows_service._untraced_labels(spatial, "First Floor", set())
        self.assertEqual([(row["label"], row["eligible"]) for row in rows],
                         [("Bedroom 3", True), ("Balcony", False)])

    def test_scheduled_window_can_be_assigned_to_untraced_room_label(self):
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 9,
                "standalone_text_items": [{"text": "BEDROOM", "bbox": [700, 180, 760, 190]},
                                          {"text": "3", "bbox": [762, 180, 770, 190]}]}]}))
            def pages(number):
                return (1000, 700, SCHEDULE if number == 20 else [word("W4", 750, 200)])
            with patch.object(reviewer_room_geometry_service, "current_records", return_value=[TRACE]), \
                 patch.object(reviewer_room_geometry_service, "post"), \
                 patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700):
                result = house_windows_service.apply(None, {"review_dir": str(root)}, root, read_page=pages)
            self.assertEqual([(row["mark"], row["room"]) for row in result["assigned_without_wall"]],
                             [("W4", "Bedroom 3")])
            self.assertFalse(any(row.get("mark") == "W4" for row in result["unplaced"]))

    def test_a_plan_with_no_traced_room_still_assigns_its_windows_by_label(self):
        # Page 9 has no trace at all; its scale comes from the sheet's printed scale (5 mm per image px).
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 9,
                "standalone_text_items": [{"text": "BEDROOM", "bbox": [700, 180, 760, 190]},
                                          {"text": "2", "bbox": [762, 180, 770, 190]}]}]}))

            def pages(number):
                return (1000, 700, SCHEDULE if number == 20 else [word("W4", 750, 200)])
            with patch.object(reviewer_room_geometry_service, "current_records", return_value=[]), \
                 patch.object(reviewer_room_geometry_service, "post"), \
                 patch.object(autonomous_tasks_service, "_p0_main_geometry_pages",
                              return_value=[{"page": 9, "level_name": "Ground Floor"}]), \
                 patch.object(autonomous_tasks_service, "_p0_context", return_value={"declared_mm_per_px": 5}), \
                 patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700):
                result = house_windows_service.apply(None, {"review_dir": str(root)}, root, read_page=pages)
            self.assertEqual([(row["mark"], row["room"]) for row in result["assigned_without_wall"]],
                             [("W4", "Bedroom 2")])

    def test_a_plan_with_no_known_scale_leaves_its_windows_unplaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = job(folder)
            with patch.object(reviewer_room_geometry_service, "current_records", return_value=[]), \
                 patch.object(reviewer_room_geometry_service, "post"), \
                 patch.object(autonomous_tasks_service, "_p0_main_geometry_pages",
                              return_value=[{"page": 9, "level_name": "Ground Floor"}]), \
                 patch.object(autonomous_tasks_service, "_p0_context", side_effect=OSError("no plan")), \
                 patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700):
                result = house_windows_service.apply(None, {"review_dir": str(root)}, root, read_page=read_page)
            self.assertEqual(result["assigned_without_wall"], [])
            self.assertTrue(any("scale isn't known" in row.get("reason", "") for row in result["unplaced"]))

    def test_trace_must_belong_to_its_plan_level(self):
        levels = {8: "Ground Floor", 9: "First Floor"}
        self.assertFalse(house_windows_service._same_plan_level(
            {"page": 8, "level_name": "Basement"}, levels))
        self.assertTrue(house_windows_service._same_plan_level(
            {"page": 9, "level_name": "First Floor"}, levels))
        self.assertTrue(house_windows_service._same_plan_level(
            {"page": 10, "level_name": "Basement"}, levels))

    def test_unmatched_windows_are_visible_in_new_draft_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "room_use_resolution.json").write_text(json.dumps({"dwelling_context": {"is_dwelling": True}}))
            (root / "house_windows.json").write_text(json.dumps({"coverage_status": "needs_review",
                "windows_in_schedule": 28, "placed": [{"mark": "W01"}], "unplaced": [{"mark": "W02"}]}))
            report = {"warnings": [], "known_exclusions": []}
            ai_preliminary_service._attach_house_window_coverage(
                {"root": root, "room_use": root / "room_use_resolution.json"}, report)
            self.assertEqual(report["glazing_coverage"], {"status": "needs_review", "scheduled": 28,
                                                          "placed": 1, "assigned_without_wall": 0, "unplaced": 1})
            self.assertIn("included-scope subtotal", report["warnings"][0])
            self.assertEqual(report["known_exclusions"][0]["component_type"], "glazing and solar")

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

    def test_a_tag_only_the_text_order_reading_finds_is_placed(self):
        # Set 15 draws each tag letter by letter; position order splits "W6" into "W" and "6", text order keeps it.
        split_plan = [row for row in PLAN if row["text"] != "W6"] + [word("W", 299, 392), word("6", 301, 392)]

        def reader(number):
            return (1000, 700, SCHEDULE if number == 20 else split_plan)
        reader.flow = lambda number: [word("W6", 300, 392)] if number == 9 else []
        with tempfile.TemporaryDirectory() as folder:
            writes = []
            with patch.object(reviewer_room_geometry_service, "current_records", return_value=[TRACE]), \
                 patch.object(reviewer_room_geometry_service, "post", side_effect=lambda _web, _project, body, **_kwargs: writes.append(body)), \
                 patch.object(autonomous_tasks_service, "room_ceiling_height", return_value=2700):
                house_windows_service.apply(None, {"id": "house"}, job(folder), read_page=reader)
            new = sorted(row["mark"] for row in writes[0]["openings"] if row.get("declaration_source") == "drawing_text")
            self.assertEqual(new, ["W4", "W6"])

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
