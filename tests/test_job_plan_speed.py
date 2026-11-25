"""/api/job-plan works each value out once and reuses it only while what it was worked out from is unchanged
(2026-11-20: 3-13 s per call on the Turramurra house, enough for the browser to give up and leave Broken pipe errors).
Each cache here is checked both ways: reused when nothing changed, worked out again when an input changed."""

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai import room_scope_confirmation
from backend import ai_preliminary_service as service
from backend import reviewer_room_geometry_service as geometry_service


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(value), encoding="utf-8")
    os.replace(staged, path)       # the jobs' atomic save: a new inode, so the signature changes


def input_set(area=12.0, floor_kind="ground"):
    return {
        "input_fingerprint": f"set-{area}",
        "dependency_fingerprints": {"building_evidence": "b"},
        "phase1_floor_basis": {"kind": floor_kind},
        "material": {"hourly_load_model": {
            "rooms": [{"room_id": "r1", "name": "Living", "zone_id": "z1", "area_m2": area,
                       "cooling_load": {"envelope_surfaces": []}, "occupancy": 4, "heat_sources": []}],
            "zones": [{"zone_id": "z1", "floor_id": "f1"}], "floors": [{"floor_id": "f1", "name": "Ground Floor"}]},
            "design_day_scenarios": {}, "preliminary_policy": {}},
        "materialized_fields": [{"room_id": "r1", "field": "area_m2", "origin": "traced", "quality_label": "AI",
                                 "evidence": [{"page": 8}]},
                                {"room_id": "r1", "field": "space_scope", "value": "comfort"},
                                {"room_id": "r1", "field": "lighting_load_w", "value": 100}],
        "excluded_spaces": [{"scope": "unresolved_scope", "room_name": "Void", "level": "Ground Floor", "reason": "?"}],
    }


class RequestMemoTests(unittest.TestCase):
    def test_one_request_builds_each_view_once_and_hands_out_copies(self):
        calls = []

        def response(web, project, view, check):
            calls.append(view)
            return {"rooms": [1]}

        with patch.object(service, "_response", side_effect=response):
            with service.one_request():
                first = service.get(None, {"review_dir": "/job"}, "workspace", check_freshness=True)
                first["rooms"].append(2)                       # a caller changing its copy changes no other
                second = service.get(None, {"review_dir": "/job"}, "workspace", check_freshness=True)
            service.get(None, {"review_dir": "/job"}, "workspace", check_freshness=True)   # a new request
        self.assertEqual(second, {"rooms": [1]})
        self.assertEqual(calls, ["workspace", "workspace"])


