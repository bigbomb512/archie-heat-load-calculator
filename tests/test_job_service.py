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
        from backend import autonomous_tasks_service, calculation_extraction_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=records), \
             patch.object(autonomous_tasks_service, "post", side_effect=lambda web, project, data, **_kwargs: posts.append(data)), \
             patch.object(calculation_extraction_service, "post"):
            answered = job_service.apply_roof_answer(SimpleNamespace(), self.project)
        self.assertEqual(answered, 2)
        self.assertEqual([(row["target"], row["answer"]) for row in posts], [("shop", "roof_directly_above"), ("bar", "roof_directly_above")])

    def test_project_roof_answer_updates_only_unknown_hand_trace_roofs_and_preserves_edges(self):
        job_service.save_job_setup(self.project, {"above": "floor"})
        hand = {"trace_id": "hand", "room_id": "shop", "declaration_source": "reviewer", "roof": "unknown",
                "edges": [{"index": 0, "boundary": "mall"}], "openings": [{"opening_id": "w", "edge_index": 0}],
                "openings_none_edges": [1]}
        already_answered = {**hand, "trace_id": "answered", "roof": "exposed", "roof_source": "reviewer"}
        ai_trace = {**hand, "trace_id": "ai", "declaration_source": "ai_determined"}
        calls = []
        from backend import autonomous_tasks_service, calculation_extraction_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(reviewer_room_geometry_service, "current_records", return_value=[hand, already_answered, ai_trace]), \
             patch.object(reviewer_room_geometry_service, "post", side_effect=lambda _web, _project, payload, **_kwargs: calls.append(payload)), \
             patch.object(calculation_extraction_service, "post") as rebuild:
            answered = job_service.apply_roof_answer(SimpleNamespace(), self.project)
            rebuild.assert_called_once()
            rebuild.reset_mock()
            job_service.apply_roof_answer(SimpleNamespace(), self.project, defer_evidence_rebuild=True)
            rebuild.assert_not_called()
        self.assertEqual(answered, 2)
        self.assertEqual(calls[0]["roof"], "not_exposed")
        self.assertEqual(calls[0]["reviewer"], "Answered on the Project tab")
        self.assertTrue(calls[0]["confirm_roof"])
        self.assertEqual(calls[0]["edges"], hand["edges"])
        self.assertEqual(calls[0]["openings"], hand["openings"])
        self.assertEqual(calls[0]["openings_none_edges"], [1])
        self.assertEqual({row["trace_id"] for row in calls}, {"hand", "ai"})

    def test_project_not_sure_keeps_p5_behavior_but_does_not_change_trace(self):
        job_service.save_job_setup(self.project, {"above": "not_sure"})
        from backend import autonomous_tasks_service
        posts = []
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[
                {"task": "P5_roof", "target": "shop", "status": "needs_contractor_answer"}]), \
             patch.object(autonomous_tasks_service, "post", side_effect=lambda _web, _project, payload, **_kwargs: posts.append(payload)), \
             patch.object(reviewer_room_geometry_service, "current_records", return_value=[
                 {"trace_id": "hand", "room_id": "shop", "roof": "unknown", "edges": [], "openings": []}]), \
             patch.object(reviewer_room_geometry_service, "post") as classify:
            self.assertEqual(job_service.apply_roof_answer(SimpleNamespace(), self.project), 1)
        self.assertEqual(posts[0]["answer"], "not_sure")  # existing P5 behavior remains
        classify.assert_not_called()

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
                         {"project": "check", "drawings": "working", "rooms": "check", "walls": "needed",
                          "windows": "todo", "results": "done"})
        self.assertEqual(first["checks"]["waiting"], 1)
        self.assertEqual(first["rooms"], {"total": 2, "included": 1, "with_area": 1})
        self.assertEqual(second["rooms"], {"total": 2, "included": 2, "with_area": 2})
        self.assertEqual(second["tabs"]["project"]["state"], "done")
        self.assertEqual(second["total_kw"], 34.1)
        self.assertEqual(second["area_overrides"][0]["area_m2"], 104.9)

    def test_status_adds_walls_and_windows_states_from_saved_envelope_records(self):
        shop = "room-use:unassigned-level:shop"
        model = {"room_scope": {"status": "confirmed", "candidates": [
            {"key": shop, "label": "Shop", "level": "Ground", "area_m2": 20, "include": True, "status": "calculated"}]},
            "hourly_ai_preliminary_load_report": {"included_scope_peak": {"final_design_total_kw": 20}}}
        complete = {shop: {"has_outline": True, "below_accuracy_bar": False, "roof": "not_exposed", "roof_source": "reviewer",
                           "edges": [{"trace_id": "t", "index": 0, "boundary": "external"}],
                           "openings": [], "no_glazing_edges": [{"trace_id": "t", "index": 0}], "window_count": 0}}
        incomplete = {shop: {**complete[shop], "roof": "unknown", "edges": [{"trace_id": "t", "index": 0, "boundary": "unknown"}], "no_glazing_edges": []}}
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(ai_preliminary_service, "get", return_value=model), \
             patch.object(job_service, "envelope_rooms", side_effect=[{}, complete, incomplete]):
            missing = job_service.status(SimpleNamespace(), self.project)
            done = job_service.status(SimpleNamespace(), self.project)
            check = job_service.status(SimpleNamespace(), self.project)
        self.assertEqual((missing["tabs"]["walls"]["state"], missing["tabs"]["windows"]["state"]), ("needed", "todo"))
        self.assertEqual((done["tabs"]["walls"]["state"], done["tabs"]["windows"]["state"]), ("done", "done"))
        self.assertEqual((check["tabs"]["walls"]["state"], check["tabs"]["windows"]["state"]), ("check", "todo"))
        self.assertEqual(check["tabs"]["windows"]["detail"], "Classify the walls first")
        self.assertEqual(done["envelope"][shop]["window_count"], 0)

    def test_subtab_statuses_cover_missing_review_complete_empty_and_in_progress(self):
        shop = "room-use:unassigned-level:shop"
        complete_model = {"room_scope": {"status": "confirmed", "candidates": [
            {"key": shop, "label": "Shop", "level": "Ground", "area_m2": 20, "include": True,
             "status": "calculated", "area_quality_label": "AI-determined"}]}}
        review_model = {"room_scope": {"status": "confirmed", "candidates": [
            {**complete_model["room_scope"]["candidates"][0], "status": "needs_use",
             "area_quality_label": "AI-determined (below accuracy bar)"}]}}
        complete_envelope = {shop: {"has_outline": True, "roof": "not_exposed", "roof_source": "reviewer",
                                    "edges": [{"trace_id": "t", "index": 0, "boundary": "external", "source": "reviewer"}]}}
        review_envelope = {shop: {"has_outline": True, "roof": "not_exposed", "roof_source": "ai_fallback",
                                  "edges": [{"trace_id": "t", "index": 0, "boundary": "external", "source": "ai_fallback"}]}}
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(ai_preliminary_service, "get", side_effect=[complete_model, review_model, {}]), \
             patch.object(job_service, "envelope_rooms", side_effect=[complete_envelope, review_envelope, {}, {}]):
            complete = job_service.status(SimpleNamespace(), self.project)
            review = job_service.status(SimpleNamespace(), self.project)
            empty = job_service.status(SimpleNamespace(), self.project)
            working_project = {**self.project, "reasoning_packet": ""}
            with patch.object(autonomous_tasks_service, "_task_progress", return_value={"total": 2, "waiting": 1, "blocked": 0}):
                working = job_service.status(SimpleNamespace(), working_project)
        self.assertEqual(complete["subtabs"]["walls"]["walls"]["state"], "done")
        self.assertEqual(complete["subtabs"]["walls"]["roof"]["state"], "done")
        self.assertEqual(complete["subtabs"]["project"]["job_site"]["state"], "done")  # address and building type remain optional
        self.assertEqual(review["subtabs"]["rooms"]["room_details"]["state"], "check")
        self.assertEqual(review["subtabs"]["rooms"]["measurements"]["state"], "check")
        self.assertEqual(review["subtabs"]["walls"]["roof"]["state"], "check")
        self.assertEqual((empty["subtabs"]["rooms"]["room_details"]["state"],
                          empty["subtabs"]["rooms"]["measurements"]["state"]), ("needed", "todo"))
        self.assertEqual((working["subtabs"]["rooms"]["room_details"]["state"],
                          working["subtabs"]["walls"]["walls"]["state"]), ("working", "working"))
        self.assertEqual(empty["tabs"]["walls"]["state"], "needed")  # no new calculation gate

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

    def test_traced_rooms_give_their_area_at_once_and_a_typed_area_still_wins(self):
        square = [[0, 0], [1000, 0], [1000, 500], [0, 500], [0, 0]]  # 1000 × 500 px at 10 mm/px = 50 m²
        (self.root / "reviewer_room_geometry.json").write_text(json.dumps({"records": [
            {"room_id": "k", "page": 20, "points_image_px": square, "calibration": {"status": "agreed", "mm_per_px": 10}},
            {"room_id": "s", "page": 20, "points_image_px": square, "calibration": {"status": "unresolved", "mm_per_px": None}},
            {"room_id": "b", "page": 20, "points_image_px": square[:3], "calibration": {"status": "agreed", "mm_per_px": 10}},
            {"room_id": "o", "page": 21, "points_image_px": square, "calibration": {"status": "declared_scale_rejected", "mm_per_px": 10},
             "declaration_source": "ai_determined"}]}))
        traced = job_service.traced_rooms(self.root)
        self.assertEqual(traced, {"k": {"area_m2": 50.0, "pages": [20], "source": "traced"},
                                  "o": {"area_m2": 50.0, "pages": [21], "source": "ai_determined"}})
        model = {"room_scope": {"status": "confirmed", "candidates": [
            {"key": "k", "label": "Kitchen", "area_m2": None, "include": False},
            {"key": "o", "label": "Office", "area_m2": None, "include": False}]}}
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(ai_preliminary_service, "get", return_value=model):
            job_service.save_area_override(self.project, {"room_id": "o", "label": "Office", "area_m2": 12})
            status = job_service.status(SimpleNamespace(), self.project)
        self.assertEqual(status["rooms"], {"total": 2, "included": 2, "with_area": 2})
        self.assertEqual(status["traced_rooms"]["k"]["area_m2"], 50.0)

    def test_envelope_summary_reads_wall_lengths_sources_roof_and_no_glazing_directly(self):
        points = [[0, 0], [400, 0], [400, 500], [0, 500], [0, 0]]
        (self.root / "reviewer_room_geometry.json").write_text(json.dumps({"records": [{
            "trace_id": "t", "room_id": "shop", "page": 20, "points_image_px": points,
            "calibration": {"status": "agreed", "mm_per_px": 10},
            "edges": [{"index": 0, "boundary": "mall"}], "edge_sources": {"0": "ai_determined"},
            "roof": "not_exposed", "roof_source": "reviewer", "openings": [{"edge_index": 0}],
            "openings_none_edges": [1],
        }]}))
        room = job_service.envelope_rooms(self.root)["shop"]
        self.assertEqual(room["edges"][0], {"trace_id": "t", "page": 20, "index": 0, "boundary": "mall",
                                             "length_m": 4.0, "source": "ai_determined"})
        self.assertEqual((room["roof"], room["roof_source"], room["window_count"], room["no_glazing_edges"]),
                         ("not_exposed", "reviewer", 1, [{"trace_id": "t", "index": 1}]))

    def test_below_accuracy_bar_is_scoped_to_each_rooms_own_trace_sources(self):
        square = [[0, 0], [400, 0], [400, 500], [0, 500], [0, 0]]
        (self.root / "reviewer_room_geometry.json").write_text(json.dumps({"records": [
            {"trace_id": "shop-trace", "room_id": "shop", "points_image_px": square,
             "calibration": {"mm_per_px": 10}, "edges": [{"index": 0, "boundary": "external"}],
             "edge_sources": {"0": "ai_fallback"}, "roof": "unknown"},
            {"trace_id": "kitchen-trace", "room_id": "kitchen", "points_image_px": square,
             "calibration": {"mm_per_px": 10}, "edges": [{"index": 0, "boundary": "external"}],
             "edge_sources": {"0": "reviewer"}, "roof": "not_exposed", "roof_source": "reviewer"},
        ]}))
        rooms = job_service.envelope_rooms(self.root)
        self.assertTrue(rooms["shop"]["below_accuracy_bar"])
        self.assertFalse(rooms["kitchen"]["below_accuracy_bar"])

    def test_windows_waits_for_unknown_walls_to_be_classified(self):
        shop = "room-use:unassigned-level:shop"
        model = {"room_scope": {"status": "confirmed", "candidates": [
            {"key": shop, "label": "Shop", "level": "Ground", "area_m2": 20, "include": True, "status": "calculated"}]}}
        unknown = {shop: {"has_outline": True, "roof": "unknown", "edges": [
            {"trace_id": "t", "index": 0, "boundary": "unknown"}], "openings": [], "no_glazing_edges": []}}
        from backend import ai_preliminary_service, autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]), \
             patch.object(ai_preliminary_service, "get", return_value=model), \
             patch.object(job_service, "envelope_rooms", return_value=unknown):
            status = job_service.status(SimpleNamespace(), self.project)
        self.assertEqual(status["tabs"]["windows"], {"state": "todo", "detail": "Classify the walls first"})

    def test_status_before_the_drawings_are_prepared(self):
        project = {**self.project, "reasoning_packet": ""}
        from backend import autonomous_tasks_service
        with patch.object(autonomous_tasks_service, "_all_current", return_value=[]):
            status = job_service.status(SimpleNamespace(), project)
        self.assertEqual({tab: row["state"] for tab, row in status["tabs"].items()},
                         {"project": "needed", "drawings": "needed", "rooms": "needed", "walls": "needed",
                          "windows": "todo", "results": "todo"})
        self.assertIsNone(status["total_kw"])


if __name__ == "__main__":
    unittest.main()
