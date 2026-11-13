#!/usr/bin/env python3
"""Hand-worked reference rooms for the AI draft path: an independent worksheet, never a project method.

    python3 tests/reference/draft_reference_rooms.py            # print the worked values
    python3 tests/reference/draft_reference_rooms.py --write    # also write tests/fixtures/draft_reference_rooms_v1.json

No Archie imports. Every physical step is a plain textbook equation written out here; the only declared inputs are
(a) the rooms themselves, (b) the assumption-pack choices the draft makes (U-values, SHGC, densities, W/m², opening
hours, activity gains: product choices, copied as numbers), and (c) the clear-sky direct and diffuse sunlight for
Sydney (tests/reference/sydney_clear_sky_2025.json, generated once with pvlib's Ineichen model; that model is not
re-derived here). Sun position, the sunlight on each surface, shading, sol-air, glass, people, lighting, equipment,
outside-air psychrometrics and fan heat are all worked independently. Agreement shows the draft path does this
arithmetic and physics as written here; it is not engineering validation and not DA09/Camel parity
(docs/draft_reference_rooms.md).
"""

import json
from math import acos, asin, atan2, cos, degrees, exp, pi, radians, sin, tan
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "fixtures" / "draft_reference_rooms_v1.json"
SKY = json.loads((HERE / "sydney_clear_sky_2025.json").read_text(encoding="utf-8"))

# ---- Declared inputs: site, design days (synthetic, not AIRAH/BoM copies) and the pack's base day shape ------------
LATITUDE, LONGITUDE = SKY["latitude"], SKY["longitude"]
INDOOR_DB, INDOOR_WB, PRESSURE = 24.0, 18.0, 101.325
DESIGN = {"db": 32.0, "cwb": 23.0}                       # 3 pm summer design point (synthetic)
MONTHLY_MEAN_MAX = [26.0, 25.8, 24.8, 22.5, 19.5, 17.0, 16.4, 17.9, 20.1, 22.2, 23.7, 25.3]   # synthetic
BASE_DAY = [(27, 20), (27, 20), (26, 20), (26, 20), (26, 20), (26, 20), (27, 20), (28, 20), (29, 21), (30, 21),
            (31, 22), (32, 22), (33, 23), (34, 23), (35, 24), (36, 24), (36, 24), (35, 24), (34, 23), (32, 22),
            (30, 21), (29, 21), (28, 20), (27, 20)]   # the pack's preliminary design-day shape (db, wb)
DESIGN_HOUR = 15