class FingerprintMemoTests(unittest.TestCase):
    def test_the_phase1_fingerprint_is_worked_out_once_per_saved_input_set(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "ai_preliminary_input_set.json"
            write(path, input_set())
            with patch.object(service, "_compute_phase1_input_fingerprint",
                              side_effect=lambda s: s["material"]["hourly_load_model"]["rooms"][0]["area_m2"]) as compute:
                derive = service._phase1_input_fingerprint
                self.assertEqual(service._file_derived(path, {}, "phase1_input_fingerprint", derive), 12.0)
                self.assertEqual(service._file_derived(path, {}, "phase1_input_fingerprint", derive), 12.0)
                self.assertEqual(compute.call_count, 1)
                write(path, input_set(area=13.0))
                self.assertEqual(service._file_derived(path, {}, "phase1_input_fingerprint", derive), 13.0)

    def test_a_derived_value_is_worked_out_again_when_its_file_is_saved_again(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            write(path, {"n": 1})
            derive = lambda value: {"n": value.get("n")}
            with patch.object(service, "_read", wraps=service._read) as read:
                self.assertEqual(service._file_derived(path, {}, "test", derive), {"n": 1})
                self.assertEqual(service._file_derived(path, {}, "test", derive), {"n": 1})
                self.assertEqual(read.call_count, 1)
                write(path, {"n": 2})
                self.assertEqual(service._file_derived(path, {}, "test", derive), {"n": 2})
            self.assertEqual(service._file_derived(Path(folder) / "missing.json", {}, "test", derive), {"n": None})


class WorkspaceInputTests(unittest.TestCase):
    def test_the_compact_input_set_gives_the_same_room_list_and_operating_inputs(self):
        full = input_set()
        compact = service._workspace_input(full)
        room_use = {"records": []}
        self.assertEqual(room_scope_confirmation.state(compact["input_set"], None, room_use),
                         room_scope_confirmation.state(full, None, room_use))
        self.assertEqual(compact["phase2_room_inputs"], service._phase2_room_inputs(full))
        self.assertEqual(compact["input_set"]["dependency_fingerprints"], full["dependency_fingerprints"])
        self.assertEqual(service._workspace_input({})["input_set"], {})

    def test_report_parts_keep_what_the_staleness_checks_compare(self):
        report = {"equipment_source_hash": "e", "phase1_input_fingerprint": "p", "scenario_results": []}
        parts = service._workspace_report_parts(report)
        self.assertEqual((parts["equipment_source_hash"], parts["phase1_input_fingerprint"]), ("e", "p"))
        self.assertEqual(parts["workspace"], service._workspace_report(report))
        self.assertEqual(service._workspace_report_parts({}), {})


class SavedGeometryFingerprintTests(unittest.TestCase):
    def test_the_saved_fingerprint_is_reused_after_a_restart_and_redone_when_an_input_or_the_code_changes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write(root / "reviewer_room_geometry.json", {"records": []})
            paths = {"root": root, "vision": root / "vision_response.json", "building": root / "building_evidence.json",
                     "run": root / "ai_preliminary_run.json"}
            builds = []

            def build(_paths):
                builds.append(1)
                return {"rooms": len(builds)}

            def restart():
                service._GEOMETRY_FINGERPRINT_MEMO.clear()
                service._GEOMETRY_MEMO.clear()

            with patch.object(service, "_preliminary_geometry", side_effect=build):
                first = service._preliminary_geometry_fingerprint(paths)
                restart()
                self.assertEqual(service._preliminary_geometry_fingerprint(paths), first)
                self.assertEqual(len(builds), 1)                         # read back from the job folder
                self.assertTrue((root / service.GEOMETRY_FINGERPRINT_CACHE).is_file())
                time.sleep(0.01)
                write(root / "reviewer_room_geometry.json", {"records": [1]})
                restart()
                self.assertNotEqual(service._preliminary_geometry_fingerprint(paths), first)
                self.assertEqual(len(builds), 2)
                restart()
                with patch.object(service, "_source_code_fingerprint", return_value="other code"):
                    service._preliminary_geometry_fingerprint(paths)
                self.assertEqual(len(builds), 3)

    def test_the_saved_fingerprint_is_not_a_json_file_so_job_signatures_ignore_it(self):
        self.assertFalse(service.GEOMETRY_FINGERPRINT_CACHE.endswith(".json"))


class PdfPageSizeTests(unittest.TestCase):
    def test_page_sizes_match_pdfplumber_including_rotated_pages(self):
        import pdfplumber
        import pypdfium2 as pdfium
        with TemporaryDirectory() as folder:
            path = Path(folder) / "set.pdf"
            document = pdfium.PdfDocument.new()
            for width, height, rotation in ((1190.55, 841.89, 0), (1190.55, 841.89, 90), (841.89, 595.28, 270)):
                page = document.new_page(width, height)
                page.set_rotation(rotation)
            document.save(str(path))
            document.close()
            stat = path.stat()
            sizes = geometry_service._cached_pdf_sizes(str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            with pdfplumber.open(str(path)) as pdf:
                expected = {index + 1: (float(page.width), float(page.height)) for index, page in enumerate(pdf.pages)}
        self.assertEqual(sizes.keys(), expected.keys())
        for number, size in expected.items():
            for value, other in zip(sizes[number], size):
                self.assertAlmostEqual(value, other, places=3)
        self.assertAlmostEqual(sizes[2][0], 841.89, places=2)            # turned a quarter: width and height swap


if __name__ == "__main__":
    unittest.main()
