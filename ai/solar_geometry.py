"""Hourly sun position and surface incidence for a site's cooling design day.

Sun position uses the NREL Solar Position Algorithm (Reda and Andreas, 2004,
NREL/TP-560-34302) as implemented by pvlib. This module gives geometry only:
where the sun is and how directly it strikes each façade direction and a flat
roof. It deliberately does not estimate irradiance (W/m²); that needs a cited
clear-sky or design-solar source, which is a separate, reviewed choice.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pvlib


MODEL = "NREL Solar Position Algorithm (Reda & Andreas 2004, NREL/TP-560-34302)"
# Outward-facing azimuths, degrees clockwise from true north.
FACADE_AZIMUTHS = {"N": 0.0, "NE": 45.0, "E": 90.0, "SE": 135.0, "S": 180.0, "SW": 225.0, "W": 270.0, "NW": 315.0}
HORIZONTAL = "horizontal"
TIME_BASIS = "local clock time (including daylight saving) at the start of each hour"
# Southern-hemisphere cooling design day: the 21st of the representative month,
# following the common load-calculation convention of using the 21st.
DEFAULT_MONTH, DEFAULT_DAY = 1, 21


def _check_site(latitude_deg, longitude_deg, timezone_name):
    if not isinstance(latitude_deg, (int, float)) or not -90 <= latitude_deg <= 90:
        raise ValueError("Latitude must be between -90 and 90 degrees.")
    if not isinstance(longitude_deg, (int, float)) or not -180 <= longitude_deg <= 180:
        raise ValueError("Longitude must be between -180 and 180 degrees.")
    try:
        return ZoneInfo(str(timezone_name))
    except Exception as error:
        raise ValueError("Timezone must be an IANA name such as Australia/Sydney.") from error


def sun_positions(latitude_deg, longitude_deg, timezone_name, year, month=DEFAULT_MONTH, day=DEFAULT_DAY,
                  elevation_m=0.0, pressure_pa=101325.0, temperature_c=12.0):
    """Return 24 hourly sun positions (degrees) for the given local date."""
    zone = _check_site(latitude_deg, longitude_deg, timezone_name)
    times = [datetime(year, month, day, hour, tzinfo=zone) for hour in range(24)]
    rows = positions_at(times, latitude_deg, longitude_deg, elevation_m, pressure_pa, temperature_c)
    return [{"hour": hour, **row} for hour, row in enumerate(rows)]


def positions_at(local_times, latitude_deg, longitude_deg, elevation_m=0.0, pressure_pa=101325.0, temperature_c=12.0):
    """Sun position at timezone-aware instants: apparent (refracted) zenith and
    elevation, and azimuth clockwise from true north, in degrees."""
    position = pvlib.solarposition.get_solarposition(
        pd.DatetimeIndex(local_times), latitude_deg, longitude_deg, altitude=elevation_m, pressure=pressure_pa,
        temperature=temperature_c, method="nrel_numpy",
    )
    return [{"zenith_deg": float(position["apparent_zenith"].iloc[index]),
             "azimuth_deg": float(position["azimuth"].iloc[index]),
             "elevation_deg": float(position["apparent_elevation"].iloc[index])}
            for index in range(len(position))]


def incidence_cosines(positions):
    """Cosine of the sun's incidence angle on each façade direction and a flat roof.

    Zero when the sun is below the horizon or behind the surface.
    """
    zenith = np.array([row["zenith_deg"] for row in positions], dtype=float)
    azimuth = np.array([row["azimuth_deg"] for row in positions], dtype=float)
    result = {}
    for name, (tilt, surface_azimuth) in {**{key: (90.0, value) for key, value in FACADE_AZIMUTHS.items()},
                                          HORIZONTAL: (0.0, 0.0)}.items():
        projection = pvlib.irradiance.aoi_projection(tilt, surface_azimuth, zenith, azimuth)
        result[name] = [round(max(0.0, float(value)), 6) if row["elevation_deg"] > 0 else 0.0
                        for value, row in zip(projection, positions)]
    return result


def design_day_geometry(location, year, month=DEFAULT_MONTH, day=DEFAULT_DAY):
    """Sun geometry for a confirmed site location (site_location_resolution 'location')."""
    if not isinstance(location, dict) or location.get("latitude_deg") is None or location.get("longitude_deg") is None:
        raise ValueError("Confirm the site location (latitude and longitude) before calculating sun positions.")
    elevation = (location.get("elevation") or {}).get("elevation_m") or 0.0
    positions = sun_positions(float(location["latitude_deg"]), float(location["longitude_deg"]), location.get("timezone", ""),
                              year, month, day, elevation_m=float(elevation))
    return {
        "model": MODEL, "implementation": f"pvlib {pvlib.__version__}",
        "location": {key: location.get(key) for key in ("latitude_deg", "longitude_deg", "state", "locality", "timezone")},
        "date": f"{year:04d}-{month:02d}-{day:02d}", "time_basis": TIME_BASIS,
        "positions": positions, "incidence_cosines": incidence_cosines(positions),
        "status": "provisional",
        "limitations": ["Geometry only: no irradiance (W/m²) is estimated here.",
                        "Design date is the 21st of the representative month by convention, not a site-specific peak."],
    }
