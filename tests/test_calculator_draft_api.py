#!/usr/bin/env python3

"""Persistence, optimistic concurrency, and recovery checks for the bridge API."""

import json
from pathlib import Path
import tempfile
import unittest

from ai.envelope import empty_envelope_library, empty_envelope_model
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from backend import draft_service
from tests.test_calculator_draft import source_data


class WebStub:
    @staticmethod
    def safe_link(path):
        return str(path)


class CalculatorDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        data = source_data()
        artifacts = {
            "thermal_model": data["thermal"], "thermal_evidence": {},
            "building_evidence": data["building"], "drawing_coverage": data["coverage"],
            "hourly_load_model": empty_hourly_load_model(), "schedule_library": empty_schedule_library(),
            "envelope_library": empty_envelope_library(), "envelope_model": empty_envelope_model(),
        }
        for name, artifact in artifacts.items():
            (self.root / f"{name}.json").write_text(json.dumps(artifact), encoding="utf-8")
        self.project = {"id": "p", "review_dir": str(self.root)}

    def tearDown(self):
        self.temp.cleanup()

    def build_and_review(self):
        built = draft_service.post(WebStub, self.project, {"action": "build"})
        draft = built["calculator_draft"]
        ids = [item["candidate_id"] for group in ("floors", "zones", "rooms") for item in draft["candidates"][group]]
        return draft_service.post(WebStub, self.project, {
            "action": "save_review", "expected_revision": draft["revision"],
            "decisions": {cid: {"decision": "accept", "reviewer": "ENG-1"} for cid in ids},
        })

    def test_build_review_preview_apply_and_repeat_noop(self):
        reviewed = self.build_and_review()
        revision = reviewed["calculator_draft"]["revision"]
        before = (self.root / "hourly_load_model.json").read_bytes()
        preview = draft_service.post(WebStub, self.project, {"action": "preview_apply", "expected_revision": revision})
        self.assertTrue(preview["preview_token"])
        self.assertEqual((self.root / "hourly_load_model.json").read_bytes(), before)
        applied = draft_service.post(WebStub, self.project, {"action": "apply", "expected_revision": revision, "preview_token": preview["preview_token"]})
        self.assertEqual(len(applied["apply_summary"]["created"]), 3)
        draft = applied["calculator_draft"]
        receipt = draft["application_receipts"][-1]
        self.assertEqual(receipt["affected_targets"], ["hourly_load_model"])
        self.assertIn("previous_artifact_revisions", receipt)
        self.assertIn("new_artifact_revisions", receipt)
        report_before = (self.root / "hourly_load_model.json").stat().st_mtime_ns
        preview_again = draft_service.post(WebStub, self.project, {"action": "preview_apply", "expected_revision": draft["revision"]})
        repeated = draft_service.post(WebStub, self.project, {"action": "apply", "expected_revision": draft["revision"], "preview_token": preview_again["preview_token"]})
        self.assertEqual(repeated["changed_artifacts"], [])
        self.assertEqual(len(repeated["apply_summary"]["already_present"]), 3)
        self.assertEqual((self.root / "hourly_load_model.json").stat().st_mtime_ns, report_before)

    def test_preview_rejects_stale_revision_and_input_fingerprint(self):
        reviewed = self.build_and_review()
        revision = reviewed["calculator_draft"]["revision"]
        preview = draft_service.post(WebStub, self.project, {"action": "preview_apply", "expected_revision": revision})
        (self.root / "drawing_coverage.json").write_text(json.dumps({"levels": []}), encoding="utf-8")
        with self.assertRaises(draft_service.DraftConflict):
            draft_service.post(WebStub, self.project, {"action": "apply", "expected_revision": revision, "preview_token": preview["preview_token"]})

    def test_failed_batch_write_recovers_all_originals(self):
        original = {"hourly_load_model.json": b'{"old": 1}', "schedule_library.json": b'{"old": 2}'}
        for name, content in original.items(): (self.root / name).write_bytes(content)
        original_atomic = draft_service.atomic_bytes
        calls = {"count": 0}

        def fail_second(path, content):
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("simulated write failure")
            return original_atomic(path, content)

        draft_service.atomic_bytes = fail_second
        try:
            with self.assertRaises(OSError):
                draft_service.commit(self.root, {"hourly_load_model.json": {"new": 1}, "schedule_library.json": {"new": 2}})
        finally:
            draft_service.atomic_bytes = original_atomic
        self.assertEqual((self.root / "hourly_load_model.json").read_bytes(), original["hourly_load_model.json"])
        self.assertEqual((self.root / "schedule_library.json").read_bytes(), original["schedule_library.json"])


if __name__ == "__main__":
    unittest.main()
