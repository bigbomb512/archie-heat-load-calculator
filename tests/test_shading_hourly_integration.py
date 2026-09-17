#!/usr/bin/env python3
"""Reviewed geometric shading replaces, rather than multiplies, manual shading."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import normalize_glazing_surfaces, validate_envelope_library, validate_envelope_model
from ai.glazing_gate import empty_glazing_method_gate, validate_glazing_method_gate
from ai.hourly_loads import glazing_contributions
from ai.shading_gate import empty_shading_method_gate, validate_shading_method_gate


def citation(value): return [{"reference": value, "page": 1, "excerpt": "Reviewed source"}]
def check(name, value):
    if not value: raise AssertionError(name)
    print(f"PASS - {name}")


def approved(factory, reference):
    gate = factory()
    gate.update({"approval_status": "approved", "engineer_name": "Engineer", "engineer_credential": "CPEng", "approved_at": "2026-09-15", "method_citation": reference, "citations": citation(reference)})
    return gate


def main():
    library = validate_envelope_library({"windows": [{
        "record_id": "win", "title": "W", "revision": 1, "review_status": "confirmed", "source": "schedule", "citations": citation("W"),
        "u_value_w_m2k": 2, "u_value_basis": "overall_window", "shgc": 0.5, "frame_fraction": 0.1, "glass_area_correction": 1, "internal_shading_factor": 1,
    }], "shading_records": [{
        "record_id": "shade", "title": "Overhang", "revision": 1, "kind": "geometric_v1", "review_status": "confirmed", "source": "Elevation", "citations": citation("A-300"),
        "geometry": {"overhang_depth_m": 3}, "hourly_sun_positions": [{"hour": h, "azimuth_deg": 0, "altitude_deg": 45} for h in range(24)],
    }]})
    model = validate_envelope_model({"active_for_calculation": True, "surfaces": [{
        "surface_id": "glass", "owner_zone_id": "zone", "owner_room_id": "room", "kind": "glazing", "orientation": "N", "opening_mapping_status": "confirmed", "window_id": "win",
        "opening_width_m": 2, "opening_height_m": 2, "opening_quantity": 1, "shading_record_ids": ["shade"], "boundary_method": "external", "review_status": "confirmed", "source": "Plan", "citations": citation("A-202"),
        "manual_solar": {"enabled": True, "incident_solar_w_m2": 500, "external_shading_factor": 0.5, "review_status": "confirmed", "source": "Solar", "citations": citation("solar")},
    }]}, library)
    glazing_gate = validate_glazing_method_gate(approved(empty_glazing_method_gate, "GM"))
    shading_gate = validate_shading_method_gate(approved(empty_shading_method_gate, "SM"))
    rows, blocked, stored = normalize_glazing_surfaces(library, model, glazing_gate, shading_gate)
    check("approved geometric record is attached to eligible glazing", len(rows) == 1 and rows[0]["geometric_shading"] and not blocked and not stored)
    room = {"room_id": "room", "indoor_cooling_setpoint_c": 24, "cooling_load": {"glazing_surfaces": rows}}
    contributions = glazing_contributions(room, {"solar:glass": [1] * 24}, 12, {"outdoor_dry_bulb_c": {"value": 34}}, glazing_gate, shading_gate)
    solar = next(row for row in contributions if row["name"] == "glazing_solar")
    check("geometry replaces the manual 0.5 shading factor with full shade", solar["total_kw"] == 0 and solar["inputs"]["external_shading"]["mode"] == "geometric")


if __name__ == "__main__": main()
