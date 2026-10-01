#!/usr/bin/env python3
"""Verify runtime room/gain proposals reach the established resolvers."""

import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import room_use_resolution
from backend.ai_preliminary_service import _proposal_for_resolution, _resolve_room_uses


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def main():
    with tempfile.TemporaryDirectory(prefix="archie-skill-bridge-") as temporary:
        root = Path(temporary)
        skill_dir = root / "skill_workflow_runs" / "run-1" / "proposals"
        skill_dir.mkdir(parents=True)
        (root / "ai_preliminary_run.json").write_text(json.dumps({"local_room_inference_proposal": {"rooms": [
            {"kind": "room", "room_id": "room-use:unassigned-level:shop", "label": "Shop", "level_name": "Unassigned level", "page": 20},
            {"kind": "room", "room_id": "room-use:unassigned-level:bar", "label": "Bar", "level_name": "Unassigned level", "page": 20},
            {"kind": "room", "room_id": "room-use:unassigned-level:kitchen", "label": "Kitchen", "level_name": "Unassigned level", "page": 20},
        ]}}))
        (root / "skill_workflow_run.json").write_text(json.dumps({"run_id": "run-1", "subskills": {
            "room_identity_use": {"status": "needs_review"}, "room_boundaries_areas": {"status": "needs_review"}
        }}))
        identity = {
            "citations": [{"citation_id": "c19", "physical_pdf_page": 19, "drawing_identity": "H509 / 201 / B", "title": "PROPOSED FLOOR LAYOUT", "excerpt_or_crop": "140-seat schedule"}],
            "proposal_fields": {"rooms": [
                {"room_id": "room-use:unassigned-level:shop", "original_label": "Shop", "taxonomy_id": "dining", "evidence_page_ids": [19]},
                {"room_id": "room-use:unassigned-level:bar", "original_label": "Bar", "taxonomy_id": "dining", "evidence_page_ids": [19]},
                {"room_id": "room-use:unassigned-level:kitchen", "original_label": "Kitchen", "taxonomy_id": "kitchen", "evidence_page_ids": [19]},
            ]},
            "inferences": [
                {"field": "rooms[room-use:unassigned-level:shop].taxonomy_id", "value": "dining", "method": "Furniture and adjacency indicate a customer hospitality area.", "confidence": .9},
                {"field": "rooms[room-use:unassigned-level:bar].taxonomy_id", "value": {"taxonomy_id": "dining", "boundary_status": "Functional zone; independent room separation unresolved"}, "method": "Functional area only.", "confidence": .8},
                {"field": "rooms[room-use:unassigned-level:kitchen].taxonomy_id", "value": "kitchen", "method": "Cooking fixtures and notes.", "confidence": .97},
            ],
            "unresolved_fields": [{"field": "rooms[room-use:unassigned-level:shop].original_label", "reason": "Shop is not a visible label."}],
            "observations": [{"citation_ids": ["c19"], "detail": "Drawing 201 seating schedule lists 140 seats."}],
        }
        geometry = {"proposal_fields": {"geometry_candidates": [
            {"room_id": "room-use:unassigned-level:bar", "unresolved_fields": ["parent_connected_zone_boundary"]}
        ]}}
        (skill_dir / "room_identity_use.json").write_text(json.dumps(identity))
        (skill_dir / "room_boundaries_areas.json").write_text(json.dumps(geometry))

        proposal = _proposal_for_resolution({"root": root, "run": root / "ai_preliminary_run.json"})
        by_label = {row["label"]: row for row in proposal["rooms"]}
        check("room identity skill enriches existing room records", by_label["Kitchen"]["room_use_category"] == "kitchen")
        check("room identity skill citations become resolver evidence", by_label["Shop"]["evidence"][-1]["page"] == 19)
        check("skill preserves a directly observed seating count", by_label["Shop"]["seat_count"] == 140)
        check("unverified source label is not allowed to win taxonomy precedence", by_label["Shop"]["direct_label_verified"] is False)
        check("functional bar subarea is marked non-counting without a separate boundary", by_label["Bar"]["non_counting_functional_subarea"] and by_label["Bar"]["calculation_scope_override"] == "unresolved_scope")
        use = room_use_resolution.resolve({}, proposal=proposal, source_fingerprints={})
        shop = next(row for row in use["records"] if row["original_label"] == "Shop")
        bar = next(row for row in use["records"] if row["original_label"] == "Bar")
        check("authoritative room-use artifact uses the skill's dining classification", shop["taxonomy_id"] == "dining")
        check("non-counting functional area cannot add duplicate comfort load", bar["status"] == "excluded" and bar["space_scope"] == "unresolved_scope")

        resolved_through_service = _resolve_room_uses({"root": root, "run": root / "ai_preliminary_run.json",
            "room_use": root / "room_use_resolution.json", "building": root / "building_evidence.json",
            "vision": root / "vision_response.json"})
        service_shop = next(row for row in resolved_through_service["records"] if row["original_label"] == "Shop")
        check("standard preliminary service path consumes runtime skill classifications", service_shop["taxonomy_id"] == "dining")


if __name__ == "__main__":
    main()
