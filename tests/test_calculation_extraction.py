import unittest

from ai.calculation_extraction import extract_calculation_input_evidence, normalise_for_hourly_model
from ai.evidence_binding import bind_calculation_evidence


class CalculationExtractionTests(unittest.TestCase):
    def setUp(self):
        self.ai = {
            "source_pdf": "drawing-6.pdf",
            "drawing_set": {"pages": [
                {"page": 20, "drawing_number": "202", "title": "Dimensioned Floor Plan", "sheet_classification": "floor_plan", "structured_content": {"markdown": "Cool Room AREA: 18.4 m² 4200 mm x 4400 mm external wall"}},
                {"page": 22, "drawing_number": "205", "title": "Reflected Ceiling / Lighting Plan", "sheet_classification": "reflected_ceiling_plan", "structured_content": {"markdown": "COOL ROOM ceiling 3.0 m 20W LED QTY: 6"}},
                {"page": 26, "drawing_number": "300", "title": "Shopfront Elevation", "sheet_classification": "elevation", "structured_content": {"markdown": "W01 1200 x 2100 mm"}},
                {"page": 30, "drawing_number": "S01", "title": "Equipment Schedule", "sheet_classification": "schedule", "structured_content": {"markdown": "Display fridge 1 2.0 kW"}},
                {"page": 31, "drawing_number": "N01", "title": "General Notes", "sheet_classification": "schedule", "structured_content": {"markdown": "Occupancy 12 people outside air 120 L/s cooling setpoint 24 C U-value 0.32 W/m2K Weekdays 08:00-18:00"}},
            ]},
        }
        self.coverage = {"page_roles": [
            {"page": 20, "proposed_role": "main_floor_plan"},
            {"page": 22, "proposed_role": "reflected_ceiling_plan"},
            {"page": 26, "proposed_role": "opening_elevation"},
            {"page": 30, "proposed_role": "schedule"},
            {"page": 31, "proposed_role": "reference"},
        ]}
        self.ocr = {"pages": [{"page": 20, "room_label_candidates": [{"text": "Cool Room", "status": "room_label"}]}, {"page": 22, "room_label_candidates": [{"text": "COOL ROOM", "status": "room_label"}]}]}

    def test_extracts_supported_values_with_citations(self):
        evidence = extract_calculation_input_evidence(self.ai, self.coverage, self.ocr)
        targets = {row["target"] for row in evidence["candidates"]}
        self.assertIn("room.Cool Room.area_m2", targets)
        self.assertIn("room.COOL ROOM.ceiling_height", targets)
        self.assertTrue(any(row["category"] == "opening" and row["value"]["tag"] == "W01" for row in evidence["candidates"]))
        self.assertTrue(all(row["source"]["page"] in {20, 22, 26, 30, 31} for row in evidence["candidates"]))

    def test_equipment_is_evidence_only(self):
        evidence = extract_calculation_input_evidence(self.ai, self.coverage, self.ocr)
        equipment = [row for row in evidence["candidates"] if row["category"] == "equipment"]
        self.assertTrue(equipment)
        self.assertTrue(all(row["status"] == "evidence_only" for row in equipment))
        self.assertTrue(all("heat_to_space_basis" in row["unresolved_fields"] for row in equipment))

    def test_ids_do_not_depend_on_page_order(self):
        first = extract_calculation_input_evidence(self.ai, self.coverage, self.ocr)
        reordered = dict(self.ai)
        reordered["drawing_set"] = {"pages": list(reversed(self.ai["drawing_set"]["pages"]))}
        second = extract_calculation_input_evidence(reordered, self.coverage, self.ocr)
        self.assertEqual({row["candidate_id"] for row in first["candidates"]}, {row["candidate_id"] for row in second["candidates"]})

    def test_conflicting_same_target_is_blocked(self):
        ai = dict(self.ai)
        ai["drawing_set"] = {"pages": self.ai["drawing_set"]["pages"] + [{"page": 32, "drawing_number": "N02", "title": "Area Note", "sheet_classification": "floor_plan", "structured_content": {"markdown": "Cool Room AREA: 21.0 m²"}}]}
        coverage = dict(self.coverage)
        coverage["page_roles"] = list(self.coverage["page_roles"]) + [{"page": 32, "proposed_role": "main_floor_plan"}]
        evidence = extract_calculation_input_evidence(ai, coverage, self.ocr)
        area = [row for row in evidence["candidates"] if row["target"] == "room.Cool Room.area_m2"]
        self.assertGreaterEqual(len(area), 2)
        self.assertTrue(all(row["status"] == "conflict" for row in area))

    def test_hourly_mapping_only_uses_exact_room_match(self):
        evidence = extract_calculation_input_evidence(self.ai, self.coverage, self.ocr)
        model = {"rooms": [{"room_id": "cool_1", "name": "Cool Room"}]}
        mapped = normalise_for_hourly_model(evidence, model)
        area = next(row for row in mapped["candidates"] if row["category"] == "area")
        self.assertEqual(area["status"], "active")
        self.assertEqual(area["target_path"], "rooms.cool_1.area_m2")
        ceiling = next(row for row in mapped["candidates"] if row["category"] == "ceiling_height")
        self.assertEqual(ceiling["status"], "active")

    def test_table_cells_are_retained_as_cited_observations(self):
        ocr = {"pages": [{"page": 26, "table_cells": [
            {"table_id": "windows", "row": "W01", "column": "Width", "text": "1200", "bbox": [10, 20, 40, 30]}
        ]}]}
        evidence = extract_calculation_input_evidence(self.ai, self.coverage, ocr)
        cells = [row for row in evidence["binding"]["observations"] if row["type"] == "table_cell"]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["row"], "W01")
        self.assertEqual(cells[0]["source"]["page"], 26)

    def test_unique_plan_elevation_opening_is_bound(self):
        evidence = {
            "source_fingerprint": "source",
            "candidates": [
                {"candidate_id": "plan", "category": "opening", "target": "opening.W01", "value": {"tag": "W01"}, "source": {"page": 20}, "status": "proposed"},
                {"candidate_id": "elevation", "category": "opening", "target": "opening.W01", "value": {"tag": "W01", "width_mm": 1200, "height_mm": 2100}, "source": {"page": 26}, "status": "active"},
            ],
            "issues": [],
        }
        coverage = {"page_roles": [{"page": 20, "proposed_role": "main_floor_plan"}, {"page": 26, "proposed_role": "opening_elevation"}]}
        bound = bind_calculation_evidence(evidence, coverage=coverage)
        self.assertEqual(len(bound["binding"]["relationships"]), 1)
        self.assertEqual(bound["binding"]["relationships"][0]["kind"], "opening_plan_elevation_binding")
        self.assertFalse(bound["binding"]["conflicts"])

    def test_ambiguous_opening_binding_is_blocked(self):
        evidence = {
            "source_fingerprint": "source",
            "candidates": [
                {"candidate_id": "plan1", "category": "opening", "target": "opening.W01", "value": {"tag": "W01"}, "source": {"page": 20}, "status": "proposed"},
                {"candidate_id": "plan2", "category": "opening", "target": "opening.W01", "value": {"tag": "W01"}, "source": {"page": 21}, "status": "proposed"},
                {"candidate_id": "elevation", "category": "opening", "target": "opening.W01", "value": {"tag": "W01", "width_mm": 1200, "height_mm": 2100}, "source": {"page": 26}, "status": "active"},
            ],
            "issues": [],
        }
        coverage = {"page_roles": [{"page": 20, "proposed_role": "main_floor_plan"}, {"page": 21, "proposed_role": "supporting_geometry_plan"}, {"page": 26, "proposed_role": "opening_elevation"}]}
        bound = bind_calculation_evidence(evidence, coverage=coverage)
        self.assertEqual(len(bound["binding"]["conflicts"]), 1)
        self.assertTrue(all(row["status"] == "conflict" for row in bound["candidates"]))

    def test_vision_disagreement_is_a_conflict(self):
        evidence = {
            "source_fingerprint": "source",
            "candidates": [
                {"candidate_id": "pdf", "category": "area", "target": "room.Cool Room.area_m2", "value": 18, "source": {"page": 20}, "status": "active", "extraction_method": "explicit_room_area"},
                {"candidate_id": "vision", "category": "area", "target": "room.Cool Room.area_m2", "value": 20, "source": {"page": 20}, "status": "proposed", "extraction_method": "manual_vision_response"},
            ],
            "issues": [],
        }
        bound = bind_calculation_evidence(evidence)
        self.assertEqual(bound["binding"]["conflicts"][0]["kind"], "vision_pdf_value_conflict")
        self.assertTrue(all(row["status"] == "conflict" for row in bound["candidates"]))


if __name__ == "__main__":
    unittest.main()
