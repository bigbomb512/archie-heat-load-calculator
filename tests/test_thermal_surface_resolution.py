import unittest

from ai.geometry_review import normalise_vision
from ai.thermal_surface_resolution import resolve_thermal_surfaces


class ThermalSurfaceResolutionTests(unittest.TestCase):
    def _vision(self, rows, page_role="main_geometry_and_dimension_plan"):
        return normalise_vision({
            "geometry_review": {
                "pages": [{
                    "page": 5,
                    "page_role": page_role,
                    "thermal_surface_candidates": rows,
                }]
            }
        })

    def test_preliminary_external_surface_is_classified_but_requires_properties(self):
        vision = self._vision([{
            "surface_id": "P5-VSURFACE-001",
            "physical_type": "wall",
            "thermal_role": "external",
            "boundary_condition": "outside",
            "owner_room_id": "room-1",
            "boundary_points_px": [[0, 0], [100, 0], [100, 50], [0, 50], [0, 0]],
            "evidence_refs": [{"page": 6, "reference": "south elevation"}],
            "confidence": "high",
            "construction_id": "wall-1",
            "u_value_w_m2k": 0.5,
        }])
        ledger = resolve_thermal_surfaces(
            vision, [{"page": 5, "proposed_role": "main_geometry_and_dimension_plan"}], "source-1",
            resolution_mode="preliminary_ai_estimate", room_ids=["room-1"],
        )
        row = ledger["surfaces"][0]
        self.assertEqual(row["status"], "ai_estimated")
        self.assertTrue(row["thermal_eligible"])

    def test_partition_requires_adjacent_space_and_review_witness(self):
        vision = self._vision([{
            "surface_id": "P5-VSURFACE-002",
            "physical_type": "partition",
            "thermal_role": "room_to_room",
            "boundary_condition": "conditioned_space",
            "owner_room_id": "room-1",
            "boundary_points_px": [[0, 0], [10, 0], [10, 3], [0, 3], [0, 0]],
            "evidence_refs": [{"page": 5, "reference": "plan wall"}],
            "independent_witnesses": [{"page": 6, "reference": "section"}],
            "confidence": "high",
            "construction_id": "partition-1",
            "u_value_w_m2k": 1.2,
            "boundary_temperature_c": 22,
        }])
        ledger = resolve_thermal_surfaces(
            vision, [{"page": 5, "proposed_role": "main_geometry_and_dimension_plan"}], "source-1",
            resolution_mode="engineering_reviewed", room_ids=["room-1", "room-2"],
        )
        row = ledger["surfaces"][0]
        self.assertEqual(row["status"], "blocked")
        self.assertIn("adjacent_space_missing", row["unresolved_fields"])

    def test_3d_only_surface_is_excluded(self):
        vision = self._vision([{
            "surface_id": "P5-VSURFACE-003",
            "physical_type": "wall",
            "thermal_role": "external",
            "boundary_condition": "outside",
            "boundary_points_px": [[0, 0], [10, 0], [10, 3], [0, 3], [0, 0]],
            "evidence_refs": [{"page": 5, "reference": "render"}],
            "confidence": "high",
        }], page_role="3d_render")
        ledger = resolve_thermal_surfaces(
            vision, [{"page": 5, "proposed_role": "3d_render"}], "source-1",
            resolution_mode="preliminary_ai_estimate",
        )
        self.assertEqual(ledger["surfaces"][0]["status"], "blocked")
        self.assertFalse(ledger["surfaces"][0]["thermal_eligible"])


if __name__ == "__main__":
    unittest.main()
