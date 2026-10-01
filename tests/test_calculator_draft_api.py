#!/usr/bin/env python3

"""Persistence, optimistic concurrency, and recovery checks for the bridge API."""

import json
from pathlib import Path
import tempfile
import unittest

from ai.envelope import empty_envelope_library, empty_envelope_model
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from ai import reviewer_room_geometry
from ai.calculator_draft import DraftConflict
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

    def add_room_trace_fixture(self):
        data = source_data()
        building = data["building"]
        building["spaces"] = []
        building["levels"] = []
        coverage = {"levels": [{"level_name": "Ground", "page_numbers": [1]}],
                    "pages": [{"page": 1, "drawing_number": "A-01", "level_name": "Ground"}],
                    "page_roles": []}
        vector_page = {"page": 1, "coordinate_systems": {"image_px": {"image_width": 1000, "image_height": 800}},
                       "confirmation_line_candidates": []}
        pdf_fp, vector_fp = "pdf-room-fixture", reviewer_room_geometry.fingerprint(vector_page)
        trace = {"trace_id": "trace-shop", "room_id": "room-use:ground:shop", "room_label": "Shop",
                 "level_name": "Ground", "page": 1,
                 "points_image_px": [[100, 100], [300, 100], [300, 300], [100, 300], [100, 100]],
                 "snapped_line_ids": [None] * 5,
                 "calibration": reviewer_room_geometry.calibration([[100, 600], [200, 600]], 1000, 100, 3.5277777778),
                 "reviewer": "QA-1", "note": "Synthetic current trace", "status": "geometry_proposed",
                 "source_fingerprints": {"source_pdf": pdf_fp, "vector_page": vector_fp}}
        rows = [
            {"room_id": "room-use:ground:shop", "original_label": "Shop", "level_name": "Ground",
             "evidence": [{"reference": "A-01 page 1", "page": 1, "excerpt": "SHOP"}]},
            {"room_id": "room-use:ground:store", "original_label": "Store", "level_name": "Ground",
             "evidence": [{"reference": "A-01 page 1", "page": 1, "excerpt": "STORE"}]},
        ]
        proof = {"entity_id": "proof-shop", "kind": "room_geometry_proof", "room_source_id": trace["room_id"],
                 "geometry_status": "geometry_proposed", "extraction_method": "reviewer_traced_boundary",
                 "source": {"page": 1, "drawing_number": "A-01"},
                 "value": {"area_m2": 20.0, "reviewer_trace_id": trace["trace_id"],
                           "calibration": trace["calibration"], "source_fingerprints": trace["source_fingerprints"]}}
        artifacts = {
            "building_evidence": building, "drawing_coverage": coverage,
            "ai_input": {"source_pdf_fingerprint": pdf_fp},
            "vector_geometry": {"geometry_key_points": {"pages": [vector_page]}},
            "room_use_resolution": {"records": rows},
            "reviewer_room_geometry": reviewer_room_geometry.validate_artifact({"records": [trace]}),
            "calculation_input_evidence": {"geometry_resolution": {"entities": [proof]}},
        }
        for name, value in artifacts.items():
            (self.root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")

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

    def test_old_coverage_level_method_marks_draft_stale_and_blocks_rebuild(self):
        draft_service.post(WebStub, self.project, {"action": "build"})
        (self.root / "drawing_coverage.json").write_text(json.dumps({"version": 4, "levels": []}), encoding="utf-8")
        current = draft_service.get(WebStub, self.project)
        self.assertEqual(current["status"], "stale")
        self.assertIn("Rebuild drawing coverage", current["stale_reasons"][0])
        with self.assertRaisesRegex(ValueError, "Rebuild drawing coverage"):
            draft_service.post(WebStub, self.project, {"action": "build"})

    def test_room_use_trace_area_applies_with_full_provenance(self):
        self.add_room_trace_fixture()
        built = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        self.assertEqual(len(built["candidates"]["rooms"]), 2)
        self.assertEqual(len(built["candidates"]["zones"]), 2)
        areas = [row for row in built["candidates"]["room_inputs"] if row["kind"] == "area"]
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0]["value"]["area_m2"], 20.0)
        floor = built["candidates"]["floors"][0]
        decisions = {floor["candidate_id"]: {"decision": "accept", "reviewer": "ENG-1"}}
        for zone in built["candidates"]["zones"]:
            decisions[zone["candidate_id"]] = {"decision": "edit", "reviewer": "ENG-1",
                "source": "Mapped to reviewed Ground floor", "citations": zone["citations"], "value": zone["value"]}
        shop = next(row for row in built["candidates"]["rooms"] if row["room_source"] == "room_use_resolution" and row["value"]["name"] == "Shop")
        store = next(row for row in built["candidates"]["rooms"] if row["value"]["name"] == "Store")
        decisions[shop["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        decisions[store["candidate_id"]] = {"decision": "reject", "reviewer": "ENG-1"}
        decisions[areas[0]["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        reviewed = draft_service.post(WebStub, self.project, {"action": "save_review", "expected_revision": built["revision"], "decisions": decisions})["calculator_draft"]
        revision = reviewed["revision"]
        preview = draft_service.post(WebStub, self.project, {"action": "preview_apply", "expected_revision": revision})
        applied = draft_service.post(WebStub, self.project, {"action": "apply", "expected_revision": revision, "preview_token": preview["preview_token"]})
        model = json.loads((self.root / "hourly_load_model.json").read_text())
        room = next(row for row in model["rooms"] if row["room_id"] == shop["value"]["room_id"])
        self.assertEqual(room["area_m2"], 20.0)
        provenance = room["bridge_provenance"][shop["candidate_id"]]
        self.assertEqual(provenance["geometry_status"], "geometry_confirmed")
        self.assertEqual(provenance["geometry_acceptance"]["proof_id"], "proof-shop")
        self.assertEqual(provenance["geometry_acceptance"]["reviewer"], "ENG-1")
        self.assertEqual(provenance["geometry_acceptance"]["calibration"]["status"], "agreed")
        self.assertEqual(applied["readiness"]["status"], "blocked")

    def test_changed_vector_page_blocks_prior_geometry_acceptance_on_apply(self):
        self.add_room_trace_fixture()
        built = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        floor = built["candidates"]["floors"][0]
        decisions = {floor["candidate_id"]: {"decision": "accept", "reviewer": "ENG-1"}}
        for zone in built["candidates"]["zones"]:
            decisions[zone["candidate_id"]] = {"decision": "edit", "reviewer": "ENG-1",
                "source": "Mapped to reviewed Ground floor", "citations": zone["citations"], "value": zone["value"]}
        shop = next(row for row in built["candidates"]["rooms"] if row["value"]["name"] == "Shop")
        area = next(row for row in built["candidates"]["room_inputs"] if row["kind"] == "area")
        decisions[shop["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        decisions[area["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        reviewed = draft_service.post(WebStub, self.project, {"action": "save_review", "expected_revision": built["revision"], "decisions": decisions})["calculator_draft"]
        preview = draft_service.post(WebStub, self.project, {"action": "preview_apply", "expected_revision": reviewed["revision"]})
        vector_path = self.root / "vector_geometry.json"
        vector = json.loads(vector_path.read_text())
        vector["geometry_key_points"]["pages"][0]["changed"] = True
        vector_path.write_text(json.dumps(vector), encoding="utf-8")
        with self.assertRaisesRegex(DraftConflict, "Source evidence changed"):
            draft_service.post(WebStub, self.project, {"action": "apply", "expected_revision": reviewed["revision"], "preview_token": preview["preview_token"]})

    def test_pre_upgrade_draft_explains_staleness_and_rebuild_retains_only_matching_decisions(self):
        built = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        floor, zone = built["candidates"]["floors"][0], built["candidates"]["zones"][0]
        built["decisions"] = {
            floor["candidate_id"]: {"decision": "accept", "reviewer": "ENG-1", "candidate_fingerprint": floor["fingerprint"]},
            zone["candidate_id"]: {"decision": "accept", "reviewer": "ENG-1", "candidate_fingerprint": "old-candidate-content"},
        }
        for name in ("ai_input", "vector_geometry", "room_use_resolution", "reviewer_room_geometry", "room_registry"):
            built["source_fingerprints"].pop(name, None)
        (self.root / "calculator_draft.json").write_text(json.dumps(built), encoding="utf-8")

        stale = draft_service.get(WebStub, self.project)
        self.assertEqual(stale["status"], "stale")
        self.assertIn("predates room and trace freshness tracking", stale["stale_reasons"][0])
        self.assertFalse(draft_service.freshness(self.root, built))

        rebuilt = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        self.assertEqual(rebuilt["decisions"][floor["candidate_id"]]["decision"], "accept")
        self.assertNotIn(zone["candidate_id"], rebuilt["decisions"])
        self.assertTrue(any(row["candidate_id"] == zone["candidate_id"] for row in rebuilt["review_history"]))
        self.assertTrue(draft_service.freshness(self.root, rebuilt))

    def test_room_inference_proposal_change_stales_the_draft_registry(self):
        built = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        proposal = {"local_room_inference_proposal": {"rooms": [{
            "room_id": "inference:ground:office", "label": "Office", "level_name": "Ground",
            "source_pages": [1], "evidence": [{"page": 1, "excerpt": "OFFICE"}],
        }]}}
        (self.root / "ai_preliminary_run.json").write_text(json.dumps(proposal), encoding="utf-8")
        status = draft_service.get(WebStub, self.project)
        self.assertEqual(status["status"], "stale")
        self.assertIn("room_registry", status["stale_reasons"][0])
        rebuilt = draft_service.post(WebStub, self.project, {"action": "build"})["calculator_draft"]
        self.assertTrue(draft_service.freshness(self.root, rebuilt))
        self.assertIn("Office", [row["value"]["name"] for row in rebuilt["candidates"]["rooms"]])

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
