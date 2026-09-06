#!/usr/bin/env python3

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.thermal_model import apply_thermal_model, build_thermal_evidence, build_thermal_model


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def bengong_input():
    return {
        "source_pdf": "bengong.pdf",
        "confirmed_pages": {
            "floor_plans": [{"page": 18, "structured_content": {"markdown": "SHOP 31B AREA: 30m² ADJOINING SHOP 31A ADJOINING SHOP 32 PEDESTRIAN STREET OPEN DISPLAY FRIDGE CAKE DISPLAY FRIDGE UPRIGHT FRIDGE UPRIGHT FREEZER UB ICE MAKER BOILER OVEN"}}],
            "reference_pages": [{"page": 21, "structured_content": {"markdown": "CH: 3380MM 20W LED DOWNLIGHT QTY: 16 25W LED DOWNLIGHT QTY: 3"}}],
        },
    }


def main():
    evidence = build_thermal_evidence(bengong_input())
    facts = {item["field"] for item in evidence["facts"]}
    check("extracts Bengong area", "zone_area_m2" in facts)
    check("extracts ceiling height", "ceiling_height_mm" in facts)
    check("extracts lighting evidence", "lighting_connected_w" in facts)
    check("extracts equipment candidates", "equipment_candidate" in facts)
    check("extracts adjacency evidence", "adjacency" in facts)
    check("all extracted evidence stays direct and cited", all(item["status"] == "direct" and item["evidence"] for item in evidence["facts"]))

    model = build_thermal_model(evidence)
    zone = model["zones"][0]
    check("builds one Shop 31B thermal zone", zone["zone_id"] == "shop_31b" and zone["area_m2"] == 30)
    check("unknown appliance wattages remain blank", all(item["watts"] is None for item in zone["internal_load_candidates"]))
    check("thermal model retains required exception queue", len(model["review_items"]) >= 7)

    requirements = apply_thermal_model(model, {})
    mapped = requirements["zones"][0]
    check("maps accepted direct geometry into design requirements", mapped["area_m2"] == 30 and mapped["ceiling_height_mm"] == 3380)
    check("maps nominal lighting without creating appliance watts", mapped["cooling_load"]["lighting_w_m2"] > 0 and mapped["heat_sources"] == [])
    rejected = apply_thermal_model(model, {"lighting_connected_w": {"decision": "reject"}})
    check("rejected fact does not become a calculation value", rejected["zones"][0]["cooling_load"]["lighting_w_m2"] is None)


if __name__ == "__main__":
    main()
