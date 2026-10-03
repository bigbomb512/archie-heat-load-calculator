#!/usr/bin/env python3
"""Current calibrated reviewer areas can enter the draft-only assembler."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, room_use_resolution
from ai.geometry_resolution import fingerprint
from backend import ai_preliminary_service, model_input_resolution_service, reviewer_room_geometry_service


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


class Web:
    def update_project(self, _project):
        return None


def write_fixture(root, *, calibration_status="agreed", current_pdf="pdf-current", trace_pdf="pdf-current", scope="comfort_hvac"):
    label, level = ("Cool Room", "Level 1") if scope == "refrigeration_process" else ("Shop", "Level 1")
    room_id = room_use_resolution.room_identity(label, level)
    room_use = {"records": [{"room_id": room_id, "original_label": label, "level_name": level,
                             "space_scope": scope, "preliminary_profile_id": "retail",
                             "taxonomy_id": "refrigeration_process" if scope == "refrigeration_process" else "retail",
                             "status": "resolved"}]}
    page = {"page": 1, "vector_page": "unchanged"}
    source_fingerprints = {"source_pdf": trace_pdf, "vector_page": fingerprint(page)}
    calibration = {"status": calibration_status,
                   "mm_per_px": 10.0 if calibration_status in {"agreed", "declared_scale_rejected"} else None,
                   "source": "reviewer_read_printed_dimension", "dimension_value_mm": 100.0,
                   "dimension_points_image_px": [[1, 1], [11, 1]]}
    trace = {"trace_id": f"trace-{label.casefold().replace(' ', '-')}", "room_id": room_id,
             "room_label": label, "level_name": level, "page": 1,
             "points_image_px": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
             "snapped_line_ids": [None] * 5, "calibration": calibration,
             "reviewer": "QA", "source_fingerprints": source_fingerprints}
    trace_id = trace["trace_id"]
    entity = {"entity_id": f"proof-{trace_id}", "room_source_id": room_id,
              "kind": "room_geometry_proof", "extraction_method": "reviewer_traced_boundary",
              "source": {"page": 1, "drawing_number": "A-101"},
              "value": {"reviewer_trace_id": trace_id, "source_fingerprints": source_fingerprints,
                        "calibration": calibration, "area_m2": 25.0}}
    proof = {"proof_id": f"proof-{trace_id}", "room_label": label, "level_name": level,
             "area_m2": 25.0, "calibration": calibration}
    artifacts = {
        "room_use_resolution.json": room_use,
        "ai_input.json": {"source_pdf_fingerprint": current_pdf},
        "vector_geometry.json": {"geometry_key_points": {"pages": [page]}},
        "reviewer_room_geometry.json": {"records": [trace]},
        "geometry_resolution.json": {"entities": [entity], "room_geometry_proofs": [proof]},
        "building_evidence.json": {"spaces": []},
        "ai_preliminary_run.json": {},
    }
    for name, value in artifacts.items():
        (root / name).write_text(json.dumps(value), encoding="utf-8")
    proposal = {"rooms": [{"kind": "room", "room_id": "proposal-room", "label": label, "level_name": level,
                           "preliminary_profile_id": "retail", "space_scope": scope,
                           "page": 1, "evidence": [{"page": 1, "excerpt": label}]}]}
    return room_use, proposal, room_id, trace


def prepare(root, room_use, proposal):
    paths = ai_preliminary_service._paths({"review_dir": str(root)})
    return ai_preliminary_service._prepare_preliminary_proposal(paths, proposal, room_use, {"entities": []})


def write_conflicting_trace(root, trace):
    trace_path = root / "reviewer_room_geometry.json"
    trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
    second_trace = json.loads(json.dumps(trace_data["records"][0]))
    second_trace["trace_id"] = "trace-second"
    trace_data["records"].append(second_trace)
    trace_path.write_text(json.dumps(trace_data), encoding="utf-8")

    geometry_path = root / "geometry_resolution.json"
    geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
    second_proof_id = "proof-trace-second"
    second_entity = json.loads(json.dumps(geometry["entities"][0]))
    second_entity["entity_id"] = second_proof_id
    second_entity["value"]["reviewer_trace_id"] = "trace-second"
    second_entity["value"]["area_m2"] = 30.0
    geometry["entities"].append(second_entity)
    second_proof = json.loads(json.dumps(geometry["room_geometry_proofs"][0]))
    second_proof.update({"proof_id": second_proof_id, "area_m2": 30.0})
    geometry["room_geometry_proofs"].append(second_proof)
    geometry_path.write_text(json.dumps(geometry), encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, room_id, trace = write_fixture(root)
        current = reviewer_room_geometry_service.current_traced_areas(root)
        check("shared helper returns calibrated area by room-use identity",
              current[room_id]["area_m2"] == 25.0 and current[room_id]["trace_id"] == trace["trace_id"])
        prepared = prepare(root, room_use, proposal)
        check("trace area enters calculation proposal as provisional with trace citation",
              prepared["rooms"][0]["area_m2"] == 25.0
              and prepared["rooms"][0]["area_origin"] == "reviewer_traced"
              and prepared["rooms"][0]["area_verification_status"] == "provisional"
              and prepared["rooms"][0]["evidence"][-1]["page"] == 1)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        material = assembled["material"]
        area_field = next(row for row in assembled["materialized_fields"] if row["field"] == "area_m2")
        check("traced room materializes with exact area, trace provenance, and provisional status",
              len(material["hourly_load_model"]["rooms"]) == 1
              and material["hourly_load_model"]["rooms"][0]["area_m2"] == 25.0
              and area_field["origin"] == "reviewer_traced"
              and area_field["verification_status"] == "provisional"
              and area_field["reviewer_trace_id"] == trace["trace_id"]
              and any(row.get("page") == 1 for row in area_field["evidence"]))
        check("traced room materializes a schedule", bool(material["schedule_library"]["schedules"]))
        project = {"id": "traced-preliminary", "review_dir": str(root)}
        hydrated, _missing = model_input_resolution_service._hydrate_preliminary_artifacts(
            Web(), project, model_input_resolution_service._paths(project), assembled,
        )
        schedule_artifact = next(row for row in hydrated if row["key"] == "schedule_library")
        saved_schedule_library = json.loads((root / "schedule_library.json").read_text(encoding="utf-8"))
        check("traced room schedule library hydrates from the preliminary input set",
              schedule_artifact["hydrated"] and schedule_artifact["can_hydrate"]
              and bool(saved_schedule_library["schedules"]))

    for case, overrides in (("stale", {"current_pdf": "pdf-changed"}),
                            ("uncalibrated", {"calibration_status": "unresolved"})):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            room_use, proposal, _room_id, _trace = write_fixture(root, **overrides)
            proposal["rooms"][0].update({"area_origin": "reviewer_traced", "area_verification_status": "confirmed",
                                         "reviewer_trace_id": "stale-provider-claim"})
            prepared = prepare(root, room_use, proposal)
            check(f"{case} trace remains excluded with actionable draft remediation",
                  not prepared["rooms"] and prepared["issues"][0]["remediation"] ==
                  "Trace and calibrate the room, then accept its area in the calculator draft.")

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, _room_id, _trace = write_fixture(root, scope="refrigeration_process")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        check("current traced refrigeration room stays excluded from comfort HVAC",
              not assembled["material"]["hourly_load_model"]["rooms"]
              and assembled["excluded_spaces"][0]["scope"] == "refrigeration_process")

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, room_id, trace = write_fixture(root)
        write_conflicting_trace(root, trace)
        project = {"id": "conflicted-trace", "review_dir": str(root)}
        eligible, missing = model_input_resolution_service._room_area_coverage(
            model_input_resolution_service._paths(project))
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        check("traces more than 2% apart leave the room missing at the model-input gate",
              len(eligible) == 1 and [row["room_id"] for row in missing] == [room_id])
        check("conflicted trace area is excluded from preliminary calculation rooms",
              not prepared["rooms"] and not assembled["material"]["hourly_load_model"]["rooms"])

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, _room_id, _trace = write_fixture(root)
        (root / "reviewer_room_geometry.json").write_text("{malformed", encoding="utf-8")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        check("malformed reviewer trace artifact does not prevent assembly and adds a review issue",
              not prepared["rooms"]
              and any(issue.get("reason") == "Reviewer trace artifact could not be read; trace-derived areas were ignored."
                      for issue in prepared["issues"])
              and not assembled["material"]["hourly_load_model"]["rooms"])


if __name__ == "__main__":
    main()
