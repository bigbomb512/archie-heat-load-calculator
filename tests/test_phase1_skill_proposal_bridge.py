"""High-confidence drawing-skill findings feed the draft Phase 1 model."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.ai_preliminary import _space_rows, assemble, calculate, validate_placeholder_proposal
from backend import skill_workflow_service
from backend.ai_preliminary_service import (
    _complete_opening_coverage,
    _merge_phase1_skill_proposals,
    _paths,
    _prepare_preliminary_proposal,
    _proposal_with_skill_findings,
    _supported_uncertainty_range,
)


class Phase1SkillProposalBridgeTests(unittest.TestCase):
    def test_uncertainty_range_requires_in_range_point_method_and_resolved_citations(self):
        row = {"_numeric_inferences": {"area_m2": {
            "value": 10, "method": "dimensioned plan", "citation_ids": ["area-page"],
            "uncertainty_range": {"low": 9, "high": 11, "method": "printed dimension rounding",
                                  "citation_ids": ["area-page"]},
        }}}
        citations = {"area-page": {"page": 2, "excerpt": "Room dimensions"}}
        accepted = _supported_uncertainty_range(row, "area_m2", citations, 10)
        self.assertEqual((accepted["low"], accepted["high"]), (9, 11))
        self.assertEqual(accepted["citations"][0]["page"], 2)
        self.assertIsNone(_supported_uncertainty_range(row, "area_m2", {}, 10))
        self.assertIsNone(_supported_uncertainty_range(row, "area_m2", citations, 12))
        row["_numeric_inferences"]["area_m2"]["uncertainty_range"]["method"] = ""
        self.assertIsNone(_supported_uncertainty_range(row, "area_m2", citations, 10))

    def test_cited_enclosed_sanitary_room_links_to_its_calibrated_area_as_a_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in {
                "ai_input.json": {"drawing_set": {"pages": [{"page": 1, "title": "Plan"}]}},
                "drawing_coverage.json": {"page_roles": [{"page": 1, "proposed_role": "main_floor_plan"}]},
                "vision_extraction_settings.json": {"owner_opt_in": True, "selected_group_ids": []},
            }.items():
                (root / name).write_text(json.dumps(value))
            project = {"id": "phase1-skill-bridge", "review_dir": str(root)}
            run_id = "run-sanitary"
            source_fp = skill_workflow_service._source_fingerprint(
                skill_workflow_service._project_paths(project), skill_workflow_service.load_catalog())
            (root / "skill_workflow_run.json").write_text(json.dumps({
                "run_id": run_id, "source_fingerprint": source_fp,
                "subskills": {"room_identity_use": {"status": "needs_review"},
                              "room_boundaries_areas": {"status": "needs_review"}},
            }))
            proposal_dir = root / "skill_workflow_runs" / run_id / "proposals"
            proposal_dir.mkdir(parents=True)
            room_id = "casa-nova:01.02:southwest-upper-wc"
            cited_room = {
                "room_id": room_id, "original_label": "", "level": None,
                "taxonomy_id": None,
                "scope": "Enclosed upper western WC compartment; conditioning unresolved.",
                "evidence_page_ids": [1], "confidence": 0.83,
                "citations": [{"page": 1, "drawing_identity": "01.02 Setout Plan",
                               "excerpt": "WC", "visual_evidence": "Enclosed WC compartment with its own door."}],
            }
            (proposal_dir / "room_identity_use.json").write_text(json.dumps({
                "confidence": 0.78,
                "citations": [{"id": "C1", "page": 1,
                               "drawing_identity": "01.02 Setout Plan", "excerpt_or_crop": "WC plan"}],
                "proposal_fields": {"rooms": [cited_room]},
            }))
            candidate = {"room_id": room_id, "label": "", "level_name": "Unassigned level",
                         "page": 1, "confidence_score": 0.78, "geometry": {"boundary_points_px": [[1, 1], [3, 1], [3, 2], [1, 2], [1, 1]]}}
            active_area = {"kind": "area", "label": "Upper southwest WC enclosure",
                           "geometry_status": "ai_estimated", "source": {"page": 1},
                           "level_candidate": "Unassigned level",
                           "value": {"room_identity_id": room_id, "area_m2": 2.62,
                                     "confidence_score": 0.78, "geometry_proof_id": "proof-wc",
                                     "derivation": {"formula": "shoelace area × calibrated scale"},
                                     "review_warnings": ["conditioning applicability"]}}
            with patch("backend.calculation_extraction_service._room_geometry_skill_proposals",
                       return_value=[candidate]), patch(
                           "backend.ai_preliminary_service._preliminary_geometry",
                           return_value={"entities": [active_area]}):
                proposal = _proposal_with_skill_findings(_paths({"review_dir": str(root)}))

            self.assertEqual(len(proposal["rooms"]), 1)
            row = proposal["rooms"][0]
            self.assertEqual(row["label"], "Upper Western WC")
            self.assertEqual(row["geometry_identity_id"], room_id)
            self.assertEqual(row["room_use_category"], "ancillary_conditioned")
            self.assertFalse(row["direct_label_verified"])
            self.assertIn("does not establish conditioning", row["room_use_rationale"])
            self.assertEqual(row["evidence"][0]["excerpt"], "Enclosed WC compartment with its own door.")

            normalized = validate_placeholder_proposal({"rooms": [{
                **row,
                "preliminary_profile_id": "generic_conditioned_room",
                "geometry": {"boundary_points_px": [[1, 1], [3, 1], [3, 2], [1, 2], [1, 1]],
                             "scale_mm_per_px": 10.58},
            }]})["rooms"]
            self.assertEqual(normalized[0]["geometry_identity_id"], room_id)
            rooms = _space_rows({}, {}, normalized, {"entities": [active_area]})
            self.assertEqual(len(rooms), 1)
            self.assertEqual(rooms[0]["area_m2"], 2.62)
            self.assertEqual(rooms[0]["area_origin"], "ai_geometry")
            self.assertEqual(rooms[0]["geometry_proof_id"], "proof-wc")

    def test_cited_surface_and_window_findings_materialize_without_manual_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 1, "title": "Plan"}]}}))
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": 1, "proposed_role": "main_floor_plan"}]}))
            (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True, "selected_group_ids": []}))
            project = {"id": "phase1-skill-bridge", "review_dir": str(root)}
            run_id = "run-1"
            source_fp = skill_workflow_service._source_fingerprint(
                skill_workflow_service._project_paths(project), skill_workflow_service.load_catalog())
            skill_ids = ("surface_inventory", "surface_area", "construction_matching", "boundary_resolution",
                         "cross_sheet_opening_match", "glazing_properties", "exposure_orientation")
            (root / "skill_workflow_run.json").write_text(json.dumps({
                "run_id": run_id, "source_fingerprint": source_fp,
                "subskills": {skill_id: {"status": "needs_review"} for skill_id in skill_ids},
            }))
            proposal_dir = root / "skill_workflow_runs" / run_id / "proposals"
            proposal_dir.mkdir(parents=True)

            def write_skill(skill_id, field, value, citation_id):
                (proposal_dir / f"{skill_id}.json").write_text(json.dumps({
                    "confidence": 0.94,
                    "citations": [{"citation_id": citation_id, "physical_pdf_page": 1,
                                   "excerpt_or_crop": f"Evidence for {field}"}],
                    "proposal_fields": {field: value if isinstance(value, list) else [value]},
                }))

            write_skill("surface_inventory", "surfaces", [{
                "surface_id": "wall-a", "label": "East external wall", "type": "wall",
                "thermal_role": "external", "owner_ids": ["shop"], "orientation": "E",
                "confidence": 0.68,
                "citations": [{"page": 1, "drawing_identity": "Plan", "excerpt": "East wall"}],
            }, {
                "surface_id": "wall-unknown-opening", "type": "wall", "thermal_role": "external",
                "owner_ids": ["shop"], "confidence": 0.67,
                "citations": [{"page": 1, "drawing_identity": "Plan", "excerpt": "Wall with unmeasured opening"}],
            }], "c-surface")
            write_skill("surface_area", "areas", [{
                "surface_id": "wall-a", "gross_area_m2": 10, "formula": "measured length × wall height",
                "opening_area_m2": 2, "net_opaque_area_m2": 8, "opening_coverage": "complete",
                "confidence": 0.72,
                "citation_ids": ["c-area"],
            }, {
                "surface_id": "wall-unknown-opening", "gross_area_m2": 9, "formula": "traced length × wall height",
                "opening_area_m2": None, "net_opaque_area_m2": None, "opening_coverage": "unknown",
                "confidence": 0.72, "citation_ids": ["c-area"],
            }], "c-area")
            write_skill("construction_matching", "constructions", {
                "surface_id": "wall-a", "assembly_id": "brick-wall", "u_value_w_m2k": 1.5,
                "source_id": "PDF wall note", "scope": {}, "citation_ids": ["c-construction"],
            }, "c-construction")
            write_skill("boundary_resolution", "boundaries", {
                "surface_id": "wall-a", "method": "external", "adjacent_owner": None,
                "temperature_c": None, "source_id": "north arrow and external wall note",
                "citation_ids": ["c-boundary"],
            }, "c-boundary")
            write_skill("cross_sheet_opening_match", "matches", {
                "opening_id": "window-1", "sighting_ids": ["plan-window-1"], "room_id": "shop",
                "host_wall_id": "wall-a", "match_basis": "matching window tag and host wall", "confidence": 0.94,
                "citation_ids": ["c-match"],
            }, "c-match")
            write_skill("glazing_properties", "properties", {
                "opening_id": "window-1", "opening_area_m2": 2, "glass_area_m2": 1.7,
                "u_value_w_m2k": 2.6, "solar_property_type": "shgc", "solar_property": 0.42,
                "source_id": "window schedule", "citation_ids": ["c-glazing"], "confidence": 0.58,
            }, "c-glazing")
            write_skill("exposure_orientation", "exposures", [], "c-orientation")

            def add_inferences(skill_id, rows):
                path = proposal_dir / f"{skill_id}.json"
                artifact = json.loads(path.read_text())
                artifact["inferences"] = rows
                path.write_text(json.dumps(artifact))

            add_inferences("surface_area", [{
                "field": "wall-a.net_opaque_area_m2", "value": 8, "method": "dimensioned host minus scheduled opening",
                "citation_ids": ["c-area"], "uncertainty_range": {
                    "low": 7, "high": 9, "method": "printed dimension rounding", "citation_ids": ["c-area"],
                },
            }])
            add_inferences("construction_matching", [{
                "field": "wall-a.u_value_w_m2k", "value": 1.5, "method": "matched wall schedule assembly",
                "citation_ids": ["c-construction"], "uncertainty_range": {
                    "low": 1.4, "high": 1.6, "method": "schedule-listed assembly range", "citation_ids": ["c-construction"],
                },
            }])
            add_inferences("glazing_properties", [
                {"field": "window-1.opening_area_m2", "value": 2, "method": "scheduled opening width by height",
                 "citation_ids": ["c-glazing"], "uncertainty_range": {
                     "low": 1.8, "high": 2.2, "method": "schedule dimension rounding", "citation_ids": ["c-glazing"],
                 }},
                {"field": "window-1.u_value_w_m2k", "value": 2.6, "method": "window schedule performance value",
                 "citation_ids": ["c-glazing"], "uncertainty_range": {
                     "low": 2.4, "high": 2.8, "method": "schedule-listed performance range", "citation_ids": ["c-glazing"],
                 }},
                {"field": "window-1.solar_property", "value": 0.42, "method": "window schedule SHGC",
                 "citation_ids": ["c-glazing"], "uncertainty_range": {
                     "low": 0.36, "high": 0.48, "method": "schedule-listed SHGC range", "citation_ids": ["c-glazing"],
                 }},
            ])

            proposal = {"rooms": [{"kind": "room", "room_id": "shop", "label": "Shop",
                                    "level_name": "Ground", "area_m2": 20, "page": 1,
                                    "preliminary_profile_id": "retail", "space_scope": "comfort_hvac",
                                    "evidence": [{"page": 1, "excerpt": "Shop"}]}],
                        "surfaces": [], "openings": [], "issues": []}
            merged = _merge_phase1_skill_proposals({"root": root}, proposal)
            self.assertEqual(len(merged["surfaces"]), 1)
            self.assertEqual(merged["surfaces"][0]["gross_area_m2"], 10)
            self.assertEqual(merged["surfaces"][0]["net_opaque_area_m2"], 8)
            self.assertEqual(merged["surfaces"][0]["opening_coverage"], "complete")
            self.assertTrue(any(item.get("excerpt") == "East wall" for item in merged["surfaces"][0]["evidence"]))
            self.assertEqual(merged["surfaces"][0]["u_value_w_m2k"], 1.5)
            self.assertEqual({row["field"] for row in merged["surfaces"][0]["supported_uncertainty_ranges"]},
                             {"area_m2", "u_value_w_m2k"})
            self.assertEqual(len(merged["openings"]), 1)
            self.assertEqual(merged["openings"][0]["opening_area_m2"], 2)
            self.assertEqual(merged["openings"][0]["external_exposure"], "external")
            self.assertEqual(merged["openings"][0]["shgc"], 0.42)
            self.assertEqual({row["field"] for row in merged["openings"][0]["supported_uncertainty_ranges"]},
                             {"area_m2", "u_value_w_m2k", "shgc"})
            self.assertTrue(all(row["evidence"] for row in [*merged["surfaces"], *merged["openings"]]))

            result = assemble({"spaces": [{"id": "shop", "name": "Shop", "level_name": "Ground",
                                            "area": "20 m2", "evidence": [{"page": 1, "excerpt": "Shop"}]}]},
                              preliminary_proposal=merged, allow_area_fallbacks=False)
            room = result["material"]["hourly_load_model"]["rooms"][0]
            self.assertEqual(len(room["cooling_load"]["envelope_surfaces"]), 1, result["exclusions"])
            self.assertEqual(len(room["cooling_load"]["glazing_surfaces"]), 1)
            self.assertEqual(room["cooling_load"]["glazing_surfaces"][0]["window"]["u_value_w_m2k"], 2.6)
            self.assertTrue(room["cooling_load"]["envelope_surfaces"][0]["supported_uncertainty_ranges"])
            self.assertTrue(room["cooling_load"]["glazing_surfaces"][0]["supported_uncertainty_ranges"])
            report = calculate(result, scope="phase1_base_room")
            reported_ranges = {row["surface_id"]: row.get("supported_load_range_kw")
                               for row in report["phase1_surface_audit"]}
            self.assertTrue(reported_ranges[room["cooling_load"]["envelope_surfaces"][0]["surface_id"]])
            self.assertTrue(reported_ranges[room["cooling_load"]["glazing_surfaces"][0]["surface_id"]])

    def test_scale_traced_full_height_openings_can_complete_a_host_without_manual_acceptance(self):
        def inference(points, value, citation_id):
            return {
                "method": "cited scale-calibrated rectangle trace",
                "value": value,
                "citation_ids": [citation_id],
                "unrounded_operands": {
                    "boundary_points_px": points,
                    "scale_x_m_per_px": 0.1,
                    "scale_y_m_per_px": 0.1,
                },
            }

        host = {
            "surface_id": "wall-shopfront",
            "gross_area_m2": 40,
            "confidence": 0.55,
            "citation_ids": ["c-host"],
            "_numeric_inferences": {
                "gross_area_m2": inference([[0, 0], [100, 0], [100, 40], [0, 40]], 40, "c-host"),
            },
        }
        matches = [
            (0, {"opening_id": "opening-west", "host_wall_id": "wall-shopfront"}, 0.82),
            (1, {"opening_id": "opening-east", "host_wall_id": "wall-shopfront"}, 0.81),
        ]
        properties = [
            (0, {"opening_id": "opening-west", "opening_area_m2": 18.4,
                 "source_id": "drawing:elevation-1:west", "confidence": 0.58,
                 "_numeric_inferences": {"opening_area_m2": inference([[4, 0], [50, 0], [50, 40], [4, 40]], 18.4, "c-west")}}, 0.58),
            (1, {"opening_id": "opening-east", "opening_area_m2": 18.4,
                 "source_id": "drawing:elevation-1:east", "confidence": 0.61,
                 "_numeric_inferences": {"opening_area_m2": inference([[50, 0], [96, 0], [96, 40], [50, 40]], 18.4, "c-east")}}, 0.61),
        ]
        area_citations = {"c-host": {"page": 40, "drawing_identity": "02.01 Shopfront Elevation"}}
        property_citations = {
            "c-west": {"page": 40, "drawing_identity": "02.01 Shopfront Elevation"},
            "c-east": {"page": 40, "drawing_identity": "02.01 Shopfront Elevation"},
        }
        derived = _complete_opening_coverage(
            [(0, host, 0.55)], matches, properties,
            area_citations=area_citations, property_citations=property_citations,
        )
        record, confidence = derived["wall-shopfront"]
        self.assertEqual(record["opening_coverage"], "complete")
        self.assertEqual(record["opening_area_m2"], 36.8)
        self.assertEqual(record["net_opaque_area_m2"], 3.2)
        self.assertEqual(confidence, 0.55)
        self.assertEqual(record["_coverage_method"], "cited_contiguous_full_height_pixel_rectangles")

        # If a matched opening lacks an area trace, the bridge must leave the
        # host coverage unresolved instead of subtracting a partial total.
        missing_one = _complete_opening_coverage(
            [(0, host, 0.55)], matches, properties[:1],
            area_citations=area_citations, property_citations=property_citations,
        )
        self.assertEqual(missing_one, {})

    def test_low_confidence_or_stale_surface_evidence_does_not_enter_the_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in {
                "ai_input.json": {"drawing_set": {"pages": [{"page": 1}]}},
                "drawing_coverage.json": {"page_roles": [{"page": 1}]},
                "vision_extraction_settings.json": {"owner_opt_in": True},
            }.items():
                (root / name).write_text(json.dumps(value))
            project = {"id": "phase1-skill-bridge", "review_dir": str(root)}
            source_fp = skill_workflow_service._source_fingerprint(
                skill_workflow_service._project_paths(project), skill_workflow_service.load_catalog())
            run_id = "run-1"
            proposal_dir = root / "skill_workflow_runs" / run_id / "proposals"
            proposal_dir.mkdir(parents=True)
            (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": run_id,
                "source_fingerprint": source_fp, "subskills": {"surface_inventory": {"status": "needs_review"}}}))
            (proposal_dir / "surface_inventory.json").write_text(json.dumps({"confidence": 0.79,
                "proposal_fields": {"surfaces": [{"surface_id": "wall-a", "type": "wall",
                    "thermal_role": "external", "owner_ids": ["shop"], "gross_area_m2": 10}]}}))
            merged = _merge_phase1_skill_proposals({"root": root}, {"rooms": [], "surfaces": [], "openings": []})
            self.assertEqual(merged["surfaces"], [])

    def test_printed_finish_sum_area_reaches_phase_one_room_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_id = "room-use:unassigned-level:cafe"
            room = {"kind": "room", "room_id": room_id, "label": "Cafe",
                    "level_name": "Unassigned level", "page": 1,
                    "evidence": [{"page": 1, "excerpt": "Cafe tenancy"}]}
            geometry_candidate = {"room_id": room_id, "label": "Cafe", "page": 1,
                "level_name": "Unassigned level", "source_pages": [1], "confidence": .86,
                "evidence": [{"page": 1, "excerpt": "TL02 40.73 m²; TL03 20.71 m²"}],
                "geometry": {"area_m2": 61.44, "formula": "40.73 m² + 20.71 m² = 61.44 m²",
                    "calibration": {"source": "printed_floor_finish_area_sum"}}}
            geometry = {"entities": [{"kind": "area", "label": "Cafe", "level_candidate": "Unassigned level",
                "geometry_status": "ai_estimated", "source": {"page": 1}, "entity_id": "area-cafe",
                "value": {"area_m2": 61.44, "derivation": {"method": "printed_floor_finish_area_sum",
                    "formula": "40.73 m² + 20.71 m² = 61.44 m²"}}}]}
            with patch("backend.calculation_extraction_service._room_geometry_skill_proposals",
                       return_value=[geometry_candidate]):
                proposal = _prepare_preliminary_proposal(
                    {"root": root}, {"rooms": [room], "surfaces": [], "openings": []},
                    {"records": [{"room_id": room_id, "original_label": "Cafe", "level_name": "Unassigned level",
                        "taxonomy_id": "dining", "preliminary_profile_id": "hospitality",
                        "space_scope": "comfort_hvac", "status": "resolved", "confidence_score": .9,
                        "evidence": room["evidence"]}]}, geometry)
            result = assemble({"spaces": []}, preliminary_proposal=proposal, geometry_resolution=geometry,
                              allow_area_fallbacks=False)

        model_rooms = result["material"]["hourly_load_model"]["rooms"]
        self.assertEqual(len(model_rooms), 1)
        self.assertEqual(model_rooms[0]["area_m2"], 61.44)
        self.assertEqual(result["phase1_assumptions"][0]["method"], "sum of distinct printed floor-finish areas")
        self.assertIn("perimeter and envelope areas remain unresolved", result["phase1_assumptions"][0]["assumption"])


if __name__ == "__main__":
    unittest.main()
