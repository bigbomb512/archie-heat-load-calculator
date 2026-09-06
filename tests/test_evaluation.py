#!/usr/bin/env python3

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.evaluation import evaluate_packet, render_markdown


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def expected_case():
    return {
        "case_id": "synthetic_vector_plan",
        "expectations": {
            "pages": {"geometry": [1], "rcp_context": [2], "excluded": [3]},
            "floors": [{"label": "Ground Floor", "source_pages": [1, 2]}],
            "major_dimensions": [{"page": 1, "value_mm": 7018}],
            "minimum_physical_walls": [{"page": 1, "minimum": 2}],
            "forbidden_wall_regions": [{"page": 1, "label": "title block", "bbox_px": [0, 900, 300, 1200]}],
            "curves": [{"page": 1, "minimum": 1}],
            "dimension_links": [{"page": 1, "value_mm": 7018}],
            "confirmation": {"minimum_status_counts": {"cad_ready_candidate": 1}},
        },
    }


def ai_input():
    return {
        "confirmed_pages": {
            "floor_plans": [{"page": 1}],
            "reflected_ceiling_plans": [{"page": 2}],
            "existing_hvac_or_services_plans": [],
            "reference_pages": [],
        },
        "building_model": {"floors": [{"label": "Ground Floor", "source_pages": [1, 2]}]},
    }


def matcher(include_dimension=True):
    dimensions = [{"value_mm": 7018}] if include_dimension else []
    return {"pages": [{"page": 1, "summary": {"major_boundary_dimensions": dimensions}, "dimension_wall_matches": []}]}


def vector(include_title_block_wall=False):
    walls = [{"candidate_id": "V-WALL", "start_px": [50, 500], "end_px": [700, 500]}]
    if include_title_block_wall:
        walls.append({"candidate_id": "V-TITLE", "start_px": [20, 1000], "end_px": [200, 1000]})
    return {"geometry_key_points": {"pages": [{"page": 1, "wall_candidates": walls}]}}


def vision(include_title_block_wall=False):
    outer = [{"wall_id": "W-OUTER", "line_start_px": [50, 500], "line_end_px": [700, 500]}]
    internal = [{"wall_id": "W-CURVE", "geometry_type": "curve_polyline", "points_px": [[700, 500], [760, 560], [780, 650]], "line_start_px": [700, 500], "line_end_px": [780, 650]}]
    if include_title_block_wall:
        outer.append({"wall_id": "W-TITLE", "line_start_px": [20, 1000], "line_end_px": [200, 1000]})
    return {
        "result": {
            "layered_geometry": {
                "pages": [{
                    "page": 1,
                    "outer_boundary_walls": outer,
                    "internal_partitions": internal,
                    "dimension_wall_links": [{"value_mm": 7018, "target_wall_id": "W-OUTER"}],
                }]
            }
        }
    }


def confirmation():
    return {"pages": [{"page": 1, "dimension_link_confirmations": [{"status": "cad_ready_candidate"}]}]}


def status_for(report, name):
    return next(item["status"] for item in report["results"] if item["name"] == name)


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        case_path = root / "case.json"
        write_json(case_path, expected_case())
        write_json(root / "ai_input.json", ai_input())
        write_json(root / "dimension_wall_matches.json", matcher())
        write_json(root / "vector_geometry.json", vector())
        write_json(root / "vision_response.json", vision())
        write_json(root / "geometry_confirmation.json", confirmation())

        report = evaluate_packet(case_path, root)
        check("correct geometry pages pass", status_for(report, "geometry pages: [1]"), "passed")
        check("correct dimensions pass", status_for(report, "major dimension 7018 on page 1"), "passed")
        check("curve stays polyline", status_for(report, "curve polyline on page 1"), "passed")
        check("physical wall dimension link passes", status_for(report, "dimension link 7018 on page 1"), "passed")
        check("cad ready confirmation passes", status_for(report, "minimum cad_ready_candidate links"), "passed")
        check("report markdown is readable", "# Evaluation: synthetic_vector_plan" in render_markdown(report), True)

        bad_pages = expected_case()
        bad_pages["expectations"]["pages"]["geometry"] = [4]
        write_json(case_path, bad_pages)
        wrong_page = evaluate_packet(case_path, root)
        check("wrong geometry page fails", status_for(wrong_page, "geometry pages: [4]"), "failed")

        write_json(case_path, expected_case())
        write_json(root / "dimension_wall_matches.json", matcher(include_dimension=False))
        missing_dimension = evaluate_packet(case_path, root)
        check("missing major dimension fails", status_for(missing_dimension, "major dimension 7018 on page 1"), "failed")

        write_json(root / "dimension_wall_matches.json", matcher())
        write_json(root / "vector_geometry.json", vector(include_title_block_wall=True))
        vector_title_block = evaluate_packet(case_path, root)
        check("title block vector candidate fails", status_for(vector_title_block, "forbidden vector region: title block"), "failed")

        write_json(root / "vector_geometry.json", vector())
        write_json(root / "vision_response.json", vision(include_title_block_wall=True))
        title_block = evaluate_packet(case_path, root)
        check("title block wall fails vision check", status_for(title_block, "forbidden classified-wall region: title block"), "failed")

        crossing = vision()
        crossing["result"]["layered_geometry"]["pages"][0]["outer_boundary_walls"].append({"wall_id": "W-CROSSING", "line_start_px": [-50, 1000], "line_end_px": [400, 1000]})
        write_json(root / "vision_response.json", crossing)
        crossing_region = evaluate_packet(case_path, root)
        check("crossing title block wall fails vision check", status_for(crossing_region, "forbidden classified-wall region: title block"), "failed")

        invalid_link = vision()
        invalid_link["result"]["layered_geometry"]["pages"][0]["dimension_wall_links"][0]["target_wall_id"] = "W-FIXTURE"
        write_json(root / "vision_response.json", invalid_link)
        invalid = evaluate_packet(case_path, root)
        check("dimension linked to non-wall fails", status_for(invalid, "dimension link 7018 on page 1"), "failed")

        (root / "vision_response.json").unlink()
        (root / "geometry_confirmation.json").unlink()
        incomplete = evaluate_packet(case_path, root)
        check("missing vision response is not evaluated", status_for(incomplete, "wall and dimension expectations"), "not_evaluated")
        check("missing confirmation is not evaluated", status_for(incomplete, "geometry confirmation expectations"), "not_evaluated")


if __name__ == "__main__":
    main()
