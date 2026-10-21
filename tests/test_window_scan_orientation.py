#!/usr/bin/env python3
"""Offline opening-sighting and site-orientation regression cases."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.opening_resolution import resolve_openings
from ai.site_orientation import outward_azimuth, validate_site_orientation
from ai.window_scan import normalize_sightings, page_batches, resolve_clusters


def check(label, condition):
    if not condition: raise AssertionError(label)
    print("PASS - " + label)


def sighting(page, tag="W1", level="L1", view="plan", appearance="proposed_design", x=0):
    return {"page": page, "bbox": [x, 0, x + 10, 10], "view_type": view,
            "appearance_status": appearance, "tag": tag, "level_name": level,
            "landmarks": ["column A", "door D1"], "geometry_clues": "two mullions", "confidence": 0.8}


def main():
    pages = [{"page": page, "drawing_number": str(page), "plan_role": "reference_only" if page % 2 else "3d_cross_check"}
             for page in range(1, 39)]
    batches = page_batches({"drawing_set": {"pages": list(reversed(pages))}})
    check("every one of 38 pages is scanned despite ranking", [p["page"] for batch in batches for p in batch["pages"]] == list(range(1, 39)))
    check("all batches have at most eight pages", len(batches) == 5 and max(len(batch["pages"]) for batch in batches) == 8)
    first = normalize_sightings({"sightings": [sighting(1), sighting(2, view="elevation")]}, pages[:2], "source", "fake", "m", "prompt")
    reordered = normalize_sightings({"sightings": [sighting(2, view="elevation"), sighting(1)]}, pages[:2], "source", "fake", "m", "prompt")
    check("sighting IDs ignore response order", [row["sighting_id"] for row in first] == [row["sighting_id"] for row in reordered])
    try: normalize_sightings({"sightings": [sighting(3)]}, pages[:2], "source", "fake", "m", "prompt")
    except ValueError: pass
    else: raise AssertionError("unknown batch page must be rejected")
    matched = resolve_clusters(first, [{"member_sighting_ids": [row["sighting_id"] for row in first],
                                        "match_reason": "tag, mullions, column and adjacent door", "competing_matches": []}], "source")
    check("plan and elevation form one proposed cluster", len(matched["openings"]) == 1 and matched["openings"][0]["status"] == "proposed")
    check("cluster cannot activate glazing directly", not matched["openings"][0]["owner_room_id"] and not matched["openings"][0].get("width_m"))
    unmatched = resolve_clusters(first, [], "source")
    check("unmatched elevations remain in register", len(unmatched["openings"]) == 2 and all(row["unresolved_fields"][0] == "unmatched sighting" for row in unmatched["openings"]))
    conflict_sightings = normalize_sightings({"sightings": [sighting(1), sighting(2, level="L2", view="render", appearance="existing_condition")]}, pages[:2], "source", "fake", "m", "prompt")
    conflict = resolve_clusters(conflict_sightings, [{"member_sighting_ids": [row["sighting_id"] for row in conflict_sightings], "match_reason": "similar"}], "source")
    check("level and proposed/existing image conflicts stay visible", conflict["openings"][0]["status"] == "conflict" and len(conflict["openings"][0]["unresolved_fields"]) >= 2)
    check("north-up top-facing line resolves north", outward_azimuth([[0, 0], [10, 0]], "left", 0) == 0)
    check("rotated plan preserves true-north bearing", outward_azimuth([[0, 0], [10, 0]], "left", 90) == 90)
    check("opposite outward side reverses façade", outward_azimuth([[0, 0], [10, 0]], "right", 0) == 180)
    check("angled façade retains degrees", outward_azimuth([[0, 0], [10, 10]], "left", 0) == 45)
    orientation = validate_site_orientation({"status": "reviewed", "site_address": "1 Sample St", "tenancy_id": "shop-1",
        "alignment": {"kind": "north_arrow", "plan_up_azimuth_deg": 0, "source": "A-001", "citations": ["A-001 north arrow"]},
        "facades": [{"host_surface_id": "wall-1", "opening_ids": ["opening-1"], "tenancy_id": "shop-1",
                     "line_px": [[0, 0], [10, 0]], "outward_side": "left", "exposure": "external",
                     "mapping_landmark_ids": ["door-1", "column-A"], "source": "survey", "citations": ["survey p1"], "review_status": "confirmed"}]})
    check("cited tenancy alignment yields reviewed azimuth", orientation["facades"][0]["status"] == "reviewed" and orientation["facades"][0]["azimuth_deg"] == 0)
    internal = deepcopy(orientation)
    internal["facades"][0]["exposure"] = "internal"
    check("internal window remains classified internal", validate_site_orientation(internal)["facades"][0]["exposure"] == "internal")
    wrong_tenancy = deepcopy(orientation)
    wrong_tenancy["tenancy_id"] = "shop-2"
    check("whole-building site cannot identify another tenancy", validate_site_orientation(wrong_tenancy)["facades"][0]["status"] != "reviewed")
    scan_register = {**matched, "pages_inspected": [1, 2]}
    merged = resolve_openings({}, "canonical-source", {1: {}, 2: {}}, scan_register)
    check("all-page sightings feed existing opening register as proposed", len(merged["openings"]) == 1 and merged["openings"][0]["status"] == "proposed")
    reviewed_scan = resolve_openings({}, "canonical-source", {1: {}, 2: {}}, scan_register, {
        "source_fingerprint": "source", "fingerprint": "review-1", "openings": {matched["openings"][0]["opening_id"]: {
            "scan_fingerprint": scan_register["fingerprint"], "review_mode": "engineering_reviewed",
            "owner_room_id": "room-1", "owner_zone_id": "zone-1", "host_wall_id": "wall-1",
            "level_name": "L1", "facade": "", "width_m": 2, "height_m": 1, "quantity": 1,
            "source": "Cited plan and elevation", "citations": [{"reference": "A1 and A2"}]}}})
    check("matched window retains confirmed ownership without guessing direction",
          reviewed_scan["openings"][0]["owner_room_id"] == "room-1"
          and reviewed_scan["openings"][0]["status"] == "ai_estimated"
          and "façade orientation remains unresolved" in reviewed_scan["openings"][0]["unresolved_fields"])


if __name__ == "__main__": main()
