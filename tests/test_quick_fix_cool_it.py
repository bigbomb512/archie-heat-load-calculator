""""Cool it" on the quick-fix screen cools a room its use left uncooled (a restaurant kitchen), and switching it back
undoes that (Casa Nova, 2026-11-23)."""
import json
import tempfile
import unittest
from pathlib import Path

from ai import room_use_resolution
from backend import ai_preliminary_service


def kitchen_use():
    building = {"spaces": [{"name": "Kitchen", "level_name": "Ground", "area_m2": 19,
                            "evidence": [{"page": 4, "excerpt": "Kitchen"}]}]}
    return room_use_resolution.resolve(building, dwelling_context={"is_dwelling": False, "evidence": []})


class CoolItTests(unittest.TestCase):
    def test_cool_it_cools_a_restaurant_kitchen_and_switching_back_undoes_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = {"root": root}
            artifact = kitchen_use()
            row = artifact["records"][0]
            self.assertEqual(row["space_scope"], "not_cooled")
            fixes = {"rooms": {row["room_id"]: {"label": "Kitchen", "cooled": True}}, "windows": {}}
            (root / "quick_fixes.json").write_text(json.dumps(fixes))
            cooled = ai_preliminary_service._with_quick_fix_cooling(paths, artifact)["records"][0]
            self.assertIn(cooled["space_scope"], {"comfort_hvac", "comfort_hvac_with_process_exception"})
            self.assertEqual(cooled["override"]["reviewer"], ai_preliminary_service.QUICK_FIX_COOLING_REVIEWER)
            fixes["rooms"][row["room_id"]]["cooled"] = False
            (root / "quick_fixes.json").write_text(json.dumps(fixes))
            saved = {**artifact, "records": [cooled]}
            stripped = ai_preliminary_service._without_stale_quick_fix_cooling(paths, saved)["records"][0]
            self.assertIsNone(stripped.get("override"))
            building = {"spaces": [{"name": "Kitchen", "level_name": "Ground", "area_m2": 19,
                                    "evidence": [{"page": 4, "excerpt": "Kitchen"}]}]}
            again = room_use_resolution.resolve(building, existing={**saved, "records": [stripped]},
                                                dwelling_context={"is_dwelling": False, "evidence": []})
            back = ai_preliminary_service._with_quick_fix_cooling(paths, again)["records"][0]
            self.assertIsNone(back.get("override"))
            self.assertEqual(back["space_scope"], "not_cooled")

    def test_a_reviewers_own_choice_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            artifact = kitchen_use()
            room_id = artifact["records"][0]["room_id"]
            artifact = room_use_resolution.apply_override(artifact, room_id, "storage", "Sam", "It's a store.")
            (root / "quick_fixes.json").write_text(json.dumps({"rooms": {room_id: {"cooled": False}}, "windows": {}}))
            row = ai_preliminary_service._with_quick_fix_cooling({"root": root}, artifact)["records"][0]
            self.assertEqual(row["override"]["reviewer"], "Sam")


if __name__ == "__main__":
    unittest.main()
