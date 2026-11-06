"""Work the drawing tasks repeat on every round is done once: the page's title-block extent, and the rooms named on
the drawings while their files are unchanged (2026-11-01, house set 18 spent minutes per round on these)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai import autonomous_tasks, room_inference
from backend import autonomous_tasks_service


def page(width=1000, height=700, scale=1.0):
    items = [{"text": text, "bbox": [x * scale, y * scale, (x + 60) * scale, (y + 10) * scale]}
             for text, x, y in (("KITCHEN", 100, 100), ("LIVING", 400, 300), ("DRAWING TITLE", 300, 660), ("REV A", 900, 690))]
    return {"page": 1, "width": width, "height": height, "standalone_text_items": items,
            "title_blocks": [{"region": "bottom_band", "bbox": [0, 640, 1000, 700]},
                             {"region": "right_band", "bbox": [850, 0, 1000, 700]}]}


class TitleBlockTests(unittest.TestCase):
    def test_one_test_per_page_gives_the_per_item_answer(self):
        for spatial in (page(), page(scale=1.4)):                       # 1.4: text in unrotated page space, scaled back
            items = spatial["standalone_text_items"]
            inside = room_inference.title_block_test(spatial, items)
            self.assertEqual([inside(item) for item in items], [False, False, True, True])
            self.assertEqual([room_inference._inside_title_block(item, spatial, items) for item in items],
                             [False, False, True, True])
        self.assertFalse(room_inference.title_block_test({"title_blocks": []})({"bbox": [0, 0, 1, 1]}))
        self.assertFalse(room_inference.title_block_test(page())({"bbox": [1, 2]}))


class InferredRoomsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        for name in autonomous_tasks_service._INFERRED_ROOM_INPUTS:
            (self.root / name).write_text("{}", encoding="utf-8")
        autonomous_tasks_service._cached_inferred_room_data.cache_clear()

    def test_worked_out_once_while_the_files_are_unchanged_and_again_after_a_change(self):
        rooms = {"rooms": [{"label": "Kitchen"}, {"label": ""}]}
        with mock.patch.object(room_inference, "infer", return_value=rooms) as infer:
            first = autonomous_tasks_service._inferred_room_data(self.root)
            first[0]["label"] = "changed by a caller"
            self.assertEqual(autonomous_tasks_service._inferred_room_data(self.root), [{"label": "Kitchen"}])
            self.assertEqual(infer.call_count, 1)
            path = self.root / "spatial_ocr.json"
            path.write_text(json.dumps({"pages": []}), encoding="utf-8")
            os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
            autonomous_tasks_service._inferred_room_data(self.root)
            self.assertEqual(infer.call_count, 2)
            (self.root / "vector_geometry.json").unlink()
            autonomous_tasks_service._inferred_room_data(self.root)
            self.assertEqual(infer.call_count, 3)


class UnreadableDimensionTests(unittest.TestCase):
    def test_null_or_unusable_reply_is_rejected_and_keeps_the_next_candidate_available(self):
        packet = {"line": {"tick_centres_image_px": [[0, 0], [100, 0]]}}
        for reply in ('{"value_mm": null, "printed_text": ""}', '{"value_mm": 3600, "printed_text": "2400"}'):
            with self.assertRaises(ValueError):
                autonomous_tasks.validate_dimension_reply(packet, reply)
        self.assertIn("another line", __import__("ai.room_outline", fromlist=["dimension_prompt"]).dimension_prompt())

    def test_null_dimension_reply_is_exhausted_and_does_not_block_the_next_line(self):
        root = Path(tempfile.mkdtemp())
        project = {"id": "t", "review_dir": str(root)}
        packet = {"task": "P0_dimensions", "page": 1, "line": {"tick_centres_image_px": [[0,0],[100,0]]}}
        for index in (1, 2):
            autonomous_tasks_service._create_run(root, "P0_dimensions", f"page-1-dimension-{index}", packet, "Read it.", [])
        with mock.patch.object(autonomous_tasks_service, "_refresh_geometry_tasks"):
            autonomous_tasks_service.post(None, project, {"action":"validate_apply", "task":"P0_dimensions",
                "target":"page-1-dimension-1", "reply":'{"value_mm":null,"printed_text":""}'})
        first = autonomous_tasks_service._current_task(root, "P0_dimensions", "page-1-dimension-1")
        second = autonomous_tasks_service._current_task(root, "P0_dimensions", "page-1-dimension-2")
        # Not needed (the next line or scale method takes over), so it never holds the job as "needs you".
        self.assertEqual(first["status"], "not_needed")
        self.assertEqual(first["applied_value"], {"fallback_required": True})
        self.assertEqual(second["status"], "waiting_for_reply")
        self.assertEqual(autonomous_tasks_service._task_progress(root)["blocked"], 0)


class TextLayerPairingTests(unittest.TestCase):
    """Numbers are paired with tick-ended lines by real distance and in image pixels (saved jobs 2026-11-06: with PDF-point
    boxes and no distance limit, Butcher Buffet's first pairing gave 0.81 mm/px on a 14.1 mm/px sheet)."""
    LINE = {"points": [(100.0, 200.0), (900.0, 200.0)], "tick_centres_image_px": [(100.0, 200.0), (900.0, 200.0)], "span_px": 800.0}

    def test_only_a_number_beside_the_line_is_paired(self):
        beside = {"value_mm": 4000, "bbox_px": [480, 170, 520, 185]}
        level_but_far = {"value_mm": 300, "bbox_px": [480, 900, 520, 915]}
        past_the_end = {"value_mm": 500, "bbox_px": [1000, 170, 1040, 185]}
        self.assertIs(autonomous_tasks_service._nearest_dimension_text(self.LINE, [level_but_far, past_the_end, beside]), beside)
        self.assertIsNone(autonomous_tasks_service._nearest_dimension_text(self.LINE, [level_but_far, past_the_end]))

    def test_text_boxes_in_pdf_points_are_converted_to_image_pixels(self):
        root = Path(tempfile.mkdtemp())
        (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 1, "dimension_candidates": [
            {"value_mm": 4000, "bbox": [100, 50, 110, 55]}, {"value_mm": None, "bbox": [1, 1, 2, 2]}]}]}), encoding="utf-8")
        rows = autonomous_tasks_service._page_dimension_texts(root, 1, {"render_dpi": 180.0, "page_origin": (0, 0)})
        self.assertEqual([row["bbox_px"] for row in rows], [[250.0, 125.0, 275.0, 137.5]])

    def test_the_vector_readings_pixel_boxes_come_first(self):
        root = Path(tempfile.mkdtemp())
        (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 1, "dimension_candidates": [
            {"value_mm": 430, "bbox": [1, 1, 2, 2]}]}]}), encoding="utf-8")
        (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [{"page": 1, "dimension_candidates": [
            {"value_mm": 300, "bbox_px": [10, 10, 20, 20]}]}]}}), encoding="utf-8")
        rows = autonomous_tasks_service._page_dimension_texts(root, 1, {"render_dpi": 180.0})
        self.assertEqual([row["value_mm"] for row in rows], [300])


