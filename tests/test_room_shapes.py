"""Room outlines that read as a plan: walls found by their shape, open-plan areas divided at the printed room names,
AI outlines squared, and corner answers that collapse a room refused (the Turramurra house, 2026-11-20)."""

import json
import unittest

from shapely.geometry import Point, Polygon, box

from ai import autonomous_tasks
from ai import room_outline as ro

MM_PER_PX = 10.0     # 1 px = 10 mm
VIEWPORT = (0, 0, 1400, 1000)


def line(points, width=0.35, grey=0.0, filled=False):
    key = (width, grey, None)
    return {"kind": "line", "style": key, "style_id": ro.style_id(key), "points": points, "filled": filled}


def double_wall(x0, y0, x1, y1, thickness_px=11):
    """A rectangle of walls drawn as two parallel faces each, the way an architect draws them."""
    t = thickness_px
    outer = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    inner = [(x0 + t, y0 + t), (x1 - t, y0 + t), (x1 - t, y1 - t), (x0 + t, y1 - t), (x0 + t, y0 + t)]
    return [line([a, b]) for a, b in zip(outer, outer[1:])] + [line([a, b]) for a, b in zip(inner, inner[1:])]


class PairedWallTests(unittest.TestCase):
    def test_two_faces_a_wall_apart_enclose_a_room_whatever_pen_draws_them(self):
        objects = double_wall(100, 100, 700, 600)
        # Dimension lines in the same pen, 50 px (500 mm) apart: too far apart to be a wall's two faces.
        objects += [line([(100, 50), (700, 50)]), line([(100, 0), (700, 0)])]
        walls = ro.paired_wall_geometry(objects, VIEWPORT, MM_PER_PX)
        rooms = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX)
        self.assertEqual(len(rooms), 1)
        self.assertAlmostEqual(ro.area_m2(rooms[0], MM_PER_PX), (578 * 478) / 1e4, delta=0.5)   # inside faces
        self.assertFalse(walls.intersects(box(100, 0, 700, 50)))                               # no dimension "wall"

    def test_a_single_line_or_a_tile_grid_is_not_a_wall(self):
        tiles = [line([(0, y), (1000, y)], width=0.1) for y in range(0, 600, 6)]   # 60 mm grid, regular
        self.assertIsNone(ro.paired_wall_geometry(tiles + [line([(0, 900), (1000, 900)])], VIEWPORT, MM_PER_PX))

    def test_a_floor_fill_chosen_as_a_wall_pen_is_left_out(self):
        fill = line([(100, 100), (700, 100), (700, 600), (100, 600), (100, 100)], width=0.0, grey=1.0, filled=True)
        poche = line([(100, 100), (700, 100), (700, 120), (100, 120), (100, 100)], width=0.0, grey=1.0, filled=True)
        walls = ro.wall_geometry([fill, poche], [fill["style_id"]], VIEWPORT, mm_per_px=MM_PER_PX)
        self.assertAlmostEqual(walls.area, 600 * 20, delta=50)        # the 200 mm solid wall stays, the floor does not
        self.assertGreater(ro.wall_geometry([fill], [fill["style_id"]], VIEWPORT).area, 290000)  # without a scale: as before


class OpenPlanSplitTests(unittest.TestCase):
    def test_an_open_plan_area_is_divided_among_its_names_with_no_gap_or_overlap(self):
        area = box(0, 0, 1200, 600)
        bench = [((800, 0), (800, 250))]            # a counter end drawn between the kitchen and the dining
        labels = [("Kitchen", (1000, 200)), ("Dining", (500, 200)), ("Living", (500, 500))]
        pieces = ro.split_open_plan(area, labels, bench, MM_PER_PX)
        self.assertEqual(sorted(name for name, _ in pieces), ["Dining", "Kitchen", "Living"])
        self.assertAlmostEqual(sum(shape.area for _, shape in pieces), area.area, delta=1)
        for index, (_, first) in enumerate(pieces):
            for _, second in pieces[index + 1:]:
                self.assertLess(first.intersection(second).area, 1)
        kitchen = dict(pieces)["Kitchen"]
        self.assertAlmostEqual(kitchen.bounds[0], 800, delta=1)       # cut along the drawn counter end
        for _, shape in pieces:                                        # every piece is a rectangle
            self.assertAlmostEqual(shape.area, shape.envelope.area, delta=1)

    def test_one_name_keeps_the_area_whole_and_names_too_close_are_not_cut_apart(self):
        area = box(0, 0, 600, 400)
        self.assertEqual(ro.split_open_plan(area, [("Living", (300, 200))], [], MM_PER_PX), [("Living", area)])
        close = ro.split_open_plan(area, [("Living", (300, 200)), ("Lounge", (330, 210))], [], MM_PER_PX)
        self.assertEqual(len(close), 1)

    def test_names_printed_just_outside_an_area_count_for_it(self):
        area = box(0, 0, 600, 400)
        found = ro.labels_in_area(area, [("Kitchen", (300, 420)), ("Garage", (300, 900)), ("Kitchen", (100, 100))],
                                  MM_PER_PX)
        self.assertEqual([name for name, _ in found], ["Kitchen"])
        self.assertLessEqual(found[0][1][1], 400)             # moved onto the area's edge, so a cut keeps it


