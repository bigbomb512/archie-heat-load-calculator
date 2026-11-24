"""Envelope review failures: a reply whose inferences are text lines is read instead of failing the skill, and a
failed step is reported by name and reason with the steps that use it. No AI is called."""

import unittest

from backend import skill_workflow_service as service

# One of the eight lines Butcher Buffet's ceiling-height reply used (2026-10-17), shortened.
TEXT_INFERENCE = ("field=heights[room-use:unassigned-level:kitchen].height_mm | value=2600 | unit=mm | "
                  "method=direct ceiling tag 'CH:2600MM' on the RCP | formula=direct read | operands=2600 | "
                  "citation_ids=p22,p29")


def _reply(inferences):
    spec = next(row for row in service.load_subskill_registry()["subskills"] if row["id"] == "ceiling_height_volume")
    return spec, {
        "subskill_id": "ceiling_height_volume", "subskill_version": spec["version"], "input_fingerprint": "0" * 64,
        "status": "needs_review", "affected_ids": [], "observations": [], "confidence": 0.35, "alternatives": [],
        "unresolved_fields": [], "remediation": [],
        # Written as text sentences, as the real reply did.
        "citations": ["p22 | 204 REFLECTIVE CEILING PLAN, rev B: 'CH:2600MM'", "p29 | kitchen elevation: '2600 AFFL'"],
        "inferences": inferences,
        "proposal_fields": {"heights": [{"room_id": "room-use:unassigned-level:kitchen", "height_mm": 2600,
                                         "scope": "room", "source_page": 22, "volume_m3": None, "formula": None}]},
    }


class TextInferenceTests(unittest.TestCase):
    def test_a_text_line_becomes_an_inference_object(self):
        item = service._inference_from_text(TEXT_INFERENCE)
        self.assertEqual(item["field"], "heights[room-use:unassigned-level:kitchen].height_mm")
        self.assertEqual((item["value"], item["unit"], item["citation_ids"]), (2600, "mm", ["p22", "p29"]))
        self.assertEqual(service._inference_from_text("field=volume_m3 | value=null | unit=m3")["value"], None)
        self.assertEqual(service._inference_from_text("field=x | method=a | b | value=1.5")["method"], "a | b")
        self.assertEqual(service._inference_from_text("no field here"), "no field here")

    def test_a_reply_with_text_line_inferences_passes_the_checks_with_a_note(self):
        spec, reply = _reply([TEXT_INFERENCE])
        result = service._validate_subskill_output(spec, reply, service.load_subskill_registry(), None)
        result = result if isinstance(result, dict) else reply
        self.assertEqual(result["inferences"][0]["value"], 2600)
        self.assertIn("Read 1 inference written as text lines.", result["remediation"])

    def test_a_text_line_without_a_field_name_still_fails_with_the_reason(self):
        spec, reply = _reply(["value=2600 | unit=mm"])
        with self.assertRaises(service.SubskillValidationError) as caught:
            service._validate_subskill_output(spec, reply, service.load_subskill_registry(), None)
        self.assertEqual(caught.exception.check, "inference_shape")


