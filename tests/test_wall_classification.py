#!/usr/bin/env python3

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.wall_classification import create_wall_classification_packet, validate_wall_classification


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def review():
    return {
        "pages": [{
            "page": 5,
            "wall_candidates": [{"candidate_id": item} for item in ["P5-WALL", "P5-LEASE", "P5-DIM"]],
            "curve_candidates": [{"candidate_id": "P5-CURVE"}],
        }]
    }


def grouped_review():
    return {
        "pages": [{
            "page": 5,
            "wall_groups": [{
                "wall_group_id": "P5-WGRP-001",
                "source_candidate_ids": ["P5-WALL", "P5-WALL-FRAGMENT"],
            }],
        }]
    }


def response(decisions):
    return {"result": {"wall_classification": {"pages": [{"page": 5, "candidate_decisions": decisions}]}}}


def decision(candidate_id, classification, evidence=True):
    item = {"candidate_id": candidate_id, "classification": classification, "confidence": "medium", "legend_pages": [7]}
    if evidence:
        item["visible_evidence"] = ["Visible drawing evidence."]
    return item


def main():
    valid = validate_wall_classification(response([
        decision("P5-WALL", "existing_wall"),
        decision("P5-LEASE", "lease_line"),
        decision("P5-DIM", "dimension_line"),
    ]), review())
    check("valid classification passes", valid["issue_count"], 0)
    check("only physical wall is approved", [item["candidate_id"] for item in valid["decisions"] if item["classification"] == "existing_wall"], ["P5-WALL"])

    invented = validate_wall_classification(response([decision("P6-WALL", "existing_wall")]), review())
    check("invented candidate fails", invented["issue_count"], 1)

    missing_evidence = validate_wall_classification(response([decision("P5-WALL", "new_partition", evidence=False)]), review())
    check("physical wall needs evidence", missing_evidence["issue_count"], 1)

    curve = validate_wall_classification(response([decision("P5-CURVE", "new_partition")]), review())
    check("approved curve remains an approved candidate", curve["approved_wall_candidates_by_page"]["5"][0]["candidate_id"], "P5-CURVE")

    grouped = validate_wall_classification(response([decision("P5-WGRP-001", "existing_wall")]), grouped_review())
    check("wall group classification passes", grouped["issue_count"], 0)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "screenshots").mkdir()
        (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": []}}), encoding="utf-8")
        (root / "ai_input.json").write_text(json.dumps({"source_files": {"page_images": []}, "confirmed_pages": {}}), encoding="utf-8")
        packet = create_wall_classification_packet(root / "ai_input.json", root / "vector_geometry.json", root / "screenshots", zip_packet=False)
        check("wall classification packet created", Path(packet["prompt"]).exists(), True)
        check("wall classification prompt is focused", "Do not match dimensions" in Path(packet["prompt"]).read_text(), True)

if __name__ == "__main__":
    main()
