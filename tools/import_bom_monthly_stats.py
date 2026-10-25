#!/usr/bin/env python3
"""Import monthly mean maximum temperatures from Bureau of Meteorology climate-statistics pages the operators saved.

Optional: every listed location already has NASA POWER monthly values (config/nasa_power_monthly_max.json); an
imported BoM station replaces them for its location, which is worth doing where NASA's grid square is flagged as
running hotter than the town (hot coasts next to desert, e.g. Broome, Port Hedland).

    python3 tools/import_bom_monthly_stats.py ~/Downloads/bom-pages/ [output.json]

Save each station's "Climate statistics for Australian locations" page (www.bom.gov.au/climate/averages/tables/
cw_NNNNNN.shtml) as HTML into one folder, one page per listed design-temperature location you need. Each page is
matched to the nearest location of the AIRAH design-temperature list (ai/design_weather.py LOCATIONS) by the
station's latitude and longitude; a station more than 50 km from every listed location is skipped. When two pages
match one location, the closer station is kept.

BoM's default terms allow in-house use only, so the output is written outside this repository
(ai.design_weather.MONTHLY_TABLE_PATH, ~/.archie/…, or the given path) and is refused inside it. The calculation reads
it from there (ai/design_weather.py load_monthly_table).
"""

import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import design_weather  # noqa: E402

MAX_KM = 50


def _text(page):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


def parse(page):
    """{"station", "station_name", "latitude", "longitude", "mean_max": [12]} from one saved climate-statistics page."""
    text = _text(page)
    name = re.search(r"Site name:\s*(.+?)\s+Site number:\s*(\d{6})", text)
    lat = re.search(r"Latitude:\s*(\d+(?:\.\d+)?)\s*°\s*([NS])", text)
    lon = re.search(r"Longitude:\s*(\d+(?:\.\d+)?)\s*°\s*([EW])", text)
    row = re.search(r"Mean maximum temperature \(°C\)((?:\s+-?\d+(?:\.\d+)?){12})", text)
    if not (name and lat and lon and row):
        raise ValueError("not a BoM climate-statistics page with a mean maximum temperature row")
    values = [float(value) for value in row.group(1).split()]
    return {"station": name.group(2), "station_name": name.group(1).strip(),
            "latitude": float(lat.group(1)) * (-1 if lat.group(2) == "S" else 1),
            "longitude": float(lon.group(1)) * (-1 if lon.group(2) == "W" else 1), "mean_max": values}


def match(station):
    """(listed location, km) nearest the station."""
    best = min(((name, design_weather.distance_km(station["latitude"], station["longitude"], lat, lon))
                for name, (lat, lon, _state) in design_weather.LOCATIONS.items()), key=lambda item: item[1])
    return best


def build(folder):
    rows, notes = {}, []
    for path in sorted(Path(folder).expanduser().glob("*.htm*")):
        try:
            station = parse(path.read_text(encoding="latin-1"))
        except (OSError, ValueError) as error:
            notes.append(f"{path.name}: skipped ({error}).")
            continue
        name, km = match(station)
        if km > MAX_KM:
            notes.append(f"{path.name}: {station['station_name']} is {km:.0f} km from the nearest listed location ({name}); skipped.")
            continue
        if name in rows and rows[name]["distance_km"] <= km:
            notes.append(f"{path.name}: {name} already has a closer station ({rows[name]['station_name']}); skipped.")
            continue
        rows[name] = {"station": station["station"], "station_name": station["station_name"],
                      "distance_km": round(km, 1), "mean_max": station["mean_max"], "imported_from": path.name}
    return rows, notes


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[2]).expanduser() if len(argv) > 2 else design_weather.MONTHLY_TABLE_PATH
    if ROOT in out.resolve().parents:
        print("Refusing to write BoM data inside the repository (in-house use only); choose a path outside it.")
        return 1
    rows, notes = build(argv[1])
    for note in notes:
        print(note)
    if not rows:
        print("No usable climate-statistics pages found.")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": design_weather.MONTHLY_SOURCE, "locations": rows}, indent=1), encoding="utf-8")
    print(f"Imported {len(rows)} location(s) to {out}: {', '.join(sorted(rows))}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
