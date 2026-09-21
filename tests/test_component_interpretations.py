"""Regression coverage for proposal-only AI component interpretations."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai import component_interpretations as interpretations
from backend import calculation_extraction_service as service


def source(page=1):
    return {"page": page, "drawing_number": "A-101", "reference": "A-101", "excerpt": "Studio A"}


def inputs(label="Studio A", fingerprint="fusion-1"):
    fusion = {
        "source_fingerprint": "pdf-1", "fingerprint": fingerprint,
        "entities": [{
            "entity_id": "evidence_room_01", "kind": "room", "label": label,
            "value": {"id": "space_01", "geometry_reference": "loop_01"},
            "source": source(), "citations": [source()], "evidence_ids": ["space_01"],
        }],
    }
    evidence = {"source_fingerprint": "pdf-1", "fingerprint": "evidence-1", "candidates": [{
        "candidate_id": "candidate_schedule_01", "category": "schedule", "target": "rooms.room_01.schedule",
        "field": "schedule", "source": source(),
    }]}
    draft = {"fingerprint": "draft-1", "candidates": {"zones": [{
        "candidate_id": "draft_zone_01", "kind": "zone", "value": {"zone_id": "zone_01", "name": "Studio A"}, "citations": [source()],
    }]}}
    return fusion, evidence, draft


def proposal(component_id, *, name="External studio", score=0.86, provider_style="flat"):
    value = {
        "component_id": component_id, "display_name": name, "confidence_score": score,
        "rationale": "Plan label and the cited geometry loop identify the same room.",
        "evidence_refs": [source()],
    }
    if provider_style == "nested":
        value["provider"] = {"provider": "placeholder", "model": "local", "prompt_policy_fingerprint": "policy-v1"}
    else:
        value.update(provider_name="placeholder", model="local", prompt_policy_fingerprint="policy-v1")
    return value


class ComponentInterpretationTests(unittest.TestCase):
    def setUp(self):
        self.fusion, self.evidence, self.draft = inputs()
        self.artifact = interpretations.build_artifact(
            fusion=self.fusion, calculation_evidence=self.evidence, calculator_draft=self.draft,
        )
        self.room_id = next(row["component_id"] for row in self.artifact["interpretations"] if row["canonical_type"] == "room")

    def test_renaming_preserves_immutable_identity_and_source_alias(self):
        before = deepcopy(next(row for row in self.artifact["interpretations"] if row["component_id"] == self.room_id))
        updated, component_id = interpretations.save_reviewer_change(
            self.artifact, {"component_id": self.room_id, "display_name": "Main showroom", "lock_display_name": True}, actor="reviewer_a",
        )
        after = next(row for row in updated["interpretations"] if row["component_id"] == component_id)
        self.assertEqual(before["component_id"], after["component_id"])
        self.assertEqual(before["source_component_ids"], after["source_component_ids"])
        self.assertEqual(after["original_label"], "Studio A")
        self.assertEqual(after["display_name"], "Main showroom")
        self.assertNotIn("display_name", updated["source_artifact_fingerprints"])

    def test_confidence_validation_and_bands(self):
        self.assertEqual(interpretations.confidence_band(0), "low")
        self.assertEqual(interpretations.confidence_band(0.50), "medium")
        self.assertEqual(interpretations.confidence_band(0.80), "high")
        for value in (-0.01, 1.01, float("inf"), True):
            with self.assertRaises(ValueError):
                interpretations.validate_confidence(value)

    def test_locked_fields_keep_values_and_record_competing_ai_update(self):
        locked, _ = interpretations.save_reviewer_change(
            self.artifact, {"component_id": self.room_id, "display_name": "Reviewer room", "confidence_score": 0.2,
                            "lock_display_name": True, "lock_confidence": True}, actor="reviewer_a",
        )
        current = interpretations.source_artifact_fingerprints(self.fusion, self.evidence, self.draft)
        updated, affected, conflicts = interpretations.apply_ai_updates(
            locked, [proposal(self.room_id)], source_artifact_fingerprints_value=current, actor="placeholder_ai",
        )
        row = next(row for row in updated["interpretations"] if row["component_id"] == self.room_id)
        self.assertEqual(affected, [self.room_id])
        self.assertEqual(conflicts, [self.room_id])
        self.assertEqual(row["display_name"], "Reviewer room")
        self.assertEqual(row["confidence_score"], 0.2)
        self.assertEqual(row["latest_ai_proposal"]["display_name"], "External studio")
        self.assertEqual(row["competing_updates"][-1]["fields"], ["display_name", "confidence"])

    def test_unlock_restores_the_newest_valid_ai_proposal(self):
        locked, _ = interpretations.save_reviewer_change(
            self.artifact, {"component_id": self.room_id, "display_name": "Reviewer room", "lock_display_name": True}, actor="reviewer_a",
        )
        current = interpretations.source_artifact_fingerprints(self.fusion, self.evidence, self.draft)
        updated, _, _ = interpretations.apply_ai_updates(locked, [proposal(self.room_id)], source_artifact_fingerprints_value=current)
        restored, _ = interpretations.unlock_and_restore_ai(updated, self.room_id, "display_name", actor="reviewer_a")
        row = next(row for row in restored["interpretations"] if row["component_id"] == self.room_id)
        self.assertIsNone(row["reviewer_locks"]["display_name"])
        self.assertEqual(row["display_name"], "External studio")
        self.assertEqual(row["display_name_origin"], "ai")

    def test_evidence_reordering_preserves_ids_and_interpretations(self):
        reversed_fusion, reversed_evidence, reversed_draft = inputs()
        reversed_fusion["entities"].reverse()
        reversed_evidence["candidates"].reverse()
        rows = interpretations.build_artifact(
            fusion=reversed_fusion, calculation_evidence=reversed_evidence, calculator_draft=reversed_draft,
        )["interpretations"]
        self.assertEqual([row["component_id"] for row in self.artifact["interpretations"]], [row["component_id"] for row in rows])

    def test_changed_source_or_prompt_evidence_makes_interpretation_stale(self):
        changed = deepcopy(self.fusion)
        changed["fingerprint"] = "fusion-2"
        self.assertFalse(interpretations.is_current(self.artifact, fusion=changed, calculation_evidence=self.evidence, calculator_draft=self.draft))
        current = interpretations.source_artifact_fingerprints(self.fusion, self.evidence, self.draft)
        updated, _, _ = interpretations.apply_ai_updates(self.artifact, [proposal(self.room_id)], source_artifact_fingerprints_value=current)
        row = next(row for row in updated["interpretations"] if row["component_id"] == self.room_id)
        self.assertEqual(row["provider"]["prompt_policy_fingerprint"], "policy-v1")

    def test_manual_and_provider_payloads_normalise_identically(self):
        current = interpretations.source_artifact_fingerprints(self.fusion, self.evidence, self.draft)
        flat, _, _ = interpretations.apply_ai_updates(deepcopy(self.artifact), [proposal(self.room_id, provider_style="flat")], source_artifact_fingerprints_value=current)
        nested, _, _ = interpretations.apply_ai_updates(deepcopy(self.artifact), [proposal(self.room_id, provider_style="nested")], source_artifact_fingerprints_value=current)
        flat_row = next(row for row in flat["interpretations"] if row["component_id"] == self.room_id)
        nested_row = next(row for row in nested["interpretations"] if row["component_id"] == self.room_id)
        self.assertEqual(flat_row["display_name"], nested_row["display_name"])
        self.assertEqual(flat_row["confidence_score"], nested_row["confidence_score"])
        self.assertEqual(flat_row["provider"], nested_row["provider"])

    def test_service_writes_audit_event_only_for_real_review_change(self):
        class Productization:
            def __init__(self): self.events = []
            def append_audit_event(self, root, **kwargs): self.events.append(kwargs)

        class Web:
            productization = Productization()
            @staticmethod
            def safe_link(path): return "/safe/" + Path(path).name

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (("architect_evidence_fusion", self.fusion), ("calculation_input_evidence", self.evidence), ("calculator_draft", self.draft), ("component_interpretations", self.artifact)):
                (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
            project = {"id": "project_1", "review_dir": str(root)}
            result = service.post(Web, project, {
                "action": "save_component_interpretation_review", "reviewer": "reviewer_a",
                "interpretation": {"component_id": self.room_id, "display_name": "Reviewed studio"},
            })
            self.assertEqual(result["status"], "current")
            self.assertEqual(len(Web.productization.events), 1)
            self.assertEqual(Web.productization.events[0]["action"], "component_interpretation_review_saved")


if __name__ == "__main__":
    unittest.main()
