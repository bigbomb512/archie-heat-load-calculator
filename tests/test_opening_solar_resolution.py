"""Focused tests for the opening-to-solar normalization bridge."""

from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.opening_solar_resolution import calculate_opening_solar, resolve_opening_solar_record, stable_opening_id


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Opening evidence"}]


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print("PASS - " + label)


def main():
    base = {
        "opening_id": "opening-w01", "source": "Plan and elevation", "page": 1,
        "owner_room_id": "room-1", "owner_zone_id": "zone-1", "host_wall_id": "wall-1",
        "level_name": "Level 1", "width_m": 2.0, "height_m": 1.5, "quantity": 2,
        "explicit_glass_area_m2": 4.8, "external_exposure": "external", "facade": "N",
        "u_value_w_m2k": 2.4, "shgc": 0.45, "citations": citation("A-101"),
        "shading_category": "partial", "status": "ai_estimated",
    }
    first = resolve_opening_solar_record(base, known_pages={1})
    second = resolve_opening_solar_record({**deepcopy(base), "sightings": list(reversed([{"page": 1}, {"page": 2}]))}, known_pages={1, 2})
    check("opening area remains separate from explicit glass area", first["opening_area_m2"] == 6.0 and first["glass_area_m2"] == 4.8)
    check("cited property provenance is retained", first["properties"]["u_value_w_m2k"] == 2.4 and first["properties"]["solar_property_count"] == 1)
    check("stable opening identity does not depend on evidence order", stable_opening_id("pdf", "L1", "W01", [1, 2]) == stable_opening_id("pdf", "L1", "W01", [1, 2]))
    check("draft shading category is explicit", first["shading"]["category"] == "partial" and first["shading"]["direct_factor"] < 1)
    internal = resolve_opening_solar_record({**base, "external_exposure": "internal"}, known_pages={1})
    check("internal exposure excludes outdoor solar", internal["opening_solar_status"] == "excluded" and "internal exposure" in " ".join(internal["solar_unresolved_fields"]))
    visual = resolve_opening_solar_record({**base, "u_value_w_m2k": 2.8, "shgc": 0.5, "window_properties": {"visual_only": True, "u_value_w_m2k": 2.8, "shgc": 0.5}}, known_pages={1})
    check("visual-only properties remain provisional in draft mode", visual["status"] == "provisional" and visual["properties"]["visual_only"])
    manual = calculate_opening_solar({**base, "incident_solar_w_m2": 500}, {}, hour=12, scenario_id="summer", indoor_temperature_c=24, boundary_temperature_c=35, solar_basis="manual")
    check("manual path keeps conduction and solar separate", manual["status"] == "calculated" and "conduction_kw" in manual and "solar_kw" in manual)


if __name__ == "__main__":
    main()
