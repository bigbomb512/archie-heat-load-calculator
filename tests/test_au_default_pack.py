#!/usr/bin/env python3

import json
from pathlib import Path
import tempfile
import unittest

from ai.research_cache import eligible_bindings, validate_record
from tools.seed_au_default_pack import seed


ROOT = Path(__file__).resolve().parents[1]


class AustraliaDefaultPackTests(unittest.TestCase):
    def test_manifest_records_are_valid_and_profiles_are_24_hours(self):
        manifest = json.loads((ROOT / "config" / "au_cooling_default_pack.json").read_text(encoding="utf-8"))
        for raw in manifest["records"]:
            record = dict(raw)
            record.update({
                "url": manifest["source"]["url"],
                "publisher": manifest["source"]["publisher"],
                "retrieved_at": manifest["source"]["retrieved_at"],
                "content_hash": manifest["source"]["content_hash"],
                "source_pack_version": manifest["pack_version"],
            })
            checked = validate_record(record)
            self.assertEqual(checked["category"], "schedule_default")
            self.assertFalse(checked["released"])
            for binding in checked["bindings"]:
                self.assertEqual(len(binding["value"]), 24)

    def test_seed_is_idempotent_and_candidates_are_not_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "research_cache.json"
            first = seed(cache_path, ROOT / "config" / "au_cooling_default_pack.json")
            second = seed(cache_path, ROOT / "config" / "au_cooling_default_pack.json")
            self.assertEqual(len(first["changed_record_ids"]), 2)
            self.assertEqual(second["changed_record_ids"], [])
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertFalse(eligible_bindings(cache, "schedule.people", {"country": "AU", "room_use": "class_6_shop", "day_type": "weekday"}))


if __name__ == "__main__":
    unittest.main()
