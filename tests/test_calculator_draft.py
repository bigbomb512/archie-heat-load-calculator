#!/usr/bin/env python3

"""Domain checks for the evidence-to-calculator bridge."""

from copy import deepcopy
import unittest

from ai.calculator_draft import DraftConflict, apply_calculator_draft, build_calculator_draft, save_review
from ai.hourly_loads import empty_hourly_load_model, empty_schedule_library
from ai.envelope import empty_envelope_library, empty_envelope_model


EVIDENCE = [{"page": 3, "kind": "reviewed_pdf_text", "excerpt": "Ground floor Shop A · AREA 42 m²"}]


def source_data():
    return {
        "thermal": {"zones": [{"name": "Shop A", "ceiling_height_mm": 3000, "status": "direct"}]},
        "building": {
            "source_pdf": "drawing-set.pdf", "levels": [],
            "spaces": [{"id": "spaces-3-1", "name": "Shop A", "area": "42 m²", "level_name": "Ground", "geometry": {"page": 3, "reference": "room-boundary-3-1"}, "geometry_status": "geometry_confirmed", "status": "direct", "evidence": EVIDENCE}],
            "lighting": [{"id": "lighting-3-1", "connected_w": 480, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "equipment": [{"id": "equipment-3-1", "name": "oven", "kind": "cooking", "quantity": 1, "watts": None, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "surfaces": [{"id": "surfaces-3-1", "kind": "external_boundary", "adjacency": "", "geometry": None, "level_name": "Ground", "status": "direct", "evidence": EVIDENCE}],
            "openings": [],
            "constructions": [{"id": "constructions-3-1", "kind": "roof", "reference": "Roof type R1", "thermal_performance": None, "status": "direct", "evidence": EVIDENCE}],
        },
        "coverage": {"levels": [{"level_name": "Ground", "purpose_status": "inferred", "purpose_evidence": EVIDENCE}]},
    }


class CalculatorDraftTests(unittest.TestCase):
    def build(self):
        data = source_data()
        original = deepcopy(data)
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        self.assertEqual(data, original, "Building a draft must not mutate source artifacts.")
        self.assertEqual(draft["schema_version"], 2)
        return draft

    def reviewed(self, draft, ids, decision="accept"):
        return save_review(draft, {cid: {"decision": decision, "reviewer": "ENG-1"} for cid in ids}, draft["revision"])

    def test_builds_stable_cited_topology_and_missing_review_items(self):
        data = source_data(); draft = self.build()
        rebuilt = build_calculator_draft(data["thermal"], data["building"], data["coverage"], draft)
        self.assertEqual(draft["candidates"]["rooms"][0]["candidate_id"], rebuilt["candidates"]["rooms"][0]["candidate_id"])
        self.assertEqual(draft["candidates"]["floors"][0]["citations"][0]["page"], 3)
        self.assertTrue(any("schedule" in item["reason"].lower() for item in draft["review_items"]))
        self.assertFalse(draft["candidates"]["schedules"])

    def test_unassigned_coverage_creates_floor_issue_but_no_floor_candidate(self):
        data = source_data()
        data["coverage"]["levels"] = [{"level_name": "Unassigned level", "page_numbers": [21]}]
        data["building"]["levels"] = []
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        self.assertEqual(draft["candidates"]["floors"], [])
        self.assertTrue(any("confirm the drawing level" in item.get("reason", "").lower()
                            for item in draft["review_items"]))

    def test_pre_classifier_coverage_cannot_build_a_draft(self):
        data = source_data()
        data["coverage"].update(version=4, levels=[{"level_name": "FL 02", "page_numbers": [21]}])
        with self.assertRaisesRegex(ValueError, "Rebuild drawing coverage"):
            build_calculator_draft(data["thermal"], data["building"], data["coverage"])

    def test_approval_requires_fingerprint_and_application_is_idempotent(self):
        draft = self.build()
        ids = [item["candidate_id"] for key in ("floors", "zones", "rooms") for item in draft["candidates"][key]]
        reviewed = self.reviewed(draft, ids)
        outcome = apply_calculator_draft(reviewed, None, empty_hourly_load_model(), empty_schedule_library(), empty_envelope_library(), empty_envelope_model(), "requirements-r1")
        model = outcome["hourly_load_model"]
        self.assertEqual((len(model["floors"]), len(model["zones"]), len(model["rooms"])), (1, 1, 1))
        self.assertEqual(model["rooms"][0]["verification_status"], "confirmed")
        self.assertFalse(outcome["envelope_model"]["active_for_calculation"])
        repeated = apply_calculator_draft(reviewed, None, model, outcome["schedule_library"], outcome["envelope_library"], outcome["envelope_model"], "requirements-r1")
        self.assertFalse(repeated["changed"]["hourly_load_model"])
        self.assertEqual(len(repeated["summary"]["already_present"]), 3)

    def test_changed_evidence_invalidates_old_approval(self):
        data = source_data(); draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        floor = draft["candidates"]["floors"][0]; reviewed = self.reviewed(draft, [floor["candidate_id"]])
        data["building"]["spaces"][0]["evidence"][0]["excerpt"] = "Ground floor Shop A · AREA 45 m²"
        changed = build_calculator_draft(data["thermal"], data["building"], data["coverage"], reviewed)
        self.assertFalse(changed["decisions"])
        self.assertTrue(changed["review_history"])

    def test_existing_authored_record_is_never_overwritten(self):
        draft = self.build(); floor = draft["candidates"]["floors"][0]; reviewed = self.reviewed(draft, [floor["candidate_id"]])
        model = empty_hourly_load_model(); model["floors"] = [{"floor_id": floor["value"]["floor_id"], "name": "Authored floor", "elevation_m": 0, "verification_status": "confirmed", "source": "Engineer authored", "citations": []}]
        outcome = apply_calculator_draft(reviewed, None, model)
        self.assertEqual(outcome["hourly_load_model"]["floors"][0]["name"], "Authored floor")
        self.assertEqual(len(outcome["summary"]["skipped_conflicts"]), 1)

    def test_label_only_room_cannot_become_active_topology(self):
        data = source_data()
        data["building"]["spaces"][0].pop("geometry")
        data["building"]["spaces"][0]["geometry_status"] = "geometry_review_required"
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"])
        ids = [item["candidate_id"] for key in ("floors", "zones", "rooms") for item in draft["candidates"][key]]
        outcome = apply_calculator_draft(self.reviewed(draft, ids))
        self.assertEqual(outcome["hourly_load_model"]["rooms"], [])
        self.assertTrue(any("geometry" in item["reason"].lower() for item in outcome["summary"]["unresolved"]))

    def test_room_registry_and_current_trace_flow_through_reviewed_draft(self):
        data = source_data()
        data["building"]["spaces"] = []
        registry = {"rooms": [
            {"room_id": "room-use:ground:shop", "label": "Shop", "level_name": "Ground", "source": "room_use_resolution",
             "evidence": [{"reference": "A-01 page 1", "page": 1, "excerpt": "SHOP"}]},
            {"room_id": "room-use:ground:store", "label": "Store", "level_name": "Ground", "source": "room_use_resolution",
             "evidence": [{"reference": "A-01 page 1", "page": 1, "excerpt": "STORE"}]},
        ], "records": [{"room_id": "room-use:ground:shop", "trace_id": "trace-shop", "reviewer": "QA-1",
                        "source_fingerprints": {"source_pdf": "pdf-current", "vector_page": "vector-current"}}]}
        calibration = {"status": "agreed", "mm_per_px": 10.0, "dimension_value_mm": 4000,
                       "difference_percent": 0.4}
        proof = {"entity_id": "proof-shop", "kind": "room_geometry_proof", "room_source_id": "room-use:ground:shop",
                 "geometry_status": "geometry_proposed", "extraction_method": "reviewer_traced_boundary",
                 "source": {"page": 1, "drawing_number": "A-01"},
                 "value": {"area_m2": 20.0, "reviewer_trace_id": "trace-shop", "calibration": calibration,
                           "source_fingerprints": registry["records"][0]["source_fingerprints"]}}
        data["coverage"]["pages"] = [{"page": 1, "drawing_number": "A-01"}]
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"],
            calculation_input_evidence={"geometry_resolution": {"entities": [proof]}}, room_registry=registry)
        self.assertEqual(len(draft["candidates"]["rooms"]), 2)
        self.assertEqual(len(draft["candidates"]["zones"]), 2)
        self.assertEqual(len([row for row in draft["candidates"]["room_inputs"] if row["kind"] == "area"]), 1)
        shop = next(row for row in draft["candidates"]["rooms"] if row["room_source"] == "room_use_resolution" and row["value"]["name"] == "Shop")
        self.assertEqual(shop["value"]["geometry_status"], "geometry_proposed")
        self.assertEqual(shop["value"]["geometry_reference"], "proof-shop")
        area = next(row for row in draft["candidates"]["room_inputs"] if row["kind"] == "area")
        self.assertEqual(area["value"], {"room_id": shop["value"]["room_id"], "area_m2": 20.0})
        self.assertIn("QA-1", area["citations"][0]["excerpt"])
        self.assertIn("A-01", area["citations"][0]["reference"])
        decisions = {}
        floor = draft["candidates"]["floors"][0]
        decisions[floor["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        for zone in draft["candidates"]["zones"]:
            decisions[zone["candidate_id"]] = {"decision": "edit", "reviewer": "ENG-1",
                "source": "Reviewed floor mapping", "citations": zone["citations"], "value": deepcopy(zone["value"])}
        decisions[shop["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        store = next(row for row in draft["candidates"]["rooms"] if row["value"]["name"] == "Store")
        decisions[store["candidate_id"]] = {"decision": "reject", "reviewer": "ENG-1"}
        decisions[area["candidate_id"]] = {"decision": "accept", "reviewer": "ENG-1"}
        reviewed = save_review(draft, decisions, draft["revision"])
        outcome = apply_calculator_draft(reviewed)
        model = outcome["hourly_load_model"]
        applied_shop = next(row for row in model["rooms"] if row["room_id"] == shop["value"]["room_id"])
        self.assertEqual(applied_shop["area_m2"], 20.0)
        self.assertEqual(applied_shop["bridge_provenance"][shop["candidate_id"]]["geometry_status"], "geometry_confirmed")
        self.assertEqual(applied_shop["bridge_provenance"][shop["candidate_id"]]["geometry_acceptance"]["proof_id"], "proof-shop")
        self.assertEqual(applied_shop["bridge_provenance"][shop["candidate_id"]]["geometry_acceptance"]["reviewer"], "ENG-1")
        conflicting_model = deepcopy(model)
        next(row for row in conflicting_model["rooms"] if row["room_id"] == shop["value"]["room_id"])["area_m2"] = 25.0
        conflict_outcome = apply_calculator_draft(reviewed, hourly_model=conflicting_model)
        self.assertEqual(next(row for row in conflict_outcome["hourly_load_model"]["rooms"] if row["room_id"] == shop["value"]["room_id"])["area_m2"], 25.0)
        self.assertTrue(any(row["candidate_id"] == area["candidate_id"] for row in conflict_outcome["summary"]["skipped_conflicts"]))

    def test_geometry_status_and_reference_are_not_editable(self):
        draft = self.build()
        room = draft["candidates"]["rooms"][0]
        value = deepcopy(room["value"])
        value["geometry_status"] = "geometry_confirmed"
        with self.assertRaisesRegex(ValueError, "cannot be edited by hand"):
            save_review(draft, {room["candidate_id"]: {"decision": "edit", "reviewer": "ENG-1",
                "source": "Manual status edit", "citations": room["citations"], "value": value}}, draft["revision"])

    def test_uncalibrated_registry_trace_does_not_create_an_area_candidate(self):
        data = source_data()
        data["building"]["spaces"] = []
        registry = {"rooms": [{"room_id": "room-use:ground:shop", "label": "Shop", "level_name": "Ground",
                               "source": "room_use_resolution", "evidence": EVIDENCE}],
                    "records": [{"room_id": "room-use:ground:shop", "trace_id": "trace-shop",
                                 "source_fingerprints": {"source_pdf": "pdf", "vector_page": "vector"}}]}
        proof = {"entity_id": "proof-shop", "kind": "room_geometry_proof", "room_source_id": "room-use:ground:shop",
                 "geometry_status": "geometry_proposed", "extraction_method": "reviewer_traced_boundary",
                 "value": {"area_m2": None, "reviewer_trace_id": "trace-shop",
                           "calibration": {"status": "unresolved", "mm_per_px": None},
                           "source_fingerprints": registry["records"][0]["source_fingerprints"]}}
        draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"],
            calculation_input_evidence={"geometry_resolution": {"entities": [proof]}}, room_registry=registry)
        self.assertFalse(any(row["kind"] == "area" for row in draft["candidates"]["room_inputs"]))
        self.assertFalse(draft["candidates"]["rooms"][0].get("reviewer_geometry_proof"))

    def test_multiple_current_traces_are_combined_or_blocked_on_disagreement(self):
        def fixture(areas, printed_area=None):
            data = source_data()
            data["building"]["spaces"] = []
            room_id = "room-use:ground:shop"
            records, proofs = [], []
            for index, area in enumerate(areas, start=1):
                trace_id, proof_id = f"trace-{index}", f"proof-{index}"
                source_fingerprints = {"source_pdf": "pdf-current", "vector_page": f"vector-{index}"}
                records.append({"room_id": room_id, "trace_id": trace_id, "reviewer": f"QA-{index}",
                                "source_fingerprints": source_fingerprints})
                proofs.append({"entity_id": proof_id, "kind": "room_geometry_proof", "room_source_id": room_id,
                    "geometry_status": "geometry_proposed", "extraction_method": "reviewer_traced_boundary",
                    "source": {"page": index, "drawing_number": f"A-0{index}"},
                    "value": {"area_m2": area, "reviewer_trace_id": trace_id,
                              "calibration": {"status": "agreed", "mm_per_px": 10.0,
                                              "dimension_value_mm": 4000, "difference_percent": 0.1},
                              "source_fingerprints": source_fingerprints}})
            registry = {"rooms": [{"room_id": room_id, "label": "Shop", "level_name": "Ground",
                                   "source": "room_use_resolution", "evidence": EVIDENCE}], "records": records}
            if printed_area is not None:
                space = {"id": "building-shop", "name": "Shop", "area": f"{printed_area} m²",
                         "level_name": "Ground", "evidence": [{"page": 1, "excerpt": f"Shop area {printed_area} m²"}]}
                data["building"]["spaces"] = [space]
            data["coverage"]["pages"] = [{"page": i, "drawing_number": f"A-0{i}"} for i in range(1, len(areas) + 1)]
            draft = build_calculator_draft(data["thermal"], data["building"], data["coverage"],
                calculation_input_evidence={"geometry_resolution": {"entities": proofs}}, room_registry=registry)
            return draft

        conflicting = fixture([20.0, 21.0])
        self.assertFalse(any(row.get("reviewer_geometry_proof") for row in conflicting["candidates"]["rooms"]))
        self.assertFalse(any(row.get("reviewer_geometry_proof") for row in conflicting["candidates"]["room_inputs"]))
        self.assertTrue(any("above the 2% comparison tolerance" in row["reason"] for row in conflicting["review_items"]))

        agreeing = fixture([20.0, 20.1])
        area_candidates = [row for row in agreeing["candidates"]["room_inputs"] if row["kind"] == "area"]
        self.assertEqual(len(area_candidates), 1)
        self.assertAlmostEqual(area_candidates[0]["value"]["area_m2"], 20.05)
        proof = next(row for row in agreeing["candidates"]["rooms"] if row.get("reviewer_geometry_proof"))["reviewer_geometry_proof"]
        self.assertEqual(len(proof["supporting_proofs"]), 2)
        self.assertEqual({row["page"] for row in proof["citations"]}, {1, 2})
        self.assertEqual(proof["comparison_tolerance"]["relative_tolerance_percent"], 2.0)

        same_as_printed = fixture([24.47], printed_area=24.5)
        area_candidates = [row for row in same_as_printed["candidates"]["room_inputs"] if row["kind"] == "area"]
        self.assertEqual(len(area_candidates), 1)
        self.assertEqual(len(area_candidates[0]["reviewer_geometry_proof"]["supporting_proofs"]), 1)
        self.assertAlmostEqual(area_candidates[0]["reviewer_geometry_proof"]["comparison_tolerance"]["printed_rounding_tolerance_m2"], 0.05)
        self.assertAlmostEqual(area_candidates[0]["reviewer_geometry_proof"]["comparison_tolerance"]["combined_tolerance_m2"], 0.5394)

        disagrees_with_printed = fixture([20.0], printed_area=22)
        self.assertFalse(any(row.get("reviewer_geometry_proof") for row in disagrees_with_printed["candidates"]["rooms"]))
        self.assertTrue(any("above the combined printed-rounding" in row["reason"] for row in disagrees_with_printed["review_items"]))

    def test_incomplete_construction_stays_outside_envelope_library(self):
        draft = self.build(); construction = next(item for item in draft["candidates"]["envelope"] if item["kind"] == "construction")
        reviewed = self.reviewed(draft, [construction["candidate_id"]]); outcome = apply_calculator_draft(reviewed)
        self.assertFalse(outcome["changed"]["envelope_library"])
        self.assertFalse(outcome["envelope_library"]["constructions"])
        self.assertTrue(any(item["candidate_id"] == construction["candidate_id"] for item in outcome["summary"]["unresolved"]))


if __name__ == "__main__":
    unittest.main()
