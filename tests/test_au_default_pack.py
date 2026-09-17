#!/usr/bin/env python3

import json
from pathlib import Path
import tempfile
import unittest

from ai.research_cache import eligible_bindings, validate_candidate_record, validate_record, empty_research_cache, upsert_record
from tools.seed_au_default_pack import inspect, seed
from tools.release_au_default_pack import release


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

    def test_candidate_report_lists_missing_coverage_without_inventing_values(self):
        report = inspect(ROOT / "config" / "au_cooling_default_pack.json")
        self.assertEqual(report["candidate_counts_by_category"], {"schedule_default": 2})
        self.assertFalse(report["ineligible_records"])
        self.assertTrue(any(row["target"] == "scenario.weather_profile" for row in report["missing_coverage"]))
        self.assertTrue(any(row["scope"] == {"day_type": "weekend"} for row in report["missing_coverage"] if row["category"] == "schedule_default"))

    def test_high_risk_target_and_incomplete_profile_are_rejected(self):
        base = {
            "record_id": "invalid-candidate", "url": "https://www.abcb.gov.au/source", "publisher": "ABCB",
            "retrieved_at": "2026-09-14T00:00:00+10:00", "content_hash": "abc", "category": "schedule_default",
            "value": "Invalid", "unit": "profile", "scope": {"country": "AU", "room_use": "retail"},
            "citation": "Source", "review_status": "proposed", "expiry": "2027-01-01T00:00:00+10:00",
            "bindings": [{"target": "schedule.people", "value": [1] * 23, "unit": "profile", "scope": {"day_type": "weekday"}}],
        }
        with self.assertRaisesRegex(ValueError, "24 hourly"):
            validate_candidate_record(base)
        base["bindings"] = [{"target": "room.area_m2", "value": 20, "unit": "m2"}]
        with self.assertRaisesRegex(ValueError, "not an allowed low-risk"):
            validate_candidate_record(base)

    def test_record_specific_source_is_preserved_but_candidate_is_never_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads((ROOT / "config" / "au_cooling_default_pack.json").read_text(encoding="utf-8"))
            manifest["records"] = [manifest["records"][0]]
            manifest["records"][0]["source"] = {
                "url": "https://www.csiro.au/cooling-profile",
                "publisher": "CSIRO", "retrieved_at": "2026-09-14T00:00:00+10:00",
                "content_hash": "csiro-candidate-v1", "citation": "CSIRO candidate profile"
            }
            pack_path = root / "pack.json"
            cache_path = root / "research_cache.json"
            pack_path.write_text(json.dumps(manifest), encoding="utf-8")
            seed(cache_path, pack_path)
            record = json.loads(cache_path.read_text(encoding="utf-8"))["records"][0]
            self.assertEqual(record["publisher"], "CSIRO")
            self.assertEqual(record["review_status"], "proposed")
            self.assertFalse(record["released"])

    def test_unallowlisted_candidate_source_is_reported_and_cannot_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads((ROOT / "config" / "au_cooling_default_pack.json").read_text(encoding="utf-8"))
            manifest["records"] = [manifest["records"][0]]
            manifest["records"][0]["source"] = {
                "url": "https://example.test/profile", "publisher": "Unapproved", "retrieved_at": "2026-09-14T00:00:00+10:00",
                "content_hash": "unapproved-v1", "citation": "Unapproved profile"
            }
            pack_path = root / "pack.json"
            pack_path.write_text(json.dumps(manifest), encoding="utf-8")
            report = inspect(pack_path)
            self.assertTrue(report["ineligible_records"])
            with self.assertRaisesRegex(ValueError, "allowlisted"):
                seed(root / "research_cache.json", pack_path)

    def test_release_tool_requires_approved_record_and_preserves_exact_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_path = root / "research_cache.json"
            manifest_path = root / "releases.json"
            record = {
                "record_id": "approved-lighting", "url": "https://www.abcb.gov.au/source", "publisher": "ABCB",
                "retrieved_at": "2026-09-14T00:00:00+10:00", "content_hash": "exact-v1",
                "category": "lighting_density_default", "value": 10, "unit": "W/m2",
                "scope": {"country": "AU", "room_use": "retail"}, "citation": "Table 1",
                "review_status": "approved", "reviewed_by": "HVAC reviewer",
                "expiry": "2027-09-14T00:00:00+10:00", "source_pack_version": "au-cooling-v1",
                "bindings": [{"target": "room.lighting_w_m2", "value": 10, "unit": "W/m2", "scope": {"room_use": "retail"}}],
            }
            cache = upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)
            cache_path.write_text(json.dumps(cache), encoding="utf-8")
            result = release(cache_path, manifest_path, pack_version="au-cooling-v1", record_ids=["approved-lighting"], engineer_name="HVAC Engineer", engineer_credential="CPEng", scope={"country": "AU"}, approval_reference="Release review", expiry="2027-12-31T00:00:00+00:00")
            self.assertEqual(result["releases"][0]["records"], [{"record_id": "approved-lighting", "content_hash": "exact-v1"}])

            record["review_status"] = "proposed"
            cache_path.write_text(json.dumps(upsert_record({**empty_research_cache(), "source_pack_version": "au-cooling-v1"}, record)), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Only engineer-approved"):
                release(cache_path, manifest_path, pack_version="au-cooling-v1", record_ids=["approved-lighting"], engineer_name="HVAC Engineer", engineer_credential="CPEng", scope={"country": "AU"}, approval_reference="Release review", expiry="2027-12-31T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