# ---- Declared inputs: assumption-pack choices (product choices, not physics) ---------------------------------------
PACK = {
    "wall_u": 0.6, "roof_u": 0.5, "glass_u": 5.8, "shgc": 0.45, "frame_fraction": 0.15,
    "oa_per_person": 7.5, "oa_per_m2": 0.3, "fan_percent": 3.2,
    "profiles": {
        # Lighting: NCC 2022 Table J7D3a; people per m²: AS 1668.2-2002 Table A1 (pack value_sources, 2026-11-12).
        "retail": {"density": 0.286, "people_diversity": 0.85, "lighting_w_m2": 14, "lighting_diversity": 0.9,
                   "equipment_w_m2": 8, "equipment_diversity": 0.75, "equipment_space_gain": 0.8, "open": range(9, 18)},
        "office": {"density": 0.10, "people_diversity": 0.85, "lighting_w_m2": 4.5, "lighting_diversity": 0.9,
                   "equipment_w_m2": 15, "equipment_diversity": 0.8, "equipment_space_gain": 0.9, "open": range(8, 18)},
        "hospitality": {"density": 0.667, "people_diversity": 0.9, "lighting_w_m2": 14, "lighting_diversity": 0.9,
                        "equipment_w_m2": 25, "equipment_diversity": 0.8, "equipment_space_gain": 0.8, "open": range(10, 23)},
    },
    # A kitchen shares the hospitality profile but has its own rows: 4 W/m² and 3.5 m² per person.
    "room_use": {"kitchen": {"density": 0.286, "lighting_w_m2": 4}, "dining": {"density": 0.667, "lighting_w_m2": 14}},
    # Heat from people by room use (AIRAH Technical Handbook 2007, p. 47, room at 24 °C), sensible / latent W.
    "activity": {"retail": (76, 74), "office": (70, 50), "dining": (70, 50), "kitchen": (85, 135)},
    # Houses (config/house_profiles.json on the pack's residential profile; user decisions 2026-10-30): people come from
    # the household (2 in the main bedroom, 1 in each other; living areas hold the whole household, counted once per
    # hour across the house), no outside air, 0.36 ACH infiltration through rooms with a wall to outside at every hour.
    # Lighting 5 W/m² (NCC Class 1); sun counted in the NatHERS cooled hours ("cooled"), people/lights/appliances in
    # the occupied hours ("open"); leakage 0.45 ACH (CSIRO 2024, NSW two-storey houses, ÷ 20).
    "house": {
        "bedroom": {"people_diversity": 0.8, "lighting_w_m2": 5, "lighting_diversity": 0.85, "equipment_w_m2": 3,
                    "equipment_diversity": 0.75, "equipment_space_gain": 0.85, "open": [range(22, 24), range(0, 7)],
                    "cooled": [range(16, 24), range(0, 9)]},
        "living": {"people_diversity": 0.8, "lighting_w_m2": 5, "lighting_diversity": 0.85, "equipment_w_m2": 5,
                   "equipment_diversity": 0.75, "equipment_space_gain": 0.85, "open": [range(7, 8), range(15, 22)],
                   "cooled": [range(7, 24)]},
        "circulation": {"people_diversity": 0.8, "lighting_w_m2": 5, "lighting_diversity": 0.85, "equipment_w_m2": 0,
                        "equipment_diversity": 0.75, "equipment_space_gain": 0.85, "open": [range(7, 22)],
                        "cooled": [range(7, 24)]},
        "people": (67, 33), "infiltration_ach": 0.45,
        # House glass with no certificate: NatHERS default ALM-002-01 A, aluminium single clear, fixed or sliding.
        "glass_u": 6.7, "shgc": 0.70,
        # A house kitchen with no appliances on the drawings: a fridge all day; cooking 2000 W × 0.5 to the room at
        # half use from 5 to 7 pm.
        "kitchen_appliances": [{"w": 60, "factor": 1.0, "hours": {hour: 1.0 for hour in range(24)}},
                               {"w": 2000, "factor": 0.5, "hours": {17: 0.5, 18: 0.5}}],
    },
}

# ---- Declared inputs: stored heat (ASHRAE Fundamentals 2009 Ch 18 Radiant Time Series method) ----------------------
# Each room's radiant time series (nonsolar, solar) and each outside wall's and roof's conduction time series are
# declared numbers (tests/reference/draft_reference_series.json), like the clear-sky sunlight: they come from Archie's
# heat balance, which tests/test_stored_heat.py checks against published ASHRAE results, and are not re-derived here.
# What this worksheet works independently is the method itself: the split of each gain into the part reaching the air
# at once and the radiant part (Nigusse 2007 / ASHRAE 2009 splits, copied as numbers), and the hour-by-hour spreading.
SERIES = json.loads((HERE / "draft_reference_series.json").read_text(encoding="utf-8"))["cases"]
RADIANT = {"people": 0.60, "lighting": 0.40, "equipment": 0.0, "wall": 0.46, "roof": 0.60,
           "glass_conduction_high_shgc": 0.33, "glass_conduction_low_shgc": 0.46, "glass_solar": 1.0}

# ---- Physics constants, each from its source ------------------------------------------------------------------------
ALPHA_OVER_HO = 0.052   # m²K/W, dark surface: ASHRAE Handbook—Fundamentals, sol-air temperature (0.026 light)
ALBEDO = 0.2            # ground reflectance, common design value
STANDARD_AIR = 1.2      # kg dry air per m³ (AIRAH/ASHRAE simplified airflow-load method)


