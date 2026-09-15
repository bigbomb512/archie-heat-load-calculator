#!/usr/bin/env python3
"""Independent checks for the inactive standalone glazing method."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.envelope import validate_envelope_library
from ai.glazing_calculation import (
    assess_glazing_eligibility, calculate_glazing, corrected_glass_area,
    glass_area, glazing_conduction, manual_solar_transmission, opening_area,
)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def citation(label):
    return [{"reference": label, "page": 26, "excerpt": "Reviewed opening evidence"}]


def records():
    library = validate_envelope_library({"windows": [{
        "record_id": "window-w01", "title": "Reviewed W01", "revision": 1,
        "review_status": "confirmed", "source": "Architect window schedule", "citations": citation("DWG 300"),
        "u_value_w_m2k": 0.5, "shgc": 0.6, "frame_fraction": 0.1,
        "glass_area_correction": 0.95, "internal_shading_factor": 0.9,
    }]})
    surface = {
        "surface_id": "surface-w01", "owner_room_id": "room-a", "owner_zone_id": "zone-a",
        "opening_mapping_status": "confirmed", "boundary_method": "external", "boundary_temperature_c": 35,
        "review_status": "confirmed", "source": "Dimensioned plan and elevation", "citations": citation("DWG 202"),
        "opening_width_m": 2, "opening_height_m": 1.5, "opening_quantity": 2,
    }
    solar = {"solar_design_w_m2": 500, "shading_factor": 0.8, "source": "Reviewed manual solar basis", "citations": citation("Solar schedule")}
    return surface, library["windows"][0], solar


def main():
    surface, window, solar = records()
    check("reviewed window properties remain in envelope storage", window["shgc"] == 0.6 and window["internal_shading_factor"] == 0.9)
    check("opening area uses explicit dimensions and quantity", opening_area(2, 1.5, 2) == 6.0)
    check("frame fraction derives glass area", glass_area(opening_area_m2=6, frame_fraction=0.1) == 5.4)
    check("explicit glass area is retained", glass_area(explicit_glass_area_m2=4.2, frame_fraction=0.1) == 4.2)
    check("glass correction is applied", corrected_glass_area(5.4, 0.95) == 5.13)
    check("positive temperature difference gives positive conduction", glazing_conduction(0.5, 6, 35, 24) == 0.033)
    check("reverse temperature difference remains signed", glazing_conduction(0.5, 6, 20, 24) == -0.012)
    check("manual solar is independently calculable", manual_solar_transmission(500, 5.13, 0.6, 0.8, 0.9) == 1.10808)
    result = calculate_glazing(surface, window, solar, indoor_temperature_c=24)
    check("complete reviewed glazing result calculates", result["status"] == "calculated")
    check("combined contribution is traceable", result["total_kw"] == 1.14108 and result["citations"]["window"])

    missing_u = dict(window)
    missing_u["u_value_w_m2k"] = None
    check("missing U-value blocks the method", "U-value is missing" in assess_glazing_eligibility(surface, missing_u, solar, indoor_temperature_c=24))
    missing_shgc = dict(window)
    missing_shgc["shgc"] = None
    missing_shgc["solar_transmission_factor"] = None
    check("missing solar property blocks the method", any("SHGC" in item for item in assess_glazing_eligibility(surface, missing_shgc, solar, indoor_temperature_c=24)))
    ambiguous = dict(surface)
    ambiguous["opening_mapping_status"] = "conflict"
    check("ambiguous ownership blocks the method", "opening-to-surface relationship is not confirmed" in assess_glazing_eligibility(ambiguous, window, solar, indoor_temperature_c=24))
    no_citation = dict(surface)
    no_citation["citations"] = []
    check("uncited geometry blocks the method", "surface source and citations are required" in assess_glazing_eligibility(no_citation, window, solar, indoor_temperature_c=24))
    incomplete_solar = dict(solar)
    incomplete_solar.pop("solar_design_w_m2")
    check("incomplete manual solar blocks the method", "complete manual incident solar input is required" in assess_glazing_eligibility(surface, window, incomplete_solar, indoor_temperature_c=24))
    invalid = calculate_glazing({**surface, "opening_width_m": -1}, window, solar, indoor_temperature_c=24)
    check("invalid dimensions remain stored-only", invalid["status"] == "blocked")


if __name__ == "__main__":
    main()
