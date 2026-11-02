"""Hand-worked reference rooms through the AI draft path (assemble → calculate), part by part.

Expected values are constants in tests/fixtures/draft_reference_rooms_v1.json, worked by the independent worksheet
tests/reference/draft_reference_rooms.py (no Archie imports). Agreement shows the draft does this arithmetic and physics
as written there; it is not engineering validation (docs/draft_reference_rooms.md).
"""

import json
from pathlib import Path
import unittest

from ai import ai_preliminary, sun

ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "tests" / "fixtures" / "draft_reference_rooms_v1.json").read_text(encoding="utf-8"))
INPUTS = SUITE["declared_inputs"]
SITE = {"confirmed_address": "Reference room, Sydney NSW",
        "location": {"latitude_deg": INPUTS["site"]["latitude"], "longitude_deg": INPUTS["site"]["longitude"],
                     "timezone": INPUTS["site"]["timezone"], "state": "NSW"}}
DESIGN_TABLE = {"source": "Synthetic reference design point", "locations": {"Sydney": {
    "db": INPUTS["design_point"]["db"], "cwb": INPUTS["design_point"]["cwb"], "wb": 24.0, "cdb": 29.0}}}
MONTHLY_TABLE = {"source": "Synthetic reference monthly maximums", "locations": {"Sydney": {
    "kind": "bom_station", "source": "synthetic", "station": "000000", "station_name": "REFERENCE",
    "mean_max": INPUTS["monthly_mean_max"]}}}
COMPONENTS = {"envelope": ("envelope", "sensible_kw"), "solar": ("solar", "sensible_kw"),
              "glazing_conduction": ("glazing_conduction", "sensible_kw"), "glazing_solar": ("glazing_solar", "sensible_kw"),
              "people_s": ("people", "sensible_kw"), "people_l": ("people", "latent_kw"),
              "lighting": ("lighting", "sensible_kw"), "equipment": ("equipment_refrigeration", "sensible_kw"),
              "outside_air_s": ("outside_air", "sensible_kw"), "outside_air_l": ("outside_air", "latent_kw")}
CARDINAL = {0.0: "N", 90.0: "E", 180.0: "S", 270.0: "W"}


def proposal(case):
    """The case's rooms as the draft's room/wall/glass proposal."""
    rooms, surfaces, openings = [], [], []
    for room in case["rooms"]:
        name = room["name"]
        rooms.append({"kind": "room", "label": name, "level_name": "Ground", "area_m2": room["area_m2"],
                      "preliminary_profile_id": room["profile"], "confidence": 0.8, "page": 1, "excerpt": name,
                      "rationale": "Reference room.", **({"occupancy_count": room["seats"]} if room.get("seats") else {})})
        host_key = None
        for index, surface in enumerate(room.get("surfaces", [])):
            key = f"{name}-surface-{index}"
            row = {"surface_key": key, "label": f"{name} {surface['label']}", "owner_room_label": name, "owner_level_name": "Ground",
                   "physical_type": surface["kind"], "gross_area_m2": surface["gross_m2"], "page": 1, "confidence": 0.8}
            if surface.get("adjacent"):
                row.update({"thermal_role": "conditioned_adjacent", "external_exposure": "", "boundary_condition": surface["adjacent"],
                            "orientation": "", "opening_coverage": "not_applicable"})
            else:
                row.update({"thermal_role": "external", "external_exposure": "external",
                            "orientation": "horizontal" if surface["tilt"] == 0 else CARDINAL[surface["azimuth"]],
                            **({"azimuth_deg": surface["azimuth"]} if surface["tilt"] else {}),
                            "opening_coverage": "complete" if surface["kind"] == "wall" and room.get("glass") else "not_applicable"})
                if surface.get("building"):
                    row["obstruction"] = {**surface["building"], "wall_height_m": surface["height_m"], "coverage": 1.0}
            surfaces.append(row)
            if surface["kind"] == "wall" and not surface.get("adjacent"):
                host_key = key
        for index, glass in enumerate(room.get("glass", [])):
            openings.append({"opening_key": f"{name}-glass-{index}", "label": f"{name} {glass['label']}", "owner_room_label": name,
                             "owner_level_name": "Ground", "host_surface_key": host_key, "external_exposure": "external",
                             "orientation": CARDINAL[glass["azimuth"]], "azimuth_deg": glass["azimuth"],
                             "width_m": glass["width_m"], "height_m": glass["height_m"], "quantity": 1,
                             "shading_category": "unshaded", "page": 1, "confidence": 0.8,
                             **({"shade": {"kind": "awning", **glass["awning"]}} if glass.get("awning") else {}),
                             **({"obstruction": {**glass["building"], "coverage": 1.0}} if glass.get("building") else {})})
    return {"rooms": rooms, "surfaces": surfaces, "openings": openings}


