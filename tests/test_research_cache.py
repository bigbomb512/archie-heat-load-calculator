#!/usr/bin/env python3

import unittest
from datetime import datetime, timedelta, timezone

from ai.research_cache import (
    approved_source_pack,
    eligible_bindings,
    eligible_records,
    empty_research_cache,
    permitted_binding_target,
    upsert_record,
    validate_record,
)


PACK = "au-cooling-v1"


def released_record(**overrides):
    """A record that is approved, released and otherwise fully compliant."""
    now = datetime.now(timezone.utc)
    record = {
        "record_id": "au-retail-lighting", "url": "https://www.abcb.gov.au/lighting",
        "publisher": "Australian Building Codes Board", "retrieved_at": now.isoformat(),
        "content_hash": "hash-1", "category": "lighting_density_default", "value": 10,
        "unit": "W/m2", "scope": {"country": "AU", "room_use": "retail"},
        "citation": "Table 1", "review_status": "approved", "reviewed_by": "Engineering lead",
        "expiry": (now + timedelta(days=30)).isoformat(), "source_pack_version": PACK,
        "released": True,
        "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2"}],
    }
    record.update(overrides)
    return record


def cache_with(*records):
    cache = {**empty_research_cache(), "source_pack_version": PACK}
    for record in records:
        cache = upsert_record(cache, record)
    return cache


