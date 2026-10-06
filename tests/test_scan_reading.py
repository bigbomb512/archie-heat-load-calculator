#!/usr/bin/env python3
"""Card S: reading scanned drawing sets (printed areas, area calibration, imperial scales, title blocks)."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shapely.geometry import box

from ai import autonomous_tasks
from ai import scan_reading as scan
from ai.dimension_wall_matcher import page_scale_denominator


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


def scale_checks():
    check("metric and imperial scales are read; NTS has none",
          [scan.parse_scale(text) for text in ("1:100", "SCALE 1:50", '1/8" = 1\'-0"', "1/8″ = 1′-0″",
                                               '3/16"=1\'-0"', '1" = 10\'', "NTS", "As indicated")]
          == [100, 50, 96, 96, 64, 120, None, None])
    check("an imperial scale is not mistaken for 1:8 by the shared scale helper",
          page_scale_denominator([{"text": '1/8" = 1\'-0"'}]) == 96 and page_scale_denominator(["1:100", "1:20"]) == 100
          and page_scale_denominator(["1/100"]) == 100)
    check("feet-and-inch dimensions are converted to millimetres",
          [round(scan.parse_dimension_mm(text), 1) for text in ("11825", "12'-6\"", "12' 6 1/2\"", "9'", '6"')]
          == [11825.0, 3810.0, 3822.7, 2743.2, 152.4] and scan.parse_dimension_mm("ABC") is None)
    check("mm per pixel follows the scale and render dpi", round(scan.mm_per_px_from_scale(96, 150), 3) == 16.256)


VIEWPORT = (0, 0, 2000, 1000)


def plan():
    """Tiles and outlines for a plan 2000 x 1000 px: Office (left), Equipment Rm (middle), Store (right)."""
    tiles = scan.area_tiles(VIEWPORT)
    outlines = [box(0, 0, 600, 1000), box(600, 0, 1400, 1000), box(1400, 0, 2000, 1000)]
    return tiles, outlines


def reply(rooms, scale_text='1/8" = 1\'-0"'):
    return {"rooms": rooms, "scale_text": scale_text}


def area_checks():
    tiles, outlines = plan()
    check("four overlapping tiles cover the plan", len(tiles) == 4 and tiles[0][2] > tiles[1][0])
    factors = [1.0] * 4
    good = reply([
        {"tile": 1, "name": "OFFICE", "number": "2", "area_value": 500, "unit": "SF", "printed_text": "500 SF", "label_px": [300, 400]},
        {"tile": 1, "name": "EQUIPMENT RM", "number": "1", "area_value": 1266, "unit": "sf", "printed_text": "1266 SF", "label_px": [1000, 300]},
        {"tile": 2, "name": "Equipment Rm", "number": "1", "area_value": 1266, "unit": "sf", "printed_text": "1,266 SF", "label_px": [125, 300]},
        {"tile": 2, "name": "STORE", "number": None, "area_value": 40.8, "unit": "m2", "printed_text": "A = 40.8SQM", "label_px": [700, 400]}])
    result = scan.validate_area_reply(good, tiles, factors, outlines)
    rooms = {room["label"]: room for room in result["rooms"]}
    check("square feet are converted and a room read on two tiles is kept once",
          set(rooms) == {"OFFICE", "EQUIPMENT RM", "STORE"} and rooms["OFFICE"]["area_m2"] == 46.45
          and rooms["EQUIPMENT RM"]["tiles"] == [1, 2] and rooms["STORE"]["area_m2"] == 40.8)
    check("each label is placed in the outline that holds it, in page pixels",
          rooms["OFFICE"]["outline_index"] == 0 and rooms["EQUIPMENT RM"]["outline_index"] == 1
          and rooms["STORE"]["outline_index"] == 2)
    check("the printed scale is read with the areas", result["scale_denominator"] == 96)

    bad_value = json.loads(json.dumps(good))
    bad_value["rooms"][2]["area_value"] = 1206
    bad_value["rooms"][2]["printed_text"] = "1206 SF"
    expect_error("a room read differently on two tiles is refused", lambda: scan.validate_area_reply(bad_value, tiles, factors), "read differently")
    expect_error("a unit other than m2 or sf is refused",
                 lambda: scan.validate_area_reply(reply([{**good["rooms"][0], "unit": "acres"}]), tiles, factors), "unit must be")
    expect_error("printed text that does not show the value is refused",
                 lambda: scan.validate_area_reply(reply([{**good["rooms"][0], "printed_text": "OFFICE"}]), tiles, factors), "does not show")
    expect_error("an implausible area is refused",
                 lambda: scan.validate_area_reply(reply([{**good["rooms"][0], "area_value": 2, "printed_text": "2 SF"}]), tiles, factors), "plausible")
    expect_error("a tile that was not sent is refused",
                 lambda: scan.validate_area_reply(reply([{**good["rooms"][0], "tile": 9}]), tiles, factors), "not sent")
    expect_error("a label outside its tile is refused",
                 lambda: scan.validate_area_reply(reply([{**good["rooms"][0], "label_px": [5000, 10]}]), tiles, factors), "outside tile")
    return result, outlines


def calibration_checks(result, outlines):
    rooms = [dict(room) for room in result["rooms"]]
    # make the printed areas consistent with a true scale of 10 mm/px for Office and Equipment Rm
    for room in rooms:
        if room["label"] == "OFFICE":
            room["area_m2"] = 600 * 1000 * 100 / 1e6
        if room["label"] == "EQUIPMENT RM":
            room["area_m2"] = 800 * 1000 * 100 / 1e6 * 1.01
        if room["label"] == "STORE":
            room["area_m2"] = 600 * 1000 * 100 / 1e6 * 1.3  # outline includes more than the room
    calibration = scan.calibration_from_areas(rooms, outlines, declared_mm_per_px=10.0)
    check("two rooms agreeing within 2% set the scale; a disagreeing room is rejected",
          abs(calibration["mm_per_px"] - 10.025) < 0.01 and calibration["rooms"] == ["EQUIPMENT RM", "OFFICE"]
          and calibration["rejected"] == ["STORE"] and calibration["declared_scale_agrees"])
    expect_error("one room alone never calibrates",
                 lambda: scan.calibration_from_areas([room for room in rooms if room["label"] == "OFFICE"], outlines), "at least 2")
    shared = [dict(rooms[0], outline_index=1), dict(rooms[1], outline_index=1)]
    expect_error("two labelled rooms in one outline do not count", lambda: scan.calibration_from_areas(shared, outlines), "found 0")
    spread = [dict(room) for room in rooms if room["label"] != "STORE"]
    spread[0]["area_m2"] *= 1.2
    expect_error("rooms that disagree by more than 2% do not calibrate", lambda: scan.calibration_from_areas(spread, outlines), "disagree")


def title_block_checks():
    lines = scan.validate_transcription({"lines": ["USDOT/VOLPE CENTER", "55 BROADWAY CENTER", "CAMBRIDGE, MA 02142-1001",
                                                   "DOT REDESIGN OF", "BUILDING #2410", "JOINT BASE CAPE COD", "SANDWICH, MA",
                                                   "HVAC FLOOR PLAN", "SHEET 04"]})
    packet, prompt = scan.site_packet_from_transcriptions({4: lines})
    texts = [row["text"] for row in packet["excerpts"]]
    check("address-like title-block lines are grouped into quotable excerpts",
          any("JOINT BASE CAPE COD" in text and "SANDWICH, MA" in text for text in texts)
          and any("55 BROADWAY CENTER" in text for text in texts) and "read from" not in prompt.casefold()
          and all(row["clue_type"] == "read_from_image" for row in packet["excerpts"]))
    site_text = next(text for text in texts if "CAPE COD" in text)
    validated = autonomous_tasks.validate_site_reply(packet, json.dumps({
        "site": {"text": "JOINT BASE CAPE COD", "page": 4, "kind": "street_address"},
        "consultant_addresses": [{"text": "55 BROADWAY CENTER", "page": 4, "why": "client office"}]}))
    check("the existing site validator accepts quotes from the transcribed excerpts",
          validated["site"]["text"] == "JOINT BASE CAPE COD" and site_text)
    expect_error("a site that was not transcribed is refused",
                 lambda: autonomous_tasks.validate_site_reply(packet, json.dumps({"site": {"text": "HANSCOM AFB", "page": 4,
                                                                                           "kind": "street_address"}, "consultant_addresses": []})),
                 "exact substring")
    expect_error("an empty transcription is refused", lambda: scan.validate_transcription({"lines": []}), "non-empty")
    expect_error("an over-long line is refused", lambda: scan.validate_transcription({"lines": ["X" * 200]}), "longer than")


def main():
    scale_checks()
    result, outlines = area_checks()
    calibration_checks(result, outlines)
    title_block_checks()


if __name__ == "__main__":
    main()
