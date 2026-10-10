"""Wall and roof U-values from an imported construction table (values here are made up, not AIRAH's), answered for a
job's external walls or exposed roof, and used by the draft calculation in place of the preliminary U-values."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import ai_preliminary, ceiling_volume_resolution, construction_u_values
from backend import need_answers_service, result_status_service
from tests import test_reviewer_traced_preliminary as traced
from tools import import_airah_handbook_u_values as importer

TEXT = """Contents
   152       Overall heat transfer coefficients (U)
Overall heat transfer coefficients (U)
Masonry walls
        Brick, solid, with                1. Outdoor air film                          0.03         0
        indoor plaster
                                          2. 110 mm brick                              0.15       190
                                          		                    U = 1/RT =      2.0 W/m².K
            1 2 3                         WITHOUT PLASTER
                                          		                    U = 1/RT =      2.4 W/m².K
        Block, (Light), with              1. Outdoor air film                          0.03         0
        tiles
                                          		                    U = 1/RT =      0.40
                                                                                    3.0 W/m².K
        For a ventilated cavity an experimental value is U=2.9 W/m2.K
Overall heat transfer coefficients (U)
Flat roofs (cont)
        Metal deck, blanket,
        plasterboard                      1. Outdoor air film                          0.03        0.04
                                          		                    U = 1/RT =      0.70 W/m².K       0.40 W/m².K
Windows
Single glass                    1. Outdoor air film                            0.03                     0.04
                                		                              U = 1/RT =     6.0 W/m².K              5.9W/m².K
