"""Safe error and create-only hydration checks for model-input resolution."""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import model_input_resolution_service
from ai.geometry_resolution import fingerprint


class Web:
    def safe_link(self, path):
        return "/artifact/" + Path(path).name

    def update_project(self, _project):
        return None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def traced_project(root, *, current_pdf="pdf-current", saved_pdf="pdf-current", calibration_status="agreed", area=25.0):
    room = {"room_id": "shop", "original_label": "Shop", "level_name": "Level 1",
            "space_scope": "comfort_hvac", "status": "resolved"}
    page = {"page": 1, "vector_page": "current"}
    source_fingerprints = {"source_pdf": saved_pdf, "vector_page": fingerprint(page)}
    calibration = {"status": calibration_status, "mm_per_px": 10.0 if calibration_status in {"agreed", "declared_scale_rejected"} else None,
                   "source": "reviewer_read_printed_dimension", "dimension_points_image_px": [[1, 1], [2, 1]],
                   "dimension_value_mm": 10.0}
    trace = {"trace_id": "trace-shop", "room_id": "shop", "page": 1,
             "points_image_px": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
             "snapped_line_ids": [None] * 5, "calibration": calibration, "reviewer": "QA",
             "source_fingerprints": source_fingerprints}
    (root / "room_use_resolution.json").write_text(json.dumps({"records": [room]}), encoding="utf-8")
    (root / "ai_input.json").write_text(json.dumps({"source_pdf_fingerprint": current_pdf}), encoding="utf-8")
    (root / "vector_geometry.json").write_text(json.dumps({"geometry_key_points": {"pages": [page]}}), encoding="utf-8")
    (root / "reviewer_room_geometry.json").write_text(json.dumps({"records": [trace]}), encoding="utf-8")
    proof = {"proof_id": "proof-shop", "room_label": "  SHOP ", "level_name": " LEVEL   1 ",
             "area_m2": area, "calibration": calibration}
    entity = {"entity_id": "proof-shop", "kind": "room_geometry_proof",
              "extraction_method": "reviewer_traced_boundary", "value": {
                  "reviewer_trace_id": "trace-shop", "source_fingerprints": source_fingerprints,
                  "calibration": calibration, "area_m2": area}}
    (root / "geometry_resolution.json").write_text(json.dumps({"entities": [entity], "room_geometry_proofs": [proof]}), encoding="utf-8")
    (root / "building_evidence.json").write_text(json.dumps({"spaces": []}), encoding="utf-8")
    (root / "ai_preliminary_run.json").write_text(json.dumps({}), encoding="utf-8")
    return {"id": "traced-project", "review_dir": str(root)}


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "resolver-errors", "review_dir": str(root)}
        (root / "ai_input.json").write_text(json.dumps({"drawing_set": {"pages": []}}), encoding="utf-8")
        (root / "room_inference_job.json").write_text(json.dumps({"status": "running", "job_id": "room-job"}), encoding="utf-8")
        try:
            model_input_resolution_service.post(Web(), project, {"action": "resolve"})
        except model_input_resolution_service.ModelInputResolutionError as error:
            payload = model_input_resolution_service.error_payload(error)
            check("pending room inference uses 202", error.status_code == 202)
            check("error has actionable fields", payload["code"] == "room_inference_pending" and payload["remediation"] and "artifact" in payload)
        else:
            raise AssertionError("pending room inference did not fail closed")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "resolver-area-coverage", "review_dir": str(root)}
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "shop", "original_label": "Shop", "level_name": "Level 1", "space_scope": "comfort_hvac", "status": "resolved"},
            {"room_id": "kitchen", "original_label": "Kitchen", "level_name": "Level 1", "space_scope": "comfort_hvac_with_process_exception", "status": "resolved"},
        ]}), encoding="utf-8")
        (root / "geometry_resolution.json").write_text(json.dumps({"entities": [
            {"kind": "area", "label": "Shop", "level_candidate": "Level 1", "geometry_status": "ai_estimated", "value": {"area_m2": 120.5}},
        ]}), encoding="utf-8")
        (root / "building_evidence.json").write_text(json.dumps({"spaces": []}), encoding="utf-8")
        (root / "ai_preliminary_run.json").write_text(json.dumps({}), encoding="utf-8")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("geometry coverage matches active areas by room and level", len(eligible) == 2 and [row["room_id"] for row in missing] == ["kitchen"])

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = traced_project(root)
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("current calibrated reviewer trace satisfies area coverage", len(eligible) == 1 and not missing)
        with patch("backend.room_inference_service.get", return_value={"room_inference": {"status": "not_started"}}), \
             patch("backend.model_input_resolution_service.ai_preliminary_service._resolve_from_packs", return_value=None), \
             patch("backend.ahu_resolution_service.post", return_value={}), \
             patch("backend.plant_resolution_service.post", return_value={}), \
             patch("backend.safety_factor_resolution_service.post", return_value={}), \
             patch("backend.model_input_resolution_service.ai_preliminary_service._assemble", return_value={}), \
             patch("backend.model_input_resolution_service._hydrate_preliminary_artifacts", return_value=([{"key": "stubbed"}], [])):
            register, _ = model_input_resolution_service._build(Web(), project)
        check("traced-only project passes the model-input room-area gate", register["required_artifacts"] == [{"key": "stubbed"}])

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = traced_project(root, calibration_status="declared_scale_rejected")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("current two-dimension calibration with rejected declared scale is accepted", len(eligible) == 1 and not missing)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = traced_project(root, calibration_status="unresolved", area=None)
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("uncalibrated reviewer trace does not satisfy area coverage", len(eligible) == 1 and [row["room_id"] for row in missing] == ["shop"])

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = traced_project(root, current_pdf="pdf-new", saved_pdf="pdf-old")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("stale reviewer trace is excluded after source fingerprint changes", len(eligible) == 1 and [row["room_id"] for row in missing] == ["shop"])

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "draft-area", "review_dir": str(root)}
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "shop", "original_label": "Shop", "level_name": "Level 1", "space_scope": "comfort_hvac", "status": "resolved"},
        ]}), encoding="utf-8")
        (root / "hourly_load_model.json").write_text(json.dumps({"floors": [{"floor_id": "f1", "name": "Level 1"}], "rooms": [
            {"room_id": "shop", "name": "Shop", "floor_id": "f1", "area_m2": 25.0,
             "bridge_provenance": {"area_123": {"reviewer": "QA"}}},
        ]}), encoding="utf-8")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("calculator-draft-applied room area satisfies coverage", len(eligible) == 1 and not missing)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "excluded-areas", "review_dir": str(root)}
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "cool", "original_label": "Cool Room", "level_name": "Level 1", "space_scope": "excluded", "status": "excluded"},
            {"room_id": "freezer", "original_label": "Freezer", "level_name": "Level 1", "space_scope": "excluded", "status": "excluded"},
        ]}), encoding="utf-8")
        (root / "hourly_load_model.json").write_text(json.dumps({"floors": [{"floor_id": "f1", "name": "Level 1"}], "rooms": [
            {"room_id": "cool", "name": "Cool Room", "floor_id": "f1", "area_m2": 15.0,
             "bridge_provenance": {"area_cool": {"reviewer": "QA"}}},
            {"room_id": "freezer", "name": "Freezer", "floor_id": "f1", "area_m2": 8.0,
             "bridge_provenance": {"area_freezer": {"reviewer": "QA"}}},
        ]}), encoding="utf-8")
        eligible, missing = model_input_resolution_service._room_area_coverage(model_input_resolution_service._paths(project))
        check("excluded cool room and freezer remain outside area coverage", not eligible and not missing)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project = {"id": "resolver-remediation", "review_dir": str(root)}
        (root / "room_use_resolution.json").write_text(json.dumps({"records": [
            {"room_id": "shop", "original_label": "Shop", "level_name": "Level 1", "space_scope": "comfort_hvac", "status": "resolved"},
        ]}), encoding="utf-8")
        (root / "geometry_resolution.json").write_text(json.dumps({"entities": [], "room_geometry_proofs": []}), encoding="utf-8")
        (root / "building_evidence.json").write_text(json.dumps({"spaces": []}), encoding="utf-8")
        (root / "ai_preliminary_run.json").write_text(json.dumps({}), encoding="utf-8")
        try:
            with patch("backend.model_input_resolution_service.ai_preliminary_service._resolve_from_packs", return_value=None):
                model_input_resolution_service.post(Web(), project, {"action": "resolve"})
        except model_input_resolution_service.ModelInputResolutionError as error:
            check("room-area remediation directs trace, calibration, and draft acceptance",
                  error.code == "room_area_unresolved" and "Trace and calibrate the room, then accept it in the calculator draft" in error.remediation
                  and "Do not use the drawing scale" not in error.remediation)
        else:
            raise AssertionError("missing room area did not fail the gate")


if __name__ == "__main__":
    main()
