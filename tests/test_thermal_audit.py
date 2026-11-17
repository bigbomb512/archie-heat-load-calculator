import tempfile
import unittest
from pathlib import Path
import json

from backend.thermal_audit_service import _flag, build_elements, read_audit, TOLERANCE_KW, _worked, _provenance, _north_origin
from ai.ai_preliminary import _glazing_value_origin, _shading_value_origin, _scenario, load_pack


class ThermalAuditTests(unittest.TestCase):
    def test_origin_is_structured_and_airah_does_not_mean_ai(self):
        self.assertEqual(_provenance(31.1, "AIRAH Handbook", origin="standard_value")["flag"], "standard_value")
        self.assertIsNone(_provenance(31.1, "AIRAH Handbook")["flag"])
        self.assertEqual(_provenance(0.8, "reviewer-declared assumption pack", origin="assumed_default")["flag"], "assumed_default")

    def test_trace_and_override_origins(self):
        self.assertEqual(_provenance(20, origin="ai_determined")["flag"], "ai_estimate")
        self.assertEqual(_provenance(20, origin="reviewer_traced")["flag"], "measured_from_drawing")
        edited = _provenance(20, origin="edited", edited_by="Operator", edited_at="2026-11-16")
        self.assertEqual((edited["flag"], edited["edited_by"], edited["edited_at"]),
                         ("edited_by_user", "Operator", "2026-11-16"))

    def test_north_basis_from_records(self):
        ai = {"plan_up_azimuth_deg": 47.338, "source": "reviewer_typed_page_up_bearing", "declaration_source": "ai_determined"}
        self.assertEqual(_north_origin(ai, {}), "ai_estimate")
        self.assertEqual(_north_origin(ai, {"axis": {"axis_deg": 47.338}}), "measured_from_drawing")
        self.assertEqual(_north_origin({"source": "reviewer_typed_page_up_bearing"}, {}), "edited_by_user")
        # An axis is the same either way round: set 15's north 306.1 deg on its 126.1 deg axis (review 2026-11-17).
        turned = {"plan_up_azimuth_deg": 306.097, "declaration_source": "ai_determined"}
        self.assertEqual(_north_origin(turned, {"axis": {"axis_deg": 126.097}}), "measured_from_drawing")
        refined = {**turned, "north_basis": {"label": "project", "survey_axis_deg": 126.097}}
        self.assertEqual(_north_origin(refined, {}), "measured_from_drawing")
        self.assertEqual(_flag("household_rule"), "assumed_default")

    def test_glazing_and_lighting_basis_flags(self):
        self.assertEqual(_provenance(5.8, origin="certificate")["flag"], "read_from_schedule")
        self.assertEqual(_provenance(6.7, origin="standard_value")["flag"], "standard_value")
        self.assertEqual(_provenance(5.8, origin="edited_by_user")["flag"], "edited_by_user")
        self.assertEqual(_provenance(5, origin="standard_value")["flag"], "standard_value")
        self.assertEqual(_provenance(5, origin="controlled_preliminary_profile")["flag"], "assumed_default")

    def test_glazing_and_shading_are_marked_at_selection(self):
        self.assertEqual(_glazing_value_origin({"glazing_basis": "certificate"}, 5.8, .45, None), "read_from_schedule")
        self.assertEqual(_glazing_value_origin({"glazing_basis": "user_edit"}, 5.8, .45, None), "edited_by_user")
        self.assertEqual(_glazing_value_origin({}, None, None, {"window_id": "ALM-002-01 A"}), "standard_value")
        self.assertEqual(_glazing_value_origin({}, None, None, None), "assumed_default")
        self.assertEqual(_shading_value_origin({"shading_basis": "user_edit"}), "edited_by_user")
        self.assertEqual(_shading_value_origin({"shading_basis": "ai_determined"}), "ai_estimate")
        self.assertEqual(_shading_value_origin({"shade": {"depth_m": 0.6, "declaration_source": "ai_determined"}}), "ai_estimate")
        self.assertEqual(_shading_value_origin({"shading_category": "unshaded"}), "assumed_default")

    def test_weather_origin_is_recorded_without_source_words(self):
        pack = load_pack()
        generic = _scenario(pack, basis={"design_day": {"site_specific": False, "source": "AIRAH mentioned in note"}})
        cited = _scenario(pack, basis={"design_day": {"site_specific": True, "source": "Cited design table"}})
        self.assertEqual(generic["scenarios"][0]["hours"][0]["outdoor_dry_bulb_c"]["value_origin"], "assumed_default")
        self.assertEqual(cited["scenarios"][0]["hours"][0]["outdoor_dry_bulb_c"]["value_origin"], "standard_value")

    def test_capture_every_scenario_and_reconcile(self):
        rows = [{"surface_id": "wall-1", "gain_kw": 0.25, "cooling_load_kw": 0.21},
                {"surface_id": "wall-2", "gain_kw": 0.1, "cooling_load_kw": 0.14}]
        room = {"room_id": "room-1", "stored_heat": {"method_id": "rts"},
                "hours": [{"hour": h, "components": {"envelope": {"sensible_kw": 0.35,
                    "heat_gain_sensible_kw": 0.35, "input_rows": [{"surfaces": rows}]}}} for h in range(24)]}
        report = {"preliminary_input_set_fingerprint": "abc", "scenario_results": [
            {"scenario_id": f"month-{m}", "representative_month": m, "rooms": [room]} for m in range(1, 13)]}
        side = build_elements(report)
        self.assertEqual(len(side["scenarios"]), 12)
        for scenario in side["scenarios"]:
            for hour in scenario["rooms"][0]["hours"]:
                self.assertLessEqual(abs(sum(row["load_kw"] for row in hour["elements"]) -
                                         hour["component_totals_kw"]["envelope"]), TOLERANCE_KW)
        self.assertEqual(side["scenarios"][0]["rooms"][0]["hours"][0]["elements"][0]["stored_heat_method"], "rts")

    def test_solar_working_uses_recorded_operands(self):
        row = {"component": "solar", "gain_kw": 0.2, "load_kw": 0.18,
               "solar_design_w_m2": 500, "solar_gain_factor": 0.1, "shading_factor": 0.8}
        worked = _worked(row, {"area_m2": 5})
        self.assertIn("5 m² × 500 W/m² × 0.1 × 0.8", worked["substituted"])
        self.assertEqual(worked["recorded_load_w"], 180)

    def test_read_only_and_missing_north(self):
        report = {"preliminary_input_set_fingerprint": "abc", "scenario_results": [{"scenario_id": "ai_preliminary_cooling_day",
                  "rooms": [{"room_id": "r", "name": "Shop", "hours": [{"hour": 15, "components": {}, "subtotal_kw": 0}]}]}],
                  "included_scope_peak": {"hour": 15}}
        model = {"input_fingerprint": "abc", "topology": {"rooms": [{"room_id": "r", "name": "Shop", "area_m2": 10,
                 "cooling_load": {"envelope_surfaces": [], "glazing_surfaces": []}}]}}
        side = build_elements(report)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in [("hourly_ai_preliminary_load_report.json", report), ("ai_preliminary_model.json", model),
                                ("thermal_audit_elements.json", side), ("reviewer_room_geometry.json", {"records": [], "page_north": {}})]:
                (root / name).write_text(json.dumps(value))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = read_audit(root, 1, 15)
            self.assertEqual(result["rooms"][0]["name"], "Shop")
            model["materialized_fields"] = [{"room_id": "r", "field": "area_m2", "value": 10,
                                              "origin": "edited", "reviewer": "Contractor", "edited_at": "2026-11-16"}]
            (root / "ai_preliminary_model.json").write_text(json.dumps(model))
            edited = read_audit(root, 1, 15)["rooms"][0]["area_m2"]
            self.assertEqual(edited["flag"], "edited_by_user")
            self.assertEqual(edited["edited_by"], "Contractor")
            (root / "room_area_overrides.json").write_text(json.dumps({"overrides": [
                {"room_id": "room:ground:shop", "room_label": "Shop", "area_m2": 10,
                 "edited_by": "Quick-fix user", "edited_at": "2026-11-16"}]}))
            quick_fix = read_audit(root, 1, 15)["rooms"][0]["area_m2"]
            self.assertEqual((quick_fix["flag"], quick_fix["edited_by"]),
                             ("edited_by_user", "Quick-fix user"))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            self.assertIsNone(result["summary"]["north"]["value"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            with self.assertRaises(ValueError):
                read_audit(root, 13, 15)

    def test_wall_bearing_comes_from_trace_edge(self):
        surface = {"surface_id": "wall-1", "kind": "opaque_wall", "reviewer_trace_id": "trace-1",
                   "edge_index": 0, "area_m2": 5, "u_value_w_m2k": 0.6}
        model = {"input_fingerprint": "abc", "topology": {"rooms": [{"room_id": "room-1", "name": "Kitchen",
                 "area_m2": 10, "cooling_load": {"envelope_surfaces": [surface], "glazing_surfaces": []}}]}}
        report = {"preliminary_input_set_fingerprint": "abc", "scenario_results": [{"scenario_id": "ai_preliminary_cooling_day",
                  "rooms": [{"room_id": "room-1", "name": "Kitchen", "hours": [{"hour": 15, "components": {}}]}]}]}
        side = build_elements(report)
        side["scenarios"][0]["rooms"][0]["hours"][0]["elements"] = [
            {"id": "wall-1", "component": "envelope", "gain_kw": 0.1, "load_kw": 0.1,
             "boundary_temperature_c": 31.1}]
        traces = {"page_north": {"1": {"plan_up_azimuth_deg": 0, "source": "reviewer_read_north_arrow"}},
                  "records": [{"trace_id": "trace-1", "room_id": "room-1", "page": 1,
                               "points_image_px": [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]],
                               "calibration": {"mm_per_px": 10}, "edges": [{"index": 0, "boundary": "external"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in [("hourly_ai_preliminary_load_report.json", report), ("ai_preliminary_model.json", model),
                                ("thermal_audit_elements.json", side), ("reviewer_room_geometry.json", traces)]:
                (root / name).write_text(json.dumps(value))
            result = read_audit(root, 1, 15)
            edge = result["pages"][0]["outlines"][0]["edges"][0]
            bearing = result["rooms"][0]["elements"][0]["values"]["orientation_deg"]
            self.assertEqual(bearing["value"], edge["outward_bearing_deg"])
            self.assertEqual(bearing["flag"], "measured_from_drawing")