# ---- Sun position: Spencer (1971) series as given by Iqbal (1983); not pvlib --------------------------------------
def sun_position(month, hour, utc_offset_h, day=21, year=2025):
    days_before = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334][month - 1]
    n = days_before + day
    gamma = 2 * pi * (n - 1) / 365
    declination = (0.006918 - 0.399912 * cos(gamma) + 0.070257 * sin(gamma) - 0.006758 * cos(2 * gamma)
                   + 0.000907 * sin(2 * gamma) - 0.002697 * cos(3 * gamma) + 0.00148 * sin(3 * gamma))
    eot_min = 229.18 * (0.000075 + 0.001868 * cos(gamma) - 0.032077 * sin(gamma) - 0.014615 * cos(2 * gamma)
                        - 0.040849 * sin(2 * gamma))
    solar_time = hour - utc_offset_h + LONGITUDE / 15 + eot_min / 60
    omega = radians(15 * (solar_time - 12))
    phi = radians(LATITUDE)
    altitude = asin(sin(phi) * sin(declination) + cos(phi) * cos(declination) * cos(omega))
    azimuth = (degrees(atan2(sin(omega), cos(omega) * sin(phi) - tan(declination) * cos(phi))) + 180) % 360
    return degrees(altitude), azimuth


def sky(month, hour):
    return SKY["months"][str(month)][hour]


# ---- Sunlight on a surface (beam on the plane, isotropic sky, ground reflection) and its shading -------------------
# ---- Glass by incidence angle: ASHRAE Handbook—Fundamentals 2001 fenestration table, single clear 3.2 mm (ID 1a) ----
# Declared data (as reproduced in Arici, MEEM 4200 Ch. 7, Table 7-3): SHGC at 0, 40, 50, 60, 70, 80 degrees and diffuse.
# The pack's glass (U 5.8, SHGC 0.45) is single glazing, so it takes the single clear curve, relative to its 0-degree value.
SINGLE_CLEAR_SHGC = [(0, 0.86), (40, 0.84), (50, 0.82), (60, 0.78), (70, 0.67), (80, 0.42), (90, 0.0)]
SINGLE_CLEAR_DIFFUSE = 0.78


def glass_angle_ratio(incidence_deg):
    for (a0, s0), (a1, s1) in zip(SINGLE_CLEAR_SHGC, SINGLE_CLEAR_SHGC[1:]):
        if incidence_deg <= a1:
            return (s0 + (s1 - s0) * (incidence_deg - a0) / (a1 - a0)) / SINGLE_CLEAR_SHGC[0][1]
    return 0.0


def plane_sunlight(month, hour, tilt_deg, surface_azimuth, height=None, awning=None, building=None, glass=False):
    """Sunlight on a plane, W/m². glass=True: each part as single clear glass takes it relative to normal incidence
    (direct × SHGC(angle)/SHGC(0), diffuse × SHGC(diffuse)/SHGC(0))."""
    row = sky(month, hour)
    if row["dni"] <= 0 and row["dhi"] <= 0:
        return 0.0
    altitude, azimuth = sun_position(month, hour, row["utc_offset_h"])
    tilt = radians(tilt_deg)
    if tilt_deg == 0:
        cos_incidence = sin(radians(altitude))
        relative = 0.0
    else:
        relative = radians(((azimuth - surface_azimuth + 180) % 360) - 180)
        cos_incidence = cos(radians(altitude)) * cos(relative) * sin(tilt) + sin(radians(altitude)) * cos(tilt)
    beam = row["dni"] * max(cos_incidence, 0.0) if altitude > 0 else 0.0
    diffuse = row["dhi"] * (1 + cos(tilt)) / 2 + row["ghi"] * ALBEDO * (1 - cos(tilt)) / 2
    sunlit = 1.0
    if tilt_deg == 90 and beam > 0 and height:
        profile_tan = tan(radians(altitude)) / cos(relative)
        top = bottom = 0.0
        if awning:          # an awning's shadow drops depth × tan(profile angle) below it, from its underside down
            top = min(max(awning["depth_m"] * profile_tan - awning["gap_m"], 0.0), height) / height
        if building:        # a building opposite: the shadow's top edge on the surface is height − distance × tan(profile)
            edge = building["height_m"] - building["distance_m"] * profile_tan
            bottom = min(max(edge - building["base_m"], 0.0), height) / height * building.get("coverage", 1.0)
        sunlit = max(0.0, 1.0 - top - bottom)
    if glass:
        incidence = degrees(acos(min(max(cos_incidence, -1.0), 1.0)))
        return (beam * sunlit * glass_angle_ratio(incidence) if beam > 0 else 0.0) + diffuse * SINGLE_CLEAR_DIFFUSE / SINGLE_CLEAR_SHGC[0][1]
    return beam * sunlit + diffuse


