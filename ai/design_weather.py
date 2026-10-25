"""Site summer design days from an imported design-temperature table (the AIRAH Technical Handbook, 4th ed. 2007).

The table itself is copyright AIRAH and is never stored in this repository: tools/import_airah_handbook_design_temps.py
reads the operators' own copy of the handbook and writes it to a file outside the repo (DEFAULT_TABLE_PATH, or the
path in ARCHIE_DESIGN_WEATHER_TABLE). This module only holds the public map positions of the listed locations.

For a confirmed site, the nearest listed location gives two comfort design days (the handbook's comfort data: 3 pm
values each exceeded on about 10 days a year, from AIRAH DA9, 1998):
- "hot":   the design dry bulb with its mean coincident wet bulb;
- "humid": the design wet bulb with its mean coincident dry bulb.
The "hot" day sizes the cooling load (the usual comfort basis); the "humid" day is calculated alongside and reported
as a dehumidification check, which matters where there is much outside air. Both days keep the hourly shape of the
preliminary design day, shifted so that 3 pm takes the design values.
"""

import json
import math
import os
from pathlib import Path

DEFAULT_TABLE_PATH = Path.home() / ".archie" / "airah_handbook_2007_design_temperatures.json"
TABLE_ENV = "ARCHIE_DESIGN_WEATHER_TABLE"
# Monthly mean maximum temperatures per listed location. User decisions 2026-10-25: the other months' design days are
# the summer design day lowered by (the location's hottest-month mean maximum − that month's mean maximum); the monthly
# means come from NASA POWER (config/nasa_power_monthly_max.json, built by tools/build_nasa_power_monthly.py; checked
# against BoM's Sydney statistics: offsets within 0.5 K). Where the operators have imported Bureau of Meteorology
# station pages (tools/import_bom_monthly_stats.py; BoM's terms: in-house use, so kept outside the repository), the
# station's values replace NASA's for that location.
NASA_MONTHLY_PATH = Path(__file__).resolve().parents[1] / "config" / "nasa_power_monthly_max.json"
MONTHLY_TABLE_PATH = Path.home() / ".archie" / "bom_monthly_climate.json"
MONTHLY_TABLE_ENV = "ARCHIE_MONTHLY_CLIMATE_TABLE"
MONTHLY_SOURCE = "Bureau of Meteorology climate statistics (mean maximum temperature by month), imported by the operators"
# NASA's grid squares are about 50 km across: on a hot coast next to desert (Broome, Port Hedland) the square runs
# hotter than the town. A hottest-month mean maximum within this of the location's design dry bulb is flagged.
NASA_SUSPECT_MARGIN_K = 1.5
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December")
DESIGN_HOUR = 15          # the handbook's comfort design temperatures are 3.00 pm values
FAR_KM = 50               # a site further than this from its listed location is flagged
SOURCE = "AIRAH Technical Handbook, 4th edition (2007), Design temperature data, p. 37 (from AIRAH DA9, 1998)"

