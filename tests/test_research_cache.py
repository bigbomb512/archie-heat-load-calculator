#!/usr/bin/env python3

import unittest
from datetime import datetime, timedelta, timezone

from ai.research_cache import empty_research_cache, eligible_records, upsert_record


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


if __name__ == "__main__":
    unittest.main()