class FailedStepTests(unittest.TestCase):
    def subskills(self, failed_row):
        return [
            {"id": "room_boundaries_areas", "status": "needs_review", "prerequisites": []},
            {"id": "ceiling_height_volume", "status": "failed", "prerequisites": ["room_boundaries_areas"], **failed_row},
            {"id": "surface_area", "status": "needs_review", "prerequisites": ["surface_inventory", "ceiling_height_volume"]},
            {"id": "infiltration", "status": "not_in_scope", "prerequisites": ["ceiling_height_volume"]},
        ]

    def test_the_failed_step_is_named_with_its_reason_and_the_steps_that_use_it(self):
        steps = service._failed_steps(self.subskills({
            "error_code": "subskill_output_invalid", "failure_phase": "proposal_validation",
            "validation_detail": "Each inference must be an object with a field name.",
            "remediation": ["Each inference must be an object with a field name."]}))
        self.assertEqual(steps, [{
            "id": "ceiling_height_volume", "label": "Ceiling heights", "kind": "answer_rejected",
            "reason": "the AI's answer didn't pass the checks (Each inference must be an object with a field name.)",
            "uses_it": ["Wall, roof and floor areas"]}])            # out-of-scope infiltration is not listed

    def test_connection_timeout_and_usage_failures_are_told_apart(self):
        cases = {
            "connection": {"error_code": "subskill_execution_failed",
                           "remediation": ["The connection to the openrouter AI service failed (Bad file descriptor)."]},
            "timeout": {"error_code": "subskill_execution_failed",
                        "remediation": ["No reply from the AI within 300 seconds, so the request was stopped."]},
            "usage_limit": {"error_code": "skill_provider_usage_limit", "remediation": ["limit"]},
            "unreadable_reply": {"error_code": "skill_provider_invalid_json", "remediation": ["x"]},
        }
        for kind, row in cases.items():
            self.assertEqual(service._failed_steps(self.subskills(row))[0]["kind"], kind)
        connection = service._failed_steps(self.subskills(cases["connection"]))[0]["reason"]
        self.assertIn("nothing was learned from the drawings", connection)

    def test_a_step_blocked_because_its_request_was_too_large_is_named(self):
        rows = self.subskills({"error_code": "prompt_over_budget"})
        rows[1]["status"] = "blocked"
        step = service._failed_steps(rows)[0]
        self.assertEqual((step["id"], step["kind"]), ("ceiling_height_volume", "too_large"))
        self.assertIn("too large to send", step["reason"])
        # A step blocked for any other reason (waiting on an earlier step) is not reported as stopped.
        rows = self.subskills({"error_code": ""})
        rows[1]["status"] = "blocked"
        self.assertEqual(service._failed_steps(rows), [])


class RoomEvidenceSizeTests(unittest.TestCase):
    def test_house_evidence_is_sent_once_not_with_every_room(self):
        import json, tempfile
        from pathlib import Path
        house = [{"page": 2, "excerpt": "BASIX commitments " + "x" * 280, "basis": "dwelling/residential drawing term"}]
        own = {"page": 7, "kind": "reviewed_pdf_text", "excerpt": "Cellar"}
        records = [{"room_id": f"room-use:basement:room-{n}", "original_label": f"Room {n}", "level_name": "Basement",
                    "status": "inferred", "evidence": [own, *house]} for n in range(40)]
        with tempfile.TemporaryDirectory() as root:
            Path(root, "room_use_resolution.json").write_text(json.dumps(
                {"records": records, "dwelling_context": {"is_dwelling": True, "evidence": house}}))
            packet, _ = service._subskill_records("room_identity_use", {"id": "p", "review_dir": root})
        self.assertEqual(packet["house_evidence_for_every_room"], house)
        self.assertEqual({json.dumps(row["evidence"]) for row in packet["rooms"]}, {json.dumps([own])})
        self.assertEqual(len(packet["rooms"]), 40)

    def test_a_non_house_keeps_each_rooms_evidence_and_adds_no_house_section(self):
        import json, tempfile
        from pathlib import Path
        row = {"page": 3, "excerpt": "Food prep", "basis": "dwelling/residential drawing term"}
        with tempfile.TemporaryDirectory() as root:
            Path(root, "room_use_resolution.json").write_text(json.dumps(
                {"records": [{"room_id": "r", "evidence": [row]}], "dwelling_context": {"is_dwelling": False, "evidence": []}}))
            packet, _ = service._subskill_records("room_identity_use", {"id": "p", "review_dir": root})
        self.assertNotIn("house_evidence_for_every_room", packet)
        self.assertEqual(packet["rooms"][0]["evidence"], [row])


if __name__ == "__main__":
    unittest.main()
