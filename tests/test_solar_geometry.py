#!/usr/bin/env python3
"""Card L part 2a: sun position and surface incidence for a site's design day."""

from datetime import datetime
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import solar_geometry


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS - " + name)


def expect_error(name, call, fragment):
    try:
        call()
    except ValueError as error:
        check(name, fragment in str(error))
        return
    raise AssertionError(name + " (no error raised)")


def reference_example():
    # The worked example published with the algorithm: Reda & Andreas (2004),
    # NREL/TP-560-34302 (Golden, Colorado, 17 Oct 2003 12:30:30, UTC-7).
    [position] = solar_geometry.positions_at(
        [datetime(2003, 10, 17, 12, 30, 30, tzinfo=ZoneInfo("Etc/GMT+7"))],
        39.742476, -105.1786, elevation_m=1830.14, pressure_pa=82000, temperature_c=11)
    check("topocentric zenith matches the published SPA example within 0.0001°", abs(position["zenith_deg"] - 50.11162) < 1e-4)
    check("topocentric azimuth matches the published SPA example within 0.0001°", abs(position["azimuth_deg"] - 194.34024) < 1e-4)


SYDNEY = {"latitude_deg": -33.8135, "longitude_deg": 151.0716, "timezone": "Australia/Sydney", "state": "NSW", "locality": "Melrose Park"}


def sydney_design_day():
    result = solar_geometry.design_day_geometry(SYDNEY, 2026)
    positions, cosines = result["positions"], result["incidence_cosines"]
    check("24 hourly positions on 21 January", len(positions) == 24 and result["date"] == "2026-01-21")
    peak = max(positions, key=lambda row: row["elevation_deg"])
    # Independent hand check: noon elevation ≈ 90° − |latitude − declination|,
    # with declination ≈ −20.0° on 21 January (Cooper's equation).
    declination = 23.45 * math.sin(math.radians(360 * (284 + 21) / 365))
    expected = 90 - abs(SYDNEY["latitude_deg"] - declination)
    check("peak sun elevation agrees with the hand check within 0.5°", abs(peak["elevation_deg"] - expected) < 0.5)
    check("in a Sydney summer the midday sun is north of overhead", peak["hour"] == 13 and (peak["azimuth_deg"] < 30 or peak["azimuth_deg"] > 330))
    night = [row["hour"] for row in positions if row["elevation_deg"] <= 0]
    check("no surface is sunlit at night", all(cosines[name][hour] == 0.0 for name in cosines for hour in night))
    check("east walls are sunlit only before solar noon, west walls only after",
          all(cosines["E"][hour] == 0 for hour in range(14, 24)) and all(cosines["W"][hour] == 0 for hour in range(0, 13)))
    check("south walls see early-morning and late-evening sun in a southern summer",
          cosines["S"][7] > 0.1 and cosines["S"][19] > 0.1 and cosines["S"][13] == 0)
    check("north walls are only grazed by the high midday sun", 0 < cosines["N"][13] < 0.3)
    check("the flat roof sees the sun most directly near midday",
          max(range(24), key=lambda hour: cosines["horizontal"][hour]) in {12, 13, 14} and cosines["horizontal"][13] > 0.95)
    check("every value is a finite cosine between 0 and 1",
          all(0 <= value <= 1 and math.isfinite(value) for values in cosines.values() for value in values))
    check("the result names its model and says it holds no irradiance",
          "Reda & Andreas" in result["model"] and any("no irradiance" in item for item in result["limitations"]))


def failure_states():
    expect_error("a site without coordinates is refused", lambda: solar_geometry.design_day_geometry({"timezone": "Australia/Sydney"}, 2026), "Confirm the site location")
    expect_error("an unknown timezone is refused", lambda: solar_geometry.design_day_geometry({**SYDNEY, "timezone": "Sydney"}, 2026), "IANA")
    expect_error("an impossible latitude is refused", lambda: solar_geometry.sun_positions(-95, 151, "Australia/Sydney", 2026), "Latitude")


def main():
    reference_example()
    sydney_design_day()
    failure_states()


if __name__ == "__main__":
    main()