class TickLineTests(unittest.TestCase):
    OBJECTS = [{"points": [(100.0, 200.0), (900.0, 200.0)], "filled": False, "style": (0.1,)},
               {"points": [(97.0, 197.0), (103.0, 203.0)], "filled": False, "style": (0.1,)},
               {"points": [(897.0, 197.0), (903.0, 203.0)], "filled": False, "style": (0.1,)},
               {"points": [(495.0, 600.0), (505.0, 610.0)], "filled": False, "style": (0.1,)}]

    def test_ticks_are_found_at_both_ends_and_a_far_shape_is_ignored(self):
        from ai import room_outline
        [line] = room_outline.dimension_line_candidates(self.OBJECTS, None, (0, 0, 1000, 1000))
        self.assertEqual(line["tick_centres_image_px"], [[100.0, 200.0], [900.0, 200.0]])
        self.assertEqual(room_outline.dimension_line_candidates(self.OBJECTS[:1], None, (0, 0, 1000, 1000)), [])

    def test_the_search_runs_once_per_page_while_the_drawing_is_unchanged(self):
        from PIL import Image
        from backend import page_analysis_cache
        context = {"objects": [], "image": Image.new("RGB", (10, 10)), "page_meta": {"page": 3}}

        class Active:
            generation = "first"
        autonomous_tasks_service._TICK_LINES_CACHE.clear()
        token = page_analysis_cache._ACTIVE.set(Active())
        try:
            with mock.patch.object(autonomous_tasks_service.room_outline, "dimension_line_candidates", return_value=[]) as search:
                autonomous_tasks_service._tick_lines(context)
                autonomous_tasks_service._tick_lines(context)
                self.assertEqual(search.call_count, 1)
                Active.generation = "changed"
                autonomous_tasks_service._tick_lines(context)
                self.assertEqual(search.call_count, 2)
        finally:
            page_analysis_cache._ACTIVE.reset(token)


