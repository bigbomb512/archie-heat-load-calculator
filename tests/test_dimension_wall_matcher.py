#!/usr/bin/env python3

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.dimension_wall_matcher import create_dimension_wall_matches, match_page_dimensions


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def line(candidate_id, start, end, width=0.15):
    return {"candidate_id": candidate_id, "geometry_type": "line", "start_px": start, "end_px": end,
            "length_px": ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5,
            "stroke_width": width, "candidate_role_hint": "possible_wall_or_dimension"}


def dimension(candidate_id, value, bbox, **extra):
    item = {"candidate_id": candidate_id, "text_seen": str(value), "value_mm": value, "bbox_px": bbox}
    item.update(extra)
    return item


def page_fixture():
    return {
        "page": 1, "image": "screenshots/page_001_floor_plan.png", "plan_role": "main_floor_plan",
        "coordinate_systems": {"image_px": {"image_width": 900, "image_height": 600}},
        "line_candidates": [
            line("P1-VLINE-WALL", [100, 300], [800, 300], 0.5),
            line("P1-VLINE-DIM", [100, 160], [800, 160]),
            line("P1-VLINE-WIT-A", [100, 160], [100, 300]),
            line("P1-VLINE-WIT-B", [800, 160], [800, 300]),
        ],
        "curve_candidates": [],
        "dimension_candidates": [
            dimension("P1-VDIMTXT-OVERALL", 7018, [420, 125, 470, 155], text_seen="7018 C.O.S.", context="overall C.O.S."),
            dimension("P1-VDIMTXT-FIXTURE", 800, [620, 125, 650, 155]),
        ],
    }


def main():
    result = match_page_dimensions(page_fixture())
    spans = result["dimension_span_candidates"]
    values = {item["value_mm"] for item in spans}
    check("major dimension span is retained", 7018 in values, True)
    check("local dimension span is retained", 800 in values, True)
    check("code creates no wall groups", "wall_groups" in result, False)
    check("code creates no rule links", result["dimension_wall_matches"], [])
    overall = next(item for item in spans if item["value_mm"] == 7018)
    check("dimension span has witness evidence", len(overall["witness_line_candidate_ids"]) >= 2, True)
    check("C.O.S. stays marked", overall["site_confirm_required"], True)

    callout = page_fixture()
    callout["dimension_candidates"] = [
        dimension("P1-CALLOUT", 3204, [420, 125, 470, 155], annotation_kind="detail_or_sheet_reference", dimension_eligibility="ineligible"),
        dimension("P1-REAL", 7018, [420, 125, 470, 155], context="overall"),
    ]
    result = match_page_dimensions(callout)
    check("detail callout is excluded", {item["value_mm"] for item in result["dimension_span_candidates"]}, {7018})

    unknown = page_fixture()
    unknown["dimension_candidates"] = [dimension("P1-UNKNOWN", 3851, [420, 125, 470, 155], annotation_kind="unknown_numeric", dimension_eligibility="vision_review")]
    result = match_page_dimensions(unknown)
    check("unknown numeric has no automatic span", result["dimension_span_candidates"], [])
    check("unknown numeric remains visible", result["summary"]["unknown_numeric_annotations"][0]["text_seen"], "3851")

    with TemporaryDirectory() as folder:
        root = Path(folder)
        vector_path = root / "vector_geometry.json"
        vector_path.write_text(json.dumps({"geometry_key_points": {"pages": [page_fixture()]}}), encoding="utf-8")
        output = create_dimension_wall_matches(vector_path, root / "dimension_wall_matches.json")
        data = json.loads(output.read_text())
        check("matcher writes span evidence", data["pages"][0]["dimension_span_candidates"][0]["page"], 1)
        check("matcher states vision owns links", "vision review creates wall links" in data["note"], True)


if __name__ == "__main__":
    main()
