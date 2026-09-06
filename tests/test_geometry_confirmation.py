#!/usr/bin/env python3

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.geometry_confirmation import create_geometry_confirmation
from ai.vision_validator import validate_vision


def check_value(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        vision_path = root / "vision_response.json"
        vector_path = root / "vector_geometry.json"
        screenshots = root / "screenshots"
        screenshots.mkdir()
        tiny_png(screenshots / "page_005_floor_plan.png")

        vision_path.write_text(json.dumps(vision_response()), encoding="utf-8")
        vector_path.write_text(json.dumps(vector_geometry()), encoding="utf-8")

        output_path = create_geometry_confirmation(
            vision_path,
            vector_path,
            root / "geometry_confirmation.json",
            root / "geometry_confirmation_overlays",
            screenshots,
        )
        data = json.loads(output_path.read_text())
        page = data["pages"][0]
        wall = page["wall_confirmations"][0]
        link = page["dimension_link_confirmations"][0]

        check_value("geometry confirmation created", output_path.exists(), True)
        check_value("wall snaps to vector wall", wall["status"], "vector_confirmed")
        check_value("wall did not snap to dimension line", wall["matched_vector_candidate_id"], "P5-VLINE-WALL")
        check_value("dimension line can still snap to thin vector", link["matched_dimension_vector_id"], "P5-VLINE-DIM")
        check_value("high confidence link becomes cad-ready candidate", link["status"], "cad_ready_candidate")
        check_value("scale conversion created", data["scale_conversions"][0]["source_value_mm"], 7018)
        check_value("cad geometry created", data["cad_ready_geometry"][0]["wall_id"], "P5-OUTER-TOP")
        check_value("overlay created", data["overlays"][0]["status"], "created")

        cos = vision_response()
        cos["result"]["layered_geometry"]["pages"][0]["dimension_wall_links"][0]["notes"] = "C.O.S"
        (root / "vision_response_cos.json").write_text(json.dumps(cos), encoding="utf-8")
        cos_output = create_geometry_confirmation(root / "vision_response_cos.json", vector_path, root / "geometry_confirmation_cos.json")
        cos_data = json.loads(cos_output.read_text())
        check_value("C.O.S link stays review-only", cos_data["pages"][0]["dimension_link_confirmations"][0]["status"], "review_only")

        # A line candidate_review already classified as a dimension line must not be
        # offered as a wall snap target, even when it is thick enough to look structural.
        review_path = root / "candidate_review.json"
        review_path.write_text(json.dumps({
            "pages": [{
                "page": 5,
                "dimension_line_candidates": [{"candidate_id": "P5-VLINE-WALL"}],
                "witness_line_candidates": [],
            }]
        }), encoding="utf-8")
        excluded = create_geometry_confirmation(
            vision_path, vector_path, root / "geometry_confirmation_excluded.json",
            candidate_review_path=review_path)
        excluded_wall = json.loads(excluded.read_text())["pages"][0]["wall_confirmations"][0]
        check_value("annotation-classified line is not a wall target",
                    excluded_wall.get("matched_vector_candidate_id"), None)
        check_value("wall without a vector falls back to estimate", excluded_wall["status"], "vision_estimated")

        # A vector far enough away is not confirmation, however well aligned it is.
        far = vector_geometry()
        for line in far["geometry_key_points"]["pages"][0]["line_candidates"]:
            if line["candidate_id"] == "P5-VLINE-WALL":
                line["start_px"] = [line["start_px"][0], line["start_px"][1] + 45]
                line["end_px"] = [line["end_px"][0], line["end_px"][1] + 45]
        far_path = root / "vector_geometry_far.json"
        far_path.write_text(json.dumps(far), encoding="utf-8")
        far_wall = json.loads(create_geometry_confirmation(
            vision_path, far_path, root / "geometry_confirmation_far.json").read_text()
        )["pages"][0]["wall_confirmations"][0]
        check_value("distant vector does not confirm a wall", far_wall["status"], "vision_estimated")

        # A historical layered response can contain several collinear PDF vectors. Its
        # combined span, rather than either short fragment, must confirm the wall.
        fragmented = vector_geometry()
        wall_line = fragmented["geometry_key_points"]["pages"][0]["line_candidates"][0]
        wall_line["end_px"] = [400, 300]
        wall_line["length_px"] = 300
        fragmented["geometry_key_points"]["pages"][0]["line_candidates"].append(
            vector_line("P5-VLINE-WALL-2", [400, 300], [700, 300], 0.5)
        )
        fragmented_vision = vision_response()
        fragmented_vision["result"]["layered_geometry"]["pages"][0]["outer_boundary_walls"][0]["source_candidate_ids"] = [
            "P5-VLINE-WALL", "P5-VLINE-WALL-2"
        ]
        fragmented_path = root / "vector_geometry_fragmented.json"
        fragmented_path.write_text(json.dumps(fragmented), encoding="utf-8")
        fragmented_vision_path = root / "vision_response_fragmented.json"
        fragmented_vision_path.write_text(json.dumps(fragmented_vision), encoding="utf-8")
        fragmented_wall = json.loads(create_geometry_confirmation(
            fragmented_vision_path, fragmented_path, root / "geometry_confirmation_fragmented.json"
        ).read_text())["pages"][0]["wall_confirmations"][0]
        check_value("fragmented wall vectors confirm as one span", fragmented_wall["status"], "vector_confirmed")
        check_value("fragmented wall records component vectors", fragmented_wall["component_vector_ids"], ["P5-VLINE-WALL", "P5-VLINE-WALL-2"])
        check_value("fragmented wall records snap metrics", "endpoint_distance_px" in fragmented_wall["snap_metrics"], True)

        partial = vision_response()
        partial["result"]["layered_geometry"]["pages"][0]["dimension_wall_links"][0]["measured_span_start_px"] = [100, 300]
        partial["result"]["layered_geometry"]["pages"][0]["dimension_wall_links"][0]["measured_span_end_px"] = [400, 300]
        partial_path = root / "vision_response_partial_span.json"
        partial_path.write_text(json.dumps(partial), encoding="utf-8")
        partial_link = json.loads(create_geometry_confirmation(
            partial_path, vector_path, root / "geometry_confirmation_partial_span.json"
        ).read_text())["pages"][0]["dimension_link_confirmations"][0]
        check_value("partial measured span cannot become CAD ready", partial_link["status"], "review_only")
        check_value("partial span records confirmation score", "span_confirmation" in partial_link, True)

        # A wall drawn as a filled band confirms against the band's centreline, and the
        # band is preferred over any stroked line lying nearby.
        banded = vector_geometry()
        page = banded["geometry_key_points"]["pages"][0]
        page["curve_candidates"] = page.get("curve_candidates", []) + [{
            "candidate_id": "P5-VCURVE-BAND",
            "points_px": [[0, 0]] * 4,
            "bbox_px": [100, 290, 700, 310],
            "wall_band": {
                "centreline_start_px": [100, 300],
                "centreline_end_px": [700, 300],
                "thickness_px": 20,
                "length_px": 600,
            },
        }]
        band_path = root / "vector_geometry_band.json"
        band_path.write_text(json.dumps(banded), encoding="utf-8")
        band_wall = json.loads(create_geometry_confirmation(
            vision_path, band_path, root / "geometry_confirmation_band.json").read_text()
        )["pages"][0]["wall_confirmations"][0]
        check_value("wall confirms against a filled wall band", band_wall["status"], "vector_confirmed")
        check_value("wall band is preferred over a nearby line", band_wall["matched_vector_candidate_id"], "P5-VCURVE-BAND")
        check_value("wall band reports thickness", band_wall["wall_thickness_px"], 20)

        # An exact scale from dpi and drawing scale must win over one back-computed
        # from a dimension link, which is biased low.
        exact = json.loads(create_geometry_confirmation(
            vision_path, vector_path, root / "geometry_confirmation_scale.json",
            page_scales={5: 5.64444}).read_text())
        cad_scale = [c for c in exact["scale_conversions"] if c["status"] == "cad_ready_candidate"][0]
        check_value("exact scale is used for cad geometry", cad_scale["source"], "drawing_scale_and_render_dpi")
        check_value("exact scale value is used", cad_scale["mm_per_px"], 5.64444)
        check_value("measured scale is kept but downgraded",
                    [c for c in exact["scale_conversions"] if c.get("source") == "measured_from_dimension_link"][0]["status"],
                    "scale_calibrated")

        raw_review = raw_candidate_review()
        owned = vision_owned_response()
        owned_validation = validate_vision(owned, raw_review)
        check_value("vision-owned polyline validates", owned_validation["issue_count"], 0)
        owned_path = root / "vision_owned_response.json"
        owned_path.write_text(json.dumps(owned), encoding="utf-8")
        owned_confirmation = json.loads(create_geometry_confirmation(
            owned_path, fragmented_path, root / "geometry_confirmation_owned.json"
        ).read_text())
        owned_wall = owned_confirmation["pages"][0]["wall_confirmations"][0]
        check_value("vision-owned wall preserves raw vector provenance",
                    owned_wall["source_candidate_ids"], ["P5-VLINE-WALL", "P5-VLINE-WALL-2"])

        image_only = vision_owned_response(source="image_proposed")
        image_only_path = root / "vision_image_only_response.json"
        image_only_path.write_text(json.dumps(image_only), encoding="utf-8")
        image_only_confirmation = json.loads(create_geometry_confirmation(
            image_only_path, fragmented_path, root / "geometry_confirmation_image_only.json"
        ).read_text())
        experimental_wall = image_only_confirmation["pages"][0]["wall_confirmations"][0]
        check_value("screenshot-only wall receives an experimental vector snap",
                    experimental_wall["status"], "experimental_vector_snap")
        check_value("experimental snap selects the best vector deterministically",
                    experimental_wall["matched_vector_candidate_id"], "P5-VLINE-WALL+P5-VLINE-WALL-2")
        check_value("experimental snap keeps ranked alternatives", len(experimental_wall["alternatives"]) > 0, True)
        check_value("experimental snap records its local corridor",
                    experimental_wall["experimental_search"]["selected_corridor_width_px"], 30)

        # The nearby thin annotation vector remains eligible as a dimension line but
        # cannot win the experimental wall snap.
        check_value("experimental snap excludes the nearby dimension line",
                    experimental_wall["matched_vector_candidate_id"] == "P5-VLINE-DIM", False)

        expanded = vector_geometry()
        expanded["geometry_key_points"]["pages"][0]["line_candidates"] = [
            vector_line("P5-VLINE-EXPANDED", [100, 355], [700, 355], 0.5),
            vector_line("P5-VLINE-DIM", [100, 250], [700, 250], 0.1),
        ]
        expanded_path = root / "vector_geometry_expanded.json"
        expanded_path.write_text(json.dumps(expanded), encoding="utf-8")
        expanded_confirmation = json.loads(create_geometry_confirmation(
            image_only_path, expanded_path, root / "geometry_confirmation_expanded.json"
        ).read_text())
        expanded_wall = expanded_confirmation["pages"][0]["wall_confirmations"][0]
        check_value("experimental snap expands its corridor when needed",
                    expanded_wall["experimental_search"]["selected_corridor_width_px"], 60)
        check_value("expanded corridor still selects the nearby structural line",
                    expanded_wall["matched_vector_candidate_id"], "P5-VLINE-EXPANDED")

        invalid = vision_owned_response()
        invalid["result"]["geometry_review"]["pages"][0]["walls"][0]["supporting_vector_ids"] = ["P6-VLINE-NOT-ON-PAGE"]
        invalid_validation = validate_vision(invalid, raw_review)
        check_value("cross-page raw vector reference fails validation", invalid_validation["issue_count"] > 0, True)


def vision_response():
    return {
        "provider": "chatgpt_manual",
        "model": "manual_vision_review",
        "result": {
            "layered_geometry": {
                "pages": [
                    {
                        "page": 5,
                        "image": "screenshots/page_005_floor_plan.png",
                        "page_role": "main_geometry_and_dimension_plan",
                        "plan_viewport_bbox_px": [0, 0, 800, 600],
                        "outer_boundary_walls": [
                            {
                                "wall_id": "P5-OUTER-TOP",
                                "classification": "outer_boundary_wall",
                                "line_start_px": [100, 300],
                                "line_end_px": [700, 300],
                                "confidence": "high",
                            }
                        ],
                        "internal_partitions": [],
                        "dimension_wall_links": [
                            {
                                "measurement_id": "P5-WD-7018",
                                "dimension_id": "P5-DIM-7018",
                                "value_mm": 7018,
                                "target_wall_id": "P5-OUTER-TOP",
                                "dimension_line_start_px": [100, 250],
                                "dimension_line_end_px": [700, 250],
                                "target_wall_start_px": [100, 300],
                                "target_wall_end_px": [700, 300],
                                "confidence": "high",
                                "site_confirm_required": False,
                            }
                        ],
                    }
                ]
            }
        },
    }


def vector_geometry():
    return {
        "geometry_key_points": {
            "pages": [
                {
                    "page": 5,
                    "image": "screenshots/page_005_floor_plan.png",
                    "plan_viewport": {"bbox_px": [0, 0, 800, 600]},
                    "line_candidates": [
                        vector_line("P5-VLINE-WALL", [100, 300], [700, 300], 0.5),
                        vector_line("P5-VLINE-DIM", [100, 250], [700, 250], 0.1),
                    ],
                    "curve_candidates": [],
                }
            ]
        }
    }


def raw_candidate_review():
    return {
        "pages": [{
            "page": 5,
            "candidate_ids": ["P5-VLINE-WALL", "P5-VLINE-WALL-2", "P5-DIM-7018"],
            "wall_candidates": [
                {"candidate_id": "P5-VLINE-WALL"},
                {"candidate_id": "P5-VLINE-WALL-2"},
            ],
            "dimension_text_candidates": [{"candidate_id": "P5-DIM-7018", "annotation_kind": "written_dimension"}],
        }]
    }


def vision_owned_response(source="vector_anchored"):
    supporting_ids = ["P5-VLINE-WALL", "P5-VLINE-WALL-2"] if source == "vector_anchored" else []
    return {
        "provider": "chatgpt_manual",
        "model": "manual_vision_review",
        "result": {
            "geometry_review": {
                "pages": [{
                    "page": 5,
                    "image": "screenshots/page_005_floor_plan.png",
                    "coordinate_system": {"image_width": 800, "image_height": 600, "units": "image_px"},
                    "plan_viewport_bbox_px": [0, 0, 800, 600],
                    "plan_viewport_confidence": "high",
                    "plan_viewport_uncertainties": [],
                    "page_role": "main_geometry_and_dimension_plan",
                    "geometry_readiness": "vision_layered",
                    "walls": [{
                        "wall_id": "P5-VWALL-001",
                        "classification": "existing_wall",
                        "geometry_role": "outer_boundary_wall",
                        "geometry_type": "polyline",
                        "points_px": [[100, 300], [400, 300], [700, 300]],
                        "supporting_vector_ids": supporting_ids,
                        "source": source,
                        "visible_evidence": ["continuous built wall line spans the dimension witnesses"],
                        "confidence": "high",
                    }],
                    "major_dimensions": [{
                        "dimension_id": "P5-DIM-7018",
                        "value_mm": 7018,
                        "text_seen": "7018",
                        "bbox_px": [360, 230, 410, 245],
                        "dimension_line_start_px": [100, 250],
                        "dimension_line_end_px": [700, 250],
                        "measured_span_start_px": [100, 250],
                        "measured_span_end_px": [700, 250],
                        "witness_lines_px": [],
                        "confidence": "high",
                        "site_confirm_required": False,
                        "visible_evidence": ["dimension line has end ticks"],
                    }],
                    "dimension_wall_links": [{
                        "measurement_id": "P5-WD-7018",
                        "dimension_id": "P5-DIM-7018",
                        "target_wall_id": "P5-VWALL-001",
                        "confidence": "high",
                        "should_use_for_calculation": False,
                        "site_confirm_required": False,
                    }],
                    "unassigned_dimensions": [],
                    "conflicts": [],
                }]
            }
        },
    }


def vector_line(candidate_id, start, end, stroke_width):
    return {
        "candidate_id": candidate_id,
        "geometry_type": "line",
        "start_px": start,
        "end_px": end,
        "length_px": ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5,
        "stroke_width": stroke_width,
        "candidate_role_hint": "possible_wall_or_dimension",
        "inside_main_plan_viewport": True,
    }


def tiny_png(path):
    from PIL import Image

    Image.new("RGB", (800, 600), "white").save(path)


if __name__ == "__main__":
    main()
