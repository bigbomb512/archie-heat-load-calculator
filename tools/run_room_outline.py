#!/usr/bin/env python3
"""Run Card P0 (automatic room outlines) on one project, with the manual ChatGPT route.

Four steps; each writes a packet folder with images and a prompt.txt to paste into
ChatGPT, and each later step reads the reply saved as reply.json in that folder.

    PYTHONPATH=. python3 tools/run_room_outline.py packets REVIEW_DIR --pdf FILE.pdf --page 20 --out OUT
        -> OUT/01_dimensions/  (one crop per dimension line) and OUT/02_wall_styles/
    PYTHONPATH=. python3 tools/run_room_outline.py areas REVIEW_DIR --pdf FILE.pdf --page 20 --out OUT
        -> needs OUT/02_wall_styles/reply.json; writes OUT/03_room_names/
    PYTHONPATH=. python3 tools/run_room_outline.py outlines REVIEW_DIR --pdf FILE.pdf --page 20 --out OUT
        -> needs OUT/03_room_names/reply.json; writes OUT/04_room_outlines/ for rooms still without an area
    PYTHONPATH=. python3 tools/run_room_outline.py apply REVIEW_DIR --pdf FILE.pdf --page 20 --out OUT
        -> needs OUT/01_dimensions/reply_N.json and OUT/03_room_names/reply.json (and 04 if written);
           writes OUT/determinations.json (scored by tools/evaluate_autonomous_tasks.py)

Nothing is written into the project; the output folder is a scratch area.
"""

import argparse
import json
import math
from pathlib import Path

import pdfplumber

from ai import room_outline as ro
from ai.dimension_wall_matcher import page_mm_per_px


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _context(review_dir, pdf_path, page_number):
    review = Path(review_dir)
    vector = _read(review / "vector_geometry.json", {})
    page = next((row for row in (vector.get("geometry_key_points") or {}).get("pages", []) if row.get("page") == page_number), {})
    image = (page.get("coordinate_systems") or {}).get("image_px", {})
    spatial = next((row for row in (_read(review / "spatial_ocr.json", {}) or {}).get("pages", []) if row.get("page") == page_number), {})
    with pdfplumber.open(pdf_path) as pdf:
        pdf_page = pdf.pages[page_number - 1]
        scale = image.get("image_width", pdf_page.width * 2.5) / float(pdf_page.width)
        objects = ro.extract_page_objects(pdf_page, scale)
        page_image = pdf_page.to_image(resolution=72 * scale).original
    dpi = 72 * scale
    declared = page_mm_per_px(spatial.get("scale_candidates", []), render_dpi=dpi)
    viewport = tuple((page.get("plan_viewport") or {}).get("bbox_px") or (0, 0, page_image.size[0], page_image.size[1]))
    return {"objects": objects, "image": page_image, "viewport": viewport, "declared_mm_per_px": declared}


def _inferred_rooms(review_dir):
    from ai import room_inference
    review = Path(review_dir)
    inferred = room_inference.infer(_read(review / "ai_input.json", {}), _read(review / "drawing_coverage.json"),
                                    _read(review / "spatial_ocr.json"), _read(review / "vector_geometry.json"))
    return [row for row in inferred.get("rooms", []) if row.get("label")]


def _room_names(review_dir):
    return sorted({row["label"] for row in _inferred_rooms(review_dir)})


def _printed_areas(review_dir):
    """Room areas printed on the drawings, as read by room detection."""
    return {row["label"]: float(row["area_m2"]) for row in _inferred_rooms(review_dir)
            if isinstance(row.get("area_m2"), (int, float)) and row["area_m2"] > 0}


