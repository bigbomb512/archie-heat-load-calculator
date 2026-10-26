"""The sun for a site: its position and clear-sky sunlight every hour of a design day in each month, and the sunlight
on a wall, roof or window of any bearing, with the shade of an awning or verandah above it.

User decisions 2026-10-25: the load is worked out for all 24 hours of the 21st of every month, from the real sun angle
at the site (not a fixed table per façade direction), and awnings/verandahs shade the glass below them.

Method (pvlib, already a dependency):
- sun position: pvlib.solarposition (NREL SPA) at each whole hour of local clock time (daylight saving as the site's
  time zone has it on that date), the same hours the design temperatures use;
- clear-sky sunlight: pvlib's Ineichen–Perez model with its monthly Linke turbidity climatology for the site, giving the
  direct-normal, diffuse-horizontal and global-horizontal sunlight of a clear day;
- sunlight on a surface: pvlib.irradiance.get_total_irradiance, isotropic sky, ground reflectance 0.2, split into the
  direct beam and the diffuse (sky + ground) parts;
- shade: an awning, verandah, canopy or eave is a horizontal projection running the full width of the glass or wall.
  Its shadow drops depth × tan(profile angle) below it (profile angle: tan = tan(sun altitude) / cos(sun azimuth −
  surface azimuth)); the part of the glass or wall in that shadow gets no direct beam. Diffuse sunlight is kept in
  full, which errs on the side of more load.
No I/O.
"""

from functools import lru_cache
from math import cos, radians, tan

MONTH_DAY = 21
YEAR = 2025                 # a fixed ordinary year: the 21st of each month and that year's daylight-saving dates
ALBEDO = 0.2
METHOD = "pvlib solar position (NREL SPA) + Ineichen–Perez clear sky (Linke turbidity climatology), isotropic sky, albedo 0.2"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
CARDINAL_AZIMUTHS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
STATE_TIMEZONES = {"NSW": "Australia/Sydney", "ACT": "Australia/Sydney", "VIC": "Australia/Melbourne",
                   "QLD": "Australia/Brisbane", "SA": "Australia/Adelaide", "WA": "Australia/Perth",
                   "TAS": "Australia/Hobart", "NT": "Australia/Darwin"}
# Listed places that keep another state's clock.
PLACE_TIMEZONES = {"Broken Hill": "Australia/Broken_Hill"}


def timezone_for(state="", place=""):
    """The IANA time zone of an Australian state (or a place that keeps another state's clock); "" when unknown."""
    return PLACE_TIMEZONES.get(str(place or "").strip()) or STATE_TIMEZONES.get(str(state or "").strip().upper(), "")


@lru_cache(maxsize=32)
def _design_days(latitude, longitude, timezone):
    import pandas as pd
    from pvlib.location import Location
    site = Location(latitude, longitude, timezone)
    days = {}
    for month in range(1, 13):
        times = pd.date_range(f"{YEAR}-{month:02d}-{MONTH_DAY:02d} 00:00", periods=24, freq="1h", tz=timezone)
        position = site.get_solarposition(times)
        sky = site.get_clearsky(times, model="ineichen", solar_position=position)
        hours = []
        for index in range(24):
            altitude = float(position["apparent_elevation"].iloc[index])
            up = altitude > 0
            hours.append({"hour": index, "altitude_deg": round(altitude, 3), "azimuth_deg": round(float(position["azimuth"].iloc[index]), 3),
                          "zenith_deg": round(float(position["apparent_zenith"].iloc[index]), 3),
                          "dni_w_m2": round(float(sky["dni"].iloc[index]), 2) if up else 0.0,
                          "dhi_w_m2": round(float(sky["dhi"].iloc[index]), 2) if up else 0.0,
                          "ghi_w_m2": round(float(sky["ghi"].iloc[index]), 2) if up else 0.0})
        days[month] = tuple(tuple(sorted(row.items())) for row in hours)
    return days


def design_days(latitude, longitude, timezone):
    """{month 1–12: [24 hours of {hour, altitude_deg, azimuth_deg, zenith_deg, dni_w_m2, dhi_w_m2, ghi_w_m2}]} for the
    21st of each month at the site. Raises ValueError for a position or time zone that can't be used."""
    if latitude is None or longitude is None or not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
        raise ValueError("The sun needs the site's latitude and longitude.")
    if not timezone:
        raise ValueError("The sun needs the site's time zone.")
    cached = _design_days(round(float(latitude), 4), round(float(longitude), 4), str(timezone))
    return {month: [dict(row) for row in hours] for month, hours in cached.items()}


def surface_azimuth(orientation="", azimuth_deg=None):
    """Bearing the surface faces (degrees clockwise from true north): the exact bearing when known, else its compass
    direction; None for neither."""
    if isinstance(azimuth_deg, (int, float)) and not isinstance(azimuth_deg, bool):
        return float(azimuth_deg) % 360
    return CARDINAL_AZIMUTHS.get(str(orientation or "").upper())


def overhang_sunlit_fraction(altitude_deg, sun_azimuth_deg, surface_azimuth_deg, height_m, depth_m, gap_m=0.0):
    """Share of a vertical surface's height (glass or wall, height_m tall) in the direct sun under a horizontal
    projection depth_m deep whose underside is gap_m above the top of the surface. 1 when the sun is behind the
    surface or below the horizon (no direct beam to shade)."""
    if not height_m or height_m <= 0 or not depth_m or depth_m <= 0 or altitude_deg <= 0:
        return 1.0
    relative = ((sun_azimuth_deg - surface_azimuth_deg + 180) % 360) - 180
    facing = cos(radians(relative))
    if facing <= 0:
        return 1.0
    shadow = depth_m * tan(radians(altitude_deg)) / facing     # how far the shadow drops below the projection
    shaded = min(max(shadow - max(gap_m or 0.0, 0.0), 0.0), height_m)
    return round(1 - shaded / height_m, 6)