# ---- Design days --------------------------------------------------------------------------------------------------
def design_day(month):
    offset = max(MONTHLY_MEAN_MAX) - MONTHLY_MEAN_MAX[month - 1]
    db_shift = DESIGN["db"] - offset - BASE_DAY[DESIGN_HOUR][0]
    wb_shift = DESIGN["cwb"] - offset - BASE_DAY[DESIGN_HOUR][1]
    return [(db + db_shift, min(db + db_shift, wb + wb_shift)) for db, wb in BASE_DAY]


# ---- Psychrometrics: ASHRAE Handbook—Fundamentals 2017, Ch. 1 -------------------------------------------------------
def saturation_kpa(t):
    """Eq. 6 (Hyland–Wexler, over liquid water)."""
    T = t + 273.15
    c = (-5.8002206e3, 1.3914993, -4.8640239e-2, 4.1764768e-5, -1.4452093e-8, 6.5459673)
    return exp(c[0] / T + c[1] + c[2] * T + c[3] * T ** 2 + c[4] * T ** 3 + c[5] * __import__("math").log(T)) / 1000


def humidity_ratio(db, wb):
    """Eq. 33 (wet bulb above freezing), with Eq. 22 for the saturated ratio at the wet bulb."""
    ws = 0.621945 * saturation_kpa(wb) / (PRESSURE - saturation_kpa(wb))
    return ((2501 - 2.326 * wb) * ws - 1.006 * (db - wb)) / (2501 + 1.86 * db - 4.186 * wb)


def enthalpy(db, w):
    """Eq. 32, kJ/kg dry air."""
    return 1.006 * db + w * (2501 + 1.86 * db)


def outside_air_kw(flow_lps, db, wb):
    mass = STANDARD_AIR * flow_lps / 1000
    w_out, w_in = humidity_ratio(db, wb), humidity_ratio(INDOOR_DB, INDOOR_WB)
    sensible = mass * (1.006 + 1.86 * w_out) * (db - INDOOR_DB)
    total = mass * (enthalpy(db, w_out) - enthalpy(INDOOR_DB, w_in))
    return sensible, total - sensible


def infiltration_kw(flow_lps, db, wb):
    """Leakage measured as outdoor air (the approved infiltration method): mass flow at the outdoor specific volume
    (Eq. 28), dry-air sensible heat, latent the rest of the enthalpy difference; only cooling gains count."""
    w_out, w_in = humidity_ratio(db, wb), humidity_ratio(INDOOR_DB, INDOOR_WB)
    specific_volume = 0.287042 * (db + 273.15) * (1 + 1.607858 * w_out) / PRESSURE
    mass = flow_lps / 1000 / specific_volume
    sensible = mass * 1.006 * (db - INDOOR_DB)
    total = mass * (enthalpy(db, w_out) - enthalpy(INDOOR_DB, w_in))
    return max(sensible, 0.0), max(total - sensible, 0.0)


