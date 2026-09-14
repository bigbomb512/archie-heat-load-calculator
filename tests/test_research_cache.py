#!/usr/bin/env python3

import unittest
from datetime import datetime, timedelta, timezone

from ai.research_cache import (
    empty_research_cache,
    eligible_bindings,
    eligible_records,
    source_record_release_status,
    upsert_record,
    validate_source_pack_release_manifest,
)


class ResearchCacheTests(unittest.TestCase):
    def released_manifest(self, record_id="retail-lighting", content_hash="abc", scope=None):
        return validate_source_pack_release_manifest({
            "schema_version": 1,
            "releases": [{
                "release_id": "au-cooling-v1-release-1", "pack_version": "au-cooling-v1", "status": "released",
                "engineer": {"name": "HVAC Engineer", "credential": "CPEng"},
                "approved_at": "2026-09-14T00:00:00+10:00", "approval_reference": "Default-pack release review",
                "scope": scope or {"country": "AU"}, "expiry": "2099-01-01T00:00:00+00:00",
                "records": [{"record_id": record_id, "content_hash": content_hash}],
            }],
        })

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
        manifest = self.released_manifest()
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}, now))
        self.assertEqual(len(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "retail"}, now, manifest)), 1)
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "room_use": "office"}, now, manifest))

    def test_engineer_release_requires_exact_record_hash_and_scope(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "retail-lighting", "url": "https://www.abcb.gov.au/lighting", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "changed", "category": "lighting_density_default", "value": 10, "unit": "W/m2", "scope": {"country": "AU"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Engineering lead", "expiry": (now + timedelta(days=1)).isoformat(), "source_pack_version": "au-cooling-v1", "released": True, "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2", "scope": {"room_use": "retail"}}]}
        cache = upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)
        manifest = self.released_manifest(content_hash="abc", scope={"country": "AU", "state": "NSW"})
        status = source_record_release_status(cache, record, {"country": "AU", "state": "NSW"}, now, manifest)
        self.assertFalse(status["eligible"])
        self.assertEqual(status["status"], "awaiting_engineering_release")
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU", "state": "VIC", "room_use": "retail"}, now, manifest))

    def test_unreleased_or_non_allowlisted_binding_cannot_be_used(self):
        now = datetime.now(timezone.utc)
        record = {"record_id": "unreleased", "url": "https://example.test/default", "publisher": "Test", "retrieved_at": now.isoformat(), "content_hash": "abc", "category": "lighting_density_default", "value": 10, "unit": "W/m2", "scope": {"country": "AU"}, "citation": "Table 1", "review_status": "approved", "reviewed_by": "Reviewer", "expiry": (now + timedelta(days=1)).isoformat(), "source_pack_version": "au-cooling-v1", "released": False, "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2"}]}
        cache = upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)
        self.assertFalse(eligible_bindings(cache, "room.lighting_w_m2", {"country": "AU"}, now))


if __name__ == "__main__":
    unittest.main()
