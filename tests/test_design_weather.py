"""Site summer design days from an imported design-temperature table (values here are made up, not AIRAH's)."""

import json
from pathlib import Path
import tempfile
import unittest

from ai import ai_preliminary, design_weather, internal_gains_resolution, room_use_resolution
from tools import import_airah_handbook_design_temps as importer

TABLE = {"source": "Test table", "locations": {"Sydney": {"cwb": 20.0, "db": 31.0, "wb": 23.0, "cdb": 29.0},
                                               "Newcastle": {"cwb": 20.0, "db": 30.0, "wb": 22.0, "cdb": 26.0}}}
SITE = {"confirmed_address": "Shop G38/22 Lemon Tree Av, Melrose Park NSW 2114",
        "location": {"latitude_deg": -33.81298, "longitude_deg": 151.07054, "basis": "reviewer_cited_map"}}


class DesignWeatherTests(unittest.TestCase):
    def test_the_nearest_listed_location_gives_a_hot_and_a_humid_day_that_read_the_design_values_at_3_pm(self):
        base = ai_preliminary.load_pack()["scenario"]["hours"]
        days = design_weather.for_site(SITE, base, table=TABLE)
        self.assertEqual((days["location"], days["far"]), ("Sydney", False))
        self.assertAlmostEqual(days["distance_km"], 14.4, delta=0.5)
        hot, humid = days["days"]
        self.assertEqual((hot["hours"][15]["db"], hot["hours"][15]["wb"]), (31.0, 20.0))
        self.assertEqual((humid["hours"][15]["db"], humid["hours"][15]["wb"]), (29.0, 23.0))
        for day in (hot, humid):
            self.assertEqual(len(day["hours"]), 24)
            self.assertTrue(all(point["wb"] <= point["db"] for point in day["hours"]))
            # The base day's shape is kept: the same rise from the coolest hour to 3 pm.
            self.assertAlmostEqual(day["hours"][15]["db"] - min(point["db"] for point in day["hours"]),
                                   base[15]["db"] - min(point["db"] for point in base), places=6)

    def test_no_design_days_without_a_confirmed_site_or_a_table_and_far_sites_are_flagged(self):
        base = ai_preliminary.load_pack()["scenario"]["hours"]
        self.assertIsNone(design_weather.for_site({"location": SITE["location"]}, base, table=TABLE))   # not confirmed
        self.assertIsNone(design_weather.for_site(SITE, base, table=None))
        alice = {**SITE, "location": {"latitude_deg": -23.70, "longitude_deg": 133.88}}
        self.assertTrue(design_weather.for_site(alice, base, table=TABLE)["far"])

    def test_a_table_is_read_only_with_listed_locations_and_sensible_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.json"
            path.write_text(json.dumps({"locations": {"Mt.Gambier": {"cwb": 18, "db": 34, "wb": 19, "cdb": 29},
                                                      "Atlantis": {"cwb": 18, "db": 34, "wb": 19, "cdb": 29},
                                                      "Sydney": {"cwb": 35, "db": 31, "wb": 23, "cdb": 29}}}))
            table = design_weather.load_table(path)
            self.assertEqual(list(table["locations"]), ["Mt. Gambier"])                   # unknown and impossible rows dropped
            path.write_text("not json")
            self.assertIsNone(design_weather.load_table(path))

    def test_the_importer_reads_the_table_rows_and_refuses_to_write_into_the_repository(self):
        text = ("Section 2\nDesign temperature data\n   CWB  DB  WB  CDB\nAUSTRALIA\n"
                "Sydney                  19.0             30.0            22.0             28.0        33.0\n"
                "Mt.Gambier              18.0             33.0            19.0             29.0\n"
                "Tennant Ck.             21.0            40.0             25.0             35.0\n"
                "CWB = Coincident wet bulb, CDB = Coincident dry bulb\n")
        rows = importer.parse(text)
        self.assertEqual(rows["Sydney"], {"cwb": 19.0, "db": 30.0, "wb": 22.0, "cdb": 28.0})
        self.assertEqual(sorted(rows), ["Mt. Gambier", "Sydney", "Tennant Ck."])
        inside = Path(importer.ROOT) / "airah.json"
        self.assertEqual(importer.main(["x", "/nonexistent.pdf", str(inside)]), 1)
        self.assertFalse(inside.exists())


class DraftDesignDayTests(unittest.TestCase):
    def assembled(self, site, table):
        building = {"spaces": [{"name": "Dining", "level_name": "Ground", "area_m2": 200, "evidence": [{"page": 2, "excerpt": "DINING"}]}]}
        room = {"kind": "room", "label": "Dining", "level_name": "Ground", "area_m2": 200, "preliminary_profile_id": "hospitality", "page": 2}
        proposal = {"rooms": [room]}
        use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
        gains = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=ai_preliminary.load_pack(),
                                                  source_fingerprints={"proposal": "a"})
        return ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use, internal_gains_resolution=gains,
                                       site_location=site, design_weather_table=table)

    def test_a_confirmed_site_is_sized_on_the_dry_bulb_day_and_checked_on_the_humid_day(self):
        generic = self.assembled(SITE, None)
        sited = self.assembled(SITE, TABLE)
        days = sited["material"]["design_day_scenarios"]["scenarios"]
        self.assertEqual([row["scenario_id"] for row in days], ["ai_preliminary_cooling_day", "ai_preliminary_humid_day"])
        basis = sited["design_conditions_basis"]["design_day"]
        self.assertTrue(basis["site_specific"])
        self.assertEqual((basis["location"], basis["far_from_site"]), ("Sydney", False))
        self.assertIn("31 °C dry bulb with 20 °C wet bulb", basis["label"])
        self.assertIn("dehumidification check", basis["label"])
        report = ai_preliminary.calculate(sited)
        self.assertEqual([row["scenario_id"] for row in report["scenario_results"]], ["ai_preliminary_cooling_day"])
        check = report["humid_day_check"]
        self.assertEqual(check["scenario_id"], "ai_preliminary_humid_day")
        self.assertGreater(check["latent_kw"], report["included_scope_peak"]["latent_kw"])   # more moisture on the humid day
        self.assertGreater(check["final_design_total_kw"], 0)
        # The generic preliminary day (36 °C / 24 °C at 3 pm) is harsher than these design days.
        self.assertLess(report["included_scope_peak"]["design_total_kw"], ai_preliminary.calculate(generic)["included_scope_peak"]["design_total_kw"])
        self.assertFalse(generic["design_conditions_basis"]["design_day"]["site_specific"])
        unconfirmed = self.assembled({"location": SITE["location"]}, TABLE)
        self.assertEqual(len(unconfirmed["material"]["design_day_scenarios"]["scenarios"]), 1)


if __name__ == "__main__":
    unittest.main()
