"""Magnetic declination at a site, to turn a magnetic north arrow into true north (user decision 2026-10-26).

World Magnetic Model 2025 (valid 2025–2030) through pygeomag (MIT). Declination is east-positive: magnetic north lies
that many degrees east of true north, so a bearing read from a magnetic arrow plus the declination is the true bearing.
Checked 2026-10-26 against the published values: Sydney 12.8° E, Perth 1.5° W, Hobart 15.6° E. No I/O.
"""

from datetime import date
from functools import lru_cache

MODEL = "World Magnetic Model 2025 (pygeomag)"


def decimal_year(day=None):
    day = day or date.today()
    return round(day.year + (day.timetuple().tm_yday - 1) / 365.25, 3)


@lru_cache(maxsize=64)
def _declination(latitude, longitude, year):
    from pygeomag import GeoMag
    result = GeoMag(coefficients_file="wmm/WMM_2025.COF").calculate(glat=latitude, glon=longitude, alt=0, time=year,
                                                                     allow_date_outside_lifespan=True)
    return round(float(result.d), 2)


def declination_deg(latitude, longitude, year=None):
    """East-positive magnetic declination in degrees at the position, for the year (default: today)."""
    if latitude is None or longitude is None or not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
        raise ValueError("Declination needs the site's latitude and longitude.")
    return _declination(round(float(latitude), 3), round(float(longitude), 3), float(year or decimal_year()))
