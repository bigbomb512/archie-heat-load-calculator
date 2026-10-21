"""Runtime skill catalog, dependency and project-run lifecycle tests."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import skill_workflow_service as skills
from backend import vision_extraction_service


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name


def empty_typed_proposal(subskill):
    def empty(descriptor):
        if descriptor.startswith("array"):
            return []
        if descriptor.startswith("object"):
            return {}
        if "null" in descriptor.split("|"):
            return None
        if descriptor in {"string", "timestamp"}:
            return ""
        if descriptor in {"int", "number"}:
            return 0
        if descriptor == "boolean":
            return False
        return None
    return {key: empty(descriptor) for key, descriptor in subskill["proposal_fields"].items()}


class SkillWorkflowTests(unittest.TestCase):
    def project(self, root):
        root = Path(root)
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 1, "title": "Proposed Floor Plan", "structured_content": {"markdown": "Proposed Floor Plan"}}]}}), encoding="utf-8")
        (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": 1, "drawing_number": "201", "title": "Proposed Floor Plan", "proposed_role": "main_floor_plan", "identity_status": "confirmed"}]}), encoding="utf-8")
        (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True, "selected_group_ids": []}), encoding="utf-8")
        return {"id": "skills-test-" + root.name, "review_dir": str(root)}

    def test_catalog_contracts_dependencies_and_full_enablement(self):
        catalog = skills.load_catalog()
        self.assertEqual(len(catalog["enabled_skill_ids"]), 10)
        self.assertTrue(all(row["enabled"] for row in catalog["skills"]))
        self.assertTrue(skills.validate_catalog(catalog))
        registry = skills.load_subskill_registry()
        self.assertEqual(len(registry["subskills"]), 45)
        self.assertTrue(skills.validate_subskill_registry(catalog, registry))
        self.assertTrue(all(row.get("task") and row.get("proposal_fields") and row.get("constraints") for row in registry["subskills"]))
        with self.assertRaisesRegex(ValueError, "cycle"):
            skills.validate_catalog({**catalog, "output_contracts": {"a": {"type": "object", "required": []}}, "skills": [
                {"id": "a", "version": 1, "purpose": "a", "depends_on": ["b"], "subskills": [], "output_contract": "a", "resolver_handoff": "a"},
                {"id": "b", "version": 1, "purpose": "b", "depends_on": ["a"], "subskills": [], "output_contract": "a", "resolver_handoff": "b"},
            ], "enabled_skill_ids": ["a"], "pilot_skill_ids": ["a"]})

    def test_selected_plan_geometry_scope_includes_real_dimension_setout_sheet(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 1, "title": "Layout Plan", "structured_content": {"markdown": "Layout Plan"}},
                {"page": 2, "title": "Dimensioned Top View Plan", "structured_content": {"markdown": "Dimensions Plan"}},
            ]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 1, "proposed_role": "main_floor_plan", "geometry_eligible": True},
            ]}), encoding="utf-8")
            (root / "vision_extraction_settings.json").write_text(json.dumps({
                "owner_opt_in": True, "selected_group_ids": ["plan_geometry"]}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": {
                "2": {"page_type": "floor_plan", "floor_plan_kind": "dimension_setout"},
                "3": {"page_type": "schedule", "floor_plan_kind": "dimension_setout"},
            }}), encoding="utf-8")

            allowed = skills._consented_page_ids(skills._project_paths(project))

        self.assertEqual(allowed, {1, 2})

    def test_pdf_review_scope_is_the_needs_list_and_what_it_depends_on_but_not_final_policy(self):
        registry = skills.load_subskill_registry()
        enabled = set(skills.load_catalog()["enabled_skill_ids"])
        selected = skills._scoped_subskill_ids(registry, enabled, "pdf_review")
        self.assertTrue({"information_needs", "room_boundaries_areas", "ceiling_height_volume", "surface_inventory",
                         "glazing_properties", "outside_air", "process_exhaust", "sheet_identity", "address_confirmation"}
                        .issubset(selected))
        # The weather comes from the Project tab's weather location; the weather-source step only waited for a
        # map-confirmed site and left the review "blocked".
        self.assertNotIn("weather_source_matching", selected)
        # Nothing reads these skills' results, so the default review doesn't pay for them.
        for unused in ("airflow_deduplication", "infiltration", "make_up_air", "surface_area", "boundary_resolution", "shading",
                       "solar_source", "system_detection", "zone_ownership", "plant_detection", "coil_duty", "component_inputs"):
            self.assertNotIn(unused, selected)
        by_id = {row["id"]: row for row in registry["subskills"]}
        for skill_id in selected:                                  # closed over prerequisites
            self.assertLessEqual(set(by_id[skill_id].get("depends_on", [])) - {"policy_source"}, selected, skill_id)
        self.assertNotIn("policy_source", selected)
        self.assertNotIn("report_readiness", selected)
        for retired in ("all", "full_review", "rooms_only"):          # only the two scopes a job runs are accepted
            with self.assertRaisesRegex(ValueError, "one of: pdf_review, phase1_envelope"):
                skills._scoped_subskill_ids(registry, enabled, retired)

    def test_review_requires_consent_and_reports_missing_provider_as_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            project, web = self.project(folder), Web()
            settings_path = Path(folder) / "vision_extraction_settings.json"
            settings_path.write_text(json.dumps({"owner_opt_in": False, "selected_group_ids": []}), encoding="utf-8")
            no_consent = skills.post(web, project, {"action": "start"})
            self.assertEqual(no_consent["status"], "blocked")
            self.assertIn("prepare the drawing set", no_consent["blocked_reason"].lower())
            self.assertEqual(no_consent["recovery"]["label"], "Check and resume")
            self.assertFalse((Path(folder) / "skill_workflow_run.json").exists())
            settings_path.write_text(json.dumps({"owner_opt_in": True, "selected_group_ids": []}), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                blocked = skills.post(web, project, {"action": "start"})
            manifest = json.loads((Path(folder) / "skill_workflow_run.json").read_text(encoding="utf-8"))
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(manifest["error_code"], "skill_provider_unavailable")
            self.assertIn("credentials", blocked["blocked_reason"])

    def test_findings_derive_evidence_labels_and_keep_missing_values_editable(self):
        with tempfile.TemporaryDirectory() as folder:
            project, web = self.project(folder), Web()
            root = Path(folder)
            catalog = skills.load_catalog()
            source_fp = skills._source_fingerprint(skills._project_paths(project), catalog)
            manifest = skills._new_manifest(catalog, source_fp, "pdf_review")
            manifest.update({"run_id": "evidence-run", "status": "needs_review"})
            manifest["subskills"]["room_identity_use"].update({"status": "needs_review", "evidence_reviewed": {
                "pages": [1], "provider": "test-provider", "model": "test-model", "started_at": 10, "finished_at": 11, "attempt_ref": "attempts/a"}})
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest))
            proposal_dir = root / "skill_workflow_runs" / "evidence-run" / "proposals"
            proposal_dir.mkdir(parents=True)
            (proposal_dir / "room_identity_use.json").write_text(json.dumps({
                "subskill_id": "room_identity_use", "status": "needs_review",
                "proposal_fields": {"rooms": [
                    {"room_id": "supported", "taxonomy_id": "shop", "page": 1, "excerpt": "SHOP"},
                    {"room_id": "inferred", "taxonomy_id": "kitchen", "page": 1, "formula": "fixture count × load"},
                    None,
                    {"room_id": "conflict", "taxonomy_id": "shop", "page": 1, "alternatives": ["shop", "kitchen"]},
                ]},
                "inferences": [{"field": "rooms", "target": "inferred", "value": "kitchen", "method": "fixture count × load"}],
                "citations": [{"physical_pdf_page": 1, "excerpt_or_crop": "Shop / Kitchen"}],
                "alternatives": [], "unresolved_fields": [{"field": "rooms[2]", "reason": "Not shown"}],
            }))
            response = skills.get(web, project)
            rows = response["findings"]
            by_target = {row["target"]: row["evidence"] for row in rows if row.get("target")}
            self.assertEqual(by_target, {"supported": "supported", "inferred": "inferred", "conflict": "conflicting"})
            self.assertTrue(any(row["evidence"] == "missing" for row in rows))
            self.assertEqual(response["status"], "needs_review")
            identity = next(row for row in response["subskills"] if row["id"] == "room_identity_use")
            self.assertEqual(identity["evidence_reviewed"]["pages"], [1])
            self.assertEqual(identity["evidence_reviewed"]["provider"], "test-provider")
            self.assertEqual(identity["evidence_reviewed"]["model"], "test-model")
            self.assertEqual(identity["evidence_reviewed"]["started_at"], 10)
            self.assertEqual(identity["evidence_reviewed"]["finished_at"], 11)
            self.assertEqual(identity["evidence_reviewed"]["attempt_ref"], "attempts/a")
            missing = next(row for row in rows if row["evidence"] == "missing")
            with self.assertRaisesRegex(ValueError, "Enter a value"):
                skills.post(web, project, {"action": "review_finding", "finding_id": missing["id"],
                    "decision": "accepted"})

    def test_supported_and_inferred_labels_are_derived_from_evidence_and_method(self):
        self.assertEqual(skills._finding_evidence({"value": 2}, "area", [1], [], "", "", []), "supported")
        self.assertEqual(skills._finding_evidence({"value": 2}, "area", [1], [{"method": "scale"}], "", "", []), "inferred")
        self.assertEqual(skills._finding_evidence({"value": 2}, "area", [], [], "", "", []), "missing")
        self.assertEqual(skills._finding_evidence(None, "area", [1], [], "", "", []), "missing")
        self.assertEqual(skills._finding_evidence({"value": 2, "printed_value": 3}, "area", [1], [], "", "", []), "conflicting")
        self.assertEqual(skills._finding_evidence({"taxonomy_id": "shop", "printed_value": "kitchen"}, "rooms", [1], [], "", "", []), "conflicting")
        rows = [{"field": "area", "target": "shop", "value": 10, "evidence": "supported"},
                {"field": "area", "target": "shop", "value": 12, "evidence": "supported"}]
        skills._mark_conflicting_findings(rows)
        self.assertEqual([row["evidence"] for row in rows], ["conflicting", "conflicting"])
        # Rows of one list field without a room/surface/page aren't two readings of one value.
        plain = [{"field": "notes", "target": "", "value": "a", "evidence": "supported"},
                 {"field": "notes", "target": "", "value": "b", "evidence": "supported"}]
        skills._mark_conflicting_findings(plain)
        self.assertEqual([row["evidence"] for row in plain], ["supported", "supported"])

    def test_the_source_fingerprint_is_reused_until_an_input_file_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            paths, catalog = skills._project_paths(project), skills.load_catalog()
            first = skills._source_fingerprint(paths, catalog)
            with patch.object(skills, "_source_inputs", side_effect=AssertionError("re-read")):
                self.assertEqual(skills._source_fingerprint(paths, catalog), first)
            (Path(folder) / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True, "selected_group_ids": ["plans"]}))
            self.assertNotEqual(skills._source_fingerprint(paths, catalog), first)
            settings_changed = skills._source_fingerprint(paths, catalog)
            (Path(folder) / "spatial_ocr.json").write_text(json.dumps({"pages": [1]}))
            self.assertNotEqual(skills._source_fingerprint(paths, catalog), settings_changed)

    def test_missing_findings_do_not_block_workflow_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(folder)
            paths = skills._project_paths(project)
            fp = skills._source_fingerprint(paths, skills.load_catalog())
            manifest = {"run_id": "r1", "source_fingerprint": fp}
            proposals = root / "skill_workflow_runs" / "r1" / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "ceiling_height_volume.json").write_text(json.dumps({"subskill_id": "ceiling_height_volume",
                "status": "needs_review", "proposal_fields": {"heights": []}, "unresolved_fields": []}))
            self.assertFalse(skills._has_open_findings(paths, manifest))

    def test_withdrawn_consent_makes_findings_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            project, web = self.project(folder), Web()
            (Path(folder) / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": False}))
            result = skills.get(web, project)
            self.assertTrue(result["read_only"])
            self.assertEqual(result["status"], "blocked")
            with self.assertRaisesRegex(ValueError, "findings are read-only"):
                skills.post(web, project, {"action": "review_finding", "finding_id": "missing", "decision": "accepted"})

    def test_after_analysis_auto_starts_only_with_saved_consent_and_uses_pdf_review_scope(self):
        original_worker = skills._run_worker
        try:
            skills._run_worker = lambda *_args: None
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                settings = Path(folder) / "vision_extraction_settings.json"
                settings.write_text(json.dumps({"owner_opt_in": False}), encoding="utf-8")
                waiting = skills.start_after_analysis(web, project)
                self.assertEqual(waiting["status"], "blocked")
                self.assertFalse((Path(folder) / "skill_workflow_run.json").exists())
                settings.write_text(json.dumps({"owner_opt_in": True}), encoding="utf-8")
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    started = skills.start_after_analysis(web, project)
                self.assertIn(started["status"], {"queued", "running"})
                manifest = json.loads((Path(folder) / "skill_workflow_run.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["scope"], "pdf_review")
        finally:
            skills._run_worker = original_worker

    def test_saving_job_consent_automatically_starts_pdf_review(self):
        original_worker = skills._run_worker
        try:
            skills._run_worker = lambda *_args: None
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                settings_path = Path(folder) / "vision_extraction_settings.json"
                settings_path.write_text(json.dumps({"owner_opt_in": False, "selected_group_ids": []}), encoding="utf-8")
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    response = vision_extraction_service.post(web, project, {"action": "save_settings",
                        "settings": {"owner_opt_in": True, "selected_group_ids": []}})
                self.assertTrue(response["settings"]["owner_opt_in"])
                self.assertEqual(response["skill_workflow"]["status"], "queued")
                manifest = json.loads(settings_path.with_name("skill_workflow_run.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["scope"], "pdf_review")
        finally:
            skills._run_worker = original_worker

    def test_current_ai_geometry_proposal_enters_preliminary_calculation_before_manual_review(self):
        from backend.calculation_extraction_service import _room_geometry_skill_proposals

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            run_id = "review-run"
            manifest = skills._new_manifest(skills.load_catalog(), skills._source_fingerprint(skills._project_paths(project), skills.load_catalog()), "pdf_review")
            manifest.update({"run_id": run_id, "status": "needs_review"})
            manifest["subskills"]["room_boundaries_areas"]["status"] = "needs_review"
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest), encoding="utf-8")
            proposal_dir = root / "skill_workflow_runs" / run_id / "proposals"
            proposal_dir.mkdir(parents=True)
            candidate = {"room_id": "room-1", "label": "Shop", "page": 1, "boundary_points_mm": [[0, 0], [4000, 0], [4000, 3000], [0, 3000], [0, 0]],
                         "area_m2": 12, "formula": "4 m × 3 m = 12 m²", "source_pages": [1]}
            (proposal_dir / "room_boundaries_areas.json").write_text(json.dumps({"subskill_id": "room_boundaries_areas",
                "proposal_fields": {"geometry_candidates": [candidate]}, "citations": [{"page": 1, "excerpt": "Shop"}]}), encoding="utf-8")
            before = _room_geometry_skill_proposals(root)
            self.assertEqual(len(before), 1)
            self.assertEqual(before[0]["geometry"]["area_m2"], 12)
            self.assertEqual(before[0]["geometry"]["formula"], "4 m × 3 m = 12 m²")
            with patch("backend.calculation_extraction_service.post", return_value={}) as build:
                result = skills.post(Web(), project, {"action": "review_finding", "finding_id": "room_boundaries_areas:geometry_candidates:0",
                    "decision": "accepted", "value": {**candidate, "area_m2": 13}, "reviewer": "Operator"})
            stored = skills._read(root / "skill_review_decisions.json", {})
            self.assertEqual(stored["decisions"]["room_boundaries_areas:geometry_candidates:0"]["value"]["area_m2"], 13)
            self.assertEqual(result["findings"][0]["status"], "accepted")
            self.assertTrue(result["findings"][0]["input_applied"])
            build.assert_called_once()
            accepted = _room_geometry_skill_proposals(root)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]["geometry"]["area_m2"], 13)

    def test_accepting_a_seat_count_sets_the_rooms_people_once_a_room_is_chosen(self):
        from backend import finding_inputs_service, need_answers_service

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project, web = self.project(root), Web()
            catalog = skills.load_catalog()
            manifest = skills._new_manifest(catalog, skills._source_fingerprint(skills._project_paths(project), catalog), "pdf_review")
            manifest.update({"run_id": "seats", "status": "needs_review"})
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest))
            proposals = root / "skill_workflow_runs" / "seats" / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "occupancy_seating.json").write_text(json.dumps({"subskill_id": "occupancy_seating", "status": "needs_review",
                "citations": [{"page": 19, "excerpt": "TB1 35 140"}],
                "proposal_fields": {"occupancy": [{"room_id": None, "count": 140, "basis": "seating schedule"}]}}))
            rooms = [{"label": "Shop", "level": "Unassigned level"}]
            with patch.object(finding_inputs_service, "rooms", return_value=rooms), \
                    patch.object(need_answers_service, "options", return_value={"rooms": rooms}), \
                    patch.object(need_answers_service, "_internal_gains_override") as set_people:
                review = skills.get(web, project)
                seats = next(row for row in review["findings"] if row["subskill_id"] == "occupancy_seating" and row["field"] == "occupancy")
                self.assertEqual((seats["sets_input"], seats["room_label"]), (True, ""))       # no room named: the operator picks
                self.assertEqual(review["answer_options"]["rooms"], rooms)
                with self.assertRaisesRegex(ValueError, "Choose the room"):
                    skills.post(web, project, {"action": "review_finding", "finding_id": seats["id"], "decision": "accepted"})
                self.assertFalse((root / "skill_review_decisions.json").exists())               # nothing half-recorded
                result = skills.post(web, project, {"action": "review_finding", "finding_id": seats["id"], "decision": "accepted",
                                                    "room": "Shop", "reviewer": "Sam"})
            set_people.assert_called_once()
            self.assertEqual(set_people.call_args.args[3:5], ("occupancy_count", 140.0))
            accepted = next(row for row in result["findings"] if row["id"] == seats["id"])
            self.assertEqual((accepted["status"], accepted["input_applied"], accepted["applied_summary"], accepted["applied_room"]),
                             ("accepted", True, "Shop: 140 people.", "Shop"))

    def test_a_disagreement_about_one_item_marks_only_that_item(self):
        alternatives = [{"field": "equipment.quantity", "component_id": "pdf-1:004:E29", "candidates": [{"value": 1}, {"value": 3}]},
                        {"field": "general", "detail": "no identifier"}]
        self.assertEqual(skills._row_alternatives(alternatives, {"equipment_id": "pdf-1:004:E29"}, 40), alternatives[:1])
        self.assertEqual(skills._row_alternatives(alternatives, {"equipment_id": "pdf-1:004:E01"}, 40), [])
        self.assertEqual(skills._row_alternatives(alternatives, {"equipment_id": "pdf-1:004:E01"}, 1), alternatives[1:])
        self.assertEqual(skills._row_alternatives(None, {}, 1), [])

    def test_scanned_page_findings_apply_s1_areas_only_after_acceptance(self):
        from backend import autonomous_tasks_service, calculation_extraction_service, room_use_resolution_service

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project, web = self.project(root), Web()
            catalog = skills.load_catalog()
            source_fp = skills._source_fingerprint(skills._project_paths(project), catalog)
            manifest = skills._new_manifest(catalog, source_fp, "pdf_review")
            manifest.update({"run_id": "scan-review", "status": "needs_review"})
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest), encoding="utf-8")
            proposal_dir = root / "skill_workflow_runs" / "scan-review" / "proposals"
            proposal_dir.mkdir(parents=True)
            page_value = {"page": 4, "status": "proposed", "rooms": [{"name": "Shop", "area_m2": 42.0}],
                          "calibration": {"status": "agreed"}}
            (proposal_dir / "scanned_printed_areas.json").write_text(json.dumps({
                "subskill_id": "scanned_printed_areas", "proposal_fields": {"pages": [page_value]},
                "citations": [{"page": 4, "excerpt": "Shop 42 m2"}], "raw_replies": {"4": {"areas": []}}}), encoding="utf-8")
            record = {"task": "S1_printed_areas", "target": "page-4", "packet": {"page": 4}}
            applied = {"applied_value": {"rooms": [{"label": "Shop", "area_m2": 42.0}]}}
            with patch.object(autonomous_tasks_service, "post") as apply_s1, \
                    patch.object(autonomous_tasks_service, "_current_task", side_effect=[record, applied]), \
                    patch.object(room_use_resolution_service, "post") as resolve, \
                    patch.object(calculation_extraction_service, "post") as build:
                result = skills.post(web, project, {"action": "review_finding",
                    "finding_id": "scanned_printed_areas:pages:0", "decision": "accepted"})
            self.assertEqual(result["findings"][0]["status"], "accepted")
            apply_s1.assert_called_once()
            self.assertEqual(apply_s1.call_args.args[2]["task"], "S1_printed_areas")
            self.assertEqual(apply_s1.call_args.args[2]["reply"], json.dumps({"areas": []}))
            resolve.assert_called_once()
            build.assert_called_once_with(web, project, {"action": "build"})

    def test_image_only_plan_uses_s1_scan_reading_and_keeps_areas_as_proposals(self):
        from backend import autonomous_tasks_service

        class Provider:
            def propose(self, _prompt, image_paths):
                self.image_paths = image_paths
                return {"rooms": [{"tile": 1, "name": "Shop", "number": None, "area_value": 42,
                    "unit": "m2", "printed_text": "42 m2", "label_px": [40, 40]}], "scale_text": None}

        provider = Provider()
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            packet = {"page": 1, "tiles": [(0, 0, 100, 100)], "factors": [1], "render_dpi": 180,
                      "working_scale_denominator": 100}
            task = ("S1_printed_areas", "page-1", packet, "Read printed areas", [b"synthetic image"], "")
            with patch.object(skills, "SKILL_PROVIDER_FACTORY", lambda _model: provider), \
                    patch.object(autonomous_tasks_service, "_s1_packets", return_value=[task]), \
                    patch.object(autonomous_tasks_service, "_create_run", return_value={"task": "S1_printed_areas"}), \
                    patch.object(autonomous_tasks_service, "_p0_context", return_value={}), \
                    patch.object(autonomous_tasks_service, "_scan_outlines", return_value=[]):
                proposal = skills._read_scanned_printed_areas(project, "image-only-run")
            self.assertEqual(proposal["subskill_id"], "scanned_printed_areas")
            self.assertEqual(proposal["proposal_fields"]["pages"][0]["rooms"],
                             [{"name": "Shop", "number": None, "area_m2": 42.0, "printed_text": "42 m2", "page": 1}], proposal)
            self.assertEqual(proposal["proposal_fields"]["pages"][0]["calibration"], None)
            self.assertEqual(len(provider.image_paths), 1)
            self.assertTrue((Path(folder) / "skill_workflow_runs" / "image-only-run" / "proposals" /
                             "scanned_printed_areas.json").exists())
            self.assertFalse((Path(folder) / "skill_review_decisions.json").exists())

    def test_scanned_plan_provider_failure_is_failed_not_needs_review(self):
        from backend import autonomous_tasks_service

        class BrokenProvider:
            def propose(self, *_args, **_kwargs):
                raise RuntimeError("provider unavailable")

        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            task = ("S1_printed_areas", "page-1", {"page": 1, "tiles": [], "factors": []}, "Read areas", [b"image"], "")
            with patch.object(skills, "SKILL_PROVIDER_FACTORY", lambda _model: BrokenProvider()), \
                    patch.object(autonomous_tasks_service, "_s1_packets", return_value=[task]), \
                    patch.object(autonomous_tasks_service, "_create_run", return_value={}):
                result = skills._read_scanned_printed_areas(project, "scan-failure")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["proposal_fields"]["pages"][0]["status"], "failed")
            self.assertNotEqual(result["status"], "needs_review")

    def test_scanned_area_edit_is_not_applied_without_s1_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project, web = self.project(root), Web()
            catalog = skills.load_catalog()
            source_fp = skills._source_fingerprint(skills._project_paths(project), catalog)
            manifest = skills._new_manifest(catalog, source_fp, "pdf_review")
            manifest.update({"run_id": "scan-edit", "status": "needs_review"})
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest), encoding="utf-8")
            proposal_dir = root / "skill_workflow_runs" / "scan-edit" / "proposals"
            proposal_dir.mkdir(parents=True)
            page_value = {"page": 4, "status": "proposed", "rooms": [{"name": "Shop", "area_m2": 42.0}]}
            (proposal_dir / "scanned_printed_areas.json").write_text(json.dumps({"subskill_id": "scanned_printed_areas",
                "proposal_fields": {"pages": [page_value]}, "citations": [{"page": 4, "excerpt": "Shop"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "edit individual room areas"):
                skills.post(web, project, {"action": "review_finding", "finding_id": "scanned_printed_areas:pages:0",
                    "decision": "accepted", "value": {**page_value, "rooms": [{"name": "Shop", "area_m2": 43.0}]}})
            self.assertFalse((root / "skill_review_decisions.json").exists())

    def test_room_geometry_skill_proposal_is_adapted_for_existing_resolver(self):
        from backend.calculation_extraction_service import _room_geometry_skill_proposals

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            run_id = "skill-run-test"
            source_fp = skills._source_fingerprint(skills._project_paths(project), skills.load_catalog())
            (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": run_id, "source_fingerprint": source_fp,
                "subskills": {"room_boundaries_areas": {"status": "provisional"}}}), encoding="utf-8")
            proposal_dir = root / "skill_workflow_runs" / run_id / "proposals"
            proposal_dir.mkdir(parents=True)
            candidate = {"room_id": "room-1", "label": "Dining", "page": 3, "level": "Ground",
                "source_pages": [2, 3],
                "boundary_ref": "loop-1", "boundary_points_px": [[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]],
                "wall_ids": ["w1", "w2", "w3", "w4"], "walls": [{"wall_id": "w1"}],
                "dimension_ids": ["d1"], "dimensions": [{"dimension_id": "d1", "value_mm": 6000}],
                "dimension_links": [{"dimension_id": "d1", "target_wall_id": "w1", "value_mm": 6000, "reason": "extension lines"}],
                "scale_mm_per_px": 300, "area_m2": 18, "formula": "polygon × scale²",
                "calibration": {"source": "linked_dimension"}, "source_crop": "crop.png", "independent_witnesses": [], "confidence": 0.92,
                "conflicts": [], "unresolved_fields": [], "alternatives": []}
            (proposal_dir / "room_boundaries_areas.json").write_text(json.dumps({"proposal_fields": {"geometry_candidates": [candidate]},
                "citations": [{"page": 2, "excerpt": "Dining layout"}, {"page": 3, "excerpt": "Dimension plan"}]}), encoding="utf-8")
            adapted = _room_geometry_skill_proposals(root)

        self.assertEqual(len(adapted), 1)  # Phase 1 can use current AI proposals before manual review.
        self.assertEqual(adapted[0]["geometry"]["scale_mm_per_px"], 300)
        self.assertEqual(adapted[0]["geometry"]["dimension_wall_links"][0]["target_wall_id"], "w1")
        self.assertEqual(adapted[0]["source_pages"], [2, 3])
        self.assertEqual({row["page"] for row in adapted[0]["evidence"]}, {2, 3})

    def test_skill_scale_is_recomputed_only_when_printed_scale_matches_page_register(self):
        from backend.calculation_extraction_service import _confirm_skill_main_viewport_scale

        calibration = {"printed_scale": "1:75", "scale_mm_per_px": 999,
                       "pdf_physical_page_width_mm": 1, "canonical_image_width_px": 2}
        confirmed, scale = _confirm_skill_main_viewport_scale(calibration, "1:75", 420, 2977)
        self.assertAlmostEqual(scale, 420 * 75 / 2977)
        self.assertAlmostEqual(confirmed["scale_mm_per_px"], scale)
        self.assertEqual(confirmed["confirmed_main_viewport_scale"], "1:75")
        mismatch, rejected_scale = _confirm_skill_main_viewport_scale(calibration, "1:5", 420, 2977)
        self.assertIsNone(rejected_scale)
        self.assertNotIn("confirmed_main_viewport_scale", mismatch)

    def test_room_geometry_skill_receives_room_profiles_and_dimensioned_plan_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 20}, {"page": 21}, {"page": 22}]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 20, "title": "Dimension Plan", "proposed_role": "main_floor_plan", "geometry_eligible": True, "scale_status": "confirmed"},
                {"page": 21, "title": "Floor Finish Plan", "proposed_role": "supporting_geometry_plan", "geometry_eligible": True},
                {"page": 22, "title": "Reflective Ceiling Plan", "proposed_role": "supporting_geometry_plan", "geometry_eligible": True},
            ], "page_relationships": [{"from_page": 21, "to_page": 20, "basis": ["same tenancy outline"]}]}), encoding="utf-8")
            (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True}), encoding="utf-8")
            (root / "ai_preliminary_run.json").write_text(json.dumps({"local_room_inference_proposal": {"rooms": [
                {"room_id": "room-use:unassigned-level:bar", "label": "Bar", "level_name": "Unassigned level", "source_pages": [21, 22], "evidence": [{"page": 21}, {"page": 22}]},
            ]}}), encoding="utf-8")
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "room-use:unassigned-level:bar", "taxonomy_id": "dining", "preliminary_profile_id": "hospitality", "space_scope": "comfort_hvac", "status": "resolved"},
            ]}), encoding="utf-8")
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
                {"page": 20, "coordinate_systems": {"plan_px": {"plan_viewport_bbox_px": [1, 2, 30, 40]}}, "line_candidates": [
                    {"candidate_id": "P20-W1", "start_px": [1, 1], "end_px": [20, 1], "length_px": 19, "candidate_role_hint": "possible_wall_or_dimension"},
                    *[{"candidate_id": f"P20-C{index}", "start_px": [index, 1], "end_px": [index + 1, 1],
                       "length_px": 1, "candidate_role_hint": "vector_context"} for index in range(399)],
                ], "dimension_candidates": [{"candidate_id": "P20-D1", "value_mm": 19000}]},
            ]}}), encoding="utf-8")
            (root / "dimension_wall_matches.json").write_text(json.dumps({"pages": [
                {"page": 20, "summary": {"dimension_text_count": 1}, "dimension_span_candidates": [{"dimension_candidate_id": "P20-D1", "value_mm": 19000}]},
            ]}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [
                {"page": 20, "scale_candidates": [{"text": "1:100"}],
                 "dimension_candidates": [{"candidate_id": "OCR-D1", "value_mm": 19000}], "room_label_candidates": []},
            ]}), encoding="utf-8")

            subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "room_boundaries_areas")
            evidence, _ = skills._shared_evidence_packet(subskill, project)

        self.assertEqual(evidence["room_candidates"][0]["preliminary_profile_id"], "hospitality")
        self.assertEqual(evidence["room_candidates"][0]["room_id"], "room-use:unassigned-level:bar")
        self.assertEqual(evidence["vector_geometry_pages"][0]["page"], 20)
        self.assertEqual(len(evidence["vector_geometry_pages"][0]["line_candidates"]), 350)
        self.assertEqual(evidence["vector_geometry_pages"][0]["line_candidates"][0][0], "P20-W1")
        self.assertEqual(evidence["dimension_evidence"][0]["dimension_span_candidates"][0]["value_mm"], 19000)
        self.assertEqual(evidence["spatial_room_evidence"][0]["scale_candidates"][0]["text"], "1:100")
        self.assertNotIn("dimension_candidates", evidence["spatial_room_evidence"][0])

    def test_preliminary_proposal_joins_room_use_profile_and_geometry_candidate(self):
        from backend import ai_preliminary_service

        with tempfile.TemporaryDirectory() as folder:
            paths = {"root": Path(folder)}
            raw = {"rooms": [{"kind": "room", "room_id": "room-use:ground:bar", "label": "Bar",
                "level_name": "Ground", "source_pages": [21, 22], "evidence": [{"page": 21, "excerpt": "Bar"}]}]}
            room_use = {"records": [{"room_id": "room-use:ground:bar", "taxonomy_id": "dining",
                "preliminary_profile_id": "hospitality", "space_scope": "comfort_hvac", "status": "resolved",
                "evidence": [{"page": 22, "excerpt": "Bar area"}]}]}
            candidate = {"room_id": "room-use:ground:bar", "label": "Bar", "level_name": "Ground", "page": 20,
                "geometry": {"boundary_points_px": [[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]],
                    "scale_mm_per_px": 100, "dimension_wall_links": [{"dimension_id": "d-1", "target_wall_id": "w-1"}]},
                "evidence": [{"page": 20, "excerpt": "Dimension plan"}]}
            with patch("backend.calculation_extraction_service._room_geometry_skill_proposals", return_value=[candidate]):
                result = ai_preliminary_service._prepare_preliminary_proposal(paths, raw, room_use, {})

        room = result["rooms"][0]
        self.assertEqual(room["preliminary_profile_id"], "hospitality")
        self.assertEqual(room["space_scope"], "comfort_hvac")
        self.assertEqual(room["room_use_category"], "dining")
        self.assertEqual(room["page"], 20)
        self.assertEqual(room["geometry"]["dimension_wall_links"][0]["dimension_id"], "d-1")
        self.assertEqual({row["page"] for row in room["evidence"]}, {20, 21, 22})

    def test_run_executes_subskills_reuses_and_marks_stale(self):
        # These check the runner itself: no scanned plans to read in the PDF review.
        self.enterContext(patch.object(skills, "_read_scanned_printed_areas", return_value=None))
        original_execute = skills._execute_subskill
        original_prepare = skills._ensure_room_evidence
        try:
            def prepare(_web, _project):
                return {"candidate_count": 2, "artifact_names": ["room_inference_job.json", "geometry_resolution.json"]}

            def execute(subskill, _project, _dependencies, source_fp):
                return {
                    "subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review" if subskill["parent"] == "rooms_geometry_gains" else "not_applicable",
                    "affected_ids": [subskill["id"]] if subskill["parent"] == "rooms_geometry_gains" else [], "observations": [], "inferences": [], "citations": [], "confidence": None,
                    "alternatives": [], "unresolved_fields": [], "remediation": ["Review evidence."],
                    "input_fingerprint": source_fp, "proposal_fields": empty_typed_proposal(subskill),
                    "artifact_names": ["drawing_coverage.json"] if subskill["parent"] == "document_mapping" else ["geometry_resolution.json"],
                }

            skills._execute_subskill = execute
            skills._ensure_room_evidence = prepare
            with tempfile.TemporaryDirectory() as folder:
                project = self.project(folder)
                web = Web()
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    started = skills.post(web, project, {"action": "start", "scope": "pdf_review"})
                self.assertIn(started["status"], {"queued", "running", "needs_review"})
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = skills.get(web, project)
                    if state["status"] in {"needs_review", "completed", "failed", "blocked"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(state["status"], "completed")
                self.assertEqual(len(state["stages"]), 10)
                self.assertEqual(state["stages"][0]["label"], "Drawing set and page mapping")
                self.assertEqual(state["stages"][2]["label"], "Rooms, geometry, and gains")
                self.assertTrue(all("id" not in row for row in state["stages"]))
                manifest = json.loads((Path(folder) / "skill_workflow_run.json").read_text())
                self.assertEqual(len(manifest["subskills"]), 45)
                self.assertTrue(all(manifest["subskills"][key]["status"] != "not_enabled" for key in (
                    "sheet_identity", "revision_scope", "page_relationships", "room_identity_use", "room_boundaries_areas",
                    "ceiling_height_volume", "occupancy_seating", "lighting_evidence", "equipment_evidence", "schedule_evidence")))
                self.assertEqual(manifest["preparation"]["status"], "completed")
                self.assertNotIn(folder, json.dumps(state))
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    repeated = skills.post(web, project, {"action": "start", "scope": "pdf_review"})
                self.assertEqual(repeated["run_id"], state["run_id"])
                (Path(folder) / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 2}]}), encoding="utf-8")
                self.assertEqual(skills.get(web, project)["status"], "stale")
        finally:
            skills._execute_subskill = original_execute
            skills._ensure_room_evidence = original_prepare

    def test_failed_subskill_blocks_only_dependents(self):
        # These check the runner itself: no scanned plans to read in the PDF review.
        self.enterContext(patch.object(skills, "_read_scanned_printed_areas", return_value=None))
        original_execute = skills._execute_subskill
        original_prepare = skills._ensure_room_evidence
        try:
            skills._ensure_room_evidence = lambda *_args: {"candidate_count": 1, "artifact_names": ["room_inference_job.json"]}

            def execute(subskill, _project, _dependencies, source_fp):
                if subskill["id"] == "room_identity_use":
                    raise RuntimeError("source_ambiguous")
                return {
                    "subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "resolved" if subskill["id"] == "sheet_identity" else "not_applicable",
                    "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
                    "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": source_fp,
                    "proposal_fields": empty_typed_proposal(subskill), "artifact_names": ["drawing_coverage.json"],
                }

            skills._execute_subskill = execute
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    skills.post(web, project, {"action": "start", "scope": "pdf_review"})
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = skills.get(web, project)
                    if state["status"] in {"failed", "blocked", "needs_review", "completed"}:
                        break
                    time.sleep(0.01)
                manifest = json.loads((Path(folder) / "skill_workflow_run.json").read_text())
                self.assertEqual(manifest["subskills"]["room_identity_use"]["status"], "failed")
                self.assertEqual(manifest["subskills"]["room_boundaries_areas"]["status"], "blocked")
                self.assertEqual(manifest["subskills"]["sheet_identity"]["status"], "resolved")
                self.assertEqual(manifest["status"], "failed")
                self.assertFalse((Path(folder) / "skill_workflow_runs" / manifest["run_id"] / "proposals" /
                                  "room_identity_use.json").exists())
        finally:
            skills._execute_subskill = original_execute
            skills._ensure_room_evidence = original_prepare

    def test_independent_pilot_subskills_run_concurrently(self):
        # These check the runner itself: no scanned plans to read in the PDF review.
        self.enterContext(patch.object(skills, "_read_scanned_printed_areas", return_value=None))
        original_execute = skills._execute_subskill
        original_prepare = skills._ensure_room_evidence
        active = 0
        max_active = 0
        guard = threading.Lock()
        observed_dependencies = {}
        try:
            skills._ensure_room_evidence = lambda *_args: {"candidate_count": 1, "artifact_names": ["room_inference_job.json"]}

            def execute(subskill, _project, dependencies, source_fp):
                nonlocal active, max_active
                observed_dependencies[subskill["id"]] = {key: value.get("status") for key, value in dependencies.items()}
                parallel_group = {"room_boundaries_areas", "occupancy_seating", "lighting_evidence", "equipment_evidence"}
                if subskill["id"] in parallel_group:
                    with guard:
                        active += 1
                        max_active = max(max_active, active)
                    time.sleep(0.08)
                    with guard:
                        active -= 1
                return {
                    "subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "resolved" if subskill["parent"] in {"document_mapping", "rooms_geometry_gains"} else "not_applicable",
                    "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
                    "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": source_fp,
                    "proposal_fields": empty_typed_proposal(subskill), "artifact_names": ["drawing_coverage.json"],
                }

            skills._execute_subskill = execute
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    skills.post(web, project, {"action": "start", "scope": "pdf_review"})
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = skills.get(web, project)
                    if state["status"] in {"failed", "blocked", "needs_review", "completed"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(state["status"], "completed")
                self.assertGreaterEqual(max_active, 2)
                self.assertEqual(observed_dependencies["room_boundaries_areas"].get("room_identity_use"), "resolved")
                self.assertEqual(observed_dependencies["schedule_evidence"].get("lighting_evidence"), "resolved")
        finally:
            skills._execute_subskill = original_execute
            skills._ensure_room_evidence = original_prepare

    def test_start_requires_analysis_and_retry_requires_failed_or_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"id": "skills-empty", "review_dir": folder}
            with self.assertRaisesRegex(ValueError, "Analyse the PDF"):
                skills.post(Web(), project, {"action": "start"})

    def test_retry_recovers_persisted_running_manifest_without_live_worker(self):
        original_worker = skills._run_worker
        try:
            skills._run_worker = lambda *_args: None
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                paths = skills._project_paths(project)
                catalog = skills.load_catalog()
                old = skills._new_manifest(catalog, skills._source_fingerprint(paths, catalog), "pdf_review")
                old.update({"status": "running", "run_id": "orphaned-run"})
                skills._write_manifest(paths["manifest"], old)

                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    response = skills.post(web, project, {"action": "retry"})

                self.assertNotEqual(response["run_id"], "orphaned-run")
                self.assertFalse(response["deduplicated"])
                self.assertEqual(skills._read(paths["manifest"], {}).get("run_id"), response["run_id"])
        finally:
            skills._run_worker = original_worker

    def test_a_run_left_running_by_a_stopped_server_shows_as_stopped_and_retry_repeats_its_scope(self):
        original_worker = skills._run_worker
        try:
            skills._run_worker = lambda *_args: None
            with tempfile.TemporaryDirectory() as folder:
                project, web = self.project(folder), Web()
                paths = skills._project_paths(project)
                catalog = skills.load_catalog()
                old = skills._new_manifest(catalog, skills._source_fingerprint(paths, catalog), "phase1_envelope")
                old.update({"status": "running", "run_id": "orphaned-run", "pid": 999999999})   # no such process
                skills._write_manifest(paths["manifest"], old)

                shown = skills.get(web, project)
                self.assertEqual((shown["status"], shown["scope"]), ("failed", "phase1_envelope"))
                self.assertIn("server stopped", shown["problem"])
                self.assertEqual(skills._read(paths["manifest"], {})["status"], "running")      # reading changes nothing

                skills._write_manifest(paths["manifest"], {**old, "pid": os.getppid()})        # another live server owns it
                owned = skills.get(web, project)
                self.assertEqual(owned["status"], "running")
                self.assertNotIn("problem", owned)
                self.assertTrue(skills.post(web, project, {"action": "retry"})["deduplicated"])

                skills._write_manifest(paths["manifest"], old)
                with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
                    response = skills.post(web, project, {"action": "retry"})
                self.assertNotEqual(response["run_id"], "orphaned-run")
                self.assertEqual(response["scope"], "phase1_envelope")                       # not the default pdf_review
                self.assertEqual(skills._read(paths["manifest"], {})["pid"], os.getpid())
                skills._RUNNING.discard(project["id"])
        finally:
            skills._run_worker = original_worker

    def test_subskill_prompt_composes_shared_parent_and_task_instructions(self):
        catalog = skills.load_catalog()
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "room_boundaries_areas")
        from ai.skill_registry import compose_subskill_instructions
        prompt = compose_subskill_instructions(task["parent"], task)
        self.assertIn("You are an evidence interpreter", prompt)
        self.assertIn("Return geometry proofs", prompt)
        self.assertIn("Do not use unrelated dimensions", prompt)

    def test_codex_image_selection_accepts_per_domain_relevance_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "thumbnails" / "page_001.png"
            image.parent.mkdir()
            image.write_bytes(b"test image")
            manifest = root / "vision_extraction_runs" / "test" / "request_manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"groups": [{"pages": [{
                "page": 1, "title": "Dimension Plan", "role": "main_floor_plan",
                "relevance": {"room_geometry": 0.85, "openings_windows": 0.0},
                "image_path": str(image),
            }]}]}), encoding="utf-8")
            (root / "vision_extraction_job.json").write_text(
                json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
            project = {"id": "codex-image-test", "review_dir": str(root)}
            subskill = {"id": "room_boundaries_areas", "task": "room boundaries",
                        "inputs": ["floor plan"]}
            with patch.object(skills, "_consented_page_ids", return_value={1}):
                selected = skills._relevant_consented_images(subskill, project)
            self.assertEqual([row["page"] for row in selected], [1])
            self.assertEqual(selected[0]["path"], image.resolve())

    def test_skill_images_are_rendered_from_analysed_pdf_without_separate_vision_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "thumbnails" / "page_020.png"
            image.parent.mkdir()
            image.write_bytes(b"thumbnail")
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 20, "title": "Dimension Plan", "structured_content": {"word_count": 0}}]},
                "source_files": {"page_images": [{"page": 20, "path": str(image), "title": "Dimension Plan"}]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 20, "proposed_role": "main_floor_plan", "geometry_eligible": True, "title": "Dimension Plan"}]}), encoding="utf-8")
            project = {"id": "scan-image-test", "review_dir": str(root)}
            subskill = {"id": "room_boundaries_areas", "task": "room boundaries", "inputs": ["floor plan"]}
            with patch.object(skills, "_consented_page_ids", return_value={20}):
                selected = skills._relevant_consented_images(subskill, project)
            self.assertEqual([row["page"] for row in selected], [20])
            self.assertEqual(selected[0]["path"], image.resolve())

    def test_room_area_skill_prefers_ranked_dimension_plan_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            groups = []
            for group_id, pages in (("plan_geometry", [(20, "Dimension Plan"), (21, "Floor Finish Plan")]),
                                    ("visual_cross_check", [(2, "Structural Notes")])):
                members = []
                for page, title in pages:
                    image = root / "thumbnails" / f"page_{page:03}.png"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    image.write_bytes(b"test image")
                    members.append({"page": page, "title": title, "image_path": str(image)})
                groups.append({"group_id": group_id, "pages": members})
            manifest = root / "vision_extraction_runs" / "test" / "request_manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"groups": groups}), encoding="utf-8")
            (root / "vision_extraction_job.json").write_text(json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
            project = {"id": "room-area-image-test", "review_dir": str(root)}
            subskill = {"id": "room_boundaries_areas", "task": "infer room boundaries from plans",
                        "inputs": ["dimension plan", "floor finish plan"]}
            with patch.object(skills, "_consented_page_ids", return_value={2, 20, 21}):
                selected = skills._relevant_consented_images(subskill, project, limit=2)
            self.assertEqual([row["page"] for row in selected], [20, 21])

    def test_room_boundary_images_include_layout_before_dimension_and_context_sheets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page_dir = root / "vision_extraction_runs" / "test" / "pages"
            page_dir.mkdir(parents=True)
            pages = []
            for number, title in [(19, "Floor Layout"), (20, "Dimension Plan"), (21, "Floor Finish"), (22, "RCP")]:
                image = page_dir / f"page-{number}.png"
                image.write_bytes(b"page")
                pages.append({"page": number, "title": title, "image_path": str(image), "relevance": 1.0})
            manifest = page_dir.parent / "request_manifest.json"
            manifest.write_text(json.dumps({"groups": [{"group_id": "plan_geometry", "pages": pages[:3]},
                {"group_id": "ceiling_lighting", "pages": pages[3:]}]}), encoding="utf-8")
            (root / "vision_extraction_job.json").write_text(json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 19, "proposed_role": "main_floor_plan", "geometry_eligible": True,
                 "identity": {"title_candidates": [{"value": "Proposed Floor Layout"}]}},
                {"page": 20, "proposed_role": "main_floor_plan", "geometry_eligible": True,
                 "title": "Dimension Plan"},
            ]}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 19, "structured_content": {"markdown": "PROPOSED FLOOR LAYOUT"}},
                {"page": 20, "structured_content": {"markdown": "DIMENSION PLAN"}},
            ]}}), encoding="utf-8")
            (root / "ai_preliminary_run.json").write_text(json.dumps({"local_room_inference_proposal": {
                "rooms": [{"source_pages": [21, 22]}]}}), encoding="utf-8")
            project = {"id": "room-boundary-image-order", "review_dir": str(root)}
            subskill = {"id": "room_boundaries_areas", "task": "room boundaries", "inputs": ["floor plan"]}
            with patch.object(skills, "_consented_page_ids", return_value={19, 20, 21, 22}):
                selected = skills._relevant_consented_images(subskill, project, limit=4)
            self.assertEqual([row["page"] for row in selected], [19, 20, 21, 22])

    def test_surface_inventory_images_include_layout_setout_ceiling_shopfront_and_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            page_dir = root / "vision_extraction_runs" / "test" / "pages"
            page_dir.mkdir(parents=True)
            existing_image = page_dir / "page-33.png"
            existing_image.write_bytes(b"page")
            manifest = page_dir.parent / "request_manifest.json"
            manifest.write_text(json.dumps({"groups": [{"group_id": "plan_geometry", "pages": [
                {"page": 33, "title": "Setout Plan", "image_path": str(existing_image)}]}]}), encoding="utf-8")
            (root / "vision_extraction_job.json").write_text(json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
            image_rows, inventory_rows = [], {}
            for number, title, page_type, floor_kind, drawing_number in [
                (32, "Layout Plan", "floor_plan", "proposed_layout", "01.01"),
                (33, "Setout Plan", "floor_plan", "dimension_setout", "01.02"),
                (34, "Dimensions Plan", "floor_plan", "dimension_setout", "01.03"),
                (36, "Reflected Ceiling Plan", "reflected_ceiling_plan", "", "01.05"),
                (40, "Shopfront Elevation", "elevation", "", "02.01"),
                (41, "Section", "section", "", "02.02"),
                (42, "Section", "section", "", "02.03"),
            ]:
                image = root / "thumbnails" / f"page_{number:03}.png"
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(b"page")
                image_rows.append({"page": number, "path": str(image), "title": title})
                inventory_rows[str(number)] = {"title": title, "page_type": page_type,
                    "floor_plan_kind": floor_kind, "drawing_number": drawing_number}
            (root / "ai_input.json").write_text(json.dumps({
                "drawing_set": {"pages": [{"page": row["page"], "title": row["title"]} for row in image_rows]},
                "source_files": {"page_images": image_rows}}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": inventory_rows}), encoding="utf-8")
            project = {"id": "surface-inventory-image-order", "review_dir": str(root)}
            subskill = {"id": "surface_inventory", "task": "inventory wall and envelope surfaces",
                        "inputs": ["plans", "sections", "elevations"]}
            with patch.object(skills, "_consented_page_ids", return_value={32, 33, 34, 36, 40, 41, 42}):
                selected = skills._relevant_consented_images(subskill, project, limit=7)
            self.assertEqual([row["page"] for row in selected], [32, 33, 34, 36, 40, 41, 42])

    def test_opening_match_images_pin_plan_setout_shopfront_and_door_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pages_dir = root / "thumbnails"
            pages_dir.mkdir()
            rows = [
                (27, "Finishes Schedules", "schedule", "", "00.04e", [{"kind": "room_geometry", "what": "Internal door D1: sliding door, 850 clear."}]),
                (32, "Layout Plan", "floor_plan", "proposed_layout", "01.01", [{"kind": "windows_glazing", "what": "Segmented shopfront glazing."}]),
                (33, "Setout Plan", "floor_plan", "dimension_setout", "01.02", [{"kind": "windows_glazing", "what": "Two entrance door sets."}]),
                (34, "Dimensions Plan", "floor_plan", "dimension_setout", "01.03", [{"kind": "windows_glazing", "what": "Glazing follows the frontage."}]),
                (36, "Reflected Ceiling Plan", "reflected_ceiling_plan", "", "01.05", []),
                (40, "Shopfront Elevation", "elevation", "", "02.01", [{"kind": "windows_glazing", "what": "Framed glazing and entrance openings."}]),
                (41, "Section", "section", "", "02.02", []),
            ]
            image_rows, inventory_rows = [], {}
            for number, title, page_type, floor_kind, drawing_number, information in rows:
                image = pages_dir / f"page-{number}.png"
                image.write_bytes(b"page")
                image_rows.append({"page": number, "path": str(image), "title": title})
                inventory_rows[str(number)] = {"title": title, "page_type": page_type,
                    "floor_plan_kind": floor_kind, "drawing_number": drawing_number,
                    "information": information}
            (root / "ai_input.json").write_text(json.dumps({
                "drawing_set": {"pages": [{"page": row[0], "title": row[1]} for row in rows]},
                "source_files": {"page_images": image_rows}}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": inventory_rows}), encoding="utf-8")
            project = {"id": "opening-match-image-order", "review_dir": str(root)}
            subskill = {"id": "cross_sheet_opening_match", "task": "match plan openings to elevations",
                        "inputs": ["opening sightings", "plans", "elevations"]}
            with patch.object(skills, "_consented_page_ids", return_value={27, 32, 33, 34, 36, 40, 41}):
                selected = skills._relevant_consented_images(subskill, project, limit=5)
            self.assertEqual([row["page"] for row in selected], [32, 33, 34, 40, 27])
            area = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "surface_area")
            with patch.object(skills, "_consented_page_ids", return_value={27, 32, 33, 34, 36, 40, 41}):
                area_selected = skills._relevant_consented_images(area, project, limit=4)
            self.assertEqual([row["page"] for row in area_selected], [32, 33, 36, 40])
            glazing = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "glazing_properties")
            with patch.object(skills, "_consented_page_ids", return_value={27, 32, 33, 34, 36, 40, 41}):
                glazing_selected = skills._relevant_consented_images(glazing, project, limit=3)
            self.assertEqual([row["page"] for row in glazing_selected], [32, 40, 27])

    def test_surface_area_dependency_packet_keeps_ids_and_citations_but_drops_unused_glazing_fields(self):
        dependencies = {
            "surface_inventory": {"status": "needs_review", "proposal": {"status": "needs_review",
                "proposal_fields": {"surfaces": [{"surface_id": "wall-1", "type": "wall", "owner_ids": ["room-1"],
                    "geometry_ref": "edge-a", "citations": [{"page": 3, "excerpt": "north wall"}], "irrelevant": "large"}]},
                "unresolved_fields": ["lots of unrelated review text"]}},
            "cross_sheet_opening_match": {"status": "needs_review", "proposal": {"status": "needs_review",
                "proposal_fields": {"matches": [{"opening_id": "win-1", "host_wall_id": "wall-1",
                    "room_id": "room-1", "citations": [{"page": 4, "excerpt": "window"}], "irrelevant": "large"}]}}},
            "glazing_properties": {"status": "needs_review", "proposal": {"status": "needs_review",
                "inferences": [{"field": "properties.win-1.opening_area_m2", "value": 2.0,
                    "method": "scale-derived rectangle", "citation_ids": ["c4"]}],
                "proposal_fields": {"properties": [{"opening_id": "win-1", "opening_area_m2": 2.0,
                    "glass_area_m2": 1.7, "u_value_w_m2k": 2.3, "solar_property": 0.4,
                    "citations": [{"page": 4, "excerpt": "window"}]}]}}},
        }
        compact = skills._compact_dependencies(dependencies, consumer_id="surface_area")
        self.assertEqual(compact["surface_inventory"]["proposal"]["proposal_fields"]["surfaces"][0]["surface_id"], "wall-1")
        self.assertEqual(compact["cross_sheet_opening_match"]["proposal"]["proposal_fields"]["matches"][0]["host_wall_id"], "wall-1")
        glazing = compact["glazing_properties"]["proposal"]["proposal_fields"]["properties"][0]
        self.assertEqual(glazing["opening_area_m2"], 2.0)
        self.assertNotIn("u_value_w_m2k", glazing)
        self.assertEqual(compact["glazing_properties"]["proposal"]["inferences"][0]["method"], "scale-derived rectangle")

    def test_selected_skill_pages_use_high_resolution_pdf_render_when_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "thumbnails" / "page_001.png"
            image.parent.mkdir()
            image.write_bytes(b"thumbnail")
            manifest = root / "vision_extraction_runs" / "test" / "request_manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"groups": [{"group_id": "plan_geometry", "pages": [{
                "page": 1, "title": "Dimension Plan", "image_path": str(image),
            }]}]}), encoding="utf-8")
            (root / "vision_extraction_job.json").write_text(json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"source_files": {"page_images": [{"page": 1}]}}), encoding="utf-8")
            project = {"id": "hires-image-test", "review_dir": str(root)}
            subskill = {"id": "room_boundaries_areas", "task": "room boundaries", "inputs": ["dimensions"]}

            def render(_ai_input, _source_image, target, _dpi):
                Path(target).write_bytes(b"high-resolution")
                return {"ok": True}

            with patch.object(skills, "_consented_page_ids", return_value={1}), \
                 patch("ai.chatgpt_packet.render_high_res_page", side_effect=render):
                selected = skills._relevant_consented_images(subskill, project, limit=2,
                    render_dir=root / "rendered")
            self.assertIn("rendered", selected[0]["path"].parts)
            self.assertEqual(selected[0]["path"].read_bytes(), b"high-resolution")

    def test_room_geometry_prompt_gets_render_to_vector_coordinate_transform(self):
        import struct
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "render.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 2980, 2108))
            (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [
                {"page": 20, "coordinate_systems": {"image_px": {"image_width": 745, "image_height": 527}}}
            ]}}), encoding="utf-8")
            frames = skills._attached_image_coordinate_frames([{"page": 20, "path": image}],
                {"id": "frame-test", "review_dir": str(root)})
        self.assertEqual(frames[0]["canonical_vector_image_px"], [745, 527])
        self.assertEqual(frames[0]["attached_to_canonical_scale"], [0.25, 527 / 2108])

    def test_room_geometry_prompt_uses_full_page_render_when_vector_geometry_is_absent(self):
        import struct
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "full-page.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 3000, 2100))
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 1, "main_scale": "1:50"}
            ]}), encoding="utf-8")
            (root / "ai_input.json").write_text(json.dumps({"source_files": {"pdf": str(root / "source.pdf")}}), encoding="utf-8")
            page = MagicMock(width=1190.55, height=841.89)
            document = MagicMock()
            document.__enter__.return_value = document
            document.pages = [page]
            with patch("pdfplumber.open", return_value=document):
                frames = skills._attached_image_coordinate_frames([{"page": 1, "path": image}],
                    {"id": "frame-no-vector", "review_dir": str(root)})
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["frame_source"], "attached_full_page_render")
        self.assertEqual(frames[0]["canonical_image_width_px"], 3000)
        self.assertAlmostEqual(frames[0]["scale_mm_per_canonical_px"], (1190.55 * 25.4 / 72 * 50) / 3000)

    def test_case_file_room_geometry_prompt_includes_attached_image_frames(self):
        subskill = next(row for row in skills.load_subskill_registry()["subskills"]
                        if row["id"] == "room_boundaries_areas")
        case = {"attached_pages": [1], "page_index": [], "readings": [], "extracted": [], "job": {}}
        image = Path("/tmp/full-page.png")
        expected_frame = {"physical_page": 1, "frame_source": "attached_full_page_render",
                          "canonical_image_width_px": 3000, "scale_mm_per_canonical_px": 7.0}
        attempt = {"evidence_pages": []}
        with patch("backend.skill_case_file.build", return_value=(case, [image])), \
             patch.object(skills, "_attached_image_coordinate_frames", return_value=[expected_frame]), \
             patch.object(skills, "_send_case_file_prompt", side_effect=lambda _p, _purpose, prompt, _images, _attempt:
                          prompt):
            prompt = skills._call_case_file_provider(subskill, {"id": "case-frame-test", "review_dir": "/tmp"},
                {}, {}, attempt)
        self.assertIn(json.dumps(expected_frame, ensure_ascii=False), prompt)
        self.assertEqual(attempt["images"][0]["page"], 1)

    def test_structured_remediation_does_not_reject_otherwise_valid_proposal(self):
        registry = skills.load_subskill_registry()
        subskill = next(row for row in registry["subskills"] if row["id"] == "room_boundaries_areas")
        result = {"subskill_id": subskill["id"], "subskill_version": subskill["version"], "status": "needs_review",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": [], "remediation": {
                "error_code": "GEOMETRY_EVIDENCE_INCOMPLETE", "next_action": "Supply the primary-plan image."},
            "input_fingerprint": "a" * 64, "proposal_fields": {"geometry_candidates": []}}
        validated = skills._validate_subskill_output(subskill, result, registry)
        self.assertEqual(validated["remediation"], ["GEOMETRY_EVIDENCE_INCOMPLETE: Supply the primary-plan image."])

    def test_connected_cafe_finish_areas_form_cited_area_only_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = {"id": "printed-finish-area-test", "review_dir": str(root)}
            (root / "ai_preliminary_run.json").write_text(json.dumps({"local_room_inference_proposal": {"rooms": [
                {"room_id": "room-use:ground:cafe", "label": "Cafe", "level_name": "Ground",
                 "source_pages": [1], "zone_role": "whole_tenancy_candidate"}
            ]}}), encoding="utf-8")
            items = [
                {"text": "TL", "bbox": [100, 100, 110, 110]}, {"text": "02", "bbox": [100, 111, 110, 121]},
                {"text": "40.73m2", "bbox": [115, 102, 145, 112]},
                {"text": "TL", "bbox": [200, 200, 210, 210]}, {"text": "03", "bbox": [200, 211, 210, 221]},
                {"text": "20.71m2", "bbox": [215, 202, 245, 212]},
            ]
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [{"page": 1, "width": 1000, "height": 700,
                "title_blocks": [{"region": "bottom_band", "bbox": [0, 600, 1000, 700]}],
                "standalone_text_items": items}]}), encoding="utf-8")
            dependencies = {"room_identity_use": {"proposal": {"proposal_fields": {"rooms": [
                {"room_id": "room-use:ground:cafe", "taxonomy_id": "dining"}
            ]}}}}
            area = skills._printed_tenant_finish_area(project, dependencies)
            self.assertEqual(area["area_m2"], 61.44)
            subskill = next(row for row in skills.load_subskill_registry()["subskills"]
                            if row["id"] == "room_boundaries_areas")
            proposal = {"subskill_id": subskill["id"], "subskill_version": subskill["version"],
                "status": "needs_review", "affected_ids": [], "observations": [], "inferences": [],
                "citations": [], "confidence": 0.7, "alternatives": [], "unresolved_fields": [],
                "remediation": [], "input_fingerprint": "b" * 64,
                "proposal_fields": {"geometry_candidates": []}}
            with patch.object(skills, "_consented_page_ids", return_value={1}):
                augmented = skills._augment_printed_tenant_area(subskill, project, dependencies, proposal)
        candidate = augmented["proposal_fields"]["geometry_candidates"][0]
        self.assertEqual(candidate["room_id"], "room-use:ground:cafe")
        self.assertEqual(candidate["area_m2"], 61.44)
        self.assertEqual(candidate["calibration"]["source"], "printed_floor_finish_area_sum")
        self.assertEqual(candidate["boundary_points_px"], [])
        self.assertEqual({row["page"] for row in augmented["citations"]}, {1})

    def test_list_findings_keep_row_citations_and_auto_accept_only_individually_confident_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 4, "identity_status": "confirmed"},
                {"page": 5, "identity_status": "confirmed"},
            ]}))
            proposal_root = root / "skill_workflow_runs" / "run-1" / "proposals"
            proposal_root.mkdir(parents=True)
            proposal = {"subskill_id": "site_clue_extraction", "status": "needs_review", "confidence": .99,
                "citations": [{"id": "c1", "page": 4, "excerpt": "Shop entry from George Street"},
                              {"id": "c2", "page": 5, "excerpt": "North elevation"}],
                "inferences": [], "alternatives": [], "unresolved_fields": [],
                "proposal_fields": {"site_candidates": [
                    {"candidate": "George Street", "confidence": .91, "citation_ids": ["c1"]},
                    {"candidate": "North elevation", "confidence": .84, "citation_ids": ["c2"]},
                    {"candidate": "Uncited guess", "confidence": .99},
                ]}}
            (proposal_root / "site_clue_extraction.json").write_text(json.dumps(proposal))
            manifest = {"run_id": "run-1", "source_fingerprint": "a" * 64, "subskills": {}}
            cited = skills._finding_citations(proposal, proposal["proposal_fields"]["site_candidates"][0], [], 3)
            uncited = skills._finding_citations(proposal, proposal["proposal_fields"]["site_candidates"][2], [], 3)
            self.assertEqual(cited, [{"page": 4, "excerpt": "Shop entry from George Street"}])
            self.assertEqual(uncited, [])
            self.assertEqual(skills._finding_confidence(proposal, proposal["proposal_fields"]["site_candidates"][0], [], 3), .91)
            self.assertEqual(skills._finding_confidence(proposal, proposal["proposal_fields"]["site_candidates"][2], [], 3), .99)
            self.assertEqual(skills._auto_review_confident_findings(root, manifest), 1)
            decisions = json.loads((root / "skill_review_decisions.json").read_text())["decisions"]
            self.assertEqual(list(decisions), ["site_clue_extraction:site_candidates:0"])
            self.assertEqual(decisions["site_clue_extraction:site_candidates:0"]["reviewer"], "AI evidence gate")

    def test_phase1_cited_numeric_inferences_are_auto_used_and_malformed_rows_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [{"page": 8}]}))
            proposal_root = root / "skill_workflow_runs" / "run-1" / "proposals"
            proposal_root.mkdir(parents=True)
            proposal = {"subskill_id": "room_identity_use", "status": "needs_review",
                "citations": [{"id": "c1", "page": 8, "excerpt": "Window schedule: double glazing"},
                              {"id": "c2", "page": 8, "excerpt": "Unambiguous second window row"}],
                "inferences": [{"field": "properties", "target": "opening-1", "value": 2.4,
                                "method": "matched the window type to the cited schedule row", "citation_ids": ["c1"]},
                               {"field": "properties", "target": "opening-2", "value": 3.2,
                                "citation_ids": ["c2"]}],
                "alternatives": [], "unresolved_fields": [], "confidence": 0.82,
                "proposal_fields": {"properties": [
                    {"opening_id": "opening-1", "u_value_w_m2k": 2.4, "confidence": 0.82, "citation_ids": ["c1"]},
                    {"opening_id": "opening-2", "u_value_w_m2k": 3.2, "confidence": 0.95, "citation_ids": ["c2"]},
                ]}}
            (proposal_root / "room_identity_use.json").write_text(json.dumps(proposal))
            manifest = {"run_id": "run-1", "source_fingerprint": "a" * 64, "scope": "phase1_envelope", "subskills": {}}
            self.assertEqual(skills._auto_review_confident_findings(root, manifest), 1)
            decisions = json.loads((root / "skill_review_decisions.json").read_text())["decisions"]
            self.assertEqual(list(decisions), ["room_identity_use:properties:0"])
            self.assertEqual(decisions["room_identity_use:properties:0"]["usage_scope"], "phase1_draft_only")
            # Envelope proposals are outside the trimmed Phase 1 skill review.
            (proposal_root / "glazing_properties.json").write_text(json.dumps({**proposal, "subskill_id": "glazing_properties"}))
            self.assertEqual(skills._auto_review_confident_findings(root, manifest), 0)
            decisions = json.loads((root / "skill_review_decisions.json").read_text())["decisions"]
            self.assertNotIn("glazing_properties:properties:0", decisions)

    def test_provider_call_retries_one_transient_transport_failure(self):
        calls = []
        attempt = {}

        def provider():
            calls.append(True)
            if len(calls) == 1:
                raise ConnectionResetError("Remote end closed connection without response")
            return {"ok": True}

        with patch.object(skills, "_TRANSIENT_PROVIDER_RETRY_DELAY_S", 0):
            result = skills._provider_call_with_retry(provider, attempt)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(attempt["transport_retries"], 1)

    def test_unresolved_fields_do_not_require_a_separate_accept_or_reject_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proposals = root / "skill_workflow_runs" / "run-1" / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "construction_matching.json").write_text(json.dumps({
                "subskill_id": "construction_matching", "status": "needs_review",
                "proposal_fields": {"constructions": []},
                "unresolved_fields": [{"field": "wall construction", "reason": "No assembly schedule."}]}))
            paths = {"root": root}
            manifest = {"run_id": "run-1", "source_fingerprint": "a" * 64}
            self.assertFalse(skills._has_open_findings(paths, manifest))

    def test_skill_equipment_already_in_equipment_queue_is_not_a_second_open_finding(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proposals = root / "skill_workflow_runs" / "run-1" / "proposals"
            proposals.mkdir(parents=True)
            (root / "page_extraction.json").write_text(json.dumps({"equipment_appliances": {"findings": [
                {"id": "equipment_appliances:1", "value": {"name": "2D UB FREEZER"}}]}}))
            proposal = {"subskill_id": "equipment_evidence", "status": "needs_review",
                "proposal_fields": {"equipment": [
                    {"name": "2D UB FREEZER", "equipment_id": "pdf:p1:freezer"},
                    {"name": "Cold display", "equipment_id": "pdf:p1:display"}]},
                "unresolved_fields": []}
            (proposals / "equipment_evidence.json").write_text(json.dumps(proposal))
            self.assertEqual(skills._equipment_evidence_mirror_indices(root, proposal), {0})
            paths = {"root": root}
            manifest = {"run_id": "run-1", "source_fingerprint": "a" * 64}
            self.assertTrue(skills._has_open_findings(paths, manifest))  # the unique item still needs review
            proposal["proposal_fields"]["equipment"].pop()
            (proposals / "equipment_evidence.json").write_text(json.dumps(proposal))
            self.assertFalse(skills._has_open_findings(paths, manifest))

    def test_numeric_proposals_need_citations_and_provider_values_are_draft_only(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "occupancy_seating")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "provisional",
            "affected_ids": ["room-1"], "observations": [], "inferences": [], "citations": [], "confidence": 0.7,
            "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": {"occupancy": [{"room_id": "room-1", "count": 12, "basis": "counted", "density_record_id": None, "rounding": None}]}}
        with self.assertRaisesRegex(ValueError, "uncited"):
            skills._validate_subskill_output(task, proposal, registry, allowed_pages={1})

    def test_multi_page_citations_are_validated_against_allowed_pages(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": ["page:1", "page:2"], "observations": [], "inferences": [],
            "citations": [{"id": "index", "physical_pages": [1, 2], "source": "page index"}],
            "confidence": 0.7, "alternatives": [], "unresolved_fields": [], "remediation": [],
            "input_fingerprint": "a" * 64,
            "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "201",
                "title": "Floor Plan", "drawing_type": "floor_plan", "alternatives": []}]}}
        self.assertEqual(skills._validate_subskill_output(task, proposal, registry, allowed_pages={1, 2}), proposal)
        # A citation to pages outside the set is dropped and noted; the rest of the answer is kept.
        import copy
        trimmed = skills._validate_subskill_output(task, copy.deepcopy(proposal), registry, allowed_pages={1})
        self.assertEqual(trimmed["citations"], [])
        self.assertIn("cites pages not in the set: [2]", trimmed["remediation"][-1])
        self.assertEqual(trimmed["proposal_fields"], proposal["proposal_fields"])

    def test_malformed_citations_are_dropped_not_fatal_and_a_source_document_reference_counts(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": [], "observations": [], "inferences": [],
            "citations": [{"id": "doc", "source_document_id": "pdf-abc", "source": "case file index"},
                          {"id": "bad-list", "physical_pages": []}, {"id": "nothing"}, "not an object",
                          {"id": "p1", "physical_page": 1}],
            "confidence": 0.7, "alternatives": [], "unresolved_fields": [], "remediation": ["keep me"],
            "input_fingerprint": "a" * 64,
            "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "201", "title": "Floor Plan",
                                                     "drawing_type": "floor_plan", "alternatives": []}]}}
        result = skills._validate_subskill_output(task, proposal, registry, allowed_pages={1})
        self.assertEqual([row["id"] for row in result["citations"]], ["doc", "p1"])
        self.assertEqual(result["remediation"][0], "keep me")
        self.assertIn("citation 2 has no usable page list", result["remediation"][-1])
        self.assertIn("citation 3 names no page or source", result["remediation"][-1])
        self.assertIn("citation 4 is a sentence naming no page", result["remediation"][-1])

    def test_runtime_physical_pdf_page_citation_alias_is_accepted(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": ["page:19"], "observations": [], "inferences": [],
            "citations": [{"citation_id": "c19", "physical_pdf_page": 19,
                "drawing_identity": "201", "excerpt_or_crop": "PROPOSED FLOOR LAYOUT"}],
            "confidence": 0.7, "alternatives": [], "unresolved_fields": [], "remediation": [],
            "input_fingerprint": "a" * 64,
            "proposal_fields": {"page_identities": [{"physical_page": 19, "drawing_number": "201",
                "title": "Proposed Floor Plan", "drawing_type": "floor_plan", "alternatives": []}]}}
        checked = skills._validate_subskill_output(task, proposal, registry, allowed_pages={19})
        self.assertEqual(checked["citations"][0]["physical_pdf_page"], 19)

    def test_a_bare_sentence_for_a_list_field_is_kept_as_one_item_and_other_types_are_refused(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": ["page:1"], "observations": "Title block reads 201.", "inferences": [],
            "citations": [{"page": 1, "excerpt": "Title block"}], "confidence": 0.7, "alternatives": "",
            "unresolved_fields": [], "remediation": "Provide the drawing register.", "input_fingerprint": "a" * 64,
            "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "201",
                "title": "Floor Plan", "drawing_type": "floor_plan", "alternatives": []}]}}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(proposal)), registry, allowed_pages={1})
        self.assertEqual((checked["observations"], checked["alternatives"], checked["remediation"]),
                         (["Title block reads 201."], [], ["Provide the drawing register."]))
        with self.assertRaises(Exception):
            skills._validate_subskill_output(task, {**json.loads(json.dumps(proposal)), "remediation": {"step": "x"}}, registry, allowed_pages={1})

    def test_a_row_with_a_required_value_left_empty_is_dropped_and_noted_not_a_failed_skill(self):
        # Butcher Buffet 2026-10-15: one opening's exposure came back null and failed the whole orientation skill.
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "exposure_orientation")
        reply = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": ["shopfront"], "observations": [], "inferences": [], "citations": [{"page": 26, "excerpt": "Shopfront"}],
            "confidence": 0.5, "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": {"exposures": [
                {"opening_id": "shopfront", "exposure": "mall", "azimuth_deg": None, "orientation_source_id": None},
                {"opening_id": "window-3200x600", "exposure": None, "azimuth_deg": None, "orientation_source_id": None}]}}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(reply)), registry, allowed_pages={26})
        self.assertEqual([row["opening_id"] for row in checked["proposal_fields"]["exposures"]], ["shopfront"])
        self.assertIn("Dropped rows with a required value left empty: exposures[1] (exposure not given).", checked["remediation"][-1])
        wrong_type = json.loads(json.dumps(reply))
        wrong_type["proposal_fields"]["exposures"][0]["exposure"] = 3            # a wrong type still fails
        with self.assertRaises(Exception):
            skills._validate_subskill_output(task, wrong_type, registry, allowed_pages={26})

    def test_citations_written_as_sentences_count_by_the_page_they_name_and_still_must_be_in_the_set(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "ceiling_height_volume")
        base = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": [], "observations": [], "confidence": 0.5, "alternatives": [], "unresolved_fields": [],
            "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": {name: [] for name in task["proposal_fields"]}}
        sentences = ["c1: p22: 'CH:3150MM' on the reflected ceiling plan", "pdf-a9ecdf45c556:p30 kitchen elevation",
                     "General note with no page", "p99: a page outside this set"]
        reply = {**base, "citations": sentences, "inferences": [
            {"field": "heights[].height_mm", "value": 3150, "method": "printed ceiling height",
             "citations": ["c1", "pdf-a9ecdf45c556:p30 kitchen elevation"]}]}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(reply)), registry, allowed_pages={22, 30})
        self.assertEqual([(row.get("id"), row["page"]) for row in checked["citations"]], [("c1", 22), (None, 30)])
        self.assertEqual(checked["citations"][1]["source_document_id"], "pdf-a9ecdf45c556")
        self.assertEqual(checked["inferences"][0]["citation_ids"], ["c1", "pdf-a9ecdf45c556:p30 kitchen elevation"])
        self.assertIn("citation 3 is a sentence naming no page", checked["remediation"][-1])
        self.assertIn("citation 4 cites pages not in the set: [99]", checked["remediation"][-1])
        # Archie's own case file (its defaults and readings) is a source: labelled, not a drawing page.
        from_case_file = {**reply, "citations": ["task_evidence.schedules: controlled preliminary room-use profile"],
                          "inferences": [{"field": "heights[].height_mm", "value": 2700, "method": "preliminary default",
                                          "citations": ["task_evidence.ceiling_height_volume.heights"]}]}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(from_case_file)), registry, allowed_pages={22, 30})
        self.assertEqual(checked["citations"][0]["source_document_id"], "archie_case_file")
        # Another skill's finding is a source too ("equipment_evidence-cold-display").
        from_skill = {**reply, "inferences": [{"field": "cold_display_rated_input_w", "value": 900, "method": "equipment finding",
                                               "citations": ["equipment_evidence-cold-display"]}]}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(from_skill)), registry, allowed_pages={22, 30})
        self.assertEqual(checked["inferences"][0]["citation_ids"], ["equipment_evidence-cold-display"])
        # A skill ID prefix alone is not proof of a cited finding.
        from_unknown_skill_finding = {**reply, "inferences": [{"field": "cold_display_rated_input_w", "value": 900,
            "method": "equipment finding", "citations": ["unknown_skill-invented-field"]}]}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(from_unknown_skill_finding)), registry,
            allowed_pages={22, 30})
        self.assertEqual(checked["inferences"], [])
        # A number citing nothing usable is dropped and noted; the rest of the reply is kept (it used to fail the
        # whole "What we need to find" list).
        uncited = {**reply, "inferences": [*reply["inferences"], {"field": "guess_w", "value": 2700, "method": "estimate",
                                                                  "citations": ["general knowledge", "p99"]}]}
        checked = skills._validate_subskill_output(task, json.loads(json.dumps(uncited)), registry, allowed_pages={22, 30})
        self.assertEqual([row["field"] for row in checked["inferences"]], ["heights[].height_mm"])
        self.assertIn("Dropped numbers given without a source or method: inference 2 (guess_w).", checked["remediation"][-1])
        self.assertEqual(skills._text_pages("Step 4 on page 21, see p.22 and pdf-ab12cd34:p23; map 5"), [21, 22, 23])


        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "provisional",
            "affected_ids": ["page:1"], "observations": [], "inferences": [],
            "citations": [{"page": 1, "excerpt": "Title block"}], "confidence": "high",
            "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "201",
                "title": "Floor Plan", "drawing_type": "floor_plan", "alternatives": []}]}}
        checked = skills._validate_subskill_output(task, proposal, registry, allowed_pages={1})
        self.assertEqual(checked["confidence"], 0.85)
        self.assertEqual(checked["status"], "needs_review")
        self.assertIn("confidence", checked["unresolved_fields"])

    def test_structured_validation_errors_keep_stable_codes_and_explain_checks(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        proposal = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": empty_typed_proposal(task)}
        cases = [
            ({key: value for key, value in proposal.items() if key != "observations"}, "envelope_missing_keys", "observations"),
            ({**proposal, "subskill_version": task["version"] + 1}, "subskill_version_mismatch", "subskill_version"),
            ({**proposal, "proposal_fields": {}}, "proposal_fields_mismatch", "proposal_fields"),
        ]
        for result, expected_check, expected_path in cases:
            with self.subTest(expected_check=expected_check):
                with self.assertRaises(skills.SubskillValidationError) as raised:
                    skills._validate_subskill_output(task, result, registry)
                self.assertEqual(str(raised.exception), "subskill_output_invalid")
                self.assertEqual(raised.exception.check, expected_check)
                self.assertEqual(raised.exception.path, expected_path)
                self.assertTrue(raised.exception.detail)

    def test_attempt_archive_keeps_raw_prompt_images_cli_and_validation(self):
        registry = skills.load_subskill_registry()
        task = next(row for row in registry["subskills"] if row["id"] == "sheet_identity")
        valid = {"subskill_id": task["id"], "subskill_version": task["version"], "status": "needs_review",
            "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
            "alternatives": [], "unresolved_fields": [], "remediation": [], "input_fingerprint": "a" * 64,
            "proposal_fields": empty_typed_proposal(task)}
        scenarios = [
            ("invalid_json", "not-json", "skill_provider_invalid_json", "provider_json_parse", None),
            ("missing_envelope", json.dumps({k: v for k, v in valid.items() if k != "observations"}),
             "subskill_output_invalid", "envelope_missing_keys", {k: v for k, v in valid.items() if k != "observations"}),
            ("wrong_version", json.dumps({**valid, "subskill_version": task["version"] + 1}),
             "subskill_output_invalid", "subskill_version_mismatch", {**valid, "subskill_version": task["version"] + 1}),
            ("fields_mismatch", json.dumps({**valid, "proposal_fields": {}}),
             "subskill_output_invalid", "proposal_fields_mismatch", {**valid, "proposal_fields": {}}),
            ("cli_failure", "Codex unavailable", "codex_cli_failed", "provider_exit_status", None),
            ("valid", json.dumps(valid), "", "proposal_valid", valid),
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, raw_output, code, check, parsed in scenarios:
                with self.subTest(name=name):
                    validation = {}
                    outcome = "accepted"
                    if parsed is not None:
                        try:
                            skills._validate_subskill_output(task, parsed, registry)
                        except skills.SubskillValidationError as error:
                            validation = {"check": error.check, "path": error.path, "detail": error.detail}
                            outcome, code = "rejected", str(error)
                    else:
                        outcome = "rejected"
                        validation = {"check": check, "path": "$", "detail": "Provider output could not be accepted."}
                    attempt_ref, metadata = skills._persist_skill_attempt(root, "run-1", task["id"],
                        attempt_record={"prompt": "Prompt for " + name, "images": [{"page": 5, "path": "page-5.png"}],
                            "raw_record": {"provider": "codex_cli", "exit_code": 2 if name == "cli_failure" else 0,
                                "stderr_tail": "permission denied" if name == "cli_failure" else "",
                                "reply_text": raw_output, "duration_seconds": 0.1}},
                        input_fingerprint="b" * 64, outcome=outcome, failure_phase="proposal_validation",
                        error_code=code, validation=validation)
                    attempt_dir = root / attempt_ref
                    saved = json.loads((attempt_dir / "attempt.json").read_text())
                    self.assertEqual((attempt_dir / "raw_output.txt").read_text(), raw_output)
                    self.assertIn("Prompt for " + name, (attempt_dir / "prompt.txt").read_text())
                    self.assertEqual(json.loads((attempt_dir / "images.json").read_text())[0]["page"], 5)
                    self.assertEqual(json.loads((attempt_dir / "cli.json").read_text()).get("exit_code"),
                                     2 if name == "cli_failure" else 0)
                    self.assertEqual(saved["validation_check"], check)
                    self.assertEqual(saved["outcome"], outcome)

    def test_case_file_invalid_json_archives_the_provider_reply_and_classifies_failure(self):
        from backend import ai_provider

        class Provider:
            name = "openrouter"
            model = "deepseek/deepseek-v4.1-flash"

            def propose(self, _prompt, image_paths=()):
                raise ai_provider.InvalidProviderReply(
                    "The reply was not valid JSON.",
                    {"provider": self.name, "model": self.model, "reply_text": '{"rooms": [',
                     "finish_reason": "length", "usage": {"output_tokens": 48000}},
                )

        with tempfile.TemporaryDirectory() as folder:
            attempt = {"prompt": "", "images": [], "raw_record": {}}
            project = {"id": "invalid-json-case-file", "review_dir": folder}
            skills._USAGE_STOPPED.pop(str(Path(folder).resolve()), None)
            with patch.object(skills, "CASE_FILE_PROVIDER_FACTORY", return_value=Provider()):
                with self.assertRaises(skills.SkillProviderError) as raised:
                    skills._send_case_file_prompt(
                        project, "skill:room_boundaries_areas", "Trace the rooms.", [], attempt
                    )

        self.assertEqual(str(raised.exception), "skill_provider_invalid_json")
        self.assertEqual(raised.exception.raw_record["reply_text"], '{"rooms": [')
        self.assertEqual(raised.exception.raw_record["finish_reason"], "length")
        self.assertIs(raised.exception.attempt_record, attempt)
        self.assertEqual(attempt["raw_record"]["provider"], "openrouter")

    def test_generic_domain_evidence_excludes_unvalidated_vector_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            entities = [{"entity_id": f"wall-{index}", "kind": "wall", "geometry_status": "geometry_proposed", "source": {"page": 1}}
                        for index in range(50)]
            entities += [{"entity_id": "dim-1", "kind": "dimension", "geometry_status": "geometry_proposed", "source": {"page": 1}},
                         {"entity_id": "wall-ok", "kind": "wall", "geometry_status": "geometry_confirmed", "source": {"page": 1}},
                         {"entity_id": "room-1", "kind": "room", "geometry_status": "geometry_review_required", "source": {"page": 1}}]
            (root / "geometry_resolution.json").write_text(json.dumps({"entities": entities}), encoding="utf-8")
            (root / "value_resolution.json").write_text(json.dumps({"records": [
                {"record_id": "v1", "target": "opening.window_1.u_value"},
                {"record_id": "v2", "target": "room.people_sensible_w_per_person"},
            ]}), encoding="utf-8")
            surface, _ = skills._subskill_records("surface_inventory", project)
            glazing, _ = skills._subskill_records("glazing_properties", project)
        surface_ids = {row.get("entity_id") for row in surface["surfaces"]}
        self.assertEqual(surface_ids, {"wall-ok", "room-1"})
        self.assertEqual({row.get("record_id") for row in glazing["properties"] if row.get("source_artifact") == "value_resolution.json"}, {"v1"})

    def test_prompt_compaction_drops_bookkeeping_and_reports_budget(self):
        subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "room_identity_use")
        evidence = {"task_evidence": {"rooms": [{"room_id": "room-1", "label": "Bar", "witness_ids": ["w1", "w2"],
                    "source_fingerprints": {"a": "b" * 64}, "record_fingerprint": "c" * 64,
                    "evidence": [{"page": 20, "excerpt": "x" * 2000}]}]}}
        dependencies = {"sheet_identity": {"status": "needs_review", "output_summary": {"record_count": 9},
            "attempt_ref": "skill_workflow_runs/r/attempts/sheet_identity/1",
            "proposal": {"status": "needs_review", "affected_ids": ["sheet:202"], "citations": [{"page": 20}],
                         "proposal_fields": {"page_identities": []}, "unresolved_fields": [],
                         "observations": [{"text": "long observation"}], "remediation": ["retry"]}}}
        prompt, report = skills._bounded_proposal_prompt(subskill, dependencies, evidence)
        for absent in ("witness_ids", "source_fingerprints", "record_fingerprint", "attempt_ref", "output_summary", "long observation"):
            self.assertNotIn(absent, prompt)
        self.assertIn("room-1", prompt)
        self.assertNotIn("sheet:202", prompt)             # a prerequisite's affected IDs are left out; its fields carry the IDs
        self.assertIn("[truncated 1400 chars]", prompt)
        self.assertEqual(report["status"], "within_budget")
        self.assertEqual(report["prompt_chars"], len(prompt))
        self.assertEqual(report["budget_chars"], 80_000)
        self.assertGreaterEqual(report["dropped_provenance_keys"], 3)
        self.assertEqual(report["truncated_strings"], 1)
        self.assertEqual(skills._prompt_budget_chars("room_boundaries_areas"), 180_000)
        with patch.dict("os.environ", {"ARCHIE_SKILL_PROMPT_MAX_CHARS": "5000"}):
            self.assertEqual(skills._prompt_budget_chars("room_boundaries_areas"), 5000)
        self.assertEqual(skills._compact_line({"candidate_id": "P20-VLINE-1", "start_px": [10.4, 20.6], "end_px": [99.5, 20.6],
                                               "candidate_role_hint": "possible_wall_or_dimension"}),
                         ["P20-VLINE-1", 10, 21, 100, 21, "W"])

    def test_prompt_compaction_shrinks_empties_schedules_numbers_and_citations(self):
        stats = {}
        hours = [0.0] * 10 + [1.0] * 13 + [0.0]
        compact = skills._compact_for_prompt({
            "room_id": "room-1", "model": None, "notes": "", "walls": [], "operands": {}, "flag": False, "count": 0,
            "schedule": {"weekday": hours, "saturday": hours, "sunday": [0.0] * 24},
            "mm_per_px": 14.102976029169326, "value_mm": 23265.0, "x": 1932.42,
            "evidence": [{"page": 21, "excerpt": "Shop"}, {"page": 21, "excerpt": "Shop"}],
            "points": [[1.0, 2.0], [1.0, 2.0]]}, stats)
        self.assertEqual(compact, {"room_id": "room-1", "flag": False, "count": 0,
            "schedule": {"weekday,saturday": "00-10:0 10-23:1 23-24:0", "sunday": "00-24:0"},
            "mm_per_px": 14.1, "value_mm": 23265.0, "x": 1932.0,
            "evidence": [{"page": 21, "excerpt": "Shop"}],    # an identical record once
            "points": [[1.0, 2.0], [1.0, 2.0]]})              # coordinates are never de-duplicated (a closing point stays)
        self.assertEqual((stats["dropped_empty"], stats["dropped_duplicates"]), (4, 1))
        self.assertEqual(skills._compact_for_prompt([0.5] * 24, {}), "00-24:0.5")
        dependencies = skills._compact_dependencies({"equipment_evidence": {"status": "needs_review", "proposal": {
            "affected_ids": ["a"], "citations": [{"id": "c1", "physical_page": 5, "locator": "Upper right", "excerpt": "E06 COMBI OVEN"},
                                                 {"page": 20, "visual_evidence": "v" * 300}]}}})
        self.assertEqual(dependencies["equipment_evidence"]["proposal"],
                         {"citations": ["p5: E06 COMBI OVEN", "p20: " + "v" * 160]})
        self.assertEqual(skills._prompt_json({"a": [1, 2]}), '{"a":[1,2]}')
        bare = skills._compact_dependencies({"equipment_evidence": {"status": "needs_review", "proposal": {
            "citations": [{"page": 5, "excerpt": "E06"}], "proposal_fields": {"equipment": []}}}}, citations=False)
        self.assertEqual(bare["equipment_evidence"]["proposal"], {"proposal_fields": {"equipment": []}})
        needs = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "information_needs")
        prompt, _ = skills._bounded_proposal_prompt(needs, {"equipment_evidence": {"status": "needs_review", "proposal": {
            "citations": [{"page": 5, "excerpt": "E06 COMBI"}], "proposal_fields": {"equipment": [{"name": "Combi oven"}]}}}}, {})
        self.assertNotIn("E06 COMBI", prompt)                              # the needs list gets values, not citations
        self.assertIn("Combi oven", prompt)

    def test_document_mapping_is_built_from_the_page_reading_and_keeps_only_printed_reference_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "page_inventory.json").write_text(json.dumps({"pages": {
                "1": {"page_type": "cover_or_drawing_list", "title": "COVER", "drawing_number": "000", "information": []},
                "2": {"page_type": "floor_plan", "title": "PLAN", "drawing_number": "", "information": []}}}))
            (root / "drawing_coverage.json").write_text(json.dumps({
                "page_roles": [{"page": 1, "resolved_drawing_number": "00O", "revision": "B"}, {"page": 2}],
                "page_relationships": [{"from_page": 1, "to_page": 2, "basis": ["compatible architect evidence roles"]},
                                       {"from_page": 2, "to_page": 1, "basis": ["section mark 1/000 on plan"], "relationship": "section"}]}))
            project = {"id": "j", "review_dir": folder}
            registry = {row["id"]: row for row in skills.load_subskill_registry()["subskills"]}
            sheets = skills._document_map_proposal(registry["sheet_identity"], project, "0" * 64)
            self.assertEqual(sheets["proposal_fields"]["page_identities"][0],
                             {"physical_page": 1, "drawing_number": "000", "title": "COVER", "drawing_type": "cover_or_drawing_list",
                              "alternatives": ["00O"]})                      # the coverage reading kept as an alternative
            self.assertIn("page(s) 2", sheets["unresolved_fields"][0])
            revisions = skills._document_map_proposal(registry["revision_scope"], project, "0" * 64)
            self.assertEqual([(row["revision"], row["status"]) for row in revisions["proposal_fields"]["revision_records"]],
                             [("B", "read_from_title_block"), (None, "not_read")])
            links = skills._document_map_proposal(registry["page_relationships"], project, "0" * 64)
            self.assertEqual(links["proposal_fields"]["page_links"],
                             [{"from_page": 2, "to_page": 1, "relationship": "section", "evidence": ["section mark 1/000 on plan"]}])
            self.assertIn("1 candidate link", links["observations"][1]["description"])
            for proposal, skill_id in ((sheets, "sheet_identity"), (revisions, "revision_scope"), (links, "page_relationships")):
                skills._validate_subskill_output(registry[skill_id], proposal, skills.load_subskill_registry())

    def test_room_boundaries_come_from_traced_outlines_without_a_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "room_use_resolution.json").write_text(json.dumps({"records": [
                {"room_id": "room-use:ground:kitchen", "original_label": "Kitchen", "status": "resolved", "space_scope": "comfort_hvac"},
                {"room_id": "room-use:ground:freezer", "original_label": "Freezer", "status": "excluded", "space_scope": "refrigeration_process"},
                {"room_id": "room-use:ground:shop", "original_label": "Shop", "status": "resolved", "space_scope": "comfort_hvac"},
                {"room_id": "room-use:ground:sign", "original_label": "Sign", "status": "excluded", "space_scope": "not_a_room"}]}))
            traced = {"room-use:ground:kitchen": {"area_m2": 104.904, "page": 20, "proof_id": "geometry_1",
                                                  "calibration": {"mm_per_px": 14.10297, "status": "agreed", "source": "printed", "x": 1}},
                      "room-use:ground:freezer": {"area_m2": 7.88, "page": 20, "proof_id": "geometry_2", "calibration": {"mm_per_px": 14.1}}}
            subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "room_boundaries_areas")
            from backend import reviewer_room_geometry_service
            with patch.object(reviewer_room_geometry_service, "current_traced_areas", return_value=traced):
                proposal = skills._traced_rooms_proposal(subskill, {"id": "j", "review_dir": folder}, "0" * 64)
        rooms = {row["label"]: row for row in proposal["proposal_fields"]["geometry_candidates"]}
        self.assertEqual(sorted(rooms), ["Freezer", "Kitchen", "Shop"])      # refrigeration rooms stay; "not a room" goes
        self.assertEqual((rooms["Kitchen"]["area_m2"], rooms["Kitchen"]["scale_mm_per_px"], rooms["Kitchen"]["page"]), (104.9, 14.103, 20))
        self.assertEqual(rooms["Kitchen"]["calibration"], {"source": "printed", "status": "agreed"})
        self.assertIsNone(rooms["Shop"]["area_m2"])
        self.assertEqual(rooms["Shop"]["unresolved_fields"], ["area_m2"])
        self.assertEqual(proposal["unresolved_fields"], ["Shop: no traced outline, so no area yet."])
        skills._validate_subskill_output(subskill, proposal, skills.load_subskill_registry())

    def test_airflow_tasks_see_their_own_airflow_records_and_only_the_gains_field_they_use(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "airflow_resolution.json").write_text(json.dumps({"records": [
                {"airflow_id": "a1", "air_path_type": "outside_air"}, {"airflow_id": "a2", "air_path_type": "infiltration"},
                {"airflow_id": "a3", "air_path_type": "process_exhaust"}, {"airflow_id": "a4", "air_path_type": "make_up_air"}]}))
            (root / "internal_gains_resolution.json").write_text(json.dumps({"records": [{"room_id": "r1", "fields": {
                "occupancy_count": {"value": 40}, "lighting_load_w": {"value": 900}, "equipment": {"value": []}}}]}))
            project = {"id": "j", "review_dir": folder}
            outside = skills._subskill_records("outside_air", project)[0]["outside_air"]
            exhaust = skills._subskill_records("process_exhaust", project)[0]["exhaust"]
        self.assertEqual([row.get("airflow_id") for row in outside if row.get("airflow_id")], ["a1"])
        self.assertEqual([row.get("airflow_id") for row in exhaust if row.get("airflow_id")], ["a3", "a4"])
        self.assertEqual([row["fields"] for row in outside if "fields" in row], [{"occupancy_count": {"value": 40}}])
        self.assertEqual([row["fields"] for row in exhaust if "fields" in row], [{"equipment": {"value": []}}])

    def test_the_review_hides_the_page_register_and_address_bookkeeping_and_gives_blocked_reasons(self):
        with tempfile.TemporaryDirectory() as folder:
            project, web = self.project(folder), Web()
            root = Path(folder)
            catalog = skills.load_catalog()
            source_fp = skills._source_fingerprint(skills._project_paths(project), catalog)
            manifest = skills._new_manifest(catalog, source_fp, "pdf_review")
            manifest.update({"run_id": "r", "status": "needs_review"})
            manifest["subskills"]["weather_source_matching"].update({"status": "blocked",
                "validation_detail": "Proposal passed schema and citation validation.",
                "remediation": "Confirm the project address and design basis, then run the location resolver."})
            (root / "skill_workflow_run.json").write_text(json.dumps(manifest))
            proposals = root / "skill_workflow_runs" / "r" / "proposals"
            proposals.mkdir(parents=True)
            (proposals / "sheet_identity.json").write_text(json.dumps({"subskill_id": "sheet_identity", "status": "needs_review",
                "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "001", "title": "COVER"}]}}))
            (proposals / "address_confirmation.json").write_text(json.dumps({"subskill_id": "address_confirmation", "status": "needs_review",
                "unresolved_fields": ["confirmed_address", "confirmation_actor", "confirmation_time", "consent_ref"],
                "proposal_fields": {"confirmed_address": None, "confirmation_actor": None, "confirmation_time": None, "consent_ref": None}}))
            with patch.object(skills, "_case_file_route", return_value=True):
                review = skills.get(web, project)
            with patch.object(skills, "_case_file_route", return_value=False):
                legacy = skills.get(web, project)
        shown = {(row["subskill_id"], row["field"]) for row in review["findings"]}
        self.assertNotIn("sheet_identity", {subskill for subskill, _ in shown})        # page register: not for review
        self.assertEqual({field for subskill, field in shown if subskill == "address_confirmation"}, {"confirmed_address"})
        self.assertIn("sheet_identity", {row["subskill_id"] for row in legacy["findings"]})   # without page reading it stays
        weather = next(row for row in review["issues"] if row["name"] == "weather_source_matching")
        self.assertEqual(weather["reason"], "Confirm the project address and design basis, then run the location resolver.")

    def test_over_budget_task_is_blocked_without_contacting_provider(self):
        original_factory, original_groups = skills.SKILL_PROVIDER_FACTORY, skills.select_page_groups
        calls = []

        def factory(_model):
            calls.append("created")
            raise AssertionError("An over-budget task must not create or call a provider.")

        try:
            skills.SKILL_PROVIDER_FACTORY = factory
            skills.select_page_groups = lambda *_args: [{"group_id": "selected-floor-plan", "pages": [{"page": 1}]}]
            with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"ARCHIE_SKILL_PROMPT_MAX_CHARS": "1000"}):
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"source_files": {}}), encoding="utf-8")
                (root / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 1, "title": "Proposed Floor Plan"}]}), encoding="utf-8")
                (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True,
                    "selected_group_ids": ["selected-floor-plan"], "model": "test-model"}), encoding="utf-8")
                task = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "sheet_identity")
                result = skills._execute_subskill(task, {"id": "p", "review_dir": str(root)}, {}, "a" * 64)
                attempt_record = result.pop("_attempt_record")
                checked = skills._validate_subskill_output(task, result, skills.load_subskill_registry(), allowed_pages={1})
        finally:
            skills.SKILL_PROVIDER_FACTORY, skills.select_page_groups = original_factory, original_groups
        self.assertEqual(calls, [])
        self.assertEqual(checked["status"], "blocked")
        self.assertEqual(checked["provider_kind"], "prompt_budget_blocked")
        self.assertIn("no AI provider was contacted", checked["remediation"][0])
        self.assertEqual(attempt_record["prompt_budget"]["status"], "over_budget")
        self.assertEqual(attempt_record["prompt_budget"]["budget_chars"], 1000)
        self.assertEqual(attempt_record["raw_record"]["provider"], "none_prompt_over_budget")

    def test_phase1_envelope_scope_is_room_identity_and_site_only(self):
        catalog = skills.load_catalog()
        registry = skills.load_subskill_registry()
        selected = skills._scoped_subskill_ids(registry, set(catalog["enabled_skill_ids"]), "phase1_envelope")
        self.assertEqual(selected, {"sheet_identity", "revision_scope", "page_relationships",
                                    "site_clue_extraction", "address_confirmation", "room_identity_use"})

    def test_phase1_address_uses_sole_cited_candidate_without_claiming_person_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            spec = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "address_confirmation")
            spec = {**spec, "_phase1_autonomous": True}
            candidate = {"subskill_id": "site_clue_extraction", "confidence": 0.94,
                "citations": [{"id": "site-page-5", "page": 5, "excerpt": "Project site: 12 Main Road, Gosford NSW 2250"}],
                "proposal_fields": {"site_candidates": [{"address": "12 Main Road, Gosford NSW 2250"}]}}
            with patch.object(skills, "_shared_evidence_packet", return_value=({}, [])), \
                    patch.object(skills, "_reused_proposal", return_value=None):
                result = skills._execute_subskill(spec, project,
                    {"site_clue_extraction": {"proposal": candidate}}, "source-fingerprint")
            self.assertEqual(result["proposal_fields"]["confirmed_address"], "12 Main Road, Gosford NSW 2250")
            self.assertIsNone(result["proposal_fields"]["confirmation_actor"])
            self.assertIn("no person confirmation is represented", result["inferences"][0]["method"])
            self.assertEqual(result["inferences"][0]["citation_ids"], ["site-page-5"])

    def test_construction_packet_includes_consented_indexed_material_schedule_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 23, "title": "Dimensioned Top View Plan",
                 "structured_content": {"markdown": "Casa Nova Gosford Finishes Schedules 00.04a"}},
            ]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 23, "proposed_role": "supporting_geometry_plan", "selection": "supporting_context",
                 "relevance": {"room_geometry": 0.7}},
            ]}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": {"23": {
                "page_type": "schedule", "title": "Finishes Schedules", "drawing_number": "00.04a",
                "information": [{"kind": "materials_construction", "what": "FT1 engineered timber floor.",
                                 "evidence": "Floor schedule, FT1 row."}],
            }}}), encoding="utf-8")
            proposal, _ = skills._shared_evidence_packet({"id": "construction_matching"}, project)
            self.assertEqual(proposal["indexed_fact_evidence"], [{
                "page": 23, "drawing_number": "00.04a", "title": "Finishes Schedules",
                "information": [{"kind": "materials_construction", "what": "FT1 engineered timber floor.",
                                 "evidence": "Floor schedule, FT1 row."}],
            }])

    def test_ceiling_packet_includes_indexed_height_facts_with_source_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [
                {"page": 36, "title": "Reflected Ceiling Plan",
                 "structured_content": {"markdown": "Casa Nova reflected ceiling plan"}},
            ]}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 36, "proposed_role": "reflected_ceiling_plan", "selection": "primary_context",
                 "relevance": {"ceiling_lighting": 0.9}},
            ]}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": {"36": {
                "page_type": "reflected_ceiling_plan", "title": "Reflected Ceiling Plan", "drawing_number": "01.05",
                "information": [{"kind": "ceiling_height", "what": "Main dining area has open ceiling at 3200 mm.",
                                 "evidence": "RCP 3200 marker."}],
            }}}), encoding="utf-8")
            proposal, _ = skills._shared_evidence_packet({"id": "ceiling_height_volume"}, project)
            self.assertEqual(proposal["indexed_fact_evidence"][0]["page"], 36)
            self.assertEqual(proposal["indexed_fact_evidence"][0]["information"][0]["what"],
                             "Main dining area has open ceiling at 3200 mm.")

    def test_surface_area_packet_omits_unrelated_finish_schedule_index_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            pages = [
                {"page": 23, "title": "Finishes Schedules", "drawing_number": "00.04a",
                 "structured_content": {"markdown": "Finishes Schedules"}},
                {"page": 36, "title": "Reflected Ceiling Plan", "drawing_number": "01.05",
                 "structured_content": {"markdown": "Reflected Ceiling Plan"}},
            ]
            (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": pages}}), encoding="utf-8")
            (root / "drawing_coverage.json").write_text(json.dumps({"page_roles": [
                {"page": 23, "proposed_role": "supporting_geometry_plan", "selection": "supporting_context"},
                {"page": 36, "proposed_role": "reflected_ceiling_plan", "selection": "primary_context"},
            ]}), encoding="utf-8")
            (root / "page_inventory.json").write_text(json.dumps({"pages": {
                "23": {"page_type": "schedule", "title": "Finishes Schedules"},
                "36": {"page_type": "reflected_ceiling_plan", "title": "Reflected Ceiling Plan"},
            }}), encoding="utf-8")
            packet, _ = skills._shared_evidence_packet({"id": "surface_area"}, project)
            page_numbers = {row["page"] for row in packet["page_index"]}
            self.assertIn(36, page_numbers)
            self.assertNotIn(23, page_numbers)

    def test_surface_inventory_prompt_receives_coordinate_frames(self):
        captured = {}
        class Provider:
            def propose(self, prompt, image_paths=()):
                captured["prompt"] = prompt
                return {"status": "needs_review"}

        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "page.png"
            image.write_bytes(b"image")
            expected_frame = {"physical_page": 32, "canonical_image_width_px": 2977,
                              "coordinate_transform": "canonical_x = attached_x"}
            def bounded(_subskill, _dependencies, _evidence, image_frames=()):
                captured["frames"] = image_frames
                return "prompt with frames", {"status": "within_budget"}
            with patch.object(skills, "_relevant_consented_images", return_value=[{"page": 32, "path": image}]), \
                 patch.object(skills, "_attached_image_coordinate_frames", return_value=[expected_frame]), \
                 patch.object(skills, "_bounded_proposal_prompt", side_effect=bounded):
                attempt = {}
                skills._call_skill_provider({"id": "surface_inventory"}, {"id": "frames", "review_dir": folder},
                    {}, {}, attempt, Provider, "test")
        self.assertEqual(captured["prompt"], "prompt with frames")
        self.assertEqual(captured["frames"], [expected_frame])

    def test_grouped_surface_inventory_prompt_receives_coordinate_frames(self):
        expected_frame = {"physical_page": 1, "canonical_image_width_px": 3000,
                          "coordinate_transform": "canonical_x = attached_x"}
        captured = {}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = self.project(root)
            (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": "frame-group", "subskills": {}}), encoding="utf-8")
            case = {"attached_pages": [1], "readings": [], "page_index": [], "extracted": [], "job": {}}
            def send(_project, _purpose, prompt, _images, _record):
                captured["prompt"] = prompt
                return {"results": {}}
            with patch("backend.skill_case_file.build_for", return_value=(case, [Path("/tmp/page.png")])), \
                 patch.object(skills, "_attached_image_coordinate_frames", return_value=[expected_frame]), \
                 patch.object(skills, "_send_case_file_prompt", side_effect=send):
                skills._ask_group("opaque_envelope", ["surface_inventory", "construction_matching"],
                    project, {"run_id": "frame-group", "subskills": {}}, {})
        self.assertIn('"canonical_image_width_px":3000', captured["prompt"])

    def test_consented_ai_worker_uses_focused_instructions_and_downgrades_to_draft(self):
        original_factory, original_groups = skills.SKILL_PROVIDER_FACTORY, skills.select_page_groups
        captured = {}

        class Provider:
            def propose(self, prompt, image_paths=()):
                captured["prompt"] = prompt
                captured["images"] = image_paths
                return {"status": "resolved", "affected_ids": ["sheet:201"],
                    "observations": [{"text": "Proposed floor plan", "page": 1}], "inferences": [],
                    "citations": [{"page": 1, "drawing_identity": "201", "excerpt": "PROPOSED FLOOR PLAN"}],
                    "confidence": 0.94, "alternatives": [], "unresolved_fields": [], "remediation": [],
                    "proposal_fields": {"page_identities": [{"physical_page": 1, "drawing_number": "201",
                        "title": "Proposed Floor Plan", "drawing_type": "floor_plan", "alternatives": []}]}}

        try:
            skills.SKILL_PROVIDER_FACTORY = lambda _model: Provider()
            skills.select_page_groups = lambda *_args: [{"group_id": "selected-floor-plan", "pages": [{"page": 1}]}]
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "ai_input.json").write_text(json.dumps({"source_files": {}}), encoding="utf-8")
                (root / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 1, "title": "Proposed Floor Plan"}]}), encoding="utf-8")
                (root / "vision_extraction_settings.json").write_text(json.dumps({"owner_opt_in": True,
                    "selected_group_ids": ["selected-floor-plan"], "model": "test-model"}), encoding="utf-8")
                (root / "vision_response.json").write_text(json.dumps({"result": {"entities": []}}), encoding="utf-8")
                image_dir = root / "vision_extraction_runs" / "last" / "pages"
                image_dir.mkdir(parents=True)
                image = image_dir / "page-1.png"
                image.write_bytes(b"test-image")
                manifest = image_dir.parent / "request_manifest.json"
                manifest.write_text(json.dumps({"groups": [{"pages": [
                    {"page": 1, "title": "Proposed Floor Plan", "image_path": str(image)},
                    {"page": 2, "title": "Unselected Section", "image_path": str(image)},
                ]}]}), encoding="utf-8")
                (root / "vision_extraction_job.json").write_text(json.dumps({"manifest_path": str(manifest)}), encoding="utf-8")
                task = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "sheet_identity")
                result = skills._execute_subskill(task, {"id": "p", "review_dir": str(root)}, {}, "a" * 64)
                checked = skills._validate_subskill_output(task, result, skills.load_subskill_registry(), allowed_pages={1})
                self.assertEqual(checked["status"], "provisional")
                self.assertIn("physical PDF page", captured["prompt"])
                self.assertIn("sheet_identity", captured["prompt"])
                self.assertNotIn(folder, captured["prompt"])
                self.assertEqual([item["page"] for item in captured["images"]], [1])
        finally:
            skills.SKILL_PROVIDER_FACTORY, skills.select_page_groups = original_factory, original_groups


if __name__ == "__main__":
    unittest.main()
