"""A house's outside walls and its walls and floors next to spaces that aren't cooled (ai/house_envelope.py)."""

import unittest

from ai import house_envelope as he


def square(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]


GARAGE = {"label": "Garage", "category": "garage", "scope": "not_cooled"}
BALCONY = {"label": "Balcony", "category": "outdoor", "scope": "not_cooled"}
LIVING = {"label": "Living", "category": "house_living", "scope": "comfort_hvac"}


class LevelTests(unittest.TestCase):
    def test_level_order(self):
        ranks = [he.level_rank(name) for name in ("Basement", "Lower Ground Floor", "Ground Floor", "First Floor",
                                                  "Level 2", "Second floor", "Unassigned level", "")]
        self.assertEqual(ranks, [-2, -1, 0, 1, 2, 2, None, None])


class WallTests(unittest.TestCase):
    def test_a_reviewers_wall_stands(self):
        self.assertEqual(he.wall_boundary("internal", "reviewer", GARAGE), ("internal", ""))

    def test_a_wall_to_a_space_that_isnt_cooled_is_unconditioned(self):
        boundary, note = he.wall_boundary("internal", "ai_determined", GARAGE)
        self.assertEqual(boundary, "unconditioned")
        self.assertIn("Garage", note)
        self.assertEqual(he.wall_boundary("unknown", "", {"label": "Ensuite", "category": "house_wet"})[0], "unconditioned")

    def test_a_garage_the_drawings_show_air_conditioned_is_a_cooled_neighbour(self):
        cooled_garage = dict(GARAGE, scope="comfort_hvac")
        self.assertEqual(he.wall_boundary("internal", "ai_determined", cooled_garage), ("internal", ""))
        self.assertEqual(he.wall_boundary("unknown", "", cooled_garage), ("internal", ""))

    def test_a_wall_to_an_outdoor_space_or_to_nothing_faces_outside(self):
        self.assertEqual(he.wall_boundary("unknown", "", BALCONY)[0], "external")
        boundary, note = he.wall_boundary("unknown", "", None)
        self.assertEqual(boundary, "external")
        self.assertIn("labelled assumption", note)

    def test_a_wall_to_a_cooled_room_stays_internal_and_classified_walls_are_kept(self):
        self.assertEqual(he.wall_boundary("unknown", "", LIVING), ("internal", ""))
        self.assertEqual(he.wall_boundary("internal", "ai_determined", LIVING), ("internal", ""))
        self.assertEqual(he.wall_boundary("external", "ai_determined", GARAGE), ("external", ""))


class StackingTests(unittest.TestCase):
    def test_stairs_traced_on_both_levels_give_the_offset(self):
        upper = [{"label": "Stair", "category": "circulation", "points_mm": square(1000, 1000, 1000, 3000)}]
        lower = [{"label": "Stairs", "category": "circulation", "points_mm": square(1500, 800, 1000, 3000)}]
        dx, dy, evidence = he.stacked_offset(upper, lower)
        self.assertEqual((round(dx), round(dy)), (500, -200))
        self.assertIn("Stair above Stairs", evidence)

    def test_no_link_a_different_size_or_disagreeing_links_give_none(self):
        stair = {"label": "Stair", "category": "circulation", "points_mm": square(0, 0, 1000, 3000)}
        self.assertIsNone(he.stacked_offset([stair], [{"label": "Bed", "category": "house_bedroom", "points_mm": square(0, 0, 1000, 3000)}]))
        self.assertIsNone(he.stacked_offset([stair], [dict(stair, points_mm=square(0, 0, 3000, 3000))]))
        lift_up = {"label": "Lift", "category": "lift_shaft", "points_mm": square(5000, 0, 1500, 1500)}
        lift_down = dict(lift_up, points_mm=square(9000, 4000, 1500, 1500))
        self.assertIsNone(he.stacked_offset([stair, lift_up], [stair, lift_down]))


class FloorTests(unittest.TestCase):
    def test_a_floor_is_split_by_what_lies_under_it(self):
        lower = [dict(GARAGE, points_mm=square(0, 0, 3000, 4000)), dict(LIVING, points_mm=square(3000, 0, 2000, 4000))]
        parts = he.floor_split(square(0, 0, 6000, 4000), lower)
        self.assertEqual([(row["below"], round(row["fraction"], 3), row["rooms"]) for row in parts],
                         [("not_cooled", 0.5, ["Garage"]), ("cooled", 0.333, ["Living"]), ("open", 0.167, [])])

    def test_slivers_under_half_a_square_metre_are_left_out(self):
        lower = [dict(LIVING, points_mm=square(0, 0, 6000, 3990))]
        parts = he.floor_split(square(0, 0, 6000, 4000), lower)
        self.assertEqual([(row["below"], row["fraction"]) for row in parts], [("cooled", 1.0)])


if __name__ == "__main__":
    unittest.main()