# Public map positions (decimal degrees) of the Australian locations the handbook lists, for choosing the nearest.
LOCATIONS = {
    "Adelaide": (-34.93, 138.60, "SA"), "Alice Springs": (-23.70, 133.88, "NT"), "Albany": (-35.02, 117.88, "WA"),
    "Albury": (-36.08, 146.92, "NSW"), "Ballarat": (-37.56, 143.85, "VIC"), "Bendigo": (-36.76, 144.28, "VIC"),
    "Bunbury": (-33.33, 115.64, "WA"), "Brisbane": (-27.47, 153.03, "QLD"), "Broken Hill": (-31.95, 141.47, "NSW"),
    "Broome": (-17.96, 122.24, "WA"), "Canberra": (-35.28, 149.13, "ACT"), "Cairns": (-16.92, 145.77, "QLD"),
    "Cooma": (-36.24, 149.12, "NSW"), "Darwin": (-12.46, 130.84, "NT"), "Geelong": (-38.15, 144.36, "VIC"),
    "Grafton": (-29.69, 152.93, "NSW"), "Griffith": (-34.29, 146.05, "NSW"), "Geraldton": (-28.78, 114.61, "WA"),
    "Hobart": (-42.88, 147.33, "TAS"), "Katherine": (-14.47, 132.26, "NT"), "Kalgoorlie": (-30.75, 121.47, "WA"),
    "Launceston": (-41.44, 147.14, "TAS"), "Melbourne": (-37.81, 144.96, "VIC"), "Mildura": (-34.21, 142.14, "VIC"),
    "Mt. Gambier": (-37.83, 140.78, "SA"), "Mt. Isa": (-20.73, 139.49, "QLD"), "Newcastle": (-32.93, 151.78, "NSW"),
    "Perth": (-31.95, 115.86, "WA"), "Pt. Augusta": (-32.49, 137.77, "SA"), "Pt. Hedland": (-20.31, 118.58, "WA"),
    "Pt. Lincoln": (-34.73, 135.86, "SA"), "Pt. Pirie": (-33.19, 138.02, "SA"), "Sale": (-38.11, 147.07, "VIC"),
    "Sydney": (-33.87, 151.21, "NSW"), "Tennant Ck.": (-19.65, 134.19, "NT"), "Toowoomba": (-27.56, 151.95, "QLD"),
    "Townsville": (-19.26, 146.82, "QLD"), "Woomera": (-31.20, 136.83, "SA"), "Wagga": (-35.12, 147.37, "NSW"),
    "Wyndham": (-15.49, 128.12, "WA"),
}


def canonical(name):
    """A table row's location name in LOCATIONS' spelling ("Mt.Gambier" -> "Mt. Gambier"), or "" if not listed."""
    key = "".join(str(name or "").casefold().split()).replace(".", "")
    return next((known for known in LOCATIONS if "".join(known.casefold().split()).replace(".", "") == key), "")


def table_path():
    return Path(os.environ.get(TABLE_ENV, "").strip() or DEFAULT_TABLE_PATH)


def load_table(path=None):
    """The imported table ({"source", "locations": {name: {"cwb", "db", "wb", "cdb"}}}), or None when absent or bad."""
    path = Path(path) if path else table_path()
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = table.get("locations") if isinstance(table, dict) else None
    if not isinstance(rows, dict) or not rows:
        return None
    clean = {}
    for name, row in rows.items():
        try:
            values = {key: float(row[key]) for key in ("cwb", "db", "wb", "cdb")}
        except (KeyError, TypeError, ValueError):
            continue
        if canonical(name) and values["cwb"] <= values["db"] and values["wb"] <= values["cdb"] and 0 < values["db"] < 55:
            clean[canonical(name)] = values
    return {"source": str(table.get("source") or SOURCE), "locations": clean} if clean else None


def distance_km(lat1, lon1, lat2, lon2):
    radius = 6371.0088
    a1, o1, a2, o2 = map(math.radians, (lat1, lon1, lat2, lon2))
    return radius * 2 * math.asin(math.sqrt(math.sin((a2 - a1) / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin((o2 - o1) / 2) ** 2))


def nearest(latitude, longitude, table):
    """(location name, its design values, distance in km) for the listed location closest to the site."""
    best = None
    for name, values in (table or {}).get("locations", {}).items():
        lat, lon, _state = LOCATIONS[name]
        km = distance_km(float(latitude), float(longitude), lat, lon)
        if best is None or km < best[2]:
            best = (name, values, km)
    return best


def _shifted(base_hours, db_at_design, wb_at_design):
    """The base day's 24 hours moved so that 3 pm reads the given dry and wet bulb; wet bulb never above dry bulb."""
    design = base_hours[DESIGN_HOUR]
    db_shift, wb_shift = db_at_design - design["db"], wb_at_design - design["wb"]
    hours = []
    for point in base_hours:
        db = round(point["db"] + db_shift, 2)
        hours.append({**point, "db": db, "wb": round(min(db, point["wb"] + wb_shift), 2), "wet_bulb_basis": "thermodynamic"})
    return hours


def monthly_table_path():
    return Path(os.environ.get(MONTHLY_TABLE_ENV, "").strip() or MONTHLY_TABLE_PATH)


def _monthly_rows(path):
    try:
        table = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, {}
    rows = table.get("locations") if isinstance(table, dict) else None
    clean = {}
    for name, row in (rows or {}).items() if isinstance(rows, dict) else ():
        values = row.get("mean_max") if isinstance(row, dict) else None
        if canonical(name) and isinstance(values, list) and len(values) == 12 and all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and -10 < value < 50 for value in values):
            clean[canonical(name)] = {**row, "mean_max": [float(value) for value in values]}
    return table, clean


