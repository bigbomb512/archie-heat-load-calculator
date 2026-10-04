#!/usr/bin/env python3
"""Card L part 4: the draft says which design day, sun values and site it used."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, value_resolution
from ai import site_location_resolution as location


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


BUILDING = {"country": "AU", "spaces": [{"name": "Dining", "level_name": "Level 1", "area": 40}]}
PROPOSAL = {"rooms": [{"label": "Dining", "level_name": "Level 1", "area_m2": 40, "preliminary_profile_id": "hospitality", "confidence": 0.8}],
            "surfaces": [], "openings": []}
EMPTY_STATE = {"schema_version": 1, "revision": 0, "source_pack_version": "", "records": []}


def resolve(site=None):
    artifact, values = value_resolution.build_value_resolution(ai_preliminary.load_pack(), BUILDING, PROPOSAL, EMPTY_STATE, site_location=site)
    return artifact, values


def cited_site(weather=None):
    state = location.set_cited_location(location.empty_site_location_resolution(), "Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114", {
        "latitude_deg": -33.81, "longitude_deg": 151.07, "state": "NSW", "locality": "Melrose Park",
        "source": "Map service", "citation": "https://maps.example/lemon-tree-av", "reviewer": "QA"}, weather_candidates=[weather] if weather else None)
    return location.select_weather_source(state, weather["source_id"]) if weather else state


def generic_checks():
    artifact, values = resolve()
    basis = ai_preliminary.design_conditions_basis(artifact)
    check("without a cited source the design day is labelled generic and not site-specific",
          not basis["design_day"]["site_specific"] and "not site-specific" in basis["design_day"]["label"]
          and ai_preliminary.PACK_VERSION in basis["design_day"]["label"])
    check("sun values are always labelled generic until a cited site solar source exists",
          not basis["sun"]["site_specific"] and "no flat-roof sun" in basis["sun"]["label"])
    check("an unconfirmed site says so", basis["site"]["label"] == "Site location not confirmed")
    scenario = ai_preliminary._scenario(ai_preliminary.load_pack(), values["scenario"], basis)["scenarios"][0]
    check("the scenario title no longer reads as a real Australian design day", "not site-specific" in scenario["title"])

    site = cited_site()
    artifact, _values = resolve(site)
    basis = ai_preliminary.design_conditions_basis(artifact, site)
    check("a confirmed site with no cited weather still uses the generic day, and the label says both",
          not basis["design_day"]["site_specific"] and basis["site"]["confirmed"]
          and basis["site"]["label"] == "Site: Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114 (reviewer-cited map position)")


def cited_weather_checks():
    weather = {"source_id": "airah-fixture", "target": "scenario.weather_profile", "unit": "profile",
               "value": {"hours": [{"db": 30 + index / 10, "wb": 20} for index in range(24)], "pressure_kpa": 101.2},
               "scope": {"country": "AU", "state": "NSW", "scenario": "summer"},
               "publisher": "Fixture publisher", "citation": "Fixture design day", "source_reference": "https://example.org/fixture", "content_hash": "fixture"}
    site = cited_site(weather)
    artifact, values = resolve(site)
    basis = ai_preliminary.design_conditions_basis(artifact, site)
    check("a selected cited design day is labelled site-specific with its source",
          basis["design_day"]["site_specific"] and basis["design_day"]["label"] == "Site design day: Fixture publisher — Fixture design day")
    scenario = ai_preliminary._scenario(ai_preliminary.load_pack(), values["scenario"], basis)["scenarios"][0]
    check("the scenario takes the cited source", scenario["title"] == "Site cooling design day" and scenario["source"] == "Fixture publisher — Fixture design day")
    check("the sun values stay generic even with a cited design day", not basis["sun"]["site_specific"])


def main():
    generic_checks()
    cited_weather_checks()


if __name__ == "__main__":
    main()