class ScaleLadderEndTests(unittest.TestCase):
    """Every page ends with a scale (user rule 2026-11-03): after the standard-object estimate, a labelled 1:100."""

    def context(self):
        return {"declared_mm_per_px": None, "render_dpi": 180.0, "viewport": (0, 0, 2000, 1400), "page_bbox": (0, 0, 1190, 842)}

    def test_assumed_scale_after_every_other_step(self):
        root = Path(tempfile.mkdtemp())
        for task in ("P0_scale_read", "P0_scale_objects"):
            autonomous_tasks_service._create_run(root, task, "page-1", {"task": task, "page": 1}, "", [],
                                                 autonomous_tasks_service.NotNeeded("Not usable."))
        with mock.patch.object(autonomous_tasks_service, "_s1_calibration", return_value=None), \
                mock.patch.object(autonomous_tasks_service, "_p0_dimensions", return_value=[]):
            calibration, _reason = autonomous_tasks_service._p0_calibration_ready(root, 1, self.context())
        self.assertEqual(calibration["status"], "assumed_scale")
        self.assertAlmostEqual(calibration["mm_per_px"], 25.4 * 100 / 180.0)
        self.assertIn("1:100 is assumed", calibration["label"])
        self.assertTrue(autonomous_tasks.usable_calibration("assumed_scale"))

    def test_no_assumption_while_a_better_step_is_still_open(self):
        root = Path(tempfile.mkdtemp())
        autonomous_tasks_service._create_run(root, "P0_scale_read", "page-1", {"task": "P0_scale_read", "page": 1}, "Read it.", [])
        with mock.patch.object(autonomous_tasks_service, "_s1_calibration", return_value=None), \
                mock.patch.object(autonomous_tasks_service, "_p0_dimensions", return_value=[]):
            calibration, _reason = autonomous_tasks_service._p0_calibration_ready(root, 1, self.context())
        self.assertIsNone(calibration)

    def test_the_object_question_names_the_kinds_the_checker_accepts(self):
        prompt = autonomous_tasks.scale_objects_prompt()
        for kind in autonomous_tasks.STANDARD_OBJECTS_MM:
            self.assertIn(kind, prompt)

    def test_ai_traces_on_any_ladder_scale_are_valid(self):
        from ai import reviewer_room_geometry
        text_layer = {"source": "drawing_text", "status": "agreed", "mm_per_px": 2.0,
                      "dimension_points_image_px": [[0, 0], [100, 0]], "dimension_value_mm": 200.0}
        for calibration in (text_layer,
                            {"source": "standard_objects", "status": "standard_objects", "mm_per_px": 2.0},
                            {"source": "assumed_scale", "status": "assumed_scale", "mm_per_px": 14.1},
                            {"source": "ai_read_printed_scale", "status": "scale_bar", "mm_per_px": 3.0}):
            row = {"trace_id": "t", "room_id": "room-use:ground:shop", "page": 1, "declaration_source": "ai_determined",
                   "points_image_px": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]], "snapped_line_ids": [None] * 5,
                   "calibration": calibration, "reviewer": "Archie AI", "source_fingerprints": {}}
            with self.subTest(calibration=calibration["status"]):
                reviewer_room_geometry.validate_artifact({"records": [row]})
        reviewer = {**row, "declaration_source": "reviewer", "calibration": text_layer}
        with self.assertRaises(ValueError):          # a person's trace still needs the dimension they read
            reviewer_room_geometry.validate_artifact({"records": [reviewer]})

if __name__ == "__main__":
    unittest.main()
