"""The site's sun hour by hour, the 21st of every month, and awnings/verandahs shading the glass (2026-10-25)."""

from copy import deepcopy
import json
from math import tan, radians
from pathlib import Path
import tempfile
import unittest

from ai import ai_preliminary, design_weather, sun

SYDNEY = {"confirmed_address": "1 Test Street, Sydney NSW 2000",
          "location": {"latitude_deg": -33.87, "longitude_deg": 151.21, "timezone": "Australia/Sydney", "state": "NSW"}}
# Synthetic design values and monthly maximums (shapes like a coastal city; not copied from AIRAH or BoM).
DESIGN_TABLE = {"source": "Test design table", "locations": {"Sydney": {"db": 32.0, "cwb": 23.0, "wb": 24.0, "cdb": 29.0}}}
MONTHLY_TABLE = {"source": "Test monthly table", "locations": {"Sydney": {
    "station": "000000", "station_name": "TEST STATION",
    "mean_max": [26.0, 25.8, 24.8, 22.5, 19.5, 17.0, 16.4, 17.9, 20.1, 22.2, 23.7, 25.3]}}}


def proposal(shade=None, orientation="N"):
    room = {"kind": "room", "label": "Shop", "level_name": "Ground", "area_m2": 100, "preliminary_profile_id": "retail",
            "confidence": 0.7, "page": 3, "excerpt": "Shop", "rationale": "Test room."}
    opening = {"opening_key": "shop-front", "label": "Shop front glass", "owner_room_label": "Shop", "owner_level_name": "Ground",
               "host_surface_key": "shop-front-wall", "external_exposure": "external", "orientation": orientation,
               "width_m": 6.0, "height_m": 2.4, "quantity": 1, "shading_category": "unshaded", "page": 3, "confidence": 0.7}
    if shade:
        opening["shade"] = shade
    return {"rooms": [room],
            "surfaces": [{"surface_key": "shop-front-wall", "label": "Shop front wall", "owner_room_label": "Shop", "owner_level_name": "Ground",
                          "physical_type": "wall", "thermal_role": "external", "external_exposure": "external", "orientation": orientation,
                          "gross_area_m2": 18.0, "opening_coverage": "complete", "page": 3, "confidence": 0.7},
                         {"surface_key": "shop-roof", "label": "Shop roof", "owner_room_label": "Shop", "owner_level_name": "Ground",
                          "physical_type": "roof", "thermal_role": "external", "external_exposure": "external", "orientation": "horizontal",
                          "gross_area_m2": 100.0, "opening_coverage": "not_applicable", "page": 3, "confidence": 0.7}],
            "openings": [opening]}


def assemble(prop, monthly=True, site=True):
    return ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prop, site_location=SYDNEY if site else None,
                                   design_weather_table=DESIGN_TABLE if site else None,
                                   monthly_climate_table=MONTHLY_TABLE if monthly else None)


def room_of(model):
    return model["material"]["hourly_load_model"]["rooms"][0]


