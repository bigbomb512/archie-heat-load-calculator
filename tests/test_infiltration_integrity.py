#!/usr/bin/env python3
"""Infiltration input-contract integrity checks.

Synthetic fixtures only. No Drawing 6 artifact, private PDF or project file is
read or written, and no engineer approval is manufactured: every test that
needs an approved gate instead asserts that the gate still blocks.
"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.hourly_loads import (
    active_infiltration_components, hour_total, infiltration_component,
    infiltration_schedule_missing, resolved_profiles, room_contributions,
    room_static_missing, validate_room_component, validate_room_components,
)
from ai.infiltration_gate import METHOD_ID, empty_infiltration_method_gate

from tests.test_infiltration_method_preparation import (
    POLICY_CITATION, SYNTHETIC, component, fixture, schedule_library, scenario,
)


GATE = empty_infiltration_method_gate()
DUPLICATE = "single declared infiltration air path"
HEIGHT = "reviewed room or zone ceiling height for ACH infiltration"
AREA = "positive room area for ACH infiltration"
DEDICATED = "dedicated infiltration schedule separate from the outside-air schedule"


def reasons(room, zone=None, gate=GATE):
    return room_static_missing(room, zone or {}, gate)


def has(room, needle, zone=None, gate=GATE):
    return any(needle in reason for reason in reasons(room, zone, gate))


class DuplicateAirPaths(unittest.TestCase):
    def test_duplicate_active_infiltration_with_different_ids_blocks(self):
        _, _, room = fixture()
        second = {**component(), "component_id": "another-infiltration"}
        room["unapproved_components"] = validate_room_components(
            [component(), second], room["room_id"])
        self.assertEqual(len(active_infiltration_components(room)), 2)
        blocker = [r for r in reasons(room) if DUPLICATE in r]
        self.assertEqual(len(blocker), 1)
        self.assertIn("another-infiltration", blocker[0])
        self.assertIn("never summed", blocker[0])

    def test_stored_duplicate_of_a_calculated_component_also_blocks(self):
        _, _, room = fixture()
        stored = {**component(), "component_id": "stored-infiltration",
                  "calculation_status": "stored_not_calculated", "citations": []}
        room["unapproved_components"] = validate_room_components(
            [component(), stored], room["room_id"])
        self.assertTrue(has(room, DUPLICATE))

    def test_single_infiltration_component_is_not_flagged(self):
        _, _, room = fixture()
        self.assertFalse(has(room, DUPLICATE))

    def test_not_present_and_unassessed_duplicates_are_not_active(self):
        _, _, room = fixture()
        absent = {**component(), "component_id": "absent-infiltration", "value": None,
                  "unit": "", "calculation_status": "not_present_confirmed",
                  "verification_status": "confirmed", "citations": []}
        room["unapproved_components"] = validate_room_components(
            [component(), absent], room["room_id"])
        self.assertEqual(len(active_infiltration_components(room)), 1)
        self.assertFalse(has(room, DUPLICATE))

    def test_duplicates_are_never_silently_summed_into_one_hour(self):
        _, _, room = fixture()
        second = {**component(), "component_id": "another-infiltration"}
        room["unapproved_components"] = validate_room_components(
            [component(), second], room["room_id"])
        self.assertTrue(has(room, DUPLICATE))
        rows = room_contributions(room, {}, GATE,
                                  {"outside_air": [1] * 24, "infiltration": [1] * 24},
                                  0, scenario()["hours"][0], 101.325)
        self.assertEqual(sum(row["name"] == "infiltration" for row in rows), 1)


class AchConversionInputs(unittest.TestCase):
    def test_ach_with_missing_area_blocks(self):
        _, _, room = fixture()
        room["area_m2"] = None
        self.assertTrue(has(room, AREA))

    def test_ach_with_zero_area_blocks(self):
        _, _, room = fixture()
        room["area_m2"] = 0
        self.assertTrue(has(room, AREA))

    def test_ach_with_missing_height_blocks(self):
        _, _, room = fixture()
        room["ceiling_height_mm"] = None
        self.assertTrue(has(room, HEIGHT))

    def test_ach_with_zero_height_blocks(self):
        _, _, room = fixture()
        room["ceiling_height_mm"] = 0
        self.assertTrue(has(room, HEIGHT, zone={"ceiling_height_mm": 0}))

    def test_ach_with_valid_room_height_clears_geometry_blockers(self):
        _, _, room = fixture()
        self.assertFalse(has(room, HEIGHT))
        self.assertFalse(has(room, AREA))

    def test_ach_with_valid_zone_height_fallback_clears_geometry_blockers(self):
        _, _, room = fixture()
        room["ceiling_height_mm"] = None
        self.assertFalse(has(room, HEIGHT, zone={"ceiling_height_mm": 3150}))

    def test_geometry_blockers_do_not_replace_the_gate_blocker(self):
        _, _, room = fixture()
        room["area_m2"] = 0
        room["ceiling_height_mm"] = None
        self.assertIn("approved infiltration method gate", reasons(room))


class DirectAirflowInputs(unittest.TestCase):
    def test_direct_units_need_no_room_volume(self):
        for unit, value in (("L/s", 6), ("m3/s", 0.006), ("m3/h", 21.6)):
            with self.subTest(unit=unit):
                _, _, room = fixture()
                room["unapproved_components"][0].update(unit=unit, value=value)
                room["ceiling_height_mm"] = None
                self.assertFalse(has(room, HEIGHT))
                self.assertFalse(has(room, AREA))

    def test_direct_airflow_still_requires_the_gate(self):
        _, _, room = fixture()
        room["unapproved_components"][0].update(unit="L/s", value=6)
        room["ceiling_height_mm"] = None
        self.assertIn("approved infiltration method gate", reasons(room))


class DedicatedSchedules(unittest.TestCase):
    def test_shared_dedicated_infiltration_profile_between_rooms_is_valid(self):
        _, _, first = fixture()
        _, _, second = fixture()
        for room in (first, second):
            room["schedule_assignments"].update(
                outside_air="test-outside", infiltration="test-infiltration")
            self.assertEqual(infiltration_schedule_missing(room), [])
            self.assertFalse(has(room, DEDICATED))
        self.assertEqual(first["schedule_assignments"]["infiltration"],
                         second["schedule_assignments"]["infiltration"])

    def test_outside_air_schedule_reused_as_infiltration_schedule_is_rejected(self):
        _, _, room = fixture()
        room["schedule_assignments"]["infiltration"] = "test-outside"
        blocker = infiltration_schedule_missing(room)
        self.assertEqual(len(blocker), 1)
        self.assertIn("test-outside", blocker[0])
        self.assertTrue(has(room, DEDICATED))

    def test_untimed_infiltration_raises_no_schedule_blocker(self):
        _, _, room = fixture()
        room["unapproved_components"][0].update(
            value=None, unit="", calculation_status="not_assessed",
            verification_status="missing", source="", citations=[],
            method_id="", air_path="", flow_reference="")
        room["schedule_assignments"]["infiltration"] = "test-outside"
        self.assertEqual(infiltration_schedule_missing(room), [])

    def test_missing_infiltration_schedule_is_still_a_profile_blocker(self):
        _, _, room = fixture()
        room["schedule_assignments"]["infiltration"] = ""
        _, missing, _ = resolved_profiles(schedule_library(), "weekday", room)
        self.assertIn("infiltration schedule assignment", missing)


class GateAndDeclaration(unittest.TestCase):
    def test_missing_gate_blocks_a_calculated_component(self):
        _, _, room = fixture()
        self.assertIn("approved infiltration method gate", reasons(room, gate=None))
        self.assertIn("approved infiltration method gate", reasons(room))

    def test_missing_citation_or_source_is_rejected_at_validation(self):
        for field, value in (("citations", []), ("source", "")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_room_component({**component(), field: value}, "test-room", 1)

    def test_wrong_air_path_or_method_or_flow_reference_is_rejected(self):
        for field, value in (("air_path", "outside_air"), ("air_path", ""),
                             ("method_id", "other_method"),
                             ("flow_reference", "indoor_condition")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    validate_room_component({**component(), field: value}, "test-room", 1)

    def test_approved_declaration_shape_survives_validation(self):
        row = validate_room_component(component(), "test-room", 1)
        self.assertEqual(row["method_id"], METHOD_ID)
        self.assertEqual(row["air_path"], "uncontrolled_infiltration")
        self.assertEqual(row["flow_reference"], "outdoor_design_condition")
        self.assertEqual(row["citations"], [POLICY_CITATION])
        self.assertEqual(row["source"], SYNTHETIC)


class SeparationFromOutsideAir(unittest.TestCase):
    def test_no_double_counting_between_infiltration_and_outside_air(self):
        _, _, room = fixture()
        before = deepcopy(room)
        profiles = {"outside_air": [1] * 24, "infiltration": [1] * 24}
        rows = room_contributions(room, {}, GATE, profiles, 0, scenario()["hours"][0], 101.325)
        self.assertEqual(sum(row["name"] == "outside_air" for row in rows), 1)
        self.assertEqual(sum(row["name"] == "infiltration" for row in rows), 1)
        total = hour_total(0, rows, 1.1)
        self.assertEqual(total["components"]["outside_air"]["inputs"]["flow_lps"], 100)
        self.assertEqual(total["components"]["infiltration"]["inputs"]["applied_flow_lps"], 6)
        self.assertEqual(room, before)

    def test_missing_infiltration_never_becomes_a_zero_flow_result(self):
        _, _, room = fixture()
        room["unapproved_components"][0].update(
            value=None, unit="", calculation_status="not_assessed",
            verification_status="missing", source="", citations=[],
            method_id="", air_path="", flow_reference="")
        self.assertIn("infiltration assessment", reasons(room))
        rows = room_contributions(room, {}, GATE, {"outside_air": [1] * 24},
                                  0, scenario()["hours"][0], 101.325)
        self.assertFalse(any(row["name"] == "infiltration" for row in rows))
        self.assertEqual(infiltration_component(room)["value"], None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
