#!/usr/bin/env python3
"""Run Card S on a scanned drawing set, with the manual ChatGPT route.

    PYTHONPATH=. python3 tools/run_scan_reading.py packets FILE.pdf --plan-page 4 --out OUT
        -> OUT/areas/ tile_N.png + prompt.txt + packet.json      (S1 printed room areas)
           OUT/title/ page_N_<region>.png + prompt.txt           (S3 title-block transcription)
    PYTHONPATH=. python3 tools/run_scan_reading.py apply FILE.pdf --out OUT
        -> needs OUT/areas/reply.json and OUT/title/reply_<image name>.json;
           writes OUT/site/packet.json + prompt.txt (the site task) and, once
           OUT/site/reply.json exists, OUT/determinations.json (P0_rooms, P1_site)

Area calibration is tried on the outlines from the scan wall finder; when it
cannot calibrate, the reason is written and the printed areas still apply
(they need no calibration). Nothing is written into a project.
"""

import argparse
import json
from pathlib import Path

import pdfplumber

from ai import autonomous_tasks, room_outline
from ai import scan_reading as scan


DPI = 150


def _read(path):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _render(pdf, page_number):
    return pdf.pages[page_number - 1].to_image(resolution=DPI).original.convert("RGB")


def _plan_viewport(image):
    """The sheet without its right-hand title strip."""
    return (0, 0, int(image.width * 0.78), image.height)


def packets(args):
    out = Path(args.out)
    with pdfplumber.open(args.pdf) as pdf:
        image = _render(pdf, args.plan_page)
        tiles = scan.area_tiles(_plan_viewport(image))
        factors = [scan.tile_factor(tile) for tile in tiles]
        for index, (tile, factor) in enumerate(zip(tiles, factors), start=1):
            crop = image.crop(tuple(int(round(value)) for value in tile))
            crop = crop.resize((int(crop.width * factor), int(crop.height * factor)))
            (out / "areas").mkdir(parents=True, exist_ok=True)
            crop.save(out / "areas" / f"tile_{index}.png")
        _write(out / "areas" / "packet.json", {"task": "S1_printed_areas", "page": args.plan_page, "dpi": DPI,
                                                "tiles": tiles, "factors": factors})
        (out / "areas" / "prompt.txt").write_text(scan.area_prompt(len(tiles)) + "\n", encoding="utf-8")
        title_images = []
        for number in range(1, len(pdf.pages) + 1):
            page_image = _render(pdf, number)
            for crop in scan.title_block_crops(page_image.width, page_image.height):
                name = f"page_{number}_{crop['region']}.png"
                region = page_image.crop(tuple(int(value) for value in crop["bbox"]))
                region.thumbnail((scan.MAX_TILE_SIDE, scan.MAX_TILE_SIDE))
                (out / "title").mkdir(parents=True, exist_ok=True)
                region.save(out / "title" / name)
                title_images.append({"page": number, "region": crop["region"], "image": name})
        _write(out / "title" / "packet.json", {"task": "S3_title_transcription", "images": title_images})
        (out / "title" / "prompt.txt").write_text(scan.transcription_prompt() + "\n", encoding="utf-8")
    print(f"S1: paste {out / 'areas' / 'prompt.txt'} with tile_1..{len(tiles)}.png; save the JSON as {out / 'areas' / 'reply.json'}")
    print(f"S3: for each image in {out / 'title'}, paste prompt.txt with it; save each reply as reply_<image name>.json")


def apply(args):
    out = Path(args.out)
    packet = _read(out / "areas" / "packet.json")
    reply = _read(out / "areas" / "reply.json")
    determinations, report = {}, {}
    with pdfplumber.open(args.pdf) as pdf:
        image = _render(pdf, packet["page"])
        viewport = _plan_viewport(image)
        outlines, calibration_note = [], ""
        if reply:
            first = scan.validate_area_reply(reply, packet["tiles"], packet["factors"])
            denominator = first["scale_denominator"]
            working = scan.mm_per_px_from_scale(denominator or 100, DPI)
            walls = room_outline.raster_wall_geometry(image, viewport, working)
            outlines = room_outline.enclosed_rooms(walls, viewport, working)
            areas = scan.validate_area_reply(reply, packet["tiles"], packet["factors"], outlines)
            try:
                calibration = scan.calibration_from_areas(areas["rooms"], outlines,
                                                          scan.mm_per_px_from_scale(denominator, DPI) if denominator else None)
            except ValueError as error:
                calibration, calibration_note = None, str(error)
            report["S1"] = {"rooms": areas["rooms"], "scale_text": areas["scale_text"], "outlines_found": len(outlines),
                            "calibration": calibration, "calibration_blocked": calibration_note}
            determinations["P0_rooms"] = [{"label": room["label"], "area_m2": room["area_m2"], "source": "ai_determined",
                                           "method": room["source"]} for room in areas["rooms"]]
    title_packet = _read(out / "title" / "packet.json") or {"images": []}
    pages = {}
    for row in title_packet["images"]:
        transcription = _read(out / "title" / f"reply_{row['image']}.json")
        if transcription:
            pages.setdefault(row["page"], []).extend(scan.validate_transcription(transcription))
    if pages:
        site_packet, site_prompt = scan.site_packet_from_transcriptions(pages)
        _write(out / "site" / "packet.json", site_packet)
        (out / "site" / "prompt.txt").write_text(site_prompt + "\n", encoding="utf-8")
        site_reply = _read(out / "site" / "reply.json")
        if site_reply:
            site = autonomous_tasks.validate_site_reply(site_packet, json.dumps(site_reply))["site"]
            determinations["P1_site"] = {"site_text": site["text"] if site else "", "source": "ai_determined"}
            report["S3"] = {"site": site, "pages_transcribed": sorted(pages)}
    stand_in = any(isinstance(value, dict) and value.get("stand_in") is True
                   for value in [reply, _read(out / "site" / "reply.json")])
    if stand_in:
        determinations["stand_in"] = True
    _write(out / "determinations.json", determinations)
    _write(out / "report.json", report)
    for room in report.get("S1", {}).get("rooms", []):
        print(f"{room['label']}: {room['area_m2']} m² (printed {room['printed_text']!r}, tiles {room['tiles']})")
    if "S1" in report:
        print(f"Scan outlines found: {report['S1']['outlines_found']}; area calibration: "
              f"{report['S1']['calibration'] or 'blocked — ' + report['S1']['calibration_blocked']}")
    if "S3" in report:
        print(f"Site: {report['S3']['site']}")
    print(f"Wrote {out / 'determinations.json'}" + (" (stand-in)" if stand_in else ""))


def main():
    parser = argparse.ArgumentParser(description="Card S: read a scanned drawing set (printed areas, title blocks).")
    parser.add_argument("step", choices=["packets", "apply"])
    parser.add_argument("pdf")
    parser.add_argument("--plan-page", type=int, default=1)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    {"packets": packets, "apply": apply}[args.step](args)


if __name__ == "__main__":
    main()