class ResearchCacheTests(unittest.TestCase):
    def test_only_approved_unexpired_records_are_eligible(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "weather-1", "url": "https://example.test/weather", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "abc", "category": "weather", "value": 32, "unit": "C", "scope": {"location": "Melbourne"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": (now + timedelta(days=1)).isoformat()}
        cache = upsert_record(empty_research_cache(), record)
        self.assertEqual(len(eligible_records(cache, "weather", {"location": "Melbourne"}, now)), 1)

    def test_scope_is_enforced(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "weather-1", "url": "https://example.test/weather", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "abc", "category": "weather", "value": 32, "unit": "C", "scope": {"location": "Sydney"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": (now + timedelta(days=1)).isoformat()}
        self.assertFalse(eligible_records(upsert_record(empty_research_cache(), record), "weather", {"location": "Melbourne"}, now))

    def test_target_bindings_allow_broader_scope_but_reject_conflicts(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "retail-lighting", "url": "https://www.abcb.gov.au/lighting", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "abc", "category": "lighting_density_default", "value": 10, "unit": "W/m2", "scope": {"country": "AU"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": (now + timedelta(days=1)).isoformat(), "source_pack_version": "au-cooling-v1", "released": True, "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2", "scope": {"room_use": "retail"}}]}
        cache = upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)
        self.assertEqual(len(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}, now)), 1)
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "office"}, now))

    def test_unreleased_or_non_allowlisted_binding_cannot_be_used(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "unreleased", "url": "https://example.test/default", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "abc", "category": "lighting_density_default", "value": 10, "unit": "W/m2", "scope": {"country": "AU"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": (now + timedelta(days=1)).isoformat(), "source_pack_version": "au-cooling-v1", "released": False, "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2"}]}
        cache = upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU"}, now))


class SourcePackGovernanceTests(unittest.TestCase):
    """Every gate a record must pass before it may supply an automatic default."""

    def test_approved_and_released_record_resolves_a_permitted_field(self):
        self.assertEqual(len(eligible_bindings(cache_with(released_record()), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"})), 1)

    def test_proposed_record_is_stored_visibly_but_stays_ineligible(self):
        record = released_record(review_status="proposed", reviewed_by="", released=False)
        cache = cache_with(record)
        stored = cache["records"][0]
        self.assertEqual(stored["review_status"], "proposed")
        self.assertFalse(stored["released"])
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_proposed_record_cannot_be_silently_released(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(review_status="proposed", reviewed_by=""))

    def test_released_record_requires_a_named_reviewer(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(reviewed_by=""))

    def test_expired_record_is_ineligible(self):
        now = datetime.now(timezone.utc)
        record = released_record(retrieved_at=(now - timedelta(days=400)).isoformat(), expiry=(now - timedelta(days=1)).isoformat())
        self.assertFalse(eligible_bindings(cache_with(record), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_expiry_must_follow_retrieval(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            validate_record(released_record(retrieved_at=now.isoformat(), expiry=(now - timedelta(days=1)).isoformat()))

    def test_rejected_record_is_ineligible(self):
        record = released_record(review_status="rejected", released=False)
        self.assertFalse(eligible_bindings(cache_with(record), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_out_of_scope_record_is_ineligible(self):
        record = released_record(scope={"country": "NZ", "room_use": "retail"})
        self.assertFalse(eligible_bindings(cache_with(record), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_uncited_record_is_rejected_at_validation(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(citation="   "))

    def test_unhashed_or_unpublished_record_is_rejected(self):
        for field in ("content_hash", "publisher"):
            with self.assertRaises(ValueError):
                validate_record(released_record(**{field: " "}))

    def test_non_https_or_hostless_url_is_rejected(self):
        for url in ("http://www.abcb.gov.au/lighting", "not-a-url"):
            with self.assertRaises(ValueError):
                validate_record(released_record(url=url))

    def test_off_domain_record_cannot_supply_a_default(self):
        record = released_record(url="https://example.test/lighting")
        self.assertFalse(eligible_bindings(cache_with(record), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_mismatched_source_pack_version_is_ineligible(self):
        record = released_record(source_pack_version="au-cooling-v2")
        self.assertFalse(eligible_bindings(cache_with(record), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}))

    def test_released_record_requires_a_geographic_scope(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(scope={"room_use": "retail"}))

    def test_released_room_binding_requires_a_use_scope(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(scope={"country": "AU"}, bindings=[{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2"}]))

    def test_conflicting_records_are_both_returned_for_the_resolver_to_block(self):
        first = released_record()
        second = released_record(record_id="au-retail-lighting-alt", value=12, bindings=[{"target": "room.lighting_w_m2", "value": 12, "unit": "W/m2"}])
        found = eligible_bindings(cache_with(first, second), "room.lighting_w_m2", {"country": "AU", "room_use": "retail"})
        self.assertEqual([row["record_id"] for row in found], ["au-retail-lighting", "au-retail-lighting-alt"])
        self.assertEqual({row["value"] for row in found}, {10, 12})


class PermittedTargetTests(unittest.TestCase):
    """Approved defaults may only reach low-risk fields, never project geometry."""

    def setUp(self):
        self.pack = approved_source_pack(PACK)

    def test_low_risk_fields_are_permitted(self):
        for target in ("room.lighting_w_m2", "room.safety_factor", "room.occupancy_density_per_m2", "schedule.people"):
            self.assertTrue(permitted_binding_target(self.pack, target), target)

    def test_geometry_envelope_and_use_are_never_permitted(self):
        for target in ("room.area_m2", "room.volume_m3", "room.occupancy", "room.u_value_w_m2k", "room.envelope_surfaces", "room.use"):
            self.assertFalse(permitted_binding_target(self.pack, target), target)

    def test_undeclared_target_is_not_permitted(self):
        self.assertFalse(permitted_binding_target(self.pack, "room.invented_field"))

    def test_a_prohibited_binding_cannot_even_be_released(self):
        with self.assertRaises(ValueError):
            validate_record(released_record(category="occupancy_default", bindings=[{"target": "room.area_m2", "value": 20, "unit": "m2"}]))

    def test_prohibited_target_is_unreachable_even_if_stored_as_proposed(self):
        record = released_record(category="occupancy_default", review_status="proposed", reviewed_by="", released=False, bindings=[{"target": "room.area_m2", "value": 20, "unit": "m2"}])
        self.assertFalse(eligible_bindings(cache_with(record), "room.area_m2", {"country": "AU", "room_use": "retail"}))


if __name__ == "__main__":
    unittest.main()