class SquaringAndCornerGuardTests(unittest.TestCase):
    def test_nearly_square_sides_are_made_square_and_real_diagonals_kept(self):
        skewed = Polygon([(0, 0), (400, 8), (404, 300), (-6, 296)])
        squared = ro.square_polygon(skewed)
        for (x0, y0), (x1, y1) in zip(squared.exterior.coords, list(squared.exterior.coords)[1:]):
            self.assertTrue(abs(x0 - x1) < 1e-6 or abs(y0 - y1) < 1e-6)
        diagonal = Polygon([(0, 0), (400, 0), (400, 300), (100, 300), (0, 200)])
        self.assertLess(ro.square_polygon(diagonal).hausdorff_distance(diagonal), 1e-6)

    def test_a_corner_answer_that_collapses_a_room_is_refused(self):
        corners = [[0, 0], [400, 0], [400, 300], [0, 300]]
        packet = {"room": "Kitchen", "corners": corners, "sketch_area_px": 120000.0, "tenancy_area_px": 1e7}
        with self.assertRaisesRegex(ValueError, "too little"):
            autonomous_tasks.validate_p0_outline_corners(packet, json.dumps({"corners": [1, 2, 3]}))
        whole = autonomous_tasks.validate_p0_outline_corners(packet, json.dumps({"corners": [1, 2, 3, 4]}))
        self.assertEqual(whole["corners"], [1, 2, 3, 4])
        # Larger than the sketch is allowed: the sketch may be a fragment of the room.
        packet["sketch_area_px"] = 30000.0
        self.assertEqual(autonomous_tasks.validate_p0_outline_corners(packet, json.dumps({"corners": [1, 2, 3, 4]}))["corners"],
                         [1, 2, 3, 4])
        # The tenancy is never judged against its sketch (Casa Nova, 2026-10-24).
        tenancy = {"room": None, "corners": corners, "sketch_area_px": 1e6}
        self.assertEqual(len(autonomous_tasks.validate_p0_outline_corners(tenancy, json.dumps({"corners": [1, 2, 3]}))["corners"]), 3)


if __name__ == "__main__":
    unittest.main()


class UnenclosedNamedFloorTests(unittest.TestCase):
    """A printed room name in floor no enclosed area holds: a kitchen open to the alfresco through bifold doors
    (Turramurra ground floor, 2026-11-21)."""

    def house(self, opening_px):
        # Two rooms side by side, 600 x 500 px each (6 x 5 m). The right room's outer wall has an opening.
        walls = [box(100, 100, 1300, 111), box(100, 589, 1300, 600), box(100, 100, 111, 600), box(694, 100, 705, 600)]
        top, bottom = 100 + (500 - opening_px) / 2.0, 100 + (500 + opening_px) / 2.0
        walls += [box(1289, 100, 1300, top), box(1289, bottom, 1300, 600)]
        from shapely.ops import unary_union
        return unary_union(walls)

    def test_a_named_room_open_to_the_outside_through_a_wide_opening_gets_its_floor(self):
        walls = self.house(opening_px=300)                # a 3 m bifold opening
        areas = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX)
        self.assertEqual(len(areas), 1)                    # the left room; the right one leaks outside
        labels = [("Living", (400, 350)), ("Kitchen", (1000, 350))]
        found = ro.unenclosed_named_floor(walls, areas, labels, VIEWPORT, MM_PER_PX)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(ro.area_m2(found[0], MM_PER_PX), 5.84 * 4.78, delta=1.0)
        self.assertTrue(found[0].contains(Point(1000, 350)))

    def test_floor_outside_the_building_or_open_along_many_metres_is_not_taken(self):
        walls = self.house(opening_px=300)
        areas = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX)
        # A name printed in the yard, outside the footprint.
        self.assertEqual(ro.unenclosed_named_floor(walls, areas, [("Garden", (1350, 800))], VIEWPORT, MM_PER_PX), [])
        # A name within 600 mm of an area is the naming step's to place.
        self.assertEqual(ro.unenclosed_named_floor(walls, areas, [("Kitchen", (740, 350))], VIEWPORT, MM_PER_PX), [])
        # A room missing its whole outer wall (5 m of edge with no wall, and more) leaks: not taken.
        from shapely.ops import unary_union
        open_walls = unary_union([box(100, 100, 1300, 111), box(100, 589, 1300, 600), box(100, 100, 111, 600),
                                  box(694, 100, 705, 600)])
        open_areas = ro.enclosed_rooms(open_walls, VIEWPORT, MM_PER_PX)
        self.assertEqual(ro.unenclosed_named_floor(open_walls, open_areas, [("Kitchen", (1000, 350))], VIEWPORT, MM_PER_PX), [])

    def test_floor_holding_two_names_is_left_alone(self):
        walls = self.house(opening_px=300)
        areas = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX)
        labels = [("Kitchen", (900, 350)), ("Dining", (1150, 350))]
        self.assertEqual(ro.unenclosed_named_floor(walls, areas, labels, VIEWPORT, MM_PER_PX), [])


class SameNamedPiecesTests(unittest.TestCase):
    def test_a_kitchen_island_55_mm_from_the_kitchen_floor_is_part_of_the_one_kitchen(self):
        # Turramurra (2026-11-21): the island's double edge line left 55 mm; at a 50 mm limit it became "Kitchen (2)".
        aisle, island = box(0, 0, 400, 100), box(0, 105.5, 300, 175)          # 10 mm/px: 55 mm apart
        self.assertEqual(ro.separate_rooms([aisle, island], MM_PER_PX), [[0, 1]])
        ensuite = box(0, 109, 300, 175)                                         # a 90 mm wall between: separate
        self.assertEqual(len(ro.separate_rooms([aisle, ensuite], MM_PER_PX)), 2)
