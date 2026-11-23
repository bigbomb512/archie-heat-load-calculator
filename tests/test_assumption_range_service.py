"""Assumption range cases on disposable jobs; no cooling calculation is run."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ai import construction_u_values
from backend import ai_preliminary_service, assumption_range_service as service, contractor_report


def write(root, name, value):
    (root / name).write_text(json.dumps(value), encoding="utf-8")


def read(root, name):
    return json.loads((root / name).read_text(encoding="utf-8"))


class Job(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "job"
        self.root.mkdir()
        self.project = {"id": "job", "review_dir": str(self.root), "name": "Test house"}
        self.report = {"included_scope_peak": {"final_design_total_kw": 9.0},
                       "preliminary_input_set_fingerprint": "first"}
        write(self.root, ai_preliminary_service._paths(self.project)["report"].name, self.report)
        self.set_surfaces(windows=[{"source": "assumption pack"}], walls=True, roof=True)
        write(self.root, "reviewer_room_geometry.json", {"page_north": {
            "1": {"plan_up_azimuth_deg": 20, "declaration_source": "ai_determined"},
            "2": {"plan_up_azimuth_deg": 350, "declaration_source": "ai_determined"}}})
        write(self.root, "room_use_resolution.json", {"dwelling_context": {"is_dwelling": True}})
        rows = []
        for unknown in service.load_cases()["unknowns"]:
            for case in unknown["cases"]:
                change = case["change"]
                if change["kind"] == "construction" and change.get("construction_id"):
                    rows.append({"id": change["construction_id"], "surface": change["surface"],
                                 "section": "Frame walls" if change["surface"] == "wall" else "Pitched roofs",
                                 "name": case["label"], "u_value_w_m2k": 0.5 + len(rows) * 0.2})
        patcher = mock.patch.object(construction_u_values, "load_table", return_value={"constructions": rows})
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_surfaces(self, *, windows=(), walls=False, roof=False):
        envelope = ([{"kind": "opaque_wall", "boundary_method": "external"}] if walls else [])
        envelope += ([{"kind": "roof"}] if roof else [])
        write(self.root, "ai_preliminary_input_set.json", {"material": {"hourly_load_model": {
            "rooms": [{"cooling_load": {"envelope_surfaces": envelope, "glazing_surfaces": list(windows)}}]}}})

    def unknowns(self):
        return {row["key"]: row for row in service.open_unknowns(self.root, self.report)}


class OpenUnknownsTests(Job):
    def test_windows_answer_and_certificates(self):
        self.assertTrue(self.unknowns()["windows"]["open"])
        write(self.root, "glazing_answers.json", {"all": {"u_value_w_m2k": 3.0, "shgc": 0.4}})
        self.assertFalse(self.unknowns()["windows"]["open"])
        self.assertTrue(self.unknowns()["windows"]["answered"])
        (self.root / "glazing_answers.json").unlink()
        self.set_surfaces(windows=[{"source": "NatHERS certificate"}, {"glazing_source": "WERS certificate"}])
        self.assertFalse(self.unknowns()["windows"]["open"])
        self.set_surfaces(windows=[{"source": "certificate"}, {"source": "assumption pack"}])
        self.assertTrue(self.unknowns()["windows"]["open"])

    def test_construction_requires_surface_and_no_answer(self):
        self.set_surfaces(walls=False, roof=False)
        self.assertFalse(self.unknowns()["wall"]["open"])
        self.assertFalse(self.unknowns()["roof"]["open"])
        self.set_surfaces(walls=True, roof=True)
        self.assertTrue(self.unknowns()["wall"]["open"])
        self.assertTrue(self.unknowns()["roof"]["open"])
        write(self.root, "construction_answers.json", {"wall": {"all": {"construction_id": "wall"}},
                                                        "roof": {"rooms": {"Living": {"construction_id": "roof"}}}})
        for key in ("wall", "roof"):
            self.assertFalse(self.unknowns()[key]["open"])
            self.assertTrue(self.unknowns()[key]["answered"])

    def test_ai_north_becomes_answered_when_reviewer_declares_it(self):
        self.assertTrue(self.unknowns()["north"]["open"])
        write(self.root, "reviewer_room_geometry.json", {"page_north": {
            "1": {"plan_up_azimuth_deg": 20, "source": "reviewer_typed_page_up_bearing"}}})
        self.assertFalse(self.unknowns()["north"]["open"])
        self.assertTrue(self.unknowns()["north"]["answered"])


class RunTests(Job):
    def test_every_case_changes_only_its_copy(self):
        originals = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        cases = {case["id"]: case["change"] for unknown in service.load_cases()["unknowns"]
                 for case in unknown["cases"]}
        seen = {}

        def calculate(copy, *_project):
            copy = Path(copy)
            glass = read(copy, "glazing_answers.json") if (copy / "glazing_answers.json").exists() else {}
            construction = read(copy, "construction_answers.json") if (copy / "construction_answers.json").exists() else {}
            north = read(copy, "reviewer_room_geometry.json")["page_north"]
            # A wall or roof case is its U-value typed in, with no handbook construction_id (see the cases file).
            walls = (construction.get("wall") or {}).get("all") or {}
            roofs = (construction.get("roof") or {}).get("all") or {}
            for answer in (walls, roofs):
                if answer:
                    self.assertEqual(answer["construction_id"], "")
            signature = (glass.get("all", {}).get("u_value_w_m2k"),
                         ("wall", walls.get("u_value_w_m2k")) if walls else None,
                         ("roof", roofs.get("u_value_w_m2k")) if roofs else None,
                         north["1"]["plan_up_azimuth_deg"])
            for case_id, change in cases.items():
                if change["kind"] == "glazing" and signature[0] == change["u_value_w_m2k"]:
                    self.assertEqual(glass["all"]["shgc"], change["shgc"])
                    seen[case_id] = True
                    return 9.0 + change["shgc"]
                if change["kind"] == "construction" and (change["surface"], change["u_value_w_m2k"]) in signature[1:3]:
                    seen[case_id] = True
                    return 9.0 + change["u_value_w_m2k"]
                if change["kind"] == "north" and signature[3] == 200:
                    self.assertEqual(north["2"]["plan_up_azimuth_deg"], 170)
                    seen[case_id] = True
                    return 9.8
            return 9.0

        with mock.patch.object(service, "_calculate", side_effect=calculate):
            result = service.run(self.project)
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(result["total_cases"], 1 + len(cases))
        self.assertEqual(result["cases"][0]["case_id"], "baseline")
        self.assertEqual(set(seen), set(cases))
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()
                          if path.name in originals}, originals)

    def test_one_failed_case_does_not_stop_the_rest(self):
        calls = 0

        def calculate(copy, *_project):
            nonlocal calls
            calls += 1
            glass = read(Path(copy), "glazing_answers.json") if (Path(copy) / "glazing_answers.json").exists() else {}
            if glass.get("all", {}).get("u_value_w_m2k") == 2.9:
                raise RuntimeError("case failed")
            return 9.0

        with mock.patch.object(service, "_calculate", side_effect=calculate):
            with self.assertLogs(service.__name__, "ERROR"):
                result = service.run(self.project)
        self.assertEqual(result["status"], "calculated")
        self.assertEqual(calls, result["total_cases"])
        failed = next(row for row in result["cases"] if row["case_id"] == "double_low_e")
        self.assertIn("RuntimeError: case failed", failed["error"])
        self.assertIn("kw", result["cases"][-1])

    def test_changed_baseline_stops_before_alternatives(self):
        with mock.patch.object(service, "_calculate", return_value=9.021) as calculate:
            result = service.run(self.project)
        self.assertEqual(result["status"], "inputs_changed")
        self.assertEqual(result["baseline_kw"], 9.021)
        self.assertEqual(len(result["cases"]), 1)
        calculate.assert_called_once()


class CompactAndGetTests(Job):
    def test_compact_uses_only_calculated_cases_and_ranks_by_unit_then_swing(self):
        unknowns = self.unknowns()
        state = {"cases": [
            {"unknown": "windows", "case_id": "w1", "label": "W1", "kw": 10.1},
            {"unknown": "windows", "case_id": "w2", "label": "W2", "error": "failed"},
            {"unknown": "wall", "case_id": "wall1", "label": "Wall", "kw": 7.0},
            {"unknown": "roof", "case_id": "roof1", "label": "Roof", "kw": 9.4},
            {"unknown": "north", "case_id": "north1", "label": "North", "error": "failed"}]}
        result = service.compact(state, self.report, list(unknowns.values()), True)
        self.assertEqual((result["low_kw"], result["high_kw"]), (7.0, 10.1))
        self.assertEqual([row["key"] for row in result["unknowns"]], ["wall", "windows", "roof", "north"])
        self.assertEqual(result["unknowns"][0]["swing_kw"], 2.0)
        self.assertIsNone(result["unknowns"][-1]["swing_kw"])
        self.assertEqual(result["unknowns"][1]["cases"][1]["status"], "not_calculated")
        base = contractor_report.ducted(9.0, True)["suggested"]
        for row in result["unknowns"]:
            for case in row["cases"]:
                if case["status"] == "calculated":
                    self.assertEqual(case["changes_unit"], contractor_report.ducted(case["kw"], True)["suggested"] != base)

    def test_get_cache_and_report_fingerprint(self):
        self.assertEqual(service.get(self.project, start=False)["status"], "not_started")
        state = {"key": service._key(self.report), "status": "calculated", "unknowns": [{"key": "windows"}],
                 "cases": [{"unknown": "windows", "case_id": "double_low_e", "label": "Low e", "kw": 8.0}]}
        write(self.root, service.CACHE_NAME, state)
        self.assertEqual(service.get(self.project, start=False)["status"], "calculated")
        self.report["preliminary_input_set_fingerprint"] = "second"
        write(self.root, "hourly_ai_preliminary_load_report.json", self.report)
        self.assertEqual(service.get(self.project, start=False)["status"], "not_started")

    def test_get_without_report_or_open_unknowns(self):
        (self.root / "hourly_ai_preliminary_load_report.json").unlink()
        self.assertEqual(service.get(self.project, start=False)["status"], "not_calculated")
        write(self.root, "hourly_ai_preliminary_load_report.json", self.report)
        self.set_surfaces()
        write(self.root, "reviewer_room_geometry.json", {"page_north": {}})
        self.assertEqual(service.get(self.project, start=False)["status"], "nothing_open")

    def test_feature_flag_off_does_not_start_thread(self):
        with mock.patch.dict("os.environ", {"ARCHIE_ASSUMPTION_RANGE": "off"}), mock.patch.object(service, "_start") as start:
            self.assertEqual(service.get(self.project)["status"], "not_started")
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
