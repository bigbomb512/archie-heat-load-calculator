#!/usr/bin/env python3

import unittest

from ai.drawing_coverage import build_drawing_coverage
from ai.geometry_resolution import build_geometry_resolution
from ai.calculation_extraction import extract_calculation_input_evidence


def fixture(mode="preliminary_ai_estimate"):
    ai = {
        "source_pdf": "ai-geometry-fixture.pdf",
        "geometry_resolution_mode": mode,
        "drawing_set": {"pages": [{
            "page": 1,
            "title": "Level 1 Dimension Plan",
            "detected_type": "floor_plan",
            "drawing_number": "A-101",
            "level_name": "Level 1",
            "structured_content": {"markdown": ""},
        }]},
    }
    walls = [
        {"wall_id": "P1-VWALL-001", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[0, 0], [100, 0]], "confidence": "high"},
        {"wall_id": "P1-VWALL-002", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[100, 0], [100, 50]], "confidence": "high"},
        {"wall_id": "P1-VWALL-003", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[100, 50], [0, 50]], "confidence": "high"},
        {"wall_id": "P1-VWALL-004", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "points_px": [[0, 50], [0, 0]], "confidence": "high"},
    ]
    vision = {"result": {"geometry_review": {"pages": [{
        "page": 1,
        "page_role": "main_geometry_and_dimension_plan",
        "geometry_readiness": "vision_layered",
        "walls": walls,
        "major_dimensions": [{"dimension_id": "P1-VDIMTXT-001", "value_mm": 10000, "confidence": "high"}],
        "dimension_wall_links": [{"dimension_id": "P1-VDIMTXT-001", "target_wall_id": "P1-VWALL-001", "confidence": "high", "reason": "arrowheads span the wall endpoints"}],
        "room_geometry_candidates": [{
            "room_geometry_id": "P1-VROOM-001",
            "label": "Shop",
            "level_name": "Level 1",
            "boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]],
            "wall_ids": [wall["wall_id"] for wall in walls],
            "dimension_ids": ["P1-VDIMTXT-001"],
            "confidence": "high",
            "confidence_score": 0.95,
            "scale_mm_per_px": 100,
            "source_pages": [1],
            "source_crop": "page_001_geometry_crop.png",
        }],
    }]}}}
    building = {"spaces": [{"id": "room-shop", "name": "Shop", "level_name": "Level 1", "evidence": [{"page": 1, "excerpt": "Shop"}]}]}
    return ai, build_drawing_coverage(ai), building, vision


