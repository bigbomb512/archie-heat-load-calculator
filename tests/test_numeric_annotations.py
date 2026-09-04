#!/usr/bin/env python3

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_pipeline.numeric_annotations import classify_numeric_annotation
from ai.vision_validator import layered_dimension_issues


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected}, got {actual}")
    print(f"PASS - {name}")


def main():
    words = [
        {"text": "1", "bbox_px": [10, 12, 16, 24]},
        {"text": "3204", "bbox_px": [18, 12, 48, 24]},
        {"text": "DETAIL", "bbox_px": [12, 30, 54, 42]},
    ]
    callout = classify_numeric_annotation("3204", [18, 12, 48, 24], words, [[6, 4, 56, 46]])
    check("callout kind", callout["annotation_kind"], "detail_or_sheet_reference")
    check("callout is ineligible", callout["dimension_eligibility"], "ineligible")

    genuine = classify_numeric_annotation("3851", [100, 100, 135, 115], [{"text": "3851", "bbox_px": [100, 100, 135, 115]}])
    check("unproven number stays unknown", genuine["annotation_kind"], "unknown_numeric")
    check("unknown stays for vision", genuine["dimension_eligibility"], "vision_review")

    blocked = layered_dimension_issues(
        14,
        {"dimension_id": "P14-VDIMTXT-001", "source_annotation_id": "P14-VDIMTXT-001", "value_mm": 3204, "text_seen": "3204", "confidence": "high"},
        {"P14-VDIMTXT-001": "detail_or_sheet_reference"},
    )
    check("validator blocks callout as a dimension", blocked[0]["field"], "layered_geometry.source_annotation_id")


if __name__ == "__main__":
    main()