def load_monthly_table(path=None, nasa_path=None):
    """The monthly table ({"source", "locations": {name: {"kind", "source", "station", "station_name", "mean_max"}}}):
    NASA POWER's values for every listed location, replaced by the operators' imported BoM station values where they
    have them; None when neither is usable. Rows without 12 plausible values are dropped."""
    nasa, nasa_rows = _monthly_rows(nasa_path or NASA_MONTHLY_PATH)
    bom, bom_rows = _monthly_rows(path or monthly_table_path())
    locations = {}
    for name, row in nasa_rows.items():
        locations[name] = {"kind": "nasa_power", "source": str(nasa.get("source", "NASA POWER")),
                           "reference": str(nasa.get("reference", "")), "station": "",
                           "station_name": f"NASA POWER grid cell at {row.get('latitude')}, {row.get('longitude')}",
                           "mean_max": row["mean_max"]}
    for name, row in bom_rows.items():
        locations[name] = {"kind": "bom_station", "source": str(bom.get("source") or MONTHLY_SOURCE),
                           "station": str(row.get("station", "")), "station_name": str(row.get("station_name", "")),
                           "mean_max": row["mean_max"]}
    if not locations:
        return None
    sources = sorted({row["source"] for row in locations.values()})
    return {"source": "; ".join(sources), "locations": locations}


def monthly_days(values, base_hours, monthly_row):
    """The 12 monthly design days (the 21st of each month) of one location: the summer design day lowered, dry and wet
    bulb alike, by (hottest-month mean maximum − that month's mean maximum). The hottest month is the summer day."""
    if len(base_hours) != 24:
        raise ValueError("The base design day needs 24 hours.")
    mean_max = monthly_row["mean_max"]
    hottest = max(mean_max)
    days = []
    for index, month_max in enumerate(mean_max):
        offset = round(hottest - month_max, 2)
        db, wb = round(values["db"] - offset, 2), round(values["cwb"] - offset, 2)
        days.append({"month": index + 1, "month_name": MONTH_NAMES[index], "offset_c": offset,
                     "hottest": month_max == hottest and index == mean_max.index(hottest),
                     "title": (f"{MONTH_NAMES[index]} design day: summer design dry bulb"
                               + (f" lowered {offset:g} K (BoM monthly mean maximum)" if offset else "")),
                     "design_point": {"db": db, "wb": wb}, "hours": _shifted(base_hours, db, wb)})
    return days


def _with_months(result, monthly_table, base_hours):
    row = ((monthly_table or {}).get("locations") or {}).get(result["location"]) if result else None
    if result and row:
        warning = ""
        if row.get("kind", "nasa_power") == "nasa_power" and max(row["mean_max"]) > result["values"]["db"] - NASA_SUSPECT_MARGIN_K:
            warning = (f"NASA POWER's hottest-month mean maximum ({max(row['mean_max']):g} °C) is close to or above "
                       f"{result['location']}'s design dry bulb ({result['values']['db']:g} °C), so the month-to-month "
                       "differences may be off: NASA's grid square can run hotter than a coastal town (in the tropics the "
                       "two are naturally close, so this may be a false alarm). Importing the BoM station page for this "
                       "location replaces them.")
        result["monthly"] = {"kind": row.get("kind", ""), "source": row.get("source") or monthly_table.get("source", ""),
                             "station": row.get("station", ""), "station_name": row.get("station_name", ""),
                             "mean_max": row["mean_max"], "warning": warning,
                             "days": monthly_days(result["values"], base_hours, row)}
    return result


