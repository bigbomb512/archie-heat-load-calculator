#!/usr/bin/env python3
"""Project-local site-location service lifecycle checks without live network use."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import site_location_service


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


class LocalWeb:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def main():
    check("station distance uses resolved coordinates", site_location_service._distance_km(-33.86, 151.2, {"latitude_deg": -33.86, "longitude_deg": 151.2}) == 0.0)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        project, web = {"id": "location-test", "review_dir": str(root)}, LocalWeb()
        write(root / "ai_input.json", {"drawing_set": {"pages": [{"page": 1, "drawing_number": "A-001", "title": "Project", "structured_content": {"markdown": "SITE: 15 Queen Street, Sydney NSW 2000"}}]}})
        inferred = site_location_service.post(web, project, {"action": "infer_from_pdf"})
        check("inference writes separate project-local artifact", (root / "site_location_resolution.json").exists() and inferred["status"] == "awaiting_address_confirmation")
        confirmed = site_location_service.post(web, project, {"action": "confirm_address", "confirmed_address": "15 Queen Street, Sydney NSW 2000", "confirm_address": True})
        check("confirmation stores consent before lookup", confirmed["site_location_resolution"]["external_lookup_consent"])

        originals = site_location_service.gnaf_candidates, site_location_service.geoscience_elevation, site_location_service._weather_candidates
        try:
            site_location_service.gnaf_candidates = lambda _address: ([{"candidate_id": "one", "formatted_address": "15 Queen Street, Sydney NSW 2000", "latitude_deg": -33.86, "longitude_deg": 151.2, "state": "NSW", "locality": "Sydney", "source_url": "https://api.geoscape.com.au/gnaf", "content_hash": "gnaf"}], "https://api.geoscape.com.au/gnaf", "response")
            site_location_service.geoscience_elevation = lambda _lon, _lat: ({"elevation_m": 18, "source_url": "https://data.ga.gov.au/elevation", "content_hash": "elevation"}, "https://data.ga.gov.au/elevation")
            site_location_service._weather_candidates = lambda _root, _location: [{"source_id": "bom-one", "target": "scenario.weather_profile", "value": {"hours": [{"db": 30, "wb": 20}] * 24, "pressure_kpa": 101.2}, "unit": "profile", "scope": {}, "publisher": "Bureau of Meteorology", "citation": "Fixture", "source_reference": "https://www.bom.gov.au/fixture", "content_hash": "bom"}]
            resolved = site_location_service.post(web, project, {"action": "resolve_location"})
        finally:
            site_location_service.gnaf_candidates, site_location_service.geoscience_elevation, site_location_service._weather_candidates = originals
        body = resolved["site_location_resolution"]
        check("single G-NAF match resolves automatically", body["location"]["timezone"] == "Australia/Sydney" and body["geocode"]["selected_candidate_id"] == "one")
        check("service response exposes only opaque artifact link", resolved["artifact_url"] == "/artifact/site_location_resolution.json" and str(root) not in json.dumps(resolved))
        selected = site_location_service.post(web, project, {"action": "select_weather_source", "source_id": "bom-one"})
        check("weather selection remains separate and draft-scoped", selected["site_location_resolution"]["selected_weather_source"]["source_id"] == "bom-one")

    with TemporaryDirectory() as directory:
        root = Path(directory)
        project, web = {"id": "cited-location-test", "review_dir": str(root)}, LocalWeb()
        original = site_location_service._weather_candidates
        try:
            site_location_service._weather_candidates = lambda _root, location: [{"source_id": "near", "target": "scenario.weather_profile", "value": {}, "scope": {"state": location["state"]}}]
            cited = site_location_service.post(web, project, {
                "action": "set_cited_location", "confirmed_address": "Melrose Central, Melrose Park NSW 2114",
                "latitude_deg": -33.8135, "longitude_deg": 151.0716, "state": "NSW", "locality": "Melrose Park",
                "source": "Map service", "citation": "https://maps.example/melrose-central", "reviewer": "QA"})
        finally:
            site_location_service._weather_candidates = original
        body = cited["site_location_resolution"]
        check("the service confirms a cited site location without any network lookup",
              body["status"] == "location_resolved" and body["location"]["basis"] == "reviewer_cited_map")
        check("weather candidates are found for the cited location", [row["source_id"] for row in body["weather_candidates"]] == ["near"])


if __name__ == "__main__":
    main()