# ---- One room, one hour ---------------------------------------------------------------------------------------------
def house_room_hour(room, month, hour):
    """A house room: house values; people, lights and appliances in its occupied hours; sun in its cooled hours;
    leakage at every hour."""
    house = PACK["house"]
    profile = house[room["profile"]]
    db, wb = design_day(month)[hour]
    open_now = 1.0 if any(hour in hours for hours in profile["open"]) else 0.0
    cooled_now = 1.0 if any(hour in hours for hours in profile["cooled"]) else 0.0
    out = {key: 0.0 for key in ("envelope", "solar", "glazing_conduction", "glazing_solar", "people_s", "people_l",
                                 "lighting", "equipment", "outside_air_s", "outside_air_l", "infiltration_s", "infiltration_l")}
    out["parts"] = []
    for surface in room.get("surfaces", []):
        envelope = surface["area_m2"] * surface["u"] * (db - INDOOR_DB) / 1000
        sun = plane_sunlight(month, hour, surface["tilt"], surface.get("azimuth", 0.0), surface.get("height_m"))
        solar = surface["area_m2"] * surface["u"] * ALPHA_OVER_HO * sun * cooled_now / 1000
        out["envelope"] += envelope
        out["solar"] += solar
        out["parts"].append({"label": surface["label"], "kind": surface["kind"], "envelope": envelope, "solar": solar})
    for glass in room.get("glass", []):
        out["glazing_conduction"] += house["glass_u"] * glass["area_m2"] * (db - INDOOR_DB) / 1000
        sun = plane_sunlight(month, hour, 90, glass["azimuth"], glass["height_m"], glass=True)
        out["glazing_solar"] += glass["area_m2"] * (1 - PACK["frame_fraction"]) * house["shgc"] * sun * cooled_now / 1000
    people = room.get("people", 0)
    out["people_s"] = people * house["people"][0] * profile["people_diversity"] * open_now / 1000
    out["people_l"] = people * house["people"][1] * profile["people_diversity"] * open_now / 1000
    out["lighting"] = room["area_m2"] * profile["lighting_w_m2"] * profile["lighting_diversity"] * open_now / 1000
    if room.get("kitchen"):
        out["equipment"] = sum(item["w"] * item["factor"] * item["hours"].get(hour, 0.0) for item in house["kitchen_appliances"]) / 1000
    else:
        out["equipment"] = (room["area_m2"] * profile["equipment_w_m2"] * profile["equipment_space_gain"]
                            * profile["equipment_diversity"] * open_now / 1000)
    if room.get("surfaces"):              # a wall to outside: leakage through it at every hour
        flow = house["infiltration_ach"] * room["area_m2"] * room["height_m"] / 3.6
        out["infiltration_s"], out["infiltration_l"] = infiltration_kw(flow, db, wb)
    out["people"] = people * open_now
    out["outside_air_lps"] = 0.0
    return out


def room_hour(room, month, hour):
    if room["profile"] in PACK["house"]:
        return house_room_hour(room, month, hour)
    profile = {**PACK["profiles"][room["profile"]], **PACK["room_use"].get(room.get("activity"), {})}
    db, wb = design_day(month)[hour]
    open_now = 1.0 if hour in profile["open"] else 0.0
    out = {key: 0.0 for key in ("envelope", "solar", "glazing_conduction", "glazing_solar", "people_s", "people_l",
                                 "lighting", "equipment", "outside_air_s", "outside_air_l", "infiltration_s", "infiltration_l")}
    out["parts"] = []
    for surface in room.get("surfaces", []):
        area, u = surface["area_m2"], surface["u"]
        if surface.get("adjacent"):          # a wall to a mall, room or tenancy: at the room's temperature
            continue
        envelope = area * u * (db - INDOOR_DB) / 1000
        sun = plane_sunlight(month, hour, surface["tilt"], surface.get("azimuth", 0.0), surface.get("height_m"),
                             None, surface.get("building"))
        solar = area * u * ALPHA_OVER_HO * sun * open_now / 1000
        out["envelope"] += envelope
        out["solar"] += solar
        out["parts"].append({"label": surface["label"], "kind": surface["kind"], "envelope": envelope, "solar": solar})
    for glass in room.get("glass", []):
        out["glazing_conduction"] += PACK["glass_u"] * glass["area_m2"] * (db - INDOOR_DB) / 1000
        sun = plane_sunlight(month, hour, 90, glass["azimuth"], glass["height_m"], glass.get("awning"), glass.get("building"), glass=True)
        out["glazing_solar"] += glass["area_m2"] * (1 - PACK["frame_fraction"]) * PACK["shgc"] * sun * open_now / 1000
    people = room.get("seats") or max(1, round(room["area_m2"] * profile["density"]))
    sensible_w, latent_w = PACK["activity"][room["activity"]]
    out["people_s"] = people * sensible_w * profile["people_diversity"] * open_now / 1000
    out["people_l"] = people * latent_w * profile["people_diversity"] * open_now / 1000
    out["lighting"] = room["area_m2"] * profile["lighting_w_m2"] * profile["lighting_diversity"] * open_now / 1000
    out["equipment"] = (room["area_m2"] * profile["equipment_w_m2"] * profile["equipment_space_gain"]
                        * profile["equipment_diversity"] * open_now / 1000)
    ventilation = people * PACK["oa_per_person"] + room["area_m2"] * PACK["oa_per_m2"]
    flow = max(ventilation, room.get("exhaust_lps", 0.0)) * open_now
    out["outside_air_s"], out["outside_air_l"] = outside_air_kw(flow, db, wb)
    out["people"] = people
    out["outside_air_lps"] = flow
    return out


