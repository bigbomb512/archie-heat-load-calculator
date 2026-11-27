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



class StandInPdfTests(unittest.TestCase):
    """A job saved in a removed worktree lost its upload; an identical PDF may stand in, only in the copy."""

    def job(self, folder, pdf_bytes_drawn_on):
        import hashlib, json
        digest = hashlib.sha256(pdf_bytes_drawn_on).hexdigest()
        (folder / "reviewer_room_geometry.json").write_text(json.dumps(
            {"records": [{"source_fingerprints": {"source_pdf": digest}}]}), encoding="utf-8")
        (folder / "ai_input.json").write_text(json.dumps({"source_pdf": "/gone/worktree/drawing.pdf"}), encoding="utf-8")

    def test_the_same_pdf_is_used_in_the_copy(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as temp:
            folder, pdf = Path(temp), Path(temp) / "same.pdf"
            pdf.write_bytes(b"%PDF drawing")
            self.job(folder, b"%PDF drawing")
            project = {"pdf": "/gone/worktree/drawing.pdf"}
            score.use_stand_in_pdf(folder, project, pdf)
            self.assertEqual(json.loads((folder / "ai_input.json").read_text())["source_pdf"], str(pdf))
            self.assertEqual(project["pdf"], str(pdf))

    def test_a_different_pdf_is_refused(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as temp:
            folder, pdf = Path(temp), Path(temp) / "other.pdf"
            pdf.write_bytes(b"%PDF another revision")
            self.job(folder, b"%PDF drawing")
            with self.assertRaises(SystemExit):
                score.use_stand_in_pdf(folder, {}, pdf)
            self.assertEqual(json.loads((folder / "ai_input.json").read_text())["source_pdf"], "/gone/worktree/drawing.pdf")



class FactsTests(unittest.TestCase):
    def test_a_room_with_no_load_is_not_cooled_floor(self):
        # Turramurra's checked job keeps a 986 m² driveway in the model with no load; it isn't 986 m² of cooled floor.
        built = {"topology": {"floors": [{"floor_id": "f", "name": "Ground Floor"}], "zones": [{"zone_id": "z", "floor_id": "f"}],
                              "rooms": [{"room_id": "living", "name": "Living", "zone_id": "z", "area_m2": 30},
                                        {"room_id": "drive", "name": "Driveway", "zone_id": "z", "area_m2": 986}]}}
        hour = lambda kw: {"components": {"walls": {"total_kw": kw}}}
        report = {"included_scope_peak": {"final_design_total_kw": 2.0},
                  "scenario_results": [{"rooms": [{"room_id": "living", "hours": [hour(2.0)]},
                                                  {"room_id": "drive", "hours": [hour(0.0)]}]}]}
        rooms = {row["name"]: row for row in score.facts(built, report, {}, [], {})["rooms"]}
        self.assertTrue(rooms["Living"]["cooled"])
        self.assertFalse(rooms["Driveway"]["cooled"])


if __name__ == "__main__":
    unittest.main()
