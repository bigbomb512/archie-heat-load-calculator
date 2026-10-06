#!/usr/bin/env python3
"""Focused tests for the Card P bounded manual-task workflow."""

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai import autonomous_tasks, reviewer_room_geometry, room_outline
from backend import autonomous_tasks_service
from backend import page_analysis_cache


class AutonomousTaskTests(unittest.TestCase):
    def test_storefront_inside_face_width_allowance(self):
        pages = [{"page": 26}]
        runs = [{"run_index": 3, "span_m": 11.45}]
        for total, accepted in [(11900, True), (12500, False), (11000, False),
                                (12050, True), (12051, False), (11221, True), (11220, False)]:
            with self.subTest(total=total):
                spatial = {"pages": [{"page": 26, "dimension_candidates": [{"value_mm": total}]}]}
                match = autonomous_tasks_service._match_storefront_run(runs, pages, spatial)
                self.assertEqual(match["run_index"], 3 if accepted else None)
                packet = {"edge_length_mm": 11450, "text_layer": [str(total)]}
                reply = json.dumps({"total_width_mm": total, "total_width_text": str(total), "panels": []})
                if accepted:
                    self.assertEqual(autonomous_tasks.validate_opening_reply(packet, reply)["total_width_mm"], total)
                else:
                    with self.assertRaisesRegex(ValueError, "match the traced storefront"):
                        autonomous_tasks.validate_opening_reply(packet, reply)

    def test_north_includes_floor_rcp_and_services_plan_views(self):
        pages = [{"page": 1, "type": "floor_plan"},
                 {"page": 22, "plan_role": "reflected_ceiling_plan"},
                 {"page": 23, "type": "reflected_ceiling_or_service_plan"},
                 {"page": 24, "plan_role": "services_or_lighting_plan"},
                 {"page": 25, "type": "existing_hvac_or_services_plan"},
                 {"page": 7, "title": "PROPOSED ELECTRICAL PLAN"},
                 {"page": 16, "plan_role": "existing_hvac_plan"},
                 {"page": 4, "type": "architect_lighting_plan"},
                 {"page": 5, "type": "architect_electrical_plan"},
                 {"page": 6, "title": "REFLECTED CEILING PLAN"},
                 {"page": 30, "type": "elevation", "title": "Shopfront elevation"}]
        selected = autonomous_tasks.plan_pages({"drawing_set": {"pages": pages}})
        self.assertEqual([row["page"] for row in selected], [1, 4, 5, 6, 7, 16, 22, 23, 24, 25])

    def test_scanned_plan_builds_area_packet_and_applies_printed_areas_with_calibration(self):
        from PIL import Image
        from shapely.geometry import Polygon
        from ai import scan_reading
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": "missing.pdf"}))
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": []}))
            context = {"image": Image.new("RGB", (400, 400), "white"), "viewport": (0, 0, 400, 400),
                       "render_dpi": 180}
            with patch.object(autonomous_tasks_service, "_p0_main_geometry_pages", return_value=[{"page": 4}]), \
                    patch.object(autonomous_tasks_service, "_p0_context", return_value=context), \
                    patch.object(autonomous_tasks_service, "_page_has_text_layer", return_value=False), \
                    patch.object(autonomous_tasks_service, "_page_dimension_candidates", return_value=[]):
                rows = autonomous_tasks_service._s1_packets(root)
            self.assertEqual(len(rows), 1)
            task, target, packet, prompt, images, reason = rows[0]
            self.assertEqual((task, target, len(images), reason), ("S1_printed_areas", "page-4", 4, ""))
            self.assertIn("overlapping tiles", prompt)
            tiles = packet["tiles"]
            reply = json.dumps({"scale_text": None, "rooms": [
                {"tile": 1, "name": "Office", "number": None, "area_value": 1, "unit": "m2",
                 "printed_text": "1 m2", "label_px": [100, 100]},
                {"tile": 1, "name": "Store", "number": None, "area_value": 1, "unit": "m2",
                 "printed_text": "1 m2", "label_px": [200, 200]},
            ]})
            polygons = [Polygon([(50,50), (150,50), (150,150), (50,150)]),
                        Polygon([(150,150), (250,150), (250,250), (150,250)])]
            record = {"packet": packet, "run_id": "synthetic", "accuracy": {"auto_apply": True},
                      "quality_label": "AI-determined", "task": task, "target": target}
            with patch.object(autonomous_task_service := autonomous_tasks_service, "_p0_context", return_value=context), \
                    patch.object(autonomous_task_service, "_scan_outlines", return_value=polygons), \
                    patch.object(autonomous_task_service.reviewer_room_geometry_service, "_rooms", return_value=[]), \
                    patch.object(autonomous_task_service.reviewer_room_geometry_service, "persist_ai_determined_traces", return_value=[]):
                applied = autonomous_task_service._apply_s1_areas(None, {}, root, record, reply)
            self.assertEqual(applied["status"], "applied")
            self.assertEqual(applied["calibration"]["status"], "agreed")
            self.assertEqual(applied["calibration"]["source"], "printed_room_areas")
            self.assertEqual([row["source"] for row in applied["applied_value"]["rooms"]],
                             ["printed (read from image)"] * 2)
            empty = json.dumps({"scale_text": None, "rooms": []})
            with patch.object(autonomous_task_service, "_p0_context", return_value=context), \
                    patch.object(autonomous_task_service, "_scan_outlines", return_value=polygons):
                blocked = autonomous_task_service._apply_s1_areas(None, {}, root, record, empty)
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["block_reason"],
                             "Scanned plan with no printed areas or dimensions; room areas need the contractor.")

    def test_s3_image_only_pages_create_transcription_and_site_packets(self):
        from ai import scan_reading
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_pdf = root / "source.pdf"
            fake_pdf.touch()
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(fake_pdf)}))
            with patch.object(autonomous_tasks_service, "_image_only_pages", return_value=[2]), \
                    patch("pdfplumber.open") as open_pdf:
                from PIL import Image
                page = SimpleNamespace(to_image=lambda resolution: SimpleNamespace(original=Image.new("RGB", (400, 300), "white")))
                open_pdf.return_value.__enter__.return_value.pages = [None, page]
                rows = autonomous_tasks_service._s3_packets(root)
            self.assertEqual([row[1] for row in rows], ["page-2-right_strip", "page-2-bottom_band"])
            self.assertTrue(all(row[4] for row in rows))
            self.assertEqual(scan_reading.validate_transcription({"lines": ["123 Main Street"]}), ["123 Main Street"])
            packet, _prompt = scan_reading.site_packet_from_transcriptions({2: ["123 Main Street", "Suite 2"]})
            self.assertEqual(packet["excerpts"][0]["text"], "123 Main Street\nSuite 2")

    def test_image_only_page_detection_uses_existing_structured_page_metadata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.touch()
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(source), "drawing_set": {"pages": [
                {"page": 1, "structured_content": {"word_count": 4}},
                {"page": 2, "structured_content": {"word_count": 0, "needs_ocr": True}},
            ]}}))
            with patch("pdfplumber.open", side_effect=AssertionError("PDF text parsing should be avoided")):
                self.assertEqual(autonomous_tasks_service._image_only_pages(root), [2])

    def test_p3_whole_page_image_and_refrigeration_union(self):
        from PIL import Image
        def trace(name, room, box):
            x, y, right, bottom = box
            return {"trace_id": name, "room_id": room, "room_label": room, "page": 20,
                    "edges": [{"index": i, "boundary": "unknown"} for i in range(4)],
                    "points_image_px": [[x,y],[right,y],[right,bottom],[x,bottom],[x,y]],
                    "calibration": {"status": "agreed", "mm_per_px": 10}}
        traces = [trace("shop-1", "shop", (150,150,700,650)),
                  trace("shop-2", "shop", (700,150,1295,650)),
                  trace("kitchen", "kitchen", (150,665,900,1065)),
                  trace("coolroom", "coolroom", (915,665,1295,1065))]
        traces[2]["points_image_px"] = [[150,665],[900,665],[900,1065],[600,1065],
                                         [600,985],[520,985],[520,1065],[150,1065],[150,665]]
        traces[2]["edges"] = [{"index": i, "boundary": "unknown"} for i in range(8)]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "shop", "space_scope": "comfort_hvac"},
                {"room_id": "kitchen", "space_scope": "comfort_hvac"},
                {"room_id": "coolroom", "space_scope": "refrigeration"}]}))
            screenshots = root / "chatgpt_packet" / "screenshots"
            screenshots.mkdir(parents=True)
            Image.new("RGB", (1800,1400), "white").save(screenshots / "page_020.png")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=traces):
                rows = autonomous_tasks_service._p3_packets(root)
            self.assertEqual(len(rows), 1)
            _, _, packet, prompt, images, error = rows[0]
            self.assertEqual(error, "")
            self.assertTrue(images)
            self.assertLessEqual(len(prompt), 1500)
            transform = packet["image_transform"]
            left, top, right, bottom = transform["page_bbox"]
            for row in traces:
                for x, y in row["points_image_px"]:
                    self.assertTrue(left <= x <= right and top <= y <= bottom)
            self.assertEqual({m["run_number"] for m in transform["markers"]},
                             {row["index"] + 1 for row in packet["edges"]})
            self.assertTrue(packet["short_run_inheritance"])
            self.assertTrue({int(index) + 1 for index in packet["short_run_inheritance"]}.isdisjoint(
                {m["run_number"] for m in transform["markers"]}))
            for marker in transform["markers"]:
                x, y = marker["image_px"]
                self.assertTrue(0 <= x < transform["width_px"] and 0 <= y < transform["height_px"])
            shared = next(row for row in packet["room_edges"] if row["trace_id"] == "kitchen" and row["edge_index"] == 1)
            self.assertIsNone(shared["perimeter_run_index"])
            self.assertNotIn("coolroom", packet["trace_ids"])
            calls = []
            record = {"packet": packet, "run_id": "round6", "accuracy": {"auto_apply": True}}
            validated = {"edges": [{"index": row["index"], "boundary": "external", "evidence": "external wall"}
                                    for row in packet["edges"]]}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=traces), patch.object(
                    autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _web, _project, body, **_kwargs: calls.append(body)):
                autonomous_tasks_service._apply_p3(None, {}, root, record, validated)
            self.assertEqual({row["trace_id"] for row in calls}, {"shop-1", "shop-2", "kitchen"})
            kitchen = next(row for row in calls if row["trace_id"] == "kitchen")
            self.assertEqual(kitchen["edges"][1]["boundary"], "internal")

    @staticmethod
    def _p3_applied_lookup(trace):
        target=f"page-{trace['page']}"
        perimeter=autonomous_tasks_service._page_perimeter_data([trace])
        trace_boundaries={row["index"]:row["boundary"] for row in trace.get("edges",[])}
        runs=[]
        for run in perimeter["runs"]:
            mapped=[row for row in perimeter["trace_edges"][trace["trace_id"]]
                    if row.get("perimeter_run_index")==run["run_index"]]
            values=[trace_boundaries.get(row["edge_index"]) for row in mapped]
            boundary=next((value for value in values if value in {"external","mall"}),
                          next((value for value in values if value),"unknown"))
            runs.append({"index":run["run_index"],"boundary":boundary})
        record={"task":"P3_boundaries","target":target,"status":"applied","applied_value":{"runs":runs}}
        return lambda _root, task, requested: record if task=="P3_boundaries" and requested==target else None

    def test_p6_kitchen_is_registered_and_builds_framework_packet_with_plan_and_elevation(self):
        from PIL import Image
        class Rendered:
            def __init__(self, image):
                self.original = image
        class Page:
            width = 100
            height = 200
            def to_image(self, resolution):
                return Rendered(Image.new("RGB", (250, 500), "white"))
        class PDF:
            pages = [Page(), Page()]
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"test pdf placeholder")
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "kitchen-id", "space_scope": "comfort_hvac"}]}), encoding="utf-8")
            trace = {"room_id": "kitchen-id", "room_label": "Kitchen", "page": 1,
                     "points_image_px": [[60, 60], [170, 60], [170, 160], [60, 160], [60, 60]],
                     "calibration": {"status": "agreed", "mm_per_px": 10}}
            ai_input = {"source_pdf": str(source), "drawing_set": {"pages": [
                {"page": 2, "title": "Kitchen Elevation"}]}}
            spatial = {"pages": [{"page": 1}, {"page": 2}]}
            with patch.object(autonomous_tasks_service, "_load_inputs", return_value=(ai_input, spatial, {})), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=[trace]), \
                 patch("pdfplumber.open", return_value=PDF()), \
                 patch.object(autonomous_tasks_service.kitchen_equipment, "page_labels",
                              side_effect=lambda _spatial, page, _region=None: [f"labels-{page}"]), \
                 patch.object(autonomous_tasks_service.kitchen_equipment, "page_words",
                              side_effect=lambda _spatial, page, _region=None: {f"word-{page}"}):
                rows = autonomous_tasks_service._p6_kitchen_packets(root)
            self.assertIn("P6_kitchen", autonomous_tasks.TASKS)
            task, target, packet, prompt, images, reason = rows[0]
            self.assertEqual((task, target, reason), ("P6_kitchen", "kitchen", ""))
            self.assertEqual([row["kind"] for row in packet["images"]], ["plan_crop", "elevation_page"])
            self.assertEqual(packet["labels_by_page"], {"1": ["labels-1"], "2": ["labels-2"]})
            self.assertEqual(len(images), 2)
            self.assertTrue(prompt)

    def test_p0_selects_one_dimension_rich_main_plan_per_level_and_skips_detail_scales(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages = [
                {"page": 1, "plan_role": "main_floor_plan", "level_name": "Ground", "title": "GA Plan", "scale": "1:100"},
                {"page": 2, "plan_role": "primary_geometry_plan", "level_name": "Ground", "title": "Dimension Plan", "scale": "1:100"},
                {"page": 3, "plan_role": "main_floor_plan", "level_name": "Ground", "title": "Joinery Plan", "scale": "1:10"},
                {"page": 4, "plan_role": "supporting_geometry_plan", "level_name": "Ground", "title": "RCP", "scale": "1:100"},
                {"page": 5, "plan_role": "main_floor_plan", "level_name": "Level 1", "title": "Level 1 GA", "scale": "1:100"},
            ]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": row["page"], "proposed_role": row["plan_role"], "level_name": row["level_name"]}
                for row in pages]}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
                {"page": 1, "dimension_candidates": [{"id": i} for i in range(2)]},
                {"page": 2, "dimension_candidates": [{"id": i} for i in range(7)]},
                {"page": 3, "dimension_candidates": [{"id": i} for i in range(20)]},
                {"page": 4, "dimension_candidates": [{"id": i} for i in range(15)]},
                {"page": 5, "dimension_candidates": [{"id": 1}]},
            ]}}), encoding="utf-8")
            selected = autonomous_tasks_service._p0_main_geometry_pages(root)
            self.assertEqual([row["page"] for row in selected], [2, 5])
            skipped = autonomous_tasks_service._p0_page_selection(root)["skipped"]
            self.assertTrue(any(row["page"] == 1 and "second view" in row["reason"] for row in skipped))
            self.assertTrue(any(row["page"] == 3 and "scale" in row["reason"] for row in skipped))
            self.assertTrue(any(row["page"] == 4 and "second view" in row["reason"] for row in skipped))

    def test_p0_uses_supporting_plan_when_level_has_no_main_plan(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            page = {"page": 9, "plan_role": "supporting_geometry_plan", "level_name": "Ground", "title": "Ground Floor Plan"}
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [page]}}))
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 9, "proposed_role": "supporting_geometry_plan", "level_name": "Ground"}]}))
            selected = autonomous_tasks_service._p0_page_selection(root)
            self.assertEqual([row["page"] for row in selected["selected"]], [9])
            self.assertTrue(any(row["reason"] == "Selected supporting plan: no main plan on this level."
                                for row in selected["notes"]))
            context = {"image": Image.new("RGB", (400, 400), "white"), "viewport": (0, 0, 400, 400),
                       "render_dpi": 180}
            with patch.object(autonomous_tasks_service, "_p0_context", return_value=context), \
                 patch.object(autonomous_tasks_service, "_page_is_raster_for_scan", return_value=True), \
                 patch.object(autonomous_tasks_service, "_page_has_text_layer", return_value=False), \
                 patch.object(autonomous_tasks_service, "_page_dimension_candidates", return_value=[]):
                self.assertEqual([row[1] for row in autonomous_tasks_service._s1_packets(root)], ["page-9"])

    def test_s1_printed_areas_become_area_only_rooms_for_confirmation(self):
        from backend import room_proposal
        from backend import reviewer_room_geometry_service
        from ai import room_scope_confirmation
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "ai_tasks" / "S1_printed_areas" / "page-4"
            run = target / "runs" / "scan-run"
            run.mkdir(parents=True)
            labels = ["Shop", "Kitchen", "Bar", "Coolroom", "Store"]
            rows = [{"label": label, "area_m2": index + 10, "source": "printed (read from image)",
                     "printed_text": f"{index + 10} m²", "level_name": "Ground", "outline": None}
                    for index, label in enumerate(labels)]
            (target / "current.json").write_text(json.dumps({"run_path": "runs/scan-run"}))
            (run / "record.json").write_text(json.dumps({"task": "S1_printed_areas", "target": "page-4",
                "status": "applied", "run_id": "scan-run", "packet": {"page": 4}, "applied_value": {"rooms": rows}}))
            proposal = room_proposal.room_proposal({}, root)
            self.assertEqual([row["label"] for row in proposal["rooms"]], labels)
            self.assertTrue(all(row["area_origin"] == "printed (read from image)" for row in proposal["rooms"]))
            with patch.object(reviewer_room_geometry_service, "current_artifact_input", return_value={
                    "records": [], "rooms": [{"room_id": row["room_id"], "label": row["label"], "level_name": "Ground"}
                                              for row in proposal["rooms"]]}):
                areas = reviewer_room_geometry_service.current_traced_areas(root)
            self.assertEqual(len(areas), 5)
            self.assertTrue(all(row["area_only"] and row["outline"] is None and
                                row["source"] == "printed (read from image)" for row in areas.values()))
            confirmation_input = {"material": {"hourly_load_model": {"floors": [], "zones": [],
                "rooms": [{"room_id": row["room_id"], "name": row["label"], "zone_id": "z",
                           "area_m2": row["area_m2"]} for row in proposal["rooms"]]}},
                "materialized_fields": [{"room_id": row["room_id"], "field": "area_m2", "value": row["area_m2"],
                    "origin": "printed (read from image)", "evidence": [{"page": 4}]} for row in proposal["rooms"]],
                "review_queue": [], "exclusions": [], "excluded_spaces": []}
            candidates = room_scope_confirmation.candidates(confirmation_input)
            self.assertEqual(sorted((row["label"], row["area_m2"]) for row in candidates),
                             sorted(zip(labels, [10, 11, 12, 13, 14])))
    def test_p0_operator_response_includes_page_skip_reasons(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[
                {"page":1,"type":"floor_plan","plan_role":"main_floor_plan","level_name":"Ground","scale":"1:100"},
                {"page":2,"type":"floor_plan","plan_role":"main_floor_plan","level_name":"Ground","scale":"1:100"},
                {"page":3,"type":"joinery_plan","plan_role":"main_floor_plan","level_name":"Ground","scale":"1:10"}]}}), encoding="utf-8")
            with patch.object(autonomous_tasks_service, "_all_current", return_value=[]):
                response = autonomous_tasks_service.get(None, {"id":"selection-test","review_dir":str(root)})
            self.assertEqual([row["page"] for row in response["skipped_pages"]], [2, 3])
            self.assertTrue(all(row["reason"] for row in response["skipped_pages"]))

    def test_p0_prefers_drawing_coverage_scale_and_reports_disagreement_without_skipping(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[
                {"page":1,"type":"floor_plan","plan_role":"main_floor_plan","level_name":"Ground","scale":"1:2"}]}}),encoding="utf-8")
            (root/"drawing_coverage.json").write_text(json.dumps({"page_roles":[
                {"page":1,"proposed_role":"main_floor_plan","level_name":"Ground","main_scale":"1:100"}]}),encoding="utf-8")
            selection=autonomous_tasks_service._p0_page_selection(root)
            self.assertEqual([row["page"] for row in selection["selected"]],[1])
            self.assertEqual(selection["skipped"],[])
            self.assertIn("drawing coverage 1:100",selection["notes"][0]["reason"])
            self.assertIn("sheet metadata 1:2",selection["notes"][0]["reason"])
            self.assertEqual(selection["notes"][0]["status"],"scale_conflict")

    def test_p0_without_readable_scale_waits_for_two_dimensions_and_raster_skips_wall_styles(self):
        from PIL import Image
        with patch.object(autonomous_tasks_service, "_p0_dimensions", return_value=[{"value_mm": 1000}]):
            calibration, reason = autonomous_tasks_service._p0_calibration_ready(Path("/tmp"), 20, {})
        self.assertIsNone(calibration)
        self.assertIn("two agreeing printed dimensions", reason)
        context = {"declared_mm_per_px": .5, "objects": [], "image": Image.new("RGB", (100, 100)),
                   "viewport": (0, 0, 100, 100), "summary": [{"hatch": False, "style_id": "wall"}]}
        with patch.object(autonomous_tasks_service, "_p0_main_geometry_pages",
                          return_value=[{"page": 20}]), \
             patch.object(autonomous_tasks_service, "_p0_context", return_value=context), \
             patch.object(autonomous_tasks_service.room_outline, "dimension_line_candidates", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p0_calibration_ready",
                          return_value=({"mm_per_px": .5, "status": "agreed", "dimension_count": 2}, "")), \
             patch.object(autonomous_tasks_service, "_p0_is_raster", return_value=True) as raster_check:
            packets = autonomous_tasks_service._p0_initial_packets(Path("/tmp"))
        self.assertTrue(raster_check.called)
        self.assertFalse(any(row[0] == "P0_wall_styles" for row in packets))

    def test_p0_raster_room_packets_use_raster_wall_geometry_without_wall_style_records(self):
        from PIL import Image
        from shapely.geometry import box
        context = {"declared_mm_per_px": .5, "objects": [], "image": Image.new("RGB", (100, 100)),
                   "viewport": (0, 0, 100, 100), "summary": []}
        area = box(20, 20, 70, 70)
        with patch.object(autonomous_tasks_service, "_p0_main_geometry_pages", return_value=[{"page": 20}]), \
             patch.object(autonomous_tasks_service, "_current_task", return_value=None), \
             patch.object(autonomous_tasks_service, "_p0_wall_style_records", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p0_context", return_value=context), \
             patch.object(autonomous_tasks_service, "_p0_calibration_ready",
                          return_value=({"mm_per_px": .5, "status": "agreed"}, "")), \
             patch.object(autonomous_tasks_service, "_p0_is_raster", return_value=True), \
             patch.object(autonomous_tasks_service.room_outline, "raster_wall_geometry",
                          return_value=box(10, 10, 15, 90)) as raster_walls, \
             patch.object(autonomous_tasks_service.room_outline, "enclosed_rooms", return_value=[area]), \
             patch.object(autonomous_tasks_service, "_inferred_room_data", return_value=[{"label": "Kitchen"}]), \
             patch.object(autonomous_tasks_service.room_outline, "render_candidate_areas",
                          return_value=(Image.new("RGB", (32, 32)), {"offset_px": [0, 0], "factor": 1})), \
             patch.object(autonomous_tasks_service, "_png_bytes", return_value=b"png"):
            rows = autonomous_tasks_service._p0_followup_packets(Path("/tmp"))
        self.assertTrue(raster_walls.called)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "P0_room_names")
        self.assertTrue(rows[0][2]["raster_mode"])
        self.assertEqual(rows[0][2]["wall_style_ids"], [])

    def test_p6_apply_and_export_are_identification_only(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            items = [{"type": "oven", "count": 2, "page": 22, "quote": "OVEN", "symbol": None,
                      "under_hood": True, "evidence_kind": "text"}]
            record = {"accuracy": {"auto_apply": False}, "packet": {"room_label": "Kitchen"}}
            applied = autonomous_tasks_service._apply_p6(record, items)
            self.assertEqual(applied["applied_value"]["label"],
                "Kitchen equipment identified from drawings (heat not yet assessed)")
            self.assertFalse(applied["applied_value"]["heat_assessed"])
            self.assertNotIn("heat_kw", applied["applied_value"])
            current = [{"task": "P6_kitchen", "stand_in": False, "applied_value": applied["applied_value"]}]
            with patch.object(autonomous_tasks_service, "_all_current", return_value=current):
                exported = autonomous_tasks_service._export_determinations(root)
            self.assertEqual(exported["P6_kitchen"], [{"type": "oven", "count": 2, "source": "ai_determined"}])

    def test_p6_reply_uses_task_framework_validation_and_persistence(self):
        with TemporaryDirectory() as temporary, \
             patch.object(autonomous_tasks_service, "_accuracy", return_value={
                 "accuracy": None, "scored": 0, "auto_apply": False, "report": ""}), \
             patch.object(autonomous_tasks_service.productization, "record_change_if_fingerprint_changed"), \
             patch.object(autonomous_tasks_service, "_refresh_geometry_tasks"):
            root = Path(temporary)
            project = {"id": "p6-test", "review_dir": str(root)}
            autonomous_tasks_service._create_run(root, "P6_kitchen", "kitchen", {
                "task": "P6_kitchen", "room_label": "Kitchen",
                "images": [{"page": 22, "kind": "plan_crop"}],
                "labels_by_page": {"22": ["OVEN"]}, "words_by_page": {"22": ["oven"]},
            }, "List equipment.")
            reply = json.dumps({"items": [{"type": "oven", "count": 1, "page": 22, "quote": "OVEN",
                                           "symbol": None, "under_hood": True}]})
            result = autonomous_tasks_service.post(None, project, {
                "action": "validate_apply", "task": "P6_kitchen", "target": "kitchen", "reply": reply})
            saved = autonomous_tasks_service._current_task(root, "P6_kitchen", "kitchen")
            self.assertEqual(saved["status"], "below_accuracy_bar")
            self.assertEqual(saved["applied_value"]["items"][0]["type"], "oven")
            self.assertIn("P6_kitchen", result["determinations"])

    def test_p2_no_arrow_applies_null_for_scoring_without_changing_reviewer_north(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reviewer_room_geometry.json").write_text(json.dumps({"page_north": {}}), encoding="utf-8")
            record = {"packet": {"page": 5}, "accuracy": {"auto_apply": False}}
            applied = autonomous_tasks_service._apply_p2(None, {}, root, record, {"found": False})
            self.assertEqual(applied["applied_value"], {"page": 5, "plan_up_azimuth_deg": None})
            self.assertEqual(applied["status"], "below_accuracy_bar")
            (root / "reviewer_room_geometry.json").write_text(json.dumps({"page_north": {
                "5": {"plan_up_azimuth_deg": 0, "declaration_source": "reviewer"}}}), encoding="utf-8")
            reviewer_record = {"packet": {"page": 5}, "accuracy": {"auto_apply": True}}
            preserved = autonomous_tasks_service._apply_p2(None, {}, root, reviewer_record, {"found": False})
            self.assertEqual(preserved["applied_value"]["plan_up_azimuth_deg"], 0)
            self.assertEqual(preserved["source"], "reviewer")

    def test_p0_dimension_reply_preserves_tick_centres_and_requires_quoted_number(self):
        packet = {"line": {"tick_centres_image_px": [[10, 20], [210, 20]], "span_px": 200}}
        checked = autonomous_tasks.validate_dimension_reply(packet, '{"value_mm":2000,"printed_text":"2,000"}')
        self.assertEqual(checked["points_image_px"], [[10, 20], [210, 20]])
        with self.assertRaisesRegex(ValueError, "does not contain"):
            autonomous_tasks.validate_dimension_reply(packet, '{"value_mm":2000,"printed_text":"1,800"}')

    def test_p0_calibration_uses_declared_scale_and_needs_two_agreeing_unscaled_dimensions(self):
        dimensions = [{"points_image_px": [[0, 0], [1000, 0]], "value_mm": 14108, "printed_text": "14,108"}]
        scaled = autonomous_tasks.calibration_from_dimensions(dimensions, 14.11)
        self.assertEqual(scaled["status"], "agreed")
        self.assertEqual(scaled["source"], "ai_read_printed_dimension")
        with self.assertRaisesRegex(ValueError, "at least two"):
            autonomous_tasks.calibration_from_dimensions(dimensions)
        two = dimensions + [{"points_image_px": [[0, 0], [500, 0]], "value_mm": 7050, "printed_text": "7,050"}]
        self.assertEqual(autonomous_tasks.calibration_from_dimensions(two)["status"], "agreed")
        with self.assertRaisesRegex(ValueError, "disagree"):
            autonomous_tasks.calibration_from_dimensions(dimensions + [
                {"points_image_px": [[0, 0], [500, 0]], "value_mm": 9000, "printed_text": "9,000"}])

    def test_p0_wall_style_reply_and_outline_naming_are_strict(self):
        summary = [{"hatch": False, "style_id": "wall"}, {"hatch": True, "style_id": "tile"}]
        self.assertEqual(room_outline.validate_wall_style_reply({"wall_styles": [1]}, summary), ["wall"])
        with self.assertRaisesRegex(ValueError, "unknown legend"):
            room_outline.validate_wall_style_reply({"wall_styles": [2]}, summary)
        self.assertEqual(autonomous_tasks.validate_wall_style_batch_reply({"summary":summary},
            '{"wall_styles":[],"uncertain":[],"notes":"No walls in this batch"}') ["wall_style_ids"], [])
        self.assertEqual(autonomous_tasks.validate_wall_style_batch_reply({"summary":summary},
            '{"wall_styles":[1],"uncertain":[]}')["wall_style_ids"], ["wall"])
        checked = room_outline.validate_naming_reply({"areas": [{"number": 1, "room": "Shop"}]}, 1,
            ["Shop"], {"offset_px": [0, 0], "factor": 1})
        self.assertEqual(checked[0][0], "Shop")
        with self.assertRaisesRegex(ValueError, "not in the room list"):
            room_outline.validate_naming_reply({"areas": [{"number": 1, "room": "Lobby"}]}, 1,
                ["Shop"], {"offset_px": [0, 0], "factor": 1})
        with self.assertRaisesRegex(ValueError, "crosses itself"):
            room_outline.validate_outline_reply({"rooms": [{"room": "Shop", "points_px": [[0,0],[10,10],[0,10],[10,0]]}],
                "open_sides": "counter"}, ["Shop"], {"offset_px": [0, 0], "factor": 1})

    def test_p3_boundary_validator_requires_evidence_and_protects_storefront(self):
        packet = {"edges": [{"index": 0}, {"index": 1}], "shared_edges": [1], "storefront_edge_index": 0}
        with self.assertRaisesRegex(ValueError, "needs quoted evidence"):
            autonomous_tasks.validate_boundary_reply(packet, json.dumps({"edges": [
                {"index": 0, "boundary": "mall", "evidence": ""}, {"index": 1, "boundary": "internal"}]}))
        with self.assertRaisesRegex(ValueError, "cannot be internal"):
            autonomous_tasks.validate_boundary_reply(packet, json.dumps({"edges": [
                {"index": 0, "boundary": "internal", "evidence": "neighbour"}, {"index": 1, "boundary": "internal"}]}))
        accepted = autonomous_tasks.validate_boundary_reply(packet, json.dumps({"edges": [
            {"index": 0, "boundary": "mall", "evidence": "SHOPFRONT"}, {"index": 1, "boundary": "internal"}]}))
        self.assertEqual([row["boundary"] for row in accepted["edges"]], ["mall", "internal"])
        displayed_numbers = autonomous_tasks.validate_boundary_reply(packet, json.dumps({"edges": [
            {"edge_number": 1, "boundary": "mall", "evidence": "SHOPFRONT"},
            {"edge_number": 2, "boundary": "internal"}]}))
        self.assertEqual([row["index"] for row in displayed_numbers["edges"]], [0, 1])

    def test_p4_validates_width_edge_and_caps_head_but_blocks_unmatched_elevation(self):
        packet = {"edge_length_mm": 11970, "ceiling_height_mm": 2700, "allow_vector_outline_read": True,
                  "text_layer": []}
        valid = autonomous_tasks.validate_opening_reply(packet, json.dumps({"total_width_mm": 11970,
            "panels": [{"width_mm": 2025, "sill_mm": 1100, "head_mm": 3000, "source": "read_from_image"},
                       {"width_mm": 9945, "sill_mm": 0, "head_mm": 2700, "source": "read_from_image"}]}))
        self.assertEqual(valid["panels"][0]["head_mm"], 2700)
        self.assertIn("capped", valid["panels"][0]["head_assumption"])
        with self.assertRaisesRegex(ValueError, "match the traced storefront edge"):
            autonomous_tasks.validate_opening_reply(packet, json.dumps({"total_width_mm": 10000,
                "panels": [{"width_mm": 2025, "sill_mm": 1100, "head_mm": 2500, "source": "read_from_image"}]}))
        with self.assertRaisesRegex(ValueError, "sum to the printed total"):
            autonomous_tasks.validate_opening_reply(packet, json.dumps({"total_width_mm": 11970,
                "panels": [{"width_mm": 2025, "sill_mm": 1100, "head_mm": 2500, "source": "read_from_image"}]}))

    def test_p4_vision_width_sum_counts_excluded_parts_with_five_percent_tolerance(self):
        packet = {"edge_length_mm": 11450, "ceiling_height_mm": 2700,
                  "allow_vector_outline_read": True, "text_layer": []}
        parts = [{"label": "Glass", "width_mm": 2025, "sill_mm": 1100, "head_mm": 2700,
                  "source": "read_from_image"}]
        excluded = [
            {"label": "Roller shutter", "width_mm": 3250, "why": "Doorway"},
            {"label": "Solid panel", "width_mm": 2950, "why": "Opaque"},
            {"label": "Signage", "width_mm": 2000, "why": "Sign"},
            {"label": "Door", "width_mm": 1325, "why": "Entry"},
        ]
        reply = {"total_width_mm": 11900, "total_width_text": "11,900",
                 "panels": parts, "excluded": excluded}
        accepted = autonomous_tasks.validate_opening_reply(packet, json.dumps(reply))
        self.assertEqual(accepted["excluded"], excluded)
        incomplete = {**reply, "excluded": [{**excluded[0], "width_mm": None}, *excluded[1:]]}
        checked = autonomous_tasks.validate_opening_reply(packet, json.dumps(incomplete))
        self.assertTrue(any("sum check skipped" in note for note in checked["notes"]))
        refused = {**reply, "excluded": [
            {"label": "Roller shutter", "width_mm": 3250, "why": "Doorway"},
            {"label": "Solid panel", "width_mm": 2000, "why": "Opaque"},
            {"label": "Signage", "width_mm": 1000, "why": "Sign"},
            {"label": "Door", "width_mm": 725, "why": "Entry"},
        ]}  # 2,025 glazed + excluded parts = 9,000 mm
        with self.assertRaisesRegex(ValueError, "sum to the printed total within 5%"):
            autonomous_tasks.validate_opening_reply(packet, json.dumps(refused))
        over_total = {**reply, "panels": [{**parts[0], "width_mm": 12000}]}
        with self.assertRaisesRegex(ValueError, "Glazed panel widths cannot exceed"):
            autonomous_tasks.validate_opening_reply(packet, json.dumps(over_total))

    def test_p4_packet_builder_emits_mall_storefront_opening_task(self):
        from PIL import Image
        from ai import ceiling_volume_resolution
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "shop", "space_scope": "comfort_hvac"}]}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 26, "title": "Storefront Elevation"}]}}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 26,
                "dimension_candidates": [{"value_mm": 11900}]}]}), encoding="utf-8")
            (root / "ceiling_volume_resolution.json").write_text("{}", encoding="utf-8")
            trace = {"trace_id": "mall-shop-trace", "room_id": "shop", "room_label": "Shop",
                     "level_name": "Ground", "page": 20,
                     "points_image_px": [[0, 0], [1145, 0], [1145, 300], [0, 300], [0, 0]],
                     "calibration": {"status": "agreed", "mm_per_px": 10},
                     "edges": [{"index": 0, "boundary": "mall"}, {"index": 1, "boundary": "external"}]}
            height_key = ceiling_volume_resolution.room_identity("Shop", "Ground")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records",
                              return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_current_task",side_effect=self._p3_applied_lookup(trace)), \
                 patch.object(ceiling_volume_resolution, "values_by_room",
                              return_value={height_key: {"ceiling_height_mm": 2700}}), \
                 patch.object(autonomous_tasks_service, "_p4_elevation_image", return_value=Image.new("RGB", (64, 64))), \
                 patch.object(autonomous_tasks_service, "_png_bytes", return_value=b"png"):
                packets = autonomous_tasks_service._p4_packets(root)
            opening_tasks = [row for row in packets if row[0] == "P4_openings" and row[2].get("edge_index") == 0]
            self.assertEqual(len(opening_tasks), 1)
            self.assertEqual(opening_tasks[0][2]["room_label"], "Shop")
            self.assertAlmostEqual(opening_tasks[0][2]["edge_length_mm"], 11450)

            self.assertIn("0 <= total_mm - span_mm <= 600", opening_tasks[0][3])
            self.assertIn("Unique run", opening_tasks[0][2]["storefront_match_reason"])

    def test_p4_matches_storefront_elevation_to_a_multi_edge_wall_run(self):
        from PIL import Image
        from ai import ceiling_volume_resolution
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[{"page":26,"title":"Storefront Elevation"}]}}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages":[{"page":26,"dimension_candidates":[{"value_mm":11970}]}]}), encoding="utf-8")
            (root / "ceiling_volume_resolution.json").write_text("{}", encoding="utf-8")
            trace = {"trace_id":"run-shop","room_id":"shop","room_label":"Shop","level_name":"Ground","page":20,
                "points_image_px":[[0,0],[600,0],[1197,0],[1197,300],[0,300],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":10},
                "edges":[{"index":0,"boundary":"mall"},{"index":1,"boundary":"mall"}]}
            key=ceiling_volume_resolution.room_identity("Shop","Ground")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_current_task",side_effect=self._p3_applied_lookup(trace)), \
                 patch.object(ceiling_volume_resolution,"values_by_room",return_value={key:{"ceiling_height_mm":2700}}), \
                 patch.object(autonomous_tasks_service,"_p4_elevation_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                packets=autonomous_tasks_service._p4_packets(root)
            target=next(row[2] for row in packets if row[0]=="P4_openings" and row[2].get("edge_indices")==[0,1])
            self.assertAlmostEqual(target["wall_run_span_m"],11.97)
            self.assertAlmostEqual(target["edge_length_mm"],11970)

    def test_p4_uses_only_shopfront_evidence_and_waits_for_p3(self):
        from PIL import Image
        from ai import ceiling_volume_resolution
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[
                {"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[
                {"page":26,"title":"Storefront Elevation"},
                {"page":27,"title":"Internal Elevation"},
                {"page":28,"title":"Internal Elevation 2"}]}}),encoding="utf-8")
            (root/"spatial_ocr.json").write_text(json.dumps({"pages":[
                {"page":26,"dimension_candidates":[]},
                {"page":27,"dimension_candidates":[{"value_mm":430}]},
                {"page":28,"dimension_candidates":[{"value_mm":433},{"value_mm":928}]}]}),encoding="utf-8")
            (root/"ceiling_volume_resolution.json").write_text("{}",encoding="utf-8")
            trace={"trace_id":"bb-shop","room_id":"shop","room_label":"Shop","level_name":"Ground","page":20,
                "points_image_px":[[0,0],[1197,0],[1197,300],[0,300],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":10},
                "edges":[{"index":0,"boundary":"mall"},{"index":1,"boundary":"internal"},
                         {"index":2,"boundary":"adjacent_tenancy"},{"index":3,"boundary":"internal"}]}
            key=ceiling_volume_resolution.room_identity("Shop","Ground")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_current_task",return_value=None), \
                 patch.object(ceiling_volume_resolution,"values_by_room",return_value={key:{"ceiling_height_mm":2700}}), \
                 patch.object(autonomous_tasks_service,"_p4_elevation_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                self.assertEqual(autonomous_tasks_service._p4_packets(root),[])

            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_current_task",side_effect=self._p3_applied_lookup(trace)), \
                 patch.object(ceiling_volume_resolution,"values_by_room",return_value={key:{"ceiling_height_mm":2700}}), \
                 patch.object(autonomous_tasks_service,"_p4_elevation_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                packets=[row for row in autonomous_tasks_service._p4_packets(root) if row[0]=="P4_openings"]
            self.assertEqual(len(packets),1)
            packet=packets[0][2]
            self.assertEqual(packet["page"],26)
            self.assertEqual(packet["edge_indices"],[0])
            self.assertAlmostEqual(packet["wall_run_span_m"],11.97)
            self.assertTrue(packet["allow_vector_outline_read"])

    def test_p3_classifies_union_perimeter_and_keeps_jagged_prompt_bounded(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[
                {"room_id":"shop","space_scope":"comfort_hvac"},
                {"room_id":"neighbor","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[{"page":26,"title":"Storefront Elevation"}]}}),encoding="utf-8")
            (root/"spatial_ocr.json").write_text(json.dumps({"pages":[{"page":20},{"page":26,"dimension_candidates":[]}]}),encoding="utf-8")
            trace={"trace_id":"shared-first","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[200,0],[200,100],[100,100],[0,100],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":100},
                "edges":[{"index":index,"boundary":"unknown"} for index in range(5)]}
            shared_room={"trace_id":"neighbor","room_id":"neighbor","room_label":"Neighbor","page":20,
                "points_image_px":[[0,100.5],[200,100.5],[200,200],[0,200],[0,100.5]],
                "calibration":{"status":"agreed","mm_per_px":100}}
            perimeter=autonomous_tasks_service._page_perimeter_data([trace,shared_room])
            # Pick the short exposed run whose dimension is unique within the 2% matching band.
            frontage=min(perimeter["runs"],key=lambda row:row["span_m"])
            spatial={"pages":[{"page":20},{"page":26,"dimension_candidates":[{"value_mm":frontage["span_m"]*1000}]}]}
            (root/"spatial_ocr.json").write_text(json.dumps(spatial),encoding="utf-8")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace,shared_room]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                packets=autonomous_tasks_service._p3_packets(root)
            self.assertEqual(len(packets),1)
            shop=packets[0][2]
            self.assertEqual(shop["page"],20)
            self.assertEqual(len(shop["trace_ids"]),2)
            self.assertIsNone(shop["storefront_run_index"])
            self.assertIn("multiple wall runs",shop["storefront_match_reason"])
            self.assertTrue(any(row["trace_id"]=="shared-first" and row["perimeter_run_index"] is None
                                for row in shop["room_edges"]))

            # Forty edge segments around a rectangle collapse to a few straight runs.
            ring=[[x*10,0] for x in range(11)]
            ring.extend([[100,y*10] for y in range(1,11)])
            ring.extend([[x*10,100] for x in range(9,-1,-1)])
            ring.extend([[0,y*10] for y in range(9,0,-1)])
            ring.append(ring[0])
            jagged={"trace_id":"jagged","room_id":"shop","room_label":"Shop","page":20,"points_image_px":ring,
                    "calibration":{"status":"agreed","mm_per_px":100}}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[jagged]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                jagged_packets=autonomous_tasks_service._p3_packets(root)
            jagged_packet=next(row[2] for row in jagged_packets if row[2].get("page")==20)
            self.assertLessEqual(len(jagged_packet["edges"]),6)

    def test_elevation_selection_uses_whole_words_and_storefront_match_checks_all_pages(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            ai_input={"drawing_set":{"pages":[
                {"page":1,"title":"Relevant Building Code"},
                {"page":26,"title":"Storefront Elevation"},
                {"page":27,"title":"Shopfront Elevation"}]}}
            spatial={"pages":[{"page":1,"dimension_candidates":[{"value_mm":23456}]},
                      {"page":26,"dimension_candidates":[{"value_mm":10000}]},
                      {"page":27,"dimension_candidates":[{"value_mm":14142}]}]}
            self.assertEqual([row["page"] for row in autonomous_tasks_service._elevation_pages(ai_input)],[26,27])
            (root/"ai_input.json").write_text(json.dumps(ai_input),encoding="utf-8")
            (root/"spatial_ocr.json").write_text(json.dumps(spatial),encoding="utf-8")
            trace={"trace_id":"pages-disagree","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[200,0],[200,100],[100,200],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":100}}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                rows=autonomous_tasks_service._p3_packets(root)
            packet=next(row[2] for row in rows if row[2].get("trace_id")=="pages-disagree")
            self.assertIsNone(packet["storefront_run_index"])
            self.assertIn("multiple wall runs",packet["storefront_match_reason"])

    def test_p3_butcher_buffet_dimension_marks_unique_11_97_m_wall_run(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[
                {"page":1,"title":"Relevant Building Code"},{"page":26,"title":"Storefront Elevation"}]}}),encoding="utf-8")
            (root/"spatial_ocr.json").write_text(json.dumps({"pages":[
                {"page":1,"dimension_candidates":[{"value_mm":23456}]},
                {"page":26,"dimension_candidates":[{"value_mm":11970}]}]}),encoding="utf-8")
            trace={"trace_id":"bb-like","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[600,0],[1197,0],[1197,200],[0,600],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":10}}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                rows=autonomous_tasks_service._p3_packets(root)
            packet=next(row[2] for row in rows if row[2].get("trace_id")=="bb-like")
            matched=next(run for run in packet["runs"] if run["run_index"]==packet["storefront_run_index"])
            self.assertAlmostEqual(matched["span_m"],11.97,places=2)
            self.assertIn("Unique run",packet["storefront_match_reason"])

    def test_p3_builds_one_bounded_perimeter_task_for_two_shop_parts(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            first={"trace_id":"shop-part-1","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[930,0],[930,500],[600,500],[600,450],[590,450],[590,500],[0,500],[0,0]],
                "calibration":{"status":"agreed","mm_per_px":10}}
            second={"trace_id":"shop-part-2","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[930,0],[1197,0],[1197,500],[930,500],[930,0]],
                "calibration":{"status":"agreed","mm_per_px":10}}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[first,second]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                packets=autonomous_tasks_service._p3_packets(root)
            self.assertEqual(len(packets),1)
            packet=packets[0][2]
            self.assertEqual(packet["trace_ids"],["shop-part-1","shop-part-2"])
            self.assertLessEqual(len(packet["edges"]),12)
            self.assertLessEqual(len(packet["runs"]),12)
            frontage=next(row for row in packet["runs"] if row["run_index"]==packet["storefront_run_index"]
                          or abs(row["span_m"]-11.97)<.03)
            mapped=[row for row in packet["room_edges"] if row["perimeter_run_index"]==frontage["run_index"]]
            self.assertEqual({row["trace_id"] for row in mapped},{"shop-part-1","shop-part-2"})
            self.assertTrue(any(row["trace_id"]=="shop-part-1" and row["perimeter_run_index"] is None
                                and row["edge_index"] in {3,4,5} for row in packet["room_edges"]))

    def test_p4_splits_one_shopfront_opening_across_two_room_parts_by_length(self):
        from PIL import Image
        from ai import ceiling_volume_resolution
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[{"page":26,"title":"Storefront Elevation"}]}}),encoding="utf-8")
            (root/"spatial_ocr.json").write_text(json.dumps({"pages":[{"page":26,"dimension_candidates":[]}]}),encoding="utf-8")
            (root/"ceiling_volume_resolution.json").write_text("{}",encoding="utf-8")
            parts=[
                {"trace_id":"shop-part-1","room_id":"shop","room_label":"Shop","level_name":"Ground","page":20,
                 "points_image_px":[[0,0],[930,0],[930,500],[0,500],[0,0]],"calibration":{"status":"agreed","mm_per_px":10},"edges":[]},
                {"trace_id":"shop-part-2","room_id":"shop","room_label":"Shop","level_name":"Ground","page":20,
                 "points_image_px":[[930,0],[1197,0],[1197,500],[930,500],[930,0]],"calibration":{"status":"agreed","mm_per_px":10},"edges":[]},
            ]
            perimeter=autonomous_tasks_service._page_perimeter_data(parts)
            frontage=next(run for run in perimeter["runs"] if abs(run["span_m"]-11.97)<.001)
            p3={"task":"P3_boundaries","target":"page-20","status":"applied",
                "applied_value":{"runs":[{"index":run["run_index"],
                    "boundary":"mall" if run["run_index"]==frontage["run_index"] else "internal"}
                    for run in perimeter["runs"]]}}
            def current_task(_root,task,target):
                return p3 if task=="P3_boundaries" and target=="page-20" else None
            room_key=ceiling_volume_resolution.room_identity("Shop","Ground")
            trace_map={row["trace_id"]:row for row in parts}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=parts), \
                 patch.object(autonomous_tasks_service,"_current_task",side_effect=current_task), \
                 patch.object(ceiling_volume_resolution,"values_by_room",return_value={room_key:{"ceiling_height_mm":2700}}), \
                 patch.object(autonomous_tasks_service,"_p4_elevation_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                rows=autonomous_tasks_service._p4_packets(root)
            self.assertEqual(len(rows),1)
            packet=rows[0][2]
            self.assertEqual(set(packet["trace_ids"]),{"shop-part-1","shop-part-2"})
            self.assertEqual({row["trace_id"] for row in packet["part_edges"]},{"shop-part-1","shop-part-2"})
            self.assertAlmostEqual(packet["wall_run_span_m"],11.97)
            validated=autonomous_tasks.validate_opening_reply(packet,json.dumps({"total_width_mm":11970,
                "panels":[{"label":"Storefront","width_mm":11970,"sill_mm":0,"head_mm":2700,
                           "printed_text":[],"source":"read_from_image"}]}))
            writes=[]
            with patch.object(autonomous_tasks_service,"_current_trace",side_effect=lambda _root,trace_id:trace_map.get(trace_id)), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"post",side_effect=lambda _web,_project,body:writes.append(body)):
                result=autonomous_tasks_service._apply_p4(None,{"id":"multi-shop"},root,
                    {"packet":packet,"trace_id":parts[0]["trace_id"],"run_id":"test-run","accuracy":{"accuracy":0.2},"quality_label":"AI-determined (below accuracy bar)"},validated)
            widths={body["trace_id"]:sum(row["width_m"] for row in body["openings"])
                    for body in writes}
            self.assertAlmostEqual(widths["shop-part-1"],9.3)
            self.assertAlmostEqual(widths["shop-part-2"],2.67)
            self.assertEqual(result["status"],"below_accuracy_bar")

    def test_run_all_with_current_reviewer_trace_does_not_raise(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            trace={"trace_id":"run-all-trace","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[200,0],[200,100],[0,100],[0,0]],"calibration":{"status":"agreed","mm_per_px":10}}
            with patch.object(autonomous_tasks_service,"_site_packet",return_value=({},"",{})), \
                 patch.object(autonomous_tasks_service,"_north_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_roof_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_p0_initial_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_p0_followup_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_p0_outline_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_p4_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service,"_p6_kitchen_packets",return_value=[]), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_trace_task_image",return_value=Image.new("RGB",(32,32))), \
                 patch.object(autonomous_tasks_service,"_png_bytes",return_value=b"png"):
                result=autonomous_tasks_service.run_all(None,{"id":"trace-project","review_dir":str(root)})
            self.assertIn("tasks",result)

    def test_stand_in_is_recorded_from_post_field_or_json_reply(self):
        for post_marker, reply_marker, expected in [(False,True,True),(True,False,True),(False,False,False)]:
            with self.subTest(post_marker=post_marker,reply_marker=reply_marker), TemporaryDirectory() as temporary, \
                 patch.object(autonomous_tasks_service,"_refresh_geometry_tasks"), \
                 patch.object(autonomous_tasks_service,"_accuracy",return_value={"accuracy":None,"scored":0,"auto_apply":False,"report":""}):
                root=Path(temporary)
                project={"id":"stand-in-test","review_dir":str(root)}
                autonomous_tasks_service._create_run(root,"P6_kitchen","kitchen",{
                    "task":"P6_kitchen","room_label":"Kitchen","images":[],"labels_by_page":{},"words_by_page":{}},"List equipment.")
                reply=json.dumps({"items":[],"stand_in":reply_marker})
                autonomous_tasks_service.post(None,project,{"action":"validate_apply","task":"P6_kitchen","target":"kitchen",
                    "reply":reply,"stand_in":post_marker})
                record=autonomous_tasks_service._current_task(root,"P6_kitchen","kitchen")
                self.assertEqual(record["stand_in"],expected)

    def test_parallel_overlapping_room_edges_within_wall_thickness_are_shared(self):
        first = {"room_id": "shop", "page": 20, "calibration": {"mm_per_px": 10},
                 "points_image_px": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]}
        neighbour = {"room_id": "kitchen", "page": 20, "calibration": {"mm_per_px": 10},
                     "points_image_px": [[0, 115], [100, 115], [100, 215], [0, 215], [0, 115]]}
        matched = autonomous_tasks_service._geometrically_shared_edge_indices(first, [neighbour])
        self.assertIn(2, matched)  # 100% overlap, 150 mm offset, parallel.
        self.assertIn(0, autonomous_tasks_service._geometrically_shared_edge_indices(neighbour, [first]))

    def test_p3_builder_does_not_ask_for_geometrically_shared_wall(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "shop", "space_scope": "comfort_hvac"},
                {"room_id": "kitchen", "space_scope": "comfort_hvac"}]}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": []}}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": []}), encoding="utf-8")
            traces = [
                {"trace_id": "shop-trace", "room_id": "shop", "room_label": "Shop", "page": 20,
                 "points_image_px": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
                 "calibration": {"status": "agreed", "mm_per_px": 10}},
                {"trace_id": "kitchen-trace", "room_id": "kitchen", "room_label": "Kitchen", "page": 20,
                 "points_image_px": [[0, 115], [100, 115], [100, 215], [0, 215], [0, 115]],
                 "calibration": {"status": "agreed", "mm_per_px": 10}},
            ]
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records",
                              return_value=traces), \
                 patch.object(autonomous_tasks_service, "_trace_task_image", return_value=Image.new("RGB", (32, 32))), \
                 patch.object(autonomous_tasks_service, "_png_bytes", return_value=b"png"):
                packets = autonomous_tasks_service._p3_packets(root)
            self.assertEqual(len(packets),1)
            shop=packets[0][2]
            shared_mapping=next(row for row in shop["room_edges"] if row["trace_id"]=="shop-trace" and row["edge_index"]==2)
            self.assertIsNone(shared_mapping["perimeter_run_index"])
            self.assertEqual(len(shop["trace_ids"]),2)

    def test_current_traced_areas_sums_ai_parts_and_reviewer_trace_wins(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            traces = [
                {"trace_id": "ai-part-1", "room_id": "room-shop", "room_label": "Shop", "level_name": "Ground",
                 "page": 20, "calibration": {"status": "agreed", "mm_per_px": 10}, "source_fingerprints": {"source_pdf": "pdf", "vector_page": "v"},
                 "declaration_source": "ai_determined", "ai_run_id": "run-ai", "part_index": 1, "part_count": 2,
                 "ai_quality_label": "AI-determined (below accuracy bar)"},
                {"trace_id": "ai-part-2", "room_id": "room-shop", "room_label": "Shop", "level_name": "Ground",
                 "page": 20, "calibration": {"status": "agreed", "mm_per_px": 10}, "source_fingerprints": {"source_pdf": "pdf", "vector_page": "v"},
                 "declaration_source": "ai_determined", "ai_run_id": "run-ai", "part_index": 2, "part_count": 2,
                 "ai_quality_label": "AI-determined (below accuracy bar)"},
            ]
            geometry = {"entities": [], "room_geometry_proofs": []}
            for index, (trace, area) in enumerate(zip(traces, (10.0, 12.0)), start=1):
                proof_id = f"proof-{index}"
                geometry["entities"].append({"entity_id": proof_id, "kind": "room_geometry_proof",
                    "extraction_method": "reviewer_traced_boundary", "value": {"reviewer_trace_id": trace["trace_id"],
                    "source_fingerprints": trace["source_fingerprints"]}, "source": {"page": 20}})
                geometry["room_geometry_proofs"].append({"proof_id": proof_id, "room_label": "Shop",
                    "level_name": "Ground", "area_m2": area, "calibration": trace["calibration"]})
            (root / "geometry_resolution.json").write_text(json.dumps(geometry), encoding="utf-8")
            artifact_input = {"records": traces, "rooms": [{"room_id": "room-shop", "name": "Shop", "level_name": "Ground"}]}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_artifact_input", return_value=artifact_input):
                result = autonomous_tasks_service.reviewer_room_geometry_service.current_traced_areas(root)
            self.assertEqual(result["room-shop"]["area_m2"], 22.0)
            newer = {**traces[0], "trace_id": "aaa-lexically-first", "ai_run_id": "run-a",
                     "part_index": 1, "part_count": 1, "created_at": "2026-10-05T12:00:00Z"}
            traces[0]["created_at"] = "2026-10-01T12:00:00Z"
            traces[1]["created_at"] = "2026-10-01T12:00:00Z"
            artifact_input["records"].append(newer)
            geometry["entities"].append({"entity_id": "proof-newer", "kind": "room_geometry_proof",
                "extraction_method": "reviewer_traced_boundary", "value": {"reviewer_trace_id": newer["trace_id"],
                "source_fingerprints": newer["source_fingerprints"]}, "source": {"page": 20}})
            geometry["room_geometry_proofs"].append({"proof_id": "proof-newer", "room_label": "Shop",
                "level_name": "Ground", "area_m2": 30.0, "calibration": newer["calibration"]})
            (root / "geometry_resolution.json").write_text(json.dumps(geometry), encoding="utf-8")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_artifact_input", return_value=artifact_input):
                result = autonomous_tasks_service.reviewer_room_geometry_service.current_traced_areas(root)
            self.assertEqual(result["room-shop"]["area_m2"], 30.0)
            self.assertEqual(result["room-shop"]["ai_run_id"], "run-a")
            reviewer = {**traces[0], "trace_id": "reviewer-shop", "declaration_source": "reviewer",
                        "ai_run_id": None, "part_index": None, "part_count": 1}
            artifact_input["records"].append(reviewer)
            geometry["entities"].append({"entity_id": "proof-reviewer", "kind": "room_geometry_proof",
                "extraction_method": "reviewer_traced_boundary", "value": {"reviewer_trace_id": "reviewer-shop",
                "source_fingerprints": reviewer["source_fingerprints"]}, "source": {"page": 20}})
            geometry["room_geometry_proofs"].append({"proof_id": "proof-reviewer", "room_label": "Shop",
                "level_name": "Ground", "area_m2": 8.0, "calibration": reviewer["calibration"]})
            (root / "geometry_resolution.json").write_text(json.dumps(geometry), encoding="utf-8")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_artifact_input", return_value=artifact_input):
                result = autonomous_tasks_service.reviewer_room_geometry_service.current_traced_areas(root)
            self.assertEqual(result["room-shop"]["area_m2"], 8.0)
            self.assertEqual(result["room-shop"]["declaration_source"], "reviewer")

    def test_current_ai_area_uses_full_enclosed_measurement_before_outline_simplification(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "ai-shop", "room_id": "shop", "room_label": "Shop", "level_name": "Ground",
                "page": 20, "calibration": {"status": "agreed", "mm_per_px": 10},
                "source_fingerprints": {"source_pdf": "pdf", "vector_page": "v"},
                "declaration_source": "ai_determined", "ai_run_id": "run-ai", "part_index": 1,
                "part_count": 1, "ai_measured_area_m2": 32.56}
            proof = {"entities": [{"entity_id": "proof", "kind": "room_geometry_proof",
                "extraction_method": "reviewer_traced_boundary", "value": {"reviewer_trace_id": "ai-shop",
                "source_fingerprints": trace["source_fingerprints"]}}],
                "room_geometry_proofs": [{"proof_id": "proof", "room_label": "Shop", "level_name": "Ground",
                    "area_m2": 32.30, "calibration": trace["calibration"]}]}
            (root / "geometry_resolution.json").write_text(json.dumps(proof), encoding="utf-8")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_artifact_input",
                              return_value={"records": [trace], "rooms": [{"room_id": "shop", "name": "Shop", "level_name": "Ground"}]}):
                result = autonomous_tasks_service.reviewer_room_geometry_service.current_traced_areas(root)
            self.assertEqual(result["shop"]["area_m2"], 32.56)

    def test_ai_trace_rerun_replaces_ai_records_and_preserves_other_reviewer_trace(self):
        with TemporaryDirectory() as temporary, patch.object(autonomous_tasks_service.calculation_extraction_service, "post"), \
             patch.object(autonomous_tasks_service.productization, "record_change_if_fingerprint_changed"):
            root = Path(temporary)
            project = {"id": "p0-test", "review_dir": str(root)}
            vector_page = {"page": 20, "page_fingerprint": "v20"}
            calibration = {"source": "ai_read_printed_dimension", "status": "agreed", "mm_per_px": 10,
                "dimension_points_image_px": [[0, 0], [100, 0]], "dimension_value_mm": 1000, "printed_text": "1,000"}
            def trace(trace_id, room_id, declaration_source, page=20):
                trace_calibration = {**calibration, "source": "reviewer_read_printed_dimension"} if declaration_source == "reviewer" else calibration
                return {"trace_id": trace_id, "room_id": room_id, "room_label": room_id.title(), "level_name": "Ground",
                    "page": page, "points_image_px": [[0,0],[100,0],[100,100],[0,100],[0,0]], "snapped_line_ids": [None]*5,
                    "calibration": trace_calibration, "reviewer": "Archie AI (P0)" if declaration_source == "ai_determined" else "QA",
                    "status": "geometry_proposed", "created_at": "now", "source_fingerprints": {"source_pdf": "pdf", "vector_page": "v20"},
                    "declaration_source": declaration_source, **({"ai_run_id": "old-run"} if declaration_source == "ai_determined" else {})}
            original = reviewer_room_geometry.validate_artifact({"records": [trace("old-ai", "shop", "ai_determined"),
                trace("other-page-ai", "kitchen", "ai_determined", page=21),
                trace("reviewer-bar", "bar", "reviewer")]})
            (root / "reviewer_room_geometry.json").write_text(json.dumps(original), encoding="utf-8")
            rows = [{"room_id": "shop", "room_label": "Shop", "page": 20, "points_image_px": [[0,0],[120,0],[120,100],[0,100],[0,0]],
                     "calibration": calibration, "part_index": 1, "part_count": 1, "method": "enclosed_walls"}]
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_source_pdf_fingerprint", return_value="pdf"), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_vector_pages", return_value=[vector_page]), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_rooms", return_value=[
                     {"room_id":"shop","label":"Shop","level_name":"Ground"},{"room_id":"bar","label":"Bar","level_name":"Ground"}]), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service.reviewer_room_geometry, "active_records", return_value=original["records"]):
                prepared = autonomous_tasks_service.reviewer_room_geometry_service.persist_ai_determined_traces(
                    SimpleNamespace(), project, rows, "new-run", "AI-determined (below accuracy bar)")
            self.assertEqual(len(prepared), 1)
            saved = json.loads((root / "reviewer_room_geometry.json").read_text())
            self.assertEqual({row["trace_id"] for row in saved["records"]},
                {prepared[0]["trace_id"], "reviewer-bar", "other-page-ai"})
            self.assertEqual(prepared[0]["part_count"], 1)
            self.assertEqual(prepared[0]["declaration_source"], "ai_determined")

    def test_p0_trace_rows_normalize_clockwise_polygon_for_trace_validator(self):
        from shapely.geometry import Polygon
        with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_rooms",
                          return_value=[{"room_id": "shop", "label": "Shop", "level_name": "Ground"}]), \
             patch.object(autonomous_tasks_service, "_inferred_room_data", return_value=[]):
            # Shapely accepts either winding, while the legacy trace validator
            # expects a positive signed area. This is a clockwise boundary.
            polygon = Polygon([(0, 0), (0, 100), (100, 100), (100, 0), (0, 0)])
            record = {"packet": {"page": 20, "calibration": {
                "source": "ai_read_printed_dimension", "status": "agreed", "mm_per_px": 10,
                "dimension_points_image_px": [[0, 0], [100, 0]], "dimension_value_mm": 1000,
                "printed_text": "1,000"}}}
            rows = autonomous_tasks_service._p0_trace_rows(
                Path("/tmp"), record, [{"label": "Shop", "polygon": polygon, "method": "enclosed_walls"}])
        self.assertEqual(len(rows), 1)
        self.assertGreater(reviewer_room_geometry.polygon_area(rows[0]["points_image_px"]), 0)

    def test_p0_trace_simplification_keeps_area_and_bounds_outline_deviation(self):
        from shapely.geometry import Polygon
        points = [(0, 0)] + [(x, 0) for x in range(10, 101, 10)] + [(100, 100)] + \
                 [(x, 100) for x in range(90, -1, -10)] + [(0, 0)]
        polygon = Polygon(points)
        simplified = autonomous_tasks_service._simplify_trace_polygon(polygon, 15)
        self.assertLess(len(simplified.exterior.coords), len(polygon.exterior.coords))
        self.assertLessEqual(abs(simplified.area - polygon.area) / polygon.area, 0.01)
        self.assertLessEqual(simplified.hausdorff_distance(polygon), 15)

    def test_geometry_tasks_are_persisted_in_order_before_open_outline_decisions(self):
        events = []
        name_packet = ("P0_room_names", "page-20", {}, "prompt", [], "")
        roof_packet = ("shop", {"facts": []}, "roof prompt", [])
        with patch.object(autonomous_tasks_service, "_p0_followup_packets",
                          return_value=[name_packet]), \
             patch.object(autonomous_tasks_service, "_p0_initial_packets", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p0_outline_packets",
                          side_effect=lambda _root: events.append("outline-builder") or []), \
             patch.object(autonomous_tasks_service, "_p3_packets", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p4_packets", return_value=[]), \
             patch.object(autonomous_tasks_service, "_roof_packets", return_value=[roof_packet]), \
             patch.object(autonomous_tasks_service, "_update_record"), \
             patch.object(autonomous_tasks_service, "_create_run",
                          side_effect=lambda _root, task, *_args, **_kwargs: events.append(task) or {"status":"waiting_for_reply"}):
            autonomous_tasks_service._refresh_geometry_tasks(Path("/tmp"))
        self.assertEqual(events, ["P0_room_names", "outline-builder", "P5_roof", "P6_kitchen"])

    def test_p3_fallback_labels_and_reviewer_boundary_precedence(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = {"id": "p3-test", "review_dir": str(root)}
            trace = {"trace_id": "trace-shop", "room_label": "Shop", "edges": [
                {"index": 0, "boundary": "unknown"}, {"index": 1, "boundary": "unknown"},
                {"index": 2, "boundary": "external"}, {"index": 3, "boundary": "unknown"}],
                "edge_sources": {"2": "reviewer"}, "points_image_px": [[0,0],[10,0],[10,10],[0,10],[0,0]]}
            packet = {"trace_id": "trace-shop", "room_label": "Shop", "storefront_edge_index": 0,
                "shared_edges": [3], "nearby_text": "Enclosed shopping centre" ,
                "shared_edge_lengths": {"3": 4},
                "edges": [{"index": 0, "length_m": 11.97}, {"index": 1, "length_m": 4},
                          {"index": 2, "length_m": 11.97}]}
            record = {"packet": packet, "run_id": "p3-run", "accuracy": {"auto_apply": False},
                      "quality_label": "AI-determined (below accuracy bar)"}
            body = {}
            with patch.object(autonomous_tasks_service, "_current_trace", return_value=trace), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post",
                    side_effect=lambda _web, _project, data: body.update(data)):
                result = autonomous_tasks_service._apply_p3(SimpleNamespace(), project, root, record,
                    {"edges": [{"index": 0, "boundary": "unknown", "evidence": ""},
                               {"index": 1, "boundary": "unknown", "evidence": ""},
                               {"index": 2, "boundary": "adjacent_tenancy", "evidence": "neighbouring tenancy"}]})
            rows = result["applied_value"]["edges"]
            self.assertEqual([row["boundary"] for row in rows], ["mall", "adjacent_tenancy", "external", "internal"])
            self.assertEqual(rows[0]["label"], "Assumed (typical for a tenancy in a centre)")
            self.assertEqual(rows[2]["source"], "reviewer")
            self.assertEqual(body["edges"][2]["boundary"], "external")
            self.assertEqual(body["edge_sources"]["2"], "reviewer")

    def test_p0_p3_p4_determinations_export_answer_key_shapes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [
                {"task":"P0_room_names","applied_value":{"rooms":[{"label":"Shop","area_m2":216.1,"source":"printed_on_drawing","method":"enclosed_walls"}]}},
                {"task":"P2_north","applied_value":{"page":5,"plan_up_azimuth_deg":None}},
                {"task":"P3_boundaries","source":"ai_determined","applied_value":{"room":"Shop","edges":[{"edge_length_m":11.97,"boundary":"mall","source":"ai_fallback"}]}},
                {"task":"P4_openings","target":"shop-edge","applied_value":{"page":26,"room":"Shop","total_width_mm":11900,
                    "glazed_panels":[{"width_mm":2025,"sill_mm":1100,"head_mm":2700,"source":"ai_determined"}]}},
                {"task":"P6_kitchen","stand_in":False,"applied_value":{"items":[{"type":"oven","count":1}]}},
            ]
            with patch.object(autonomous_tasks_service, "_all_current", return_value=records):
                result = autonomous_tasks_service._export_determinations(root)
            self.assertEqual(result["P0_rooms"], [{"label":"Shop","area_m2":216.1,"source":"printed_on_drawing","method":"enclosed_walls"}])
            self.assertEqual(result["P2_north"], [{"page":5,"plan_up_azimuth_deg":None,"source":"ai_determined"}])
            self.assertEqual(result["P3_boundaries"], [{"room":"Shop","edge_length_m":11.97,"boundary":"mall","source":"ai_fallback"}])
            self.assertEqual(result["P6_kitchen"], [{"type":"oven","count":1,"source":"ai_determined"}])
            self.assertEqual(result["P4_openings"], [{"page":26,"room":"Shop","total_width_mm":11900,
                "glazed_panels":[{"width_mm":2025,"sill_mm":1100,"head_mm":2700,"source":"ai_determined"}]}])
            self.assertEqual(json.loads((root/"ai_tasks"/"determinations.json").read_text()), result)

    def test_p3_page_export_preserves_part_edges_and_scores_case_a(self):
        from ai.autonomous_task_scoring import score_case
        rooms = [
            {"trace_id": "shop-part-1", "room": "Shop", "edges": [
                {"index": 0, "edge_length_m": 4.67, "boundary": "mall", "source": "reviewer"},
                {"index": 1, "edge_length_m": 3.88, "boundary": "mall"},
                {"index": 2, "edge_length_m": 3.0, "boundary": "internal"},
                {"index": 3, "edge_length_m": 3.0, "boundary": "internal"}]},
            {"trace_id": "shop-part-2", "room": "Shop", "edges": [
                {"index": 0, "edge_length_m": 2.52, "boundary": "mall", "source": "ai_fallback"},
                {"index": 1, "edge_length_m": 3.0, "boundary": "internal"}]},
        ]
        page_record = {"task": "P3_boundaries", "target": "page-20", "source": "ai_determined",
                       "applied_value": {"page": 20, "rooms": rooms}}
        # A legacy record for the same trace/edge must not double-count the frontage.
        legacy = {"task": "P3_boundaries", "packet": {"trace_id": "shop-part-1"},
                  "applied_value": {"room": "Shop", "edges": [rooms[0]["edges"][0]]}}
        expected = [{"room": room["room"], "edge_length_m": edge["edge_length_m"],
                     "boundary": edge["boundary"], "source": edge.get("source", "ai_determined")}
                    for room in rooms for edge in room["edges"]]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(autonomous_tasks_service, "_all_current", return_value=[legacy, page_record]):
                result = autonomous_tasks_service._export_determinations(root)
            self.assertEqual(result["P3_boundaries"], expected)
            self.assertEqual(sum(row["edge_length_m"] == 3.0 for row in result["P3_boundaries"]), 3)
            for path in (root / "ai_tasks" / "determinations.json", root / "ai_task_determinations.json"):
                self.assertEqual(json.loads(path.read_text())["P3_boundaries"], expected)
            key = json.loads((Path(__file__).resolve().parents[1] / "evaluations/autonomous/caseA.json").read_text())
            scored = score_case(key, result)["tasks"]["P3_boundaries"]
            shop = next(row for row in scored if row["item"] == "Shop mall length")
            self.assertEqual(shop["status"], "correct")
            self.assertIn("11.07 m mall", shop["detail"])

    def test_contractor_roof_export_is_skipped_from_ai_score(self):
        import importlib.util
        tool_path = Path(__file__).resolve().parents[1] / "tools" / "evaluate_autonomous_tasks.py"
        spec = importlib.util.spec_from_file_location("evaluate_autonomous_tasks_contractor_test", tool_path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        root = Path(__file__).resolve().parents[1]
        key = {"case_id": "contractor", "tasks": {"P5_roof": {"rooms": [{"room": "Shop", "roof": "not_exposed"}]}}}
        determinations = {"P5_roof": [{"room": "Shop", "roof": "not_exposed", "source": "reviewer"}]}
        report = evaluator.evaluate_case_excluding_contractor_answers(key, determinations)
        self.assertNotIn("P5_roof", report["tasks"])
        self.assertEqual(report["not_ai_determined"]["P5_roof"], ["Shop"])
        markdown = evaluator.render_markdown([report], {}, .85)
        self.assertIn("contractor/reviewer answer(s): Shop (not AI-determined)", markdown)
        self.assertIn("not applicable for AI coverage", markdown)
        self.assertNotIn("P5_roof", report["tasks"])

        mixed_key = {"case_id": "mixed", "tasks": {"P5_roof": {"rooms": [
            {"room": "Shop", "roof": "not_exposed"}, {"room": "Kitchen", "roof": "not_exposed"}]}}}
        mixed = evaluator.evaluate_case_excluding_contractor_answers(mixed_key, {"P5_roof": [
            {"room": "Shop", "roof": "not_exposed", "source": "contractor"},
            {"room": "Kitchen", "roof": "not_exposed", "source": "ai_determined"}]})
        self.assertEqual([row["item"] for row in mixed["tasks"]["P5_roof"]], ["Kitchen"])
        self.assertNotIn("not_applicable", mixed)

    def test_p3_batch_resolves_current_geometry_once(self):
        traces = [{"trace_id": f"part-{i}", "room_id": "shop", "room_label": "Shop",
                   "points_image_px": [[0,0],[100,0]], "edges": [{"index": 0, "boundary": "unknown"}]}
                  for i in range(6)]
        packet = {"trace_ids": [row["trace_id"] for row in traces], "room_edges": [
            {"trace_id": row["trace_id"], "edge_index": 0, "length_m": 1.0,
             "perimeter_run_index": 0} for row in traces], "edges": [{"index": 0}], "runs": [],
            "storefront_run_index": 0, "nearby_text": "", "short_run_inheritance": {}}
        record = {"packet": packet, "accuracy": {"auto_apply": True}, "run_id": "p3-profile"}
        with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=traces) as current_records, \
             patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post") as classify, \
             patch.object(autonomous_tasks_service.calculation_extraction_service, "post") as rebuild:
            autonomous_tasks_service._apply_p3(SimpleNamespace(), {"id": "profile"}, Path("."), record,
                {"edges": [{"index": 0, "boundary": "external", "evidence": "external wall"}]})
        self.assertEqual(current_records.call_count, 1)
        self.assertEqual(classify.call_count, 6)
        self.assertTrue(all(call.kwargs["_defer_evidence_rebuild"] for call in classify.call_args_list))
        self.assertEqual(rebuild.call_count, 1)

    def test_site_cross_check_ignores_page_and_folds_case_and_whitespace(self):
        packet = {"rule_based_top_candidate": {"text": "TENANCY MZ01, M38, MELROSE CENTRAL", "page": 20}}
        reply = {"site": {"text": "tenancy   mz01, m38, melrose central", "page": 1}}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, reply)["status"], "agrees")

    def test_site_cross_check_accepts_contained_real_project_excerpt_and_rejects_other_centre(self):
        full_line = "PROJECT NAME: BUTCHER'S BUFFET TENANCY MZ01,M38, MELROSE CENTRAL PROJECT ADDRESS"
        page_twenty = "TENANCY MZ01,M38, MELROSE CENTRAL"
        packet = {"excerpts": [{"page": 1, "text": full_line}],
                  "rule_based_top_candidate": {"text": page_twenty, "page": 20}}
        for ai_text in ("MELROSE CENTRAL", full_line, page_twenty):
            validated = autonomous_tasks.validate_site_reply(packet, json.dumps({
                "site": {"text": ai_text, "page": 1, "kind": "tenancy_in_centre"},
                "consultant_addresses": [],
            }))
            self.assertEqual(autonomous_tasks.site_cross_check(packet, validated)["status"], "agrees")
        result = autonomous_tasks.site_cross_check(
            {"rule_based_top_candidate": {"text": page_twenty, "page": 20}},
            {"site": {"text": "TENANCY MZ01,M38, DIFFERENT CENTRE", "page": 1}},
        )
        self.assertEqual(result["status"], "disagrees")

    def test_site_reply_must_quote_supplied_page_excerpt(self):
        packet = {"excerpts": [{"page": 2, "text": "TENANCY G12, HARBOUR CENTRE"}]}
        checked = autonomous_tasks.validate_site_reply(packet, json.dumps({
            "site": {"text": "TENANCY G12, HARBOUR CENTRE", "page": 2, "kind": "tenancy_in_centre"},
            "consultant_addresses": [],
        }))
        self.assertEqual(checked["site"]["page"], 2)
        with self.assertRaisesRegex(ValueError, "exact substring"):
            autonomous_tasks.validate_site_reply(packet, json.dumps({
                "site": {"text": "Harbour Centre, Sydney NSW 2000", "page": 2, "kind": "street_address"},
                "consultant_addresses": [],
            }))

    def test_site_cross_check_exposes_conflict_against_rule_candidate(self):
        packet = {"rule_based_top_candidate": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 1}}
        ai_result = {"site": {"text": "42 TEST STREET", "page": 2, "kind": "street_address"}}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, ai_result)["status"], "disagrees")
        ai_result["site"] = {"text": "TENANCY K2, CENTRAL ARCADE", "page": 20, "kind": "tenancy_in_centre"}
        self.assertEqual(autonomous_tasks.site_cross_check(packet, ai_result)["status"], "agrees")

    def test_site_conflict_retries_once_then_uses_rule_candidate_as_assumed_fallback(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = {"text": "TENANCY K2, CENTRAL ARCADE", "page": 1, "kind": "tenancy_in_centre", "confidence": .8}
            record = {"packet": {"rule_based_top_candidate": candidate, "excerpts": [
                {"page": 1, "text": candidate["text"]}, {"page": 2, "text": "42 TEST STREET"}]},
                "prompt": "Site prompt", "retry_count": 0, "run_id": "run-1", "accuracy": {"accuracy": .7},
                "quality_label": "AI-determined (below accuracy bar)", "reply_hash": "hash", "input_fingerprint": "fp"}
            conflicting = {"site": {"text": "42 TEST STREET", "page": 2, "kind": "street_address"}, "consultant_addresses": []}
            retry = autonomous_tasks_service._apply_p1(root, {}, record, conflicting)
            self.assertEqual(retry["status"], "waiting_for_reply")
            self.assertEqual(retry["retry_count"], 1)
            self.assertIn("Cross-check conflict", retry["prompt"])
            fallback = autonomous_tasks_service._apply_p1(root, {}, retry, conflicting)
            self.assertEqual(fallback["source"], "ai_fallback")
            self.assertEqual(fallback["status"], "applied_fallback")
            self.assertEqual(fallback["applied_value"]["site_text"], candidate["text"])

    def test_declare_north_accepts_services_plan_page_outside_trace_page_context(self):
        from backend import reviewer_room_geometry_service
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": number, "type": "services_or_lighting_plan"} for number in range(20, 26)]}}))
            project = {"id": "north-test", "review_dir": temporary}
            web = SimpleNamespace(update_project=lambda _project: None)
            from backend import calculation_extraction_service, productization
            with patch.object(calculation_extraction_service, "post", return_value={}), \
                 patch.object(reviewer_room_geometry_service, "_response", return_value={}), \
                 patch.object(productization, "record_change_if_fingerprint_changed"):
                reviewer_room_geometry_service.post(web, project, {"action": "declare_north", "page": 25,
                    "reviewer": "Archie AI", "plan_up_azimuth_deg": 0.0,
                    "declaration_source": "ai_determined", "ai_run_id": "north-run"})
            saved = json.loads((root / "reviewer_room_geometry.json").read_text())
            self.assertEqual(saved["page_north"]["25"]["plan_up_azimuth_deg"], 0.0)

    def test_p2_applies_agreement_across_floor_rcp_and_services_pages(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages = [{"page": number, "type": ("floor_plan" if number < 22 else
                      "reflected_ceiling_plan" if number < 25 else "services_or_lighting_plan")}
                     for number in range(20, 26)]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}))
            records = {}
            for number in range(20, 26):
                record = autonomous_tasks_service._create_run(root, "P2_north", f"page-{number}",
                    {"task": "P2_north", "page": number}, "north")
                record.update({"status": "waiting_for_reply", "validation": {"found": True,
                    "plan_up_azimuth_deg": 0.0, "description": "arrow up"},
                    "accuracy": {"auto_apply": True}, "source": "ai_determined"})
                autonomous_tasks_service._update_record(root, record)
                records[number] = record
            current = records[20]
            applied = []
            def declare(_web, _project, body, **kwargs):
                applied.append((body["page"], kwargs.get("_defer_evidence_rebuild")))
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=declare), \
                 patch.object(autonomous_tasks_service.calculation_extraction_service, "post", return_value={}) as rebuild:
                result = autonomous_tasks_service._apply_p2(object(), {"id": "bb", "review_dir": str(root)},
                    root, current, {"found": True, "plan_up_azimuth_deg": 0.0, "description": "arrow up"})
            self.assertEqual(applied, [(number, True) for number in range(20, 26)])
            self.assertEqual(rebuild.call_count, 1)
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["applied_value"]["plan_up_azimuth_deg"], 0.0)

    def test_p2_rejected_page_does_not_block_other_north_applications(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            pages = [{"page": number, "type": "floor_plan"} for number in range(20, 23)]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}))
            records = {}
            for number in range(20, 23):
                record = autonomous_tasks_service._create_run(root, "P2_north", f"page-{number}",
                    {"task": "P2_north", "page": number}, "north")
                record.update({"status": "waiting_for_reply", "validation": {"found": True,
                    "plan_up_azimuth_deg": 0.0}, "accuracy": {"auto_apply": True}})
                autonomous_tasks_service._update_record(root, record)
                records[number] = record
            applied = []
            def declare(_web, _project, body, **kwargs):
                if body["page"] == 21:
                    raise ValueError("page unavailable")
                applied.append(body["page"])
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=declare):
                result = autonomous_tasks_service._apply_p2(None, {"id": "bb", "review_dir": str(root)}, root,
                    records[20], {"found": True, "plan_up_azimuth_deg": 0.0})
            self.assertEqual(applied, [20, 22])
            self.assertEqual(result["status"], "applied")
            rejected = autonomous_tasks_service._current_task(root, "P2_north", "page-21")
            self.assertEqual(rejected["status"], "blocked")
            self.assertIn("could not be applied", rejected["block_reason"])

    def test_north_reply_maps_crop_coordinates_to_page_and_computes_bearing(self):
        packet = {"page": 4, "crops": [{"crop_index": 0, "width_px": 100, "height_px": 100,
                    "page_bbox": [200, 300, 400, 400]}]}
        result = autonomous_tasks.validate_north_reply(packet, json.dumps({
            "found": True, "crop_index": 0, "tail_px": [50, 70], "tip_px": [50, 20],
            "labelled_north": True, "description": "Arrow points up",
        }))
        self.assertEqual(result["tail_page_px"], [400.0, 580.0])
        self.assertEqual(result["tip_page_px"], [400.0, 380.0])
        self.assertAlmostEqual(result["plan_up_azimuth_deg"], 0)
        with self.assertRaisesRegex(ValueError, "inside the selected crop"):
            autonomous_tasks.validate_north_reply(packet, json.dumps({"found": True, "crop_index": 0,
                "tail_px": [101, 70], "tip_px": [50, 20]}))

    def test_north_consensus_requires_tolerance_or_strict_majority(self):
        self.assertEqual(autonomous_tasks.north_consensus([(1, 0), (2, 2)]) ["status"], "agreed")
        self.assertEqual(autonomous_tasks.north_consensus([(1, 0), (2, 90)]) ["status"], "disagreement")
        majority = autonomous_tasks.north_consensus([(1, 0), (2, 2), (3, 90)])
        self.assertEqual(majority["status"], "majority")
        self.assertAlmostEqual(majority["winner"], 1)

    def test_roof_requires_explicit_drawing_evidence_and_has_no_building_type_fallback(self):
        self.assertIsNone(autonomous_tasks.TASKS["P5_roof"]["fallback"])
        self.assertFalse(autonomous_tasks_service._has_explicit_roof_evidence({"facts": []}))
        self.assertFalse(autonomous_tasks_service._has_explicit_roof_evidence({"facts": [{"text": "Level 2"}]}))
        self.assertTrue(autonomous_tasks_service._has_explicit_roof_evidence({"facts": [{"text": "ROOF OVER TENANCY"}]}))

    def test_roof_reply_must_quote_drawing_fact(self):
        packet = {"facts": [{"page": 9, "text": "ROOF OVER TENANCY"}], "site_name": "", "tenancy_prefix": "",
                  "room": {"level_name": "Level 1"}}
        self.assertEqual(autonomous_tasks.validate_roof_reply(packet, json.dumps({"roof": "exposed", "evidence": "ROOF OVER TENANCY"}))["roof"], "exposed")
        with self.assertRaisesRegex(ValueError, "quoted drawing fact"):
            autonomous_tasks.validate_roof_reply(packet, json.dumps({"roof": "exposed", "evidence": "probably exposed"}))

    def test_roof_answer_unknown_and_no_evidence_needs_contractor_answer(self):
        packet = {"facts": [], "room": {"room_id": "shop", "room_label": "Shop"}}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            record = {"task": "P5_roof", "target": "shop", "packet": packet, "accuracy": {"auto_apply": True}}
            result = autonomous_tasks_service._apply_p5(
                None, {"review_dir": temporary}, root, record, {"roof": "unknown", "evidence": ""})
            self.assertEqual(result["status"], "needs_contractor_answer")
            self.assertIn("Is there a floor or another tenancy", result["block_reason"])

    def test_run_all_surfaces_no_evidence_roof_question_in_contractor_labels(self):
        with TemporaryDirectory() as temporary:
            project = {"id": "roof-no-evidence", "review_dir": temporary}
            packet = {"facts": [], "room": {"room_id": "shop", "room_label": "Shop"}}
            with patch.object(autonomous_tasks_service, "_site_packet", return_value=({}, "", {})), \
                 patch.object(autonomous_tasks_service, "_north_packets", return_value=[]), \
                 patch.object(autonomous_tasks_service, "_roof_packets", return_value=[("shop", packet, "roof prompt", [])]):
                autonomous_tasks_service.run_all(SimpleNamespace(), project)
            record = autonomous_tasks_service._current_task(Path(temporary), "P5_roof", "shop")
            self.assertEqual(record["status"], "needs_contractor_answer")
            label = autonomous_tasks_service.get_labels(project)["tasks"][0]
            self.assertEqual(label["question"], "Is there a floor or another tenancy directly above this shop, or is it the roof?")
            self.assertEqual(label["room_label"], "Shop")

    def test_contractor_roof_answers_use_reviewer_classification_and_not_sure_stays_unknown(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            project = {"id": "roof-project", "review_dir": temporary}
            web = SimpleNamespace()
            record = autonomous_tasks_service._create_run(root, "P5_roof", "shop", {
                "room": {"room_id": "shop", "room_label": "Shop"}, "facts": []}, "roof prompt")
            record.update({"status": "needs_contractor_answer", "block_reason": autonomous_tasks_service.ROOF_CONTRACTOR_QUESTION})
            autonomous_tasks_service._update_record(root, record)
            calls = []
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"artifact": artifact}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body, **_kwargs: calls.append(body)):
                result = autonomous_tasks_service._answer_roof(web, project, root, {"task": "P5_roof", "target": "shop", "answer": "not_sure"})
                self.assertEqual(calls[-1]["roof"], "unknown")
                self.assertEqual(calls[-1]["reviewer"], "Answered by the contractor")
                saved = autonomous_tasks_service._current_task(root, "P5_roof", "shop")
                self.assertEqual(saved["status"], "contractor_answered_not_sure")
                self.assertEqual(saved["applied_value"], {})
                self.assertEqual(result["tasks"][0]["status"], "contractor_answered_not_sure")
                labels = autonomous_tasks_service.get_labels(project)["tasks"]
                self.assertEqual(labels[0]["status"], "contractor_answered_not_sure")
                self.assertIn("not assessed", labels[0]["message"])

    def test_contractor_roof_answer_records_floor_above_as_reviewer_declaration(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {"trace_id": "trace-shop", "room_id": "shop", "room_label": "Shop", "edges": [], "roof": "unknown"}
            second_part = {**trace, "trace_id": "trace-shop-part-2", "page": 21}
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace, second_part]}), encoding="utf-8")
            project = {"id": "roof-answer", "review_dir": temporary}
            record = autonomous_tasks_service._create_run(root, "P5_roof", "shop", {"room": {"room_label": "Shop"}}, "prompt")
            record["status"] = "needs_contractor_answer"
            autonomous_tasks_service._update_record(root, record)
            calls = []
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"artifact": artifact}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body, **_kwargs: calls.append(body)):
                autonomous_tasks_service._answer_roof(SimpleNamespace(), project, root, {
                    "task": "P5_roof", "target": "shop", "answer": "floor_tenancy_above"})
            self.assertEqual(len(calls), 2)
            self.assertEqual({call["trace_id"] for call in calls},{"trace-shop","trace-shop-part-2"})
            self.assertTrue(all(call["action"]=="classify_envelope" for call in calls))
            self.assertTrue(all(call["roof"]=="not_exposed" for call in calls))
            self.assertTrue(all(call["reviewer"]=="Answered by the contractor" for call in calls))
            self.assertTrue(all(call["confirm_roof"] for call in calls))
            saved = autonomous_tasks_service._current_task(root, "P5_roof", "shop")
            self.assertEqual(saved["status"], "applied")
            self.assertEqual(saved["applied_value"]["label"], "Answered by the contractor")
            exported = json.loads((root / "ai_task_determinations.json").read_text())
            self.assertEqual(exported["P5_roof"][0]["source"], "reviewer")

    def test_p6_before_any_room_outline_reports_waiting_for_p0(self):
        with patch.object(autonomous_tasks_service,"_load_inputs",return_value=({}, {}, {})), \
             patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[]):
            packets=autonomous_tasks_service._p6_kitchen_packets(Path("/tmp"))
        self.assertEqual(packets[0][-1],"Waiting for room outlines (P0).")

    def test_p4_omits_rooms_with_no_external_or_mall_perimeter_run(self):
        from ai import ceiling_volume_resolution
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"room_use_resolution.json").write_text(json.dumps({"records":[{"room_id":"shop","space_scope":"comfort_hvac"}]}),encoding="utf-8")
            (root/"ai_input.json").write_text(json.dumps({"drawing_set":{"pages":[{"page":26,"title":"Storefront Elevation"}]}}),encoding="utf-8")
            trace={"trace_id":"shop","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[100,0],[100,100],[0,100],[0,0]],"calibration":{"status":"agreed","mm_per_px":10}}
            p3={"task":"P3_boundaries","target":"page-20","status":"applied",
                "applied_value":{"runs":[{"index":i,"boundary":"internal"} for i in range(4)]}}
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service,"_current_task",return_value=p3), \
                 patch.object(ceiling_volume_resolution,"values_by_room",return_value={}):
                self.assertEqual(autonomous_tasks_service._p4_packets(root),[])

    def test_p3_persists_boundary_exclusion_quality_label(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            trace={"trace_id":"shop","room_id":"shop","room_label":"Shop","page":20,
                "points_image_px":[[0,0],[100,0]],"edges":[{"index":0,"boundary":"unknown"}]}
            packet={"page":20,"trace_ids":["shop"],"room_edges":[{"trace_id":"shop","edge_index":0,
                "perimeter_run_index":0,"length_m":1.0}],"storefront_run_index":None,"nearby_text":""}
            record={"packet":packet,"run_id":"p3-stand-in","accuracy":{"accuracy":0.5},
                "quality_label":"AI-determined (below accuracy bar)","stand_in":True}
            calls=[]
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"current_records",return_value=[trace]), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service,"post",side_effect=lambda _w,_p,body,**_kwargs:calls.append(body)):
                autonomous_tasks_service._apply_p3(None,{"id":"stand-in-p3"},root,record,
                    {"edges":[{"index":0,"boundary":"adjacent_tenancy","evidence":"quoted note"}]})
            evidence=calls[0]["boundary_evidence"][0]
            self.assertEqual(evidence["label"],"Stand-in (test)")

    def test_run_reused_when_inputs_match_and_new_run_created_when_they_change(self):
        with TemporaryDirectory() as temporary, patch.object(autonomous_tasks_service, "_accuracy", return_value={
                "accuracy": 0.9, "scored": 5, "auto_apply": True, "report": "test.json"}):
            root = Path(temporary)
            first = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": []}, "Prompt")
            same = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": []}, "Prompt")
            changed = autonomous_tasks_service._create_run(root, "P1_site", "project", {"excerpts": [{"page": 1}]}, "Prompt")
            self.assertEqual(first["run_id"], same["run_id"])
            self.assertNotEqual(first["run_id"], changed["run_id"])
            self.assertTrue((root / "ai_tasks/P1_site/project/runs" / first["run_id"] / "reply.json").parent.is_dir())

    def test_accuracy_registry_is_required_and_requires_ten_scored_items(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "accuracy.json"
            with patch.object(autonomous_tasks_service, "ACCURACY_PATH", path):
                self.assertFalse(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])
                path.write_text(json.dumps({"tasks": {"P1_site": {"accuracy": 1.0, "scored": 9}}}))
                self.assertFalse(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])
                path.write_text(json.dumps({"tasks": {"P1_site": {"accuracy": .9, "scored": 10}}}))
                self.assertTrue(autonomous_tasks_service._accuracy("P1_site")["auto_apply"])

    def test_accuracy_recorder_rejects_standins_partial_cases_and_small_samples(self):
        tool_path = Path(__file__).resolve().parents[1] / "tools" / "evaluate_autonomous_tasks.py"
        spec = importlib.util.spec_from_file_location("evaluate_autonomous_tasks_test", tool_path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        self.assertTrue(evaluator._contains_stand_in({"P1_site": {"stand_in": True}}))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            keys = root / "evaluations" / "autonomous"
            keys.mkdir(parents=True)
            for number in range(10):
                (keys / f"case{number}.json").write_text(json.dumps({"case_id": f"case{number}",
                    "tasks": {"P1_site": {"site_must_contain": ["site"]},
                              "P5_roof": {"rooms": [{"room": "Shop", "roof": "not_exposed"}]}}}))
            evaluator.ROOT = root
            summary = {"P1_site": {"correct": 10, "wrong": 0, "missing": 0, "scored": 10},
                       "P5_roof": {"correct": 10, "wrong": 0, "missing": 0, "scored": 10}}
            complete = [{"case_id": f"case{number}", "tasks": {"P1_site": [{"status": "correct"}],
                        "P5_roof": [{"status": "correct"}]}}
                        for number in range(10)]
            self.assertTrue(evaluator._recordable_accuracy(complete, summary, .85)["tasks"]["P1_site"]["auto_apply"])
            with self.assertRaisesRegex(ValueError, "case9"):
                evaluator._recordable_accuracy(complete[:-1], summary, .85)
            with self.assertRaisesRegex(ValueError, "at least 10"):
                evaluator._recordable_accuracy(complete, {"P1_site": {**summary["P1_site"], "scored": 9}}, .85)
            record_reports = [*complete[:-1], {"case_id": "case9", "tasks": {"P1_site": [{"status": "correct"}]},
                              "not_applicable": {"P5_roof": ["Shop"]}}]
            record_summary = {"P1_site": summary["P1_site"],
                              "P5_roof": {"correct": 10, "wrong": 0, "missing": 0, "scored": 10}}
            recorded = evaluator._recordable_accuracy(record_reports, record_summary, .85)
            self.assertEqual(recorded["tasks"]["P5_roof"]["case_ids"], [f"case{i}" for i in range(9)])

    def test_record_cli_refuses_stand_in_marker(self):
        tool_path = Path(__file__).resolve().parents[1] / "tools" / "evaluate_autonomous_tasks.py"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "case.json"
            determination = root / "determinations.json"
            key.write_text(json.dumps({"case_id": "standin", "tasks": {"P1_site": {}}}), encoding="utf-8")
            determination.write_text(json.dumps({"stand_in": True, "P1_site": {"site_text": "sample"}}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(tool_path), f"{key}={determination}", "--record",
                                     "--output-dir", str(root / "reports")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("marked stand_in", result.stderr)

    def test_manual_reply_attempts_are_archived_without_overwriting_rejected_reply(self):
        with TemporaryDirectory() as temporary, patch.object(autonomous_tasks_service, "_accuracy", return_value={
                "accuracy": 0.7, "scored": 3, "auto_apply": False, "report": "test.json"}), \
             patch.object(autonomous_tasks_service.productization, "record_change_if_fingerprint_changed"):
            root = Path(temporary)
            project = {"id": "test-project", "review_dir": str(root)}
            web = SimpleNamespace()
            packet = {"excerpts": [{"page": 3, "text": "TENANCY K2, CENTRAL ARCADE"}],
                      "rule_based_top_candidate": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 3,
                                                    "kind": "tenancy_in_centre", "confidence": .8}}
            autonomous_task_service_record = autonomous_tasks_service._create_run(
                root, "P1_site", "project", packet, "Site prompt")
            bad = autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": "P1_site", "target": "project",
                "reply": "not json", "model_note": "stand-in"})
            record = autonomous_tasks_service._current_task(root, "P1_site", "project")
            attempts = sorted((root / "ai_tasks/P1_site/project/runs" / autonomous_task_service_record["run_id"] / "reply_attempts").glob("*.json"))
            self.assertEqual(len(attempts), 1)
            first_reply = json.loads(attempts[0].read_text(encoding="utf-8"))
            self.assertEqual(first_reply["outcome"], "rejected")
            good = json.dumps({"site": {"text": "TENANCY K2, CENTRAL ARCADE", "page": 3, "kind": "tenancy_in_centre"}, "consultant_addresses": []})
            autonomous_tasks_service.post(web, project, {"action": "validate_apply", "task": "P1_site", "target": "project",
                "reply": good, "model_note": "test", "stand_in": True})
            attempts = sorted((root / "ai_tasks/P1_site/project/runs" / autonomous_task_service_record["run_id"] / "reply_attempts").glob("*.json"))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(json.loads(attempts[0].read_text(encoding="utf-8"))["raw_reply"], "not json")
            self.assertEqual(json.loads(attempts[1].read_text(encoding="utf-8"))["raw_reply"], good)
            self.assertEqual(autonomous_tasks_service._current_task(root, "P1_site", "project")["status"], "below_accuracy_bar")
            self.assertTrue(json.loads((root / "ai_tasks/determinations.json").read_text())["stand_in"])

    def test_roof_packets_only_include_explicit_comfort_scope_traces(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "comfort", "space_scope": "comfort_hvac"},
                {"room_id": "process", "space_scope": "refrigeration_process"},
                {"room_id": "excluded", "space_scope": "not_a_room"},
                {"room_id": "unresolved", "space_scope": "unresolved_scope"},
            ]}), encoding="utf-8")
            traces = [{"trace_id": room_id, "room_id": room_id, "room_label": room_id.title(), "level_name": "Ground", "page": 1,
                       "calibration": {"status": "agreed"}} for room_id in ("comfort", "process", "excluded", "unresolved")]
            with patch.object(autonomous_tasks_service, "_load_inputs", return_value=({}, {}, {})), \
                 patch.object(autonomous_tasks_service, "_latest_site", return_value={}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"review_dir": root}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records", return_value=traces), \
                 patch.object(autonomous_tasks, "build_roof_facts", side_effect=lambda _a, _s, room: {"room": room}), \
                 patch.object(autonomous_tasks, "roof_prompt", return_value="prompt"):
                packets = autonomous_tasks_service._roof_packets(root)
            self.assertEqual([row[0] for row in packets], ["comfort"])

    def test_ai_declaration_metadata_validates_and_legacy_fields_remain_absent(self):
        north = {"1": {"page": 1, "plan_up_azimuth_deg": 0, "source": "reviewer_typed_page_up_bearing",
                        "reviewer": "Archie AI", "declared_at": "now", "declaration_source": "ai_determined", "ai_run_id": "run-1"}}
        checked = reviewer_room_geometry.validate_artifact({"records": [], "page_north": north})
        self.assertEqual(checked["page_north"]["1"]["declaration_source"], "ai_determined")
        self.assertEqual(reviewer_room_geometry.validate_artifact({"records": []}).keys(), {"schema_version", "records", "fingerprint"})
        with self.assertRaisesRegex(ValueError, "run ID"):
            reviewer_room_geometry.validate_artifact({"records": [], "page_north": {"1": {
                **north["1"], "ai_run_id": ""}}})


    def test_operator_time_is_stored_with_the_reply_attempt_and_bounded(self):
        from backend import autonomous_tasks_service as service
        self.assertEqual(service._operator_seconds(95), 95.0)
        for bad in (-1, 90000, "95", True, None):
            self.assertIsNone(service._operator_seconds(bad))
        with TemporaryDirectory() as temp:
            root = Path(temp)
            record = {"task": "P1_site", "target": "project", "run_id": "r1"}
            (root / "ai_tasks" / "P1_site" / "project" / "runs" / "r1").mkdir(parents=True)
            path = service._archive_reply(root, record, "{}", "GPT", 61.5)
            self.assertEqual(json.loads(path.read_text())["operator_seconds"], 61.5)
            self.assertEqual(record["reply_attempts"][-1]["operator_seconds"], 61.5)
            service._archive_reply(root, record, "{}", "GPT")
            self.assertNotIn("operator_seconds", record["reply_attempts"][-1])

    def test_labels_view_reports_progress_over_unfinished_checks_without_prompts(self):
        records = [{"task": "P0_dimensions", "target": "d1", "status": "applied", "applied_value": {"value_mm": 11825},
                    "prompt": "secret prompt"},
                   {"task": "P0_wall_styles", "target": "b1", "status": "waiting_for_reply", "prompt": "secret prompt"},
                   {"task": "P1_site", "target": "project", "status": "blocked", "prompt": ""}]
        with patch.object(autonomous_tasks_service, "_all_current", return_value=records):
            labels = autonomous_tasks_service.get_labels({"id": "p", "review_dir": "/tmp/none"})
            changed = [dict(records[0]), dict(records[1], status="applied", applied_value={"wall_style_ids": []}), records[2]]
        with patch.object(autonomous_tasks_service, "_all_current", return_value=changed):
            later = autonomous_tasks_service.get_labels({"id": "p", "review_dir": "/tmp/none"})
        self.assertEqual([row["task"] for row in labels["tasks"]], ["P0_dimensions"])
        self.assertEqual({key: labels["progress"][key] for key in ("total", "waiting", "blocked")},
                         {"total": 3, "waiting": 1, "blocked": 1})
        self.assertNotIn("secret prompt", json.dumps(labels))
        self.assertNotEqual(labels["progress"]["marker"], later["progress"]["marker"])
        self.assertEqual(later["progress"]["waiting"], 0)

    def test_page_analysis_cache_reuses_context_and_preserves_render(self):
        from PIL import Image, ImageChops
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"synthetic-pdf-content")
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(source)}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": []}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": []}}), encoding="utf-8")
            calls = []

            def build():
                calls.append("built")
                return {"objects": [{"kind": "line", "style": (1.0, 0.5, None), "style_id": "s1",
                                      "points": [(1.0, 2.0), (3.0, 4.0)], "filled": False}],
                        "image": Image.new("RGB", (20, 12), (10, 20, 30)),
                        "viewport": (0, 0, 20, 12), "image_scale": 2.5, "page_meta": {"page": 3}}

            with page_analysis_cache.operation(root) as cache:
                cold = page_analysis_cache.get_context(root, 3, build)
                self.assertEqual(cache.stats["page_renders"], {"3": 1})
                self.assertEqual(cache.stats["page_object_extractions"], {"3": 1})
                memory = page_analysis_cache.get_context(root, 3, build)
                self.assertIs(cold, memory)
            self.assertEqual(calls, ["built"])

            with page_analysis_cache.operation(root) as cache:
                warm = page_analysis_cache.get_context(root, 3, build)
                self.assertEqual(cache.stats["cache_hits"], {"3": 1})
                self.assertEqual(cache.stats["page_renders"], {})
            self.assertEqual(warm["image"].size, cold["image"].size)
            self.assertEqual(ImageChops.difference(warm["image"], cold["image"]).getbbox(), None)
            self.assertEqual(warm["objects"], cold["objects"])
            self.assertEqual(warm["page_meta"], cold["page_meta"])
            self.assertEqual(calls, ["built"])

    def test_page_analysis_cache_invalidates_changed_inputs_and_version(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"pdf-v1")
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(source)}), encoding="utf-8")
            spatial = root / "spatial_ocr.json"
            vector = root / "vector_geometry.json"
            spatial.write_text('{"version":1}', encoding="utf-8")
            vector.write_text('{"version":1}', encoding="utf-8")
            builds = []

            def build():
                builds.append(1)
                return {"objects": [], "image": Image.new("RGB", (4, 4), "white"), "page_meta": {}}

            def populate():
                with page_analysis_cache.operation(root):
                    page_analysis_cache.get_context(root, 1, build)

            populate()  # Initial cold build.
            source.write_bytes(b"pdf-v2")
            populate()  # Source PDF changed.
            spatial.write_text('{"version":2}', encoding="utf-8")
            populate()  # Spatial OCR changed.
            vector.write_text('{"version":2}', encoding="utf-8")
            populate()  # Vector metadata changed.
            with patch.object(page_analysis_cache, "CACHE_VERSION", page_analysis_cache.CACHE_VERSION + 1):
                populate()  # Cache schema/render implementation changed.
            self.assertEqual(len(builds), 5)
            generations = [path for path in (root / "page_analysis_cache").iterdir() if path.is_dir()]
            self.assertEqual(len(generations), 1)

    def test_page_analysis_cache_keeps_scanned_task_packet_identical(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.pdf"
            source.write_bytes(b"synthetic-pdf-content")
            (root / "ai_input.json").write_text(json.dumps({"source_pdf": str(source)}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": []}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": []}}), encoding="utf-8")
            builds = []

            def build():
                builds.append(1)
                return {"objects": [], "image": Image.new("RGB", (400, 400), "white"),
                        "viewport": (0, 0, 400, 400), "image_scale": 1.0, "page_origin": (0, 0),
                        "page_bbox": (0, 0, 400, 400), "pdf_images": [], "pdf_has_chars": False,
                        "pdf_chars_in_viewport": False, "render_dpi": 72.0,
                        "declared_mm_per_px": None, "summary": [], "page_meta": {"page": 4}}

            def make_packet():
                with page_analysis_cache.operation(root):
                    with patch.object(autonomous_tasks_service, "_p0_main_geometry_pages", return_value=[{"page": 4}]), \
                            patch.object(autonomous_tasks_service, "_p0_context",
                                         side_effect=lambda cache_root, page: page_analysis_cache.get_context(cache_root, page, build)), \
                            patch.object(autonomous_tasks_service, "_page_has_text_layer", return_value=False), \
                            patch.object(autonomous_tasks_service, "_page_dimension_candidates", return_value=[]):
                        return autonomous_tasks_service._s1_packets(root)

            cold = make_packet()
            warm = make_packet()
            self.assertEqual(cold, warm)
            self.assertEqual(len(builds), 1)
            self.assertEqual(cold[0][0:4], warm[0][0:4])
            self.assertEqual(cold[0][4], warm[0][4])

    def test_raster_detection_uses_actual_cached_render_scale(self):
        from PIL import Image
        context = {"image": Image.new("RGB", (401, 300), "white"), "image_scale": 1.0,
                   "pdf_page_width": 400.0, "page_bbox": (0, 0, 400, 300), "pdf_images": [],
                   "viewport": (0, 0, 401, 300), "objects": [], "declared_mm_per_px": None}
        observed = []

        def classify(_page, _viewport, scale, mm_per_px, objects=None):
            observed.append((scale, mm_per_px, objects))
            return True

        with patch.object(room_outline, "page_is_raster", side_effect=classify):
            self.assertTrue(autonomous_tasks_service._page_is_raster_for_scan(Path("."), 1, context))
            self.assertTrue(autonomous_tasks_service._p0_is_raster(Path("."), 1, context, {"mm_per_px": 4.0}))
        actual_scale = 401 / 400
        self.assertEqual([row[0] for row in observed], [actual_scale, actual_scale])
        self.assertEqual(observed[0][1], 25.4 * 100 / (72 * actual_scale))
        self.assertEqual(observed[1][1], 4.0)

if __name__ == "__main__":
    unittest.main()
