"""Regression cases reduced from the saved Turramurra live2/live3 outline replies."""

import json
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai import room_outline
from backend import autonomous_tasks_service, room_proposal


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "turramurra_outline_replies.json").read_text())


class SavedOutlineReplyTests(unittest.TestCase):
    def validate_case(self, case):
        saved = FIXTURES[case]
        diagnostics = {}
        outlines = room_outline.validate_outline_reply(saved["reply"], saved["packet"]["room_names"],
            saved["packet"]["transform"], diagnostics=diagnostics)
        return outlines, diagnostics

    def test_bad_garage_does_not_discard_good_rooms(self):
        outlines, notes = self.validate_case("turra_live2_page_8")
        self.assertEqual(len(outlines), 15)
        self.assertTrue(any(row["room"] == "Kitchen" for row in outlines))
        self.assertTrue(any(row["room"] == "Garage" and "three corner points" in row["reason"]
                            for row in notes["not_drawn"]))

    def test_extra_names_are_recorded_without_being_applied(self):
        for case, extra in (("turra_live2_page_9", "ROBE"), ("turra_live3_page_8", "PDR")):
            with self.subTest(case=case):
                outlines, notes = self.validate_case(case)
                self.assertNotIn(extra, [row["room"] for row in outlines])
                self.assertIn(extra, [row["room"] for row in notes["extra_rooms"]])
                self.assertTrue(outlines)

    def test_asked_name_matches_after_case_normalisation(self):
        saved = FIXTURES["turra_live3_page_9"]
        reply = {"rooms": [{**saved["reply"]["rooms"][0], "room": "BALCONY"}]}
        self.assertEqual(room_outline.validate_outline_reply(reply, saved["packet"]["room_names"],
                         saved["packet"]["transform"])[0]["room"], "Balcony")

    def test_all_bad_rooms_reject_whole_reply(self):
        saved = FIXTURES["turra_live2_page_8"]
        reply = {"rooms": [row for row in saved["reply"]["rooms"] if row["room"] == "GARAGE"]}
        with self.assertRaisesRegex(ValueError, "no usable rooms"):
            room_outline.validate_outline_reply(reply, saved["packet"]["room_names"], saved["packet"]["transform"])

    def test_malformed_reply_rejected(self):
        saved = FIXTURES["turra_live3_page_8"]
        with self.assertRaisesRegex(ValueError, "rooms list"):
            room_outline.validate_outline_reply({"rooms": "bad"}, saved["packet"]["room_names"],
                                                saved["packet"]["transform"])

    def test_caller_applies_good_rooms_and_records_failures_and_extras(self):
        for case, expected in (("turra_live2_page_8", "Garage"), ("turra_live3_page_8", "PDR")):
            with self.subTest(case=case), TemporaryDirectory() as temporary:
                saved = FIXTURES[case]
                packet = {**saved["packet"], "task": "P0_room_outlines"}
                autonomous_tasks_service._create_run(Path(temporary), "P0_room_outlines", "page-8", packet, "Draw rooms.")
                applied = []

                def apply(_web, _project, _root, record, validated, **_kwargs):
                    applied.extend(row["room"] for row in validated)
                    record.update({"status": "below_accuracy_bar", "applied_value": {"rooms": []}})
                    return record

                with patch.object(autonomous_tasks_service, "_apply_p0_outlines", side_effect=apply), \
                     patch.object(autonomous_tasks_service, "get", return_value={}):
                    autonomous_tasks_service._post(None, {"id": "replay", "review_dir": temporary},
                        {"action": "validate_apply", "task": "P0_room_outlines", "target": "page-8",
                         "reply": json.dumps(saved["reply"]), "defer": True})
                record = autonomous_tasks_service._current_task(Path(temporary), "P0_room_outlines", "page-8")
                self.assertEqual(record["status"], "below_accuracy_bar")
                self.assertTrue(applied)
                self.assertNotIn(expected, applied)
                notes = record["applied_value"]
                if expected == "Garage":
                    self.assertTrue(any(row["room"] == expected and "three corner points" in row["reason"]
                                        for row in notes["not_drawn"]))
                else:
                    self.assertIn(expected, [row["room"] for row in notes["extra_rooms"]])

    def test_partial_apply_replaces_only_drawn_room_traces(self):
        record = {"packet": {"page": 9, "room_names": ["Balcony", "Bedroom 3"],
                             "calibration": {"mm_per_px": 10}},
                  "run_id": "run", "quality_label": "AI-determined", "accuracy": {"auto_apply": False}}
        validated = [{"room": "Balcony", "points": [(0, 0), (10, 0), (10, 10), (0, 10)]}]
        geometry = autonomous_tasks_service.reviewer_room_geometry_service
        with patch.object(room_proposal, "save_use_named_rooms"), \
             patch.object(autonomous_tasks_service, "_page_level", return_value="First"), \
             patch.object(autonomous_tasks_service, "_outlined_names_to_add", return_value=[]), \
             patch.object(autonomous_tasks_service, "_p0_context", return_value={}), \
             patch.object(autonomous_tasks_service, "_p0_walls_areas", return_value=(None, [], [])), \
             patch.object(autonomous_tasks_service, "_tenancy_polygon", return_value=None), \
             patch.object(autonomous_tasks_service, "_p0_trace_rows", return_value=[]), \
             patch.object(geometry, "_rooms", return_value=[]), \
             patch.object(geometry, "current_records", return_value=[]), \
             patch.object(geometry, "persist_ai_determined_traces", return_value=[]) as persist:
            autonomous_tasks_service._apply_p0_outlines(None, {"review_dir": "/tmp"}, Path("/tmp"),
                                                        record, validated, defer=True)
        self.assertEqual(persist.call_args.kwargs["replace_room_labels"], ["Balcony"])


if __name__ == "__main__":
    unittest.main()
