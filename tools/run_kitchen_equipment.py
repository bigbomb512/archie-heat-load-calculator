#!/usr/bin/env python3
"""Run Card Q's equipment-identification task (P6) on one project, with the manual ChatGPT route.

    PYTHONPATH=. python3 tools/run_kitchen_equipment.py packets REVIEW_DIR --pdf FILE.pdf --out OUT
        -> OUT/ plan_crop.png, elevation_N.png, prompt.txt, packet.json
    PYTHONPATH=. python3 tools/run_kitchen_equipment.py apply REVIEW_DIR --out OUT
        -> needs OUT/reply.json; writes OUT/determinations.json (P6_kitchen, scored by
           tools/evaluate_autonomous_tasks.py)

The kitchen outline comes from the project's current room traces (reviewer or AI-determined).
Nothing is written into the project.
"""

import argparse
import json
from pathlib import Path

import pdfplumber

from ai import kitchen_equipment as kitchen
from backend import reviewer_room_geometry_service


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _kitchen_trace(review_dir):
    traces = reviewer_room_geometry_service.current_records(reviewer_room_geometry_service._paths({"review_dir": str(review_dir)}))
    rows = [row for row in traces if "kitchen" in str(row.get("room_label", row.get("room_id", ""))).casefold()]
    if not rows:
        raise SystemExit("No current Kitchen outline (reviewer or AI-determined) in this project; run the room outlines first.")
    return rows[0]


def packets(args):
    review = Path(args.review_dir)
    ai_input, spatial = _read(review / "ai_input.json", {}), _read(review / "spatial_ocr.json", {})
    trace = _kitchen_trace(review)
    xs = [point[0] for point in trace["points_image_px"]]
    ys = [point[1] for point in trace["points_image_px"]]
    with pdfplumber.open(args.pdf) as pdf:
        plan = pdf.pages[trace["page"] - 1]
        image = plan.to_image(resolution=180).original.convert("RGB")
        scale = image.size[0] / float(plan.width)
        margin = 60
        bbox = (max(0, min(xs) - margin), max(0, min(ys) - margin), min(image.size[0], max(xs) + margin), min(image.size[1], max(ys) + margin))
        elevation_pages = kitchen.find_kitchen_elevation_pages(ai_input, spatial)[:2]
        region_pt = tuple(value / scale for value in bbox)
        labels = {trace["page"]: kitchen.page_labels(spatial, trace["page"], region_pt)}
        words = {trace["page"]: kitchen.page_words(spatial, trace["page"], region_pt)}
        for page in elevation_pages:
            labels[page] = kitchen.page_labels(spatial, page)
            words[page] = kitchen.page_words(spatial, page)
        packet, prompt = kitchen.build_packet(trace["page"], bbox, elevation_pages, labels, words)
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        crop = image.crop(tuple(int(value) for value in bbox))
        crop.thumbnail((kitchen.MAX_IMAGE_SIDE, kitchen.MAX_IMAGE_SIDE))
        crop.save(out / "plan_crop.png")
        for page in elevation_pages:
            sheet = pdf.pages[page - 1].to_image(resolution=180).original.convert("RGB")
            sheet.thumbnail((kitchen.MAX_IMAGE_SIDE, kitchen.MAX_IMAGE_SIDE))
            sheet.save(out / f"elevation_{page}.png")
    (out / "packet.json").write_text(json.dumps(packet, indent=1), encoding="utf-8")
    (out / "prompt.txt").write_text(prompt + "\n\n(Attach plan_crop.png as the plan of page "
                                    f"{trace['page']}, and each elevation_N.png as page N.)\n", encoding="utf-8")
    print(f"Prompt {len(prompt)} characters; images: plan_crop.png (page {trace['page']}), "
          + ", ".join(f"elevation_{page}.png" for page in elevation_pages))
    print(f"Next: paste {out / 'prompt.txt'} with the images into ChatGPT; save the JSON reply as {out / 'reply.json'}.")


def apply(args):
    out = Path(args.out)
    items = kitchen.validate_reply(_read(out / "reply.json"), _read(out / "packet.json"))
    source = "stand_in" if (_read(out / "reply.json") or {}).get("stand_in") else "ai_determined"
    determinations = {"P6_kitchen": kitchen.to_determinations(items, source)}
    if source == "stand_in":
        determinations["stand_in"] = True
    (out / "determinations.json").write_text(json.dumps(determinations, indent=1), encoding="utf-8")
    for item in items:
        print(f"{item['type']} × {item['count']} (p. {item['page']}, {item['evidence_kind']}: {item['quote'] or item['symbol']})")
    print(f"Wrote {out / 'determinations.json'}")


def main():
    parser = argparse.ArgumentParser(description="Run Card Q kitchen equipment identification on one project.")
    parser.add_argument("step", choices=["packets", "apply"])
    parser.add_argument("review_dir")
    parser.add_argument("--pdf")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.step == "packets" and not args.pdf:
        parser.error("packets needs --pdf")
    {"packets": packets, "apply": apply}[args.step](args)


if __name__ == "__main__":
    main()
