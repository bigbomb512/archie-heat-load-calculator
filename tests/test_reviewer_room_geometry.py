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
from ai.geometry_resolution import build_geometry_resolution, fingerprint
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

    def test_page_north_orients_edges_for_both_polygon_windings(self):
        clockwise = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
        counter_clockwise = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]
        self.assertEqual([reviewer_room_geometry.oriented_edge_cardinal(clockwise, i, 0)[0] for i in range(4)],
                         ["N", "E", "S", "W"])
        self.assertEqual([reviewer_room_geometry.oriented_edge_cardinal(counter_clockwise, i, 0)[0] for i in range(4)],
                         ["W", "S", "E", "N"])

    def test_old_format_artifact_revalidation_preserves_fingerprint(self):
        old = reviewer_room_geometry.validate_artifact({"records": [self.trace]})
        old.pop("page_north", None)
        old.pop("rooms", None)
        old["fingerprint"] = reviewer_room_geometry.fingerprint({
            key: value for key, value in old.items() if key != "fingerprint"
        })
        checked = reviewer_room_geometry.validate_artifact(old)
        self.assertEqual(checked["fingerprint"], old["fingerprint"])
        self.assertNotIn("page_north", checked)
        self.assertNotIn("rooms", checked)

    def test_north_arrow_bearing_and_page_declaration_validation(self):
        self.assertEqual(reviewer_room_geometry.page_up_bearing_from_north_arrow([[10, 20], [10, 5]]), 0)
        self.assertEqual(reviewer_room_geometry.page_up_bearing_from_north_arrow([[10, 20], [10, 35]]), 180)
        north = {"1": {"page": 1, "plan_up_azimuth_deg": 0, "source": "reviewer_read_north_arrow",
                        "north_arrow_points_image_px": [[10, 20], [10, 5]], "reviewer": "QA", "declared_at": "now"}}
        result = reviewer_room_geometry.validate_artifact({"records": [], "page_north": north})
        self.assertEqual(result["page_north"]["1"]["reviewer"], "QA")
        with self.assertRaisesRegex(ValueError, "under 360"):
            reviewer_room_geometry.validate_artifact({"records": [], "page_north": {"1": {
                **north["1"], "plan_up_azimuth_deg": 360}}})

    def test_page_north_change_is_a_preliminary_source_change(self):
        from backend import ai_preliminary_service
        with TemporaryDirectory() as directory:
            paths = ai_preliminary_service._paths({"review_dir": directory})
            first = ai_preliminary_service._sources(paths)["reviewer_room_geometry_north"]
            (paths["root"] / "reviewer_room_geometry.json").write_text(json.dumps({"page_north": {
                "1": {"page": 1, "plan_up_azimuth_deg": 15, "source": "reviewer_typed_page_up_bearing",
                      "reviewer": "QA", "declared_at": "now"}}}), encoding="utf-8")
            second = ai_preliminary_service._sources(paths)["reviewer_room_geometry_north"]
        self.assertNotEqual(first, second)

    def test_workspace_shows_single_legacy_glazing_option_and_external_edge_facing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace = {**self.trace, "edges": [{"index": 0, "boundary": "external"}],
                     "envelope_reviewer": "QA", "envelope_declared_at": "now"}
            north = {"1": {"page": 1, "plan_up_azimuth_deg": 0,
                "source": "reviewer_typed_page_up_bearing", "reviewer": "QA", "declared_at": "now"}}
            artifact_path = root / "reviewer_room_geometry.json"
            artifact_path.write_text(json.dumps(reviewer_room_geometry.validate_artifact({
                "records": [trace], "page_north": north,
            })), encoding="utf-8")
            project = {"id": "workspace-test", "review_dir": str(root)}
            web = SimpleNamespace(safe_link=lambda _path: "")
            with patch.object(reviewer_room_geometry_service, "_source_pdf_fingerprint", return_value=""), \
                 patch.object(reviewer_room_geometry_service, "_vector_pages", return_value=[]), \
                 patch.object(reviewer_room_geometry_service, "_trace_with_current_room", return_value={}), \
                 patch.object(reviewer_room_geometry_service, "_rooms", return_value=[]), \
                 patch.object(reviewer_room_geometry_service, "_page_context", return_value=[]):
                result = reviewer_room_geometry_service._response(web, project)
        self.assertEqual(list(result["glazing_choices"]), ["retail"])
        self.assertEqual(result["glazing_choices"]["retail"]["label"],
                         "Preliminary single glazing (pack au-preliminary-v3): U 5.8, SHGC 0.45")
        self.assertEqual(result["reviewer_room_geometry"]["records"][0]["edge_facings"]["0"], "N")

    def test_opening_dimensions_and_external_edge_are_validated(self):
        with TemporaryDirectory() as directory:
            paths = {"root": Path(directory)}
            room = {"room_id": "room-shop", "label": "Shop", "level_name": "Ground"}
            trace = {**self.trace, "edges": [{"index": 0, "boundary": "external"}]}
            inputs = {"openings": [{"opening_id": "front", "edge_index": 0, "width_m": 2.025,
                       "head_height_m": 3.55, "sill_height_m": 0.9, "elevation_page": 26,
                       "glazing_choice": "retail", "shading_category": "unshaded"}]}
            with patch("ai.ceiling_volume_resolution.values_by_room", return_value={"room-shop": {"ceiling_height_mm": 3750}}):
                valid = reviewer_room_geometry_service._validated_trace_openings(
                    inputs, trace, paths, room, self.calibration)
                self.assertEqual(valid[0]["width_m"], 2.025)
                legacy = {**inputs["openings"][0], "glazing_choice": "office"}
                self.assertEqual(reviewer_room_geometry_service._validated_trace_openings(
                    {"openings": [legacy]}, trace, paths, room, self.calibration)[0]["glazing_choice"], "office")
                for bad, message in [
                    ({**inputs["openings"][0], "edge_index": 1}, "external edge"),
                    ({**inputs["openings"][0], "width_m": 5}, "edge length"),
                    ({**inputs["openings"][0], "head_height_m": 0.8, "sill_height_m": 0.9}, "head height"),
                    ({**inputs["openings"][0], "sill_height_m": 3.6}, "head height"),
                    ({**inputs["openings"][0], "head_height_m": 3.76}, "enter only the glass below the ceiling"),
                ]:
                    with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                        reviewer_room_geometry_service._validated_trace_openings(
                            {"openings": [bad]}, trace, paths, room, self.calibration)

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

    def test_envelope_classification_validation_defaults_and_rejects_bad_values(self):
        checked = reviewer_room_geometry.validate_artifact({"records": [self.trace]})["records"][0]
        self.assertEqual(checked["edges"], [{"index": index, "boundary": "unknown"} for index in range(4)])
        self.assertEqual(checked["roof"], "unknown")
        for edges, roof in (([{"index": 4, "boundary": "external"}], "unknown"),
                            ([{"index": 0, "boundary": "party_wall"}], "unknown"),
                            ([], "pitched")):
            with self.assertRaises(ValueError):
                reviewer_room_geometry.validate_envelope_classification(edges, roof, 4)

    def test_classify_envelope_service_requires_reviewer_and_preserves_reviewer_rooms(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            vector_page = {"page": 1, "unchanged": True}
            trace = {**self.trace, "source_fingerprints": {"source_pdf": "pdf-a", "vector_page": fingerprint(vector_page)}}
            reviewer_room = {"room_id": "reviewer-room", "label": "Kiosk", "level_name": "Ground",
                             "taxonomy_id": "retail", "reviewer": "QA", "source": "reviewer_added"}
            (root / "ai_input.json").write_text(json.dumps({"source_pdf_fingerprint": "pdf-a"}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [vector_page]}}), encoding="utf-8")
            (root / "building_evidence.json").write_text(json.dumps({"spaces": [
                {"id": "room-shop", "name": "Shop", "level_name": "Ground", "evidence": [{"page": 1}]},
            ]}), encoding="utf-8")
            path = root / "reviewer_room_geometry.json"
            path.write_text(json.dumps(reviewer_room_geometry.validate_artifact({"records": [trace], "rooms": [reviewer_room]})), encoding="utf-8")
            project = {"id": "classification-test", "review_dir": str(root)}
            web = SimpleNamespace(safe_link=lambda _path: "", update_project=lambda _project: None)
            valid_payload = {"action": "classify_envelope", "trace_id": trace["trace_id"],
                             "edges": [{"index": 0, "boundary": "external"}], "roof": "exposed", "reviewer": "QA-2",
                             "openings": [{"opening_id": "front-window", "edge_index": 0,
                                 "width_m": 1.0, "head_height_m": 2.0, "sill_height_m": 0.9,
                                 "elevation_page": 26, "glazing_choice": "retail", "shading_category": "unshaded"}]}
            with patch.object(reviewer_room_geometry_service, "_response", return_value={"ok": True}), \
                 patch("backend.calculation_extraction_service.post", return_value={"calculation_input_evidence": {}, "geometry_resolution": {}}), \
                 patch("backend.productization.record_change_if_fingerprint_changed"), \
                 patch("ai.ceiling_volume_resolution.values_by_room", return_value={"room-shop": {"ceiling_height_mm": 3750}}):
                with self.assertRaisesRegex(ValueError, "reviewer name"):
                    reviewer_room_geometry_service.post(web, project, {**valid_payload, "reviewer": ""})
                reviewer_room_geometry_service.post(web, project, valid_payload)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["records"][0]["edges"], [
                {"index": 0, "boundary": "external"}, *[{"index": index, "boundary": "unknown"} for index in range(1, 4)],
            ])
            self.assertEqual(saved["records"][0]["roof"], "exposed")
            self.assertEqual(saved["records"][0]["envelope_reviewer"], "QA-2")
            self.assertTrue(saved["records"][0]["envelope_declared_at"])
            self.assertEqual(saved["records"][0]["openings"][0]["opening_id"], "front-window")
            self.assertEqual(saved["rooms"], [reviewer_room])

    def test_declare_north_action_persists_page_bearing_reviewer_and_time(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "reviewer_room_geometry.json"
            path.write_text(json.dumps(reviewer_room_geometry.empty_artifact()), encoding="utf-8")
            project = {"id": "north-test", "review_dir": str(root)}
            web = SimpleNamespace(safe_link=lambda _path: "", update_project=lambda _project: None)
            with patch.object(reviewer_room_geometry_service, "_page_context", return_value=[{"page": 3}]), \
                 patch.object(reviewer_room_geometry_service, "_response", return_value={"ok": True}), \
                 patch("backend.calculation_extraction_service.post", return_value={}), \
                 patch("backend.productization.record_change_if_fingerprint_changed"):
                reviewer_room_geometry_service.post(web, project, {
                    "action": "declare_north", "page": 3, "reviewer": "QA",
                    "plan_up_azimuth_deg": 27.5,
                })
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["page_north"]["3"]["plan_up_azimuth_deg"], 27.5)
            self.assertEqual(saved["page_north"]["3"]["source"], "reviewer_typed_page_up_bearing")
            self.assertEqual(saved["page_north"]["3"]["reviewer"], "QA")
            self.assertTrue(saved["page_north"]["3"]["declared_at"])

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
