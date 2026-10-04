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



def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


def title_block_checks():
    # Interleaved title-block text from a real fit-out set (Butcher Buffet):
    # the tenancy line names the centre, the street address is the
    # architect's office next to their email and phone numbers.
    title_block = ("26.02.26 B FOR CONSTRUCTION PLAN accordance with the requirements of current editions\n"
                   "admin@studio.example specifications. TENANCY MZ01,M38, MELROSE CENTRAL DRAWN SCALE REV NO\n"
                   "0430 377 302 (Sunny Liu) 5C/211-223 Pacific Hwy, All dimensions to North Sydney NSW 2060 be checked on site.")
    ai_input = {"drawing_set": {"pages": [{"page": 3, "drawing_number": "26.02", "title": "Dimension Plan",
                                           "structured_content": {"markdown": title_block}}]}}
    context = location.infer_pdf_context(ai_input, {}, {})
    check("sheet text such as 'PLAN ... current' is not read as a street in the NT",
          not any("PLAN" in row["address"] for row in context["address_candidates"]))
    check("an office address next to an email and phone number is excluded, with its reason, despite 'checked on site'",
          not context["address_candidates"] and context["excluded_address_candidates"]
          and "consultant" in context["excluded_address_candidates"][0]["reason"])
    centre = next((row for row in context["site_name_candidates"] if row.get("basis") == "tenancy_line"), None)
    check("the tenancy line proposes the centre as the site name, with the tenancy reference",
          centre is not None and centre["site_name"] == "Melrose Central" and centre["tenancy"] == "MZ01,M38")
    check("the centre is the highest-confidence site name", context["site_name_candidates"][0]["site_name"] == "Melrose Central")

    shop = {"drawing_set": {"pages": [{"page": 1, "drawing_number": "A-001", "title": "Site plan", "structured_content": {"markdown":
            "PROJECT ADDRESS: Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114"}}]}}
    check("a shop address with a lettered unit and the 'Av' abbreviation is read",
          [row["address"] for row in location.infer_pdf_context(shop, {}, {})["address_candidates"]] == ["G38/22 Lemon Tree Av, Melrose Park NSW 2114"])

    notes = {"drawing_set": {"pages": [{"page": 1, "drawing_number": "M-001", "title": "Notes", "structured_content": {"markdown":
             "SITE-SPECIFIC REQUIREMENTS. ALLOW FOR ACCESS PANELS\nPROJECT - 250209 Rev Date Description\nsite - do not scale"}}]}}
    check("note text and revision rows are not proposed as site names", location.infer_pdf_context(notes, {}, {})["site_name_candidates"] == [])


def cited_location_checks():
    state = location.empty_site_location_resolution()
    check("adding the new fields does not change the fingerprint of an empty site artifact",
          state["fingerprint"] == "706d254e65352616dd409f9bbcf43da256c26e548d71ff947ab150d2fb4c65f8")
    cited = {"latitude_deg": -33.8135, "longitude_deg": 151.0716, "state": "nsw", "locality": "Melrose Park",
             "source": "Map service", "citation": "https://maps.example/melrose-central", "reviewer": "QA"}
    address = "Melrose Central, Melrose Park NSW 2114"
    expect_error("a cited location needs a citation", lambda: location.set_cited_location(state, address, {**cited, "citation": ""}), "Cite where")
    expect_error("a cited location needs a reviewer", lambda: location.set_cited_location(state, address, {**cited, "reviewer": " "}), "reviewer")
    expect_error("a position outside Australia is refused", lambda: location.set_cited_location(state, address, {**cited, "latitude_deg": 33.8}), "latitude")
    expect_error("an unknown state is refused", lambda: location.set_cited_location(state, address, {**cited, "state": "XX"}), "state")
    resolved = location.set_cited_location(state, address, cited)
    check("a cited location resolves the site without G-NAF or lookup consent",
          resolved["status"] == "location_resolved" and resolved["location"]["timezone"] == "Australia/Sydney"
          and resolved["location"]["basis"] == "reviewer_cited_map" and not resolved["external_lookup_consent"]
          and resolved["geocode"]["status"] == "not_requested")
    check("the cited location keeps who cited it and from where",
          resolved["cited_location"]["reviewer"] == "QA" and resolved["cited_location"]["citation"].startswith("https://"))
    check("the cited location survives validation", location.validate_site_location_resolution(resolved)["location"]["latitude_deg"] == -33.8135)
    readdressed = location.confirm_address(resolved, "15 Queen Street, Sydney NSW 2000", True)
    check("confirming a different address for lookup clears the cited location",
          not readdressed["cited_location"] and readdressed["location"]["latitude_deg"] is None)


if __name__ == "__main__":
    main()
    title_block_checks()
    cited_location_checks()
