#!/usr/bin/env python3
"""Current calibrated reviewer areas can enter the draft-only assembler."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, ceiling_volume_resolution, room_use_resolution
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


def prepare(root, room_use, proposal, geometry=None):
    paths = ai_preliminary_service._paths({"review_dir": str(root)})
    return ai_preliminary_service._prepare_preliminary_proposal(paths, proposal, room_use, geometry or {"entities": []})


def set_envelope_trace(root, trace, edges, roof):
    path = root / "reviewer_room_geometry.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    row = next(item for item in artifact["records"] if item["trace_id"] == trace["trace_id"])
    edge_by_index = {edge["index"]: edge for edge in edges}
    complete_edges = [{"index": index, "boundary": edge_by_index.get(index, {}).get("boundary", "unknown")}
                      for index in range(len(row["points_image_px"]) - 1)]
    row.update({"edges": complete_edges, "roof": roof, "envelope_reviewer": "HVAC reviewer", "envelope_declared_at": "2026-10-04T00:00:00Z"})
    path.write_text(json.dumps(artifact), encoding="utf-8")


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
        room_use, proposal, _room_id, trace = write_fixture(root, scope="refrigeration_process")
        set_envelope_trace(root, trace, [{"index": 0, "boundary": "external"}], "exposed")
        proposal["surfaces"] = [{"surface_key": "provider-roof", "physical_type": "roof",
                                 "owner_room_id": "proposal-room", "owner_room_label": "Cool Room",
                                 "owner_level_name": "Level 1", "thermal_role": "external"}]
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        check("current traced refrigeration room stays excluded from comfort HVAC",
              not assembled["material"]["hourly_load_model"]["rooms"]
              and assembled["excluded_spaces"][0]["scope"] == "refrigeration_process"
              and not prepared["surfaces"]
              and prepared["rooms"][0]["area_m2"] == 25.0
              and prepared["rooms"][0]["area_origin"] == "reviewer_traced")

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
        room_use, _proposal, room_id, trace = write_fixture(root)
        room_id = room_use_resolution.room_identity("Bar", "Unassigned level")
        room_use["records"][0].update({"room_id": room_id, "original_label": "Bar",
                                       "level_name": "Unassigned level", "evidence": [{"page": 3, "excerpt": "room-use evidence"}]})
        (root / "room_use_resolution.json").write_text(json.dumps(room_use), encoding="utf-8")
        building_id = "spaces-21-2"
        (root / "building_evidence.json").write_text(json.dumps({"spaces": [
            {"id": building_id, "name": "Bar", "level_name": "", "area_m2": None,
             "evidence": [{"page": 2, "excerpt": "building evidence"}]},
        ]}), encoding="utf-8")
        trace_path = root / "reviewer_room_geometry.json"
        trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
        trace_data["records"][0].update({"room_id": building_id, "room_label": "Bar", "level_name": ""})
        trace_path.write_text(json.dumps(trace_data), encoding="utf-8")
        geometry_path = root / "geometry_resolution.json"
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        geometry["entities"][0]["room_source_id"] = building_id
        geometry["entities"][0]["value"]["room_source_id"] = building_id
        geometry["room_geometry_proofs"][0].update({"room_label": "Bar", "level_name": ""})
        geometry_path.write_text(json.dumps(geometry), encoding="utf-8")

        trace_rooms = reviewer_room_geometry_service._rooms(
            reviewer_room_geometry_service._paths({"review_dir": str(root)}))
        current_records = reviewer_room_geometry_service.current_records(
            reviewer_room_geometry_service._paths({"review_dir": str(root)}))
        traced = reviewer_room_geometry_service.current_traced_areas(root)
        project = {"id": "legacy-building-trace", "review_dir": str(root)}
        eligible, missing = model_input_resolution_service._room_area_coverage(
            model_input_resolution_service._paths(project))
        check("building and room-use duplicates yield one picker row using the room-use identity and merged evidence",
              len(trace_rooms) == 1 and trace_rooms[0]["room_id"] == room_id
              and trace_rooms[0]["label"] == "Bar" and trace_rooms[0]["level_name"] == "Unassigned level"
              and trace_rooms[0]["source_pages"] == [2, 3]
              and {item["excerpt"] for item in trace_rooms[0]["evidence"]} == {"building evidence", "room-use evidence"})
        check("legacy building-ID trace stays current and maps its area to the room-use identity",
              len(current_records) == 1 and current_records[0]["room_id"] == room_id
              and traced[room_id]["area_m2"] == 25.0 and traced[room_id]["trace_id"] == trace["trace_id"])
        check("legacy building-ID trace satisfies the room-use area gate",
              any(row["room_id"] == room_id for row in eligible)
              and not any(row["room_id"] == room_id for row in missing))

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

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, _room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [], "exposed")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        room = assembled["material"]["hourly_load_model"]["rooms"][0]
        roof = next(surface for surface in room["cooling_load"]["envelope_surfaces"] if surface["kind"] == "roof")
        expected_u = ai_preliminary.load_pack()["profiles"]["retail"]["roof_u_w_m2k"]
        report = ai_preliminary.calculate(assembled)
        check("exposed reviewer roof uses traced area and the controlled roof U-value",
              roof["area_m2"] == 25.0 and roof["u_value_w_m2k"] == expected_u
              and roof["orientation"] == "horizontal" and roof["reviewer_trace_id"] == trace["trace_id"])
        check("unknown edges and roof classification keep envelope scope incomplete",
              not report["scope_summary"]["complete_scope"]
              and any(item.get("component_id") == "unclassified_wall_boundaries"
                      and item.get("component") == "Walls — boundary not classified"
                      and item.get("reason") == "4 of 4 wall edges not classified."
                      for item in report["unresolved_room_inputs"])
              and any(item.get("component_id") == "roof_solar"
                      and item.get("component") == "Roof sun — not assessed"
                      for item in report["unresolved_room_inputs"])
              and not any(item.get("component") == "Envelope" for item in report["unresolved_room_inputs"]))

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [{"index": 0, "boundary": "external"},
                                         {"index": 1, "boundary": "internal"},
                                         {"index": 2, "boundary": "internal"},
                                         {"index": 3, "boundary": "internal"}], "not_exposed")
        (root / "ceiling_volume_resolution.json").write_text(json.dumps({"records": [
            {"room_id": ceiling_volume_resolution.room_identity("Shop", "Level 1"),
             "status": "resolved", "ceiling_height_mm": 3000},
        ]}), encoding="utf-8")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        room = assembled["material"]["hourly_load_model"]["rooms"][0]
        wall = next(surface for surface in room["cooling_load"]["envelope_surfaces"] if surface["kind"] == "opaque_wall")
        report = ai_preliminary.calculate(assembled)
        check("external reviewer edge uses calibrated length times resolved ceiling height",
              abs(wall["area_m2"] - 0.3) < 1e-9 and wall["orientation"] == ""
              and wall["u_value_w_m2k"] == ai_preliminary.load_pack()["profiles"]["retail"]["wall_u_w_m2k"])
        check("included unknown-orientation walls are explicitly not assessed for façade solar",
              any(item.get("component") == "external walls — orientation not assessed (no façade solar)"
                  for item in assembled["exclusions"])
              and not report["scope_summary"]["complete_scope"])

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, _room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [{"index": 0, "boundary": "external"}], "not_exposed")
        prepared = prepare(root, room_use, proposal)
        missing_height = prepared["envelope_assessments"][0]
        check("external reviewer edge without ceiling height is listed once as not assessed",
              not prepared["surfaces"]
              and sum(item.get("component_id") == "external_wall_edge_0"
                      for item in missing_height["not_assessed"] + missing_height["excluded"]) == 1
              and any(item.get("component") == "External wall area — not assessed"
                      and item.get("reason") == "Ceiling height unresolved; wall area cannot be derived."
                      for item in missing_height["not_assessed"]))
        set_envelope_trace(root, trace, [{"index": 0, "boundary": "adjacent_tenancy"},
                                         {"index": 1, "boundary": "internal"},
                                         {"index": 2, "boundary": "internal"},
                                         {"index": 3, "boundary": "internal"}], "unknown")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        check("unknown roof stays not assessed while adjacent and internal edges stay excluded",
              any(item.get("component_id") == "roof_exposure" for item in prepared["envelope_assessments"][0]["not_assessed"])
              and {row["component"] for row in prepared["envelope_assessments"][0]["excluded"]} >= {"Adjacent tenancy boundary", "Internal boundary"}
              and not assembled["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_surfaces"])
        boundary_exclusions = [item for item in assembled["exclusions"] if item.get("envelope_exclusion")]
        boundary_ids = [item.get("component_id") for item in boundary_exclusions]
        check("each excluded reviewer boundary is recorded once in known exclusions",
              len(boundary_exclusions) == 4 and len(boundary_ids) == len(set(boundary_ids)))

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, _room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [{"index": index, "boundary": "internal"} for index in range(4)], "not_exposed")
        prepared = prepare(root, room_use, proposal)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            allow_area_fallbacks=False)
        report = ai_preliminary.calculate(assembled)
        check("reviewer-declared internal room clears only the envelope assessment without leaving generic Envelope unresolved",
              assembled["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_not_applicable"]
              and not any(item.get("component_type") == "envelope" for item in report["unresolved_room_inputs"])
              and not any(item.get("component") == "opaque envelope"
                          and "No structurally valid surface geometry" in item.get("reason", "")
                          for item in assembled["exclusions"])
              and not report["scope_summary"]["complete_scope"])

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [], "exposed")
        ledger = {"thermal_surface_ledger": {"surfaces": [{
            "surface_id": "ledger-wall", "owner_room_id": room_id, "owner_room_label": "Shop", "level_name": "Level 1",
            "physical_type": "wall", "thermal_role": "external", "boundary_condition": "outside",
            "external_exposure": "external", "thermal_eligible": True, "status": "provisional",
            "gross_area_m2": 2.0, "opening_coverage_status": "not_applicable", "confidence_score": 0.9,
        }]}}
        prepared = prepare(root, room_use, proposal, geometry=ledger)
        assembled = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared,
                                            geometry_resolution=ledger, allow_area_fallbacks=False)
        surfaces = assembled["material"]["hourly_load_model"]["rooms"][0]["cooling_load"]["envelope_surfaces"]
        check("a ledger wall does not suppress a trace roof",
              len(surfaces) == 2 and any(surface.get("surface_id") == "ledger-wall" for surface in surfaces)
              and any(surface.get("kind") == "roof" for surface in surfaces))

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        room_use, proposal, room_id, trace = write_fixture(root)
        set_envelope_trace(root, trace, [], "exposed")
        ledger = {"thermal_surface_ledger": {"surfaces": [{
            "surface_id": "ledger-roof", "owner_room_id": room_id, "owner_room_label": "Shop", "level_name": "Level 1",
            "physical_type": "roof", "thermal_role": "external", "boundary_condition": "outside",
            "external_exposure": "external", "thermal_eligible": True, "status": "provisional",
            "gross_area_m2": 25.0, "opening_coverage": "not_applicable", "confidence_score": 0.9,
        }]}}
        prepared = prepare(root, room_use, proposal, geometry=ledger)
        check("same-type ledger roof prevents duplicate trace roof while leaving roof solar not assessed",
              not any(row.get("surface_key", "").endswith(":roof") for row in prepared["surfaces"])
              and any(row.get("component_id") == "roof_solar" for row in prepared["envelope_assessments"][0]["not_assessed"]))


if __name__ == "__main__":
    main()
