#!/usr/bin/env python3
"""Page-role check page: what it shows per set, and how a person's answers update an answer sheet."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from ai.page_role_check import apply_answers, case_view

SHEET = {"case_id": "caseP99", "description": "Two-level cafe", "confirmed": False,
         "pages": {"geometry": [4, 5], "rcp": [6], "must_keep": [4, 5, 6], "not_geometry": [1, 2]},
         "levels": {"4": "Ground"}, "notes": ["p3 is the existing plan."]}
PACKET = {"primary_pages": [{"page": 4, "type": "floor_plan", "plan_role": "main_floor_plan", "title": "Ground Floor Plan"},
                            {"page": 3, "type": "floor_plan", "plan_role": "main_floor_plan"}],
          "reference_pages": [{"page": 6, "type": "reflected_ceiling_plan", "plan_role": "reference_context"}],
          "discarded_pages": [{"page": 1, "type": "cover_or_drawing_list"}], "kept_pages": []}
ALL_RIGHT = {"geometry": {"4": "right", "5": "right"}, "rcp": {"6": "right"},
             "must_keep": {"4": "right", "5": "right", "6": "right"}, "not_geometry": {"1": "right", "2": "right"}}


class CaseViewTests(unittest.TestCase):
    def test_the_view_holds_the_sheet_and_the_apps_current_picks(self):
        view = case_view(SHEET, PACKET, 9)
        self.assertEqual(view["facts"]["geometry"], [4, 5])
        self.assertEqual(view["levels"], {"4": "Ground"})
        self.assertEqual(view["app"]["main_plan"], [3, 4])
        self.assertEqual(view["app"]["rcp"], [6])
        self.assertEqual(view["app"]["discarded"], [1])
        self.assertEqual(view["app"]["titles"]["4"], "Ground Floor Plan")
        self.assertEqual(view["page_count"], 9)

    def test_view_exposes_confirmed_information_and_ai_draft(self):
        sheet = {**SHEET, "information": {"1": ["room_geometry"]}, "information_status": "drafted_from_ai"}
        view = case_view(sheet, PACKET, 9, {1: {"information": [{"kind": "room_geometry"}]}})
        self.assertEqual(view["information"], {"1": ["room_geometry"]})
        self.assertEqual(view["ai_information"], {"1": ["room_geometry"]})
        self.assertTrue(view["has_ai_replies"])


class ApplyAnswersTests(unittest.TestCase):
    def test_every_page_marked_and_ticked_confirms_the_sheet(self):
        sheet, problems = apply_answers(SHEET, {"checked": True, "marks": ALL_RIGHT,
                                                "levels": {"4": "Ground", "5": " Level  1 "}}, 9, "2026-10-07")
        self.assertEqual(problems, [])
        self.assertTrue(sheet["confirmed"])
        self.assertEqual(sheet["confirmed_on"], "2026-10-07")
        self.assertEqual(sheet["pages"], SHEET["pages"])
        self.assertEqual(sheet["levels"], {"4": "Ground", "5": "Level 1"})

    def test_wrong_pages_are_removed_and_added_pages_join_their_fact(self):
        marks = json.loads(json.dumps(ALL_RIGHT))
        marks["geometry"]["5"] = "wrong"
        marks["not_geometry"]["2"] = "wrong"
        sheet, problems = apply_answers(SHEET, {"checked": True, "marks": marks, "added": {"rcp": [7], "geometry": [2]},
                                                "levels": {"5": "Level 1"}, "notes": "p7 is the second RCP"}, 9, "2026-10-07")
        self.assertEqual(problems, [])
        self.assertEqual(sheet["pages"]["geometry"], [2, 4])
        self.assertEqual(sheet["pages"]["rcp"], [6, 7])
        self.assertEqual(sheet["pages"]["not_geometry"], [1])
        self.assertNotIn("levels", sheet)  # p5 is no longer a main plan; p4's level was cleared by the person
        self.assertEqual(sheet["notes"][-1], "Person check 2026-10-07: p7 is the second RCP")
        again, _ = apply_answers(sheet, {"checked": True, "marks": marks, "added": {"rcp": [7], "geometry": [2]},
                                         "notes": "p7 is the second RCP"}, 9, "2026-10-09")
        self.assertEqual(again["notes"].count("Person check 2026-10-07: p7 is the second RCP"), 1)   # not repeated on re-apply
        self.assertFalse(any("2026-10-09" in note for note in again["notes"]))

    def test_unmarked_pages_unticked_sets_and_clashes_leave_the_sheet_unconfirmed(self):
        partial = {"geometry": {"4": "right"}}
        sheet, problems = apply_answers(SHEET, {"checked": True, "marks": partial}, 9, "d")
        self.assertFalse(sheet["confirmed"])
        self.assertNotIn("confirmed_on", sheet)
        self.assertTrue(any("geometry: pages [5]" in problem for problem in problems))
        sheet, problems = apply_answers(SHEET, {"checked": False, "marks": ALL_RIGHT}, 9, "d")
        self.assertEqual(problems, ["not ticked as checked"])
        self.assertFalse(sheet["confirmed"])
        sheet, problems = apply_answers(SHEET, {"checked": True, "marks": ALL_RIGHT, "added": {"geometry": [1]}}, 9, "d")
        self.assertFalse(sheet["confirmed"])
        self.assertIn("both a main plan and never-a-main-plan", problems[0])
        self.assertNotIn(1, sheet["pages"]["not_geometry"])

    def test_bad_answers_are_refused(self):
        with self.assertRaisesRegex(ValueError, "not in this set"):
            apply_answers(SHEET, {"added": {"rcp": [10]}}, 9, "d")
        with self.assertRaisesRegex(ValueError, "not in this set"):
            apply_answers(SHEET, {"added": {"rcp": ["7"]}}, 9, "d")
        with self.assertRaisesRegex(ValueError, "right or wrong"):
            apply_answers(SHEET, {"marks": {"rcp": {"6": "maybe"}}}, 9, "d")

    def test_information_is_confirmed_blind_only_after_all_pages_are_done(self):
        info = {str(page): (["room_geometry"] if page == 1 else []) for page in range(1, 10)}
        nothing = {str(page): page != 1 for page in range(1, 10)}
        sheet, problems = apply_answers(SHEET, {"information": info, "information_nothing": nothing,
                                                "information_checked": True, "marks": ALL_RIGHT,
                                                "checked": True}, 9, "2026-10-08")
        self.assertEqual(problems, [])
        self.assertEqual(sheet["information_status"], "confirmed_blind")
        self.assertEqual(sheet["information"]["1"], ["room_geometry"])

        partial = dict(info)
        partial.pop("9")
        incomplete_nothing = dict(nothing)
        incomplete_nothing.pop("9")
        sheet, problems = apply_answers(SHEET, {"information": partial, "information_nothing": incomplete_nothing,
                                                "information_checked": True, "marks": ALL_RIGHT,
                                                "checked": True}, 9, "d")
        self.assertEqual(sheet["information_status"], "not_started")
        self.assertTrue(sheet["confirmed"])  # role confirmation is independent of the incomplete information key
        self.assertTrue(any("page contents" in problem for problem in problems))

    def test_information_draft_provenance_is_preserved_when_checked(self):
        info = {str(page): [] for page in range(1, 10)}
        nothing = {str(page): True for page in range(1, 10)}
        sheet, _ = apply_answers(SHEET, {"information": info, "information_nothing": nothing,
                                         "information_drafted": True, "information_checked": True}, 9, "d")
        self.assertEqual(sheet["information_status"], "confirmed_from_draft")

    def test_bad_information_kinds_and_page_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown kind"):
            apply_answers(SHEET, {"information": {"1": ["made_up"]}}, 9, "d")
        with self.assertRaisesRegex(ValueError, "not in this set"):
            apply_answers(SHEET, {"information": {"10": ["room_geometry"]}}, 9, "d")
        with self.assertRaisesRegex(ValueError, "list of kinds"):
            apply_answers(SHEET, {"information": {"1": "room_geometry"}}, 9, "d")


class ServerTests(unittest.TestCase):
    def test_the_server_saves_answers_for_known_sets_only_and_refuses_unknown_paths(self):
        from tools import page_role_check as tool
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as folder, patch.object(tool, "ANSWERS", Path(folder) / "answers.json"):
            data = {"cases": [{"case_id": "caseP99", "page_count": 9}], "missing": []}
            server = ThreadingHTTPServer(("127.0.0.1", 0), tool.make_handler(data, None, {"caseP99": 9}))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                def post(body):
                    request = urllib.request.Request(base + "/api/answers", data=json.dumps(body).encode(), method="POST")
                    return urllib.request.urlopen(request).status
                self.assertEqual(post({"caseP99": {"checked": True}}), 200)
                self.assertEqual(json.loads(urllib.request.urlopen(base + "/api/data").read())["answers"],
                                 {"caseP99": {"checked": True}})
                with self.assertRaises(urllib.error.HTTPError) as refused:
                    post({"caseP00": {}})
                self.assertEqual(refused.exception.code, 400)
                for path in ("/thumb/caseP99/10", "/thumb/caseP00/1", "/thumb/../x", "/etc/passwd"):
                    with self.assertRaises(urllib.error.HTTPError) as missing:
                        urllib.request.urlopen(base + path)
                    self.assertEqual(missing.exception.code, 404, path)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
