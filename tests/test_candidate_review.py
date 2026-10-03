#!/usr/bin/env python3

import json
from io import BytesIO
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.candidate_review import CROP_RENDER_DPI, create_candidate_review, dimension_zone_crops
from ai.chatgpt_packet import MAX_ADAPTIVE_CROPS_PER_PAGE, create_chatgpt_packet
from ai.geometry_review import normalise_vision
from ai.reasoning_packet import create_reasoning_packet_from_vision
from ai.design_requirements import validate_design_requirements
from ai.vision_validator import validate_vision
from backend import web_app
from backend.web_app import rebuild_reasoning_packet, save_project_vision_response, vision_response_attempt_history


def check_value(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def line(candidate_id, start, end, width, score=75):
    return {
        "candidate_id": candidate_id,
        "geometry_type": "line",
        "start_px": start,
        "end_px": end,
        "points_plan_px": [start, end],
        "length_px": ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5,
        "orientation": "horizontal",
        "stroke_width": width,
        "candidate_role_hint": "possible_wall_or_dimension",
        "confidence_score": score,
        "classification_reasons": ["test candidate"],
        "confidence": "high",
    }


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        vector_path = root / "vector_geometry.json"
        vector_path.write_text(
            json.dumps(
                {
                    "geometry_key_points": {
                        "pages": [
                            {
                                "page": 5,
                                "title": "General Arrangement Plan",
                                "plan_role": "main_floor_plan",
                                "image": "screenshots/page_005_floor_plan.png",
                                "plan_viewport": {
                                    "bbox_px": [80, 120, 760, 500],
                                    "confidence": "medium",
                                    "reasons": ["test viewport"],
                                },
                                "coordinate_systems": {
                                    "image_px": {"image_width": 800, "image_height": 600},
                                    "plan_px": {"plan_viewport_bbox_px": [80, 120, 760, 500]},
                                },
                                "line_candidates": [
                                    line("P5-VLINE-WALL", [100, 300], [700, 300], 0.5),
                                    line("P5-VLINE-DIM", [100, 250], [700, 250], 0.1),
                                    line("P5-VLINE-WIT", [100, 250], [100, 300], 0.1, 45),
                                ],
                                "curve_candidates": [
                                    {
                                        "candidate_id": "P5-VCURVE-001",
                                        "geometry_type": "curve_polyline",
                                        "points_px": [[700, 300], [730, 340], [720, 390], [690, 420]],
                                        "points_plan_px": [[700, 300], [730, 260], [720, 210], [690, 180]],
                                        "point_count": 4,
                                        "bbox_px": [690, 300, 730, 420],
                                        "confidence_score": 60,
                                        "confidence": "medium",
                                    }
                                ],
                                "dimension_candidates": [
                                    {
                                        "candidate_id": "P5-VDIMTXT-001",
                                        "text_seen": "7018",
                                        "value_mm": 7018,
                                        "bbox_px": [380, 225, 420, 245],
                                        "context": "overall dimension",
                                    }
                                ],
                                "wall_candidates": [],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        matches_path = root / "dimension_wall_matches.json"
        matches_path.write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "page": 5,
                            "dimension_span_candidates": [{
                                "dimension_candidate_id": "P5-VDIMTXT-001",
                                "value_mm": 7018,
                                "dimension_line_candidate_id": "P5-VLINE-DIM",
                                "dimension_line_start_px": [100, 250],
                                "dimension_line_end_px": [700, 250],
                            }],
                            "summary": {
                                "machine_extraction_gaps": [
                                    {"type": "missing_major_boundary_dimensions"}
                                ]
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        chatgpt_dir = root / "chatgpt_packet"
        screenshots_dir = chatgpt_dir / "screenshots"
        screenshots_dir.mkdir(parents=True)
        tiny_png(screenshots_dir / "page_005_floor_plan.png")
        (chatgpt_dir / "prompt.md").write_text("Review page 5 and return JSON.", encoding="utf-8")
        (chatgpt_dir / "manifest.json").write_text(json.dumps({
            "screenshots_kept_for_reasoning": [{"page": 5, "path": "screenshots/page_005_floor_plan.png"}],
        }), encoding="utf-8")
        output = create_candidate_review(vector_path, matches_path)
        data = json.loads(output.read_text())
        page = data["pages"][0]
        overlay_kinds = {item.get("kind") for item in data["overlays"] if item.get("status") == "created"}
        check_value("candidate review created", output.name, "candidate_review.json")
        check_value("candidate review has page", page["page"], 5)
        check_value("wall candidate classified", page["wall_candidates"][0]["candidate_id"], "P5-VLINE-WALL")
        check_value("dimension line classified", page["dimension_line_candidates"][0]["candidate_id"], "P5-VLINE-DIM")
        check_value("curve candidate kept", page["curve_candidates"][0]["review_role"], "curve_wall_candidate")
        check_value("candidate crop created", len(page["crops"]) >= 1, True)
        check_value("viewport crop first", page["crops"][0]["label"], "main_plan_viewport")
        check_value("viewport crop uses detected bbox", page["crops"][0]["bbox_px"], [80, 120, 760, 500])
        zone = [item for item in page["crops"] if item["label"] == "major_dimension_zone"][0]
        check_value("major dimension zone includes source value", zone["dimension_values_mm"], [7018])
        check_value("major dimension zone retains candidate id", zone["candidate_ids"], ["P5-VDIMTXT-001"])
        check_value("major dimension zone includes raw vectors", "P5-VLINE-WALL" in zone["raw_vector_ids"], True)
        check_value("major dimension zone uses image coordinates", zone["coordinate_system"], "image_px")
        check_value("dimension spans exposed", page["dimension_span_candidates"][0]["dimension_candidate_id"], "P5-VDIMTXT-001")
        check_value("dimension summary exposed", page["dimension_summary"]["machine_extraction_gaps"][0]["type"], "missing_major_boundary_dimensions")
        check_value("clean wall overlay created", "walls" in overlay_kinds, True)
        check_value("clean dimension overlay created", "dimensions" in overlay_kinds, True)
        check_value("fixture overlay created", "fixtures_or_joinery" in overlay_kinds, True)
        check_value("debug all-candidates overlay created", "all_candidates" in overlay_kinds, True)
        zone_image = [item for item in data["overlays"] if item.get("crop_id") == zone["crop_id"]][0]
        check_value("zone crop stores original source bbox", zone_image["source_bbox_px"], [160, 5, 640, 465])
        check_value("zone crop is enlarged for vision", zone_image["render_scale"], 2)

        # A source-PDF crop is re-rendered at a higher DPI instead of merely enlarging
        # the packet screenshot, while the original image_px crop coordinates remain.
        from PIL import Image

        source_pdf = root / "source.pdf"
        page_image = Image.new("RGB", (800, 600), "white")
        page_image.save(source_pdf, "PDF", save_all=True, append_images=[page_image.copy() for _ in range(4)])
        source_vector = json.loads(vector_path.read_text())
        source_vector["source_pdf"] = str(source_pdf)
        source_vector_path = root / "vector_geometry_source.json"
        source_vector_path.write_text(json.dumps(source_vector), encoding="utf-8")
        source_review = json.loads(
            create_candidate_review(
                source_vector_path,
                matches_path,
                root / "candidate_review_source.json",
                root / "candidate_overlays_source",
                screenshots_dir,
            ).read_text()
        )
        source_crop = next(item for item in source_review["overlays"] if item.get("crop_id") == "P5-CROP-002")
        check_value("source PDF crop is rendered directly", source_crop["render_source"], "source_pdf")
        check_value("source PDF crop records DPI", source_crop["render_dpi"], CROP_RENDER_DPI)
        check_value("source PDF crop keeps page coordinates", source_crop["source_bbox_px"], [160, 5, 640, 465])
        check_value("source PDF crop records rendered dimensions", source_crop["image_width"] > 0 and source_crop["image_height"] > 0, True)

        adaptive_dimensions = [
            {
                "candidate_id": f"P5-VDIMTXT-ADAPT-{index}",
                "bbox_px": [index * 600, 100, index * 600 + 30, 120],
                "value_mm": 3000 + index,
                "dimension_priority": 400000 + index,
                "dimension_category": "major_boundary",
            }
            for index in range(5)
        ]
        adaptive_zones = dimension_zone_crops(5, adaptive_dimensions, 3200, 600)
        check_value("adaptive zones cover fifth major dimension", len(adaptive_zones), 5)
        check_value("fifth dimension zone is marked adaptive", adaptive_zones[-1]["adaptive"], True)

        no_text_vector = json.loads(vector_path.read_text())
        no_text_vector["geometry_key_points"]["pages"][0]["dimension_candidates"] = []
        no_text_vector["geometry_key_points"]["pages"][0]["line_candidates"].append(
            line("P5-VLINE-VERTICAL-BAND", [720, 140], [720, 480], 0.1)
        )
        no_text_path = root / "vector_geometry_no_text.json"
        no_text_path.write_text(json.dumps(no_text_vector), encoding="utf-8")
        no_text_matches = root / "dimension_wall_matches_no_text.json"
        no_text_matches.write_text(json.dumps({"pages": [{"page": 5, "summary": {}}]}), encoding="utf-8")
        no_text_review = create_candidate_review(no_text_path, no_text_matches, root / "candidate_review_no_text.json", root / "candidate_overlays_no_text")
        no_text_page = json.loads(no_text_review.read_text())["pages"][0]
        check_value("raw vectors survive missing matcher spans", len(no_text_page["wall_candidates"]) >= 1, True)
        band = [item for item in no_text_page["crops"] if item["label"] == "dimension_band_candidate"][0]
        check_value("vector dimension band survives missing OCR", band["evidence_source"], "vector_dimension_band")
        check_value("vector dimension band keeps line candidates", "P5-VLINE-DIM" in band["dimension_line_candidate_ids"], True)
        check_value("vector dimension band does not invent a value", band["dimension_values_mm"], [])
        no_text_bands = [item for item in no_text_page["crops"] if item["label"] == "dimension_band_candidate"]
        check_value("horizontal and vertical bands stay separate", len(no_text_bands) >= 2, True)
        check_value("combined review prompt included", "one combined review" in data["prompt"], True)
        check_value("prompt mentions extraction gaps", "machine_extraction_gaps" in data["prompt"], True)

        ai_input_path = root / "ai_input.json"
        (root / "page_005_floor_plan.png").write_bytes((screenshots_dir / "page_005_floor_plan.png").read_bytes())
        ai_input_path.write_text(
            json.dumps(
                {
                    "source_pdf": "missing.pdf",
                    "source_files": {"page_images": [{"page": 5, "type": "floor_plan", "path": "page_005_floor_plan.png"}]},
                    "confirmed_pages": {"floor_plans": [{"page": 5}], "reflected_ceiling_plans": [], "existing_hvac_or_services_plans": [], "reference_pages": []},
                    "design_inputs": {"legend_key_pages": []},
                }
            ),
            encoding="utf-8",
        )
        compact_packet = create_chatgpt_packet(ai_input_path, root / "compact_packet")
        compact_context = json.loads(Path(compact_packet["context"]).read_text())
        compact_page = compact_context["geometry_pages"][0]
        check_value("compact packet keeps one full plan", compact_page["image"].startswith("vision_evidence/"), True)
        check_value("compact packet limits adaptive crops", len(compact_page["crops"]) <= MAX_ADAPTIVE_CROPS_PER_PAGE, True)
        check_value("compact packet limits raw vectors per crop", all(len(item["raw_vector_ids"]) <= 12 for item in compact_page["crops"]), True)
        with zipfile.ZipFile(compact_packet["zip"]) as archive:
            names = archive.namelist()
        check_value("drawing-set zip includes reviewed screenshots", any(name.startswith("screenshots/") for name in names), True)
        check_value("compact zip excludes raw vectors", "vector_geometry.json" in names, False)

        compact_vision = {
            "result": {
                "geometry_review": {
                    "pages": [
                        {
                            "page": 5,
                            "image": compact_page["image"],
                            "coordinate_system": {"image_width": 800, "image_height": 600, "units": "image_px"},
                            "plan_viewport_bbox_px": [80, 120, 760, 500],
                            "plan_viewport_confidence": "high",
                            "plan_viewport_uncertainties": [],
                            "page_role": "main_geometry_and_dimension_plan",
                            "geometry_readiness": "vision_layered",
                            "walls": [{"wall_id": "P5-VWALL-001", "classification": "existing_wall", "geometry_role": "outer_boundary_wall", "geometry_type": "line", "points_px": [[100, 300], [700, 300]], "supporting_vector_ids": ["P5-VLINE-WALL"], "source": "vector_anchored", "visible_evidence": ["solid plan boundary"], "confidence": "high"}],
                            "fixed_obstacles": [{"obstacle_id": "P5-OBS-001", "classification": "unknown_fixed_obstacle", "geometry_type": "circle", "centre_px": [620, 430], "radius_px": 35, "related_dimensions_mm": [950], "routing_constraint": "do_not_route_through", "visible_evidence": ["visible circular built obstruction"], "confidence": "high"}],
                            "major_dimensions": [{"dimension_id": "P5-VDIMTXT-001", "value_mm": 7018, "text_seen": "7018", "bbox_px": [380, 225, 420, 245], "dimension_line_start_px": [100, 250], "dimension_line_end_px": [700, 250], "measured_span_start_px": [100, 250], "measured_span_end_px": [700, 250], "dimension_kind": "overall", "confidence": "high", "site_confirm_required": False}],
                            "dimension_wall_links": [{"measurement_id": "P5-WD-001", "dimension_id": "P5-VDIMTXT-001", "target_wall_id": "P5-VWALL-001", "confidence": "high", "should_use_for_calculation": True, "site_confirm_required": False, "visible_evidence": ["witness lines cover the wall"]}],
                            "unassigned_dimensions": [],
                            "conflicts": [],
                        }
                    ]
                }
            }
        }
        normalised = normalise_vision(compact_vision, data)
        check_value("compact response derives coordinate review", normalised["result"]["coordinate_review"]["pages"][0]["wall_dimensions"][0]["value_mm"], 7018)
        check_value("compact response derives layered geometry", normalised["result"]["layered_geometry"]["pages"][0]["outer_boundary_walls"][0]["wall_id"], "P5-VWALL-001")
        check_value("compact response preserves fixed obstacle", normalised["result"]["layered_geometry"]["pages"][0]["fixed_obstacles"][0]["related_dimensions_mm"], [950])
        check_value("compact response validates", validate_vision(compact_vision, data)["issue_count"], 0)

        top_level_compact_vision = compact_vision["result"]
        top_level_normalised = normalise_vision(top_level_compact_vision, data)
        check_value("top-level compact response derives layered geometry", top_level_normalised["result"]["layered_geometry"]["pages"][0]["outer_boundary_walls"][0]["wall_id"], "P5-VWALL-001")
        check_value("top-level compact response validates", validate_vision(top_level_compact_vision, data)["issue_count"], 0)

        invalid_obstacle = json.loads(json.dumps(compact_vision))
        invalid_obstacle["result"]["geometry_review"]["pages"][0]["fixed_obstacles"][0]["radius_px"] = 0
        check_value("invalid fixed obstacle fails validation", validate_vision(invalid_obstacle, data)["issue_count"] > 0, True)

        screenshot_dimension = json.loads(json.dumps(compact_vision))
        screenshot_dimension["result"]["geometry_review"]["pages"][0]["major_dimensions"] = [{
            "dimension_id": "P5-VDIM-VISION-001",
            "source": "screenshot_visible",
            "source_annotation_id": None,
            "value_mm": 8075,
            "text_seen": "8075",
            "dimension_kind": "overall",
            "bbox_px": [380, 225, 420, 245],
            "dimension_line_start_px": [100, 250],
            "dimension_line_end_px": [700, 250],
            "arrowhead_start_px": [100, 250],
            "arrowhead_end_px": [700, 250],
            "witness_lines_px": [[[100, 250], [100, 300]]],
            "measured_span_start_px": [100, 250],
            "measured_span_end_px": [700, 250],
            "visible_evidence": ["8075 sits on an overall dimension line with arrows and witness lines"],
            "confidence": "high",
            "site_confirm_required": False,
        }]
        screenshot_dimension["result"]["geometry_review"]["pages"][0]["dimension_wall_links"][0]["dimension_id"] = "P5-VDIM-VISION-001"
        screenshot_normalised = normalise_vision(screenshot_dimension, data)
        check_value("screenshot dimension preserves source", screenshot_normalised["result"]["layered_geometry"]["pages"][0]["dimension_candidates"][0]["source"], "screenshot_visible")
        check_value("screenshot dimension validates", validate_vision(screenshot_dimension, data)["issue_count"], 0)

        missing_evidence = json.loads(json.dumps(screenshot_dimension))
        missing_evidence["result"]["geometry_review"]["pages"][0]["major_dimensions"][0]["witness_lines_px"] = []
        missing_evidence["result"]["geometry_review"]["pages"][0]["major_dimensions"][0]["arrowhead_start_px"] = None
        missing_evidence["result"]["geometry_review"]["pages"][0]["major_dimensions"][0]["arrowhead_end_px"] = None
        check_value("screenshot dimension without evidence fails", validate_vision(missing_evidence, data)["issue_count"] > 0, True)

        malformed_id = json.loads(json.dumps(screenshot_dimension))
        malformed_id["result"]["geometry_review"]["pages"][0]["major_dimensions"][0]["dimension_id"] = "P5-DIMENSION-001"
        malformed_id["result"]["geometry_review"]["pages"][0]["dimension_wall_links"][0]["dimension_id"] = "P5-DIMENSION-001"
        check_value("malformed screenshot dimension id fails", validate_vision(malformed_id, data)["issue_count"] > 0, True)

        vision = {
            "provider": "chatgpt_manual",
            "model": "manual_vision_review",
            "source": "candidate_review",
            "result": {
                "coordinate_review": {
                    "pages": [
                        {
                            "page": 5,
                            "image": "screenshots/page_005_floor_plan.png",
                            "coordinate_system": {"image_width": 800, "image_height": 600, "units": "image_px"},
                            "plan_viewport_bbox_px": [0, 0, 800, 600],
                            "wall_candidates": [
                                {
                                    "candidate_id": "P5-WALL-001",
                                    "source_candidate_id": "P5-VLINE-WALL",
                                    "line_start_px": [100, 300],
                                    "line_end_px": [700, 300],
                                    "source": "pdf_vector",
                                    "confidence": "high",
                                }
                            ],
                            "dimension_candidates": [
                                {
                                    "candidate_id": "P5-DIM-001",
                                    "source_candidate_id": "P5-VDIMTXT-001",
                                    "text_seen": "7018",
                                    "value_mm": 7018,
                                    "bbox_px": [380, 225, 420, 245],
                                    "source": "pdf_vector",
                                    "confidence": "high",
                                }
                            ],
                            "room_label_candidates": [],
                            "opening_candidates": [],
                            "wall_dimensions": [
                                {
                                    "measurement_id": "P5-WD-001",
                                    "value_mm": 7018,
                                    "dimension_text_candidate_id": "P5-VDIMTXT-001",
                                    "dimension_line_candidate_id": "P5-VLINE-DIM",
                                    "dimension_text_bbox_px": [380, 225, 420, 245],
                                    "dimension_line_start_px": [100, 250],
                                    "dimension_line_end_px": [700, 250],
                                    "target_wall_candidate_id": "P5-WALL-001",
                                    "target_wall_start_px": [100, 300],
                                    "target_wall_end_px": [700, 300],
                                    "source": "vision_model",
                                    "confidence": "high",
                                    "should_use_for_calculation": True,
                                }
                            ],
                        }
                    ]
                }
            },
        }
        validation = validate_vision(vision, data)
        check_value("candidate-aware vision validates", validation["issue_count"], 0)
        bad_vision = json.loads(json.dumps(vision))
        bad_vision["result"]["coordinate_review"]["pages"][0]["wall_dimensions"][0]["dimension_line_candidate_id"] = "P5-MISSING"
        bad_validation = validate_vision(bad_vision, data)
        check_value("invalid vision candidate id fails", bad_validation["issue_count"] > 0, True)

        vision["result"]["layered_geometry"] = layered_geometry()
        layered_validation = validate_vision(vision, data)
        check_value("layered geometry validates", layered_validation["issue_count"], 0)
        check_value("layered geometry page counted", layered_validation["layered_geometry_page_count"], 1)
        check_value("layered geometry page ready", layered_validation["layered_geometry_ready_pages"], [5])

        bad_layered = json.loads(json.dumps(vision))
        bad_layered["result"]["layered_geometry"]["pages"][0]["fixture_or_joinery_geometry"][0]["classification"] = "outer_boundary_wall"
        bad_layered_validation = validate_vision(bad_layered, data)
        check_value("bad layered fixture classification fails", bad_layered_validation["issue_count"] > 0, True)

        legacy_issue_vision = json.loads(json.dumps(vision))
        legacy_issue_vision["result"]["coordinate_review"]["pages"][0]["wall_dimensions"][0]["dimension_line_candidate_id"] = "P5-MISSING"
        legacy_issue_validation = validate_vision(legacy_issue_vision, data)
        check_value("legacy-only issue is not counted as a layered issue", legacy_issue_validation["layered_geometry_issue_count"], 0)
        check_value("legacy-only issue still counted in overall issue_count", legacy_issue_validation["issue_count"] > 0, True)

        bad_source_candidate_vision = json.loads(json.dumps(vision))
        bad_source_candidate_vision["result"]["layered_geometry"]["pages"][0]["outer_boundary_walls"][0]["source_candidate_ids"] = ["P5-MISSING-VLINE"]
        bad_source_candidate_validation = validate_vision(bad_source_candidate_vision, data)
        check_value("invented source_candidate_id is flagged", bad_source_candidate_validation["layered_geometry_issue_count"] > 0, True)

        ai_input_path = root / "ai_input.json"
        ai_input_path.write_text(
            json.dumps(
                {
                    "source_pdf": "sample.pdf",
                    "review_status": {},
                    "design_inputs": {
                        "geometry_evidence_pages": [{"page": 5, "title": "General Arrangement Plan"}],
                        "dimension_evidence_pages": [{"page": 5, "title": "General Arrangement Plan"}],
                        "rcp_service_context_pages": [],
                        "legend_key_pages": [],
                    },
                    "confirmed_pages": {"floor_plans": [], "reflected_ceiling_plans": [], "existing_hvac_or_services_plans": [], "reference_pages": []},
                    "building_model": {"floors": []},
                }
            ),
            encoding="utf-8",
        )
        vision_path = root / "vision_response.json"
        vision_path.write_text(json.dumps(vision), encoding="utf-8")
        reasoning = create_reasoning_packet_from_vision(
            ai_input_path,
            vision_path,
            chatgpt_dir,
            root / "reasoning_packet",
            zip_packet=False,
            vector_geometry_path=vector_path,
            dimension_wall_matches_path=matches_path,
            candidate_review_path=output,
        )
        check_value("reasoning packet from vision created", Path(reasoning["folder"]).exists(), True)
        check_value("reasoning packet includes vision response", Path(reasoning["vision_response"]).exists(), True)
        check_value("reasoning packet includes vision validation", Path(reasoning["vision_validation"]).exists(), True)
        check_value("reasoning packet includes candidate review", Path(reasoning["candidate_review"]).exists(), True)
        check_value("reasoning packet includes coordinate review", Path(reasoning["coordinate_review"]).exists(), True)
        check_value("reasoning packet includes geometry confirmation", Path(reasoning["geometry_confirmation"]).exists(), True)
        check_value("reasoning packet includes geometry confirmation overlay", len(reasoning["geometry_confirmation_overlays"]), 1)
        reasoning_manifest = json.loads(Path(reasoning["manifest"]).read_text())
        reasoning_prompt = Path(reasoning["prompt"]).read_text()
        check_value("reasoning packet prioritizes layered geometry", reasoning_manifest["geometry_verification_status"], "geometry_vision_layered")
        check_value("reasoning manifest summarizes layered walls", reasoning_manifest["evidence_summary"]["vision_layered_geometry_evidence"]["outer_boundary_wall_count"], 1)
        check_value("reasoning manifest summarizes fixed obstacles", reasoning_manifest["evidence_summary"]["vision_layered_geometry_evidence"]["fixed_obstacle_count"], 1)
        check_value("reasoning manifest summarizes geometry confirmation", reasoning_manifest["evidence_summary"]["geometry_confirmation_evidence"]["status"], "cad_ready_candidate")
        check_value("reasoning prompt mentions layered geometry", "layered_geometry" in reasoning_prompt, True)
        check_value("reasoning prompt mentions geometry confirmation", "geometry_confirmation.json" in reasoning_prompt, True)

        legacy_issue_vision_path = root / "vision_response_legacy_issue.json"
        legacy_issue_vision_path.write_text(json.dumps(legacy_issue_vision), encoding="utf-8")
        legacy_issue_reasoning = create_reasoning_packet_from_vision(
            ai_input_path,
            legacy_issue_vision_path,
            chatgpt_dir,
            root / "reasoning_packet_legacy_issue",
            zip_packet=False,
            vector_geometry_path=vector_path,
            dimension_wall_matches_path=matches_path,
            candidate_review_path=output,
        )
        legacy_issue_manifest = json.loads(Path(legacy_issue_reasoning["manifest"]).read_text())
        check_value(
            "legacy-only validation issue does not block layered geometry status",
            legacy_issue_manifest["geometry_verification_status"],
            "geometry_vision_layered",
        )

        project = {
            "id": "test-project",
            "name": "sample.pdf",
            "review_dir": str(root),
            "ai_input": str(ai_input_path),
            "chatgpt_packet": {"folder": str(chatgpt_dir)},
            "vector_geometry": str(vector_path),
            "dimension_wall_matches": str(matches_path),
            "candidate_review": str(output),
        }
        (root / "vision_response.json").unlink(missing_ok=True)
        no_ai_payload = json.dumps({"project_id": "test-project"}).encode("utf-8")
        no_ai_request = type("Request", (), {"path": "/api/vision-response/no-ai", "headers": {
            "Content-Length": str(len(no_ai_payload))}, "rfile": BytesIO(no_ai_payload)})()
        with patch.object(web_app, "project_by_id", return_value=project), patch.object(web_app, "update_project"):
            no_ai_result = web_app.api_start_without_ai_evidence(no_ai_request)
            repeated_no_ai = web_app.api_start_without_ai_evidence(no_ai_request)
        check_value("no-AI path creates a reasoning packet", no_ai_result["has_reasoning_packet"], True)
        check_value("repeated no-AI start is idempotent", repeated_no_ai["status"], "already_started_without_ai")
        no_ai_attempt = next(item for item in vision_response_attempt_history(project)
                             if item["outcome"] == "no_ai_evidence")
        no_ai_attempt_dir = root / "chatgpt_runs" / no_ai_attempt["attempt_id"]
        check_value("no-AI start is archived with its own outcome", no_ai_attempt["outcome"], "no_ai_evidence")
        check_value("no-AI history is not reported as an accepted AI reply",
                    no_ai_attempt["outcome"] != "accepted", True)
        check_value("no-AI provenance records none as its evidence source",
                    json.loads(Path(project["vision_response"]).read_text())["evidence_source"], "none")
        check_value("no-AI marker is not labelled as a ChatGPT provider",
                    json.loads(Path(project["vision_response"]).read_text())["provider"], "none")
        check_value("no-AI attempt preserves a raw marker", json.loads((no_ai_attempt_dir / "raw_reply.txt").read_text())["evidence_source"], "none")
        requirement_body = json.dumps({"project_id": "test-project", "requirements": web_app.empty_design_requirements()}).encode("utf-8")
        requirement_request = type("Request", (), {"path": "/api/design-requirements", "headers": {
            "Content-Length": str(len(requirement_body))}, "rfile": BytesIO(requirement_body)})()
        with patch.object(web_app, "project_by_id", return_value=project), patch.object(web_app, "update_project"):
            saved_requirements = web_app.api_save_design_requirements(requirement_request)
        check_value("design inputs can be saved without an AI reply",
                    Path(project["design_requirements"]).is_file() and bool(saved_requirements.get("requirements")), True)
        raw_reply = "```json\n" + json.dumps(vision) + "\n```"
        saved = save_project_vision_response(project, raw_reply, model_note="ChatGPT test model",
            attached_pages=[2, 5, 2, 0, "4"])
        check_value("backend saves pasted vision response", Path(saved["vision_response_path"]).exists(), True)
        check_value("later real reply replaces the no-AI marker",
                    json.loads(Path(saved["vision_response_path"]).read_text()).get("source"), "manual_chatgpt")
        attempt_path = Path(root / "chatgpt_runs" / saved["attempt"]["attempt_id"])
        attempt_meta = json.loads((attempt_path / "attempt.json").read_text(encoding="utf-8"))
        check_value("raw reply is preserved byte-for-byte", (attempt_path / "raw_reply.txt").read_text(encoding="utf-8"), raw_reply)
        check_value("accepted attempt records outcome", attempt_meta["outcome"], "accepted")
        check_value("attempt records model note", attempt_meta["model_note"], "ChatGPT test model")
        check_value("attempt records packet pages", attempt_meta["packet_pages"], [5])
        check_value("attempt records pages actually attached", attempt_meta["attached_pages"], [2, 5])
        check_value("attempt records packet fingerprint", len(attempt_meta["packet_fingerprint"]) == 64, True)
        check_value("attempt records prompt fingerprint", len(attempt_meta["prompt_fingerprint"]) == 64, True)
        check_value("accepted attempt stores validation counts", "validation_issues" in attempt_meta["result_counts"], True)
        check_value("backend creates vision validation", Path(saved["vision_validation_path"]).exists(), True)
        check_value("backend creates coordinate review", Path(saved["coordinate_review_path"]).exists(), True)
        check_value("backend creates geometry confirmation", Path(saved["geometry_confirmation_path"]).exists(), True)
        check_value("backend withholds geometry artifact urls outside a registered project root", saved["response"]["geometry_confirmation_url"], "")
        check_value("backend returns layered status", saved["response"]["geometry_verification_status"], "geometry_vision_layered")
        check_value("backend creates reasoning manifest", Path(saved["reasoning_packet_raw"]["manifest"]).exists(), True)

        requirements_path = root / "design_requirements.json"
        requirements_path.write_text(json.dumps(validate_design_requirements({
            "space_usage": "Retail", "occupancy": 12, "operating_hours": "Weekdays",
            "indoor_cooling_setpoint_c": 24, "indoor_heating_setpoint_c": 20,
            "outdoor_summer_db_c": 35, "outdoor_winter_db_c": 5,
            "fresh_air_basis": "Designer basis", "exhaust_basis": "No process exhaust",
            "cooking_activity": "none", "hood_requirement": "not_required",
            "exhaust_outcome": "not_required", "make_up_air_requirement": "not_required",
            "ceiling_height_mm": 3000, "ceiling_void_height_mm": 400,
            "heat_sources": [{"name": "Fridge", "quantity": 1, "watts": 600, "verification_status": "confirmed", "source": "Schedule"}],
            "existing_services": "Existing supply", "code_basis": "NCC and AS 1668",
            "verification": {
                "occupancy": {"status": "confirmed", "source": "Client brief"},
                "design_conditions": {"status": "confirmed", "source": "Designer basis"},
                "outside_air": {"status": "confirmed", "source": "AS 1668"},
                "exhaust": {"status": "not_applicable", "source": "No cooking process"},
                "heat_sources": {"status": "confirmed", "source": "Schedule"},
                "ceiling": {"status": "confirmed", "source": "Architectural plan"},
                "existing_services": {"status": "confirmed", "source": "Site survey"},
            },
        })), encoding="utf-8")
        project["vision_response"] = saved["vision_response_path"]
        current_real_reply = Path(saved["vision_response_path"]).read_bytes()
        project.pop("reasoning_packet", None)
        with patch.object(web_app, "project_by_id", return_value=project), patch.object(web_app, "update_project"):
            preserved = web_app.api_start_without_ai_evidence(no_ai_request)
        check_value("no-AI start never replaces a later real reply", preserved["status"], "real_reply_preserved")
        check_value("re-confirming with a real reply rebuilds the workspace", preserved["has_reasoning_packet"], True)
        check_value("real reply bytes stay unchanged after a repeated no-AI start",
                    Path(saved["vision_response_path"]).read_bytes(), current_real_reply)
        from ai.heat_loads import calculate_heat_load_report
        from ai.ventilation import calculate_ventilation_report

        heat_load_path = root / "heat_load_report.json"
        current_requirements = json.loads(requirements_path.read_text())
        heat_load_path.write_text(json.dumps(calculate_heat_load_report(current_requirements)), encoding="utf-8")
        project["heat_load_report"] = str(heat_load_path)
        ventilation_path = root / "ventilation_report.json"
        ventilation_path.write_text(json.dumps(calculate_ventilation_report(current_requirements)), encoding="utf-8")
        project["ventilation_report"] = str(ventilation_path)
        rebuilt = rebuild_reasoning_packet(project, requirements_path)
        rebuilt_manifest = json.loads(Path(rebuilt["reasoning_packet_raw"]["manifest"]).read_text())
        check_value("reasoning packet copies design requirements", Path(rebuilt["reasoning_packet_raw"]["design_requirements"]).exists(), True)
        check_value("reasoning packet records complete inputs", rebuilt_manifest["design_requirements"]["readiness"]["status"], "final_design_inputs_complete")
        check_value("reasoning packet copies current heat-load report", Path(rebuilt["reasoning_packet_raw"]["heat_load_report"]).exists(), True)
        check_value("reasoning packet records heat-load summary", rebuilt_manifest["heat_load_summary"]["status"], "blocked")
        check_value("reasoning packet copies current ventilation report", Path(rebuilt["reasoning_packet_raw"]["ventilation_report"]).exists(), True)
        check_value("reasoning packet records ventilation summary", rebuilt_manifest["ventilation_summary"]["status"], "blocked")

        stale_requirements = dict(current_requirements)
        stale_requirements["updated_at"] = "changed-after-calculation"
        requirements_path.write_text(json.dumps(stale_requirements), encoding="utf-8")
        stale = rebuild_reasoning_packet(project, requirements_path)
        check_value("stale heat-load report is excluded", stale["reasoning_packet_raw"]["heat_load_report"], "")
        check_value("stale ventilation report is excluded", stale["reasoning_packet_raw"]["ventilation_report"], "")
        check_value("stale heat-load file is removed from packet", (Path(stale["reasoning_packet_raw"]["folder"]) / "heat_load_report.json").exists(), False)
        check_value("stale ventilation file is removed from packet", (Path(stale["reasoning_packet_raw"]["folder"]) / "ventilation_report.json").exists(), False)

        previous_current_response = Path(saved["vision_response_path"]).read_bytes()
        try:
            save_project_vision_response(project, "{not valid json")
            raise AssertionError("invalid pasted JSON should fail")
        except ValueError as error:
            check_value("backend rejects invalid pasted JSON", "not valid JSON" in str(error), True)
        check_value("rejected paste does not replace current accepted response", Path(saved["vision_response_path"]).read_bytes(), previous_current_response)
        attempts = vision_response_attempt_history(project)
        check_value("no-AI, accepted and rejected attempts remain in history", len(attempts), 3)
        rejected = next(item for item in attempts if item["outcome"] == "rejected")
        rejected_dir = root / "chatgpt_runs" / rejected["attempt_id"]
        check_value("rejected attempt retains raw reply", (rejected_dir / "raw_reply.txt").read_text(encoding="utf-8"), "{not valid json")
        check_value("rejected attempt records parse reason", "not valid JSON" in rejected["outcome_detail"], True)

        try:
            with patch("backend.web_app.rebuild_reasoning_packet", side_effect=RuntimeError("reasoning rebuild failed")):
                save_project_vision_response(project, json.dumps(vision), model_note="rebuild failure case")
            raise AssertionError("a parsed reply with a failed reasoning rebuild should be rejected")
        except RuntimeError as error:
            check_value("reasoning rebuild failure is reported", str(error), "reasoning rebuild failed")
        check_value("parsed reply rejected by rebuild does not replace accepted response",
                    Path(saved["vision_response_path"]).read_bytes(), previous_current_response)
        rebuild_rejected = next(item for item in vision_response_attempt_history(project)
                                if item.get("model_note") == "rebuild failure case")
        check_value("parsed reply rebuild failure is archived as rejected", rebuild_rejected["outcome"], "rejected")
        check_value("rebuild failure text is retained", rebuild_rejected["outcome_detail"], "reasoning rebuild failed")

        with TemporaryDirectory() as folder:
            root = Path(folder)
            response_path = root / "vision_response.json"
            previous = b'{"accepted":"before"}'
            response_path.write_bytes(previous)
            project_for_api = {"id": "api-test", "review_dir": str(root), "vision_response": str(response_path)}
            attempt_id = "api-chain-failure"
            attempt_dir = root / "chatgpt_runs" / attempt_id
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "attempt.json").write_text(json.dumps({"attempt_id": attempt_id,
                "created_at": "now", "outcome": "processing", "outcome_detail": "Working"}), encoding="utf-8")

            def fake_save(*_args, **_kwargs):
                response_path.write_bytes(b'{"accepted":"candidate"}')
                return {"vision_response_path": str(response_path), "vision_validation_path": str(root / "vision_validation.json"),
                    "coordinate_review_path": str(root / "coordinate_review.json"), "geometry_confirmation_path": "",
                    "reasoning_packet_raw": {}, "attempt": {"attempt_id": attempt_id, "outcome": "processing"},
                    "response": {}}

            payload = json.dumps({"project_id": "api-test", "vision_json": "{}"}).encode("utf-8")
            request = type("Request", (), {"path": "/api/vision-response", "headers": {
                "Content-Length": str(len(payload))}, "rfile": BytesIO(payload)})()
            try:
                with patch.object(web_app, "project_by_id", return_value=project_for_api), \
                     patch.object(web_app, "save_project_vision_response", side_effect=fake_save), \
                     patch.object(web_app, "_rebuild_evidence_chain", side_effect=RuntimeError("evidence rebuild failed")):
                    web_app.api_save_vision_response(request)
                raise AssertionError("evidence-chain failure should reject the paste")
            except RuntimeError as error:
                check_value("full evidence-chain failure is reported", str(error), "evidence rebuild failed")
            check_value("evidence-chain failure restores previously accepted response", response_path.read_bytes(), previous)
            api_attempt = json.loads((attempt_dir / "attempt.json").read_text(encoding="utf-8"))
            check_value("paste is not accepted until evidence chain completes", api_attempt["outcome"], "rejected")
            check_value("evidence-chain failure reason is retained", api_attempt["outcome_detail"], "evidence rebuild failed")

        missing_packet_project = {"review_dir": str(root / "missing-packet-project")}
        try:
            save_project_vision_response(missing_packet_project, "reply without packet")
            raise AssertionError("missing packet should reject a pasted reply")
        except ValueError as error:
            check_value("missing packet is rejected after archive", "Create the ChatGPT packet" in str(error), True)
        missing_attempt = vision_response_attempt_history(missing_packet_project)[0]
        missing_attempt_dir = Path(missing_packet_project["review_dir"]) / "chatgpt_runs" / missing_attempt["attempt_id"]
        check_value("reply is archived even when packet context is missing", (missing_attempt_dir / "raw_reply.txt").read_text(encoding="utf-8"), "reply without packet")
        check_value("missing packet attempt has rejected outcome", missing_attempt["outcome"], "rejected")


def tiny_png(path):
    from PIL import Image

    Image.new("RGB", (800, 600), "white").save(path)


def layered_geometry():
    return {
        "pages": [
            {
                "page": 5,
                "image": "screenshots/page_005_floor_plan.png",
                "page_role": "main_geometry_and_dimension_plan",
                "plan_viewport_bbox_px": [0, 0, 800, 600],
                "geometry_readiness": "vision_layered",
                "outer_boundary_walls": [
                    {
                        "wall_id": "P5-OUTER-TOP",
                        "classification": "outer_boundary_wall",
                        "label": "top boundary",
                        "line_start_px": [100, 300],
                        "line_end_px": [700, 300],
                        "confidence": "high",
                        "source_candidate_ids": ["P5-VLINE-WALL"],
                    }
                ],
                "internal_partitions": [],
                "fixture_or_joinery_geometry": [
                    {
                        "geometry_id": "P5-FIXTURE-001",
                        "classification": "fixture_or_joinery",
                        "label": "display counter",
                        "line_start_px": [100, 360],
                        "line_end_px": [300, 360],
                        "confidence": "medium",
                        "source_candidate_ids": [],
                    }
                ],
                "fixed_obstacles": [
                    {
                        "obstacle_id": "P5-OBS-001",
                        "classification": "unknown_fixed_obstacle",
                        "geometry_type": "circle",
                        "centre_px": [620, 430],
                        "radius_px": 35,
                        "related_dimensions_mm": [950],
                        "routing_constraint": "do_not_route_through",
                        "visible_evidence": ["visible circular built obstruction"],
                        "confidence": "high",
                    }
                ],
                "columns": [],
                "openings": [],
                "dimension_candidates": [
                    {
                        "dimension_id": "P5-DIM-7018",
                        "value_mm": 7018,
                        "text_seen": "7018",
                        "dimension_kind": "overall",
                        "bbox_px": [380, 225, 420, 245],
                        "dimension_line_start_px": [100, 250],
                        "dimension_line_end_px": [700, 250],
                        "measured_span_start_px": [100, 250],
                        "measured_span_end_px": [700, 250],
                        "confidence": "high",
                        "site_confirm_required": False,
                    }
                ],
                "dimension_wall_links": [
                    {
                        "measurement_id": "P5-WD-7018",
                        "dimension_id": "P5-DIM-7018",
                        "value_mm": 7018,
                        "target_wall_id": "P5-OUTER-TOP",
                        "target_wall_classification": "outer_boundary_wall",
                        "dimension_line_start_px": [100, 250],
                        "dimension_line_end_px": [700, 250],
                        "target_wall_start_px": [100, 300],
                        "target_wall_end_px": [700, 300],
                        "confidence": "high",
                        "should_use_for_calculation": True,
                        "site_confirm_required": False,
                    }
                ],
                "rejected_or_noise_candidates": [
                    {
                        "candidate_id": "P5-VLINE-DIM",
                        "classification": "dimension_line",
                        "reason": "dimension line, not wall",
                    }
                ],
            }
        ]
    }


if __name__ == "__main__":
    main()
