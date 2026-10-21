"""The supervisor repairs current calculation inputs, with bounded attempts and recovery."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend import autonomous_tasks_service as tasks, drawing_tasks_service as drawings
from backend import phase1_supervisor_service as supervisor
from backend.job_runner import JobCancelled, checkpoint, read_json, write_json


def room(name="Shop", **changes):
    return {"id": name.lower(), "name": name, "scope": "comfort_hvac", "area_m2": 40,
            "area_source": "printed_dimensions", "height": {"origin": "project_evidence", "ceiling_height_mm": 3200},
            "pages": [20], "traces": [{"page": 20, "roof": "not_exposed", "roof_source": "ai_determined",
                                     "edges": [{"index": 0, "boundary": "external"}]}], **changes}


def snapshot(*rooms, **changes):
    return {"rooms": list(rooms) or [room()], "tasks": [], "height_pages": [], "storefront_pages": [],
            "report": {}, "model_inputs": [], **changes}


class AuditTests(unittest.TestCase):
    def test_missing_and_last_resort_areas_have_first_priority(self):
        for value in (room(area_m2=None), room(area_source="room_size_estimated")):
            gap = supervisor.audit(snapshot(value))[0]
            self.assertEqual((gap["rank"], gap["task"], gap["kind"]), (1, "P0_room_outlines", "room_area"))

    def test_unanswered_drawing_task_is_routed_to_its_original_packet(self):
        record = {"task": "P5_roof", "target": "shop", "packet": {"room_id": "shop", "page": 20}, "status": "blocked"}
        gap = supervisor.audit(snapshot(tasks=[record]))[0]
        self.assertEqual((gap["rank"], gap["task"], gap["target"]), (2, "P5_roof", "shop"))

    def test_missing_storefront_glass_is_found_in_model_even_when_skills_claim_success(self):
        value = snapshot(storefront_pages=[25], skill_proposals={"glazing_properties": {"status": "completed"}})
        gap = supervisor.audit(value)[0]
        self.assertEqual((gap["rank"], gap["task"], gap["pages"]), (3, "P4_storefront_wall", [25]))
        value["tasks"] = [{"task": "P4_openings", "target": "page-25-shop", "status": "applied", "packet": {"room_id": "shop"}}]
        self.assertEqual(supervisor.audit(value)[0]["task"], "P4_openings")
        value["rooms"][0]["model"] = {"cooling_load": {"glazing_surfaces": [{"area_m2": 12}]}}
        self.assertEqual(supervisor.audit(value), [])

    def test_fallback_ceiling_height_with_labels_reasks_the_labelled_page(self):
        gap = supervisor.audit(snapshot(room(height={"origin": "preliminary_fallback", "ceiling_height_mm": 2700}), height_pages=[22]))[0]
        self.assertEqual((gap["rank"], gap["task"], gap["pages"]), (4, "P7_ceiling_heights", [22]))

    def test_all_internal_walls_with_shopfront_elevation_need_boundary_check(self):
        value = room(traces=[{"page": 20, "roof": "not_exposed", "edges": [{"boundary": "internal"}, {"boundary": "adjacent_tenancy"}]}])
        gap = supervisor.audit(snapshot(value, storefront_pages=[25]))[0]
        self.assertEqual((gap["rank"], gap["task"]), (5, "P3_boundaries"))

    def test_unknown_wall_and_fallback_roof_are_separate_material_gaps(self):
        value = room(traces=[{"page": 20, "roof": "exposed", "roof_source": "ai_fallback", "edges": [{"boundary": "unknown"}]}])
        gaps = supervisor.audit(snapshot(value))
        self.assertEqual({(gap["rank"], gap["task"]) for gap in gaps}, {(6, "P3_boundaries"), (6, "P5_roof")})

    def test_zero_and_omitted_room_results_are_not_read_as_complete(self):
        report = {"phase1_coverage": {"rooms_left_out": [{"label": "Shop"}]},
                  "scenario_results": [{"rooms": [{"name": "Shop", "peak": {"total_kw": 0}}]}]}
        gap = supervisor.audit(snapshot(report=report))[0]
        self.assertEqual((gap["rank"], gap["kind"], gap["task"]), (7, "omitted_room", "P3_boundaries"))
        report["phase1_coverage"]["rooms_left_out"] = []
        self.assertEqual(supervisor.audit(snapshot(report=report))[0]["kind"], "zero_load")
        self.assertEqual(supervisor.audit(snapshot(report={"included_scope_peak": {"total_kw": 0}}))[0]["kind"], "zero_tenancy")

    def test_an_internal_rooms_zero_load_is_the_right_answer_not_a_gap(self):
        # Butcher Buffet's Bar: walls to other rooms and tenancies, a ceiling to conditioned space. Zero by rule.
        bar = room("Bar", traces=[{"page": 20, "roof": "not_exposed", "roof_source": "ai_determined",
                                   "edges": [{"index": 0, "boundary": "internal"}, {"index": 1, "boundary": "adjacent_tenancy"},
                                             {"index": 2, "boundary": "mall"}]}])
        report = {"scenario_results": [{"rooms": [{"name": "Bar", "peak": {"total_kw": 0}}]}], "included_scope_peak": {"total_kw": 0}}
        self.assertEqual([gap["kind"] for gap in supervisor.audit(snapshot(bar, report=report))], [])
        exposed_roof = room("Bar", traces=[{**bar["traces"][0], "roof": "exposed"}])
        self.assertEqual(supervisor.audit(snapshot(exposed_roof, report=report))[0]["kind"], "zero_load")

    def test_material_gaps_are_ranked_and_not_starved_by_routine_notes(self):
        value = room(area_m2=None, height={"origin": "preliminary_fallback"}, traces=[
            {"page": 20, "roof": "unknown", "edges": [{"boundary": "external"}, {"boundary": "unknown"}]}])
        task = {"task": "P3_boundaries", "target": "page-20", "status": "waiting_for_reply", "packet": {"page": 20}}
        gaps = supervisor.audit(snapshot(value, storefront_pages=[25], height_pages=[22], tasks=[task],
            skill_proposals={"sheet_identity": {"unresolved_fields": ["hundreds of routine notes"]}}))
        self.assertEqual([gap["rank"] for gap in gaps], [1, 2, 3, 4, 6, 6])
        self.assertEqual(len(supervisor.select_investigations(gaps, {})), 6)
        self.assertEqual(supervisor.audit(snapshot(skill_proposals={"a": {"unresolved_fields": ["note"]}})), [])

    def test_changed_prose_does_not_change_gap_identity_or_model_progress(self):
        one = snapshot(room(area_m2=None))
        two = deepcopy(one)
        two["rooms"][0]["height"]["rationale"] = "Different prose, same value"
        self.assertEqual(supervisor.model_fingerprint(one), supervisor.model_fingerprint(two))
        self.assertEqual(supervisor.audit(one)[0]["id"], supervisor.audit(two)[0]["id"])

    def test_an_empty_model_cannot_be_called_ready(self):
        gaps = supervisor.audit({"rooms": [], "tasks": []})
        self.assertEqual(gaps[0]["task"], "room_identity_use")
        self.assertIn("none is represented", gaps[0]["message"])

    def test_refrigeration_scope_is_not_silently_converted_into_a_cooled_room(self):
        self.assertEqual(supervisor.audit(snapshot(room("Freezer", scope="refrigeration_process", area_m2=None))), [])


class RoutingAndCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "test", "review_dir": str(self.root), "reasoning_packet": "ready"}

    def test_targeted_note_changes_fingerprint_but_same_note_reuses_the_run(self):
        packet = {"task": "P7_ceiling_heights", "page": 22, "rooms": ["Shop"], "labels": []}
        base = tasks._create_run(self.root, "P7_ceiling_heights", "page-22", deepcopy(packet), "Read labels")
        gap = supervisor.audit(snapshot(room(height={"origin": "preliminary_fallback"}), height_pages=[22]))[0]
        with patch.object(tasks, "_p7_packets", return_value=[("P7_ceiling_heights", "page-22", packet, "Read labels", [], "")]):
            first = tasks.prepare_investigation(self.project, gap)[0]
            second = tasks.prepare_investigation(self.project, gap)[0]
        self.assertNotEqual(first["input_fingerprint"], base["input_fingerprint"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertIn(gap["note"], first["prompt"])
        self.assertIn("22", first["prompt"])

    def test_refresh_preserves_a_targeted_rejection_and_its_correction_reason(self):
        baseline = {"room": "Shop"}
        targeted = tasks._create_run(self.root, "P5_roof", "shop", {**baseline, "supervisor_note": "Check above"}, "Read roof\nCheck above")
        targeted["reply_attempts"] = [{"outcome": "rejected", "reason": "The quotation was not on the drawing."}]
        tasks._update_record(self.root, targeted)
        refreshed = tasks._create_run(self.root, "P5_roof", "shop", baseline, "Read roof")
        self.assertEqual(refreshed["run_id"], targeted["run_id"])
        self.assertIn("quotation was not on the drawing", drawings._prompt(refreshed))

    def test_checks_for_same_packet_are_combined_before_creating_a_run(self):
        gaps = supervisor.audit(snapshot(room("Shop", height={"origin": "preliminary_fallback"}),
            room("Kitchen", height={"origin": "preliminary_fallback"}), height_pages=[22]))
        grouped = supervisor.group_investigations(gaps)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["room_names"], ["Kitchen", "Shop"])
        self.assertEqual(set(grouped[0]["gap_ids"]), {gap["id"] for gap in gaps})

    def test_cancelled_request_keeps_paid_answer_and_resume_reuses_it(self):
        record = tasks._create_run(self.root, "P5_roof", "shop", {"room": "Shop"}, "Read roof")
        cancelled = threading.Event()
        calls = []
        class Fake:
            model = "fake"
            def propose(_self, prompt, image_paths):
                calls.append(prompt)
                cancelled.set()
                return {"roof": "not_exposed"}, {"usage": {"cost_usd": .05}}
        def check():
            if cancelled.is_set():
                raise JobCancelled()
        with self.assertRaises(JobCancelled):
            drawings.request_investigation(self.project, record, provider_for=lambda task: Fake(), check=check)
        cancelled.clear()
        result = drawings.request_investigation(self.project, record, provider_for=lambda task: Fake(), check=lambda: None)
        self.assertTrue(result["reused"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(supervisor._usage(self.root), {"calls": 1, "cost_usd": .05, "unpriced_calls": 0})

    def test_stale_worker_cannot_apply_over_a_replacement_task(self):
        old = tasks._create_run(self.root, "P5_roof", "shop", {"v": 1}, "Old")
        tasks._create_run(self.root, "P5_roof", "shop", {"v": 2}, "New")
        with patch.object(tasks, "post") as publish:
            result = drawings.apply_investigation(None, self.project, old, {"reply": {}, "cache_path": "/unused", "reused": True})
        publish.assert_not_called()
        self.assertEqual(result["status"], "superseded")

    def test_failed_reply_is_archived_with_reason_and_cost(self):
        record = tasks._create_run(self.root, "P5_roof", "shop", {"v": 1}, "Read")
        error = ai_error = RuntimeError("invalid JSON")
        ai_error.raw_record = {"reply_text": "broken", "usage": {"cost_usd": .02}}
        drawings.archive_investigation_failure(self.project, record, error)
        saved = tasks._current_task(self.root, "P5_roof", "shop")
        self.assertEqual(saved["reply_attempts"][0]["outcome"], "provider_failed")
        attempt = next((tasks._record_file(self.root, saved).parent / "reply_attempts").glob("*.json"))
        self.assertEqual(read_json(attempt)["raw_reply"], "broken")

    def test_a_partial_room_use_reply_cannot_replace_the_full_room_list(self):
        from backend import skill_workflow_service as skills
        folder = self.root / "skill_workflow_runs" / "review" / "proposals"
        folder.mkdir(parents=True)
        previous = {"proposal_fields": {"rooms": [{"room_id": "shop"}, {"room_id": "kitchen"}]}, "citations": [{"id": "c1", "page": 1}]}
        write_json(folder / "room_identity_use.json", previous)
        write_json(self.root / "skill_workflow_run.json", {"run_id": "review", "source_fingerprint": "a" * 64,
            "subskills": {"room_identity_use": {"status": "done"}}})
        partial = {"status": "provisional", "input_fingerprint": "b" * 64, "proposal_fields": {"rooms": [{"room_id": "shop"}]}}
        with patch.object(skills, "_execute_subskill", return_value=partial), \
                patch.object(skills, "_validate_subskill_output", side_effect=lambda spec, reply, *args, **kwargs: reply), \
                patch.object(skills, "_consented_page_ids", return_value={1}), \
                patch.object(skills, "_persist_skill_attempt") as archive:
            with self.assertRaisesRegex(ValueError, "omitted previously evidenced rooms"):
                supervisor._room_use_investigation(None, self.project, {"note": "Check Shop"})
        self.assertEqual(read_json(folder / "room_identity_use.json"), previous)
        self.assertEqual(archive.call_args.kwargs["outcome"], "rejected")

    def test_one_packet_is_selected_per_attempt_then_an_alternative_view(self):
        packet = {"task": "P7_ceiling_heights", "rooms": ["Shop"], "labels": []}
        gap = supervisor.audit(snapshot(room(height={"origin": "preliminary_fallback"}), height_pages=[22, 23]))[0]
        choices = [("P7_ceiling_heights", f"page-{page}", {**packet, "page": page}, "Read", [], "") for page in (22, 23)]
        with patch.object(tasks, "_p7_packets", return_value=choices):
            first = tasks.prepare_investigation(self.project, gap)
            second = tasks.prepare_investigation(self.project, {**gap, "previous_targets": ["page-22"]})
        self.assertEqual(len(first), 1)
        self.assertEqual(second[0]["target"], "page-23")

    def test_bookkeeping_never_counts_as_model_progress(self):
        value = snapshot(model_inputs=[{"name": "Shop", "envelope": [{"kind": "opaque_wall", "area_m2": 20, "source": "old", "surface_id": "old"}]}])
        changed = deepcopy(value)
        changed["model_inputs"][0]["envelope"][0].update(source="new citation", surface_id="new-run-id")
        self.assertEqual(supervisor.model_fingerprint(value), supervisor.model_fingerprint(changed))
        changed["model_inputs"][0]["envelope"][0]["area_m2"] = 25
        self.assertNotEqual(supervisor.model_fingerprint(value), supervisor.model_fingerprint(changed))



class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = {"id": "run", "review_dir": str(self.root), "reasoning_packet": "ready"}
        write_json(self.root / supervisor.JOB_FILE, {"status": "running"})
        self.patchers = [patch.object(supervisor.tasks, "_refresh_geometry_tasks"),
                        patch("backend.job_progress.stage_blocker", return_value=""),
                        patch.object(supervisor, "ai_unavailable", return_value="")]
        for item in self.patchers:
            item.start(); self.addCleanup(item.stop)

    def fake_prepare(self, project, gap):
        return [{"task": gap["task"], "target": gap.get("room_id"), "run_id": "fake"}]

    def execute(self, snapshots):
        with patch.object(supervisor, "_rebuild", side_effect=snapshots), \
                patch.object(tasks, "prepare_investigation", side_effect=self.fake_prepare), \
                patch.object(drawings, "request_investigation", return_value={"reply": {}}) as ask, \
                patch.object(drawings, "apply_investigation", return_value={"status": "applied"}):
            result = supervisor.run(None, self.project)
        return result, ask.call_count

    def test_rebuild_and_reaudit_close_fixed_gap(self):
        result, calls = self.execute([snapshot(room(area_m2=None)), snapshot()])
        self.assertEqual(result["outcome"], "draft_ready")
        self.assertEqual(calls, 1)
        tasks._refresh_geometry_tasks.assert_called_once()
        self.assertEqual(read_json(self.root / supervisor.JOB_FILE)["gaps_fixed"], 1)

    def test_no_progress_stops_after_one_cycle_and_retains_exception(self):
        value = snapshot(room(area_m2=None))
        result, calls = self.execute([value, value])
        saved = read_json(self.root / supervisor.JOB_FILE)
        self.assertEqual((calls, saved["cycle"], saved["stop_reason"]), (1, 1, "no_progress"))
        self.assertEqual(result["exceptions"][0]["attempts"], 1)
        self.assertTrue(result["exceptions"][0]["tried"])

    def test_each_gap_gets_at_most_two_attempts_even_if_other_inputs_improve(self):
        values = [snapshot(room(area_m2=None), model_inputs=[number]) for number in range(3)]
        result, calls = self.execute(values)
        self.assertEqual(calls, 2)
        self.assertEqual(result["exceptions"][0]["attempts"], 2)
        self.assertEqual(read_json(self.root / supervisor.JOB_FILE)["stop_reason"], "attempt_limit")

    def test_replacement_run_restores_attempts_from_durable_state(self):
        value = snapshot(room(area_m2=None))
        self.execute([value, value])
        # BackgroundJob creates a replacement file with the original start fields.
        write_json(self.root / supervisor.JOB_FILE, {"status": "running"})
        result, calls = self.execute([value, value])
        self.assertEqual(calls, 1)
        self.assertEqual(result["exceptions"][0]["attempts"], 2)

    def test_three_cycles_backstop_when_new_material_gaps_keep_appearing(self):
        values = [snapshot(room(str(number), area_m2=None)) for number in range(4)]
        _, calls = self.execute(values)
        saved = read_json(self.root / supervisor.JOB_FILE)
        self.assertEqual((calls, saved["cycle"], saved["stop_reason"]), (3, 3, "cycle_limit"))

    def test_all_material_gaps_are_queued_in_first_cycle_not_only_first_eight(self):
        values = snapshot(*[room(f"Space {number}", traces=[{"roof": "unknown", "edges": []}]) for number in range(12)])
        with patch.dict("os.environ", {"ARCHIE_AI_PARALLEL_CALLS": "3"}):
            _, calls = self.execute([values, values])
        self.assertEqual(calls, 12)
        self.assertEqual(read_json(self.root / supervisor.JOB_FILE)["cycle"], 1)

    def test_concurrency_setting_controls_simultaneous_provider_calls(self):
        values = snapshot(*[room(f"Space {number}", traces=[{"roof": "unknown", "edges": []}]) for number in range(9)])
        lock = threading.Lock()
        active = peak = 0
        def request(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1; peak = max(peak, active)
            time.sleep(.025)
            with lock:
                active -= 1
            return {}
        with patch.dict("os.environ", {"ARCHIE_AI_PARALLEL_CALLS": "3"}), \
                patch.object(supervisor, "_rebuild", side_effect=[values, values]), \
                patch.object(tasks, "prepare_investigation", side_effect=self.fake_prepare), \
                patch.object(drawings, "request_investigation", side_effect=request), \
                patch.object(drawings, "apply_investigation", return_value={"status": "applied"}):
            supervisor.run(None, self.project)
        self.assertEqual(peak, 3)

    def test_no_supported_cooled_scope_finishes_as_needs_information(self):
        with patch.object(supervisor, "_rebuild", return_value=snapshot(room("Freezer", scope="refrigeration_process"))):
            result = supervisor.run(None, self.project)
        self.assertEqual(result["outcome"], "needs_information")
        self.assertTrue(result["exceptions"])

    def test_a_batch_that_applied_nothing_skips_the_rebuild(self):
        rebuilds = []
        def rebuild(web, project):
            rebuilds.append(1)
            return snapshot(room(area_m2=None), room("Bar"))
        with patch.object(supervisor, "_rebuild", side_effect=rebuild), \
                patch.object(tasks, "prepare_investigation", side_effect=self.fake_prepare), \
                patch.object(drawings, "request_investigation", return_value={"reply": {}}), \
                patch.object(drawings, "apply_investigation", return_value={"status": "rejected", "reason": "not valid"}):
            result = supervisor.run(None, self.project)
        self.assertEqual(len(rebuilds), 1)                      # only the first look; rejected replies change nothing
        self.assertEqual(result["outcome"], "partial_draft")

    def test_without_ai_it_still_finishes_listing_the_gaps_so_phase1_is_never_held_up(self):
        # 2026-10-21: the check refused to start without the old per-job opt-in, which current jobs don't have, and
        # Phase 1 waits for it, so no job got an empty-room load.
        with patch.object(supervisor, "ai_unavailable", return_value="The AI isn't set up on this computer, so these weren't re-checked."), \
                patch.object(supervisor, "_rebuild", return_value=snapshot(room(area_source="room_size_estimated"))), \
                patch.object(drawings, "request_investigation") as ask:
            result = supervisor.run(None, self.project)
        ask.assert_not_called()
        self.assertEqual(result["outcome"], "partial_draft")
        self.assertIn("weren't re-checked", result["exceptions"][0]["message"])
        self.assertEqual(read_json(self.root / supervisor.JOB_FILE)["stop_reason"], "ai_unavailable")



class ConsentTests(unittest.TestCase):
    def test_page_reading_jobs_may_ask_and_start_needs_no_opt_in_or_key(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        project = {"id": "consent", "review_dir": temp.name, "reasoning_packet": "ready"}
        with patch.object(supervisor.ai_provider, "configured", return_value=True), \
                patch("backend.page_inventory_service.enabled", return_value=True):
            self.assertEqual(supervisor.ai_unavailable(project), "")          # no vision_extraction_settings.json
        with patch("backend.page_inventory_service.enabled", return_value=False):
            self.assertIn("AI drawing analysis is off", supervisor.ai_unavailable(project))
        with patch.object(supervisor.ai_provider, "configured", return_value=False), \
                patch("backend.page_inventory_service.enabled", return_value=True):
            self.assertIn("isn't set up", supervisor.ai_unavailable(project))
        with patch.object(supervisor._JOB, "start", return_value={"status": "queued"}) as start, \
                patch("backend.job_progress.stage_blocker", return_value=""), \
                patch.object(supervisor.ai_provider, "configured", return_value=False):
            supervisor.start(None, project)
        start.assert_called_once()


class RecoveryTests(unittest.TestCase):
    def test_restart_deduplicates_waits_for_old_call_and_keeps_durable_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = {"id": "restart", "review_dir": folder, "reasoning_packet": "ready"}
            write_json(root / "vision_extraction_settings.json", {"owner_opt_in": True})
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            invocations = []
            def fake_run(web, current, step):
                invocations.append(True)
                if len(invocations) == 1:
                    job = read_json(root / supervisor.JOB_FILE)
                    write_json(root / "phase1_supervisor_state.json", {"supervision_id": job["supervision_id"],
                        "attempts": {"gap": 1}, "history": [], "cycle": 1})
                    entered.set(); release.wait(2)
                    checkpoint()
                    self.fail("The old worker must not publish after cancellation")
                state = read_json(root / "phase1_supervisor_state.json")
                self.assertEqual(state["attempts"], {"gap": 1})
                finished.set()
                return {"exceptions": [], "outcome": "draft_ready"}
            with patch.object(supervisor, "run", side_effect=fake_run), \
                    patch("backend.job_progress.stage_blocker", return_value=""), \
                    patch.object(supervisor.ai_provider, "configured", return_value=True):
                first = supervisor.start(None, project)
                self.assertTrue(entered.wait(2))
                for _ in range(3):
                    state = supervisor.start(None, project, action="restart", expected_run_id=first["job_id"])
                    self.assertEqual(state["step_state"], "stopping")
                self.assertEqual(len(invocations), 1)
                release.set()
                self.assertTrue(finished.wait(3))
                until = time.monotonic() + 3
                while supervisor._JOB.key(project) in supervisor._JOB.running and time.monotonic() < until:
                    time.sleep(.01)
            self.assertEqual(len(invocations), 2)
            self.assertEqual(supervisor.status(None, project)["status"], "done")

    def test_orphan_and_stale_runs_offer_recovery_without_mutating_saved_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "orphan", "review_dir": folder}
            root = Path(folder)
            write_json(root / supervisor.JOB_FILE, {"status": "running", "pid": -1, "job_id": "old"})
            self.assertEqual(supervisor.status(None, project)["status"], "interrupted")
            self.assertEqual(read_json(root / supervisor.JOB_FILE)["status"], "running")
            write_json(root / supervisor.JOB_FILE, {"status": "done", "source_fingerprint": "old"})
            self.assertEqual(supervisor.status(None, project)["recovery"]["label"], "Try again")
            self.assertFalse(supervisor.status(None, project)["current"])



if __name__ == "__main__":
    unittest.main()
