"""Walls to an unconditioned space (plant room, store, dock): counted against the operators' answered temperature of the
space beyond, conduction only; not counted, and listed as to find, until that temperature is answered."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai import ai_preliminary, ceiling_volume_resolution, reviewer_room_geometry
from backend import need_answers_service, result_status_service
from tests import test_reviewer_traced_preliminary as traced

ROOMS = [{"label": "Shop", "level": "Level 1"}, {"label": "Bar", "level": "Level 1"}]


def draft(root, room_use, proposal):
    prepared = traced.prepare(root, room_use, json.loads(json.dumps(proposal)))
    assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared, allow_area_fallbacks=False)
    return assembled, ai_preliminary.calculate(assembled)


class UnconditionedWallTests(unittest.TestCase):
    def test_the_boundary_is_accepted_on_a_traced_edge(self):
        checked = reviewer_room_geometry.validate_envelope_classification([{"index": 0, "boundary": "unconditioned"}], "unknown", 4)
        self.assertEqual([edge["boundary"] for edge in checked["edges"]], ["unconditioned", "unknown", "unknown", "unknown"])
        with self.assertRaisesRegex(ValueError, "unconditioned or unknown"):
            reviewer_room_geometry.validate_envelope_classification([{"index": 0, "boundary": "garage"}], "unknown", 4)

    def test_a_wall_uses_the_buffer_estimate_until_the_temperature_is_answered_then_conducts_against_it_without_sun(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, trace = traced.write_fixture(root)
            traced.set_envelope_trace(root, trace, [{"index": 0, "boundary": "unconditioned"}, {"index": 1, "boundary": "internal"},
                                                    {"index": 2, "boundary": "internal"}, {"index": 3, "boundary": "internal"}], "not_exposed")
            (root / "ceiling_volume_resolution.json").write_text(json.dumps({"records": [
                {"room_id": ceiling_volume_resolution.room_identity("Shop", "Level 1"), "status": "resolved", "ceiling_height_mm": 3000}]}))

            assembled, report = draft(root, room_use, proposal)
            room = assembled["material"]["hourly_load_model"]["rooms"][0]
            # Every surface is itemised: the unconditioned wall on the buffer estimate, three internal walls and the
            # ceiling under a floor above at the room's temperature, and the slab floor.
            surfaces = {row["label"]: row for row in room["cooling_load"]["envelope_surfaces"]}
            buffer = surfaces["Shop wall 1 to an unconditioned space"]
            self.assertEqual(buffer["boundary_method"], "fixed_adjacent_temperature")
            self.assertIn("outdoor temperature minus 3 K", buffer["boundary_source"])
            self.assertEqual({label for label in surfaces} - {buffer["label"]},
                             {"Shop wall 2 to another room", "Shop wall 3 to another room", "Shop wall 4 to another room",
                              "Shop ceiling (floor above)", "Shop floor (slab)"})
            self.assertEqual(surfaces["Shop wall 2 to another room"]["boundary_temperature_c"], room["indoor_cooling_setpoint_c"])
            self.assertFalse(any(row["component_id"].startswith("unconditioned_wall_temperature")
                                 for row in report.get("unresolved_room_inputs", [])))
            (root / "hourly_ai_preliminary_load_report.json").write_text(json.dumps(report))
            (root / "ai_preliminary_input_set.json").write_text(json.dumps(assembled))
            with patch("backend.skill_workflow_service.get", return_value={}):
                items = result_status_service.gather(None, {"id": "j", "review_dir": folder})["items"]
            self.assertEqual([row["kind"] for row in items if row["topic"] == "Walls to unconditioned spaces"], ["assumed"])

            need_answers_service._save_unconditioned({"review_dir": folder}, {"value": "38", "note": "Plant room, unventilated",
                                                                             "source": "site_visit"}, ROOMS, "Sam")
            assembled, report = draft(root, room_use, proposal)
            [wall] = [row for row in assembled["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_surfaces"]
                      if row["label"] == "Shop wall 1 to an unconditioned space"]
            self.assertEqual((wall["kind"], wall["boundary_method"], wall["boundary_temperature_c"], wall["solar_gain_factor"]),
                             ("opaque_wall", "fixed_adjacent_temperature", 38.0, 0))
            self.assertIn("Plant room, unventilated", wall["boundary_source"])
            self.assertAlmostEqual(wall["area_m2"], 0.3)                         # 0.1 m edge × 3 m ceiling, as for an outside wall
            envelope = report["included_scope_peak"]["components"]["envelope"]
            indoor = room["indoor_cooling_setpoint_c"]
            self.assertAlmostEqual(envelope["sensible_kw"], round(0.3 * wall["u_value_w_m2k"] * (38 - indoor) / 1000, 4), places=4)
            self.assertFalse(any(row["component_id"].startswith("unconditioned_wall_temperature")
                                 for row in report.get("unresolved_room_inputs", [])))

    def test_the_temperature_answer_is_checked_and_a_room_answer_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"review_dir": folder}
            for value in ("5", "80", "warm"):
                with self.assertRaises(ValueError):
                    need_answers_service._save_unconditioned(project, {"value": value}, ROOMS, "Sam")
            self.assertEqual(need_answers_service.unconditioned_temperature(folder, "Shop"), (None, None))
            result = need_answers_service._save_unconditioned(project, {"value": "32"}, ROOMS, "Sam")
            self.assertEqual(result["summary"], "Every room's walls to unconditioned spaces: 32 °C beyond them.")
            need_answers_service._save_unconditioned(project, {"value": "40", "room": "Bar"}, ROOMS, "Sam")
            self.assertEqual(need_answers_service.unconditioned_temperature(folder, "bar")[0], 40.0)
            self.assertEqual(need_answers_service.unconditioned_temperature(folder, "Shop")[0], 32.0)


if __name__ == "__main__":
    unittest.main()
