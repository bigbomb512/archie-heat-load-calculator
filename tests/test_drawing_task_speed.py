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
    def test_a_null_reading_closes_the_task_and_a_wrong_reading_is_asked_again(self):
        self.assertTrue(autonomous_tasks.dimension_unreadable('{"value_mm": null, "printed_text": ""}'))
        self.assertFalse(autonomous_tasks.dimension_unreadable('{"value_mm": 3600, "printed_text": "3600"}'))
        self.assertFalse(autonomous_tasks.dimension_unreadable('{"printed_text": ""}'))
        self.assertFalse(autonomous_tasks.dimension_unreadable("not json"))
        root = Path(tempfile.mkdtemp())
        project = {"id": "t", "review_dir": str(root)}
        packet = {"task": "P0_dimensions", "page": 1, "line": {"tick_centres_image_px": [[0, 0], [100, 0]]}}
        for target in ("page-1-dimension-1", "page-1-dimension-2"):
            autonomous_tasks_service._create_run(root, "P0_dimensions", target, packet, "Read it.", [])
        autonomous_tasks_service.post(None, project, {"action": "validate_apply", "task": "P0_dimensions",
                                                      "target": "page-1-dimension-1", "reply": '{"value_mm": null, "printed_text": ""}'})
        autonomous_tasks_service.post(None, project, {"action": "validate_apply", "task": "P0_dimensions",
                                                      "target": "page-1-dimension-2", "reply": '{"value_mm": 3600, "printed_text": "2400"}'})
        closed = autonomous_tasks_service._current_task(root, "P0_dimensions", "page-1-dimension-1")
        self.assertEqual(closed["status"], "blocked")
        self.assertIn("could not read", closed["block_reason"])
        self.assertEqual(autonomous_tasks_service._current_task(root, "P0_dimensions", "page-1-dimension-2")["status"],
                         "waiting_for_reply")
        self.assertEqual(autonomous_tasks_service._p0_dimensions(root, 1), [])
        self.assertTrue(autonomous_tasks_service._p0_dimensions_settled(root, 1) is False)   # the wrong one: asked again


if __name__ == "__main__":
    unittest.main()
