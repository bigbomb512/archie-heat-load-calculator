#!/usr/bin/env python3
"""Independent checks for reviewed geometric shading V1."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai.shading_geometry import geometric_shading_factor


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print(f"PASS - {name}")


def citation(reference):
    return [{"reference": reference, "page": 1, "excerpt": "Reviewed geometry"}]


def positions(azimuth=0, altitude=45):
    return [{"hour": hour, "azimuth_deg": azimuth, "altitude_deg": altitude} for hour in range(24)]


def surface():
    return {"surface_id": "glazing-1", "orientation": "N", "opening_width_m": 2, "opening_height_m": 2,
            "opening_mapping_status": "confirmed", "review_status": "confirmed"}


def record(geometry, azimuth=0, altitude=45):
    return {"record_id": "shade-1", "review_status": "confirmed", "source": "Elevation", "citations": citation("A-300"),
            "geometry": geometry, "hourly_sun_positions": positions(azimuth, altitude)}


def main():
    no_shade = geometric_shading_factor(surface(), record({"overhang_depth_m": 0.0001}, altitude=45), 12)
    check("near-zero confirmed geometry leaves the opening effectively unshaded", no_shade["status"] == "calculated" and no_shade["external_shading_factor"] > 0.999)
    full_overhang = geometric_shading_factor(surface(), record({"overhang_depth_m": 3}, altitude=45), 12)
    check("deep overhang produces fully bounded shade", full_overhang["external_shading_factor"] == 0)
    blocked = geometric_shading_factor(surface(), record({"obstruction_altitude_deg": 50}, altitude=45), 12)
    check("cited obstruction altitude can fully block direct sun", blocked["external_shading_factor"] == 0)
    back_of_plane = geometric_shading_factor(surface(), record({"overhang_depth_m": 3}, azimuth=180, altitude=45), 12)
    check("back-of-plane sun does not invent a diffuse/direct adjustment", back_of_plane["external_shading_factor"] == 1)
    bad = geometric_shading_factor({**surface(), "orientation": "internal"}, record({"overhang_depth_m": 1}), 12)
    check("unsupported orientation blocks geometry", bad["status"] == "blocked")


if __name__ == "__main__":
    main()