def run(case, scope="full"):
    exhaust = {room["name"].casefold(): {"lps": room["exhaust_lps"], "method": "through_space"}
               for room in case["rooms"] if room.get("exhaust_lps")}
    model = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=proposal(case), site_location=SITE,
                                    design_weather_table=DESIGN_TABLE, monthly_climate_table=MONTHLY_TABLE,
                                    process_exhaust=exhaust or None)
    return model, ai_preliminary.calculate(model, scope=scope)


def room_hour(report, month, hour, name):
    scenario = next(row for row in report["scenario_results"]
                    if row["representative_month"] == ai_preliminary.design_weather.MONTH_NAMES[month - 1])
    room = next(row for row in scenario["rooms"] if row.get("name") == name)
    return room["hours"][hour]["components"]


class DraftReferenceRoomTests(unittest.TestCase):
    maxDiff = None

    def assert_close(self, label, expected, actual, relative):
        tolerance = max(SUITE["tolerances"]["component_absolute_kw"], abs(expected) * relative)
        self.assertLessEqual(abs(actual - expected), tolerance,
                             f"{label}: expected {expected:.4f} kW, Archie {actual:.4f} kW, deviation {actual - expected:+.4f} "
                             f"(tolerance {tolerance:.4f})")

    def test_every_component_at_every_check_hour(self):
        for case in SUITE["cases"]:
            _model, report = run(case)
            for check in case["checks"]:
                for name, expected in check["rooms"].items():
                    actual = room_hour(report, check["month"], check["hour"], name)
                    for key, (component, part) in COMPONENTS.items():
                        relative = SUITE["tolerances"]["latent_relative" if key.endswith("_l") else "component_relative"]
                        with self.subTest(case=case["case_id"], month=check["month"], hour=check["hour"], room=name, component=key):
                            self.assert_close(f"{case['case_id']} {name} month {check['month']} {check['hour']:02d}:00 {key}",
                                              expected[key], (actual.get(component) or {}).get(part, 0.0), relative)

    def test_the_sizing_month_hour_and_total(self):
        for case in SUITE["cases"]:
            _model, report = run(case)
            peak = report["included_scope_peak"]
            with self.subTest(case=case["case_id"]):
                self.assertEqual((peak["month"], peak["hour"]),
                                 (ai_preliminary.design_weather.MONTH_NAMES[case["peak"]["month"] - 1], case["peak"]["hour"]))
                self.assert_close(f"{case['case_id']} design total", case["peak"]["total_kw"], peak["raw_coincident_total_kw"],
                                  SUITE["tolerances"]["total_relative"])

    def test_phase1_is_the_envelope_and_sun_only(self):
        for case in SUITE["cases"]:
            _model, report = run(case, scope="phase1_base_room")
            check = case["checks"][0]
            expected = sum(check["rooms"][name][key] for name in check["rooms"]
                           for key in ("envelope", "solar", "glazing_conduction", "glazing_solar"))
            actual = sum((room_hour(report, check["month"], check["hour"], name).get(component) or {}).get("total_kw", 0.0)
                         for name in check["rooms"] for component in ("envelope", "solar", "glazing_conduction", "glazing_solar",
                                                                       "people", "lighting", "equipment_refrigeration", "outside_air"))
            with self.subTest(case=case["case_id"]):
                self.assert_close(f"{case['case_id']} Phase 1 at month {check['month']} {check['hour']:02d}:00", expected, actual,
                                  SUITE["tolerances"]["component_relative"])

    def test_the_worksheet_sun_agrees_with_archies_sun(self):
        self.assertLessEqual(SUITE["sun_position_check_deg"], SUITE["tolerances"]["sun_position_deg"])
        days = sun.design_days(INPUTS["site"]["latitude"], INPUTS["site"]["longitude"], INPUTS["site"]["timezone"])
        sky = json.loads((ROOT / "tests" / "reference" / "sydney_clear_sky_2025.json").read_text(encoding="utf-8"))
        for month in (1, 6):
            for row, declared in zip(days[month], sky["months"][str(month)]):
                self.assertAlmostEqual(row["altitude_deg"], declared["pvlib_elevation_deg"], delta=0.05)
                self.assertAlmostEqual(row["dni_w_m2"], declared["dni"], delta=0.5)


if __name__ == "__main__":
    unittest.main()