def packets(args):
    context = _context(args.review_dir, args.pdf, args.page)
    out = Path(args.out)
    dimensions = out / "01_dimensions"
    dimensions.mkdir(parents=True, exist_ok=True)
    from PIL import ImageDraw
    rows = ro.dimension_line_candidates(context["objects"], None, (0, 0) + context["image"].size, limit=3)
    for index, row in enumerate(rows, start=1):
        (x1, y1), (x2, y2) = row["points"]
        image = context["image"].convert("RGB")
        ImageDraw.Draw(image).line([(x1, y1), (x2, y2)], fill=(255, 0, 0), width=5)
        pad = 160
        crop = image.crop((int(max(0, min(x1, x2) - pad)), int(max(0, min(y1, y2) - pad)),
                           int(min(image.size[0], max(x1, x2) + pad)), int(min(image.size[1], max(y1, y2) + pad))))
        crop.thumbnail((1024, 1024))
        crop.save(dimensions / f"dimension_{index}.png")
    (dimensions / "lines.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    (dimensions / "prompt.txt").write_text(ro.dimension_prompt() + "\n\n(Send one image at a time; save each reply as reply_N.json.)\n", encoding="utf-8")
    styles = out / "02_wall_styles"
    styles.mkdir(parents=True, exist_ok=True)
    summary = ro.style_summary(context["objects"], context["viewport"], context["declared_mm_per_px"])
    image, numbered = ro.render_styles(context["objects"], summary, context["viewport"])
    image.save(styles / "styles.png")
    (styles / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (styles / "prompt.txt").write_text(ro.wall_style_prompt(numbered), encoding="utf-8")
    print(f"Declared scale: {context['declared_mm_per_px']} mm/px · {len(rows)} dimension crops · {len(numbered)} styles in the legend")
    print(f"Next: paste {styles / 'prompt.txt'} with styles.png into ChatGPT; save the JSON reply as {styles / 'reply.json'}.")


def _areas(args, context):
    out = Path(args.out)
    summary = _read(out / "02_wall_styles" / "summary.json")
    numbered = [row for row in summary if not row["hatch"]][:14]
    wall_ids = ro.validate_wall_style_reply(_read(out / "02_wall_styles" / "reply.json"), numbered)
    walls = ro.wall_geometry(context["objects"], wall_ids, context["viewport"])
    rooms = ro.enclosed_rooms(walls, context["viewport"], context["declared_mm_per_px"], args.door_gap_mm)
    return wall_ids, rooms


def areas(args):
    context = _context(args.review_dir, args.pdf, args.page)
    _wall_ids, rooms = _areas(args, context)
    folder = Path(args.out) / "03_room_names"
    folder.mkdir(parents=True, exist_ok=True)
    image, transform = ro.render_candidate_areas(context["image"], rooms, context["viewport"])
    image.save(folder / "areas.png")
    names = _room_names(args.review_dir)
    (folder / "transform.json").write_text(json.dumps(transform), encoding="utf-8")
    (folder / "prompt.txt").write_text(ro.room_naming_prompt(names, len(rooms)), encoding="utf-8")
    print(f"{len(rooms)} enclosed areas; rooms to name: {', '.join(names)}")
    print(f"Next: paste {folder / 'prompt.txt'} with areas.png into ChatGPT; save the JSON reply as {folder / 'reply.json'}.")


def outlines(args):
    """Packet asking the AI to outline the rooms that got no area from walls or a split."""
    context = _context(args.review_dir, args.pdf, args.page)
    out = Path(args.out)
    _wall_ids, rooms = _areas(args, context)
    names, splits = ro.validate_naming_reply(_read(out / "03_room_names" / "reply.json"), len(rooms), _room_names(args.review_dir),
                                             _read(out / "03_room_names" / "transform.json"))
    placed = {name for name in names.values() if name != ro.NOT_A_ROOM} | {item["room"] for split in splits for item in split["rooms"]}
    missing = [name for name in _room_names(args.review_dir) if name not in placed and name not in _printed_areas(args.review_dir)]
    if not missing:
        print("Every room already has an area from walls, a split or a printed value; no outline packet needed.")
        return
    folder = out / "04_room_outlines"
    folder.mkdir(parents=True, exist_ok=True)
    image, transform = ro.render_candidate_areas(context["image"], [], context["viewport"])
    image.save(folder / "plan.png")
    (folder / "transform.json").write_text(json.dumps(transform), encoding="utf-8")
    (folder / "prompt.txt").write_text(ro.outline_prompt(missing), encoding="utf-8")
    print(f"Rooms to outline: {', '.join(missing)}")
    print(f"Next: paste {folder / 'prompt.txt'} with plan.png into ChatGPT; save the JSON reply as {folder / 'reply.json'}.")


def apply(args):
    context = _context(args.review_dir, args.pdf, args.page)
    out = Path(args.out)
    lines = _read(out / "01_dimensions" / "lines.json", [])
    calibrations, problems = [], []
    for index, row in enumerate(lines, start=1):
        reply = _read(out / "01_dimensions" / f"reply_{index}.json")
        if not reply or reply.get("value_mm") is None:
            continue
        try:
            calibrations.append(ro.calibration_from_dimension(reply["value_mm"], row["span_px"], context["declared_mm_per_px"]))
        except ValueError as error:
            problems.append(f"dimension {index}: {error}")
    if not calibrations:
        raise SystemExit("No printed dimension agreed with the declared scale; P0 cannot calibrate. " + "; ".join(problems))
    mm_per_px = sum(row["mm_per_px"] for row in calibrations) / len(calibrations)
    if any(abs(row["mm_per_px"] / mm_per_px - 1) > 0.02 for row in calibrations):
        raise SystemExit("Printed dimensions disagree with each other by more than 2%; not calibrating.")
    calibration = {**calibrations[0], "mm_per_px": mm_per_px, "dimension_count": len(calibrations)}
    wall_ids, rooms = _areas(args, context)
    folder = out / "03_room_names"
    names, splits = ro.validate_naming_reply(_read(folder / "reply.json"), len(rooms), _room_names(args.review_dir),
                                             _read(folder / "transform.json"))
    segments = ro.drawn_segments(context["objects"], [row["style_id"] for row in _read(out / "02_wall_styles" / "summary.json") if not row["hatch"]])
    outlines = []
    by_room = {}
    for index, name in names.items():
        if name != ro.NOT_A_ROOM:
            by_room.setdefault(name, []).append(rooms[index])
    from shapely.ops import unary_union
    for name, parts in by_room.items():
        outlines.append({"label": name, "polygon": unary_union(parts), "method": "enclosed_walls", "areas_merged": len(parts)})
    snap_px = 250 / mm_per_px  # snap the AI's split line to a drawn line within 250 mm
    for split in splits:
        line, report = ro.snap_polyline(split["line"], segments, snap_px)
        pieces = ro.split_area(rooms[split["area_index"]], line, split["rooms"])
        for room, polygon in pieces.items():
            outlines.append({"label": room, "polygon": polygon, "method": "ai_split_snapped",
                             "snapped_segments": sum(item["snapped"] for item in report), "segments": len(report)})
    outline_reply = _read(out / "04_room_outlines" / "reply.json")
    if outline_reply:
        for row in ro.validate_outline_reply(outline_reply, _room_names(args.review_dir), _read(out / "04_room_outlines" / "transform.json")):
            polygon, report = ro.snap_polygon(row["points"], segments, snap_px)
            outlines.append({"label": row["room"], "polygon": polygon, "method": "ai_outline_snapped", **report})
    printed = _printed_areas(args.review_dir)
    rooms_out = []
    for label in sorted({row["label"] for row in outlines} | set(printed)):
        measured = {row["method"]: ro.area_m2(row["polygon"], mm_per_px) for row in outlines if row["label"] == label}
        choice = ro.choose_area(printed.get(label), measured.get("enclosed_walls") or measured.get("ai_split_snapped"),
                                measured.get("ai_outline_snapped"))
        if choice["area_m2"] is not None:
            rooms_out.append({"label": label, "area_m2": round(choice["area_m2"], 2), "source": "ai",
                              "method": choice["source"], "conflict": choice["conflict"]})
    determinations = {"P0_rooms": rooms_out, "calibration": calibration, "problems": problems}
    (out / "determinations.json").write_text(json.dumps(determinations, indent=1), encoding="utf-8")
    (out / "outlines.json").write_text(json.dumps([{**{k: v for k, v in row.items() if k != "polygon"},
                                                     "points_image_px": [list(point) for point in (row["polygon"].exterior.coords if row["polygon"].geom_type == "Polygon" else row["polygon"].convex_hull.exterior.coords)],
                                                     "parts": len(getattr(row["polygon"], "geoms", [row["polygon"]]))}
                                                    for row in outlines], indent=1), encoding="utf-8")
    for row in determinations["P0_rooms"]:
        print(f"{row['label']}: {row['area_m2']} m² ({row['method']}){' — ' + row['conflict'] if row['conflict'] else ''}")
    print(f"Calibration {mm_per_px:.4f} mm/px from {len(calibrations)} printed dimension(s); wrote {out / 'determinations.json'}")


def main():
    parser = argparse.ArgumentParser(description="Run Card P0 automatic room outlines on one project.")
    parser.add_argument("step", choices=["packets", "areas", "outlines", "apply"])
    parser.add_argument("review_dir")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--door-gap-mm", type=float, default=ro.DEFAULT_DOOR_GAP_MM)
    args = parser.parse_args()
    {"packets": packets, "areas": areas, "outlines": outlines, "apply": apply}[args.step](args)


if __name__ == "__main__":
    main()
