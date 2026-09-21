#!/usr/bin/env python3
"""Regression vectors for reviewed untagged shopfront glazing systems."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import normalize_glazing_surfaces, validate_envelope_library, validate_envelope_model
from ai.glazing_gate import WEATHER_FACADE_GLAZING_POLICY, validate_glazing_method_gate
from ai.opening_resolution import resolve_openings
from ai.site_orientation import validate_site_orientation
from ai.window_scan import normalize_sightings, resolve_clusters
from backend.window_scan_service import _validate_glazing_system_review


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print("PASS - " + label)


def citations(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed evidence"}]


def scan_register():
    pages = [{"page": 1, "drawing_number": "01.01"}, {"page": 2, "drawing_number": "02.01"}]
    sightings = normalize_sightings({"sightings": [
        {"page": 1, "bbox": [10, 10, 100, 40], "view_type": "plan", "appearance_status": "proposed_design",
         "tag": "", "level_name": "Level 1", "landmarks": ["bar", "entry door"], "geometry_clues": "curved shopfront", "facade_clue": "", "room_clue": "dining", "host_wall_clue": "wall-1", "revision": "D", "source_excerpt": "layout", "confidence": 0.9},
        {"page": 2, "bbox": [10, 10, 100, 50], "view_type": "elevation", "appearance_status": "proposed_design",
         "tag": "", "level_name": "Level 1", "landmarks": ["bar", "entry door"], "geometry_clues": "multi-panel shopfront", "facade_clue": "", "room_clue": "dining", "host_wall_clue": "wall-1", "revision": "D", "source_excerpt": "shopfront elevation", "confidence": 0.95},
    ]}, pages, "scan-source", "test", "test-model", "prompt")
    register = resolve_clusters(sightings, [{"member_sighting_ids": [row["sighting_id"] for row in sightings],
                                             "match_reason": "entry door, mullion order and bar align", "mirrored_view": False,
                                             "competing_matches": []}], "scan-source")
    register["pages_inspected"] = [1, 2]
    register["fingerprint"] = "scan-fingerprint"
    return register


def approved_gate():
    return validate_glazing_method_gate({"approval_status": "approved", "engineer_name": "Engineer", "engineer_credential": "CPEng",
                                         "approved_at": "2026-09-20", "method_citation": "GM-01", "citations": citations("GM-01"),
                                         "policy": WEATHER_FACADE_GLAZING_POLICY})


def main():
    register = scan_register()
    opening = register["openings"][0]
    review = {"review_type": "glazing_system", "system_name": "Main shopfront glazing", "owner_room_id": "room-1",
              "owner_zone_id": "zone-1", "host_wall_id": "wall-1", "level_name": "Level 1", "width_m": 6.0,
              "height_m": 2.94, "quantity": 1, "external_exposure": "external", "includes_glazed_doors": True,
              "member_sighting_ids": opening["sighting_ids"], "glass_area_basis": "reviewed_frame_fraction",
              "opening_coverage": {"status": "complete", "source": "Plan + elevation", "citations": citations("01.01/02.01")},
              "source": "Plan + elevation", "citations": citations("01.01/02.01"), "review_mode": "engineering_reviewed",
              "scan_fingerprint": "scan-fingerprint"}
    _validate_glazing_system_review(review, opening)
    resolved = resolve_openings({}, "evidence-source", {1: {}, 2: {}}, register,
                                {"source_fingerprint": "scan-source", "fingerprint": "review-fingerprint", "openings": {opening["opening_id"]: review}})
    row = resolved["openings"][0]
    check("reviewed shopfront system preserves ownership, geometry and coverage", row["status"] == "reviewed" and row["system_name"] == "Main shopfront glazing" and row["opening_coverage"]["status"] == "complete")
    check("reviewed system does not invent a façade direction", "façade orientation remains unresolved" in row["unresolved_fields"])
    broken = deepcopy(review)
    broken["external_exposure"] = "unresolved"
    try:
        _validate_glazing_system_review(broken, opening)
    except ValueError:
        pass
    else:
        raise AssertionError("unresolved exposure must be rejected")

    library = validate_envelope_library({"windows": [{"record_id": "shopfront-window", "title": "Shopfront system",
        "revision": 1, "review_status": "confirmed", "source": "Supplier data sheet", "citations": citations("DS-01"),
        "property_source_type": "supplier_datasheet", "property_applicability": "Main shopfront system", "u_value_w_m2k": 2.8,
        "u_value_basis": "overall_window", "shgc": 0.52, "frame_fraction": 0.12, "glass_area_correction": 1.0,
        "internal_shading_factor": 1.0}], "constructions": [{"record_id": "wall-construction", "title": "Wall", "revision": 1,
        "review_status": "confirmed", "source": "Wall detail", "citations": citations("01.02"), "kind": "opaque_wall", "u_value_w_m2k": 0.5}]})
    invalid_property_source = {"record_id": "uncited-window", "title": "Uncited", "revision": 1, "review_status": "confirmed",
                               "source": "Supplier document", "citations": citations("DS-02"), "property_source_type": "",
                               "property_applicability": "Shopfront", "u_value_w_m2k": 2.8, "u_value_basis": "overall_window", "shgc": 0.5}
    try:
        validate_envelope_library({"windows": [invalid_property_source]})
    except ValueError:
        pass
    else:
        raise AssertionError("a new confirmed window must name its property source type")
    print("PASS - new confirmed window requires property source type")
    model = validate_envelope_model({"active_for_calculation": True, "surfaces": [
        {"surface_id": "wall-1", "owner_zone_id": "zone-1", "owner_room_id": "", "kind": "opaque_wall", "orientation": "N", "area_m2": 24,
         "area_basis": "gross_with_confirmed_openings", "linked_opening_surface_ids": ["shopfront-surface"], "opening_coverage_status": "confirmed",
         "construction_id": "wall-construction", "boundary_method": "external", "review_status": "confirmed", "source": "Plan", "citations": citations("01.01"), "manual_solar": {"enabled": False}},
        {"surface_id": "shopfront-surface", "owner_zone_id": "zone-1", "owner_room_id": "room-1", "kind": "glazing", "orientation": "N",
         "opening_mapping_status": "confirmed", "geometry_mode": "engineering_reviewed", "opening_evidence_id": row["opening_id"], "host_surface_id": "wall-1", "host_wall_evidence_id": "wall-1",
         "window_id": "shopfront-window", "opening_width_m": 6.0, "opening_height_m": 2.94, "opening_quantity": 1,
         "boundary_method": "external", "review_status": "confirmed", "source": "Plan/elevation", "citations": citations("01.01/02.01"),
         "solar_basis": "weather_facade", "solar_radiation_source_id": "weather", "solar_shading_mode": "manual", "direct_shading_factor": 1.0,
         "diffuse_shading_factor": 1.0, "diffuse_shading_source": "No obstruction confirmed", "diffuse_shading_citations": citations("02.01"),
         "manual_solar": {"enabled": False}}
    ]}, library)
    orientation = validate_site_orientation({"status": "reviewed", "tenancy_id": "casa-nova",
        "alignment": {"kind": "north_arrow", "plan_up_azimuth_deg": 0, "source": "Cited plan", "citations": citations("site plan")},
        "facades": [{"host_surface_id": "wall-1", "opening_ids": [row["opening_id"]], "tenancy_id": "casa-nova", "line_px": [[0, 0], [10, 0]], "outward_side": "left",
                      "exposure": "external", "mapping_landmark_ids": ["entry", "column"], "source": "Plan + map", "citations": citations("map"), "review_status": "confirmed"}]})
    model["surfaces"][1]["azimuth_deg"] = orientation["facades"][0]["azimuth_deg"]
    model["surfaces"][1]["orientation_source_fingerprint"] = orientation["fingerprint"]
    included, blocked, _stored = normalize_glazing_surfaces(library, model, approved_gate(), opening_register=resolved, site_orientation=orientation)
    check("reviewed external shopfront system becomes glazing-eligible once orientation is confirmed", len(included) == 1 and not blocked)
    incomplete = deepcopy(resolved)
    incomplete["openings"][0]["opening_coverage"]["status"] = "incomplete"
    _included, blocked, _stored = normalize_glazing_surfaces(library, model, approved_gate(), opening_register=incomplete, site_orientation=orientation)
    check("incomplete system coverage blocks gross host-wall subtraction", bool(blocked) and "opening coverage" in blocked[0]["reason"])


if __name__ == "__main__":
    main()
