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


class AutonomousTaskTests(unittest.TestCase):
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
                "dimension_candidates": [{"value_mm": 11970}]}]}), encoding="utf-8")
            (root / "ceiling_volume_resolution.json").write_text("{}", encoding="utf-8")
            trace = {"trace_id": "mall-shop-trace", "room_id": "shop", "room_label": "Shop",
                     "level_name": "Ground", "page": 20,
                     "points_image_px": [[0, 0], [1197, 0], [1197, 300], [0, 300], [0, 0]],
                     "calibration": {"status": "agreed", "mm_per_px": 10},
                     "edges": [{"index": 0, "boundary": "mall"}]}
            height_key = ceiling_volume_resolution.room_identity("Shop", "Ground")
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "current_records",
                              return_value=[trace]), \
                 patch.object(ceiling_volume_resolution, "values_by_room",
                              return_value={height_key: {"ceiling_height_mm": 2700}}), \
                 patch.object(autonomous_tasks_service, "_p4_elevation_image", return_value=Image.new("RGB", (64, 64))), \
                 patch.object(autonomous_tasks_service, "_png_bytes", return_value=b"png"):
                packets = autonomous_tasks_service._p4_packets(root)
            opening_tasks = [row for row in packets if row[0] == "P4_openings" and row[2].get("edge_index") == 0]
            self.assertEqual(len(opening_tasks), 1)
            self.assertEqual(opening_tasks[0][2]["room_label"], "Shop")
            self.assertAlmostEqual(opening_tasks[0][2]["edge_length_mm"], 11970)

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
            shop = next(row[2] for row in packets if row[2].get("trace_id") == "shop-trace")
            self.assertIn(2, shop["shared_edges"])
            self.assertNotIn(2, {row["index"] for row in shop["edges"]})

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
        with patch.object(autonomous_tasks_service, "_p0_followup_packets",
                          return_value=[name_packet]), \
             patch.object(autonomous_tasks_service, "_p0_outline_packets",
                          side_effect=lambda _root: events.append("outline-builder") or []), \
             patch.object(autonomous_tasks_service, "_p3_packets", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p4_packets", return_value=[]), \
             patch.object(autonomous_tasks_service, "_create_run",
                          side_effect=lambda _root, task, *_args, **_kwargs: events.append(task)):
            autonomous_tasks_service._refresh_geometry_tasks(Path("/tmp"))
        self.assertEqual(events, ["P0_room_names", "outline-builder"])

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
                {"task":"P3_boundaries","source":"ai_determined","applied_value":{"room":"Shop","edges":[{"edge_length_m":11.97,"boundary":"mall","source":"ai_fallback"}]}},
                {"task":"P4_openings","target":"shop-edge","applied_value":{"page":26,"room":"Shop","total_width_mm":11900,
                    "glazed_panels":[{"width_mm":2025,"sill_mm":1100,"head_mm":2700,"source":"ai_determined"}]}},
            ]
            with patch.object(autonomous_tasks_service, "_all_current", return_value=records):
                result = autonomous_tasks_service._export_determinations(root)
            self.assertEqual(result["P0_rooms"], [{"label":"Shop","area_m2":216.1,"source":"printed_on_drawing","method":"enclosed_walls"}])
            self.assertEqual(result["P3_boundaries"], [{"room":"Shop","edge_length_m":11.97,"boundary":"mall","source":"ai_fallback"}])
            self.assertEqual(result["P4_openings"], [{"page":26,"room":"Shop","total_width_mm":11900,
                "glazed_panels":[{"width_mm":2025,"sill_mm":1100,"head_mm":2700,"source":"ai_determined"}]}])
            self.assertEqual(json.loads((root/"ai_tasks"/"determinations.json").read_text()), result)

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
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body: calls.append(body)):
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
            artifact = root / "reviewer_room_geometry.json"
            artifact.write_text(json.dumps({"records": [trace]}), encoding="utf-8")
            project = {"id": "roof-answer", "review_dir": temporary}
            record = autonomous_tasks_service._create_run(root, "P5_roof", "shop", {"room": {"room_label": "Shop"}}, "prompt")
            record["status"] = "needs_contractor_answer"
            autonomous_tasks_service._update_record(root, record)
            calls = []
            with patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "_paths", return_value={"artifact": artifact}), \
                 patch.object(autonomous_tasks_service.reviewer_room_geometry_service, "post", side_effect=lambda _w, _p, body: calls.append(body)):
                autonomous_tasks_service._answer_roof(SimpleNamespace(), project, root, {
                    "task": "P5_roof", "target": "shop", "answer": "floor_tenancy_above"})
            self.assertEqual(calls[0]["action"], "classify_envelope")
            self.assertEqual(calls[0]["roof"], "not_exposed")
            self.assertEqual(calls[0]["reviewer"], "Answered by the contractor")
            self.assertTrue(calls[0]["confirm_roof"])
            saved = autonomous_tasks_service._current_task(root, "P5_roof", "shop")
            self.assertEqual(saved["status"], "applied")
            self.assertEqual(saved["applied_value"]["label"], "Answered by the contractor")
            exported = json.loads((root / "ai_task_determinations.json").read_text())
            self.assertEqual(exported["P5_roof"][0]["source"], "reviewer")

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
                    "tasks": {"P1_site": {"site_must_contain": ["site"]}}}))
            evaluator.ROOT = root
            summary = {"P1_site": {"correct": 10, "wrong": 0, "missing": 0, "scored": 10}}
            complete = [{"case_id": f"case{number}", "tasks": {"P1_site": [{"status": "correct"}]}}
                        for number in range(10)]
            self.assertTrue(evaluator._recordable_accuracy(complete, summary, .85)["tasks"]["P1_site"]["auto_apply"])
            with self.assertRaisesRegex(ValueError, "case9"):
                evaluator._recordable_accuracy(complete[:-1], summary, .85)
            with self.assertRaisesRegex(ValueError, "at least 10"):
                evaluator._recordable_accuracy(complete, {"P1_site": {**summary["P1_site"], "scored": 9}}, .85)

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
            traces = [{"room_id": room_id, "room_label": room_id.title(), "level_name": "Ground", "page": 1,
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


if __name__ == "__main__":
    unittest.main()