class SunPositionTests(unittest.TestCase):
    def test_noon_sun_matches_the_hand_calculation(self):
        # Solar noon altitude = 90° − latitude ± declination (23.44° at the solstices): 79.57° and 32.69° at 33.87° S.
        days = sun.design_days(-33.87, 151.21, "Australia/Sydney")
        self.assertAlmostEqual(max(row["altitude_deg"] for row in days[12]), 79.57, delta=0.6)
        self.assertAlmostEqual(max(row["altitude_deg"] for row in days[6]), 32.69, delta=0.6)
        self.assertEqual([row["hour"] for row in days[1]], list(range(24)))
        self.assertTrue(all(row["dni_w_m2"] == 0 for row in days[1] if row["altitude_deg"] <= 0))

    def test_north_glass_gets_more_sun_in_winter_and_the_roof_less(self):
        site = {"latitude": -33.87, "longitude": 151.21, "timezone": "Australia/Sydney"}
        self.assertGreater(max(sun.surface_totals(site, 6, 90, 0)), 1.5 * max(sun.surface_totals(site, 1, 90, 0)))
        self.assertGreater(max(sun.surface_totals(site, 1, 0, 0)), 1.5 * max(sun.surface_totals(site, 6, 0, 0)))
        west = sun.surface_totals(site, 1, 90, 270)
        self.assertGreater(west.index(max(west)), 14)            # the west wall peaks in the late afternoon

    def test_overhang_shadow_follows_the_profile_angle(self):
        # Sun straight in front at 60°: a 1 m deep awning casts tan(60°) = 1.732 m of shadow down the face.
        self.assertAlmostEqual(sun.overhang_sunlit_fraction(60, 0, 0, 2.0, 1.0, 0.0), 1 - tan(radians(60)) / 2, places=5)
        self.assertAlmostEqual(sun.overhang_sunlit_fraction(60, 0, 0, 2.0, 1.0, 0.5), 1 - (tan(radians(60)) - 0.5) / 2, places=5)
        # 45° off the wall's bearing the profile angle steepens (tan = tan(alt) / cos(45°)).
        self.assertAlmostEqual(sun.overhang_sunlit_fraction(30, 45, 0, 2.0, 1.0), 1 - tan(radians(30)) / 0.7071068 / 2, places=4)
        self.assertEqual(sun.overhang_sunlit_fraction(60, 180, 0, 2.0, 1.0), 1.0)   # sun behind the wall
        self.assertEqual(sun.overhang_sunlit_fraction(80, 0, 0, 2.0, 3.0), 0.0)     # fully shaded

    def test_time_zones(self):
        self.assertEqual(sun.timezone_for("NSW"), "Australia/Sydney")
        self.assertEqual(sun.timezone_for("NSW", "Broken Hill"), "Australia/Broken_Hill")
        self.assertEqual(sun.timezone_for("QLD"), "Australia/Brisbane")
        self.assertEqual(sun.timezone_for(""), "")


