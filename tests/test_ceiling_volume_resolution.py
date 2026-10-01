#!/usr/bin/env python3
"""Focused checks for source-linked draft ceiling height and room volume."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import ai_preliminary, ceiling_volume_resolution


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def building():
    return {"spaces": [
        {"name": "Dining", "level_name": "Level 1", "area": 50, "evidence": [{"page": 2, "excerpt": "DINING"}]},
        {"name": "Store", "level_name": "Level 1", "area": 20, "evidence": [{"page": 2, "excerpt": "STORE"}]},
    ]}


def proposal():
    return {"rooms": [
        {"kind": "room", "label": "Dining", "level_name": "Level 1", "area_m2": 50, "preliminary_profile_id": "hospitality", "page": 3,
         "evidence": [{"page": 3, "excerpt": "DINING CLG 3000 AFFL"}], "ceiling_height_mm": 3000,
         "ceiling_evidence": [{"page": 3, "excerpt": "DINING CLG 3000 AFFL"}], "confidence": 0.9},
        {"kind": "room", "label": "Store", "level_name": "Level 1", "area_m2": 20, "preliminary_profile_id": "storage", "page": 3,
         "evidence": [{"page": 3, "excerpt": "STORE"}], "confidence": 0.6},
    ]}


def record(artifact, label):
    return next(row for row in artifact["records"] if row["original_label"] == label)


def main():
    pack = ai_preliminary.load_pack()
    sources = {"building_evidence": "a", "vision_response": "b", "ai_preliminary_proposal": "c", "preliminary_geometry_resolution": "d"}
    artifact = ceiling_volume_resolution.resolve(building(), proposal=proposal(), assumption_pack=pack, source_fingerprints=sources)
    dining, store = record(artifact, "Dining"), record(artifact, "Store")
    check("AI-linked ceiling evidence supplies an explicit draft height", dining["ceiling_height_mm"] == 3000 and dining["origin"] == "ai_estimated")
    check("volume uses precise resolved room area and ceiling height", dining["volume_m3"] == 150 and dining["derivation"]["operands"]["ceiling_height_mm"] == 3000)
    check("controlled fallback remains visible rather than hidden", store["ceiling_height_mm"] == 2700 and store["origin"] == "preliminary_fallback")
    assembled = ai_preliminary.assemble(building(), preliminary_proposal=proposal(), ceiling_volume_resolution=artifact,
                                        source_fingerprints={"ceiling_volume_resolution": artifact["fingerprint"]})
    dining_model = next(row for row in assembled["material"]["hourly_load_model"]["rooms"] if row["name"] == "Dining")
    ceiling_field = next(row for row in assembled["materialized_fields"] if row["room_id"] == dining_model["room_id"] and row["field"] == "ceiling_height_mm")
    check("preliminary assembly consumes the resolver rather than an inline fallback", dining_model["ceiling_height_mm"] == 3000 and ceiling_field["origin"] == "ai_estimated")
    check("preliminary snapshot fingerprints the separate ceiling resolver", assembled["dependency_fingerprints"]["ceiling_volume_resolution"] == artifact["fingerprint"])
    overridden = deepcopy(artifact)
    record(overridden, "Dining")["override"] = {"height_mm": 3200, "reviewer": "Engineer", "note": "Section confirmed", "updated_at": "2026-09-23T00:00:00+00:00"}
    override_result = ceiling_volume_resolution.resolve(building(), proposal=proposal(), assumption_pack=pack, source_fingerprints=sources, existing=overridden)
    check("contractor override outranks AI evidence", record(override_result, "Dining")["ceiling_height_mm"] == 3200 and record(override_result, "Dining")["origin"] == "contractor_override")
    conflicting = proposal()
    conflicting["rooms"].append({"kind": "room", "label": "Dining", "level_name": "Level 1", "area_m2": 50, "preliminary_profile_id": "hospitality", "page": 4,
                                  "evidence": [{"page": 4, "excerpt": "DINING CLG 2800"}], "ceiling_height_mm": 2800,
                                  "ceiling_evidence": [{"page": 4, "excerpt": "DINING CLG 2800"}], "confidence": 0.9})
    conflict_result = ceiling_volume_resolution.resolve(building(), proposal=conflicting, assumption_pack=pack, source_fingerprints=sources)
    check("competing equal AI candidates fail closed", record(conflict_result, "Dining")["status"] == "conflict")
    check("source changes stale the existing artifact", not ceiling_volume_resolution.is_current(artifact, {**sources, "vision_response": "changed"}, pack))


if __name__ == "__main__":
    main()
