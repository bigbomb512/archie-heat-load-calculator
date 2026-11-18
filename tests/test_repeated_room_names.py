#!/usr/bin/env python3
"""One name printed on separate rooms (Turramurra's three first-floor ENSUITEs, 2026-11-18) gives separate rooms."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from shapely.geometry import box

from ai import room_outline, room_use_resolution
from backend import autonomous_tasks_service, phase1_supervisor_service, reviewer_room_geometry_service, room_proposal


MM_PER_PX = 10.0       # 100 px = 1 m


def _square(x, y, size):
    return [[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]


class OccurrenceIdentityTests(unittest.TestCase):
    def test_single_name_keeps_its_identity_and_occurrences_are_distinct(self):
        self.assertEqual(room_use_resolution.room_identity("Ensuite", "First Floor"), "room-use:first-floor:ensuite")
        labels = [room_use_resolution.occurrence_label("Ensuite", n) for n in (1, 2, 3)]
        self.assertEqual(labels, ["Ensuite (1)", "Ensuite (2)", "Ensuite (3)"])
        self.assertEqual({room_use_resolution.room_identity(label, "First Floor") for label in labels},
                         {"room-use:first-floor:ensuite-1", "room-use:first-floor:ensuite-2", "room-use:first-floor:ensuite-3"})
        self.assertEqual(room_use_resolution.base_label("Ensuite (2)"), "Ensuite")
        self.assertEqual(room_use_resolution.base_label("Bedroom 2"), "Bedroom 2")
        # An occurrence label still classifies by the drawing's name.
        self.assertEqual(room_use_resolution._house_label_matches("Ensuite (3)"), ["house_wet"])

    def test_same_name_on_different_levels_is_two_identities(self):
        self.assertNotEqual(room_use_resolution.room_identity("Storage", "Basement"),
                            room_use_resolution.room_identity("Storage", "First Floor"))


class NumberRepeatedRoomsTests(unittest.TestCase):
    def test_separate_same_named_outlines_become_numbered_rooms_in_reading_order(self):
        rows = [("Ensuite", box(900, 0, 1000, 150)),        # top right
                ("Ensuite", box(0, 0, 100, 150)),           # top left
                ("Ensuite", box(0, 400, 200, 600)),         # below
                ("Bathroom", box(300, 0, 500, 200)),
                ("Bedroom", box(0, 800, 300, 1100)), ("Bedroom", box(400, 800, 700, 1100))]
        renamed, numbered = autonomous_tasks_service.number_repeated_rooms(rows, MM_PER_PX)
        self.assertEqual(renamed, {1: "Ensuite (1)", 0: "Ensuite (2)", 2: "Ensuite (3)"})
        self.assertEqual(numbered, [("Ensuite (1)", "Ensuite"), ("Ensuite (2)", "Ensuite"), ("Ensuite (3)", "Ensuite")])
        # The same outlines in another order are numbered the same: stable across rebuilds.
        again, _ = autonomous_tasks_service.number_repeated_rooms(list(reversed(rows)), MM_PER_PX)
        self.assertEqual({rows[len(rows) - 1 - index][1].bounds: label for index, label in again.items()},
                         {rows[index][1].bounds: label for index, label in renamed.items()})

    def test_touching_pieces_and_slivers_stay_one_room(self):
        touching = [("Ensuite", box(0, 0, 100, 200)), ("Ensuite", box(100, 0, 200, 200))]
        self.assertEqual(autonomous_tasks_service.number_repeated_rooms(touching, MM_PER_PX), ({}, []))
        sliver = [("Ensuite", box(0, 0, 200, 200)), ("Ensuite", box(300, 0, 320, 20))]   # 0.04 m², 1 m away
        self.assertEqual(autonomous_tasks_service.number_repeated_rooms(sliver, MM_PER_PX), ({}, []))
        self.assertEqual(room_outline.separate_rooms([box(0, 0, 200, 200), box(300, 0, 320, 20)], MM_PER_PX), [[0, 1]])
        self.assertEqual(autonomous_tasks_service.number_repeated_rooms([("Ensuite", box(0, 0, 200, 200))], MM_PER_PX), ({}, []))

    def test_a_printed_number_is_skipped(self):
        rows = [("Ensuite", box(0, 0, 200, 200)), ("Ensuite", box(500, 0, 700, 200))]
        renamed, _ = autonomous_tasks_service.number_repeated_rooms(rows, MM_PER_PX, printed_labels=["Ensuite 1"])
        self.assertEqual(renamed, {0: "Ensuite (2)", 1: "Ensuite (3)"})

    def test_outline_step_numbers_house_rooms_only(self):
        record = {"task": "P0_room_outlines", "target": "page-9", "run_id": "run-1", "packet": {"page": 9}}
        polygons = [{"label": "Ensuite", "polygon": box(0, 0, 200, 200)}, {"label": "Ensuite", "polygon": box(500, 0, 700, 200)}]
        with TemporaryDirectory() as folder, \
             patch("backend.house_windows_service.is_dwelling", return_value=True), \
             patch.object(autonomous_tasks_service, "_page_level", return_value="First Floor"), \
             patch.object(reviewer_room_geometry_service, "_rooms", return_value=[]):
            autonomous_tasks_service._number_separate_repeated_rooms(Path(folder), record, polygons, MM_PER_PX)
            self.assertEqual([row["label"] for row in polygons], ["Ensuite (1)", "Ensuite (2)"])
            saved = room_proposal.use_named_rooms(Path(folder))
            self.assertEqual([(row["label"], row["derived_from_repeated_label"]) for row in saved],
                             [("Ensuite (1)", "Ensuite"), ("Ensuite (2)", "Ensuite")])
        shop = [{"label": "WC", "polygon": box(0, 0, 200, 200)}, {"label": "WC", "polygon": box(500, 0, 700, 200)}]
        with TemporaryDirectory() as folder, patch("backend.house_windows_service.is_dwelling", return_value=False):
            autonomous_tasks_service._number_separate_repeated_rooms(Path(folder), record, shop, MM_PER_PX)
        self.assertEqual([row["label"] for row in shop], ["WC", "WC"])


class RoomListTests(unittest.TestCase):
    def test_numbered_rooms_replace_the_merged_record_on_their_level_only(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            room_proposal.save_numbered_rooms(root, "P0_numbered_repeat:P0_room_outlines:page-9", 9, "First Floor",
                                              [("Ensuite (1)", "Ensuite"), ("Ensuite (2)", "Ensuite")], "run-1")
            run = {"local_room_inference_proposal": {"rooms": [
                {"kind": "room", "label": "Ensuite", "level_name": "First Floor"},
                {"kind": "room", "label": "Ensuite", "level_name": "Ground Floor"},
                {"kind": "room", "label": "Storage", "level_name": "Basement"},
                {"kind": "room", "label": "Storage", "level_name": "First Floor"}]}}
            proposal = room_proposal.room_proposal(run, root)
            labels = {(row["label"], row["level_name"]) for row in proposal["rooms"]}
            expected = {("Ensuite (1)", "First Floor"), ("Ensuite (2)", "First Floor"), ("Ensuite", "Ground Floor"),
                        ("Storage", "Basement"), ("Storage", "First Floor")}
            self.assertEqual(labels, expected)
            building = {"spaces": [{"name": "Ensuite", "level_name": "First Floor"},
                                   {"name": "Storage", "level_name": "Basement"},
                                   {"name": "Storage", "level_name": "First Floor"}]}
            rows = room_use_resolution._room_rows(building, {}, proposal)
            self.assertEqual({(row["label"], row["level_name"]) for row in rows}, expected)
            # Bedroom numbering (the original use of this mechanism) still replaces only bedrooms.
            self.assertEqual(room_proposal.numbered_bedroom_levels(root), set())

    def test_merged_rooms_use_override_carries_to_each_numbered_room(self):
        building = {"spaces": [{"name": "Ensuite", "level_name": "First Floor"}]}
        merged = room_use_resolution.resolve(building, dwelling_context={"is_dwelling": True, "evidence": []})
        merged = room_use_resolution.apply_override(merged, "room-use:first-floor:ensuite", "house_living", "Reviewer", "Cooled")
        proposal = {"rooms": [{"kind": "room", "label": "Ensuite (1)", "level_name": "First Floor",
                               "source": "numbered_from_repeated_label", "repeated_label": "Ensuite"},
                              {"kind": "room", "label": "Ensuite (2)", "level_name": "First Floor",
                               "source": "numbered_from_repeated_label", "repeated_label": "Ensuite"}]}
        result = room_use_resolution.resolve(building, proposal=proposal, existing=merged)
        by_id = {row["room_id"]: row for row in result["records"]}
        self.assertEqual(sorted(by_id), ["room-use:first-floor:ensuite-1", "room-use:first-floor:ensuite-2"])
        self.assertTrue(all(row["taxonomy_id"] == "house_living" and row["override"] for row in by_id.values()))


class SavedJobMigrationTests(unittest.TestCase):
    def _trace(self, trace_id, room_id, label, points, part, count, source="ai_determined"):
        return {"trace_id": trace_id, "room_id": room_id, "room_label": label, "level_name": "First Floor", "page": 9,
                "points_image_px": points, "snapped_line_ids": [None] * len(points),
                "calibration": {"mm_per_px": MM_PER_PX, "status": "standard_objects", "source": "standard_objects"},
                "reviewer": "Archie AI (P0)", "note": "", "status": "geometry_proposed", "created_at": "2026-11-18T00:00:00",
                "source_fingerprints": {}, "declaration_source": source, "ai_run_id": "run-1",
                "part_index": part, "part_count": count, "method": "ai_outline_snapped", "evidence": [],
                "edges": [{"index": 0, "boundary": "external"}], "roof": "exposed",
                "envelope_reviewer": "Archie drawing text (windows)", "envelope_declared_at": "2026-11-18T00:00:00"}

    def _job(self, root, dwelling=True):
        (root / "room_use_resolution.json").write_text(json.dumps({
            "dwelling_context": {"is_dwelling": dwelling, "evidence": []},
            "records": [{"room_id": "room-use:first-floor:ensuite", "original_label": "Ensuite", "level_name": "First Floor"},
                        {"room_id": "room-use:first-floor:corridor", "original_label": "Corridor", "level_name": "First Floor"}]}))
        records = [self._trace("t1", "room-use:first-floor:ensuite", "Ensuite", _square(0, 0, 200), 1, 3),
                   self._trace("t2", "room-use:first-floor:ensuite", "Ensuite", _square(1000, 0, 300), 2, 3),
                   self._trace("t3", "room-use:first-floor:ensuite", "Ensuite", _square(0, 500, 200), 3, 3),
                   self._trace("t4", "room-use:first-floor:corridor", "Corridor", _square(400, 400, 300), 1, 1)]
        (root / "reviewer_room_geometry.json").write_text(json.dumps({"schema_version": 1, "records": records}))

    def test_saved_parts_of_separate_rooms_are_split_keeping_trace_ids(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._job(root)
            split = reviewer_room_geometry_service.split_repeated_room_traces(root)
            self.assertEqual(split, {"room-use:first-floor:ensuite": [
                "room-use:first-floor:ensuite-1", "room-use:first-floor:ensuite-2", "room-use:first-floor:ensuite-3"]})
            records = {row["trace_id"]: row for row in json.loads((root / "reviewer_room_geometry.json").read_text())["records"]}
            self.assertEqual({key: (row["room_id"], row["room_label"], row["part_index"], row["part_count"])
                              for key, row in records.items()}, {
                "t1": ("room-use:first-floor:ensuite-1", "Ensuite (1)", 1, 1),
                "t2": ("room-use:first-floor:ensuite-2", "Ensuite (2)", 1, 1),
                "t3": ("room-use:first-floor:ensuite-3", "Ensuite (3)", 1, 1),
                "t4": ("room-use:first-floor:corridor", "Corridor", 1, 1)})      # a single-name room is unchanged
            self.assertEqual(records["t2"]["roof"], "exposed")                   # answers saved on an outline stay
            rooms = {row["room_id"] for row in reviewer_room_geometry_service._rooms(
                reviewer_room_geometry_service._paths({"review_dir": folder}))}
            self.assertTrue({"room-use:first-floor:ensuite-1", "room-use:first-floor:ensuite-2",
                             "room-use:first-floor:ensuite-3", "room-use:first-floor:corridor"} <= rooms)
            self.assertNotIn("room-use:first-floor:ensuite", rooms)
            before = (root / "reviewer_room_geometry.json").read_text()
            self.assertEqual(reviewer_room_geometry_service.split_repeated_room_traces(root), {})   # idempotent
            self.assertEqual((root / "reviewer_room_geometry.json").read_text(), before)

    def test_reviewer_traces_and_shops_are_not_regrouped(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._job(root, dwelling=False)
            self.assertEqual(reviewer_room_geometry_service.split_repeated_room_traces(root), {})
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self._job(root)
            raw = json.loads((root / "reviewer_room_geometry.json").read_text())
            for row in raw["records"]:
                row["declaration_source"] = "reviewer"
            (root / "reviewer_room_geometry.json").write_text(json.dumps(raw))
            self.assertEqual(reviewer_room_geometry_service.split_repeated_room_traces(root), {})


class SupervisorNamesTests(unittest.TestCase):
    def test_gap_list_names_rooms_of_one_label_on_two_levels_apart(self):
        records = [{"room_id": "room-use:basement:storage", "original_label": "Storage", "level_name": "Basement"},
                   {"room_id": "room-use:first-floor:storage", "original_label": "Storage", "level_name": "First Floor"},
                   {"room_id": "room-use:first-floor:ensuite-1", "original_label": "Ensuite (1)", "level_name": "First Floor"},
                   {"room_id": "room-use:first-floor:corridor", "original_label": "Corridor", "level_name": "First Floor"}]
        names = [phase1_supervisor_service.room_display_name(row, records) for row in records]
        self.assertEqual(names, ["Storage (Basement)", "Storage (First Floor)", "Ensuite (1)", "Corridor"])
        trace = {"page": 25, "roof": "unknown", "edges": []}
        snapshot = {"rooms": [
            {"id": "room-use:basement:storage", "name": "Storage", "display_name": names[0], "label_unique": False,
             "scope": "comfort_hvac", "traces": [trace], "area_m2": 33.2, "pages": [25]},
            {"id": "room-use:first-floor:storage", "name": "Storage", "display_name": names[1], "label_unique": False,
             "scope": "comfort_hvac", "traces": [], "area_m2": 0, "pages": [9]}]}
        gaps = phase1_supervisor_service.audit(snapshot)
        self.assertEqual(sorted(gap["message"] for gap in gaps if gap["kind"] in {"roof", "room_area"}), [
            "Check Storage (First Floor)'s outline and area; its area is missing or only a last-resort estimate.",
            "Check what is above Storage (Basement); its roof or ceiling exposure is unknown or assumed."])


if __name__ == "__main__":
    unittest.main()