class MonthlyWeatherTests(unittest.TestCase):
    def test_months_are_the_summer_day_lowered_from_the_hottest_month(self):
        base = [{"db": 20 + hour * 0.3, "wb": 17.0} for hour in range(24)]
        days = design_weather.monthly_days(DESIGN_TABLE["locations"]["Sydney"], base, MONTHLY_TABLE["locations"]["Sydney"])
        self.assertEqual(len(days), 12)
        self.assertEqual([day["month"] for day in days if day["hottest"]], [1])
        self.assertEqual(days[0]["design_point"], {"db": 32.0, "wb": 23.0})
        self.assertEqual(days[6]["offset_c"], 9.6)                                  # July: 26.0 − 16.4
        self.assertEqual(days[6]["design_point"], {"db": 22.4, "wb": 13.4})
        self.assertEqual(days[6]["hours"][design_weather.DESIGN_HOUR]["db"], 22.4)

    def test_a_hottest_month_other_than_january_is_the_reference(self):
        row = {"mean_max": [32.0, 32.1, 32.5, 32.9, 31.7, 30.6, 30.4, 31.4, 32.9, 33.6, 34.3, 33.4]}
        base = [{"db": 30.0, "wb": 24.0} for _hour in range(24)]
        days = design_weather.monthly_days({"db": 35.0, "cwb": 26.0}, base, row)
        self.assertEqual([day["month_name"] for day in days if day["hottest"]], ["November"])
        self.assertTrue(all(day["design_point"]["db"] <= 35.0 for day in days))

    def test_monthly_table_loading_drops_bad_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path, missing = Path(folder) / "monthly.json", Path(folder) / "missing.json"
            path.write_text(json.dumps({"locations": {**MONTHLY_TABLE["locations"], "Perth": {"mean_max": [30] * 11},
                                                      "Atlantis": {"mean_max": [20] * 12}}}), encoding="utf-8")
            table = design_weather.load_monthly_table(path, nasa_path=missing)
            self.assertEqual(sorted(table["locations"]), ["Sydney"])
            self.assertEqual(table["locations"]["Sydney"]["kind"], "bom_station")
            self.assertIsNone(design_weather.load_monthly_table(missing, nasa_path=missing))

    def test_nasa_power_covers_every_listed_location_and_bom_replaces_it(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.json"
            nasa = design_weather.load_monthly_table(missing)
            self.assertEqual(sorted(nasa["locations"]), sorted(design_weather.LOCATIONS))
            self.assertTrue(all(row["kind"] == "nasa_power" and len(row["mean_max"]) == 12 for row in nasa["locations"].values()))
            self.assertIn("NASA POWER", nasa["locations"]["Sydney"]["source"])
            bom = Path(folder) / "bom.json"
            bom.write_text(json.dumps(MONTHLY_TABLE), encoding="utf-8")
            merged = design_weather.load_monthly_table(bom)
            self.assertEqual(merged["locations"]["Sydney"]["kind"], "bom_station")
            self.assertEqual(merged["locations"]["Melbourne"]["kind"], "nasa_power")

    def test_nasa_power_sydney_offsets_agree_with_the_bom_station(self):
        # Independent check (2026-10-25): BoM Sydney (Observatory Hill, 066062) monthly mean maximums, 1859–2020.
        bom = [26.0, 25.8, 24.8, 22.5, 19.5, 17.0, 16.4, 17.9, 20.1, 22.2, 23.7, 25.3]
        nasa = design_weather.load_monthly_table(Path(tempfile.gettempdir()) / "no-bom-file.json")["locations"]["Sydney"]["mean_max"]
        for month in range(12):
            self.assertAlmostEqual(max(nasa) - nasa[month], max(bom) - bom[month], delta=0.6)

    def test_a_nasa_grid_square_hotter_than_the_design_day_is_flagged(self):
        base = [{"db": 30.0, "wb": 24.0} for _hour in range(24)]
        table = {"locations": {"Broome": {"kind": "nasa_power", "source": "NASA POWER test", "station": "", "station_name": "grid",
                                          "mean_max": [34.0, 33.4, 33.7, 34.6, 32.7, 30.3, 30.4, 32.5, 36.0, 38.6, 39.1, 37.3]}}}
        result = design_weather._with_months({"location": "Broome", "values": {"db": 37.0, "cwb": 26.0}}, table, base)
        self.assertIn("grid square can run hotter", result["monthly"]["warning"])
        table["locations"]["Broome"]["kind"] = "bom_station"
        self.assertEqual(design_weather._with_months({"location": "Broome", "values": {"db": 37.0, "cwb": 26.0}}, table, base)["monthly"]["warning"], "")

    def test_importer_reads_a_saved_climate_statistics_page(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("bom_import", Path(__file__).resolve().parents[1] / "tools" / "import_bom_monthly_stats.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        page = ("<html><body><p>Site name: <b>TEST CITY AIRPORT</b> Site number: <b>099999</b> Latitude: 33.90 &deg;S "
                "Longitude: 151.10 &deg;E</p><table><tr><th>Mean maximum temperature (&deg;C)</th>"
                + "".join(f"<td>{value}</td>" for value in [27, 26.5, 25, 23, 20, 17.5, 17, 18.5, 21, 23, 24.5, 26]) +
                "<td>22.4</td></tr></table></body></html>")
        station = module.parse(page)
        self.assertEqual((station["station"], station["latitude"]), ("099999", -33.9))
        self.assertEqual(station["mean_max"][6], 17.0)
        self.assertEqual(module.match(station)[0], "Sydney")
        with self.assertRaises(ValueError):
            module.parse("<html>not a climate page</html>")


class DraftSunAndSeasonsTests(unittest.TestCase):
    def test_every_month_is_calculated_with_its_own_sun_and_the_largest_sizes_the_system(self):
        model = assemble(proposal())
        scenarios = model["material"]["design_day_scenarios"]["scenarios"]
        self.assertEqual(len(scenarios), 13)                                       # 12 months + the humid check
        self.assertEqual(sorted(row["sun_month"] for row in scenarios if row["scenario_id"] != "ai_preliminary_humid_day"),
                         list(range(1, 13)))
        room = room_of(model)
        glass = room["cooling_load"]["glazing_surfaces"][0]
        roof = next(item for item in room["cooling_load"]["envelope_surfaces"] if item["kind"] == "roof")
        self.assertEqual(glass["solar_basis"], "site_sun_hourly")
        self.assertEqual(len(glass["preliminary_hourly_solar_w_m2"]), 13)
        self.assertGreater(max(glass["preliminary_hourly_solar_w_m2"]["ai_preliminary_month_06"]),
                           max(glass["preliminary_hourly_solar_w_m2"]["ai_preliminary_cooling_day"]))
        self.assertGreater(max(roof["preliminary_hourly_solar_w_m2"]["ai_preliminary_cooling_day"]), 900)
        self.assertTrue(model["design_conditions_basis"]["sun"]["site_specific"])
        self.assertTrue(model["design_conditions_basis"]["design_day"]["months"]["calculated"])

        report = ai_preliminary.calculate(model)
        seasonal = report["seasonal"]
        self.assertEqual([row["month"] for row in seasonal["months"]], list(design_weather.MONTH_NAMES))
        self.assertTrue(all(len(row["hourly_kw"]) == 24 for row in seasonal["months"]))
        governing = report["included_scope_peak"]
        self.assertEqual(seasonal["design_month"], governing["month"])
        self.assertAlmostEqual(seasonal["design_kw"], governing["raw_coincident_total_kw"], delta=0.05)
        self.assertEqual(seasonal["design_kw"], max(row["peak_kw"] for row in seasonal["months"]))
        june = next(row for row in seasonal["months"] if row["month"] == "June")
        january = next(row for row in seasonal["months"] if row["month"] == "January")
        self.assertNotEqual(june["hourly_sun_kw"], january["hourly_sun_kw"])
        # The walls' and roof's own sun (not only the glass) changes month to month: the hourly lists survive the
        # model validation (they were once dropped there, leaving every month on the summer peak value all day).
        def opaque_sun(scenario_id, hour):
            scenario = next(row for row in report["scenario_results"] if row["scenario_id"] == scenario_id)
            return scenario["included_scope_hours"][hour]["components"]["solar"]["total_kw"]
        self.assertGreater(opaque_sun("ai_preliminary_cooling_day", 13), 1.5 * opaque_sun("ai_preliminary_month_06", 13))
        self.assertNotEqual(opaque_sun("ai_preliminary_cooling_day", 9), opaque_sun("ai_preliminary_cooling_day", 13))

    def test_an_awning_shades_the_direct_sun_on_the_glass(self):
        bare = room_of(assemble(proposal()))["cooling_load"]["glazing_surfaces"][0]["preliminary_hourly_solar_w_m2"]
        shaded_model = assemble(proposal(shade={"kind": "awning", "depth_m": 1.5, "gap_m": 0.3, "full_width": True}))
        shaded = room_of(shaded_model)["cooling_load"]["glazing_surfaces"][0]
        january = "ai_preliminary_cooling_day"
        self.assertLess(max(shaded["preliminary_hourly_solar_w_m2"][january]), 0.75 * max(bare[january]))
        self.assertEqual(shaded["manual_solar"]["external_shading_factor"], 1.0)     # geometry replaces the category
        self.assertTrue(any(row["component"] == "glazing shade" and "1.5 m deep" in row["reason"]
                            for row in shaded_model["phase1_assumptions"]))
        unmeasured = assemble(proposal(shade={"kind": "verandah", "depth_m": None, "full_width": True}))
        glass = room_of(unmeasured)["cooling_load"]["glazing_surfaces"][0]["preliminary_hourly_solar_w_m2"]
        self.assertEqual(glass[january], bare[january])
        self.assertTrue(any("depth isn't printed" in row["reason"] for row in unmeasured["phase1_assumptions"]))

    def test_without_monthly_statistics_only_the_summer_day_runs_with_the_site_sun(self):
        model = assemble(proposal(), monthly=False)
        self.assertEqual([row["scenario_id"] for row in model["material"]["design_day_scenarios"]["scenarios"]],
                         ["ai_preliminary_cooling_day", "ai_preliminary_humid_day"])
        self.assertFalse(model["design_conditions_basis"]["design_day"]["months"]["calculated"])
        self.assertIn("preliminary_hourly_solar_w_m2", room_of(model)["cooling_load"]["glazing_surfaces"][0])
        self.assertEqual(len(ai_preliminary.calculate(model)["seasonal"]["months"]), 1)

    def test_without_a_site_the_generic_direction_profiles_stay(self):
        model = assemble(proposal(), site=False)
        glass = room_of(model)["cooling_load"]["glazing_surfaces"][0]
        self.assertNotIn("preliminary_hourly_solar_w_m2", glass)
        self.assertEqual(glass["solar_basis"], "ai_preliminary_cardinal_profile")
        self.assertFalse(model["design_conditions_basis"]["sun"]["site_specific"])

    def test_phase1_also_covers_every_month(self):
        report = ai_preliminary.calculate(assemble(proposal()), scope="phase1_base_room")
        self.assertEqual(len(report["seasonal"]["months"]), 12)
        self.assertEqual(report["included_scope_peak"]["components"]["people"]["total_kw"], 0)


class OpeningShadeReplyTests(unittest.TestCase):
    def test_reply_shade_is_validated_and_optional(self):
        from ai import autonomous_tasks
        self.assertIsNone(autonomous_tasks._opening_shade_reply(None))
        self.assertIsNone(autonomous_tasks._opening_shade_reply({"kind": "none"}))
        shade = autonomous_tasks._opening_shade_reply({"kind": "Verandah", "depth_mm": 2400, "underside_mm": 2900,
                                                       "full_width": True, "printed_text": ["VERANDAH OVER"]})
        self.assertEqual((shade["kind"], shade["depth_mm"], shade["underside_mm"]), ("verandah", 2400.0, 2900.0))
        with self.assertRaises(ValueError):
            autonomous_tasks._opening_shade_reply({"kind": "umbrella"})
        with self.assertRaises(ValueError):
            autonomous_tasks._opening_shade_reply({"kind": "awning", "depth_mm": -5})

    def test_panel_shade_uses_the_read_head_height(self):
        from backend import autonomous_tasks_service as service
        validated = {"shade": {"kind": "awning", "depth_mm": 1800.0, "underside_mm": 3000.0, "full_width": False, "printed_text": []}}
        result = service._p4_panel_shade(validated, {"head_mm": 2700.0, "head_read_mm": 2700.0})
        self.assertEqual(result["shade"]["depth_m"], 1.8)
        self.assertAlmostEqual(result["shade"]["gap_m"], 0.3)
        self.assertEqual(service._p4_panel_shade({"shade": None}, {"head_mm": 2700.0}), {})


if __name__ == "__main__":
    unittest.main()


class SurroundingsTests(unittest.TestCase):
    """Buildings in the way (user decisions 2026-10-26): what stands opposite each outside wall shades it."""

    def test_shadow_of_a_building_opposite_matches_the_hand_calculation(self):
        # A 10 m building 12 m away, sun straight in front: the shadow's top edge is at 10 − 12 × tan(altitude).
        self.assertAlmostEqual(sun.obstruction_shaded_fraction(35, 270, 270, 0, 3, 12, 10), (10 - 12 * tan(radians(35))) / 3, places=5)
        self.assertEqual(sun.obstruction_shaded_fraction(20, 270, 270, 0, 3, 12, 10), 1.0)
        self.assertEqual(sun.obstruction_shaded_fraction(45, 270, 270, 0, 3, 12, 10), 0.0)   # above atan(10/12) = 39.8°
        # 60° off the wall's bearing the ray travels twice as far to reach the building.
        self.assertAlmostEqual(sun.obstruction_shaded_fraction(20, 330, 270, 0, 3, 12, 10), (10 - 24 * tan(radians(20))) / 3, places=5)
        self.assertEqual(sun.obstruction_shaded_fraction(30, 90, 270, 0, 3, 12, 10), 0.0)    # sun behind the wall
        # Glass on an upper floor 7 m up sees over the same building sooner.
        self.assertLess(sun.obstruction_shaded_fraction(30, 270, 270, 7, 10, 12, 10), sun.obstruction_shaded_fraction(30, 270, 270, 0, 3, 12, 10))

    def test_building_and_awning_together_leave_the_strip_between_their_shadows(self):
        day = [{"hour": 0, "altitude_deg": 30.0, "azimuth_deg": 270.0, "zenith_deg": 60.0,
                "dni_w_m2": 800.0, "dhi_w_m2": 100.0, "ghi_w_m2": 500.0}]
        bare = sun.surface_hours(day, 90, 270, height_m=3.0)[0]
        awning = {"depth_m": 1.0, "gap_m": 0.0}
        building = {"distance_m": 12.0, "height_m": 7.0, "base_m": 0.0}
        both = sun.surface_hours(day, 90, 270, shade=awning, height_m=3.0, obstruction=building)[0]
        top = 1 - sun.overhang_sunlit_fraction(30, 270, 270, 3.0, 1.0)
        bottom = sun.obstruction_shaded_fraction(30, 270, 270, 0, 3, 12, 7)
        self.assertAlmostEqual(both["sunlit_fraction"], max(0.0, 1 - top - bottom), places=5)
        self.assertAlmostEqual(both["total_w_m2"], round(bare["direct_w_m2"] * both["sunlit_fraction"] + bare["diffuse_w_m2"], 2), delta=0.02)
        half = sun.surface_hours(day, 90, 270, height_m=3.0, obstruction={**building, "coverage": 0.5})[0]
        self.assertAlmostEqual(half["sunlit_fraction"], 1 - 0.5 * bottom, places=5)

    def test_reply_heights_from_print_storeys_or_one_storey(self):
        from ai import autonomous_tasks
        packet = {"walls": {"1": {}, "2": {}, "3": {}, "4": {}}}
        reply = {"walls": [
            {"wall_number": 1, "opposite": "building", "distance_m": 12, "height_m": 9.5, "full_length": True, "evidence": "RL 9.5 on the streetscape"},
            {"wall_number": 2, "opposite": "building", "distance_m": 8, "storeys": 3, "full_length": False, "evidence": "EXISTING 3 STOREY"},
            {"wall_number": 3, "opposite": "building", "distance_m": 15, "full_length": True, "evidence": "existing building outline"},
            {"wall_number": 4, "opposite": "open", "distance_m": 30, "evidence": "street and car park opposite"}]}
        walls = autonomous_tasks.validate_surroundings_reply(packet, json.dumps(reply))["walls"]
        self.assertEqual((walls[1]["height_m"], walls[2]["height_m"], walls[3]["height_m"]), (9.5, 9.9, 3.3))
        self.assertIn("one storey assumed", walls[3]["height_basis"])
        self.assertIsNone(walls[4]["distance_m"])                      # open ground shades nothing
        with self.assertRaises(ValueError):                             # every wall must be answered
            autonomous_tasks.validate_surroundings_reply(packet, json.dumps({"walls": reply["walls"][:3]}))
        with self.assertRaises(ValueError):
            autonomous_tasks.validate_surroundings_reply({"walls": {"1": {}}}, json.dumps({"walls": [{**reply["walls"][0], "distance_m": -2}]}))
        prompt = autonomous_tasks.surroundings_prompt({str(n): {"faces": "NW (315°)", "length_m": 12.34} for n in range(1, 9)},
                                                      ["page 2: SITE PLAN", "page 9: STREETSCAPE ELEVATION"])
        self.assertLessEqual(len(prompt), 2000)

    def test_floor_level_from_the_level_name(self):
        from backend import ai_preliminary_service as service
        self.assertEqual(service._floor_level_m("Ground floor")[0], 0.0)
        self.assertEqual(service._floor_level_m("Unassigned level")[0], 0.0)
        self.assertEqual(service._floor_level_m("Level 2")[0], 7.0)
        self.assertEqual(service._floor_level_m("First floor")[0], 3.5)
        self.assertIn("3.5 m per level", service._floor_level_m("L1")[1])

    def test_a_building_opposite_reaches_the_wall_and_cuts_its_sun(self):
        from backend import autonomous_tasks_service
        from tests import test_reviewer_traced_preliminary as traced

        def build(with_building, answer=None):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                room_use, prop, _room_id, trace = traced.write_fixture(root)
                traced.set_envelope_trace(root, trace, [{"index": 0, "boundary": "external"}], "not_exposed")
                registry = json.loads((root / "reviewer_room_geometry.json").read_text())
                registry["page_north"] = {"1": {"page": 1, "plan_up_azimuth_deg": 0, "source": "reviewer_typed_page_up_bearing",
                                                "reviewer": "QA", "declared_at": "now",
                                                "north_basis": {"label": "unlabelled", "reason": "North arrow with no label; taken as true north."}}}
                (root / "reviewer_room_geometry.json").write_text(json.dumps(registry))
                if with_building:
                    packet = {"task": "P3_surroundings", "page": 1, "walls": {"1": {"faces": "N (0°)", "length_m": 0.1}},
                              "wall_edges": {"1": {"run_index": 0, "azimuth_deg": 0.0, "edges": [[trace["trace_id"], 0]]}}}
                    record = autonomous_tasks_service._create_run(root, "P3_surroundings", "page-1", packet, "surroundings")
                    validated = {"walls": {1: answer or {"opposite": "building", "distance_m": 6.0, "height_m": 9.9,
                                               "height_basis": "3 storeys × 3.3 m", "full_length": True, "evidence": "EXISTING 3 STOREY"}}}
                    autonomous_tasks_service._update_record(root, autonomous_tasks_service._apply_p3_surroundings(record, validated))
                prepared = traced.prepare(root, room_use, prop)
                model = ai_preliminary.assemble({"spaces": []}, preliminary_proposal=prepared, allow_area_fallbacks=False,
                                                site_location=SYDNEY, design_weather_table=DESIGN_TABLE,
                                                monthly_climate_table=MONTHLY_TABLE)
                walls = room_of(model)["cooling_load"]["envelope_surfaces"]
                wall = (next((item for item in walls if item["kind"] == "opaque_wall" and item.get("boundary_method") == "external"), None)
                        or next(item for item in walls if item["kind"] == "opaque_wall" and item["label"].startswith("Shop wall 1 ")))
                return wall, model["phase1_assumptions"]

        open_wall, open_notes = build(False)
        shaded_wall, shaded_notes = build(True)
        self.assertEqual(open_wall["orientation"], "N")
        self.assertNotIn("obstruction", open_wall)
        self.assertTrue(any("wasn't read from the drawings" in row["reason"] for row in open_notes))
        self.assertTrue(any("taken as true north" in row["reason"] for row in open_notes))
        self.assertEqual(shaded_wall["obstruction"]["height_m"], 9.9)
        june = "ai_preliminary_month_06"           # low winter sun from the north: well inside the building's shadow
        self.assertLess(sum(shaded_wall["preliminary_hourly_solar_w_m2"][june]), 0.6 * sum(open_wall["preliminary_hourly_solar_w_m2"][june]))
        self.assertTrue(any("9.9 m tall (3 storeys × 3.3 m)" in row["reason"] for row in shaded_notes))
        # The site plan shows more of the same building outside the wall (e.g. a shopping centre): no sun on it.
        inside_wall, inside_notes = build(True, {"opposite": "inside_building", "distance_m": None, "height_m": None,
                                                 "height_basis": "", "full_length": True, "evidence": "retail tenancies west of the shop"})
        # It becomes a wall to the mall at the room's temperature: no sun and no heat through it.
        self.assertEqual(inside_wall["label"], "Shop wall 1 to the mall")
        self.assertEqual(inside_wall["boundary_method"], "fixed_adjacent_temperature")
        self.assertEqual(inside_wall["solar_gain_factor"], 0)
        self.assertTrue(any("counted as a wall to the mall" in row["reason"] for row in inside_notes))


class TenancyCloseupTests(unittest.TestCase):
    def test_closeup_finds_the_red_tenancy_marking_and_enlarges_it(self):
        from PIL import Image, ImageDraw
        from backend import autonomous_tasks_service
        sheet = Image.new("RGB", (2000, 1400), "white")
        draw = ImageDraw.Draw(sheet)
        draw.rectangle((300, 200, 1700, 1200), outline="black", width=3)        # the centre's floor plate
        draw.rectangle((620, 900, 800, 1090), outline=(230, 20, 20), width=6)   # the tenancy, outlined in red
        closeup = autonomous_tasks_service._tenancy_closeup(sheet)
        self.assertEqual(max(closeup.size), 1400)                               # enlarged from a small crop
        self.assertLess(closeup.width / closeup.height, 2)
        self.assertIsNone(autonomous_tasks_service._tenancy_closeup(Image.new("RGB", (800, 600), "white")))
