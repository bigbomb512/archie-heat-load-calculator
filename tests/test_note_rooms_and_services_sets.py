"""Gaps found running new drawing sets on 2026-11-02 (Happy Cap, Global Exchange's mechanical set).

- A room named only in a general note ("ALL NEW WALLS IN DRINK BAR AREA ...") stays a clue, is flagged to the AI steps,
  and is removed, labelled, when the plan doesn't show it (no floor, or under 2 m²). Rooms printed on the plan never are.
- A question the checker refused twice is settled as not answered: it no longer holds the job at the drawings stage.
- A set with no floor plan traces its rooms on a scaled services plan; tasks with nothing to ask are "not needed".
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import room_inference, room_outline, site_location_resolution
from backend import autonomous_tasks_service as tasks
from backend import room_inference_service


def words(page, line, y, x=1000.0):
    """One text line as the PDF text layer gives it: one item per word."""
    items = []
    for word in line.split():
        items.append({"text": word, "bbox": [x, y, x + 6 * len(word), y + 5]})
        x += 6 * len(word) + 4
    return items


class LabelEvidenceTests(unittest.TestCase):
    def page(self):
        items = (words(8, "ALL NEW WALLS IN DRINK BAR AREA TO BE OF SOLID", 313)        # Happy Cap's copied note
                 + words(8, "SHOP FLOOR TILE FLOOR TILING", 400)                         # Butcher Buffet's finish legend
                 + [{"text": "STORAGE", "bbox": [200, 500, 245, 505]}]                   # a printed plan label
                 + words(8, "KITCHEN FL 01", 600, 300))                                  # label beside a finish code
        return {"page": 8, "standalone_text_items": items}

    def test_note_and_legend_words_are_kept_as_note_evidence_and_plan_labels_as_labels(self):
        kinds = room_inference.room_label_evidence(self.page())
        self.assertEqual(kinds["bar"]["kind"], "note")
        self.assertIn("DRINK BAR AREA", kinds["bar"]["context"])
        self.assertEqual(kinds["shop"]["kind"], "note")
        self.assertEqual(kinds["storage"]["kind"], "plan_label")
        self.assertEqual(kinds["kitchen"]["kind"], "plan_label")
        # Nothing is dropped: notes are still evidence that a room exists (Butcher Buffet's Shop is only in its legend).
        self.assertEqual({label for label, _ in room_inference.room_label_items(self.page())}, {"Bar", "Shop", "Storage", "Kitchen"})

    def test_a_room_printed_anywhere_on_the_plan_is_not_note_only(self):
        page = self.page()
        page["standalone_text_items"].append({"text": "BAR", "bbox": [700, 800, 718, 805]})
        self.assertEqual(room_inference.room_label_evidence(page)["bar"]["kind"], "plan_label")

    def test_infer_marks_note_only_rooms(self):
        ai_input = {"drawing_set": {"pages": [{"page": 8, "title": "Floor plan", "detected_type": "floor_plan",
                                               "plan_role": "main_floor_plan", "scale": "1:50"}]}}
        rooms = {row["label"]: row for row in room_inference.infer(ai_input, {}, {"pages": [self.page()]})["rooms"]}
        self.assertEqual(rooms["Bar"]["label_evidence"], "note")
        self.assertIn("DRINK BAR AREA", rooms["Bar"]["note_text"])
        self.assertEqual(rooms["Storage"]["label_evidence"], "plan")


class NoteOnlyPromptTests(unittest.TestCase):
    def test_prompts_say_which_rooms_are_only_in_a_note(self):
        notes = {"Bar": "ALL NEW WALLS IN DRINK BAR AREA TO BE OF SOLID"}
        naming = room_outline.room_naming_prompt(["Bar", "Storage"], 3, notes)
        outline = room_outline.outline_prompt(["Bar"], (), notes)
        for prompt in (naming, outline):
            self.assertIn('Bar ("ALL NEW WALLS IN DRINK BAR AREA TO BE OF SOLID")', prompt)
            self.assertIn("may not be in this tenancy", prompt)
        self.assertIn("not_drawn rather than drawing a guess", outline)
        # Without note-only rooms the prompts are as before.
        self.assertNotIn("general note", room_outline.room_naming_prompt(["Bar"], 3))
        self.assertNotIn("general note", room_outline.outline_prompt(["Bar"]))


class RoomsNotOnPlanTests(unittest.TestCase):
    INFERRED = [
        {"label": "Bar", "label_evidence": "note", "note_text": "ALL NEW WALLS IN DRINK BAR AREA", "source_pages": [8, 11]},
        {"label": "Storage", "label_evidence": "plan", "source_pages": [7]},
        {"label": "Kitchen", "label_evidence": "note", "note_text": "ALL WALLS IN KITCHEN AND BOH AREA", "source_pages": [33]},
    ]

    def removed(self, records, inferred=INFERRED):
        with patch.object(tasks, "_inferred_room_data", return_value=inferred), \
                patch.object(tasks, "_all_current", return_value=records):
            return tasks.rooms_not_on_plan(Path("."))

    @staticmethod
    def outline(rooms, asked, status="below_accuracy_bar", not_drawn=()):
        return {"task": "P0_room_outlines", "target": "page-7", "status": status, "packet": {"room_names": asked},
                "applied_value": {"rooms": [{"label": label, "area_m2": area} for label, area in rooms],
                                  "not_drawn": [{"room": name, "reason": "not on the plan"} for name in not_drawn]}}

    def test_a_note_only_room_with_a_tiny_outline_is_removed_with_the_reason(self):
        removed = self.removed([self.outline([("Bar", 0.58), ("Storage", 0.8)], ["Bar", "Storage"])])
        self.assertEqual(list(removed), ["bar"])          # Storage is printed on the plan: never removed for size
        self.assertIn("mentioned only in a general note", removed["bar"]["reason"])
        self.assertIn("only 0.58 m²", removed["bar"]["reason"])
        self.assertEqual(removed["bar"]["pages"], [8, 11])

    def test_a_note_only_room_the_plan_doesnt_show_is_removed(self):
        removed = self.removed([self.outline([], ["Bar"], not_drawn=["Bar"])])
        self.assertIn("the plan shows no such room", removed["bar"]["reason"])

    def test_a_note_only_room_with_real_floor_is_kept(self):
        # Casa Nova's Kitchen is only in a note but is a real room: an outline of 2 m² or more keeps it.
        self.assertEqual(self.removed([self.outline([("Kitchen", 2.0), ("Bar", 5.0)], ["Kitchen", "Bar"])]), {})

    def test_a_later_answer_that_finds_the_room_brings_it_back(self):
        records = [self.outline([], ["Bar"], not_drawn=["Bar"]),
                   {"task": "P0_unnamed_space", "status": "applied", "packet": {},
                    "applied_value": {"rooms": [{"label": "Bar", "area_m2": 6.2}]}}]
        self.assertEqual(self.removed(records), {})

    def test_nothing_is_removed_before_the_outline_step_has_answered(self):
        self.assertEqual(self.removed([self.outline([], ["Bar"], status="waiting_for_reply")]), {})

    def test_the_room_list_leaves_removed_rooms_out_and_lists_them(self):
        proposal = {"rooms": [{"label": "Bar"}, {"label": "Storage"}]}
        removed = {"bar": {"label": "Bar", "reason": "Bar: mentioned only in a general note ..."}}
        result = room_inference_service._without_rooms_not_on_plan(proposal, removed)
        self.assertEqual([row["label"] for row in result["rooms"]], ["Storage"])
        self.assertEqual(result["rooms_not_on_plan"][0]["label"], "Bar")
        self.assertIs(room_inference_service._without_rooms_not_on_plan(proposal, {}), proposal)


class NotAnsweredTests(unittest.TestCase):
    def test_a_question_refused_twice_doesnt_count_as_waiting(self):
        refused_twice = {"task": "P0_outline_corners", "target": "room-bar-page-7", "status": "waiting_for_reply",
                         "reply_attempts": [{"outcome": "rejected"}, {"outcome": "rejected"}]}
        refused_once = {"task": "P0_outline_corners", "target": "room-shop-page-7", "status": "waiting_for_reply",
                        "reply_attempts": [{"outcome": "rejected"}]}
        # A north check accepted on one page and waiting for the others is still waiting, not refused.
        north = {"task": "P2_north", "target": "page-4", "status": "waiting_for_reply",
                 "reply_attempts": [{"outcome": "accepted"}, {"outcome": "accepted"}]}
        with patch.object(tasks, "_all_current", return_value=[refused_twice, refused_once, north]):
            progress = tasks._task_progress(Path("."))
        self.assertEqual((progress["waiting"], progress["not_answered"]), (2, 1))


class NotNeededTests(unittest.TestCase):
    def test_a_task_with_nothing_to_ask_is_saved_as_not_needed_and_not_rebuilt(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            reason = tasks.NotNeeded("No printed dimension line on this plan.")
            first = tasks._create_run(root, "P0_dimensions", "page-5-dimension-1", {"task": "P0_dimensions", "page": 5}, "", [], reason)
            again = tasks._create_run(root, "P0_dimensions", "page-5-dimension-1", {"task": "P0_dimensions", "page": 5}, "", [], reason)
            self.assertEqual((first["status"], first["applied_value"]["note"]), ("not_needed", str(reason)))
            self.assertEqual(again["run_id"], first["run_id"])
            with patch.object(tasks, "_all_current", return_value=[first]):
                progress = tasks._task_progress(root)
            self.assertEqual((progress["blocked"], progress["waiting"], progress["not_needed"]), (0, 0, 1))
            # An older blocked record of the same question is replaced (Global Exchange's north pages with no image).
            blocked = tasks._create_run(root, "P2_north", "page-1", {"task": "P2_north", "page": 1, "crops": []}, "", [],
                                        "No full-resolution page render is available for page 1.")
            settled = tasks._create_run(root, "P2_north", "page-1", {"task": "P2_north", "page": 1, "crops": []}, "", [],
                                        tasks.NotNeeded("This page has no plan image to check for a north arrow."))
            self.assertEqual((blocked["status"], settled["status"]), ("blocked", "not_needed"))
            self.assertNotEqual(blocked["run_id"], settled["run_id"])

    def test_kitchen_task_is_not_needed_once_the_outlines_are_finished_and_there_is_no_kitchen(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": []}}), encoding="utf-8")
            with patch.object(tasks, "_inferred_room_data", return_value=[{"label": "Storage"}, {"label": "Retail"}]), \
                    patch.object(tasks.reviewer_room_geometry_service, "current_records", return_value=[]), \
                    patch.object(tasks, "_p0_settled", return_value=True):
                [(_task, _target, _packet, _prompt, _images, reason)] = tasks._p6_kitchen_packets(root)
            self.assertIsInstance(reason, tasks.NotNeeded)
            with patch.object(tasks, "_inferred_room_data", return_value=[{"label": "Kitchen"}]), \
                    patch.object(tasks.reviewer_room_geometry_service, "current_records", return_value=[]), \
                    patch.object(tasks, "_p0_settled", return_value=True):
                [(_task, _target, _packet, _prompt, _images, reason)] = tasks._p6_kitchen_packets(root)
            self.assertNotIsInstance(reason, tasks.NotNeeded)          # a kitchen still to trace: wait for it


class ServicesOnlySetTests(unittest.TestCase):
    def job(self, folder, with_floor_plan=False):
        root = Path(folder)
        pages = [{"page": number, "title": title, "plan_role": "existing_hvac_plan",
                  "detected_type": "existing_hvac_or_services_plan", "scale": "1:50"}
                 for number, title in ((4, "Mechanical Services Plan"), (5, "Fitout Mechanical Services Ductwork Plan"))]
        pages.append({"page": 7, "title": "Equipment Schedule", "plan_role": "reference", "detected_type": "schedule"})
        if with_floor_plan:
            pages.append({"page": 2, "title": "Floor Plan", "plan_role": "main_floor_plan", "detected_type": "floor_plan",
                          "scale": "1:100", "type": "floor_plan"})
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}), encoding="utf-8")
        (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
            {"page": row["page"], "proposed_role": row["plan_role"], "main_scale": row.get("scale", ""), "level_name": "Level 2"}
            for row in pages]}), encoding="utf-8")
        (root / "spatial_ocr.json").write_text(json.dumps({"pages": [
            {"page": 5, "dimension_candidates": [{"text": "300"}]}]}), encoding="utf-8")
        return root

    def test_a_set_with_no_floor_plan_traces_on_its_services_plan(self):
        with TemporaryDirectory() as folder:
            selection = tasks._p0_page_selection(self.job(folder))
        self.assertEqual([row["page"] for row in selection["selected"]], [5])
        self.assertTrue(any(note["status"] == "services_plan_fallback" for note in selection["notes"]))

    def test_a_set_with_a_floor_plan_keeps_it(self):
        with TemporaryDirectory() as folder:
            selection = tasks._p0_page_selection(self.job(folder, with_floor_plan=True))
        self.assertEqual([row["page"] for row in selection["selected"]], [2])

    def test_the_site_is_read_from_a_place_level_and_venue_line(self):
        text = ("Design by: SC Project:\n2026/04/19 1 PRELIMINARY ISSUE Drawn by: QD Global Exchange WSA\n"
                "Checked by: SC International Kiosk, Level 2, Western Sydney Airport - R2-025\nProject number: M-800266")
        ai_input = {"drawing_set": {"pages": [{"page": 4, "title": "Mechanical Services Plan",
                                               "structured_content": {"markdown": text}}]}}
        names = site_location_resolution.infer_pdf_context(ai_input)["site_name_candidates"]
        venue = [row for row in names if row.get("basis") == "venue_line"]
        self.assertEqual(venue[0]["site_name"], "Western Sydney Airport")
        self.assertIn("International Kiosk, Level 2", venue[0]["tenancy"])
        self.assertIn("Western Sydney Airport", venue[0]["source"]["excerpt"])


if __name__ == "__main__":
    unittest.main()
