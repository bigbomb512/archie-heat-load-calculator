#!/usr/bin/env python3
"""Job workspace backend: job setup, typed room areas and the tab-rail status (docs/UI_REBUILD_PLAN.md phase 1)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend import job_service, reviewer_room_geometry_service


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = {"id": "job", "name": "Corner cafe.pdf", "review_dir": str(self.root), "reasoning_packet": "x"}

    def tearDown(self):
        self.temp.cleanup()

    def test_job_setup_saves_known_fields_and_refuses_unknown_choices(self):
        saved = job_service.save_job_setup(self.project, {"name": "  Corner   cafe ", "address": "1 Main St, Ryde NSW",
                                                          "building_type": "food_tenancy", "above": "floor", "edited_by": "Sam"})
        self.assertEqual((saved["name"], saved["address"], saved["building_type"], saved["above"], saved["updated_by"]),
                         ("Corner cafe", "1 Main St, Ryde NSW", "food_tenancy", "floor", "Sam"))
        self.assertEqual(job_service.job_setup(self.project)["address"], "1 Main St, Ryde NSW")
        with self.assertRaisesRegex(ValueError, "building type"):
            job_service.save_job_setup(self.project, {"building_type": "spaceship"})
        with self.assertRaisesRegex(ValueError, "above the tenancy"):
            job_service.save_job_setup(self.project, {"above": "sky"})
        kept = job_service.save_job_setup(self.project, {})
        self.assertEqual(kept["building_type"], "food_tenancy")

    def test_the_answer_for_whats_above_is_applied_to_every_open_roof_question(self):
        job_service.save_job_setup(self.project, {"above": "roof"})
        records = [{"task": "P5_roof", "target": "shop", "status": "needs_contractor_answer"},
                   {"task": "P5_roof", "target": "bar", "status": "needs_contractor_answer"},
                   {"task": "P5_roof", "target": "kitchen", "status": "applied"}]
        posts = []
        from backend import autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=records), \
             patch.object(autonomous_tasks_service, "post", side_effect=lambda web, project, data: posts.append(data)):
            answered = job_service.apply_roof_answer(SimpleNamespace(), self.project)
        self.assertEqual(answered, 2)
        self.assertEqual([(row["target"], row["answer"]) for row in posts], [("shop", "roof_directly_above"), ("bar", "roof_directly_above")])

    def test_typed_areas_are_set_cleared_and_checked(self):
        job_service.save_area_override(self.project, {"room_id": "room-use:unassigned-level:kitchen", "label": "Kitchen",
                                                      "area_m2": 104.94, "edited_by": "Sam"})
        rows = job_service.area_overrides(self.root)
        self.assertEqual([(row["room_id"], row["area_m2"], row["edited_by"]) for row in rows],
                         [("room-use:unassigned-level:kitchen", 104.94, "Sam")])
        job_service.save_area_override(self.project, {"room_id": "room-use:unassigned-level:kitchen", "area_m2": 99})
        self.assertEqual([row["area_m2"] for row in job_service.area_overrides(self.root)], [99.0])
        job_service.save_area_override(self.project, {"room_id": "room-use:unassigned-level:kitchen", "area_m2": None})
        self.assertEqual(job_service.area_overrides(self.root), [])
        for bad in (0, -3, 25000, "12"):
            with self.assertRaisesRegex(ValueError, "area in m²"):
                job_service.save_area_override(self.project, {"room_id": "r", "area_m2": bad})
        with self.assertRaisesRegex(ValueError, "name"):
            job_service.save_area_override(self.project, {"area_m2": 10})

    def test_a_typed_area_wins_in_the_area_lookup_and_keeps_a_traced_room_outline(self):
        traced = {"room-use:unassigned-level:shop": {"room_id": "room-use:unassigned-level:shop", "room_label": "Shop",
                                                     "area_m2": 201.3, "trace_id": "t1", "edges": [{"index": 0, "boundary": "mall"}],
                                                     "declaration_source": "ai_determined"}}
        job_service.save_area_override(self.project, {"room_id": "room-use:unassigned-level:shop", "label": "Shop", "area_m2": 216.1})
        job_service.save_area_override(self.project, {"room_id": "room-use:unassigned-level:store", "label": "Store", "area_m2": 6})
        with patch.object(reviewer_room_geometry_service, "current_artifact_input", return_value={"records": [], "rooms": []}):
            base = reviewer_room_geometry_service.current_traced_areas(self.root)
        self.assertEqual(base["room-use:unassigned-level:store"]["area_source"], "edited")
        self.assertTrue(base["room-use:unassigned-level:store"]["area_only"])
        merged = reviewer_room_geometry_service.apply_area_overrides(traced, job_service.area_overrides(self.root))
        self.assertEqual(merged["room-use:unassigned-level:shop"]["area_m2"], 216.1)
        self.assertEqual(merged["room-use:unassigned-level:shop"]["area_source"], "edited")
        self.assertNotIn("area_only", merged["room-use:unassigned-level:shop"])
        self.assertEqual(traced["room-use:unassigned-level:shop"]["area_m2"], 201.3)  # input not changed
        self.assertEqual(base["room-use:unassigned-level:shop"]["area_m2"], 216.1)
        self.assertEqual(merged["room-use:unassigned-level:shop"]["edges"], [{"index": 0, "boundary": "mall"}])

    def test_status_gives_one_state_per_tab_and_counts_typed_areas_at_once(self):
        tasks = [{"task": "P1_site", "target": "project", "status": "below_accuracy_bar",
                  "applied_value": {"site_text": "TENANCY 7, CENTRAL MALL"}},
                 {"task": "P0_wall_styles", "target": "b1", "status": "waiting_for_reply"}]
        model = {"room_scope": {"status": "confirmed", "candidates": [
                    {"key": "k", "label": "Kitchen", "area_m2": None, "include": False, "status": "no_area"},
                    {"key": "s", "label": "Shop", "area_m2": 216.1, "include": True, "status": "calculated",
                     "area_quality_label": "AI-determined (below accuracy bar)"}]},
                 "hourly_ai_preliminary_load_report": {"included_scope_peak": {"final_design_total_kw": 34.1}}}
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=tasks), \
             patch.object(ai_preliminary_service, "get", return_value=model):
            first = job_service.status(SimpleNamespace(), self.project)
            job_service.save_area_override(self.project, {"room_id": "k", "label": "Kitchen", "area_m2": 104.9})
            job_service.save_job_setup(self.project, {"address": "1 Main St", "above": "floor"})
            second = job_service.status(SimpleNamespace(), self.project)
        self.assertEqual(first["found_site"], "TENANCY 7, CENTRAL MALL")
        self.assertEqual({tab: row["state"] for tab, row in first["tabs"].items()},
                         {"project": "check", "drawings": "working", "rooms": "check", "results": "done"})
        self.assertEqual(first["checks"]["waiting"], 1)
        self.assertEqual(first["rooms"], {"total": 2, "included": 1, "with_area": 1})
        self.assertEqual(second["rooms"], {"total": 2, "included": 2, "with_area": 2})
        self.assertEqual(second["tabs"]["project"]["state"], "done")
        self.assertEqual(second["total_kw"], 34.1)
        self.assertEqual(second["area_overrides"][0]["area_m2"], 104.9)

    def test_typed_heights_are_saved_by_the_ceiling_identity_and_checked(self):
        saved = job_service.save_height_override(self.project, {"room_key": "room-use:unassigned-level:kitchen", "label": "Kitchen",
                                                                 "ceiling_height_mm": 3200})
        self.assertEqual(saved["room_id"], "room:unassigned-level:kitchen")
        self.assertEqual([(row["room_id"], row["room_key"], row["ceiling_height_mm"]) for row in job_service.height_overrides(self.root)],
                         [("room:unassigned-level:kitchen", "room-use:unassigned-level:kitchen", 3200)])
        for bad in (1000, 20000, "3.2"):
            with self.assertRaisesRegex(ValueError, "ceiling height"):
                job_service.save_height_override(self.project, {"label": "Kitchen", "ceiling_height_mm": bad})
        with self.assertRaisesRegex(ValueError, "room"):
            job_service.save_height_override(self.project, {"ceiling_height_mm": 3000})
        job_service.save_height_override(self.project, {"label": "Kitchen", "ceiling_height_mm": None})
        self.assertEqual(job_service.height_overrides(self.root), [])

    def test_a_typed_height_wins_in_the_ceiling_resolver_and_leaves_no_record_for_a_missing_room(self):
        from ai import ai_preliminary, ceiling_volume_resolution
        building = {"spaces": [{"name": "Kitchen", "level_name": "Unassigned level"}]}
        pack = ai_preliminary.load_pack()
        before = ceiling_volume_resolution.resolve(building, {}, {"rooms": []}, {}, pack, {}, None)
        kitchen = next(row for row in before["records"] if row["room_id"] == "room:unassigned-level:kitchen")
        self.assertEqual(kitchen["origin"], "preliminary_fallback")
        job_service.save_height_override(self.project, {"label": "Kitchen", "ceiling_height_mm": 3600})
        job_service.save_height_override(self.project, {"label": "Old store", "ceiling_height_mm": 3000})
        after = job_service.drop_typed_height_stubs(ceiling_volume_resolution.resolve(
            building, {}, {"rooms": []}, {}, pack, {}, job_service.with_typed_heights(before, self.root)))
        kitchen = next(row for row in after["records"] if row["room_id"] == "room:unassigned-level:kitchen")
        self.assertEqual((kitchen["ceiling_height_mm"], kitchen["origin"], kitchen["status"]), (3600.0, "contractor_override", "resolved"))
        self.assertNotIn("room:unassigned-level:old-store", [row["room_id"] for row in after["records"]])
        checked = ceiling_volume_resolution.validate(after)
        self.assertEqual(checked["fingerprint"], after["fingerprint"])
        self.assertIs(job_service.with_typed_heights(before, self.root / "none"), before)

    def test_status_reports_heights_per_room_and_flags_typed_inputs_newer_than_the_result(self):
        model = {"room_scope": {"status": "confirmed", "candidates": [
                    {"key": "room-use:unassigned-level:kitchen", "label": "Kitchen", "level": "Unassigned level", "area_m2": 100, "include": True},
                    {"key": "room-use:unassigned-level:shop", "label": "Shop", "level": "Unassigned level", "area_m2": 200, "include": True}]},
                 "hourly_ai_preliminary_load_report": {"included_scope_peak": {"final_design_total_kw": 30.0}}}
        (self.root / "ceiling_volume_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "room:unassigned-level:shop", "ceiling_height_mm": 2700.0, "origin": "preliminary_fallback"}]}))
        report = self.root / "hourly_ai_preliminary_load_report.json"
        report.write_text("{}")
        import os
        os.utime(report, (1_000_000, 1_000_000))
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(ai_preliminary_service, "get", return_value=model):
            first = job_service.status(SimpleNamespace(), self.project)
            job_service.save_height_override(self.project, {"label": "Kitchen", "ceiling_height_mm": 3200})
            second = job_service.status(SimpleNamespace(), self.project)
        self.assertFalse(first["result_stale"])
        self.assertEqual(first["room_heights"], {"room-use:unassigned-level:shop": {"ceiling_height_mm": 2700.0, "origin": "preliminary_fallback"}})
        self.assertTrue(second["result_stale"])
        self.assertEqual(second["tabs"]["results"]["state"], "check")
        self.assertEqual(second["room_heights"]["room-use:unassigned-level:kitchen"], {"ceiling_height_mm": 3200, "origin": "edited"})

    def test_the_saved_page_selection_is_returned_so_unticked_pages_stay_unticked(self):
        from backend import web_app
        self.assertIsNone(web_app.selected_pages(self.project))
        (self.root / "reviewed_decisions.json").write_text(json.dumps({"pages": [
            {"page": 21, "decision": "Confirm as detected"}, {"page": 20, "decision": "Confirm as floor plan"},
            {"page": "x"}, "bad", {"page": 20}]}))
        self.assertEqual(web_app.selected_pages(self.project), [20, 21])
        (self.root / "reviewed_decisions.json").write_text("{broken")
        self.assertIsNone(web_app.selected_pages(self.project))

    def test_status_before_the_drawings_are_prepared(self):
        project = {**self.project, "reasoning_packet": ""}
        from backend import autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]):
            status = job_service.status(SimpleNamespace(), project)
        self.assertEqual({tab: row["state"] for tab, row in status["tabs"].items()},
                         {"project": "needed", "drawings": "needed", "rooms": "needed", "results": "todo"})
        self.assertIsNone(status["total_kw"])


if __name__ == "__main__":
    unittest.main()
