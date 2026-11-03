"""A two-storey house through the Phase 1 surface builder: walls to the garage, unclassified outside walls, and an
upper bedroom's floor split over the garage and the bedroom below (matched by the stair traced on both levels)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import ceiling_volume_resolution, room_use_resolution
from backend import ai_preliminary_service, reviewer_room_geometry_service


def square(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]


ROOMS = [  # label, level, page, taxonomy, scope, outline (px; 10 mm per px)
    ("Garage", "Ground Floor", 1, "garage", "not_cooled", square(0, 0, 300, 400)),
    ("Bed 2", "Ground Floor", 1, "house_bedroom", "comfort_hvac", square(300, 0, 300, 400)),
    ("Stair", "Ground Floor", 1, "circulation", "comfort_hvac", square(600, 0, 100, 300)),
    # The first-floor plan sits 50 px right and down of the ground-floor one; the stair shows by how much.
    ("Bed 1", "First Floor", 2, "house_bedroom", "comfort_hvac", square(50, 50, 600, 400)),
    ("Stair", "First Floor", 2, "circulation", "comfort_hvac", square(650, 50, 100, 300)),
]


def trace(label, level, page, points):
    identity = room_use_resolution.room_identity(label, level)
    return {"trace_id": f"trace-{label}-{page}".replace(" ", "-").casefold(), "room_id": identity, "room_label": label,
            "level_name": level, "page": page, "points_image_px": points,
            "calibration": {"status": "agreed", "mm_per_px": 10.0},
            "edges": [{"index": index, "boundary": "unknown"} for index in range(len(points) - 1)],
            "roof": "not_exposed", "openings": [], "area_m2": (points[2][0] - points[0][0]) * (points[2][1] - points[0][1]) / 10000,
            "envelope_reviewer": "Archie AI", "declaration_source": "ai_determined"}


class HouseEnvelopeAssemblyTests(unittest.TestCase):
    def prepare(self, dwelling=True):
        traces = [trace(label, level, page, points) for label, level, page, _taxonomy, _scope, points in ROOMS]
        records = [{"room_id": row["room_id"], "original_label": row["room_label"], "level_name": row["level_name"],
                    "taxonomy_id": taxonomy, "space_scope": scope, "preliminary_profile_id": taxonomy, "status": "resolved"}
                   for row, (_l, _v, _p, taxonomy, scope, _pts) in zip(traces, ROOMS)]
        room_use = {"records": records, **({"dwelling_context": {"is_dwelling": True, "evidence": []}} if dwelling else {})}
        proposal = {"rooms": [{"kind": "room", "room_id": row["room_id"], "label": row["room_label"],
                               "level_name": row["level_name"], "page": row["page"], "space_scope": scope,
                               "preliminary_profile_id": taxonomy, "evidence": [{"page": row["page"], "excerpt": row["room_label"]}]}
                              for row, (_l, _v, _p, taxonomy, scope, _pts) in zip(traces, ROOMS)]}
        ceilings = {ceiling_volume_resolution.room_identity(row["room_label"], row["level_name"]): {"ceiling_height_mm": 2700}
                    for row in traces}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in {"room_use_resolution.json": room_use, "ai_input.json": {}, "building_evidence.json": {"spaces": []},
                                "ai_preliminary_run.json": {}, "vector_geometry.json": {}}.items():
                (root / name).write_text(json.dumps(value))
            paths = ai_preliminary_service._paths({"review_dir": str(root)})
            with patch.object(reviewer_room_geometry_service, "current_traced_areas",
                              return_value={row["room_id"]: row for row in traces}), \
                 patch.object(reviewer_room_geometry_service, "current_records", return_value=traces), \
                 patch.object(ceiling_volume_resolution, "values_by_room", return_value=ceilings):
                prepared = ai_preliminary_service._prepare_preliminary_proposal(paths, proposal, room_use, {"entities": []})
        return prepared["surfaces"]

    def surfaces_of(self, surfaces, trace_id):
        return [row for row in surfaces if row.get("reviewer_trace_id") == trace_id]

    def test_walls_to_the_garage_are_unconditioned_and_unclassified_outer_walls_face_outside(self):
        walls = {row["surface_key"].rsplit(":", 1)[-1]: row for row in self.surfaces_of(self.prepare(), "trace-bed-2-1")
                 if row["physical_type"] == "wall"}
        # Bed 2: top (0) and bottom (2) walls face nothing → outside; right (1) runs along the stair → internal;
        # left (3) is the garage wall → unconditioned (buffer temperature).
        self.assertEqual(walls["3"]["boundary_condition"], "unconditioned_space")
        self.assertIn("Garage", walls["3"]["phase1_assumption"])
        self.assertEqual(walls["1"]["boundary_condition"], "internal")
        outside = [row for key, row in walls.items() if key in {"0", "2"}]
        self.assertTrue(all(row["thermal_role"] == "external" for row in outside), outside)
        self.assertTrue(any("labelled assumption" in str(row.get("phase1_assumptions")) for row in outside))

    def test_the_upper_bedroom_floor_is_split_over_the_garage_and_the_bedroom_below(self):
        floors = [row for row in self.surfaces_of(self.prepare(), "trace-bed-1-2") if row["physical_type"] == "floor"]
        by_kind = {row["surface_key"].rsplit(":", 1)[-1]: row for row in floors}
        self.assertEqual(sorted(by_kind), ["cooled", "not-cooled"])
        self.assertAlmostEqual(by_kind["not-cooled"]["gross_area_m2"], 12.0)
        self.assertEqual(by_kind["not-cooled"]["boundary_condition"], "unconditioned_below")
        self.assertIn("Stair above Stair", by_kind["not-cooled"]["rationale"])
        self.assertIn("Over Garage", by_kind["not-cooled"]["rationale"])
        self.assertAlmostEqual(by_kind["cooled"]["gross_area_m2"], 12.0)
        self.assertEqual(by_kind["cooled"]["thermal_role"], "conditioned_adjacent")

    def test_roofs_come_from_the_level_plans(self):
        surfaces = self.prepare()
        top = [row for row in self.surfaces_of(surfaces, "trace-bed-1-2") if row["physical_type"] in {"roof", "ceiling"}]
        self.assertEqual([(row["physical_type"], row["gross_area_m2"]) for row in top], [("roof", 24.0)])
        self.assertIn("top level", top[0]["rationale"])
        below = [row for row in self.surfaces_of(surfaces, "trace-bed-2-1") if row["physical_type"] in {"roof", "ceiling"}]
        self.assertEqual([(row["physical_type"], row["thermal_role"], row["gross_area_m2"]) for row in below],
                         [("ceiling", "conditioned_adjacent", 12.0)])
        self.assertIn("Bed 1", below[0]["label"])

    def test_the_lowest_level_sits_on_the_ground(self):
        floors = [row for row in self.surfaces_of(self.prepare(), "trace-bed-2-1") if row["physical_type"] == "floor"]
        self.assertEqual([row["boundary_condition"] for row in floors], ["ground"])
        self.assertIn("lowest level", floors[0]["rationale"])

    def test_a_shop_keeps_its_rules(self):
        walls = [row for row in self.surfaces_of(self.prepare(dwelling=False), "trace-bed-2-1") if row["physical_type"] == "wall"]
        self.assertEqual(sorted({row["boundary_condition"] for row in walls}), ["adjacent_tenancy", "internal"])


class OutsideWallQuestionTests(unittest.TestCase):
    """The outside-walls AI question (P3) isn't asked for a house unless its drawings mention a shared wall."""

    def job(self, folder, dwelling, inventory_text):
        root = Path(folder)
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [], "dwelling_context": {"is_dwelling": dwelling}}))
        (root / "page_inventory.json").write_text(json.dumps({"pages": {"3": {"title": inventory_text, "information": []}}}))
        return root

    def test_skipped_only_for_a_house_without_shared_walls(self):
        from backend import autonomous_tasks_service
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(autonomous_tasks_service._house_without_shared_walls(self.job(folder, True, "GROUND FLOOR PLAN")))
            self.assertEqual(autonomous_tasks_service._p3_packets(Path(folder)), [])
        for text in ("NATHERS COMMITMENTS DWELLING A", "PARTY WALL TO NO. 47", "Dual occupancy - ground floor"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as folder:
                self.assertFalse(autonomous_tasks_service._house_without_shared_walls(self.job(folder, True, text)))
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(autonomous_tasks_service._house_without_shared_walls(self.job(folder, False, "GROUND FLOOR PLAN")))


if __name__ == "__main__":
    unittest.main()
