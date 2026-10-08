#!/usr/bin/env python3
"""Answers from the What-we-need-to-find list go into the calculation through the existing input routes."""

import unittest
from unittest.mock import patch

from backend import internal_gains_resolution_service, job_service, need_answers_service as answers, page_extraction_service

OPTIONS = {"kinds": [], "rooms": [{"label": "Kitchen", "level": "Ground"}, {"label": "Shop", "level": "Ground"}],
           "equipment": [{"id": "eq:oven", "label": "E06 COMBI OVEN"}],
           "above": [{"id": "roof", "label": "The roof"}, {"id": "floor", "label": "Another floor or tenancy"}]}
PROJECT = {"id": "job", "review_dir": "/nonexistent"}


class ApplyTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(answers, "options", return_value=OPTIONS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def apply(self, **data):
        return answers.apply(None, PROJECT, {"source": "client", "reviewer": "Sam", **data})

    def test_room_area_and_ceiling_height_use_the_typed_overrides(self):
        with patch.object(job_service, "save_area_override") as area, patch.object(job_service, "save_height_override") as height:
            self.assertEqual(self.apply(kind="room_area", room="kitchen", value="42.5")["summary"], "Kitchen area set to 42.5 m².")
            self.apply(kind="ceiling_height", room="Shop", value=3.2)
        area.assert_called_once_with(PROJECT, {"label": "Kitchen", "level_name": "Ground", "area_m2": 42.5, "edited_by": "Sam"})
        self.assertEqual(height.call_args[0][1]["ceiling_height_mm"], 3200)

    def test_people_and_lighting_use_internal_gains_overrides_resolving_first_when_stale(self):
        calls = []

        def post(web, project, data):
            calls.append(data)
            if data["action"] == "apply_override" and len(calls) == 1:
                raise ValueError("Internal-gains evidence is stale. Resolve internal gains again before changing an override.")

        with patch.object(internal_gains_resolution_service, "post", side_effect=post):
            result = self.apply(kind="occupancy", room="Shop", value="60")
        self.assertEqual([row["action"] for row in calls], ["apply_override", "resolve", "apply_override"])
        self.assertEqual((calls[-1]["room_id"], calls[-1]["field"], calls[-1]["value"]), ("room-use:ground:shop", "occupancy_count", 60.0))
        self.assertTrue(result["applied"])
        with patch.object(internal_gains_resolution_service, "post") as post_lighting:
            self.apply(kind="lighting_load", room="Shop", value="1,200")
        self.assertEqual((post_lighting.call_args[0][2]["field"], post_lighting.call_args[0][2]["value"]), ("lighting_load_w", 1200.0))

    def test_an_equipment_rating_accepts_that_item_with_its_room_rating_and_factor(self):
        finding = {"id": "eq:oven", "value": {"code": "E06", "name": "COMBI OVEN", "quantity": 1, "under_hood": True}, "decided_value": None}
        with patch.object(page_extraction_service, "status", return_value={"findings": [finding]}), \
                patch.object(page_extraction_service, "review") as review:
            result = self.apply(kind="equipment_rating", room="Kitchen", equipment_id="eq:oven", value="18 kW")
        value = review.call_args[0][2]["value"]
        self.assertEqual((value["room"], value["rated_input_w"], value["heat_to_space_factor"]), ("Kitchen", 18000, 0.2))
        self.assertIn("E06 COMBI OVEN in Kitchen: 18000 W each", result["summary"])
        with patch.object(page_extraction_service, "status", return_value={"findings": [finding]}):
            with self.assertRaisesRegex(ValueError, "Choose the equipment item"):
                self.apply(kind="equipment_rating", room="Kitchen", equipment_id="eq:none", value="18 kW")

    def test_the_roof_answer_is_the_jobs_answer(self):
        with patch.object(job_service, "save_job_setup") as setup, patch.object(job_service, "apply_roof_answer") as roof:
            self.assertEqual(self.apply(kind="roof_above", value="roof")["summary"], "Above the tenancy: The roof.")
        self.assertEqual(setup.call_args[0][1]["above"], "roof")
        roof.assert_called_once()
        with self.assertRaisesRegex(ValueError, "what is above"):
            self.apply(kind="roof_above", value="sky")

    def test_kinds_without_an_input_are_notes_and_bad_answers_are_refused(self):
        self.assertEqual(self.apply(kind="opening_hours", value="11am-10pm"),
                         {"applied": False, "summary": "Kept as a note (opening hours); not used by the calculation yet."})
        for data, message in (({"kind": "colour", "value": "1"}, "kind of answer"), ({"kind": "room_area", "room": "Attic", "value": "4"}, "Choose the room"),
                              ({"kind": "room_area", "room": "Shop", "value": "big"}, "as a number"),
                              ({"kind": "occupancy", "room": "Shop", "value": "-3"}, "above 0")):
            with self.assertRaisesRegex(ValueError, message):
                self.apply(**data)


if __name__ == "__main__":
    unittest.main()


class GlazingTests(unittest.TestCase):
    def test_glass_answers_apply_to_one_rooms_windows_or_to_every_window(self):
        import json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder, patch.object(answers, "options", return_value=OPTIONS):
            project = {"id": "job", "review_dir": folder}
            result = answers.apply(None, project, {"kind": "glazing", "value": "6.38 mm low-e laminated", "u_value_w_m2k": "3.4",
                                                   "shgc": "0.32", "room": "Shop", "source": "spec_sheet", "reviewer": "Sam"})
            self.assertEqual(result["summary"], "Glass for Shop's windows: U 3.4 W/m²K, SHGC 0.32 (6.38 mm low-e laminated).")
            answers.apply(None, project, {"kind": "glazing", "value": "clear", "u_value_w_m2k": 5.8, "shgc": 0.7, "source": "client"})
            for data, message in (({"u_value_w_m2k": "40", "shgc": "0.3"}, "between 0.5 and 7"), ({"u_value_w_m2k": "3", "shgc": "1.5"}, "between 0.05 and 0.95"),
                                  ({"u_value_w_m2k": "", "shgc": "0.3"}, "as a number"), ({"u_value_w_m2k": "3", "shgc": "0.3", "room": "Attic"}, "Choose the room")):
                with self.assertRaisesRegex(ValueError, message):
                    answers.apply(None, project, {"kind": "glazing", "source": "client", **data})
            proposal = {"openings": [{"owner_room_label": "Shop", "assumptions": ["preliminary_glazing_profile", "unshaded"]},
                                     {"owner_room_label": "Kitchen", "assumptions": []}]}
            answers.apply_glazing(folder, proposal)
            shop, kitchen = proposal["openings"]
            self.assertEqual((shop["u_value_w_m2k"], shop["shgc"], shop["assumptions"]), (3.4, 0.32, ["unshaded"]))
            self.assertIn("spec_sheet", shop["glazing_source"])
            self.assertEqual((kitchen["u_value_w_m2k"], kitchen["shgc"]), (5.8, 0.7))    # the every-window answer
            stored = json.loads((Path(folder) / answers.GLAZING_FILE).read_text())
            self.assertEqual(set(stored["rooms"]), {"Shop"})
        unanswered = {"openings": [{"owner_room_label": "Shop"}]}
        with tempfile.TemporaryDirectory() as empty:
            self.assertNotIn("u_value_w_m2k", answers.apply_glazing(empty, unanswered)["openings"][0])


class ExhaustTests(unittest.TestCase):
    def test_exhaust_needs_a_room_and_rate_and_defaults_to_replacement_through_the_space(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder, patch.object(answers, "options", return_value=OPTIONS):
            project = {"id": "job", "review_dir": folder}
            result = answers.apply(None, project, {"kind": "exhaust", "room": "Kitchen", "value": "1,200 L/s", "source": "mechanical_drawings"})
            self.assertEqual(result["summary"], "Kitchen exhaust 1200 L/s, replaced through the air-conditioned space (no dedicated make-up air) "
                                                "(assumed: method not given); its replacement air is counted as outside air through the air conditioning.")
            self.assertEqual(answers.process_exhaust(folder)["kitchen"], {**answers.process_exhaust(folder)["kitchen"],
                                                                        "lps": 1200.0, "method": "through_space", "method_assumed": True})
            tempered = answers.apply(None, project, {"kind": "exhaust", "room": "Kitchen", "value": "900", "method": "tempered_makeup", "source": "client"})
            self.assertIn("its load belongs to the make-up air unit", tempered["summary"])
            self.assertEqual((answers.process_exhaust(folder)["kitchen"]["method"], answers.process_exhaust(folder)["kitchen"]["method_assumed"]),
                             ("tempered_makeup", False))
            for data, message in (({"room": "Kitchen", "value": "5"}, "between 10 and 20000"), ({"room": "Kitchen", "value": "900", "method": "open window"}, "how the exhausted air"),
                                  ({"room": "", "value": "900"}, "Choose the room")):
                with self.assertRaisesRegex(ValueError, message):
                    answers.apply(None, project, {"kind": "exhaust", "source": "client", **data})
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(answers.process_exhaust(empty), {})