def design_days(values, base_hours):
    """The site's two summer design days, each 24 hours, from one location's design values."""
    if len(base_hours) != 24:
        raise ValueError("The base design day needs 24 hours.")
    return [
        {"day": "hot", "title": "Summer design day: design dry bulb with coincident wet bulb",
         "design_point": {"db": values["db"], "wb": values["cwb"]}, "hours": _shifted(base_hours, values["db"], values["cwb"])},
        {"day": "humid", "title": "Summer design day: design wet bulb with coincident dry bulb",
         "design_point": {"db": values["cdb"], "wb": values["wb"]}, "hours": _shifted(base_hours, values["cdb"], values["wb"])},
    ]


def for_site(site_location, base_hours, table, monthly_table=None):
    """The site's design days, or None when the site isn't confirmed with a position or no table is given.

    site_location is the job's site-location resolution ({"confirmed_address", "location": {"latitude_deg", ...}});
    table is load_table()'s result, passed in by the caller (nothing here reads the operators' file on its own)."""
    location = (site_location or {}).get("location", {}) if isinstance(site_location, dict) else {}
    address = str((site_location or {}).get("confirmed_address", "") if isinstance(site_location, dict) else "").strip()
    if not table or not address or location.get("latitude_deg") is None or location.get("longitude_deg") is None:
        return None
    name, values, km = nearest(location["latitude_deg"], location["longitude_deg"], table)
    return _with_months({"location": name, "state": LOCATIONS[name][2], "distance_km": round(km, 1), "far": km > FAR_KM,
                         "values": values, "source": table["source"], "days": design_days(values, base_hours)},
                        monthly_table, base_hours)


def for_location(name, base_hours, table, monthly_table=None):
    """The design days of a listed location chosen by the operators (Project tab), or None when it isn't in the table."""
    name = canonical(name)
    values = ((table or {}).get("locations") or {}).get(name)
    if not values:
        return None
    return _with_months({"location": name, "state": LOCATIONS[name][2], "distance_km": None, "far": False, "chosen": True,
                         "values": values, "source": table["source"], "days": design_days(values, base_hours)},
                        monthly_table, base_hours)


STATE_CAPITALS = {"NSW": "Sydney", "VIC": "Melbourne", "QLD": "Brisbane", "SA": "Adelaide", "WA": "Perth", "TAS": "Hobart",
                  "NT": "Darwin", "ACT": "Canberra"}


def suggested_location(address, site_location=None, table=None):
    """A first suggestion for the weather location: the nearest listed location to a confirmed site position; else a
    listed town named in the address; else the capital of the state in the address; else "". The operator confirms."""
    location = (site_location or {}).get("location", {}) if isinstance(site_location, dict) else {}
    names = set(((table or {}).get("locations") or {}) or LOCATIONS)
    if location.get("latitude_deg") is not None and location.get("longitude_deg") is not None and table:
        return nearest(location["latitude_deg"], location["longitude_deg"], table)[0]
    text = " ".join(str(address or "").replace(",", " ").split()).casefold()
    for name in sorted(names, key=len, reverse=True):
        if f" {name.casefold().rstrip('.')}" in f" {text}" and name not in STATE_CAPITALS.values():
            return name
    for state, capital in STATE_CAPITALS.items():
        if f" {state.casefold()} " in f" {text} " and capital in names:
            return capital
    return ""
