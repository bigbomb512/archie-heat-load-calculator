#!/usr/bin/env python3
"""Rule matching tests use explicitly synthetic test-only values, never defaults."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import au_ventilation_rules as rules
from ai.design_requirements import empty_zone_ventilation_requirements
from ai.ventilation import calculate_ventilation_report


JURISDICTIONS = sorted(rules.JURISDICTIONS)


def rule_record(jurisdiction, edition, start, end, rule_id):
    return {
        "rule_id": rule_id, "jurisdiction": jurisdiction, "status": "released",
        "effective_from": start, "effective_to": end, "ncc_edition": edition,
        "ncc_clause": "TEST NCC clause", "standard_edition": "TEST standard edition",
        "standard_clause": "TEST ventilation clause", "source": "TEST ONLY — synthetic fixture",
        "source_access_basis": "TEST ONLY — not authorized source material",
        "reviewed_by": "Test reviewer", "reviewer_credential": "Test credential", "reviewed_at": "2024-01-01",
        "scope": {"building_class": "class_6", "room_use": "dining"},
        "requirements": {"outside_air_method": "combined", "rates": {
            "people_rate_lps_per_person": 2.0, "area_rate_lps_per_m2": 0.3,
        }},
    }


def fixture_pack():
    pack = rules.empty_ruleset()
    pack["status"] = "test_only"
    for state in JURISDICTIONS:
        pack["jurisdictions"][state] = {"status": "released", "ncc_adoptions": [
            {"jurisdiction": state, "ncc_edition": "NCC TEST A", "effective_from": "2024-01-01", "effective_to": "2024-12-31", "status": "released", "source": "TEST", "reviewed_by": "Test reviewer", "reviewed_at": "2024-01-01"},
            {"jurisdiction": state, "ncc_edition": "NCC TEST B", "effective_from": "2025-01-01", "effective_to": "", "status": "released", "source": "TEST", "reviewed_by": "Test reviewer", "reviewed_at": "2025-01-01"},
        ]}
        pack["rules"].extend([
            rule_record(state, "NCC TEST A", "2024-01-01", "2024-12-31", f"test-{state.lower()}-a"),
            rule_record(state, "NCC TEST B", "2025-01-01", "", f"test-{state.lower()}-b"),
        ])
    return rules.validate_ruleset(pack)


def context(day="2024-05-01", building_class="class_6", project_basis=""):
    return {"building_approval_application_date": day, "building_class": building_class,
            "building_use": "Restaurant", "project_specific_ventilation_basis": project_basis}


def main():
    production = rules.load_ruleset()
    assert set(production["jurisdictions"]) == rules.JURISDICTIONS
    assert all(not row.get("ncc_adoptions") for row in production["jurisdictions"].values())
    assert production["rules"] == []
    print("PASS - production ruleset declares eight jurisdictions and releases no unreviewed rates")

    pack = fixture_pack()
    for state in JURISDICTIONS:
        resolved = rules.resolve({"state": state}, context(), [{"zone_id": "dining", "room_use_category": "dining"}], pack)
        assert resolved["status"] == "matched" and resolved["jurisdiction"] == state
    print("PASS - explicit released rule matches in every jurisdiction")

    end = rules.resolve({"state": "NSW"}, context("2024-12-31"), [{"zone_id": "d", "room_use_category": "dining"}], pack)
    start = rules.resolve({"state": "NSW"}, context("2025-01-01"), [{"zone_id": "d", "room_use_category": "dining"}], pack)
    assert end["ncc_edition"] == "NCC TEST A" and end["zone_results"][0]["rule_id"] == "test-nsw-a"
    assert start["ncc_edition"] == "NCC TEST B" and start["zone_results"][0]["rule_id"] == "test-nsw-b"
    print("PASS - code edition and rule switch on exact effective-date boundary")

    unresolved_context = rules.resolve({"state": "NSW"}, context("", "unknown"), [{"zone_id": "d", "room_use_category": "dining"}], pack)
    assert unresolved_context["status"] == "needs_review" and not unresolved_context["zone_results"][0]["rule_id"]
    project_override = rules.resolve({"state": "NSW"}, context(project_basis="Project mechanical brief M-01"), [{"zone_id": "d", "room_use_category": "dining"}], pack)
    assert project_override["status"] == "needs_review" and not project_override["zone_results"][0]["rule_id"]
    unknown_use = rules.resolve({"state": "NSW"}, context(), [{"zone_id": "d", "room_use_category": ""}], pack)
    assert unknown_use["zone_results"][0]["status"] == "blocked"
    print("PASS - unknown class/date/use and project-specific basis block automatic selection")

    duplicate = fixture_pack()
    duplicate["rules"].append(rule_record("NSW", "NCC TEST A", "2024-01-01", "2024-12-31", "test-nsw-duplicate"))
    ambiguous = rules.resolve({"state": "NSW"}, context(), [{"zone_id": "d", "room_use_category": "dining"}], duplicate)
    assert ambiguous["zone_results"][0]["status"] == "blocked"
    assert "Multiple" in ambiguous["zone_results"][0]["message"]
    print("PASS - overlapping applicable rules never auto-select")

    resolved = rules.resolve({"state": "NSW"}, context(), [{"zone_id": "dining", "room_use_category": "dining"}], pack)
    payload = {"zones": [{"zone_id": "dining", "area_m2": 40, "occupancy": 20,
                          "cooling_load": {"outside_air_lps": None},
                          "ventilation_requirements": empty_zone_ventilation_requirements()}]}
    updated, outcomes = rules.apply_matches(payload, resolved, pack)
    vent = updated["zones"][0]["ventilation_requirements"]
    assert outcomes == [{"zone_id": "dining", "status": "applied", "rule_id": "test-nsw-a"}]
    assert vent["people_rate_lps_per_person"] == 2.0 and vent["rule_citation"].startswith("TEST ONLY")
    assert updated["zones"][0]["cooling_load"]["outside_air_lps"] == 40.0
    assert "test-nsw-a" in updated["zones"][0]["cooling_load"]["source"]
    assert vent["process_exhaust_lps"] is None
    report = calculate_ventilation_report(updated)
    assert report["zone_results"][0]["basis"]["rule_id"] == "test-nsw-a"
    assert report["zone_results"][0]["basis"]["rule_pack_version"] == "1.0.0"
    print("PASS - uniquely matched rules fill blank rates, propagate outside-air flow, and keep process exhaust separate")

    hourly = {"rooms": [{"room_id": "dining-room", "zone_id": "dining", "source_zone_id": "dining",
                         "cooling_load": {"outside_air_lps": None, "source": ""}}]}
    synced, hourly_outcomes = rules.sync_hourly_model_outside_air(hourly, updated)
    assert synced["rooms"][0]["cooling_load"]["outside_air_lps"] == 40.0
    assert hourly_outcomes[0]["rule_id"] == "test-nsw-a"
    manual_model = {"rooms": [{"room_id": "dining-room", "zone_id": "dining", "source_zone_id": "dining",
                               "cooling_load": {"outside_air_lps": 90, "source": "Designer input"}}]}
    preserved, manual_outcomes = rules.sync_hourly_model_outside_air(manual_model, updated)
    assert preserved["rooms"][0]["cooling_load"]["outside_air_lps"] == 90
    assert manual_outcomes[0]["status"] == "manual_conflict"
    cleared, stale_outcomes = rules.sync_hourly_model_outside_air(synced, {"zones": [{"zone_id": "dining", "cooling_load": {"outside_air_lps": None}, "ventilation_requirements": {}}]})
    assert cleared["rooms"][0]["cooling_load"]["outside_air_lps"] is None
    assert stale_outcomes[0]["status"] == "invalidated"
    print("PASS - hourly room model receives matched outside air, preserves manual flows, and clears stale automatic flows")

    unmatched = {"zone_results": [{"zone_id": "dining", "status": "blocked", "message": "No released rule."}]}
    invalidated, invalidation_outcome = rules.apply_matches(updated, unmatched, pack)
    invalidated_zone = invalidated["zones"][0]
    assert invalidated_zone["ventilation_requirements"]["rule_id"] == ""
    assert invalidated_zone["ventilation_requirements"]["people_rate_lps_per_person"] is None
    assert invalidated_zone["cooling_load"]["outside_air_lps"] is None
    assert invalidation_outcome[0]["status"] == "previous_rule_invalidated"
    print("PASS - loss of location/date/rule match invalidates prior automatic values and restores prior blank inputs")

    conflict_payload = {"zones": [{"zone_id": "dining", "area_m2": 40, "occupancy": 20,
                                   "cooling_load": {"outside_air_lps": 90},
                                   "ventilation_requirements": {**empty_zone_ventilation_requirements(), "outside_air_method": "occupancy", "people_rate_lps_per_person": 5.0}}]}
    unchanged, conflicts = rules.apply_matches(conflict_payload, resolved, pack)
    assert unchanged["zones"][0]["ventilation_requirements"]["people_rate_lps_per_person"] == 5.0
    assert conflicts[0]["status"] == "manual_conflict"
    print("PASS - existing manual values remain untouched and are reported as conflicts")

    changed_auto = updated["zones"][0]
    changed_auto["ventilation_requirements"]["people_rate_lps_per_person"] = 4.0
    manualized, status = rules.apply_matches({"zones": [changed_auto]}, unmatched, pack)
    assert manualized["zones"][0]["ventilation_requirements"]["people_rate_lps_per_person"] == 4.0
    assert status[0]["status"] == "stale_values_preserved"
    print("PASS - edited automatic values are preserved and clearly demoted when their rule context becomes stale")

    try:
        invalid = fixture_pack()
        invalid["rules"][0]["source_access_basis"] = ""
        rules.validate_ruleset(invalid)
        raise AssertionError("released rule without source rights metadata was accepted")
    except ValueError:
        print("PASS - released rule requires source access and engineer review provenance")


if __name__ == "__main__":
    main()
