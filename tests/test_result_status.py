#!/usr/bin/env python3
"""What in a result is still assumed, a placeholder or still to find."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import job_service, result_status_service as status, skill_workflow_service


def write(root, name, value):
    (Path(root) / name).write_text(json.dumps(value))


class GatherTests(unittest.TestCase):
    def test_every_kind_of_gap_is_found_and_ordered_to_find_first(self):
        with tempfile.TemporaryDirectory() as folder:
            write(folder, "hourly_ai_preliminary_load_report.json", {
                "design_conditions_basis": {"design_day": {"site_specific": False}, "sun": {"site_specific": False}, "site": {"confirmed": False}},
                "included_scope_peak": {"components": {"glazing_solar": {"total_kw": 2}}}})
            write(folder, "internal_gains_resolution.json", {"records": [
                {"room_id": "r:shop", "original_label": "Shop", "status": "provisional", "fields": {
                    "occupancy_count": {"origin": "controlled_preliminary_profile"}, "lighting_load_w": {"origin": "unresolved"},
                    "equipment": {"origin": "direct_project_evidence"}}},
                {"room_id": "r:kitchen", "original_label": "Kitchen", "status": "provisional", "fields": {
                    "occupancy_count": {"origin": "contractor_override"}, "lighting_load_w": {"origin": "contractor_override"},
                    "equipment": {"origin": "direct_project_evidence"}}},
                {"room_id": "r:cool", "original_label": "Coolroom", "status": "excluded", "fields": {"occupancy_count": {"origin": "unresolved"}}}],
                "schedules": [{"room_id": "r:shop", "origin": "controlled_preliminary_profile"}, {"room_id": "r:kitchen", "origin": "direct_project_evidence"}]})
            write(folder, "page_extraction.json", {"equipment_appliances": {"run": "r1", "findings": [
                {"id": "eq:fridge", "value": {"code": "E21", "name": "UB FRIDGE"}}, {"id": "eq:oven", "value": {"code": "E06", "name": "COMBI OVEN", "under_hood": True}},
                {"id": "eq:tv", "value": {"name": "TV"}}]}})
            write(folder, "page_extraction_decisions.json", {"equipment_appliances": {
                "eq:fridge": {"status": "accepted", "run": "r1", "value": {"code": "E21", "name": "UB FRIDGE", "rated_input_w": 350, "heat_to_space_factor": 1.0}},
                "eq:oven": {"status": "accepted", "run": "r1", "value": {"code": "E06", "name": "COMBI OVEN", "rated_input_w": 18000, "heat_to_space_factor": 0.3}}}})
            write(folder, "exhaust_answers.json", {"rooms": {"Kitchen": {"lps": 1200, "method": "through_space", "method_assumed": True}}})
            review = {"answers": {"information_needs:needs:1": {"target": "Tenancy", "field": "operating_hours"}}, "findings": [
                {"id": "information_needs:needs:0", "subskill_id": "information_needs", "field": "needs",
                 "value": {"target": "E06 Combi oven", "field": "rated_input_w", "why": "No rating printed.", "where_to_look": "spec sheet"}},
                {"id": "information_needs:needs:1", "subskill_id": "information_needs", "field": "needs", "value": {"target": "Tenancy", "field": "operating_hours"}},
                {"id": "lighting_evidence:lighting:0", "subskill_id": "lighting_evidence", "field": "lighting", "status": "proposed"}]}
            with patch.object(skill_workflow_service, "get", return_value=review), patch.object(job_service, "job_setup", return_value={"above": ""}):
                result = status.gather(None, {"id": "j", "review_dir": folder})
        items = result["items"]
        texts = {(row["kind"], row["topic"]): row for row in items}
        self.assertEqual([row["kind"] for row in items], sorted((row["kind"] for row in items), key=status.ORDER.get))
        self.assertIn(("to_find", "E06 Combi oven"), texts)                         # unanswered need
        self.assertNotIn(("to_find", "Tenancy"), texts)                             # answered need
        self.assertIn("1 finding(s)", texts[("to_find", "PDF review")]["text"])
        self.assertIn("1 equipment item(s)", texts[("to_find", "Equipment")]["text"])   # the TV, not reviewed
        self.assertEqual({row["topic"] for row in items if row["kind"] == "not_set"}, {"Site", "Roof"})
        self.assertIn("1200 L/s assumed", texts[("assumed", "Kitchen")]["text"])
        shop = [row["text"] for row in items if row["topic"] == "Shop"]
        self.assertTrue(any("people (typical density), lighting (typical W/m²)" in text for text in shop))
        self.assertTrue(any("Opening hours are typical" in text for text in shop))
        self.assertFalse(any(row["topic"] in {"Kitchen", "Coolroom"} and row["kind"] == "typical" for row in items))
        equipment = [row for row in items if row["topic"] == "Equipment"]
        self.assertEqual(next(row for row in equipment if row["kind"] == "typical")["detail"], "E21 UB FRIDGE")    # typical rating kept
        self.assertEqual(next(row for row in equipment if row["kind"] == "placeholder")["detail"], "E21 UB FRIDGE")  # oven factor was edited
        self.assertIn(("typical", "Windows"), texts)
        self.assertEqual({row["topic"] for row in items if row["kind"] == "placeholder"} - {"Equipment"}, {"Weather", "Sun"})

    def test_a_room_topped_up_to_its_equipment_allowance_is_listed_as_typical(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(skill_workflow_service, "get", return_value={}), \
                patch.object(job_service, "job_setup", return_value={"above": "roof"}):
            write(folder, "hourly_ai_preliminary_load_report.json", {"scenario_results": [{"rooms": [
                {"name": "Shop", "hours": [{"contributions": [{"inputs": {"sources": [
                    {"name": "POS"}, {"name": "Typical equipment allowance top-up (listed items are below the room's typical level)"}]}}]}]},
                {"name": "Kitchen", "hours": [{"contributions": [{"inputs": {"sources": [{"name": "Combi oven"}]}}]}]}]}]})
            result = status.gather(None, {"id": "j", "review_dir": folder})
        topped = [row for row in result["items"] if "topped up" in row["text"]]
        self.assertEqual([(row["kind"], row["topic"]) for row in topped], [("typical", "Shop")])

    def test_design_days_from_the_nearest_listed_location_are_named(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(skill_workflow_service, "get", return_value={}), \
                patch.object(job_service, "job_setup", return_value={"above": "roof"}):
            write(folder, "hourly_ai_preliminary_load_report.json", {"design_conditions_basis": {"design_day": {
                "site_specific": True, "origin": "design_temperature_table_nearest_location", "location": "Sydney",
                "distance_km": 14.4, "far_from_site": False, "label": "Summer design days for Sydney"}}})
            result = status.gather(None, {"id": "j", "review_dir": folder})
        weather = [row for row in result["items"] if row["topic"] == "Weather"]
        self.assertEqual([row["kind"] for row in weather], ["assumed"])
        self.assertIn("Sydney, the nearest listed location (14.4 km)", weather[0]["text"])

    def test_a_job_with_nothing_calculated_or_answered_gives_only_what_it_can(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(skill_workflow_service, "get", return_value={}), \
                patch.object(job_service, "job_setup", return_value={"above": "roof"}):
            result = status.gather(None, {"id": "j", "review_dir": folder})
        self.assertEqual((result["items"], result["calculated"]), ([], False))


if __name__ == "__main__":
    unittest.main()