# ---- Stored heat: one room, one whole day -----------------------------------------------------------------------------
def spread(series, gains):
    """Hour h receives series[j] of the gain j hours earlier (the day repeats)."""
    return [sum(series[j] * gains[(hour - j) % 24] for j in range(24)) for hour in range(24)]


def cooling(series, radiant, gains):
    """The non-radiant part reaches the air in its own hour; the radiant part is spread by the room's series."""
    released = spread(series, gains)
    return [(1 - radiant) * gains[hour] + radiant * released[hour] for hour in range(24)]


DAYS = {}


def room_day(case, room, month):
    """The room's 24 hours of cooling load: each hour's heat gains (room_hour) through the stored-heat step."""
    key = (case["case_id"], room["name"], month)
    if key in DAYS:
        return DAYS[key]
    hours = [room_hour(room, month, hour) for hour in range(24)]
    series = SERIES[case["case_id"]][room["name"]]
    nonsolar, solar = series["nonsolar"], series["solar"]
    shgc = PACK["house"]["shgc"] if room["profile"] in PACK["house"] else PACK["shgc"]
    glass_radiant = RADIANT["glass_conduction_high_shgc" if shgc > 0.5 else "glass_conduction_low_shgc"]
    day = [{name: value for name, value in row.items() if name != "parts"} for row in hours]
    for name, radiant, rts in (("people_s", RADIANT["people"], nonsolar), ("lighting", RADIANT["lighting"], nonsolar),
                               ("equipment", RADIANT["equipment"], nonsolar),
                               ("glazing_conduction", glass_radiant, nonsolar), ("glazing_solar", RADIANT["glass_solar"], solar)):
        for hour, value in enumerate(cooling(rts, radiant, [row[name] for row in hours])):
            day[hour][name] = value
    for name in ("envelope", "solar"):          # each wall and roof: its conduction series first, then the split
        totals = [0.0] * 24
        for index, part in enumerate(hours[0]["parts"]):
            gains = [row["parts"][index][name] for row in hours]
            cts = series["cts"].get(part["label"])
            arrived = spread(cts, gains) if cts else gains
            for hour, value in enumerate(cooling(nonsolar, RADIANT["roof" if part["kind"] == "roof" else "wall"], arrived)):
                totals[hour] += value
        for hour in range(24):
            day[hour][name] = totals[hour]
    DAYS[key] = day
    return day


