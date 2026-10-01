#!/usr/bin/env python3
"""Focused checks for draft-only licensed Australian design-weather resolution."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import site_design_weather_resolution as weather
from ai.heat_loads import wet_bulb_method_id


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def profile(db, wb=None, dew=None):
    rows = []
    for hour in range(24):
        row = {"hour": hour, "outdoor_dry_bulb_c": db}
        if wb is not None:
            row["outdoor_wet_bulb_c"] = wb
        if dew is not None:
            row["outdoor_dew_point_c"] = dew
        rows.append(row)
    return {"hours": rows, "atmospheric_pressure_kpa": 101.2, "representative_month": "January"}


def record(record_id, scenario, value, *, locality="Sydney", state="NSW", basis="comfort"):
    return {
        "record_id": record_id, "scenario": scenario, "basis": basis,
        "scope": {"country": "AU", "locality": locality, "state": state},
        "profile": value, "content_hash": record_id + "-hash", "publisher": "AIRAH",
        "citation": "DA09 fixture table", "source_reference": "https://airah.org.au/fixture",
        "station_reference": "Sydney Observatory", "release_version": "2000-2021",
        "expiry": "2099-01-01T00:00:00+00:00",
    }


def pack(records):
    return {"schema_version": 1, "pack": {
        "pack_id": "airah-da09-fixture", "pack_version": "fixture-1",
        "licence_attestation": {"reference": "internal licence fixture", "authorised_by": "Admin"},
        "release": {"status": "released", "engineer": {"name": "Engineer", "credential": "CPEng"},
                    "approved_at": "2026-01-01T00:00:00+00:00", "approval_reference": "release-fixture", "expiry": "2099-01-01T00:00:00+00:00"},
        "records": records,
    }}


LOCATION = {"fingerprint": "location-fingerprint", "location": {"latitude_deg": -33.86, "longitude_deg": 151.2, "locality": "Sydney", "state": "NSW", "timezone": "Australia/Sydney"}}


def main():
    base = pack([record("sydney-cooling", "cooling", profile(35, 23)), record("sydney-heating", "heating", profile(5, 4))])
    selected_basis = weather.set_design_basis(weather.empty_site_design_weather_resolution(), "comfort", LOCATION)
    check("basis requires confirmed location and is persisted", selected_basis["design_basis"] == "comfort" and selected_basis["location_fingerprint"] == "location-fingerprint")
    resolved = weather.resolve_candidates(selected_basis, LOCATION, base)
    check("current specific cooling profile auto-selects", resolved["cooling"]["selected"]["record_id"] == "sydney-cooling")
    check("current specific heating profile auto-selects", resolved["heating"]["selected"]["record_id"] == "sydney-heating")
    check("full result is draft ready", resolved["status"] == "draft_ready")
    check("profile keeps 24 hour pressure-bound values", len(resolved["cooling"]["selected"]["profile"]["hours"]) == 24 and resolved["cooling"]["selected"]["profile"]["pressure_kpa"] == 101.2)

    dew_pack = pack([record("dew-cooling", "cooling", {**profile(33, dew=18), "psychrometric_method_citation": "Approved psychrometric conversion"}), record("dew-heating", "heating", profile(4, 3))])
    dew = weather.resolve_candidates(selected_basis, LOCATION, dew_pack)
    derived = dew["cooling"]["selected"]
    check("nonnegative dew-point conversion records its thermodynamic basis and method",
          derived["conversion"]["0"]["result_wet_bulb_c"] <= 33
          and derived["profile"]["hours"][0]["wet_bulb_basis"] == "thermodynamic"
          and derived["conversion"]["0"]["wet_bulb_method_id"] == wet_bulb_method_id("thermodynamic"))

    frost_pack = pack([record("frost-cooling", "cooling", {**profile(4, dew=-5), "psychrometric_method_citation": "Approved psychrometric conversion"}), record("frost-heating", "heating", profile(4, 3))])
    frost = weather.resolve_candidates(selected_basis, LOCATION, frost_pack)["cooling"]["selected"]
    check("subzero dew-point conversion remains legacy-unverified", frost["profile"]["hours"][0]["wet_bulb_basis"] == "legacy_unverified"
          and frost["conversion"]["0"]["wet_bulb_method_id"] == wet_bulb_method_id("legacy_unverified"))

    point = deepcopy(base)
    point["pack"]["records"][0].pop("profile")
    point["pack"]["records"][0]["design_point"] = {"outdoor_dry_bulb_c": 35, "outdoor_wet_bulb_c": 23}
    point_resolved = weather.resolve_candidates(selected_basis, LOCATION, point)
    check("point-only candidate remains visible but unselected", not point_resolved["cooling"]["selected"] and point_resolved["cooling"]["candidates"][0]["calculation_eligible"] is False)

    competing = pack([record("cool-a", "cooling", profile(35, 23)), record("cool-b", "cooling", profile(36, 24)), record("heat", "heating", profile(5, 4))])
    conflicted = weather.resolve_candidates(selected_basis, LOCATION, competing)
    check("equally ranked differing cooling profiles require a selection", not conflicted["cooling"]["selected"] and conflicted["cooling"]["conflicts"])
    chosen = weather.select_candidate(conflicted, "cooling", "cool-b")
    check("manual candidate selection resolves the cooling conflict", chosen["cooling"]["selected"]["record_id"] == "cool-b")
    cleared = weather.clear_selection(chosen, "cooling")
    check("selection can be cleared without changing reviewed scenarios", not cleared["cooling"]["selected"] and cleared["status"] == "needs_review")

    expired = deepcopy(base)
    expired["pack"]["release"]["expiry"] = "2020-01-01T00:00:00+00:00"
    try:
        weather.validate_licensed_airah_pack(expired)
    except ValueError as error:
        check("expired licensed pack is rejected", "expired" in str(error).casefold())
    else:
        raise AssertionError("expired licensed pack was accepted")


if __name__ == "__main__":
    main()
