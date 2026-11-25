"""Architect questions, email order, and persisted answers on a tiny job."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ai import construction_u_values
from backend import architect_questions_service as service, assumption_range_service


def write(root, name, value):
    (root / name).write_text(json.dumps(value), encoding="utf-8")


def read(root, name):
    return json.loads((root / name).read_text(encoding="utf-8"))


class ArchitectQuestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "house", "review_dir": str(self.root), "name": "Test house"}
        write(self.root, "hourly_ai_preliminary_load_report.json", {
            "included_scope_peak": {"final_design_total_kw": 9.0}, "preliminary_input_set_fingerprint": "first"})
        write(self.root, "ai_preliminary_input_set.json", {"material": {"hourly_load_model": {"rooms": [
            {"cooling_load": {"envelope_surfaces": [{"kind": "opaque_wall", "boundary_method": "external"},
                                                      {"kind": "roof"}],
                              "glazing_surfaces": [{"source": "assumption pack"}]}}]}}})
        write(self.root, "reviewer_room_geometry.json", {"page_north": {
            "1": {"plan_up_azimuth_deg": 20, "declaration_source": "ai_determined"},
            "2": {"plan_up_azimuth_deg": 350, "declaration_source": "ai_determined"}}})
        write(self.root, "room_use_resolution.json", {"dwelling_context": {"is_dwelling": True}})
        rows = [{"id": "frame-walls:brick-veneer", "surface": "wall", "section": "Frame walls",
                 "name": "Brick Veneer", "u_value_w_m2k": 1.9},
                {"id": "pitched-roofs:tiled-roof-gypsum-board", "surface": "roof", "section": "Pitched roofs",
                 "name": "Tiled roof, gypsum board", "u_value_w_m2k": 1.32}]
        patcher = mock.patch.object(construction_u_values, "load_table", return_value={"constructions": rows})
        patcher.start()
        self.addCleanup(patcher.stop)

    def range_result(self):
        return {"status": "calculated", "saved_kw": 9.0, "low_kw": 7.0, "high_kw": 10.5,
                "unknowns": [
                    {"key": "windows", "swing_kw": 0.5, "low_kw": 9.0, "high_kw": 9.5, "changes_unit": True,
                     "cases": [{"status": "calculated", "label": "Low e", "kw": 9.5, "delta_kw": 0.5,
                                "unit": "10 kW", "changes_unit": True}]},
                    {"key": "wall", "swing_kw": 2.0, "low_kw": 7.0, "high_kw": 9.0, "changes_unit": False,
                     "cases": []},
                    {"key": "roof", "swing_kw": 1.5, "low_kw": 9.0, "high_kw": 10.5, "changes_unit": True,
                     "cases": []},
                    {"key": "north", "swing_kw": 3.0, "low_kw": 9.0, "high_kw": 12.0, "changes_unit": False,
                     "cases": []}]}

    def test_get_ranks_items_and_email_only_open_questions(self):
        write(self.root, "construction_answers.json", {"roof": {"all": {"construction_id": "answered"}}})
        write(self.root, service.FILE, {"asked": {"wall": {"by": "Alex", "at": 12}}})
        with mock.patch.object(assumption_range_service, "get", return_value=self.range_result()):
            result = service.get(self.project, start=False)
        self.assertEqual([(row["id"], row["status"]) for row in result["items"]],
                         [("windows", "open"), ("north", "open"), ("wall", "asked"), ("roof", "answered")])
        self.assertEqual(result["open_count"], 3)
        self.assertEqual(result["unit_changing_open"], 1)
        body = result["email"]["body"]
        questions = {row["id"]: row["question"] for row in result["items"]}
        self.assertLess(body.index(questions["windows"]), body.index(questions["north"]))
        self.assertLess(body.index(questions["north"]), body.index(questions["wall"]))
        self.assertNotIn(questions["roof"], body)
        self.assertIn("Could move the load by up to 0.5 kW", body)
        self.assertIn("from 7.0 to 10.5 kW", body)
        with mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}):
            pending = service.get(self.project, start=False)
        self.assertNotIn("could be anywhere", pending["email"]["body"])

    def test_get_without_imported_handbook_table(self):
        with mock.patch.object(construction_u_values, "load_table", return_value=None), \
             mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}):
            result = service.get(self.project, start=False)
        self.assertEqual(result["constructions"], {"wall": [], "roof": []})

    def test_mark_and_unmark_persist_reviewer_and_time(self):
        with mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}), \
             mock.patch.object(service.time, "time", return_value=123.5):
            marked = service.post(None, self.project, {"action": "mark_asked", "ids": ["wall"],
                                                       "reviewer": "  Alex  Builder  "})
            self.assertEqual(marked["items"][1]["status"], "asked")
            self.assertEqual(read(self.root, service.FILE)["asked"]["wall"], {"by": "Alex Builder", "at": 123.5})
            service.post(None, self.project, {"action": "unmark", "ids": ["wall"]})
        self.assertEqual(read(self.root, service.FILE)["asked"], {})

    def test_invalid_ids_raise(self):
        with self.assertRaises(ValueError):
            service.post(None, self.project, {"action": "mark_asked", "ids": ["invalid"]})
        with self.assertRaises(ValueError):
            service.post(None, self.project, {"action": "mark_asked", "ids": ["wall", "invalid"]})
        with self.assertRaises(ValueError):
            service.post(None, self.project, {"action": "answer", "id": "invalid"})
        self.assertFalse((self.root / service.FILE).exists())

    def test_answer_windows_writes_all_and_checks_ranges(self):
        with mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}):
            result = service.post(None, self.project, {"action": "answer", "id": "windows", "reviewer": "Alex",
                                                       "u_value_w_m2k": 2.9, "shgc": 0.4, "value": "Low e"})
            answer = read(self.root, "glazing_answers.json")["all"]
            self.assertEqual((answer["u_value_w_m2k"], answer["shgc"], answer["by"]), (2.9, 0.4, "Alex"))
            self.assertIn("Glass for every window", result["summary"])
            self.assertEqual(next(row for row in result["items"] if row["id"] == "windows")["status"], "answered")
            for u, shgc in [(-1, 0.4), (2.9, 2.0)]:
                with self.subTest(u=u, shgc=shgc), self.assertRaises(ValueError):
                    service.post(None, self.project, {"action": "answer", "id": "windows",
                                                           "u_value_w_m2k": u, "shgc": shgc})

    def test_answer_wall_with_handbook_id(self):
        chosen = "frame-walls:brick-veneer"
        handbook = construction_u_values.find(construction_u_values.load_table(), chosen)
        with mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}):
            result = service.post(None, self.project, {"action": "answer", "id": "wall",
                                                       "construction_id": chosen, "reviewer": "Alex"})
        answer = read(self.root, "construction_answers.json")["wall"]["all"]
        self.assertEqual((answer["construction_id"], answer["u_value_w_m2k"]),
                         (chosen, handbook["u_value_w_m2k"]))
        self.assertIn("AIRAH Technical Handbook", result["summary"])

    def test_reversed_north_declares_each_page_and_rejects_bad_choice(self):
        with mock.patch.object(assumption_range_service, "get", return_value={"status": "not_started"}), \
             mock.patch("backend.reviewer_room_geometry_service.post") as declare:
            result = service.post(None, self.project, {"action": "answer", "id": "north",
                                                       "choice": "reversed", "reviewer": "Alex"})
            self.assertEqual(declare.call_count, 2)
            calls = [call.args[2] for call in declare.call_args_list]
            self.assertEqual([(row["page"], row["plan_up_azimuth_deg"]) for row in calls], [(1, 200), (2, 170)])
            self.assertTrue(all(row["action"] == "declare_north" and row["reviewer"] == "Alex" for row in calls))
            self.assertIn("2 plan page", result["summary"])
            with self.assertRaises(ValueError):
                service.post(None, self.project, {"action": "answer", "id": "north", "choice": "upside_down"})
            self.assertEqual(declare.call_count, 2)


if __name__ == "__main__":
    unittest.main()