# ---- The three reference rooms --------------------------------------------------------------------------------------
CASES = [
    {"case_id": "A_west_shop", "title": "80 m² shop, west shopfront, 2-storey building 15 m opposite (Sydney)",
     "rooms": [{"name": "Shop", "profile": "retail", "activity": "retail", "area_m2": 80.0,
                "surfaces": [{"label": "West wall", "kind": "wall", "tilt": 90, "azimuth": 270.0, "area_m2": 30.0 - 14.4,
                              "gross_m2": 30.0, "u": PACK["wall_u"], "height_m": 3.0,
                              "building": {"distance_m": 15.0, "height_m": 6.6, "base_m": 0.0}}],
                "glass": [{"label": "Shopfront", "azimuth": 270.0, "area_m2": 6.0 * 2.4, "width_m": 6.0, "height_m": 2.4,
                           "sill_m": 0.3, "building": {"distance_m": 15.0, "height_m": 6.6, "base_m": 0.3}}]}],
     "checks": [(1, 10), (1, 14), (1, 16), (1, 17), (1, 18), (6, 14)]},
    {"case_id": "B_roof_office", "title": "120 m² single-storey office, exposed roof, north window with a 1.5 m awning (Sydney)",
     "rooms": [{"name": "Office", "profile": "office", "activity": "office", "area_m2": 120.0,
                "surfaces": [{"label": "Roof", "kind": "roof", "tilt": 0, "azimuth": 0.0, "area_m2": 120.0, "gross_m2": 120.0,
                              "u": PACK["roof_u"]},
                             {"label": "North wall", "kind": "wall", "tilt": 90, "azimuth": 0.0, "area_m2": 36.0 - 6.0,
                              "gross_m2": 36.0, "u": PACK["wall_u"], "height_m": 3.0}],
                "glass": [{"label": "North window", "azimuth": 0.0, "area_m2": 4.0 * 1.5, "width_m": 4.0, "height_m": 1.5,
                           "sill_m": 0.9, "awning": {"depth_m": 1.5, "gap_m": 0.3}}]}],
     "checks": [(1, 12), (1, 15), (6, 12), (6, 15), (3, 13)]},
    {"case_id": "C_mall_dining", "title": "Mall restaurant: 150 m² dining with 100 printed seats and a 40 m² kitchen with 600 L/s exhaust",
     "rooms": [{"name": "Dining", "profile": "hospitality", "activity": "dining", "area_m2": 150.0, "seats": 100,
                "surfaces": [{"label": "Shopfront to the mall", "kind": "wall", "tilt": 90, "azimuth": 180.0, "area_m2": 36.0,
                              "gross_m2": 36.0, "u": PACK["wall_u"], "adjacent": "mall"}]},
               {"name": "Kitchen", "profile": "hospitality", "activity": "kitchen", "area_m2": 40.0, "exhaust_lps": 600.0}],
     "checks": [(1, 15), (1, 9), (7, 15)]},
    {"case_id": "D_house", "title": "Single-storey house: main bedroom with a west window, bedroom 2, north living room, kitchen and hall (Sydney)",
     "dwelling": True, "household": 3,
     "rooms": [{"name": "MASTER BEDROOM", "profile": "bedroom", "area_m2": 16.0, "height_m": 2.7, "people": 2,
                "surfaces": [{"label": "West wall", "kind": "wall", "tilt": 90, "azimuth": 270.0, "area_m2": 10.8 - 2.16,
                              "gross_m2": 10.8, "u": PACK["wall_u"], "height_m": 2.7}],
                "glass": [{"label": "West window", "azimuth": 270.0, "area_m2": 1.8 * 1.2, "width_m": 1.8, "height_m": 1.2,
                           "sill_m": 0.9}]},
               {"name": "BED 2", "profile": "bedroom", "area_m2": 11.0, "height_m": 2.7, "people": 1},
               {"name": "LIVING", "profile": "living", "area_m2": 30.0, "height_m": 2.7, "people": 3,
                "surfaces": [{"label": "North wall", "kind": "wall", "tilt": 90, "azimuth": 0.0, "area_m2": 16.2 - 6.3,
                              "gross_m2": 16.2, "u": PACK["wall_u"], "height_m": 2.7}],
                "glass": [{"label": "North doors", "azimuth": 0.0, "area_m2": 3.0 * 2.1, "width_m": 3.0, "height_m": 2.1,
                           "sill_m": 0.0}]},
               {"name": "KITCHEN", "profile": "living", "area_m2": 15.0, "height_m": 2.7, "people": 3, "kitchen": True},
               {"name": "HALL", "profile": "circulation", "area_m2": 8.0, "height_m": 2.7, "people": 0}],
     "checks": [(1, 10), (1, 16), (1, 17), (1, 23), (7, 16)]},
]


def people_counted(case, hour_rooms):
    """The share of the rooms' people heat a whole-house total keeps: a household is in one place at a time, so the
    people in its rooms this hour count at most once (shops: every room's people count)."""
    household = case.get("household")
    present = sum(row["people"] for row in hour_rooms)
    return min(1.0, household / present) if household and present else 1.0


