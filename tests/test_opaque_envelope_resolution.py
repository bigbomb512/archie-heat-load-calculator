import unittest

from ai.thermal_surface_resolution import resolve_opaque_envelope


class OpaqueEnvelopeResolutionTests(unittest.TestCase):
    def _ledger(self, coverage="confirmed"):
        return {"source_fingerprint": "pdf-1", "surfaces": [{
            "surface_id": "wall-1", "physical_type": "wall", "thermal_role": "external",
            "boundary_condition": "outside", "owner_room_id": "room-1", "owner_zone_id": "zone-1",
            "gross_area_m2": 30, "opening_coverage_status": coverage, "opening_ids": ["opening-1"],
            "evidence_refs": [{"page": 4}], "confidence": "high",
        }]}

    def test_confirmed_openings_subtract_once_and_use_controlled_assembly(self):
        result = resolve_opaque_envelope(
            self._ledger(), {"openings": [{"opening_id": "opening-1", "opening_area_m2": 8}]},
            source_pack={"preliminary_envelope": {"opaque_constructions": {"wall": {"construction_id": "wall-pack", "u_value_w_m2k": 0.6}}}},
            resolution_mode="preliminary_ai_estimate", weather_available=True,
        )
        row = result["surfaces"][0]
        self.assertTrue(row["thermal_eligible"])
        self.assertEqual(row["net_opaque_area_m2"], 22)
        self.assertEqual(row["construction_id"], "wall-pack")

    def test_preliminary_incomplete_coverage_uses_known_openings_and_marks_assumption(self):
        result = resolve_opaque_envelope(
            self._ledger("incomplete"), {"openings": [{"opening_id": "opening-1", "opening_area_m2": 8}]},
            source_pack={"preliminary_envelope": {"opaque_constructions": {"wall": {"construction_id": "wall-pack", "u_value_w_m2k": 0.6}}}},
            resolution_mode="preliminary_ai_estimate", weather_available=True,
        )
        row = result["surfaces"][0]
        self.assertTrue(row["thermal_eligible"])
        self.assertEqual(row["net_opaque_area_m2"], 22)
        self.assertEqual(row["opening_coverage_status"], "incomplete")
        self.assertTrue(any("Other openings may be uncounted" in value for value in row["assumptions"]))

    def test_reviewed_incomplete_coverage_still_blocks(self):
        result = resolve_opaque_envelope(
            self._ledger("incomplete"), {"openings": [{"opening_id": "opening-1", "opening_area_m2": 8}]},
            source_pack={"preliminary_envelope": {"opaque_constructions": {"wall": {"construction_id": "wall-pack", "u_value_w_m2k": 0.6}}}},
            resolution_mode="engineering_reviewed", weather_available=True,
        )
        self.assertFalse(result["surfaces"][0]["thermal_eligible"])
        self.assertIn("opening_coverage_incomplete", result["surfaces"][0]["unresolved_fields"])

    def test_reviewer_entered_openings_subtract_without_claiming_complete_coverage(self):
        result = resolve_opaque_envelope(
            self._ledger("reviewer_entered"), {"openings": [{"opening_id": "opening-1", "opening_area_m2": 8}]},
            source_pack={"preliminary_envelope": {"opaque_constructions": {"wall": {"construction_id": "wall-pack", "u_value_w_m2k": 0.6}}}},
            resolution_mode="preliminary_ai_estimate", weather_available=True,
        )
        row = result["surfaces"][0]
        self.assertTrue(row["thermal_eligible"])
        self.assertEqual(row["net_opaque_area_m2"], 22)
        self.assertEqual(row["opening_coverage_status"], "reviewer_entered")

    def test_non_external_boundary_without_temperature_uses_the_labelled_estimate_only_in_a_preliminary_model(self):
        pack = {"preliminary_envelope": {"opaque_constructions": {"wall": {"construction_id": "wall-pack", "u_value_w_m2k": 0.6}}}}
        ledger = self._ledger("not_applicable")
        ledger["surfaces"][0].update({"thermal_role": "fixed_adjacent", "boundary_condition": "corridor", "opening_ids": []})
        result = resolve_opaque_envelope(ledger, source_pack=pack, resolution_mode="preliminary_ai_estimate", weather_available=True)
        surface = result["surfaces"][0]
        self.assertTrue(surface["thermal_eligible"])                     # Phase 1 counts it rather than dropping it
        self.assertIn("unconditioned space at outdoor temperature minus 3 K (buffer-space estimate)", surface["assumptions"])
        ledger = self._ledger("not_applicable")
        ledger["surfaces"][0].update({"thermal_role": "fixed_adjacent", "boundary_condition": "corridor", "opening_ids": [],
                                      "construction_id": "wall-pack", "u_value_w_m2k": 0.6})
        reviewed = resolve_opaque_envelope(ledger, source_pack=pack, resolution_mode="engineering_reviewed", weather_available=True)
        self.assertFalse(reviewed["surfaces"][0]["thermal_eligible"])
        self.assertIn("boundary_temperature_missing", reviewed["surfaces"][0]["unresolved_fields"])

    def test_preliminary_unresolved_floor_uses_a_labelled_boundary_estimate_not_zero(self):
        ledger = self._ledger("not_applicable")
        ledger["surfaces"][0].update({
            "surface_id": "floor-1", "physical_type": "floor", "thermal_role": "unresolved",
            "boundary_condition": "unresolved", "opening_ids": [], "gross_area_m2": 20,
        })
        result = resolve_opaque_envelope(
            ledger, source_pack={"preliminary_envelope": {"opaque_constructions": {
                "floor": {"construction_id": "floor-pack", "u_value_w_m2k": 0.8}}}},
            resolution_mode="preliminary_ai_estimate", weather_available=False,
        )
        row = result["surfaces"][0]
        self.assertTrue(row["thermal_eligible"])
        self.assertEqual(row["boundary_method"], "fixed_adjacent_temperature")
        self.assertTrue(any("labelled adjacent-space temperature estimate" in value and "not a zero-load" in value
                            for value in row["assumptions"]))


if __name__ == "__main__":
    unittest.main()