def obstruction_shaded_fraction(altitude_deg, sun_azimuth_deg, surface_azimuth_deg, bottom_m, top_m, distance_m, height_m):
    """Share of a vertical surface from bottom_m to top_m above the ground in the shadow of a building height_m tall
    whose face stands distance_m in front of it (user decision 2026-10-26). A point at height z is in shadow when the
    sun's ray from it, travelling distance / cos(azimuth difference) horizontally, is still below the building's top:
    z + distance × tan(altitude) / cos(difference) < height. The shadow fills from the bottom up."""
    if not distance_m or distance_m <= 0 or not height_m or height_m <= 0 or top_m <= bottom_m or altitude_deg <= 0:
        return 0.0
    relative = ((sun_azimuth_deg - surface_azimuth_deg + 180) % 360) - 180
    facing = cos(radians(relative))
    if facing <= 0:
        return 0.0                                    # sun behind the surface: no direct beam to block
    edge = height_m - distance_m * tan(radians(altitude_deg)) / facing   # shadow's top edge on the surface
    return round(min(max(edge - bottom_m, 0.0), top_m - bottom_m) / (top_m - bottom_m), 6)


def surface_hours(day, tilt_deg, azimuth_deg, shade=None, height_m=None, obstruction=None):
    """24 hours of sunlight on one surface on one design day: [{"direct_w_m2", "diffuse_w_m2", "sunlit_fraction",
    "total_w_m2"}]. tilt 0 = flat roof, 90 = wall or window. shade = {"depth_m", "gap_m"} for a projection above a
    vertical surface of height_m; the shaded part gets no direct beam. obstruction = {"distance_m", "height_m",
    "base_m" (the surface's bottom above the ground), "coverage" (1 for a building along the whole wall, less for
    part of it)}: a building opposite shades it from the bottom up; with an awning too, the sunlit share is what is
    left between the two shadows."""
    import numpy as np
    from pvlib.irradiance import get_total_irradiance
    up = np.array([row["altitude_deg"] > 0 for row in day])
    plane = get_total_irradiance(tilt_deg, azimuth_deg, np.array([row["zenith_deg"] for row in day]),
                                 np.array([row["azimuth_deg"] for row in day]), np.array([row["dni_w_m2"] for row in day]),
                                 np.array([row["ghi_w_m2"] for row in day]), np.array([row["dhi_w_m2"] for row in day]),
                                 albedo=ALBEDO, model="isotropic")
    direct = np.where(up, np.clip(np.nan_to_num(np.asarray(plane["poa_direct"], dtype=float)), 0, None), 0.0)
    diffuse = np.where(up, np.clip(np.nan_to_num(np.asarray(plane["poa_sky_diffuse"], dtype=float)
                                                 + np.asarray(plane["poa_ground_diffuse"], dtype=float)), 0, None), 0.0)
    result = []
    for index, row in enumerate(day):
        sunlit = 1.0
        if shade and tilt_deg >= 89 and height_m and up[index]:
            sunlit = overhang_sunlit_fraction(row["altitude_deg"], row["azimuth_deg"], azimuth_deg, height_m,
                                              shade.get("depth_m"), shade.get("gap_m", 0.0))
        if obstruction and tilt_deg >= 89 and height_m and up[index]:
            base = float(obstruction.get("base_m") or 0.0)
            blocked = obstruction_shaded_fraction(row["altitude_deg"], row["azimuth_deg"], azimuth_deg, base, base + height_m,
                                                  obstruction.get("distance_m"), obstruction.get("height_m"))
            sunlit = round(max(0.0, sunlit - blocked * float(obstruction.get("coverage", 1.0))), 6)
        result.append({"direct_w_m2": round(float(direct[index]), 2), "diffuse_w_m2": round(float(diffuse[index]), 2),
                       "sunlit_fraction": sunlit, "total_w_m2": round(float(direct[index]) * sunlit + float(diffuse[index]), 2)})
    return result


@lru_cache(maxsize=4096)
def _surface_totals(latitude, longitude, timezone, month, tilt_deg, azimuth_deg, depth_m, gap_m, height_m, obstruction):
    day = design_days(latitude, longitude, timezone)[month]
    shade = {"depth_m": depth_m, "gap_m": gap_m} if depth_m else None
    blocking = dict(zip(("distance_m", "height_m", "base_m", "coverage"), obstruction)) if obstruction else None
    return tuple(row["total_w_m2"] for row in surface_hours(day, tilt_deg, azimuth_deg, shade, height_m, blocking))


def surface_totals(site, month, tilt_deg, azimuth_deg, shade=None, height_m=None, obstruction=None):
    """The 24 hourly totals (W/m², direct in the sun + diffuse) on one surface on the 21st of a month, cached.
    site = {"latitude", "longitude", "timezone"}."""
    shade = shade or {}
    blocking = (tuple(round(float(obstruction.get(key) or default), 3) for key, default in
                      (("distance_m", 0.0), ("height_m", 0.0), ("base_m", 0.0), ("coverage", 1.0)))
                if obstruction and obstruction.get("distance_m") and obstruction.get("height_m") else None)
    return list(_surface_totals(round(float(site["latitude"]), 4), round(float(site["longitude"]), 4), str(site["timezone"]),
                                int(month), float(tilt_deg), round(float(azimuth_deg or 0.0) % 360, 1),
                                float(shade.get("depth_m") or 0.0), float(shade.get("gap_m") or 0.0),
                                float(height_m or 0.0), blocking))
