#!/usr/bin/env python3
"""Write the reference rooms as a handout for entering in Camel (docs/reference_rooms_for_camel.md and .xlsx).

    python3 tools/export_reference_rooms_for_camel.py

Reads tests/fixtures/draft_reference_rooms_v1.json, so the handout always matches the tested rooms. The contractor's
Camel results for these rooms can be compared with Archie in the parity harness (docs/da09_camel_benchmark.md).
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "tests" / "fixtures" / "draft_reference_rooms_v1.json").read_text(encoding="utf-8"))
FACING = {0.0: "north", 90.0: "east", 180.0: "south", 270.0: "west"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def room_rows(case):
    inputs = SUITE["declared_inputs"]
    pack = inputs["pack"]
    rows = []
    for room in case["rooms"]:
        profile = pack["profiles"][room["profile"]]
        open_from, open_to = profile["open"]
        people = room.get("seats") or max(1, round(room["area_m2"] * profile["density"]))
        sensible, latent = pack["activity"][room["activity"]]
        rows.append((room["name"], "Floor area", f"{room['area_m2']:g} m²"))
        rows.append((room["name"], "Above / below", "Floor above and slab below at the room's temperature (no load)"
                     if not any(s["kind"] == "roof" for s in room.get("surfaces", [])) else "Exposed roof; slab on ground (no load)"))
        for surface in room.get("surfaces", []):
            if surface.get("adjacent"):
                rows.append((room["name"], surface["label"], f"{surface['gross_m2']:g} m² to the {surface['adjacent']} (same temperature, no load)"))
            elif surface["kind"] == "roof":
                rows.append((room["name"], surface["label"], f"{surface['area_m2']:g} m², U {surface['u']:g} W/m²K, dark (absorptance/h_o 0.052)"))
            else:
                building = surface.get("building")
                rows.append((room["name"], surface["label"],
                             f"{surface['gross_m2']:g} m² gross ({surface['area_m2']:g} m² net of glass), facing {FACING[surface['azimuth']]}, "
                             f"U {surface['u']:g} W/m²K, dark, {surface.get('height_m', 3):g} m high"
                             + (f"; a {building['height_m']:g} m tall building {building['distance_m']:g} m away along its whole length" if building else "")))
        for glass in room.get("glass", []):
            shade = glass.get("awning")
            building = glass.get("building")
            rows.append((room["name"], glass["label"],
                         f"{glass['width_m']:g} × {glass['height_m']:g} m (sill {glass.get('sill_m', 0):g} m), facing {FACING[glass['azimuth']]}, "
                         f"U {pack['glass_u']:g}, SHGC {pack['shgc']:g}, frame {pack['frame_fraction'] * 100:g}%, no blinds"
                         + (f"; awning {shade['depth_m']:g} m deep, {shade['gap_m']:g} m above the head" if shade else "")
                         + (f"; {building['height_m']:g} m building {building['distance_m']:g} m opposite" if building else "")))
        rows.append((room["name"], "People", f"{people} ({'printed seats' if room.get('seats') else f'{profile['density']:g} per m²'}), "
                     f"{sensible} W sensible / {latent} W latent each, diversity {profile['people_diversity']:g}"))
        rows.append((room["name"], "Lighting", f"{profile['lighting_w_m2']:g} W/m², diversity {profile['lighting_diversity']:g}"))
        rows.append((room["name"], "Equipment", f"{profile['equipment_w_m2']:g} W/m² × heat to room {profile['equipment_space_gain']:g} × diversity {profile['equipment_diversity']:g}"))
        ventilation = f"{pack['oa_per_person']:g} L/s per person + {pack['oa_per_m2']:g} L/s per m² (standard air 1.2 kg/m³)"
        rows.append((room["name"], "Outside air", ventilation + (f"; or the kitchen exhaust {room['exhaust_lps']:g} L/s if larger (made up through the room)"
                                                                 if room.get("exhaust_lps") else "")))
        rows.append((room["name"], "Hours", f"Open {open_from}:00 to {open_to}:00 (people, lights, equipment, outside air and sun loads on; walls and glass conduct all day)"))
    return rows


def main():
    inputs = SUITE["declared_inputs"]
    lines = ["# Reference rooms for Camel", "",
             "Three simple rooms to enter in Camel, so Archie's draft can be compared with an independent tool "
             "(docs/draft_reference_rooms.md). Please run each as described and send the room loads by component, with "
             "the peak month and hour, for January 3 pm and the peak.", "",
             f"**Site and weather (all rooms):** Sydney ({abs(inputs['site']['latitude']):g}°S, {inputs['site']['longitude']:g}°E). "
             f"Summer design {inputs['design_point']['db']:g} °C dry bulb with {inputs['design_point']['cwb']:g} °C wet bulb at 3 pm; "
             f"inside {inputs['indoor']['db']:g} °C / {inputs['indoor']['wb']:g} °C wet bulb. Clear sky, 21st of each month. "
             "No safety allowance; fan heat 3.2% of room sensible. These temperatures are synthetic test values, not AIRAH data.", ""]
    workbook_rows = [("Case", "Room", "Item", "Value")]
    for case in SUITE["cases"]:
        lines += [f"## {case['case_id'].split('_', 1)[0]}. {case['title']}", "", "| Room | Item | Value |", "|---|---|---|"]
        for room, item, value in room_rows(case):
            lines.append(f"| {room} | {item} | {value} |")
            workbook_rows.append((case["case_id"], room, item, value))
        peak = case["peak"]
        lines += ["", f"Archie's worked peak: **{peak['total_kw']:.2f} kW** in {MONTHS[peak['month'] - 1]} at {peak['hour']}:00.", ""]
    (ROOT / "docs" / "reference_rooms_for_camel.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = "Reference rooms"
    for row in workbook_rows:
        sheet.append(row)
    for column, width in zip("ABCD", (16, 12, 24, 110)):
        sheet.column_dimensions[column].width = width
    book.save(ROOT / "docs" / "reference_rooms_for_camel.xlsx")
    print("Wrote docs/reference_rooms_for_camel.md and .xlsx")


if __name__ == "__main__":
    main()
