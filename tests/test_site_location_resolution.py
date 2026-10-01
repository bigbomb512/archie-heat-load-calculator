#!/usr/bin/env python3
"""Focused project-location and draft weather-resolution checks."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import site_location_resolution as location
from ai import value_resolution
from ai.ai_preliminary import load_pack


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def weather_profile():
    return {"hours": [{"db": 30 + index / 10, "wb": 20 + index / 10} for index in range(24)], "pressure_kpa": 101.2}


def main():
    ai_input = {"drawing_set": {"pages": [
        {"page": 1, "drawing_number": "A-001", "title": "Project construction drawing", "structured_content": {"markdown": "PROJECT: Example Fitout\n15 Queen Street, Sydney NSW 2000\nTRUE NORTH"}},
        {"page": 2, "drawing_number": "A-002", "title": "Consultant details", "structured_content": {"markdown": "Architect office: 1 Consultant Road, Sydney NSW 2000\n26.02.26"}},
    ]}}
    context = location.infer_pdf_context(ai_input, {}, {})
    check("PDF extractor retains the project address", len(context["address_candidates"]) == 1 and context["address_candidates"][0]["address"].startswith("15 Queen"))
    check("unrelated consultant office address is excluded", all("Consultant" not in row["address"] for row in context["address_candidates"]))
    check("north-arrow clue stays proposed evidence", len(context["orientation_clues"]) == 1)

    state = location.with_pdf_context(location.empty_site_location_resolution(), context)
    check("unconfirmed address remains gated", state["status"] == "awaiting_address_confirmation")
    try:
        location.confirm_address(state, "15 Queen Street, Sydney NSW 2000", False)
        raise AssertionError("location consent should be required")
    except ValueError:
        check("address lookup requires explicit consent", True)
    state = location.confirm_address(state, "15 Queen Street, Sydney NSW 2000", True)
    wrong_state = {"candidate_id": "wrong-state", "formatted_address": "15 Queen Street, Melbourne VIC 3000", "latitude_deg": -37.8,
                   "longitude_deg": 144.9, "state": "VIC", "locality": "Melbourne", "source_url": "https://api.geoscape.com.au/gnaf", "content_hash": "wrong"}
    try:
        location.apply_geocode_candidates(state, [wrong_state], wrong_state["source_url"], "response")
        raise AssertionError("wrong state should not resolve")
    except ValueError:
        check("state/suburb mismatch is rejected", True)
    candidate = {
        "candidate_id": "gnaf-example", "formatted_address": "15 Queen Street, Sydney NSW 2000",
        "latitude_deg": -33.8688, "longitude_deg": 151.2093, "state": "NSW", "locality": "Sydney",
        "confidence": 0.99, "provider_record_id": "GNAF-1", "release_version": "2026-09",
        "source_url": "https://api.geoscape.com.au/gnaf", "content_hash": "gnaf-hash",
    }
    weather = {"source_id": "bom-sydney-summer", "target": "scenario.weather_profile", "value": weather_profile(), "unit": "profile",
               "scope": {"country": "AU", "state": "NSW", "locality": "Sydney", "scenario": "summer"},
               "publisher": "Bureau of Meteorology", "citation": "Sydney design profile", "source_reference": "https://www.bom.gov.au/example", "content_hash": "bom-hash"}
    state = location.apply_geocode_candidates(state, [candidate], candidate["source_url"], "response-hash", "gnaf-example",
                                                {"elevation_m": 20, "source_url": "https://data.ga.gov.au/elevation", "content_hash": "ga-hash"}, [weather])
    check("G-NAF candidate resolves coordinates and IANA timezone", state["location"]["timezone"] == "Australia/Sydney" and state["status"] == "location_resolved")
    check("address does not create façade records", not state.get("facades") and not state["map_or_survey_evidence"])
    state = location.select_weather_source(state, "bom-sydney-summer")
    check("released selected weather source is retained", state["selected_weather_source"]["source_id"] == "bom-sydney-summer")

    building = {"country": "AU", "spaces": [{"name": "Dining", "level_name": "Level 1", "area": 40}]}
    proposal = {"rooms": [{"label": "Dining", "level_name": "Level 1", "area_m2": 40, "preliminary_profile_id": "hospitality", "confidence": 0.8}], "surfaces": [], "openings": []}
    artifact, values = value_resolution.build_value_resolution(load_pack(), building, proposal, {"schema_version": 1, "revision": 0, "source_pack_version": "", "records": []}, site_location=state)
    selected = next(row for row in artifact["records"] if row["target"] == "scenario.weather_profile")
    check("selected location weather replaces generic preliminary weather", selected["origin"] == "released_source_pack" and values["scenario"]["weather_profile"]["hours"][0]["db"] == 30)


if __name__ == "__main__":
    main()
