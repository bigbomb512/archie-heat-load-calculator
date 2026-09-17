#!/usr/bin/env python3
"""Fixed-adjacent partitions are room-owned and cannot double count."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import apply_reviewed_envelope_to_hourly_model, normalize_surfaces, validate_envelope_library, validate_envelope_model


def check(name, value):
    if not value: raise AssertionError(name)
    print(f"PASS - {name}")


def citation(reference): return [{"reference": reference, "page": 1, "excerpt": "Reviewed boundary"}]


def partition(surface_id="partition-1", boundary_id="adjacent-1"):
    return {"surface_id": surface_id, "owner_zone_id": "zone", "owner_room_id": "room-a", "kind": "partition", "orientation": "internal", "area_m2": 10,
            "construction_id": "partition", "boundary_method": "fixed_adjacent_temperature", "adjacent_temperature_c": 30,
            "adjacent_boundary_id": boundary_id, "adjacent_temperature_source": "Adjacent temperature schedule", "adjacent_temperature_citations": citation("B-01"),
            "review_status": "confirmed", "source": "Section", "citations": citation("A-400"), "manual_solar": {"enabled": False}}


def main():
    library = validate_envelope_library({"constructions": [{"record_id": "partition", "title": "P", "revision": 1, "kind": "partition", "u_value_w_m2k": 1, "review_status": "confirmed", "source": "Schedule", "citations": citation("S-1")} ]})
    model = validate_envelope_model({"active_for_calculation": True, "surfaces": [partition()]}, library)
    hourly = {"rooms": [{"room_id": "room-a", "zone_id": "zone", "cooling_load": {"envelope_surfaces": [], "glazing_surfaces": []}, "schedule_assignments": {"solar": {}}}, {"room_id": "room-b", "zone_id": "zone", "cooling_load": {"envelope_surfaces": [], "glazing_surfaces": []}, "schedule_assignments": {"solar": {}}}]}
    applied = apply_reviewed_envelope_to_hourly_model(hourly, library, model)
    check("partition applies only to its exact owning room", len(applied["rooms"][0]["cooling_load"]["envelope_surfaces"]) == 1 and not applied["rooms"][1]["cooling_load"]["envelope_surfaces"])
    duplicate = validate_envelope_model({"active_for_calculation": True, "surfaces": [partition(), partition("partition-2")]}, library)
    _included, blocked, _stored = normalize_surfaces(library, duplicate)
    check("duplicate adjacent boundary is blocked instead of double counted", len(blocked) == 1 and "already owned" in blocked[0]["reason"])


if __name__ == "__main__": main()
