import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("score", Path(__file__).resolve().parents[1] / "tools" / "score_house_pdf_only.py")
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]          # 100 x 100 px; at 1 m² per 100 px², 100 m²
BESIDE = [[100, 0], [200, 0], [200, 100], [100, 100], [100, 0]]


def room(name, points, area, kw, page=7, level="Ground Floor", cooled=True, glass=None):
    return {"room_id": name, "name": name, "level": level, "area_m2": area, "ceiling_mm": 2700, "cooled": cooled,
            "peak_kw": kw, "glass_m2": glass or {}, "outlines": [{"page": page, "points": points}] if points else []}


def facts(rooms, kw, complete=True, north=None):
    return {"total_kw": kw, "month": "February", "hour": 16, "status": "calculated", "complete": complete,
            "partial_label": "", "north": north or {}, "rooms": rooms}


class CompareTests(unittest.TestCase):
    def test_a_result_that_misses_half_the_floor_is_not_complete(self):
        checked = facts([room("Living", SQUARE, 100, 5.0, glass={"W": 10}), room("Bedroom", BESIDE, 100, 1.0)], 6.0, north={"7": 306.0})
        ai = facts([room("Lounge", SQUARE, 100, 4.0, glass={"W": 8})], 4.0, complete=True, north={"7": 300.0})
        result = score.compare(checked, ai)
        self.assertAlmostEqual(result["floor"]["covered_share"], 0.5)
        self.assertEqual(result["floor"]["ai_outside_checked_m2"], 0)
        self.assertTrue(result["honesty"]["false_complete"])
        rows = {row["room"]: row for row in result["rooms"]}
        self.assertEqual(rows["Living"]["ai_room"], "Lounge")
        self.assertIsNone(rows["Bedroom"]["ai_room"])
        self.assertEqual(result["load"]["error"], round((4 - 6) / 6, 3))
        self.assertEqual(result["north"]["7"]["error_deg"], 6.0)
        self.assertEqual(result["glass_m2"], {"checked": {"W": 10}, "ai": {"W": 8}})

    def test_a_close_result_labelled_partial_is_not_false_complete(self):
        checked = facts([room("Living", SQUARE, 100, 5.0)], 5.0)
        ai = facts([room("Living", SQUARE, 100, 5.2)], 5.2, complete=False)
        result = score.compare(checked, ai)
        self.assertFalse(result["honesty"]["should_not_be_complete"])
        self.assertFalse(result["honesty"]["false_complete"])
        self.assertEqual(result["floor"]["covered_share"], 1.0)

    def test_a_level_traced_on_an_aligned_sheet_is_compared_on_its_own_plan_page(self):
        checked = facts([room("Gym", SQUARE, 100, 2.0, page=7, level="Lower Ground Floor")], 2.0)
        ai = facts([room("Gym", SQUARE, 100, 2.0, page=4, level="Lower Ground")], 2.0)
        self.assertEqual(score.compare(checked, ai)["floor"]["covered_share"], 0.0)
        self.assertEqual(score.compare(checked, ai, {"Lower Ground Floor": 4})["floor"]["covered_share"], 1.0)

    def test_cooled_rooms_without_an_outline_and_extra_floor_are_reported(self):
        checked = facts([room("Living", SQUARE, 100, 5.0)], 5.0)
        ai = facts([room("Living", [[0, 0], [150, 0], [150, 100], [0, 100], [0, 0]], 150, 6.0),
                    room("Study", None, 12, 0.5), room("Store", None, 4, 0.0, cooled=False)], 6.5)
        result = score.compare(checked, ai)
        self.assertEqual(result["floor"]["ai_outside_checked_m2"], 50.0)
        self.assertEqual([row["room"] for row in result["floor"]["ai_cooled_without_outline"]], ["Study"])
        self.assertIn("Living", score.markdown("house", result))


if __name__ == "__main__":
    unittest.main()
