"""Phase 1 gives every traced room its walls, ceiling or roof and floor every time (user spec 2026-10-16): each surface
is itemised with its area, U-value and the temperature beyond it, and what the AI could not settle is filled with a
labelled fallback instead of being dropped."""

import json
from pathlib import Path
import tempfile
import unittest

from shapely.geometry import Polygon

from ai import ai_preliminary
from backend import ai_preliminary_service
from tests import test_reviewer_traced_preliminary as traced


def drawings(root, *notes):
    """The fixture's ai_input with page text (e.g. a ceiling-plan note)."""
    path = root / "ai_input.json"
    data = json.loads(path.read_text())
    data["drawing_set"] = {"pages": [{"page": 2, "title": "Reflective Ceiling Plan", "structured_content": {"markdown": note}}
                                     for note in notes]}
    path.write_text(json.dumps(data))


def phase1(root, room_use, proposal):
    prepared = traced.prepare(root, room_use, json.loads(json.dumps(proposal)))
    assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared, allow_area_fallbacks=False)
    room = assembled["material"]["hourly_load_model"]["rooms"][0]
    surfaces = {row["label"]: row for row in room["cooling_load"]["envelope_surfaces"]}
    return assembled, room, surfaces, ai_preliminary.calculate(assembled, scope="phase1_base_room")


class ItemisedPhaseOneTests(unittest.TestCase):
    def test_with_no_wall_or_roof_answers_the_room_still_gets_every_surface_with_labelled_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, trace = traced.write_fixture(root)
            traced.set_envelope_trace(root, trace, [], "unknown")        # the AI answered nothing
            drawings(root, "FLOOR TILES 600X600")                         # nothing about what is above or below
            assembled, room, surfaces, report = phase1(root, room_use, proposal)
        self.assertEqual(sorted(surfaces), ["Shop floor (slab)", "Shop traced footprint roof",
                                            "Shop wall 1 to a neighbouring tenancy", "Shop wall 2 to a neighbouring tenancy",
                                            "Shop wall 3 to a neighbouring tenancy", "Shop wall 4 to a neighbouring tenancy"])
        wall = surfaces["Shop wall 1 to a neighbouring tenancy"]
        self.assertAlmostEqual(wall["area_m2"], 0.27)                     # 0.1 m edge x the 2.7 m default height
        self.assertEqual(wall["boundary_temperature_c"], room["indoor_cooling_setpoint_c"])
        notes = [row["reason"] for row in assembled["phase1_assumptions"]]
        self.assertEqual(sum("walls 1, 2, 3, 4 of Shop" in note for note in notes), 1)   # one note for the room's walls
        self.assertTrue(any("2.7 m preliminary default height" in note for note in notes))
        self.assertTrue(any("roof is directly above (most likely for a single-storey shop" in note for note in notes))
        self.assertTrue(any("slab on the ground or over another conditioned space" in note for note in notes))
        # The exposed roof is the room's load; nothing is left unresolved.
        self.assertGreater(report["included_scope_peak"]["components"]["envelope"]["sensible_kw"], 0)
        # Only the sun on the roof stays open: the method has no cited horizontal solar profile yet (conduction only).
        self.assertEqual([row["component_id"] for row in report.get("unresolved_room_inputs", [])
                          if row.get("component_type") == "envelope"], ["roof_solar"])
        self.assertEqual(len(report["phase1_surface_audit"]), 6)

    def test_a_slab_over_the_ceiling_gives_a_ceiling_under_a_floor_above_at_no_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, trace = traced.write_fixture(root)
            traced.set_envelope_trace(root, trace, [], "unknown")
            drawings(root, "CH:2900MM STRUCTURE ABOVE. U/S OF SLAB 3750MM")
            _assembled, room, surfaces, report = phase1(root, room_use, proposal)
        ceiling = surfaces["Shop ceiling (floor above)"]
        self.assertEqual((ceiling["kind"], ceiling["boundary_temperature_c"]), ("ceiling", room["indoor_cooling_setpoint_c"]))
        self.assertNotIn("Shop traced footprint roof", surfaces)
        self.assertEqual(report["included_scope_peak"]["components"]["envelope"]["sensible_kw"], 0)

    def test_a_floor_over_a_car_park_is_counted_on_the_buffer_estimate_not_assumed_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, trace = traced.write_fixture(root)
            traced.set_envelope_trace(root, trace, [{"index": index, "boundary": "internal"} for index in range(4)], "not_exposed")
            drawings(root, "NEW TENANCY SLAB OVER CAR PARK BELOW")
            _assembled, room, surfaces, report = phase1(root, room_use, proposal)
        floor = surfaces["Shop floor over an unconditioned space"]
        self.assertIn("outdoor temperature minus 3 K", floor["boundary_source"])
        self.assertGreater(floor["boundary_temperature_c"], room["indoor_cooling_setpoint_c"])
        self.assertGreater(report["included_scope_peak"]["components"]["envelope"]["sensible_kw"], 0)
        self.assertEqual(surfaces["Shop wall 1 to another room"]["u_value_w_m2k"], 1.2)   # walls between rooms: partitions

    def test_a_wall_along_another_traced_room_faces_that_room(self):
        # 10 mm per pixel: a 10 m x 5 m shop.
        shop = Polygon([(0, 0), (1000, 0), (1000, 500), (0, 500)])
        part = {"room_id": "bar", "page": 20, "points_image_px": [[400, 100], [600, 100], [600, 200], [400, 200], [400, 100]]}
        # An island inside the shop: every wall faces the shop.
        self.assertEqual(ai_preliminary_service._edges_along_other_rooms(part, [0, 1, 2, 3], {20: [("shop", shop)]}, 10),
                         {0, 1, 2, 3})
        # A 5 m room beside the shop with a 200 mm wall between: only the shared wall faces it.
        kitchen = {"room_id": "kitchen", "page": 20, "points_image_px": [[1020, 0], [1520, 0], [1520, 500], [1020, 500], [1020, 0]]}
        self.assertEqual(ai_preliminary_service._edges_along_other_rooms(kitchen, [0, 1, 2, 3], {20: [("shop", shop)]}, 10), {3})
        self.assertEqual(ai_preliminary_service._edges_along_other_rooms(kitchen, [0, 1, 2, 3], {21: [("shop", shop)]}, 10), set())


if __name__ == "__main__":
    unittest.main()
