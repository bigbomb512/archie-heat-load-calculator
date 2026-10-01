#!/usr/bin/env python3
"""Analytical and integration checks for proposal-only room-boundary traces."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from types import SimpleNamespace
from unittest.mock import patch

from ai import reviewer_room_geometry
from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution
from ai.calculation_extraction import extract_calculation_input_evidence
from backend import reviewer_room_geometry_service


class ReviewerRoomGeometryTests(unittest.TestCase):
    def setUp(self):
        self.points = [[0, 0], [400, 0], [400, 500], [0, 500], [0, 0]]
        self.calibration = reviewer_room_geometry.calibration([[0, 0], [400, 0]], 4000, 100, 3.5433070866)
        self.trace = {
            "trace_id": "trace-shop", "room_id": "room-shop", "room_label": "Shop",
            "level_name": "Ground", "page": 1, "points_image_px": self.points,
            "snapped_line_ids": ["L1", "L2", "L3", "L4", "L1"],
            "calibration": self.calibration, "reviewer": "Test reviewer", "note": "Synthetic test",
            "status": "geometry_proposed", "source_fingerprints": {"source_pdf": "pdf-a", "vector_page": "vector-a"},
        }

    def test_known_rectangle_calculates_twenty_square_metres_but_stays_proposed(self):
        self.assertEqual(self.calibration["status"], "agreed")
        result = reviewer_room_geometry.validate_artifact({"records": [self.trace]})
        ai = {"source_pdf": "synthetic.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Ground Floor Plan", "drawing_number": "A-01", "level_name": "Ground"},
        ]}}
        coverage = build_drawing_coverage(ai)
        coverage["page_roles"][0]["proposed_role"] = "primary_geometry_plan"
        building = {"spaces": [{"id": "room-shop", "name": "Shop", "level_name": "Ground", "evidence": [{"page": 1}]}]}
        geometry = build_geometry_resolution(ai, coverage, building, reviewer_room_geometry={"records": result["records"]})
        proof = next(row for row in geometry["entities"] if row["kind"] == "room_geometry_proof")
        self.assertEqual(proof["value"]["area_m2"], 20.0)
        self.assertEqual(proof["geometry_status"], "geometry_proposed")
        self.assertEqual(geometry["summary"]["active_room_area_count"], 0)
        evidence = extract_calculation_input_evidence(ai, coverage, building=building,
                                                       reviewer_room_geometry={"records": result["records"]})
        area = next(row for row in evidence["candidates"] if row["target"] == "room.Shop.area_m2")
        self.assertEqual(area["status"], "proposed")
        self.assertEqual(area["value"], 20.0)
        self.assertEqual(area["room_id"], "room-shop")

    def test_trace_binds_to_current_room_use_registry_when_building_spaces_are_absent(self):
        ai = {"source_pdf": "synthetic.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Ground Floor Plan", "drawing_number": "A-01", "level_name": "Ground"},
        ]}}
        coverage = build_drawing_coverage(ai)
        coverage["page_roles"][0]["proposed_role"] = "primary_geometry_plan"
        geometry = build_geometry_resolution(
            ai, coverage, {"spaces": []}, reviewer_room_geometry={
                "rooms": [{"room_id": "room-shop", "label": "Shop", "level_name": "Ground", "source": "room_use_resolution"}],
                "records": [self.trace],
            },
        )
        proof = next(row for row in geometry["entities"] if row["kind"] == "room_geometry_proof")
        self.assertEqual(proof["room_source_id"], "room-shop")
        self.assertEqual(proof["value"]["area_m2"], 20.0)

    def test_rejects_open_zero_area_and_self_intersecting_polygons(self):
        for points, message in [
            ([[0, 0], [1, 0], [0, 1]], "closing point"),
            ([[0, 0], [1, 0], [0, 0], [0, 0]], "zero area"),
            ([[0, 0], [4, 4], [0, 4], [4, 0], [0, 0]], "self-intersects"),
        ]:
            row = {**self.trace, "points_image_px": points,
                   "snapped_line_ids": [None] * len(points)}
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                reviewer_room_geometry.validate_artifact({"records": [row]})

    def test_calibration_disagreement_missing_scale_and_disagreeing_second_dimensions_block_area(self):
        bad = reviewer_room_geometry.calibration([[0, 0], [400, 0]], 4200, 100, 3.5433070866)
        self.assertEqual(bad["status"], "unresolved")
        self.assertEqual(bad["reason"], "dimension_disagrees_with_declared_scale")
        missing = reviewer_room_geometry.calibration([[0, 0], [400, 0]], 4000, None, 3.5433070866)
        self.assertEqual(missing["reason"], "declared_scale_missing")
        two_disagree = reviewer_room_geometry.calibration(
            [[0, 0], [400, 0]], 4200, 100, 3.5433070866,
            second_dimension={"points_image_px": [[0, 0], [500, 0]], "value_mm": 5250},
        )
        self.assertEqual(two_disagree["reason"], "two_reviewer_dimensions_agree_declared_scale_rejected")
        self.assertAlmostEqual(two_disagree["mm_per_px"], 10.5)
        conflict = reviewer_room_geometry.calibration(
            [[0, 0], [400, 0]], 4200, 100, 3.5433070866,
            second_dimension={"points_image_px": [[0, 0], [500, 0]], "value_mm": 4800},
        )
        self.assertEqual(conflict["reason"], "reviewer_dimensions_disagree")
        self.assertIsNone(conflict["mm_per_px"])

    def test_missing_scale_remediation_does_not_suggest_that_two_dimensions_can_replace_it(self):
        unresolved = reviewer_room_geometry.calibration([[0, 0], [400, 0]], 4000, None, 3.5433070866)
        trace = {**self.trace, "calibration": unresolved}
        ai = {"source_pdf": "synthetic.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Ground Floor Plan", "drawing_number": "A-01", "level_name": "Ground"},
        ]}}
        coverage = build_drawing_coverage(ai)
        coverage["page_roles"][0]["proposed_role"] = "primary_geometry_plan"
        building = {"spaces": [{"id": "room-shop", "name": "Shop", "level_name": "Ground", "evidence": [{"page": 1}]}]}
        result = build_geometry_resolution(ai, coverage, building, reviewer_room_geometry={"records": [trace]})
        issue = next(row for row in result["review_items"] if row.get("field") == "geometry_area")
        self.assertIn("declared plan scale is required", issue["remediation"])
        self.assertNotIn("two agreeing printed dimensions", issue["remediation"])

    def test_stale_vector_fingerprint_is_excluded(self):
        artifact = reviewer_room_geometry.validate_artifact({"records": [self.trace]})
        rows = reviewer_room_geometry.active_records(artifact, "pdf-a", [{"page": 1, "changed": True}])
        self.assertEqual(rows, [])

    def test_service_rejects_unknown_room_and_unknown_or_distant_snap_lines(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "building_evidence.json").write_text(json.dumps({"spaces": []}), encoding="utf-8")
            project = {"id": "test", "review_dir": str(root)}
            with self.assertRaisesRegex(ValueError, "unknown room"):
                reviewer_room_geometry_service.post(object(), project, {"action": "save", "room_id": "missing-room"})
        vector = {"confirmation_line_candidates": [{"candidate_id": "line-1", "start_px": [0, 0], "end_px": [100, 0]}]}
        with self.assertRaisesRegex(ValueError, "no longer present"):
            reviewer_room_geometry_service._validate_snaps([[20, 0]], ["removed-line"], vector)
        with self.assertRaisesRegex(ValueError, "outside the server snap tolerance"):
            reviewer_room_geometry_service._validate_snaps([[50, 100]], ["line-1"], vector)

    def test_service_save_rebuilds_proposed_candidate_and_delete_removes_it(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            ai = {"source_pdf_fingerprint": "pdf-fixture", "drawing_set": {"pages": [
                {"page": 1, "title": "Ground Floor Plan", "drawing_number": "A-01", "level_name": "Ground"},
            ]}}
            coverage = build_drawing_coverage(ai)
            coverage["page_roles"][0]["proposed_role"] = "primary_geometry_plan"
            vector_page = {"page": 1, "coordinate_systems": {"image_px": {"image_width": 1000, "image_height": 800}},
                           "confirmation_line_candidates": [], "image": "screenshots/page_001.png"}
            for name, value in {
                "ai_input": ai, "drawing_coverage": coverage,
                "vector_geometry": {"geometry_key_points": {"pages": [vector_page]}},
                "building_evidence": {"spaces": [{"id": "room-shop", "name": "Shop", "level_name": "Ground", "evidence": [{"page": 1}]}]},
            }.items():
                (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            web = SimpleNamespace(safe_link=lambda path: str(path), update_project=lambda project: None,
                                  productization=__import__("backend.productization", fromlist=["productization"]))
            project = {"id": "test-project", "review_dir": str(root)}
            page_context = {"page": 1, "title": "Ground Plan", "proposed_role": "primary_geometry_plan",
                            "scale_denominator": 100, "image_px_per_pt": 3.5277777778,
                            "image_width_px": 1000, "image_height_px": 800,
                            "preview_url": "", "preview_matches_vector_coordinates": True}
            payload = {"action": "save", "room_id": "room-shop", "page": 1,
                       "points_image_px": [[100, 100], [300, 100], [300, 300], [100, 300], [100, 100]],
                       "snapped_line_ids": [None] * 5, "dimension_points_image_px": [[100, 600], [200, 600]],
                       "dimension_value_mm": 1000, "reviewer": "QA-1", "source_pdf_fingerprint": "pdf-fixture",
                       "vector_page_fingerprint": reviewer_room_geometry_service._page_fp(vector_page)}
            with patch.object(reviewer_room_geometry_service, "_page_context", return_value=[{**page_context, "image_width_px": None, "image_height_px": None,
                                                                                         "preview_matches_vector_coordinates": False}]):
                with self.assertRaisesRegex(ValueError, "matching full-resolution plan image"):
                    reviewer_room_geometry_service.post(web, project, payload)
            with patch.object(reviewer_room_geometry_service, "_page_context", return_value=[page_context]):
                saved = reviewer_room_geometry_service.post(web, project, payload)
                candidates = saved["calculation_input_evidence"]["candidates"]
                area = next(row for row in candidates if row["target"] == "room.Shop.area_m2")
                self.assertEqual(area["status"], "proposed")
                self.assertEqual(area["room_id"], "room-shop")
                proof = next(row for row in saved["geometry_resolution"]["entities"] if row["kind"] == "room_geometry_proof")
                self.assertEqual(proof["value"]["calibration"]["status"], "agreed")
                self.assertEqual(proof["value"]["source_fingerprints"], {"source_pdf": "pdf-fixture", "vector_page": reviewer_room_geometry_service._page_fp(vector_page)})
                deleted = reviewer_room_geometry_service.post(web, project, {"action": "delete", "trace_id": "room_trace_" + reviewer_room_geometry_service.fingerprint(["room-shop", 1])[:20]})
                self.assertFalse(any(row.get("target") == "room.Shop.area_m2" for row in deleted["calculation_input_evidence"]["candidates"]))
                self.assertFalse(any(row.get("kind") == "room_geometry_proof" for row in deleted["geometry_resolution"]["entities"]))

    def test_page_context_serves_only_full_resolution_render_matching_vector_coordinates(self):
        from PIL import Image
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            packet = root / "chatgpt_packet"
            screenshots = packet / "screenshots"
            screenshots.mkdir(parents=True)
            full = screenshots / "page_001_floor_plan.png"
            Image.new("RGB", (1000, 800), "white").save(full)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 1, "title": "Ground Floor", "thumbnail_path": "thumbnails/page_001.png"},
            ]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 1, "proposed_role": "primary_geometry_plan", "main_scale": "1:100"}]}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [{
                "page": 1, "image": "screenshots/page_001_floor_plan.png",
                "coordinate_systems": {"image_px": {"image_width": 1000, "image_height": 800}},
            }]}}), encoding="utf-8")
            web = SimpleNamespace(safe_link=lambda path: str(path))
            row = reviewer_room_geometry_service._page_context(reviewer_room_geometry_service._paths({"review_dir": str(root)}), web)[0]
            self.assertEqual(Path(row["preview_url"]).resolve(), full.resolve())
            self.assertEqual((row["preview_width_px"], row["preview_height_px"]), (1000, 800))
            self.assertTrue(row["preview_matches_vector_coordinates"])
            Image.new("RGB", (745, 527), "white").save(full)
            mismatched = reviewer_room_geometry_service._page_context(reviewer_room_geometry_service._paths({"review_dir": str(root)}), web)[0]
            self.assertEqual(mismatched["preview_url"], "")
            self.assertFalse(mismatched["preview_matches_vector_coordinates"])

    def test_diagnostics_report_filter_counts_and_empty_geometry(self):
        ai = {"source_pdf": "synthetic.pdf", "drawing_set": {"pages": [
            {"page": 1, "title": "Plan", "drawing_number": "A-01", "level_name": "Ground"},
        ]}}
        coverage = build_drawing_coverage(ai)
        coverage["page_roles"][0]["proposed_role"] = "primary_geometry_plan"
        building = {"spaces": [{"id": "room-shop", "name": "Shop", "level_name": "Ground", "evidence": [{"page": 1}]}]}
        vector = {"geometry_key_points": {"pages": [{"page": 1, "line_candidates": [
            {"candidate_id": "bad", "candidate_role_hint": "possible_dimension", "start_px": [0, 0], "end_px": [2, 0]},
        ]}]}}
        ocr = {"pages": [{"page": 1, "room_label_candidates": [
            {"text": "Shop", "status": "possible_room_or_area_label", "bbox": [1, 1, 2, 2]},
        ]}]}
        result = build_geometry_resolution(ai, coverage, building, ocr, vector)
        diag = result["deterministic_proof_diagnostics"]
        self.assertEqual(diag["pages"][0]["wall_lines"]["accepted_count"], 0)
        self.assertGreaterEqual(diag["pages"][0]["wall_lines"]["rejected_count"], 1)
        self.assertEqual(diag["pages"][0]["label_points"]["accepted_count"], 0)
        self.assertEqual(diag["pages"][0]["label_points"]["status_filtered_count"], 1)
        self.assertIn("no_automatic_room_geometry_proof", [row["reason"] for row in diag["rooms"]])


if __name__ == "__main__":
    unittest.main()