class AiPrimaryGeometryTests(unittest.TestCase):
    def test_preliminary_ai_geometry_binds_dimension_and_activates_area(self):
        ai, coverage, building, vision = fixture()
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        area = next(row for row in result["entities"] if row["kind"] == "area" and row["geometry_status"] == "ai_estimated")
        self.assertEqual(area["value"]["area_m2"], 50.0)
        self.assertEqual(result["summary"]["ai_estimated_room_geometry_count"], 1)
        self.assertTrue(any(row["kind"] == "ai_dimension_to_wall" and row["status"] == "active" for row in result["relationships"]))

        evidence = extract_calculation_input_evidence(ai, coverage, {}, vision_response=vision, building=building)
        candidate = next(row for row in evidence["candidates"] if row["category"] == "area")
        self.assertEqual(candidate["status"], "active")
        self.assertEqual(candidate["room_id"], "room-shop")
        self.assertEqual(candidate["geometry_mode"], "preliminary_ai_estimate")
        self.assertTrue(candidate["derivation"]["formula"])

    def test_engineering_reviewed_mode_does_not_trust_ai_alone(self):
        ai, coverage, building, vision = fixture("engineering_reviewed")
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "ai_estimated" for row in result["entities"]))
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertEqual(proposal["geometry_status"], "geometry_review_required")
        self.assertIn("independent_geometry_witness", proposal["unresolved_fields"])

    def test_skill_boundary_proposal_enters_same_geometry_validator(self):
        ai, coverage, building, _vision = fixture()
        wall_ids = [f"skill-wall-{index}" for index in range(4)]
        walls = [
            {"wall_id": wall_ids[0], "line_start_px": [0, 0], "line_end_px": [100, 0]},
            {"wall_id": wall_ids[1], "line_start_px": [100, 0], "line_end_px": [100, 50]},
            {"wall_id": wall_ids[2], "line_start_px": [100, 50], "line_end_px": [0, 50]},
            {"wall_id": wall_ids[3], "line_start_px": [0, 50], "line_end_px": [0, 0]},
        ]
        skill_candidate = {
            "room_id": "room-shop", "label": "Shop", "level_name": "Level 1", "page": 1,
            "source_pages": [1], "confidence_score": 0.95,
            "geometry": {"boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]],
                "wall_ids": wall_ids, "walls": walls, "dimension_ids": ["dim-shop-width"],
                "dimensions": [{"dimension_id": "dim-shop-width", "value_mm": 10000}],
                "dimension_wall_links": [{"dimension_id": "dim-shop-width", "target_wall_id": wall_ids[0],
                    "value_mm": 10000, "reason": "dimension extension lines terminate at the cited wall endpoints",
                    "measured_span_start_px": [0, 0], "measured_span_end_px": [100, 0]}],
                "scale_mm_per_px": 100},
        }
        evidence = extract_calculation_input_evidence(
            ai, coverage, building=building, geometry_room_proposals=[skill_candidate],
        )
        candidate = next(row for row in evidence["candidates"] if row["category"] == "area" and row["status"] == "active")
        self.assertEqual(candidate["value"], 50.0)
        self.assertEqual(candidate["room_id"], "room-shop")
        proof = next(row for row in evidence["geometry_resolution"]["room_geometry_proofs"] if row["room_label"] == "Shop")
        self.assertEqual(proof["status"], "ai_estimated")
        self.assertIn("dimension_bindings", proof)

    def test_skill_reported_area_cannot_activate_without_valid_boundary(self):
        ai, coverage, building, _vision = fixture()
        candidate = {
            "room_id": "room-shop", "label": "Shop", "level_name": "Level 1", "page": 1,
            "source_pages": [1], "confidence_score": 0.95,
            "geometry": {"boundary_points_px": [[0, 0], [100, 50], [0, 50], [100, 0], [0, 0]],
                "wall_ids": [], "dimension_ids": [], "dimension_wall_links": [], "walls": [],
                "dimensions": [], "scale_mm_per_px": 100, "area_m2": 50.0},
        }
        evidence = extract_calculation_input_evidence(
            ai, coverage, building=building, geometry_room_proposals=[candidate],
        )
        areas = [row for row in evidence["candidates"] if row["category"] == "area" and row["label"] == "Shop"]
        self.assertFalse(any(row["status"] == "active" for row in areas))
        proof = next(row for row in evidence["geometry_resolution"]["entities"]
                     if row["kind"] == "ai_room_geometry" and row["label"] == "Shop")
        self.assertIn("validated_boundary_shape", proof["unresolved_fields"])

    def test_unknown_wall_reference_is_blocked_at_room_scope(self):
        ai, coverage, building, vision = fixture()
        vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]["wall_ids"] = ["P1-VWALL-NOT-FOUND"]
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertEqual(proposal["geometry_status"], "geometry_review_required")
        self.assertIn("unknown_wall_reference", proposal["unresolved_fields"])
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "ai_estimated" for row in result["entities"]))

    def test_open_boundary_is_blocked(self):
        ai, coverage, building, vision = fixture()
        vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]["boundary_points_px"] = [[0, 0], [100, 0], [100, 50], [0, 50]]
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertIn("validated_boundary_shape", proposal["unresolved_fields"])

    def test_reviewed_ai_geometry_requires_and_accepts_distinct_witness(self):
        ai, _, building, vision = fixture("engineering_reviewed")
        ai["drawing_set"]["pages"].append({
            "page": 2, "title": "Level 1 Finish Plan", "detected_type": "floor_plan",
            "drawing_number": "A-102", "level_name": "Level 1", "structured_content": {"markdown": ""},
        })
        coverage = build_drawing_coverage(ai)
        building["spaces"][0]["evidence"].append({"page": 2, "geometry_reference": "finish-region-shop", "excerpt": "Shop"})
        candidate = vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]
        candidate["independent_witnesses"] = [{
            "page": 2, "kind": "finish_plan_region", "source_fingerprint": "finish-plan-fingerprint",
            "reference": "finish-region-shop",
        }]
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proof = next(row for row in result["room_geometry_proofs"] if row["room_label"] == "Shop")
        self.assertEqual(proof["status"], "geometry_confirmed")
        self.assertTrue(proof["independent_witness_ids"])
        area = next(row for row in result["entities"] if row["kind"] == "area" and row["geometry_status"] == "geometry_confirmed")
        self.assertEqual(area["value"]["area_m2"], 50.0)

    def test_same_page_duplicate_ai_boundaries_are_conflicts(self):
        ai, coverage, building, vision = fixture()
        candidate = vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]
        duplicate = dict(candidate)
        duplicate["room_geometry_id"] = "P1-VROOM-002"
        duplicate["boundary_points_px"] = [[0, 0], [80, 0], [80, 50], [0, 50], [0, 0]]
        vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"].append(duplicate)
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposals = [row for row in result["entities"] if row["kind"] == "ai_room_geometry"]
        self.assertTrue(all("geometry_conflict" in row["unresolved_fields"] for row in proposals))
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "ai_estimated" for row in result["entities"]))

    def test_dimension_link_without_reason_is_blocked(self):
        ai, coverage, building, vision = fixture()
        vision["result"]["geometry_review"]["pages"][0]["dimension_wall_links"][0]["reason"] = ""
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertIn("dimension_wall_link_reason", proposal["unresolved_fields"])

    def test_reported_ai_area_cannot_override_or_conflict_with_derived_area(self):
        ai, coverage, building, vision = fixture()
        candidate = vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]
        candidate["area_m2"] = 90.0
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertEqual(proposal["value"]["area_m2"], 50.0)
        self.assertIn("reported_area_conflict", proposal["unresolved_fields"])
        self.assertEqual(proposal["geometry_status"], "geometry_review_required")
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "ai_estimated" for row in result["entities"]))

    def test_polygon_and_ordered_wall_links_must_describe_the_same_loop(self):
        ai, coverage, building, vision = fixture()
        candidate = vision["result"]["geometry_review"]["pages"][0]["room_geometry_candidates"][0]
        candidate["wall_ids"] = ["P1-VWALL-002", "P1-VWALL-001", "P1-VWALL-003", "P1-VWALL-004"]
        result = build_geometry_resolution(ai, coverage, building, vision_response=vision)
        proposal = next(row for row in result["entities"] if row["kind"] == "ai_room_geometry")
        self.assertIn("boundary_wall_alignment", proposal["unresolved_fields"])
        self.assertFalse(any(row["kind"] == "area" and row["geometry_status"] == "ai_estimated" for row in result["entities"]))


if __name__ == "__main__":
    unittest.main()