def fan_kw(hour_rooms, share=1.0):
    room_sensible = sum(row["envelope"] + row["solar"] + row["glazing_conduction"] + row["glazing_solar"] + row["people_s"] * share
                        + row["lighting"] + row["equipment"] + row["infiltration_s"] for row in hour_rooms)
    return max(room_sensible, 0.0) * PACK["fan_percent"] / 100


def total_kw(hour_rooms, share=1.0):
    return sum(row["envelope"] + row["solar"] + row["glazing_conduction"] + row["glazing_solar"]
               + (row["people_s"] + row["people_l"]) * share + row["lighting"] + row["equipment"] + row["outside_air_s"]
               + row["outside_air_l"] + row["infiltration_s"] + row["infiltration_l"]
               for row in hour_rooms) + fan_kw(hour_rooms, share)


def case_total(case, month, hour):
    rows = [room_day(case, room, month)[hour] for room in case["rooms"]]
    return total_kw(rows, people_counted(case, rows))


def work(case):
    checks = []
    for month, hour in case["checks"]:
        rows = {room["name"]: room_day(case, room, month)[hour] for room in case["rooms"]}
        share = people_counted(case, list(rows.values()))
        checks.append({"month": month, "hour": hour,
                       "rooms": {name: {key: round(value, 4) for key, value in row.items()} for name, row in rows.items()},
                       "people_counted_share": round(share, 4),
                       "fan_heat_kw": round(fan_kw(list(rows.values()), share), 4),
                       "total_kw": round(total_kw(list(rows.values()), share), 4)})
    best = max(((month, hour, case_total(case, month, hour))
                for month in range(1, 13) for hour in range(24)), key=lambda item: (round(item[2], 6), -item[0], -item[1]))
    return {"case_id": case["case_id"], "title": case["title"],
            **{key: case[key] for key in ("dwelling", "household") if key in case}, "rooms": case["rooms"], "checks": checks,
            "peak": {"month": best[0], "hour": best[1], "total_kw": round(best[2], 4)}}


def sun_check():
    """Largest difference between this worksheet's sun position and pvlib's, over every sunlit hour."""
    worst = 0.0
    for month in range(1, 13):
        for row in SKY["months"][str(month)]:
            if row["pvlib_elevation_deg"] > 5:
                altitude, azimuth = sun_position(month, row["hour"], row["utc_offset_h"])
                worst = max(worst, abs(altitude - row["pvlib_elevation_deg"]),
                            abs(((azimuth - row["pvlib_azimuth_deg"] + 180) % 360) - 180) * cos(radians(altitude)))
    return round(worst, 3)


def make_rooms_serialisable(value):
    if isinstance(value, range):
        return [value.start, value.stop]
    if isinstance(value, dict):
        return {key: make_rooms_serialisable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_rooms_serialisable(item) for item in value]
    return value


def main(argv):
    result = {
        "schema_version": 1, "suite_id": "draft_reference_rooms_v1",
        "purpose": "Hand-worked reference rooms for the AI draft path (assemble → calculate); arithmetic and physics QA, "
                   "not engineering validation.",
        "worksheet": "tests/reference/draft_reference_rooms.py",
        "declared_inputs": make_rooms_serialisable({"site": {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": SKY["timezone"]},
                                                    "design_point": DESIGN, "monthly_mean_max": MONTHLY_MEAN_MAX,
                                                    "indoor": {"db": INDOOR_DB, "wb": INDOOR_WB, "pressure_kpa": PRESSURE},
                                                    "pack": PACK, "clear_sky": "tests/reference/sydney_clear_sky_2025.json"}),
        "physics": {"alpha_over_ho_m2k_w": ALPHA_OVER_HO, "albedo": ALBEDO, "standard_air_kg_m3": STANDARD_AIR,
                    "sun_position": "Spencer (1971) / Iqbal (1983)", "psychrometrics": "ASHRAE Fundamentals 2017 Ch. 1 Eq. 6, 22, 32, 33"},
        "tolerances": {"component_relative": 0.02, "component_absolute_kw": 0.02, "latent_relative": 0.03,
                       "total_relative": 0.01, "sun_position_deg": 1.0},
        "sun_position_check_deg": sun_check(),
        "cases": [make_rooms_serialisable(work(case)) for case in CASES],
    }
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if "--write" in argv:
        FIXTURE.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
