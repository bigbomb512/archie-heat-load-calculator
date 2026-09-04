#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.vector_geometry import candidate_inside_viewport_score, detect_main_plan_viewport, geometry_page_refs, score_curve, score_line, wall_band


def check_value(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def main():
    vector_page_input = {
        "design_inputs": {
            "geometry_evidence_pages": [
                {"page": 5, "title": "General Arrangement Plan", "plan_role": "main_floor_plan"},
                {"page": 13, "title": "Door Batten Details", "plan_role": "enlarged_plan"},
            ],
            "dimension_evidence_pages": [
                {"page": 18, "title": "Dimension Plan", "plan_role": "main_floor_plan"},
                {"page": 7, "title": "Reflected Ceiling Plan", "plan_role": "reflected_ceiling_plan"},
            ],
        },
        "confirmed_pages": {"floor_plans": []},
    }
    check_value("vector extraction skips details and rcp", [page["page"] for page in geometry_page_refs(vector_page_input)], [5, 18])

    strong_line = score_line(220, "horizontal", 0.5)
    weak_line = score_line(22, "horizontal", 0.05)
    check_value("strong vector line is wall-like", strong_line["role"], "possible_wall_or_dimension")
    check_value("weak vector line remains context", weak_line["role"], "vector_context")

    strong_curve = score_curve([[0, 0], [60, 20], [120, 80], [220, 120]], [0, 0, 220, 120], 0.5)
    check_value("large vector curve is wall-like", strong_curve["role"], "possible_curved_wall_or_symbol")

    raw_lines = [
        {"candidate_id": "P5-VLINE-0001", "start_px": [0, 0], "end_px": [200, 0], "candidate_role_hint": "possible_wall_or_dimension"},
        {"candidate_id": "P5-VLINE-0002", "start_px": [0, 0], "end_px": [15, 0], "candidate_role_hint": "likely_noise"},
    ]
    raw_curve = {"candidate_id": "P5-VCURVE-0001", "points_plan_px": [[200, 0], [240, 20], [260, 60], [260, 100]]}
    check_value("raw long vector remains neutral evidence", raw_lines[0]["candidate_role_hint"], "possible_wall_or_dimension")
    check_value("raw short vector remains visible noise evidence", raw_lines[1]["candidate_role_hint"], "likely_noise")
    check_value("curved raw vector keeps multiple points", len(raw_curve["points_plan_px"]), 4)

    # Walls drawn as filled bands never reach line_candidates, so the closed polygon
    # has to be recognised and reduced to its centreline.
    closed = [[0, 0]] * 4
    horizontal = wall_band(closed, [463.88, 591.2, 1894.37, 612.35])
    check_value("wall band centreline is the middle of the band", horizontal["centreline_start_px"], [463.88, 601.78])
    check_value("wall band centreline runs the long axis", horizontal["centreline_end_px"], [1894.37, 601.78])
    check_value("wall band records thickness", horizontal["thickness_px"], 21.15)
    vertical = wall_band(closed, [442.57, 612.35, 463.88, 1259.15])
    check_value("vertical wall band is detected", vertical["centreline_start_px"], [453.23, 612.35])
    check_value("short polygon is not a wall band", wall_band(closed, [100, 100, 180, 118]), None)
    check_value("squat polygon is not a wall band", wall_band(closed, [0, 0, 400, 200]), None)
    check_value("open polyline is not a wall band", wall_band([[0, 0]] * 3, [463.88, 591.2, 1894.37, 612.35]), None)
    check_value("raw vectors are not pre-labelled as walls", "wall_id" in raw_lines[0], False)

    image_size = [1000, 800]
    lines = [
        {"start_px": [20, 20], "end_px": [980, 20], "length_px": 960},
        {"start_px": [30, 120], "end_px": [180, 120], "length_px": 150},
        {"start_px": [780, 80], "end_px": [940, 80], "length_px": 160},
        {"start_px": [420, 210], "end_px": [760, 210], "length_px": 340},
        {"start_px": [420, 210], "end_px": [420, 540], "length_px": 330},
        {"start_px": [420, 540], "end_px": [760, 540], "length_px": 340},
        {"start_px": [760, 210], "end_px": [760, 540], "length_px": 330},
        {"start_px": [500, 300], "end_px": [690, 300], "length_px": 190},
    ]
    dimensions = [
        {"bbox_px": [560, 170, 610, 190]},
        {"bbox_px": [790, 350, 835, 370]},
    ]
    viewport = detect_main_plan_viewport(lines, [], [], dimensions, image_size)
    bbox = viewport["bbox_px"]
    check_value("viewport detection avoids full page", bbox != [0, 0, 1000, 800], True)
    check_value("viewport detection keeps plan cluster", bbox[0] <= 420 and bbox[2] >= 760, True)
    check_value("viewport excludes left logo/notes furniture", bbox[0] > 180, True)
    check_value("viewport excludes right approval furniture", bbox[2] < 940, True)
    check_value("viewport detection confidence", viewport["confidence"] in {"medium", "high"}, True)

    inside_score = candidate_inside_viewport_score({"start_px": [430, 220], "end_px": [750, 220], "geometry_type": "line"}, viewport, image_size)
    outside_score = candidate_inside_viewport_score({"start_px": [10, 20], "end_px": [990, 20], "geometry_type": "line"}, viewport, image_size)
    check_value("inside viewport boosts candidate", inside_score["score_delta"] > 0, True)
    check_value("outside viewport penalizes candidate", outside_score["score_delta"] < 0, True)


if __name__ == "__main__":
    main()