Thermal properties of building and insulating material
"""
TABLE = {"source": "Test table", "constructions": [
    {"id": "masonry-walls:brick", "section": "Masonry walls", "name": "Brick", "u_value_w_m2k": 2.0},
    {"id": "flat-roofs:deck", "section": "Flat roofs", "name": "Metal deck, blanket", "u_value_w_m2k": 0.4},
    {"id": "windows:single", "section": "Windows", "name": "Single glass", "u_value_w_m2k": 5.9},     # not a wall or roof
    {"id": "flat-roofs:bad", "section": "Flat roofs", "name": "Impossible", "u_value_w_m2k": 40},
    {"id": "masonry-walls:brick", "section": "Masonry walls", "name": "Duplicate", "u_value_w_m2k": 1.0}]}


class ImportTests(unittest.TestCase):
    def test_the_importer_reads_constructions_variants_and_summer_values_and_skips_notes(self):
        rows = importer.parse(TEXT)
        self.assertEqual([(row["section"], row["name"], row["u_value_w_m2k"]) for row in rows], [
            ("Masonry walls", "Brick, solid, with indoor plaster", 2.0),
            ("Masonry walls", "Brick, solid, with indoor plaster (without plaster)", 2.4),
            ("Masonry walls", "Block, (Light), with tiles", 3.0),            # the U printed a line below its label
            ("Flat roofs", "Metal deck, blanket, plasterboard", 0.4),        # summer (heat flow down)
            ("Windows", "Single glass", 5.9)])
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        inside = Path(importer.ROOT) / "u.json"
        self.assertEqual(importer.main(["x", "/nonexistent.pdf", str(inside)]), 1)
        self.assertFalse(inside.exists())

    def test_only_wall_and_roof_rows_in_range_are_offered_once_each(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "u.json"
            path.write_text(json.dumps(TABLE))
            table = construction_u_values.load_table(path)
            self.assertEqual([row["id"] for row in table["constructions"]], ["masonry-walls:brick", "flat-roofs:deck"])
            self.assertEqual([row["id"] for row in construction_u_values.choices(table, "roof")], ["flat-roofs:deck"])
            self.assertIn("U 0.4 W/m²K", construction_u_values.choices(table, "roof")[0]["label"])
            path.write_text("[]")
            self.assertIsNone(construction_u_values.load_table(path))


class AnswerTests(unittest.TestCase):
    rooms = [{"label": "Shop", "level": "Level 1"}, {"label": "Bar", "level": "Level 1"}]

    def save(self, project, **data):
        return need_answers_service._save_construction(project, {"source": "drawings", **data}, self.rooms, "Sam")

    def test_a_listed_construction_or_a_typed_u_value_is_saved_and_a_room_answer_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            table_path = Path(folder) / "u.json"
            table_path.write_text(json.dumps(TABLE))
            project = {"review_dir": folder}
            with patch.dict("os.environ", {construction_u_values.TABLE_ENV: str(table_path)}):
                for data, message in (({"surface": "floor"}, "walls or the roof"),
                                      ({"surface": "wall", "construction_id": "flat-roofs:deck"}, "listed constructions"),
                                      ({"surface": "wall", "u_value_w_m2k": "9"}, "between"),
                                      ({"surface": "wall", "u_value_w_m2k": "1.2"}, "Describe the construction")):
                    with self.assertRaisesRegex(ValueError, message):
                        self.save(project, **data)
                result = self.save(project, surface="roof", construction_id="flat-roofs:deck")
                self.assertEqual(result["summary"], "Every room's exposed roof: Metal deck, blanket, U 0.4 W/m²K (AIRAH Technical Handbook).")
                self.save(project, surface="wall", u_value_w_m2k="0.45", value="Insulated precast panel", room="Bar")
                self.save(project, surface="wall", construction_id="masonry-walls:brick")
            proposal = {"surfaces": [
                {"physical_type": "wall", "owner_room_label": "Shop"}, {"physical_type": "wall", "owner_room_label": "bar"},
                {"physical_type": "roof", "owner_room_label": "Shop"}, {"physical_type": "floor", "owner_room_label": "Shop"}]}
            shop_wall, bar_wall, roof, floor = need_answers_service.apply_constructions(folder, proposal)["surfaces"]
            self.assertEqual((shop_wall["u_value_w_m2k"], shop_wall["construction_id"]), (2.0, "masonry-walls:brick"))
            self.assertEqual((bar_wall["u_value_w_m2k"], bar_wall["construction_id"]), (0.45, "answered-wall"))
            self.assertIn("Insulated precast panel", bar_wall["construction_source"])
            self.assertEqual(roof["u_value_w_m2k"], 0.4)
            self.assertIn("AIRAH", roof["construction_source"])
            self.assertNotIn("u_value_w_m2k", floor)


class CalculationTests(unittest.TestCase):
    def test_answered_walls_and_roof_replace_the_preliminary_u_values_in_the_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, trace = traced.write_fixture(root)
            traced.set_envelope_trace(root, trace, [{"index": 0, "boundary": "external"}], "exposed")
            (root / "ceiling_volume_resolution.json").write_text(json.dumps({"records": [
                {"room_id": ceiling_volume_resolution.room_identity("Shop", "Level 1"), "status": "resolved", "ceiling_height_mm": 3000}]}))

            def surfaces():
                prepared = traced.prepare(root, room_use, json.loads(json.dumps(proposal)))
                assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared, allow_area_fallbacks=False)
                room = assembled["material"]["hourly_load_model"]["rooms"][0]
                rows = {row["kind"]: row for row in room["cooling_load"]["envelope_surfaces"]}
                return rows, ai_preliminary.calculate(assembled)["included_scope_peak"]["components"]["envelope"]["total_kw"]

            before, before_kw = surfaces()
            self.assertEqual((before["opaque_wall"]["u_value_w_m2k"], before["roof"]["u_value_w_m2k"]), (0.6, 0.5))
            (root / need_answers_service.CONSTRUCTION_FILE).write_text(json.dumps({
                "wall": {"all": {"u_value_w_m2k": 3.9, "construction_id": "masonry-walls:concrete", "construction": "Concrete, dense",
                                 "reference": "Test table", "source": "drawings"}, "rooms": {}},
                "roof": {"all": None, "rooms": {"Shop": {"u_value_w_m2k": 1.73, "construction_id": "", "construction": "Concrete roof",
                                                          "reference": "", "source": "site_visit"}}}}))
            after, after_kw = surfaces()
            self.assertEqual((after["opaque_wall"]["u_value_w_m2k"], after["roof"]["u_value_w_m2k"]), (3.9, 1.73))
            self.assertIn("Concrete, dense", after["opaque_wall"]["source"])
            self.assertIn("Answered by the operators", after["roof"]["source"])
            self.assertGreater(after_kw, before_kw)                      # heavier, less insulated constructions

    def test_results_list_walls_and_roof_still_on_the_preliminary_u_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps({
                "included_scope_peak": {"components": {"envelope": {"total_kw": 1.2}}}}))
            with patch("backend.skill_workflow_service.get", return_value={}):
                gather = lambda: [row for row in result_status_service.gather(None, {"id": "j", "review_dir": folder})["items"]
                                  if row["topic"] == "Walls and roof"]
                [row] = gather()
                self.assertIn("external walls and exposed roof use the preliminary U-value (walls 0.6, roof 0.5", row["text"])
                (root / "construction_answers.json").write_text(json.dumps({"wall": {"all": {"u_value_w_m2k": 2}, "rooms": {}}}))
                [row] = gather()
                self.assertIn("The exposed roof use", row["text"])
                (root / "construction_answers.json").write_text(json.dumps({"wall": {"all": {"u_value_w_m2k": 2}}, "roof": {"rooms": {"Shop": {}}}}))
                self.assertEqual(gather(), [])


if __name__ == "__main__":
    unittest.main()
