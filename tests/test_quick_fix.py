"""Quick fixes (backend/quick_fix_service.py): checked before anything is saved, applied through the existing routes,
and read by the calculation (blinds and awnings on the windows, cooled or not in the room selection)."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import calculation_service, need_answers_service, quick_fix_service as fix
from tests import test_draft_reference_rooms as reference


ROOMS = [{"room_id": "room-use:ground:bed-1", "label": "Bed 1", "name": "Bed 1", "level": "Ground", "area_m2": 12.0,
          "use_id": "house_bedroom", "people": 1, "kind": "comfort", "can_cool": True, "cooled": True},
         {"room_id": "room-use:ground:ens", "label": "Ens", "name": "Ens", "level": "Ground", "area_m2": 36.0,
          "use_id": "house_wet", "people": 0, "kind": "comfort", "can_cool": True, "cooled": True}]


class SaveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = {"id": "j", "review_dir": str(self.root)}
        screen = {"rooms": ROOMS, "uses": {"house_bedroom": "House bedroom", "garage": "Garage", "house_wet": "Wet area"}}
        for target, name, value in ((fix, "get", screen), (fix, "_apply_use", None), (need_answers_service, "apply", {})):
            patch.object(target, name, return_value=value).start()
        self.area = patch("backend.job_service.save_area_override").start()
        self.addCleanup(patch.stopall)

    def stored(self):
        return json.loads((self.root / fix.FILE).read_text())

    def test_the_set_15_garage_is_renamed_reused_and_left_uncooled_in_one_save(self):
        result = fix.save(None, self.project, {"rooms": [{"room_id": "room-use:ground:ens", "name": "Garage", "use": "garage",
                                                         "cooled": False}]})
        room = self.stored()["rooms"]["room-use:ground:ens"]
        self.assertEqual((room["name"], room["cooled"], room["label"]), ("Garage", False, "Ens"))
        fix._apply_use.assert_called_once()
        self.assertEqual(fix._apply_use.call_args.args[2:4], ("room-use:ground:ens", "garage"))
        self.assertEqual(fix.cooled_choices(self.root), {"room-use:ground:ens": False})
        self.assertEqual(fix.display_names(self.root), {"room-use:ground:ens": "Garage"})
        self.assertTrue(any("not cooled" in text for text in result["applied"]))

    def test_area_and_people_go_through_the_existing_routes(self):
        fix.save(None, self.project, {"rooms": [{"room_id": "room-use:ground:bed-1", "area_m2": 14.5, "people": 2}]})
        self.assertEqual(self.area.call_args.args[1]["area_m2"], 14.5)
        self.assertEqual(self.area.call_args.args[1]["room_id"], "room-use:ground:bed-1")
        sent = need_answers_service.apply.call_args.args[2]
        self.assertEqual((sent["kind"], sent["room"], sent["value"]), ("occupancy", "Bed 1", 2))

    def test_one_bad_value_saves_nothing(self):
        for bad in ({"room_id": "room-use:ground:bed-1", "name": "Bed one", "area_m2": 0},
                    {"room_id": "room-use:ground:bed-1", "people": -1},
                    {"room_id": "room-use:ground:bed-1", "name": "  "},
                    {"room_id": "room-use:ground:bed-1", "use": "not_a_use"},
                    {"room_id": "room-use:ground:bed-1", "cooled": "no"},
                    {"room_id": "nowhere", "name": "X"}):
            with self.subTest(change=bad), self.assertRaises(ValueError):
                fix.save(None, self.project, {"rooms": [{"room_id": "room-use:ground:bed-1", "name": "Main bedroom"}, bad]})
            self.assertFalse((self.root / fix.FILE).exists())
            self.area.assert_not_called()
        with self.assertRaisesRegex(ValueError, "where the glass"):
            fix.save(None, self.project, {"windows": [{"room": "Bed 1", "glass": {"u_value_w_m2k": 4.0, "shgc": 0.5}}]})
        with self.assertRaises(ValueError):
            fix.save(None, self.project, {"windows": [{"room": "Bed 1", "shade": {"kind": "awning", "depth_m": 30}}]})
        with self.assertRaises(ValueError):
            fix.save(None, self.project, {"windows": [{"room": "Kitchen", "blinds": "none"}]})

    def test_window_answers_are_stored_cleared_and_given_to_the_glass_answers(self):
        fix.save(None, self.project, {"windows": [
            {"room": "bed 1", "glass": {"preset": "nathers_hinged"}, "blinds": "venetian_light",
             "shade": {"kind": "awning", "depth_m": 0.9, "gap_m": 0.3}},
            {"room": "*", "blinds": "roller_dark"}]})
        windows = self.stored()["windows"]
        self.assertEqual(windows["Bed 1"], {"blinds": "venetian_light", "shade": {"kind": "awning", "depth_m": 0.9, "gap_m": 0.3}})
        self.assertEqual(windows["*"], {"blinds": "roller_dark"})
        glazing = json.loads((self.root / "glazing_answers.json").read_text())
        self.assertEqual((glazing["rooms"]["Bed 1"]["u_value_w_m2k"], glazing["rooms"]["Bed 1"]["shgc"]), (6.7, 0.57))
        fix.save(None, self.project, {"windows": [{"room": "Bed 1", "glass": "drawings", "blinds": "drawings", "shade": "drawings"}]})
        self.assertNotIn("Bed 1", self.stored()["windows"])
        self.assertEqual(json.loads((self.root / "glazing_answers.json").read_text())["rooms"], {})
        fix.save(None, self.project, {"windows": [{"room": "Bed 1", "shade": "none"}]})
        self.assertEqual(self.stored()["windows"]["Bed 1"], {"shade": None})

    def test_naming_a_room_back_to_its_label_removes_the_display_name(self):
        fix.save(None, self.project, {"rooms": [{"room_id": "room-use:ground:bed-1", "name": "Main bedroom"}]})
        fix.save(None, self.project, {"rooms": [{"room_id": "room-use:ground:bed-1", "name": "Bed 1"}]})
        self.assertEqual(fix.display_names(self.root), {})


class ApplyWindowsTests(unittest.TestCase):
    def test_the_rooms_answer_wins_field_by_field_over_every_window(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / fix.FILE).write_text(json.dumps({"windows": {
                "*": {"blinds": "roller_dark", "shade": {"kind": "eave", "depth_m": 0.6}},
                "Bed 1": {"blinds": "none"}, "Living": {"shade": None}}}))
            proposal = {"openings": [{"owner_room_label": "BED 1", "internal_shade": {"kind": "venetian"}},
                                     {"owner_room_label": "Living", "shade": {"kind": "awning", "depth_m": 2}},
                                     {"owner_room_label": "Hall"}]}
            bed, living, hall = fix.apply_windows(folder, proposal)["openings"]
        self.assertNotIn("internal_shade", bed)                       # "no blinds" removes the drawing's blind
        self.assertEqual(bed["shade"]["kind"], "eave")                # every window's eave still applies
        self.assertNotIn("shade", living)                             # "no shade" removes the drawn awning
        self.assertEqual(living["internal_shade"]["kind"], "roller")
        self.assertEqual((hall["internal_shade"]["colour"], hall["shade"]["depth_m"]), ("dark", 0.6))

    def test_without_answers_the_proposal_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            proposal = {"openings": [{"owner_room_label": "Bed 1", "shade": {"kind": "awning", "depth_m": 1}}]}
            self.assertEqual(fix.apply_windows(folder, json.loads(json.dumps(proposal))), proposal)


class ThroughTheCalculationTests(unittest.TestCase):
    """A blind and an awning entered as quick fixes reach the draft's glass, as on a schedule or a drawing."""

    def test_a_blind_and_an_awning_entered_as_fixes_reach_the_glass(self):
        case = next(row for row in reference.SUITE["cases"] if row["case_id"] == "A_west_shop")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / fix.FILE).write_text(json.dumps({"windows": {"Shop": {
                "blinds": "venetian_light", "shade": {"kind": "awning", "depth_m": 1.2, "gap_m": 0.2}}}}))
            proposal = fix.apply_windows(folder, reference.proposal(case))
        model = reference.ai_preliminary.assemble({"spaces": []}, preliminary_proposal=proposal, site_location=reference.SITE,
                                                   design_weather_table=reference.DESIGN_TABLE, monthly_climate_table=reference.MONTHLY_TABLE)
        glazing = model["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["glazing_surfaces"][0]
        self.assertEqual(glazing["window"]["internal_shading_factor"], 0.78)       # light venetian on the pack's glass
        self.assertEqual(glazing["shade"]["depth_m"], 1.2)
        _plain_model, plain = reference.run(case)
        shaded = reference.ai_preliminary.calculate(model)
        gain = lambda report: sum(reference.room_hour(report, 1, hour, "Shop")["glazing_solar"]["heat_gain_sensible_kw"]
                                  for hour in range(24))
        self.assertLess(gain(shaded), 0.78 * gain(plain))         # the blind, and the awning's shadow on top


class CooledChoiceTests(unittest.TestCase):
    def test_a_saved_choice_decides_the_room_unless_the_request_chooses(self):
        scope = {"candidates": [{"key": "a", "status": "calculated", "area_m2": 10, "include": True},
                                {"key": "b", "status": "calculated", "area_m2": 10, "include": True},
                                {"key": "c", "status": "needs_use", "area_m2": None}]}
        rows = {row["key"]: row["include"] for row in calculation_service._rows(scope, {**{"a": False, "c": True}, **{}})}
        self.assertEqual(rows, {"a": False, "b": True, "c": False})        # a room with no area can't be cooled by a tick
        rows = {row["key"]: row["include"] for row in calculation_service._rows(scope, {**{"a": False}, **{"a": True}})}
        self.assertTrue(rows["a"])


if __name__ == "__main__":
    unittest.main()
