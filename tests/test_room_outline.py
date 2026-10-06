#!/usr/bin/env python3
"""Card P0: automatic room outlines from a plan's vector drawing (synthetic plans)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapely.geometry import LineString, Polygon, box

from ai import room_outline as ro


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


MM_PER_PX = 10.0  # 1 px = 10 mm, so 100 px = 1 m
VIEWPORT = (0, 0, 1400, 1000)
WALL = (0, 0.67, 0.67)
FURNITURE = (0.24, 0.0, None)
TILE = (0.48, 0.78, None)


def filled(points, style=WALL):
    return {"kind": "curve", "style": style, "style_id": ro.style_id(style), "points": points + [points[0]], "filled": True}


def line(a, b, style):
    return {"kind": "line", "style": style, "style_id": ro.style_id(style), "points": [a, b], "filled": False}


def two_room_plan():
    """Two rooms 5 m x 6 m and 4 m x 6 m (inside faces), 150 mm walls, a 900 mm door between them."""
    t = 15  # wall thickness in px
    objects = [
        filled([(100, 100), (1100, 100), (1100, 100 + t), (100, 100 + t)]),          # top
        filled([(100, 700 - t), (1100, 700 - t), (1100, 700), (100, 700)]),          # bottom
        filled([(100, 100), (100 + t, 100), (100 + t, 700), (100, 700)]),            # left
        filled([(1100 - t, 100), (1100, 100), (1100, 700), (1100 - t, 700)]),        # right
        # partition at x = 615..630 with a 90 px (900 mm) door gap from y 400 to 490
        filled([(615, 100), (630, 100), (630, 400), (615, 400)]),
        filled([(615, 490), (630, 490), (630, 700), (615, 700)]),
        # furniture: a table inside room 1 (must not cut the room)
        filled([(300, 300), (400, 300), (400, 360), (300, 360)], FURNITURE),
    ]
    # floor tiles: a 250 mm grid across the plan (must be removed as hatch)
    for y in range(120, 690, 25):
        objects.append(line((130, y), (1080, y), TILE))
    return objects


def enclosed_room_checks():
    objects = two_room_plan()
    check("the floor-tile grid is found as hatch", ro.hatch_styles(objects) == {TILE})
    summary = ro.style_summary(objects, VIEWPORT, MM_PER_PX)
    check("the style summary flags hatch and lists the other styles", any(row["hatch"] for row in summary) and len(summary) == 3)
    walls = ro.wall_geometry(objects, [ro.style_id(WALL)], VIEWPORT)
    rooms = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX, door_gap_mm=1000)
    areas = sorted(round(ro.area_m2(room, MM_PER_PX), 2) for room in rooms)
    # inside faces: room 1 x 115..615 (5.00 m) by 115..685 (5.70 m); room 2 x 630..1085 (4.55 m)
    check("closing the 900 mm door gap yields the two rooms", len(rooms) == 2)
    check("room areas are measured to the inside face of the walls (within 2%)",
          abs(areas[0] - 4.55 * 5.70) / (4.55 * 5.70) < 0.02 and abs(areas[1] - 5.00 * 5.70) / (5.00 * 5.70) < 0.02)
    check("furniture inside a room is part of its floor, not a hole", all(len(room.interiors) == 0 for room in rooms))
    merged = ro.enclosed_rooms(walls, VIEWPORT, MM_PER_PX, door_gap_mm=600)
    check("a door wider than the closing gap leaves the two rooms joined", len(merged) == 1)
    with_furniture_as_wall = ro.enclosed_rooms(ro.wall_geometry(objects, [ro.style_id(WALL), ro.style_id(FURNITURE)], VIEWPORT),
                                               VIEWPORT, MM_PER_PX, door_gap_mm=1000)
    check("choosing furniture as a wall style still keeps the room (islands are filled)", len(with_furniture_as_wall) == 2)
    open_walls = ro.wall_geometry([item for item in objects if item["points"][0] != (100, 700 - 15)], [ro.style_id(WALL)], VIEWPORT)
    check("an area open to the plan edge is not a room", ro.enclosed_rooms(open_walls, VIEWPORT, MM_PER_PX) == [])
    # a 20 mm single-line partition jutting 2 m into room 1 from the top wall, and a 400 mm pier jutting 1 m
    jut = objects + [filled([(300, 115), (302, 115), (302, 315), (300, 315)])]
    jut_rooms = ro.enclosed_rooms(ro.wall_geometry(jut, [ro.style_id(WALL)], VIEWPORT), VIEWPORT, MM_PER_PX, door_gap_mm=1000)
    room_1 = max(jut_rooms, key=lambda room: room.bounds[0] < 300)
    check("a thin partition jutting into a room leaves no notch in its outline (still four corners, area within 1%)",
          len(room_1.exterior.coords) == 5 and abs(ro.area_m2(room_1, MM_PER_PX) - 5.00 * 5.70) / (5.00 * 5.70) < 0.01)
    pier = objects + [filled([(300, 115), (340, 115), (340, 215), (300, 215)])]
    pier_rooms = ro.enclosed_rooms(ro.wall_geometry(pier, [ro.style_id(WALL)], VIEWPORT), VIEWPORT, MM_PER_PX, door_gap_mm=1000)
    room_1 = max(pier_rooms, key=lambda room: room.bounds[0] < 300)
    check("a 400 mm pier is a real recess and stays in the outline", len(room_1.exterior.coords) > 5)

    labels = [{"text": "Office", "point": (300, 500)}, {"text": "Store", "point": (800, 300)}, {"text": "Lobby", "point": (1300, 900)}]
    matched, ambiguous, unmatched = ro.assign_labels(rooms, labels)
    check("labels inside rooms are matched and a label outside is unmatched",
          {row["label"] for row in matched} == {"Office", "Store"} and [row["text"] for row in unmatched] == ["Lobby"] and not ambiguous)
    _matched, ambiguous, _unmatched = ro.assign_labels(merged, labels[:2])
    check("two labels in one area are reported as ambiguous", ambiguous and set(ambiguous[0]["labels"]) == {"Office", "Store"})


def wall_style_checks():
    summary = ro.style_summary(two_room_plan(), VIEWPORT, MM_PER_PX)
    numbered = [row for row in summary if not row["hatch"]]
    wall_number = next(index + 1 for index, row in enumerate(numbered) if row["style_id"] == ro.style_id(WALL))
    check("the wall-style reply maps legend numbers to style IDs",
          ro.validate_wall_style_reply({"wall_styles": [wall_number]}, summary) == [ro.style_id(WALL)])
    expect_error("an unknown legend number is refused", lambda: ro.validate_wall_style_reply({"wall_styles": [9]}, summary), "unknown legend number")
    expect_error("an empty wall list is refused", lambda: ro.validate_wall_style_reply({"wall_styles": []}, summary), "no wall styles")
    check("the wall-style prompt lists only non-hatch styles", ro.wall_style_prompt(summary).count("style-") == len(numbered))


def naming_and_split_checks():
    transform = {"offset_px": [0, 0], "factor": 0.5}
    names, splits = ro.validate_naming_reply({"areas": [{"number": 1, "room": "Shop"}, {"number": 2, "room": "not_a_room"}],
                                               "splits": [{"number": 1, "line_px": [[100, 0], [100, 300]],
                                                           "rooms": [{"room": "Shop", "point_px": [50, 100]}, {"room": "Bar", "point_px": [200, 100]}],
                                                           "feature": "bar counter edge"}]},
                                              2, ["Shop", "Bar"], transform)
    check("a split replaces the area's single name and points are scaled back to page pixels",
          0 not in names and names[1] == ro.NOT_A_ROOM and splits[0]["line"][0] == (200.0, 0.0) and splits[0]["rooms"][1]["point"] == (400.0, 200.0))
    expect_error("an unknown room name is refused",
                 lambda: ro.validate_naming_reply({"areas": [{"number": 1, "room": "Lounge"}]}, 1, ["Shop"], transform), "not in the room list")
    expect_error("an unknown area number is refused",
                 lambda: ro.validate_naming_reply({"areas": [{"number": 3, "room": "Shop"}]}, 2, ["Shop"], transform), "unknown area")
    merged, _none = ro.validate_naming_reply({"areas": [{"number": 1, "room": "Shop"}, {"number": 2, "room": "Shop"}]}, 2, ["Shop"], transform)
    check("one room may cover two enclosed areas", merged == {0: "Shop", 1: "Shop"})
    expect_error("a room both split out and named for another area is refused",
                 lambda: ro.validate_naming_reply({"areas": [{"number": 2, "room": "Bar"}],
                                                   "splits": [{"number": 1, "line_px": [[0, 0], [0, 9]],
                                                               "rooms": [{"room": "Shop", "point_px": [1, 1]}, {"room": "Bar", "point_px": [2, 2]}]}]},
                                                  2, ["Shop", "Bar"], transform), "cannot also be given")

    area = box(0, 0, 1000, 600)
    counter_edge = LineString([(398, -50), (398, 650)])
    snapped, report = ro.snap_polyline([(410, 100), (410, 500)], [counter_edge, LineString([(0, 300), (1000, 300)])], max_offset_px=20)
    check("a sketched split is snapped onto the nearest drawn line running the same way",
          report[0]["snapped"] and abs(snapped[0][0] - 398) < 1e-6 and abs(snapped[1][0] - 398) < 1e-6)
    _unsnapped, far = ro.snap_polyline([(470, 100), (470, 500)], [counter_edge], max_offset_px=20)
    check("a sketch far from any drawn line is reported as unsnapped", far == [{"snapped": False}])
    pieces = ro.split_area(area, snapped, [{"room": "Bar", "point": (200, 300)}, {"room": "Shop", "point": (700, 300)}])
    check("the snapped split cuts the area along the drawn line",
          abs(pieces["Bar"].area - 398 * 600) < 1 and abs(pieces["Shop"].area - 602 * 600) < 1)
    expect_error("both room points on one side are refused",
                 lambda: ro.split_area(area, snapped, [{"room": "Bar", "point": (100, 300)}, {"room": "Shop", "point": (200, 300)}]),
                 "different parts")


def page_origin_checks():
    class Page:
        bbox = (-595.26, 420.9, 595.26, 1262.7)
        lines = [{"pts": [(-595.26, 420.9), (-495.26, 420.9)], "linewidth": 0.5, "stroking_color": (0,), "fill": False}]
        curves, rects = [], []
    objects = ro.extract_page_objects(Page(), 2.5)
    check("shapes are measured from the page box origin, not the PDF's absolute coordinates",
          objects[0]["points"] == [(0.0, 0.0), (250.0, 0.0)])


def outline_checks():
    transform = {"offset_px": [100, 50], "factor": 0.5}
    outlines = ro.validate_outline_reply({"rooms": [{"room": "Counter", "points_px": [[0, 0], [100, 0], [100, 60], [0, 60]],
                                                     "open_sides": "tenancy line"}]}, ["Counter"], transform)
    check("an AI outline is converted back to page pixels", outlines[0]["points"][2] == (300.0, 170.0))
    expect_error("a self-crossing outline is refused",
                 lambda: ro.validate_outline_reply({"rooms": [{"room": "Counter", "points_px": [[0, 0], [100, 100], [100, 0], [0, 100]]}]},
                                                   ["Counter"], transform), "crosses itself")
    expect_error("a room outlined twice is refused",
                 lambda: ro.validate_outline_reply({"rooms": [{"room": "Counter", "points_px": [[0, 0], [9, 0], [9, 9]]}] * 2},
                                                   ["Counter"], transform), "twice")
    # Drawn room: 600 x 400 px. The AI sketch is off by up to 12 px on each side.
    drawn = [LineString([(0, 0), (600, 0)]), LineString([(600, 0), (600, 400)]),
             LineString([(600, 400), (0, 400)]), LineString([(0, 400), (0, 0)])]
    sketch = [(10, -8), (612, 6), (590, 410), (-7, 395)]
    polygon, report = ro.snap_polygon(sketch, drawn, max_offset_px=20)
    check("a rough sketch snaps to the drawn room and takes its exact area",
          report == {"edges": 4, "snapped_edges": 4} and abs(polygon.area - 600 * 400) < 1)
    loose, loose_report = ro.snap_polygon([(10, -8), (612, 6), (590, 410), (-7, 395)], drawn[:2], max_offset_px=20)
    check("edges with no drawn line nearby keep the sketch and are counted", loose_report["snapped_edges"] == 2 and loose.area > 0)
    check("a printed area is preferred and a disagreeing measurement is reported",
          ro.choose_area(printed_m2=9.0, enclosed_m2=7.5)["area_m2"] == 9.0 and "differs" in ro.choose_area(printed_m2=9.0, enclosed_m2=7.5)["conflict"])
    check("without a printed area, an enclosed-walls area beats an AI outline",
          ro.choose_area(enclosed_m2=10.8, outline_m2=11.5)["source"] == "enclosed_walls"
          and ro.choose_area(outline_m2=11.5)["source"] == "ai_outline_snapped")


def simplify_and_run_checks():
    mm = 10.0  # 100 px = 1 m
    # 10 m x 6 m room with hairline artefacts on the top wall and a 100 mm deep, 300 mm wide pier on the left wall.
    ring = [(0, 0)] + [(100 + i * 0.1, 0.05 * (i % 2)) for i in range(20)] + [(1000, 0), (1000, 600), (0, 600),
            (0, 400), (10, 400), (10, 370), (0, 370)]
    room = Polygon(ring)
    simple, report = ro.simplify_outline(room, mm)
    check("hairline artefacts and a 100 mm pier are removed", report["edges_after"] == 4 and report["edges_before"] > 20)
    check("simplification changes the area by at most 1%", report["area_change"] <= 0.01 and abs(simple.area - room.area) / room.area <= 0.01)
    deep = Polygon([(0, 0), (1000, 0), (1000, 600), (0, 600), (0, 400), (60, 400), (60, 300), (0, 300)])
    kept, _report = ro.simplify_outline(deep, mm)
    check("a 600 mm deep recess is a real wall and is kept", len(kept.exterior.coords) - 1 == 8)
    expect_error("an empty outline is refused", lambda: ro.simplify_outline(Polygon(), mm), "positive area")

    # Shopfront along the bottom (y = 600): 7.2 m glazing, a 300 mm pier set 200 mm back, then 4.5 m glazing.
    shop = [(0, 0), (1200, 0), (1200, 600), (750, 600), (750, 580), (720, 580), (720, 600), (0, 600), (0, 0)]
    runs = ro.wall_runs(shop, mm)
    front = [row for row in runs if 3 in row["edge_indices"] or 6 in row["edge_indices"]]
    check("a shopfront broken by a pier is one wall run spanning its full width",
          len(front) == 1 and set(front[0]["edge_indices"]) >= {2, 3, 4, 5, 6} and abs(front[0]["span_m"] - 12.0) < 0.01)
    check("the run's in-line length leaves out the pier's own faces", abs(front[0]["length_m"] - 11.7) < 0.01)
    check("opposite sides of a room are separate runs even though they are parallel", len(runs) == 4)
    split = [(0, 0), (1000, 0), (1000, 600), (700, 600), (700, 500), (400, 500), (400, 600), (0, 600), (0, 0)]
    run_of = {index: row["run_index"] for row in ro.wall_runs(split, mm, max_gap_mm=2000) for index in row["edge_indices"]}
    check("pieces on one line more than 2 m apart are separate runs", run_of[2] != run_of[6])
    check("pieces on one line closer than the gap limit are one run",
          {index: row["run_index"] for row in ro.wall_runs(split, mm, max_gap_mm=4000) for index in row["edge_indices"]}[2]
          == {index: row["run_index"] for row in ro.wall_runs(split, mm, max_gap_mm=4000) for index in row["edge_indices"]}[6])


def raster_checks():
    from PIL import Image, ImageDraw
    mm = 10.0  # 1 px = 10 mm
    image = Image.new("RGB", (900, 700), "white")
    draw = ImageDraw.Draw(image)
    # 15 px (150 mm) thick walls around a 6 m x 4 m room, with a 90 px (900 mm) door gap in the bottom wall.
    for rectangle in [(100, 100, 715, 115), (100, 100, 115, 515), (700, 100, 715, 515), (100, 500, 350, 515), (440, 500, 715, 515)]:
        draw.rectangle(rectangle, fill="black")
    # Thin dimension lines and text-like strokes that must not count as walls.
    draw.line((100, 600, 715, 600), fill="black", width=1)
    draw.line((300, 300, 500, 300), fill="black", width=2)
    viewport = (0, 0, 900, 700)
    walls = ro.raster_wall_geometry(image, viewport, mm, min_wall_mm=60, cell_mm=10)
    rooms = ro.enclosed_rooms(walls, viewport, mm, door_gap_mm=1000)
    check("a scanned plan's thick walls enclose the room and thin lines are ignored",
          len(rooms) == 1 and abs(ro.area_m2(rooms[0], mm) - 5.85 * 3.85) / (5.85 * 3.85) < 0.03)

    class Page:
        bbox = (0, 0, 360, 280)
        lines, curves, rects = [], [], []
        images = [{"x0": 0, "top": 0, "x1": 360, "bottom": 280}]
    check("a page covered by an image with little drawn line work is raster",
          ro.page_is_raster(Page(), viewport, 2.5, mm, objects=[]))
    # Irregularly spaced drawn lines (walls, furniture), not a regular hatch: about 3 m of line per m² of plan.
    positions, x = [], 3
    while x < 900:
        positions.append(x)
        x += 9 + (x * 7) % 23
    busy = [line((x, 0), (x, 700), (0.24, 0.0, None)) for x in positions]
    check("the irregular lines are drawn work, not hatch, and dense enough for a vector plan",
          ro.hatch_styles(busy) == set() and ro.vector_line_density(busy, viewport, mm) >= ro.RASTER_MAX_LINE_DENSITY)
    check("a page with an image underneath but full vector line work is not raster",
          not ro.page_is_raster(Page(), viewport, 2.5, mm, objects=busy))
    Page.images = []
    check("a page with no images is never raster", not ro.page_is_raster(Page(), viewport, 2.5, mm, objects=[]))


def main():
    page_origin_checks()
    raster_checks()
    simplify_and_run_checks()
    outline_checks()
    enclosed_room_checks()
    wall_style_checks()
    naming_and_split_checks()


if __name__ == "__main__":
    main()
