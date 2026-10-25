#!/usr/bin/env python3
"""Build config/nasa_power_monthly_max.json: the monthly mean daily maximum temperature at each listed design-weather
location (ai/design_weather.py LOCATIONS), from NASA POWER's daily data.

    python3 tools/build_nasa_power_monthly.py [start_year end_year]

User decision 2026-10-25: the other months' design days are the summer design day lowered by (the location's hottest-
month mean maximum − that month's), and NASA POWER supplies the monthly means (openly usable; cite the service,
version and date accessed). For each location this reads the daily T2M_MAX (2 m air temperature, daily maximum) for
whole years and averages it by calendar month. Checked against the Bureau of Meteorology's Sydney (Observatory Hill)
statistics on 2026-10-25: monthly means within 0.8 °C, month-to-month offsets within 0.5 K.
"""

from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import design_weather  # noqa: E402

OUT = ROOT / "config" / "nasa_power_monthly_max.json"
URL = ("https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M_MAX&community=RE"
       "&longitude={lon}&latitude={lat}&start={start}0101&end={end}1231&format=JSON")


def monthly_mean_max(latitude, longitude, start, end):
    with urllib.request.urlopen(URL.format(lat=latitude, lon=longitude, start=start, end=end), timeout=120) as reply:
        data = json.load(reply)
    fill = data.get("header", {}).get("fill_value", -999.0)
    by_month = defaultdict(list)
    for day, value in data["properties"]["parameter"]["T2M_MAX"].items():
        if value != fill:
            by_month[int(day[4:6])].append(float(value))
    if any(len(by_month[month]) < 20 * (end - start + 1) for month in range(1, 13)):
        raise ValueError("too many missing days")
    return ([round(sum(by_month[month]) / len(by_month[month]), 2) for month in range(1, 13)],
            sum(len(values) for values in by_month.values()), data.get("header", {}).get("api", {}).get("version", ""))


def main(argv):
    start, end = (int(argv[1]), int(argv[2])) if len(argv) > 2 else (2001, 2025)
    rows, version = {}, ""
    for name, (latitude, longitude, state) in sorted(design_weather.LOCATIONS.items()):
        try:
            values, days, version = monthly_mean_max(latitude, longitude, start, end)
        except (OSError, ValueError, KeyError) as error:
            print(f"{name}: skipped ({error}).")
            continue
        rows[name] = {"latitude": latitude, "longitude": longitude, "state": state, "mean_max": values, "days": days}
        print(f"{name}: {values}")
        time.sleep(1)
    accessed = date.today().isoformat()
    OUT.write_text(json.dumps({
        "source": (f"NASA POWER daily 2 m maximum air temperature (T2M_MAX, MERRA-2 based), averaged by month "
                   f"{start}–{end}; API {version}, accessed {accessed}"),
        "reference": ("The data was obtained from the National Aeronautics and Space Administration (NASA) Langley Research "
                      "Center (LaRC) Prediction of Worldwide Energy Resources (POWER) Project funded through the NASA "
                      f"Earth Science Division. POWER Daily API {version}, accessed {accessed}."),
        "parameter": "T2M_MAX daily, averaged by calendar month", "period": [start, end], "api_version": version,
        "accessed": accessed, "locations": rows}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} of {len(design_weather.LOCATIONS)} locations to {OUT}.")
    return 0 if len(rows) == len(design_weather.LOCATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
