"""Kitchen appliance heat entered from the manufacturer's data sheet: sensible and moisture heat to the room, labelled,
in place of rating × a generic factor (values here are made up, not any manufacturer's)."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import ai_preliminary, equipment_heat as heat, internal_gains_resolution, room_use_resolution
from ai.heat_loads import equipment_load
from backend import ai_preliminary_service, page_extraction_service, result_status_service

OVEN = {"name": "Combi oven", "code": "E06", "quantity": 2, "room": "Kitchen", "rated_input_w": None, "heat_to_space_factor": None,
        "sensible_to_room_w": 1500.0, "latent_to_room_w": 900.0, "heat_reference": "Maker X combi 6-1/1 data sheet", "pages": [5]}


class DataSheetHeatTests(unittest.TestCase):
    def test_the_data_sheet_figure_replaces_rating_times_factor_and_moisture_needs_it(self):
        self.assertTrue(heat.from_data_sheet(OVEN))
        self.assertEqual((heat.heat_w(OVEN), heat.latent_w(OVEN)), (3000, 1800))
        # A rating and factor are ignored once the data sheet's figure is in.
        self.assertEqual(heat.heat_w({**OVEN, "rated_input_w": 18000, "heat_to_space_factor": 0.2}), 3000)
        # Moisture alone (no sensible figure) isn't counted; without either, rating × factor as before.
        self.assertEqual(heat.latent_w({**OVEN, "sensible_to_room_w": None}), 0)
        self.assertIsNone(heat.heat_w({**OVEN, "sensible_to_room_w": None}))
        self.assertEqual(heat.heat_w({"quantity": 2, "rated_input_w": 600, "heat_to_space_factor": 1}), 1200)

    def test_the_engine_adds_moisture_heat_with_the_items_diversity(self):
        source = {"name": "Combi oven", "kind": "other", "quantity": 2, "watts": 1500, "diversity_factor": 0.5,
                  "space_gain_factor": 1.0, "latent_w": 900}
        load = equipment_load([source])
        self.assertEqual((load["sensible_kw"], load["latent_kw"]), (1.5, 0.9))
        self.assertEqual(equipment_load([{**source, "latent_w": None}])["latent_kw"], 0)

    def test_accepting_checks_the_figures_and_asks_where_they_come_from(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / page_extraction_service.RESULT_FILE).write_text(json.dumps({"equipment_appliances": {
                "run": "r1", "findings": [{"id": "equipment_appliances:oven", "value": {"name": "Combi oven", "code": "E06", "quantity": 2},
                                           "conflicts": {}, "pages": [5], "citations": [], "evidence": "supported"}]}}))
            project = {"id": "j", "review_dir": str(root)}
            base = {"finding_id": "equipment_appliances:oven", "decision": "accepted"}
            item = {"name": "Combi oven", "code": "E06", "quantity": 2, "room": "Kitchen"}
            with patch.object(page_extraction_service, "rooms", return_value=["Kitchen"]):
                for value, message in (({**item, "sensible_to_room_w": 1500}, "where the heat figure comes from"),
                                       ({**item, "latent_to_room_w": 900, "heat_reference": "sheet"}, "heat to the room \\(sensible\\)"),
                                       ({**item, "sensible_to_room_w": 0, "heat_reference": "sheet"}, "above 0 W"),
                                       ({**item, "sensible_to_room_w": 1500, "latent_to_room_w": -1, "heat_reference": "sheet"}, "negative")):
                    with self.assertRaisesRegex(ValueError, message):
                        page_extraction_service.review(None, project, {**base, "value": value})
                state = page_extraction_service.review(None, project, {**base, "value": {
                    **item, "sensible_to_room_w": "1500", "latent_to_room_w": "900", "heat_reference": "  Maker X   combi "}})
            [finding] = state["findings"]
            self.assertEqual((finding["in_calculation"], finding["from_data_sheet"], finding["accepted_heat_w"], finding["accepted_latent_w"]),
                             (True, True, 3000, 1800))
            self.assertEqual(finding["decided_value"]["heat_reference"], "Maker X combi")

    def test_a_data_sheet_item_reaches_the_draft_with_its_moisture_heat_and_label(self):
        proposal = {"rooms": [{"label": "Kitchen", "room_id": "k"}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(page_extraction_service, "accepted", return_value=[OVEN]):
            result = ai_preliminary_service._with_accepted_equipment({"root": Path(folder)}, proposal)
        [row] = result["rooms"][0]["equipment"]
        self.assertEqual((row["rated_input_w"], row["heat_to_space_factor"], row["latent_w"]), (1500.0, 1.0, 900.0))
        self.assertIn("Data sheet (Maker X combi 6-1/1 data sheet)", row["source"])

        building = {"spaces": [{"name": "Kitchen", "level_name": "Ground", "area_m2": 30, "evidence": [{"page": 2, "excerpt": "KITCHEN"}]}]}
        room = {"kind": "room", "label": "Kitchen", "level_name": "Ground", "area_m2": 30, "preliminary_profile_id": "hospitality", "page": 2,
                "equipment": [{**row, "evidence": [{"page": 5, "excerpt": "E06 Combi oven"}]}]}
        drawing = {"rooms": [room]}
        use = room_use_resolution.resolve(building, proposal=drawing, source_fingerprints={})
        gains = internal_gains_resolution.resolve(building, proposal=drawing, room_use=use, pack=ai_preliminary.load_pack(),
                                                  source_fingerprints={"proposal": "a"})
        out = ai_preliminary.assemble(building, preliminary_proposal=drawing, room_use_resolution=use, internal_gains_resolution=gains)
        oven = next(source for source in out["material"]["requirements"]["zones"][0]["heat_sources"] if source["name"] == "Combi oven")
        self.assertEqual((oven["watts"], oven["space_gain_factor"], oven["latent_w"]), (1500.0, 1.0, 900.0))
        with_moisture = ai_preliminary.calculate(out)["included_scope_peak"]["components"]["equipment_refrigeration"]
        oven_dry = {**room, "equipment": [{**room["equipment"][0], "latent_w": 0}]}
        use = room_use_resolution.resolve(building, proposal={"rooms": [oven_dry]}, source_fingerprints={})
        gains = internal_gains_resolution.resolve(building, proposal={"rooms": [oven_dry]}, room_use=use, pack=ai_preliminary.load_pack(),
                                                  source_fingerprints={"proposal": "a"})
        dry = ai_preliminary.calculate(ai_preliminary.assemble(building, preliminary_proposal={"rooms": [oven_dry]}, room_use_resolution=use,
                                                               internal_gains_resolution=gains))["included_scope_peak"]["components"]["equipment_refrigeration"]
        self.assertGreater(with_moisture["latent_kw"], dry["latent_kw"])
        self.assertAlmostEqual(with_moisture["sensible_kw"], dry["sensible_kw"], places=3)

    def test_results_list_kitchen_appliances_not_yet_counted_and_skip_data_sheet_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "page_extraction.json").write_text(json.dumps({"equipment_appliances": {"run": "r1", "findings": [
                {"id": "a", "value": {"code": "E06", "name": "COMBI OVEN"}}, {"id": "b", "value": {"code": "E07", "name": "DEEP FRYER"}},
                {"id": "c", "value": {"code": "E08", "name": "GRIDDLE"}}, {"id": "d", "value": {"code": "E09", "name": "DISHWASHER"}}]}}))
            (root / "page_extraction_decisions.json").write_text(json.dumps({"equipment_appliances": {
                "a": {"status": "accepted", "run": "r1", "value": {**OVEN, "name": "COMBI OVEN"}},
                "c": {"status": "rejected", "run": "r1", "value": None},
                "d": {"status": "accepted", "run": "r1", "value": {"code": "E09", "name": "DISHWASHER", "rated_input_w": None}}}}))
            with patch("backend.skill_workflow_service.get", return_value={}):
                items = result_status_service.gather(None, {"id": "j", "review_dir": str(root)})["items"]
        kitchen = [row for row in items if row["topic"] == "Kitchen equipment"]
        self.assertEqual(len(kitchen), 1)
        self.assertEqual(kitchen[0]["kind"], "to_find")
        self.assertEqual(kitchen[0]["detail"], "E07 DEEP FRYER, E09 DISHWASHER")   # the oven is counted, the griddle rejected
        self.assertFalse(any("COMBI OVEN" in row.get("detail", "") for row in items))


if __name__ == "__main__":
    unittest.main()
