#!/usr/bin/env python3
"""Offline all-page scan and site-orientation regression cases."""

from copy import deepcopy
import json
from io import BytesIO
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.opening_resolution import resolve_openings
from ai.site_orientation import outward_azimuth, validate_site_orientation
from ai.window_scan import normalize_sightings, page_batches, resolve_clusters
from backend import site_orientation_service, window_scan_service


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

    original_urlopen = site_orientation_service.urllib.request.urlopen
    imagery_request_urls = []
    def fake_urlopen(request, timeout):
        imagery_request_urls.append(request.full_url)
        return BytesIO(json.dumps({"features": [{"attributes": {
            "OBJECTID": 5, "BlockName": "test imagery", "BlockType": "Aerial",
            "BlockStartDate": 1704067200000, "Resolution_cm": 10}}]}).encode())
    try:
        site_orientation_service.urllib.request.urlopen = fake_urlopen
        imagery = site_orientation_service.nsw_imagery_candidates(151.2, -33.8)
    finally:
        site_orientation_service.urllib.request.urlopen = original_urlopen
    check("dated imagery lookup uses NSW point service and retains candidate date",
          "/NSW_Imagery_Dates/MapServer/0/query?" in imagery_request_urls[0]
          and imagery["candidates"][0]["imagery_date"] == "2024-01-01"
          and imagery["status"] == "date_candidate_only_not_tenancy_or_facade_evidence")

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        ai_input = {"source_pdf": "synthetic-test-only", "drawing_set": {"pages": pages[:9]}}
        (root / "ai_input.json").write_text(json.dumps(ai_input))
        (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True, "max_budget_aud": 10}))
        project = {"id": "test", "review_dir": str(root)}
        web = type("Web", (), {"project_by_id": staticmethod(lambda _: project), "safe_link": staticmethod(lambda path: "/safe/" + Path(path).name)})()
        old_render = window_scan_service._page_image
        old_factory = window_scan_service.PROVIDER_FACTORY
        old_rate = os.environ.get("ARCHIE_WINDOW_SCAN_COST_PER_BATCH_AUD")
        calls = []
        class FakeProvider:
            def scan(self, batch):
                calls.append(batch["batch_id"])
                if batch["batch_id"] == "windows-002" and calls.count("windows-002") == 1:
                    raise RuntimeError("transient test failure")
                return {"sightings": []}
            def match(self, sightings): return {"clusters": []}
        def render(_ai_input, _page, target):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"test image")
            return target
        try:
            window_scan_service._page_image = render
            window_scan_service.PROVIDER_FACTORY = lambda model: FakeProvider()
            os.environ["ARCHIE_WINDOW_SCAN_COST_PER_BATCH_AUD"] = "1"
            first_job = window_scan_service.post(web, project, {"action": "start", "confirm_all_pages": True})
            for _ in range(100):
                if window_scan_service.get(web, project)["job"]["status"] == "failed": break
                time.sleep(0.01)
            check("failed batch preserves immutable manifest", window_scan_service.get(web, project)["job"]["status"] == "failed")
            manifest_id = first_job["job"]["run_id"]
            saved_manifest = json.loads((root / "window_scan_runs" / manifest_id / "manifest.json").read_text())
            check("retry manifest pins exact scan and matching prompts",
                  saved_manifest["scan_prompt"] == window_scan_service.SCAN_PROMPT
                  and saved_manifest["match_prompt"] == window_scan_service.MATCH_PROMPT)
            window_scan_service.post(web, project, {"action": "retry", "confirm_all_pages": True})
            for _ in range(100):
                if window_scan_service.get(web, project)["job"]["status"] == "completed": break
                time.sleep(0.01)
            finished = window_scan_service.get(web, project)
            check("retry reuses successful first batch", calls.count("windows-001") == 1 and calls.count("windows-002") == 2)
            check("retry uses same manifest and covers all pages", finished["job"]["run_id"] == manifest_id and finished["register"]["pages_inspected"] == list(range(1, 10)))
        finally:
            window_scan_service._page_image = old_render
            window_scan_service.PROVIDER_FACTORY = old_factory
            if old_rate is None: os.environ.pop("ARCHIE_WINDOW_SCAN_COST_PER_BATCH_AUD", None)
            else: os.environ["ARCHIE_WINDOW_SCAN_COST_PER_BATCH_AUD"] = old_rate


if __name__ == "__main__": main()
