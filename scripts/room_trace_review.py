"""Read-only, page-image review of saved dwelling room outlines.

The output directory must be outside output/web_review. Window counts are a
read-only replay with the current placement code, not saved placement counts.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import vector_geometry
from ai.room_outline import OPEN_FLOOR_MAX_M2
from backend import autonomous_tasks_service, reviewer_room_geometry_service
from scripts import eval_room_tracing, eval_window_placement


WEB_REVIEW = Path(__file__).resolve().parents[1] / "output" / "web_review"
COLOURS = {"normal": (0, 170, 90), "multi_name": (220, 30, 80),
           "overlap": (240, 125, 0), "implausible_area": (150, 50, 210),
           "outside_extent": (30, 100, 230)}


def _polygon(record):
    points = record.get("points_image_px") or []
    if len(points) < 3:
        return None
    try:
        shape = Polygon(points)
        shape = shape if shape.is_valid else shape.buffer(0)
        return shape if not shape.is_empty and shape.area > 0 else None
    except (TypeError, ValueError):
        return None


def flag_page(traces, labels, extent=None):
    """Classify trace parts and labels in image pixel coordinates."""
    shapes = [_polygon(row) for row in traces]
    result = []
    for index, row in enumerate(traces):
        shape = shapes[index]
        scale = float((row.get("calibration") or {}).get("mm_per_px") or 0)
        area = round(shape.area * (scale / 1000) ** 2, 2) if shape is not None and scale > 0 else None
        inside = [label for label in labels if shape is not None and shape.covers(Point(label["x"], label["y"]))]
        names = {eval_room_tracing._base(label["name"]) for label in inside}
        overlaps = [traces[other].get("trace_id") for other, peer in enumerate(shapes)
                    if other != index and shape is not None and peer is not None and scale > 0
                    and shape.intersection(peer).area * (scale / 1000) ** 2 > 0.5 + 1e-9]
        flags = []
        if len(names) > 1:
            flags.append("multi_name")
        if overlaps:
            flags.append("overlap")
        if area is not None and (area < 2 or area > OPEN_FLOOR_MAX_M2):
            flags.append("implausible_area")
        if extent is not None and shape is not None and shape.difference(extent).area > 1:
            flags.append("outside_extent")
        result.append({"trace_id": row.get("trace_id"), "room": row.get("room_label"),
                       "area_m2": area, "printed_names": [label["name"] for label in inside],
                       "name_mismatch": bool(inside) and all(
                           eval_room_tracing._base(label["name"]) != eval_room_tracing._base(row.get("room_label"))
                           for label in inside), "overlaps_with": overlaps, "flags": flags})
    outside = [label for label in labels if not any(
        shape is not None and shape.covers(Point(label["x"], label["y"])) for shape in shapes)]
    return result, outside


def _font(size):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _overlay(source, destination, traces, flags, outside, extent):
    with Image.open(source) as page:
        image = page.convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(max(14, min(image.size) // 90))
    for row, finding in zip(traces, flags):
        shape = _polygon(row)
        if shape is None:
            continue
        priority = ("multi_name", "overlap", "implausible_area", "outside_extent")
        colour = COLOURS[next((flag for flag in priority if flag in finding["flags"]), "normal")]
        for part in getattr(shape, "geoms", [shape]):
            if part.geom_type == "Polygon":
                draw.polygon(list(part.exterior.coords), fill=(*colour, 55), outline=(*colour, 255), width=5)
        point = shape.representative_point()
        area = "?" if finding["area_m2"] is None else f'{finding["area_m2"]:g}'
        label = f'{finding["room"] or "Unnamed"} · {area} m²'
        draw.text((point.x, point.y), label, font=font, anchor="mm", fill=(0, 0, 0, 255),
                  stroke_width=3, stroke_fill=(255, 255, 255, 255))
        for position, flag in enumerate(finding["flags"]):
            x = point.x + (position - (len(finding["flags"]) - 1) / 2) * 20
            y = point.y + font.size + 6
            draw.rectangle((x - 7, y - 7, x + 7, y + 7), fill=(*COLOURS[flag], 255),
                           outline=(0, 0, 0, 255), width=2)
    for label in outside:
        x, y = label["x"], label["y"]
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(255, 230, 0, 255),
                     outline=(0, 0, 0, 255), width=3)
        draw.text((x + 12, y), label["name"], font=font, anchor="lm", fill=(0, 0, 0, 255),
                  stroke_width=3, stroke_fill=(255, 255, 255, 255))
    legend = "yellow dot: printed label outside traces | red: multiple names | orange: overlap | purple: area | blue: extent"
    draw.text((12, 12), legend, font=font, fill=(0, 0, 0, 255), stroke_width=3,
              stroke_fill=(255, 255, 255, 255))
    if extent is None:
        draw.text((12, 18 + font.size), "Outer extent unavailable; check skipped", font=font,
                  fill=(0, 0, 0, 255), stroke_width=3, stroke_fill=(255, 255, 255, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(destination)


def review_job(root, out):
    coverage = eval_room_tracing.evaluate(root)
    records = reviewer_room_geometry_service.current_records(
        reviewer_room_geometry_service._paths({"review_dir": str(root)}))
    pages = []
    for page in coverage["pages"]:
        number = page["page"]
        traces = [row for row in records if row.get("page") == number and _polygon(row) is not None]
        extent = autonomous_tasks_service._tenancy_polygon(root, number)
        findings, outside = flag_page(traces, page["printed_names"], extent)
        screenshot = vector_geometry.screenshot_for_page(root / "chatgpt_packet" / "screenshots", number)
        rendered_from_pdf = False
        if screenshot is None:
            source_pdf = eval_room_tracing._read(root / "ai_input.json").get("source_pdf")
            if source_pdf and Path(source_pdf).is_file():
                stem = out / root.name / f"page_{number:03d}_source_render"
                stem.parent.mkdir(parents=True, exist_ok=True)
                try:
                    subprocess.run(["pdftoppm", "-f", str(number), "-l", str(number),
                                    "-singlefile", "-png", "-r", "150", source_pdf, str(stem)],
                                   check=True, capture_output=True)
                    screenshot = stem.with_suffix(".png")
                    rendered_from_pdf = True
                except (OSError, subprocess.CalledProcessError):
                    pass
        overlay = None
        if screenshot:
            overlay = f'{root.name}/page_{number:03d}_review.png'
            _overlay(screenshot, out / overlay, traces, findings, outside, extent)
        pages.append({"page": number, "level": page["level"], "overlay": overlay,
                      "screenshot_missing": screenshot is None, "rendered_from_pdf": rendered_from_pdf,
                      "outer_extent": "available" if extent is not None else "unavailable; check skipped",
                      "labels": page["printed_names"], "labels_outside": outside, "traces": findings})
    windows = eval_window_placement.evaluate(root)
    summary = {"trace_parts": sum(len(page["traces"]) for page in pages),
               "located_labels": sum(len(page["labels"]) for page in pages),
               "labels_outside": sum(len(page["labels_outside"]) for page in pages),
               "multi_name_traces": sum("multi_name" in trace["flags"] for page in pages for trace in page["traces"]),
               "mismatches": sum(trace["name_mismatch"] for page in pages for trace in page["traces"]),
               "implausible_areas": sum("implausible_area" in trace["flags"] for page in pages for trace in page["traces"]),
               "overlaps": sum("overlap" in trace["flags"] for page in pages for trace in page["traces"]),
               "outside_extent": sum("outside_extent" in trace["flags"] for page in pages for trace in page["traces"]),
               "extent_unavailable_pages": sum(page["outer_extent"].startswith("unavailable") for page in pages),
               "schedule_windows": windows["schedule"], "placed_windows": windows["placed"]}
    return {"job": root.name, "summary": summary, "pages": pages}


FIELDS = ("trace_parts", "located_labels", "labels_outside", "multi_name_traces", "mismatches",
          "implausible_areas", "overlaps", "outside_extent", "schedule_windows", "placed_windows")
WORSE_IF_MORE = {"labels_outside", "multi_name_traces", "mismatches", "implausible_areas",
                 "overlaps", "outside_extent"}


def _regressions(after, before):
    return [key for key in FIELDS if (after[key] > before[key] if key in WORSE_IF_MORE
                                     else after[key] < before[key])]


def _baseline_job(baseline, job):
    return baseline if (baseline / "reviewer_room_geometry.json").exists() else baseline / job.name


def _markdown(results, baseline):
    lines = ["# Room trace review", "", "Counts are diagnostic. Windows are read-only current-code placement replay.",
             "Overlaps count trace parts touching another part by more than 0.5 m². Extent checks use saved corner validated floor outlines only.", ""]
    for result in results:
        lines += [f'## {result["job"]}', ""]
        before = result.get("baseline", {}).get("summary")
        lines.append("| Measure | Before | After | Change |" if before else "| Measure | Count |")
        lines.append("|---|---:|---:|---|" if before else "|---|---:|")
        for key in FIELDS:
            after = result["summary"].get(key)
            if before:
                old = before.get(key)
                regression = key in _regressions(result["summary"], before)
                lines.append(f'| {key} | {old} | {after} | {"REGRESSION" if regression else ""} |')
            else:
                lines.append(f'| {key} | {after} |')
        lines += ["", f'Outer extent unavailable on {result["summary"]["extent_unavailable_pages"]} main plan pages.']
        for page in result["pages"]:
            lines.append(f'- Page {page["page"]}: {page["outer_extent"]}; overlay: {page["overlay"] or "screenshot missing"}')
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jobs", nargs="*", type=Path, help="job directories; default is every saved dwelling")
    parser.add_argument("--baseline", type=Path, help="original job or parent directory of original jobs")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if out.is_relative_to(WEB_REVIEW.resolve()) or out == WEB_REVIEW.resolve():
        parser.error("--out must be outside output/web_review")
    jobs = args.jobs or [path for path in sorted(WEB_REVIEW.iterdir()) if path.is_dir()
                         and eval_room_tracing._read(path / "room_use_resolution.json").get("dwelling_context", {}).get("is_dwelling") is True]
    for job in jobs:
        if not job.is_dir():
            parser.error(f"job directory does not exist: {job}")
    results = []
    for job in jobs:
        result = review_job(job, out)
        if args.baseline:
            original = _baseline_job(args.baseline, job)
            if not original.is_dir():
                parser.error(f"baseline job directory does not exist: {original}")
            before = review_job(original, out / "baseline")
            result["baseline"] = before
            result["regressions"] = _regressions(result["summary"], before["summary"])
        results.append(result)
        print(json.dumps({"job": job.name, **result["summary"]}), flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps({"jobs": results}, indent=2) + "\n")
    (out / "summary.md").write_text(_markdown(results, args.baseline) + "\n")


if __name__ == "__main__":
    main()
