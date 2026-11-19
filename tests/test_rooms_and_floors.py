"""Job A (2026.08.06 set, 2026-11-18): Claude's pixel coordinates read as 0-1000 grid values, plan room lists the
naming step never saw, and "Ensuite (First)" for a first-floor room."""

import json
import tempfile
import unittest
from pathlib import Path

from ai.page_inventory import level_display_name
from backend import autonomous_tasks_service, drawing_tasks_service as drawing
from backend.phase1_supervisor_service import room_display_name


class Provider:
    def __init__(self, model, name="openrouter"):
        self.model, self.name = model, name


class CoordinateFrameTests(unittest.TestCase):
    def test_claude_is_asked_for_pixels_and_its_reply_is_not_converted(self):
        sizes = [(1536, 1106)]
        self.assertFalse(drawing.uses_grid("P0_unnamed_space", sizes, Provider("anthropic/claude-haiku-5.5")))
        self.assertFalse(drawing.uses_grid("P0_tenancy_outline", sizes, Provider("claude-sonnet-5-5", "anthropic")))
        self.assertTrue(drawing.uses_grid("P0_unnamed_space", sizes, Provider("google/gemini-3.8-flash")))
        self.assertTrue(drawing.uses_grid("P0_unnamed_space", sizes))                  # no provider known: as before
        self.assertFalse(drawing.uses_grid("P0_outline_corners", sizes, Provider("google/gemini-3.8-flash")))

    def test_a_reply_beyond_the_grid_is_already_in_pixels(self):
        # Job A's tenancy: the house's east wall at x 1022-1024 on a 1,536-px plan; as grid values it went to x 1572.
        tenancy = {"points_px": [[497, 440], [1022, 446], [1024, 755], [486, 640]], "evidence": "east wall x≈1022"}
        self.assertEqual(drawing.grid_to_pixels(tenancy, (1536, 1106)), tenancy)
        on_grid = {"spaces": [{"room": "Living", "points_px": [[400, 400], [500, 500]], "reason": ""}]}
        self.assertEqual(drawing.grid_to_pixels(on_grid, (1536, 1106))["spaces"][0]["points_px"],
                         [[614.4, 442.4], [768.0, 553.0]])
        north = {"found": True, "crop_index": 0, "tail_px": [435, 754], "tip_px": [489, 642]}
        self.assertEqual(drawing.grid_to_pixels(north, (1024, 526))["tail_px"], [445.4, 396.6])
        self.assertEqual(drawing.grid_to_pixels(on_grid, None), on_grid)


class ReadingRoomNameTests(unittest.TestCase):
    def names(self, information):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "page_inventory.json").write_text(json.dumps({"pages": {"7": {"information": information}}}))
            return autonomous_tasks_service._reading_room_names(Path(folder), 7)

    def test_a_plain_list_of_plan_labels_gives_the_rooms(self):
        ground = [{"kind": "room_geometry", "evidence": "room labels on plan",
                   "what": "Infinity pool, spa and deck at rear; living & dining, kitchen, WIR, ensuite, lounge, porch "
                           "and existing garage"},
                  {"kind": "room_geometry", "what": "1200mm pool fence complies with AS1926.1-2012",
                   "evidence": "annotation at top"}]
        self.assertEqual(self.names(ground), ["Deck", "Ensuite", "Existing garage", "Infinity pool", "Kitchen",
                                              "Living & dining", "Lounge", "Porch", "Spa", "WIR"])
        first = [{"kind": "room_geometry", "what": "High ceiling area, rumpus, WIR, ensuite and minibar",
                  "evidence": "room labels on plan"}]
        self.assertEqual(self.names(first), ["Ensuite", "Minibar", "Rumpus", "WIR"])      # "High ceiling area" is a void

    def test_labels_after_a_lead_in_and_dimension_clauses_are_left_out(self):
        basement = [{"kind": "room_geometry", "evidence": "room labels and dimension strings",
                     "what": "Basement plan with labels CELLAR, CHILLED CELLAR STORAGE, STAIR, LIFT; grid dimensions "
                             "including bottom run 4.420 m, 3.040 m; overall width 24, grid lines A-G"}]
        self.assertEqual(self.names(basement), ["CELLAR", "CHILLED CELLAR STORAGE", "LIFT", "STAIR"])
        # A list whose evidence isn't the plan's labels isn't read as rooms.
        self.assertEqual(self.names([{"kind": "room_geometry", "what": "Kitchen, dining", "evidence": "furniture"}]), [])
        # The "includes" form reads as before.
        self.assertEqual(self.names([{"kind": "room_geometry", "evidence": "x",
                                      "what": "Ground floor includes Garage, Lift and Bath."}]), ["Bath", "Garage", "Lift"])


class LevelNameTests(unittest.TestCase):
    def test_a_bare_level_reads_as_a_floor(self):
        self.assertEqual([level_display_name(value) for value in
                          ("First", "Ground", "Lower Ground", "first", "Ground Floor", "Basement", "Level 1", "", None)],
                         ["First Floor", "Ground Floor", "Lower Ground Floor", "first Floor", "Ground Floor", "Basement",
                          "Level 1", "", ""])

    def test_a_room_shown_with_its_level_uses_the_full_level_name(self):
        records = [{"room_id": "room-use:ground-floor:ensuite", "label": "Ensuite", "level_name": "Ground Floor"},
                   {"room_id": "room-use:first:ensuite", "label": "Ensuite", "level_name": "First"},
                   {"room_id": "room-use:basement:storage", "label": "Storage", "level_name": "Basement"}]
        self.assertEqual([room_display_name(row, records) for row in records],
                         ["Ensuite (Ground Floor)", "Ensuite (First Floor)", "Storage"])


if __name__ == "__main__":
    unittest.main()
