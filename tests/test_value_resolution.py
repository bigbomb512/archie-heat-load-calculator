"""Focused checks for draft-only AI evidence-to-source resolution."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, value_resolution
from ai.research_cache import validate_cache


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def building():
    return {"spaces": [{"name": "Dining", "level_name": "Level 1", "area": 60,
                         "evidence": [{"page": 2, "excerpt": "Dining"}]}]}


def proposal():
    return {"rooms": [{"kind": "room", "label": "Dining", "level_name": "Level 1", "area_m2": 60,
                         "preliminary_profile_id": "hospitality", "confidence": 0.8, "page": 2}],
            "surfaces": [], "openings": [], "issues": []}


def released_cache():
    record = {
        "record_id": "au-hospitality-occupancy", "url": "https://abcb.gov.au/example/occupancy",
        "publisher": "ABCB", "retrieved_at": "2026-01-01T00:00:00+00:00", "content_hash": "occupancy-hash",
        "category": "occupancy_default", "value": 0.2, "unit": "people/m2",
        "scope": {"country": "AU", "room_use": "hospitality"}, "citation": "Fixture occupancy source",
        "review_status": "approved", "reviewed_by": "Engineer", "expiry": "2099-01-01T00:00:00+00:00",
        "source_pack_version": "au-cooling-v1", "bindings": [{"target": "room.occupancy_density_per_m2", "value": 0.2,
                                                                    "unit": "people/m2", "scope": {}}],
    }
    return validate_cache({"schema_version": 1, "revision": 1, "source_pack_version": "au-cooling-v1", "records": [record]})


def manifest():
    return {"schema_version": 1, "releases": [{"release_id": "release-hospitality", "pack_version": "au-cooling-v1",
            "status": "released", "engineer": {"name": "Fixture Engineer", "credential": "MIEAust"},
            "approved_at": "2026-01-01T00:00:00+00:00", "approval_reference": "fixture-release",
            "scope": {"country": "AU", "room_use": "hospitality"}, "expiry": "2099-01-01T00:00:00+00:00",
            "records": [{"record_id": "au-hospitality-occupancy", "content_hash": "occupancy-hash"}]}]}


def record_for(artifact, target):
    return next(row for row in artifact["records"] if row["target"] == target and row["target_id"] == "room:level-1:dining")


def main():
    pack = ai_preliminary.load_pack()
    artifact, values = value_resolution.build_value_resolution(pack, building(), proposal(), released_cache(),
                                                                 release_manifest=manifest(), source_fingerprints={"fixture": "a"})
    occupancy = record_for(artifact, "room.occupancy_density_per_m2")
    check("released pack wins over a preliminary fallback", occupancy["origin"] == "released_source_pack" and occupancy["value"] == 0.2)
    check("matching context is preserved with the selected source", occupancy["context"]["room_use"] == "hospitality" and occupancy["citation"]["content_hash"] == "occupancy-hash")
    check("room value map feeds the selected released value to assembly", values["rooms"]["room:level-1:dining"]["occupancy_density_people_m2"] == 0.2)
    site_location = {"fingerprint": "sydney-location", "location": {"locality": "Sydney", "state": "NSW", "latitude_deg": -33.86, "longitude_deg": 151.2}}
    weather_profile = {"hours": [{"hour": hour, "db": 30 + (hour == 14), "wb": 20} for hour in range(24)], "pressure_kpa": 101.2}
    design_weather = {"status": "draft_ready", "location_fingerprint": "sydney-location", "cooling": {"selected": {"record_id": "airah-sydney", "profile": weather_profile, "publisher": "AIRAH", "citation": "Fixture", "content_hash": "weather-hash"}}}
    weather_artifact, weather_values = value_resolution.build_value_resolution(pack, building(), proposal(), released_cache(),
                                                                                release_manifest=manifest(), site_location=site_location,
                                                                                site_design_weather=design_weather)
    weather_record = next(row for row in weather_artifact["records"] if row["target"] == "scenario.weather_profile")
    check("selected licensed cooling weather replaces the generic preliminary profile", weather_record["origin"] == "released_source_pack" and weather_values["scenario"]["weather_profile"]["hours"][14]["db"] == 31)
    outside_air = record_for(artifact, "room.outside_air_lps")
    check("derived outside air retains formula operands", outside_air["derivation"]["operands"]["area_m2"] == 60 and outside_air["value"] > 0)

    stale_ceiling, stale_values = value_resolution.build_value_resolution(
        pack, building(), proposal(), released_cache(), release_manifest=manifest(),
        ceiling_volume_resolution={"records": [{"room_id": "room:level-1:dining", "ceiling_height_mm": 9000,
                                                  "volume_m3": 540, "origin": "contractor_override", "status": "stale"}]},
        internal_gains_resolution={"records": [{"room_id": "room-use:level-1:dining", "status": "stale",
                                                  "occupancy_count": 300, "fields": {"occupancy_count": {"value": 300}}}]},
    )
    check("stale ceiling override is not used in room value mapping",
          stale_values["rooms"]["room:level-1:dining"]["ceiling_height_mm"] != 9000)
    check("stale internal-gains override is not copied into active value resolution",
          not any(row.get("rationale") == "Internal-gains resolver materialized field."
                  and row.get("value") == 300 for row in stale_ceiling["records"]))

    state = value_resolution.empty_value_resolution()
    state["source_pack_version"] = "au-cooling-v1"
    state = value_resolution.add_project_source(state, {
        "target": "room.lighting_w_m2", "target_id": "room:level-1:dining", "value": 12.5, "unit": "W/m2",
        "url": "https://abcb.gov.au/example/lighting", "publisher": "ABCB", "citation": "Fixture project source",
        "content_hash": "lighting-hash", "excerpt": "Fixture lighting clause", "retrieved_at": "2026-01-01T00:00:00+00:00",
        "expiry": "2099-01-01T00:00:00+00:00", "scope": {"country": "AU", "room_use": "hospitality"},
    })
    try:
        value_resolution.add_project_source(value_resolution.empty_value_resolution(), {
            "target": "room.lighting_w_m2", "target_id": "room:level-1:dining", "value": 12.5, "unit": "W/m2",
            "url": "https://abcb.gov.au/example/expired", "publisher": "ABCB", "citation": "Expired fixture",
            "content_hash": "expired-hash", "retrieved_at": "2026-01-02T00:00:00+00:00", "expiry": "2026-01-01T00:00:00+00:00",
            "scope": {"country": "AU"}, "source_pack_version": "au-cooling-v1",
        })
    except ValueError:
        expired_rejected = True
    else:
        expired_rejected = False
    check("expired project source is rejected", expired_rejected)
    state = value_resolution.add_override(state, {"target": "room.lighting_w_m2", "target_id": "room:level-1:dining",
                                                   "value": 15, "unit": "W/m2", "reason": "Contractor-confirmed fixture."})
    overridden, _values = value_resolution.build_value_resolution(pack, building(), proposal(), released_cache(), state, manifest())
    lighting = record_for(overridden, "room.lighting_w_m2")
    check("contractor override has deterministic precedence", lighting["origin"] == "contractor_override" and lighting["value"] == 15)
    without_override = deepcopy(state)
    without_override["overrides"] = []
    project_sourced, _values = value_resolution.build_value_resolution(pack, building(), proposal(), released_cache(), without_override, manifest())
    check("scoped project source outranks preliminary fallback", record_for(project_sourced, "room.lighting_w_m2")["origin"] == "project_evidence")

    try:
        value_resolution.queue_research_job(state, "room.wall_u_value_w_m2k", "room:level-1:dining", {"country": "AU"}, "Missing construction")
    except ValueError:
        blocked_without_consent = True
    else:
        blocked_without_consent = False
    check("external research requires recorded consent", blocked_without_consent)
    state["research_consent"] = True
    queued = value_resolution.queue_research_job(state, "room.wall_u_value_w_m2k", "room:level-1:dining", {"country": "AU"}, "Missing construction")
    check("research queue is allowlisted and contains no fetched value", queued["research_jobs"][0]["allowed_domains"] and queued["research_jobs"][0]["status"] == "queued")

    assembled = ai_preliminary.assemble(building(), preliminary_proposal=proposal(), value_resolution=state,
                                         research_cache=released_cache(), source_pack_releases=manifest())
    room = assembled["material"]["hourly_load_model"]["rooms"][0]
    check("resolved lighting value reaches the existing hourly-engine input", room["cooling_load"]["lighting_w_m2"] == 15)
    check("snapshot retains per-value source drill-down", assembled["value_resolution"]["records"] and assembled["resolved_value_records"] == assembled["value_resolution"]["records"])
    changed = deepcopy(state)
    changed["overrides"][0]["value"] = 16
    check("a source resolution change stales the preliminary snapshot fingerprint", assembled["input_fingerprint"] != ai_preliminary.assemble(
        building(), preliminary_proposal=proposal(), value_resolution=changed, research_cache=released_cache(), source_pack_releases=manifest()
    )["input_fingerprint"])


if __name__ == "__main__":
    main()
