"""Runtime skill catalog, dependency and project-run lifecycle tests."""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import skill_workflow_service as skills


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
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": [{"page": 1}]}}), encoding="utf-8")
        (root / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 1, "drawing_number": "201", "title": "Proposed Floor Plan", "identity_status": "confirmed"}]}), encoding="utf-8")
        return {"id": "skills-test-" + root.name, "review_dir": str(root)}

    def test_catalog_contracts_dependencies_and_full_enablement(self):
        catalog = skills.load_catalog()
        self.assertEqual(len(catalog["enabled_skill_ids"]), 10)
        self.assertTrue(all(row["enabled"] for row in catalog["skills"]))
        self.assertTrue(skills.validate_catalog(catalog))
        registry = skills.load_subskill_registry()
        self.assertEqual(len(registry["subskills"]), 44)
        self.assertTrue(skills.validate_subskill_registry(catalog, registry))
        self.assertTrue(all(row.get("task") and row.get("proposal_fields") and row.get("constraints") for row in registry["subskills"]))
        with self.assertRaisesRegex(ValueError, "cycle"):
            skills.validate_catalog({**catalog, "output_contracts": {"a": {"type": "object", "required": []}}, "skills": [
                {"id": "a", "version": 1, "purpose": "a", "depends_on": ["b"], "subskills": [], "output_contract": "a", "resolver_handoff": "a"},
                {"id": "b", "version": 1, "purpose": "b", "depends_on": ["a"], "subskills": [], "output_contract": "a", "resolver_handoff": "b"},
            ], "enabled_skill_ids": ["a"], "pilot_skill_ids": ["a"]})

    def test_room_geometry_skill_proposal_is_adapted_for_existing_resolver(self):
        from backend.calculation_extraction_service import _room_geometry_skill_proposals

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_id = "skill-run-test"
            (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": run_id,
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

        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0]["geometry"]["scale_mm_per_px"], 300)
        self.assertEqual(adapted[0]["geometry"]["dimension_wall_links"][0]["target_wall_id"], "w1")
        self.assertEqual(adapted[0]["source_pages"], [2, 3])
        self.assertEqual({row["page"] for row in adapted[0]["evidence"]}, {2, 3})

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
                ], "dimension_candidates": [{"candidate_id": "P20-D1", "value_mm": 19000}]},
            ]}}), encoding="utf-8")
            (root / "dimension_wall_matches.json").write_text(json.dumps({"pages": [
                {"page": 20, "summary": {"dimension_text_count": 1}, "dimension_span_candidates": [{"dimension_candidate_id": "P20-D1", "value_mm": 19000}]},
            ]}), encoding="utf-8")
            (root / "spatial_ocr.json").write_text(json.dumps({"pages": [
                {"page": 20, "scale_candidates": [{"text": "1:100"}], "room_label_candidates": []},
            ]}), encoding="utf-8")

            subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "room_boundaries_areas")
            evidence, _ = skills._shared_evidence_packet(subskill, project)

        self.assertEqual(evidence["room_candidates"][0]["preliminary_profile_id"], "hospitality")
        self.assertEqual(evidence["room_candidates"][0]["room_id"], "room-use:unassigned-level:bar")
        self.assertEqual(evidence["vector_geometry_pages"][0]["page"], 20)
        self.assertEqual(evidence["vector_geometry_pages"][0]["line_candidates"][0][0], "P20-W1")
        self.assertEqual(evidence["dimension_evidence"][0]["dimension_span_candidates"][0]["value_mm"], 19000)
        self.assertEqual(evidence["spatial_room_evidence"][0]["scale_candidates"][0]["text"], "1:100")

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
                started = skills.post(web, project, {"action": "start"})
                self.assertIn(started["status"], {"queued", "running", "needs_review"})
                deadline = time.time() + 30
                while time.time() < deadline:
                    state = skills.get(web, project)
                    if state["status"] in {"needs_review", "completed", "failed", "blocked"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(state["status"], "needs_review")
                self.assertEqual(len(state["stages"]), 10)
                self.assertEqual(state["stages"][0]["label"], "Drawing set and page mapping")
                self.assertEqual(state["stages"][2]["label"], "Rooms, geometry, and gains")
                self.assertTrue(all("id" not in row for row in state["stages"]))
                manifest = json.loads((Path(folder) / "skill_workflow_run.json").read_text())
                self.assertEqual(len(manifest["subskills"]), 44)
                self.assertTrue(all(manifest["subskills"][key]["status"] != "not_enabled" for key in (
                    "sheet_identity", "revision_scope", "page_relationships", "room_identity_use", "room_boundaries_areas",
                    "ceiling_height_volume", "occupancy_seating", "lighting_evidence", "equipment_evidence", "schedule_evidence")))
                self.assertEqual(manifest["preparation"]["status"], "completed")
                self.assertNotIn(folder, json.dumps(state))
                repeated = skills.post(web, project, {"action": "start"})
                self.assertEqual(repeated["run_id"], state["run_id"])
                (Path(folder) / "drawing_coverage.json").write_text(json.dumps({"pages": [{"page": 2}]}), encoding="utf-8")
                self.assertEqual(skills.get(web, project)["status"], "stale")
        finally:
            skills._execute_subskill = original_execute
            skills._ensure_room_evidence = original_prepare

    def test_failed_subskill_blocks_only_dependents(self):
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
                skills.post(web, project, {"action": "start"})
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
        finally:
            skills._execute_subskill = original_execute
            skills._ensure_room_evidence = original_prepare

    def test_independent_pilot_subskills_run_concurrently(self):
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
                skills.post(web, project, {"action": "start"})
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
                old = skills._new_manifest(catalog, skills._source_fingerprint(paths, catalog))
                old.update({"status": "running", "run_id": "orphaned-run"})
                skills._write_manifest(paths["manifest"], old)

                response = skills.post(web, project, {"action": "retry"})

                self.assertNotEqual(response["run_id"], "orphaned-run")
                self.assertFalse(response["deduplicated"])
                self.assertEqual(skills._read(paths["manifest"], {}).get("run_id"), response["run_id"])
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
        with self.assertRaisesRegex(ValueError, "unknown_page"):
            skills._validate_subskill_output(task, proposal, registry, allowed_pages={1})

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

    def test_qualitative_or_percentage_confidence_is_downgraded_to_review(self):
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
