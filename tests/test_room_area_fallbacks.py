"""Every room's area is found without anyone tracing (user decision 2026-10-16): the sheet's printed scale when no
printed dimension can be read, then a last area step (printed area, printed dimensions, an AI measurement off the plan,
or the tenancy remainder). Each is labelled with where the area came from."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import autonomous_tasks, reviewer_room_geometry
from backend import autonomous_tasks_service as tasks, drawing_tasks_service, reviewer_room_geometry_service as geometry

A3 = (0, 0, 1190.55, 841.89)          # PDF points


def context(declared=7.05, viewport=(0, 0, 2000, 1500)):
    return {"declared_mm_per_px": declared, "page_bbox": A3, "pdf_page_width": A3[2], "viewport": viewport}


def spatial(*scale_texts):
    return ({}, {"pages": [{"page": 1, "scale_candidates": [{"text": text} for text in scale_texts]}]}, {})


class SheetScaleTests(unittest.TestCase):
    def test_with_no_readable_dimension_the_sheet_scale_is_used_and_labelled_unchecked(self):
        with TemporaryDirectory() as folder, patch.object(tasks, "_load_inputs", return_value=spatial("1:50")):
            calibration, reason = tasks._p0_calibration_ready(Path(folder), 1, context())
        self.assertEqual((calibration["status"], calibration["source"], calibration["mm_per_px"]),
                         ("declared_scale_only", "sheet_printed_scale", 7.05))
        self.assertEqual(calibration["label"], "Scaled from the sheet's printed scale 1:50; no printed dimension could be read to check it.")
        self.assertEqual(reason, "")

    def test_a_scale_stated_for_bigger_paper_than_the_pdf_is_corrected(self):
        with TemporaryDirectory() as folder, patch.object(tasks, "_load_inputs", return_value=spatial("1:100 @ A1")):
            calibration, _ = tasks._p0_calibration_ready(Path(folder), 1, context(declared=14.1, viewport=(0, 0, 1000, 700)))
        self.assertAlmostEqual(calibration["paper_adjustment"], 841 / 420, places=2)  # an A1 drawing printed on A3
        self.assertAlmostEqual(calibration["mm_per_px"], 14.1 * 841 / 420, places=1)
        self.assertIn("at A1 (this PDF is printed on a different paper size, corrected)", calibration["label"])

    def test_an_implausible_plan_size_or_a_pending_dimension_reading_gives_no_scale_yet(self):
        with TemporaryDirectory() as folder, patch.object(tasks, "_load_inputs", return_value=spatial("1:5000")):
            calibration, _ = tasks._p0_calibration_ready(Path(folder), 1, context(declared=705, viewport=(0, 0, 2000, 1500)))
        self.assertIsNone(calibration)                                      # 1.4 km across: not a room plan
        with TemporaryDirectory() as folder, patch.object(tasks, "_load_inputs", return_value=spatial("1:50")):
            root = Path(folder)
            tasks._create_run(root, "P0_dimensions", "page-1-dimension-1", {"task": "P0_dimensions", "page": 1}, "Read it.", [])
            self.assertIsNone(tasks._p0_calibration_ready(root, 1, context())[0])   # still waiting for the AI's reading
            self.assertFalse(tasks._p0_dimensions_settled(root, 1))

    def test_an_ai_outline_may_carry_the_sheet_scale_but_a_persons_trace_may_not(self):
        trace = {"trace_id": "t", "room_id": "r", "room_label": "Shop", "level_name": "G", "page": 1,
                 "points_image_px": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], "snapped_line_ids": [None] * 5,
                 "calibration": {"source": "sheet_printed_scale", "status": "declared_scale_only", "mm_per_px": 7.05},
                 "reviewer": "Archie AI (P0)", "source_fingerprints": {"source_pdf": "a"}, "status": "geometry_proposed",
                 "created_at": "now", "declaration_source": "ai_determined", "ai_run_id": "run"}
        reviewer_room_geometry.validate_artifact({"records": [trace]})
        with self.assertRaisesRegex(ValueError, "two finite points"):
            reviewer_room_geometry.validate_artifact({"records": [{**trace, "declaration_source": "reviewer"}]})


class RoomSizeStepTests(unittest.TestCase):
    packet = {"room_names": ["Bar", "Office", "Store", "Shop"], "page": 20, "level_name": "Ground"}

    def test_each_basis_is_checked_and_turned_into_an_area(self):
        reply = json.dumps({"rooms": [
            {"room": "Bar", "basis": "printed_area", "area_m2": 32.6, "evidence": "BAR 32.6m²"},
            {"room": "Office", "basis": "printed_dimensions", "length_mm": 3000, "width_mm": 3000, "evidence": "3000 x 3000"},
            {"room": "Store", "basis": "estimated", "length_mm": 2500, "width_mm": 2000, "evidence": ""}],
            "not_found": [], "tenancy_area_m2": 300, "tenancy_evidence": "TENANCY AREA 300m²"})
        checked = autonomous_tasks.validate_room_size_reply(self.packet, reply)
        self.assertEqual([(row["room"], row["area_m2"]) for row in checked["rooms"]], [("Bar", 32.6), ("Office", 9.0), ("Store", 5.0)])
        self.assertEqual(checked["tenancy_area_m2"], 300)
        for bad, message in (({"room": "Bar", "basis": "printed_area", "area_m2": 32.6, "evidence": ""}, "printed text"),
                             ({"room": "Bar", "basis": "estimated", "length_mm": 200000, "width_mm": 200000}, "not a plausible"),
                             ({"room": "Bar", "basis": "guess", "area_m2": 3}, "basis")):
            with self.assertRaisesRegex(ValueError, message):
                autonomous_tasks.validate_room_size_reply(self.packet, json.dumps({"rooms": [bad]}))

    def test_one_room_left_over_takes_the_tenancy_remainder(self):
        record = {"packet": self.packet, "accuracy": {"auto_apply": False}, "quality_label": "AI-determined (below accuracy bar)"}
        validated = {"rooms": [{"room": "Bar", "basis": "printed_area", "area_m2": 32.6, "evidence": "BAR 32.6m²"},
                               {"room": "Office", "basis": "estimated", "area_m2": 9.0, "evidence": ""}],
                     "not_found": [{"room": "Store", "reason": "On the neighbour's plan."}],
                     "tenancy_area_m2": 300.0, "tenancy_evidence": "TENANCY AREA 300m²"}
        already = {"kitchen": {"room_label": "Kitchen", "area_m2": 104.9}}
        with patch.object(tasks.reviewer_room_geometry_service, "current_traced_areas", return_value=already):
            applied = tasks._apply_p0_sizes(None, {}, Path("."), record, validated)["applied_value"]
        shop = next(row for row in applied["rooms"] if row["label"] == "Shop")
        self.assertEqual((shop["area_m2"], shop["source"]), (round(300 - 104.9 - 32.6 - 9.0, 2), "tenancy area remainder"))
        self.assertIn("TENANCY AREA 300m² less the other rooms", shop["printed_text"])
        self.assertEqual([row["source"] for row in applied["rooms"][:2]], ["printed (read from image)", "AI estimate from the plan"])

    def test_the_step_waits_for_the_outline_steps_and_asks_only_for_rooms_without_an_area(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            rooms = [{"label": name} for name in ("Bar", "Kitchen", "Shop")]
            have = {"k": {"room_label": "Kitchen", "area_m2": 104.9}}
            with patch.object(tasks, "_p0_main_geometry_pages", return_value=[{"page": 20}]), \
                 patch.object(tasks, "_inferred_room_data", return_value=rooms), \
                 patch.object(tasks.reviewer_room_geometry_service, "current_traced_areas", return_value=have), \
                 patch.object(tasks, "_p0_context", return_value={"image": __import__("PIL.Image", fromlist=["Image"]).new("RGB", (400, 300)),
                                                                  "viewport": (0, 0, 400, 300), "declared_mm_per_px": 20}), \
                 patch.object(tasks, "_p0_calibration_ready", return_value=({"mm_per_px": 20, "label": "1:100"}, "")), \
                 patch.object(tasks, "_p0_name_sheets", return_value=([], [])):
                tasks._create_run(root, "P0_room_outlines", "page-20", {"task": "P0_room_outlines", "page": 20}, "Draw.", [])
                self.assertEqual(tasks._p0_size_packets(root), [])            # the outline answer is still to come
                record = tasks._current_task(root, "P0_room_outlines", "page-20")
                record.update({"status": "below_accuracy_bar"})
                tasks._update_record(root, record)
                [(task, target, packet, prompt, images, reason)] = tasks._p0_size_packets(root)
        self.assertEqual((task, target, packet["room_names"], reason), ("P0_room_sizes", "page-20", ["Bar", "Shop"], ""))
        self.assertIn("one pixel is about", prompt)
        self.assertFalse(drawing_tasks_service.uses_grid("P0_room_sizes", [(400, 300)]))   # no coordinates asked


class AreaMergeTests(unittest.TestCase):
    def _sizes(self, root, rooms):
        tasks._create_run(root, "P0_room_sizes", "page-20", {"task": "P0_room_sizes", "page": 20, "room_names": []}, "Sizes.", [])
        record = tasks._current_task(root, "P0_room_sizes", "page-20")
        record.update({"status": "below_accuracy_bar", "applied_value": {"rooms": rooms}})
        tasks._update_record(root, record)

    def test_an_area_with_no_outline_is_used_with_its_source(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._sizes(root, [{"label": "Store", "area_m2": 5.0, "source": "AI estimate from the plan", "level_name": "Ground"}])
            artifact = {"records": [], "rooms": [{"room_id": "store", "label": "Store", "level_name": "Ground"}]}
            with patch.object(geometry, "current_artifact_input", return_value=artifact), \
                 patch("backend.job_service.area_overrides", return_value=[]):
                areas = geometry.current_traced_areas(root)
        store = next(row for row in areas.values() if row.get("room_label") == "Store")
        self.assertEqual((store["area_m2"], store["area_source"], store["area_only"]), (5.0, "AI estimate from the plan", True))

    def test_a_measured_outline_beats_an_estimate_and_a_printed_area_beats_an_ai_outline(self):
        outline = {"declaration_source": "ai_determined", "area_only": False}
        self.assertFalse(geometry.area_only_may_replace(outline, "AI estimate from the plan"))
        self.assertFalse(geometry.area_only_may_replace(outline, "tenancy area remainder"))
        self.assertTrue(geometry.area_only_may_replace(outline, "printed (read from image)"))
        self.assertFalse(geometry.area_only_may_replace({"declaration_source": "reviewer"}, "printed (read from image)"))
        self.assertTrue(geometry.area_only_may_replace(None, "AI estimate from the plan"))


class AskOnlyWhenEverythingFailedTests(unittest.TestCase):
    def test_nobody_is_asked_for_an_area_while_an_ai_area_step_can_still_answer(self):
        from backend import job_service
        waiting = {"task": "P0_room_sizes", "status": "waiting_for_reply", "reply_attempts": []}
        self.assertTrue(job_service._areas_still_being_found([waiting]))
        self.assertFalse(job_service._areas_still_being_found([{**waiting, "reply_attempts": [{}, {}]}]))  # gave up
        self.assertFalse(job_service._areas_still_being_found([{**waiting, "status": "below_accuracy_bar"}]))
        self.assertFalse(job_service._areas_still_being_found([{**waiting, "task": "P2_north"}]))


if __name__ == "__main__":
    unittest.main()
