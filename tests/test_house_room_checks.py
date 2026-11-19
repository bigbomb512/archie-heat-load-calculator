#!/usr/bin/env python3
"""House job 1795082136 (2026-11-19) room checks: an ENSUITE traced on top of ENSUITE (2) and cooled, and a Family
outline that came out a triangle because the corner the AI said had no number was cut straight across."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shapely.geometry import Polygon, box

from ai import autonomous_tasks, room_outline, room_use_resolution
from backend import autonomous_tasks_service, reviewer_room_geometry_service, room_proposal


def _ring(shape):
    return [list(point) for point in list(shape.exterior.coords)[:-1]]


class ReplacedPlainNameTests(unittest.TestCase):
    def test_outline_steps_plain_name_is_replaced_by_its_numbered_rooms(self):
        """The outline step saves the printed name it drew (named_on_plan) and the numbered rooms; only the numbered
        ones are rooms. The plain ENSUITE came back as a fourth room with no room-use record, so it was cooled."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            room_proposal.save_use_named_rooms(root, "P0_room_outlines", 7, "First", ["ENSUITE", "STUDY"], "run-1",
                                               named_on_plan=True)
            room_proposal.save_use_named_rooms(root, "P0_room_outlines", 6, "Ground", ["ENSUITE"], "run-1",
                                               named_on_plan=True)
            room_proposal.save_numbered_rooms(root, "P0_numbered_repeat:P0_room_outlines:page-7", 7, "First",
                                              [("ENSUITE (1)", "ENSUITE"), ("ENSUITE (2)", "ENSUITE")], "run-1")
            rooms = {(row["label"], row["level_name"]) for row in room_proposal.room_proposal({}, root)["rooms"]}
        self.assertEqual(rooms, {("ENSUITE (1)", "First"), ("ENSUITE (2)", "First"), ("STUDY", "First"),
                                 ("ENSUITE", "Ground")})      # another level's single ENSUITE is its own room

    def test_numbered_bedrooms_replace_the_plain_name_the_same_way(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            room_proposal.save_use_named_rooms(root, "P0_room_outlines", 7, "First", ["BEDROOM"], "run-1", named_on_plan=True)
            room_proposal.save_use_named_rooms(root, "P0_numbered:P0_room_outlines:page-7", 7, "First",
                                               ["Bedroom 1", "Bedroom 2"], "run-1", derived_from_repeated_label=True)
            labels = sorted(row["label"] for row in room_proposal.room_proposal({}, root)["rooms"])
        self.assertEqual(labels, ["Bedroom 1", "Bedroom 2"])

    def test_current_plain_trace_does_not_restore_a_replaced_room(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            room_proposal.save_numbered_rooms(root, "P0_numbered_repeat:P0_room_outlines:page-7", 7, "First",
                                              [("ENSUITE (1)", "ENSUITE"), ("ENSUITE (2)", "ENSUITE")], "run-1")
            traces = [{"room_label": label, "level_name": "First", "page": 7,
                       "room_id": room_use_resolution.room_identity(label, "First")}
                      for label in ("ENSUITE", "ENSUITE (1)", "ENSUITE (2)")]
            with patch.object(room_proposal, "traced_rooms", return_value=traces):
                labels = {row["label"] for row in room_proposal.room_proposal({}, root)["rooms"]}
        self.assertEqual(labels, {"ENSUITE (1)", "ENSUITE (2)"})

    def test_upper_case_numbered_ensuites_are_not_cooled_in_a_house(self):
        context = {"is_dwelling": True, "evidence": []}
        proposal = {"rooms": [{"kind": "room", "label": f"ENSUITE ({n})", "level_name": "First",
                               "source": "numbered_from_repeated_label", "repeated_label": "ENSUITE"} for n in (1, 2)]}
        result = room_use_resolution.resolve({"spaces": []}, proposal=proposal, dwelling_context=context)
        self.assertEqual({row["original_label"]: row["space_scope"] for row in result["records"]},
                         {"ENSUITE (1)": "not_cooled", "ENSUITE (2)": "not_cooled"})


class CornerQuestionPerNumberedRoomTests(unittest.TestCase):
    # Page 7's three ENSUITE sketches and the numbered traces the outline step saved for them (image pixels).
    E1, E2, E3 = box(3252, 1484, 3387, 1627), box(2868, 1956, 3093, 2123), box(3252, 2411, 3387, 2557)
    SKETCHES = [_ring(box(3259, 1485, 3384, 1637)), _ring(box(2865, 1956, 3093, 2122)), _ring(box(3253, 2408, 3384, 2557))]

    def _traces(self):
        rows = [("ENSUITE (1)", self.E1), ("ENSUITE (2)", self.E2), ("ENSUITE (3)", self.E3),
                ("Bedroom 1", box(3387, 1588, 3657, 1862)), ("Bedroom 2", box(2864, 2257, 3252, 2557)),
                ("Study", box(3387, 2414, 3654, 2545))]
        return [{"room_label": label, "page": 7, "points_image_px": _ring(shape) + [_ring(shape)[0]]} for label, shape in rows]

    def _subjects(self, spaces):
        record = {"status": "below_accuracy_bar", "validation": {"result": spaces}}
        def current(root, task, target):
            return record if (task, target) == ("P0_room_outlines", "page-7") else None
        with patch.object(autonomous_tasks_service, "_current_task", side_effect=current), \
             patch.object(autonomous_tasks_service, "rooms_not_on_plan", return_value={}), \
             patch.object(reviewer_room_geometry_service, "current_records", return_value=self._traces()):
            return autonomous_tasks_service._corner_subjects(Path("/nonexistent"), 7)

    def test_each_numbered_room_gets_its_own_corner_question(self):
        spaces = [{"room": "ENSUITE", "points": points} for points in self.SKETCHES]
        spaces += [{"room": "STUDY", "points": _ring(box(3392, 2408, 3661, 2557))}]
        subjects = self._subjects(spaces)
        self.assertEqual([(target, room, len(sketches)) for target, room, _task, sketches in subjects],
                         [("room-ensuite-1-page-7", "ENSUITE (1)", 1), ("room-ensuite-2-page-7", "ENSUITE (2)", 1),
                          ("room-ensuite-3-page-7", "ENSUITE (3)", 1), ("room-study-page-7", "STUDY", 1)])
        self.assertEqual(subjects[1][3], [self.SKETCHES[1]])     # ENSUITE (2) is asked about its own sketch

    def test_plain_bedroom_sketches_go_to_the_numbered_bedrooms(self):
        spaces = [{"room": "BEDROOM", "points": _ring(box(3390, 1590, 3650, 1860))},
                  {"room": "BEDROOM", "points": _ring(box(2870, 2260, 3250, 2550))}]
        self.assertEqual([room for _target, room, _task, _sketches in self._subjects(spaces)], ["Bedroom 1", "Bedroom 2"])

    def test_a_sketch_over_none_of_the_numbered_rooms_is_not_asked_about(self):
        stray = _ring(box(100, 100, 300, 300))
        groups = autonomous_tasks_service._sketches_by_numbered_room(
            "Ensuite", [self.SKETCHES[0], stray], {"ensuite": [("ENSUITE (1)", self.E1)]})
        self.assertEqual(groups, [("ENSUITE (1)", [self.SKETCHES[0]])])

    def test_a_name_with_no_numbered_rooms_is_asked_about_as_before(self):
        groups = autonomous_tasks_service._sketches_by_numbered_room("Kitchen", self.SKETCHES, {"ensuite": []})
        self.assertEqual(groups, [("Kitchen", self.SKETCHES)])


class MissingCornerTests(unittest.TestCase):
    # Ground-floor Family as recorded: the AI picked corners 120, 153, 144, 143, 152 and said the top side and the east
    # side have no numbered corner, so the outline cut from the north-west corner to the south-east one.
    PICKED = [(3645.9, 2344.13), (4161.11, 2621.22), (4021.5, 2588.63), (3785.51, 2588.63), (3645.9, 2621.22)]
    SKETCH = [[3666.43, 2320.72], [3666.43, 2608.24], [4155.21, 2608.24], [4155.21, 2320.72]]

    def _packet(self, sketch=None):
        return {"room": "Family", "corners": [list(point) for point in self.PICKED], "sketch": sketch or self.SKETCH}

    def test_the_missing_corner_is_put_back_so_the_room_is_not_a_triangle(self):
        reply = json.dumps({"corners": [1, 2, 3, 4, 5], "missing_sides": "The top side has no numbered corner."})
        value = autonomous_tasks.validate_p0_outline_corners(self._packet(), reply)
        self.assertEqual(value["completed_corners"], [[4161.11, 2344.13]])
        built = Polygon(value["parts"][0])
        self.assertAlmostEqual(built.area / Polygon(self.SKETCH).area, 1.0, delta=0.1)
        # The fireplace notch along the south wall (slightly slanted sides) is kept as picked.
        self.assertIn([4021.5, 2588.63], [list(point) for point in value["parts"][0]])
        self.assertEqual(len(value["parts"][0]), 6)

    def test_nothing_is_added_when_the_reply_reports_no_missing_side(self):
        reply = json.dumps({"corners": [1, 2, 3, 4, 5], "missing_sides": ""})
        value = autonomous_tasks.validate_p0_outline_corners(self._packet(), reply)
        self.assertNotIn("completed_corners", value)
        self.assertEqual(len(value["parts"][0]), 5)

    def test_a_slanted_wall_the_sketch_also_follows_is_kept(self):
        # A room with a real 45-degree wall: the sketch cuts the same corner off, so no corner is put back.
        picked = [(0, 0), (400, 0), (400, 200), (200, 400), (0, 400)]
        sketch = [[5, 5], [395, 5], [395, 205], [205, 395], [5, 395]]
        points, added = room_outline.complete_missing_corners(picked, sketch)
        self.assertEqual(added, [])
        self.assertEqual(points, [list(point) for point in picked])

    def test_the_tenancy_is_never_completed_from_its_sketch(self):
        packet = {"room": None, "corners": [list(point) for point in self.PICKED], "sketch": self.SKETCH}
        reply = json.dumps({"corners": [1, 2, 3, 4, 5], "missing_sides": "The top side has no numbered corner."})
        self.assertNotIn("completed_corners", autonomous_tasks.validate_p0_outline_corners(packet, reply))


if __name__ == "__main__":
    unittest.main()
