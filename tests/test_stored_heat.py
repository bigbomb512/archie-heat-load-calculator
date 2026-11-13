"""Stored heat (ai/stored_heat.py, ai/stored_heat_rooms.py): our own heat balance against published results, and the
hourly step that turns heat gains into cooling load.

References (independent of Archie):
* Journal of Engineering (University of Baghdad) vol 21 no 5, May 2015, "Cooling load calculations for typical Iraqi
  roof and wall constructions using ASHRAE's RTS method": Table 3 layer properties, Table 4 periodic response factors
  from the ASHRAE PRF/RTF generator (Iu & Fisher 2001) for a 30 cm thermo-stone wall and a concrete roof.
* ASHRAE Handbook—Fundamentals 2009 Ch 18 Table 19, nonsolar RTS for heavyweight construction, no carpet, 10% glass
  (reproduced in the same paper, Table 7): 22, 10, 6, 5, 5, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2 %.
  The zone is ASHRAE's representative zone as we read Table 21 (15 x 15 x 2.7 m, one outside wall; 100 mm brick,
  200 mm concrete, cavity, 50 mm insulation, 20 mm gypsum; 200 mm concrete floor/ceiling with a ceiling cavity and
  acoustic tile; 20 mm gypsum / 200 mm concrete block partitions; 25 mm wood over half the floor area), with AIRAH
  2007 material properties. Table 21 itself wasn't available to check this reading against.
"""
from copy import deepcopy
import unittest

from ai import stored_heat as sh, stored_heat_rooms as rooms

WALL_PRF = [0.0136, 0.0124, 0.0119, 0.0132, 0.0169, 0.0221, 0.0275, 0.0321, 0.0353, 0.0371, 0.0376, 0.0371,
            0.0359, 0.0342, 0.0322, 0.0300, 0.0278, 0.0257, 0.0236, 0.0216, 0.0197, 0.0180, 0.0164, 0.0149]
ROOF_PRF = [0.0138, 0.0134, 0.0132, 0.0134, 0.0140, 0.0148, 0.0156, 0.0162, 0.0166, 0.0169, 0.0171, 0.0171,
            0.0171, 0.0170, 0.0168, 0.0166, 0.0163, 0.0160, 0.0157, 0.0154, 0.0151, 0.0148, 0.0144, 0.0141]
HEAVY_RTS = [22, 10, 6, 5, 5, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2]


def m(thickness, k, rho, c_kj):
    return {"thickness_m": thickness, "k_w_mk": k, "density_kg_m3": rho, "specific_heat_j_kgk": c_kj * 1000}


def airah(name, thickness):
    row = sh.load()["materials"][name]
    return {"thickness_m": thickness, **{key: row[key] for key in ("k_w_mk", "density_kg_m3", "specific_heat_j_kgk")}}


def ashrae_heavy_zone():
    length, height = 15.0, 2.7
    outside = length * height
    glass = 0.10 * outside
    floor = [airah("dense_concrete", 0.2), {"r_m2k_w": 0.17}, airah("acoustic_tile", 0.013)]
    return [
        {"area_m2": outside - glass, "film_r": 0.12, "side": "outdoor",
         "layers": [airah("plasterboard", 0.02), airah("glasswool", 0.05), {"r_m2k_w": 0.16}, airah("dense_concrete", 0.2),
                    airah("clay_brick", 0.1), {"r_m2k_w": 0.04}]},
        {"area_m2": glass, "film_r": 0.12, "side": "window", "layers": [], "r_to_outside": 1 / 2.8 - 0.12},
        {"area_m2": 3 * length * height, "film_r": 0.12, "side": "mirror",
         "layers": [airah("plasterboard", 0.02), airah("dense_hollow_block", 0.2), airah("plasterboard", 0.02)]},
        {"area_m2": length * length, "film_r": 0.11, "side": "pair_top", "layers": floor, "pair_index": 4, "floor": True},
        {"area_m2": length * length, "film_r": 0.16, "side": "pair_bottom", "layers": floor, "pair_index": 3},
        {"area_m2": 0.5 * length * length, "film_r": 0.12, "side": "adiabatic", "layers": [airah("timber", 0.025)]},
    ]


class ConductionTimeSeriesTests(unittest.TestCase):
    def test_a_massless_construction_passes_heat_in_the_same_hour(self):
        prf = sh.periodic_response_factors([{"r_m2k_w": 0.04}, {"r_m2k_w": 2.0}, {"r_m2k_w": 0.12}])
        self.assertAlmostEqual(sum(prf), 1 / 2.16, places=6)
        self.assertAlmostEqual(prf[0], 1 / 2.16, delta=0.002)
        self.assertTrue(all(abs(v) < 0.002 for v in prf[1:]))

    def test_the_factors_sum_to_the_u_value_for_any_construction(self):
        layers = [{"r_m2k_w": 0.04}, airah("clay_brick", 0.11), {"r_m2k_w": 0.16}, airah("glasswool", 0.09),
                  airah("plasterboard", 0.01), {"r_m2k_w": 0.12}]
        self.assertAlmostEqual(sum(sh.periodic_response_factors(layers)), 1 / sh.resistance(layers), places=6)
        self.assertAlmostEqual(sum(sh.conduction_time_series(layers)), 1.0, places=9)

    def test_the_thermo_stone_wall_matches_the_ashrae_generator(self):
        # Paper Table 2/3: cement plaster 3 cm, thermo-stone 30 cm, juss 1.5 cm, gypsum 1 cm; films 0.044 / 0.12.
        wall = [{"r_m2k_w": 0.044}, m(0.03, 1.08, 2050, 0.84), m(0.30, 0.21, 760, 0.8), m(0.015, 0.72, 1858, 0.84),
                m(0.01, 0.57, 1200, 0.84), {"r_m2k_w": 0.12}]
        prf = sh.periodic_response_factors(wall)
        self.assertAlmostEqual(sum(prf), sum(WALL_PRF), delta=0.02 * sum(WALL_PRF))
        ours, theirs = sh.conduction_time_series(wall), [v / sum(WALL_PRF) for v in WALL_PRF]
        self.assertLess(max(abs(a - b) for a, b in zip(ours, theirs)), 0.006)
        self.assertEqual(ours.index(max(ours)), theirs.index(max(theirs)))

    def test_the_concrete_roof_matches_the_ashrae_generator(self):
        # Paper Table 2/3; the membrane and acoustic-tile thicknesses aren't given: no membrane, a 13 mm tile (AIRAH).
        roof = [{"r_m2k_w": 0.044}, m(0.04, 0.85, 2220, 0.837), m(0.05, 0.25, 1450, 0.84), m(0.01, 0.35, 1400, 1.67),
                m(0.05, 0.03, 30, 2.03), m(0.15, 1.49, 2300, 0.84), {"r_m2k_w": 0.176}, m(0.013, 0.061, 481, 0.84),
                {"r_m2k_w": 0.163}]
        ours, theirs = sh.conduction_time_series(roof), [v / sum(ROOF_PRF) for v in ROOF_PRF]
        self.assertLess(max(abs(a - b) for a, b in zip(ours, theirs)), 0.002)
        self.assertAlmostEqual(sum(sh.periodic_response_factors(roof)), sum(ROOF_PRF), delta=0.03 * sum(ROOF_PRF))


class RadiantTimeSeriesTests(unittest.TestCase):
    def test_the_heavyweight_zone_matches_ashrae_table_19_within_two_points(self):
        ours = [100 * v for v in sh.radiant_time_series(ashrae_heavy_zone(), "nonsolar")]
        for hour, (got, published) in enumerate(zip(ours, HEAVY_RTS)):
            with self.subTest(hour=hour):
                self.assertLessEqual(abs(got - published), 2.0)

    def test_the_series_are_fractions_of_one_that_never_go_negative(self):
        for kind in ("nonsolar", "solar"):
            series = sh.radiant_time_series(ashrae_heavy_zone(), kind)
            self.assertAlmostEqual(sum(series), 1.0, places=9)
            self.assertGreaterEqual(min(series), -1e-9)

    def test_a_lighter_room_gives_its_heat_back_sooner(self):
        heavy = sh.radiant_time_series(ashrae_heavy_zone())
        light = ashrae_heavy_zone()
        light[2]["layers"] = [airah("plasterboard", 0.01), {"r_m2k_w": 0.16}, airah("plasterboard", 0.01)]
        for face in (3, 4):
            light[face]["layers"] = [airah("timber", 0.019), {"r_m2k_w": 0.17}, airah("plasterboard", 0.01)]
        light_series = sh.radiant_time_series(light)
        self.assertGreater(light_series[0], heavy[0])
        self.assertGreater(sum(light_series[:3]), sum(heavy[:3]) + 0.1)

    def test_a_film_whose_conductance_is_below_its_radiative_part_is_refused(self):
        zone = ashrae_heavy_zone()
        zone[0]["film_r"] = 0.5          # 2 W/m²K in all, less than the ~5.4 W/m²K of long-wave radiation alone
        with self.assertRaises(ValueError):
            sh.radiant_time_series(zone)


def draft_room(**changes):
    room = {"room_id": "room-a", "name": "Shop", "area_m2": 60.0, "ceiling_height_mm": 3000, "cooling_load": {
        "envelope_surfaces": [
            {"surface_id": "w", "kind": "opaque_wall", "label": "Shop West wall", "boundary_condition": "",
             "boundary_method": "external", "area_m2": 18.0, "u_value_w_m2k": 0.6, "construction_id": "au-preliminary-v3-wall-standard"},
            {"surface_id": "p", "kind": "opaque_wall", "label": "Shop wall to another room", "boundary_condition": "internal",
             "boundary_method": "fixed_adjacent_temperature", "area_m2": 30.0, "u_value_w_m2k": 1.2},
            {"surface_id": "f", "kind": "floor", "label": "Shop floor (slab)", "boundary_condition": "ground",
             "boundary_method": "fixed_adjacent_temperature", "area_m2": 60.0, "u_value_w_m2k": 0.8},
            {"surface_id": "r", "kind": "roof", "label": "Shop roof", "boundary_condition": "", "boundary_method": "external",
             "area_m2": 60.0, "u_value_w_m2k": 0.5}],
        "glazing_surfaces": [{"surface_id": "g", "explicit_opening_area_m2": 12.0, "window": {"u_value_w_m2k": 5.8, "shgc": 0.45}}]}}
    room.update(changes)
    return room


class RoomRecordTests(unittest.TestCase):
    def test_a_traced_room_gets_its_series_and_outside_surfaces_their_conduction_series(self):
        record = rooms.room_record(draft_room(), dwelling=False, profile_id="retail")
        self.assertEqual(record["status"], "calculated")
        self.assertEqual(sorted(record["surface_cts"]), ["r", "w"])
        self.assertEqual(rooms.validate(record)["nonsolar_rts"], record["nonsolar_rts"])
        chosen = {row["surface_ids"][0]: row for row in record["constructions"] if row["surface_ids"]}
        self.assertEqual(chosen["w"]["construction"], "wall_lightweight")
        self.assertEqual(chosen["f"]["construction"], "floor_slab_on_ground")
        self.assertEqual(chosen["f"]["floor_covering"], "hard")
        self.assertEqual(chosen["r"]["construction"], "roof_metal")
        self.assertEqual(record["floor_covering"], "hard")

    def test_the_insulation_is_sized_to_the_surfaces_u_value(self):
        cfg = sh.load()
        layers, fit = rooms._fitted(cfg, cfg["constructions"]["wall_brick_veneer"]["layers"], 0.6, 0.12)
        self.assertAlmostEqual(sh.resistance(layers) + 0.12, 1 / 0.6, places=3)
        self.assertGreater(fit, 0)
        # A U-value the bare construction can't reach keeps the construction without insulation.
        layers, fit = rooms._fitted(cfg, cfg["constructions"]["wall_brick_veneer"]["layers"], 5.0, 0.12)
        self.assertIsNone(fit)

    def test_construction_words_on_the_drawings_choose_the_construction(self):
        room = draft_room()
        room["cooling_load"]["envelope_surfaces"][0]["label"] = "Shop west wall (double brick)"
        room["cooling_load"]["envelope_surfaces"][3]["construction_source"] = "Answered by the operators: concrete roof slab"
        record = rooms.room_record(room, profile_id="retail")
        chosen = {row["surface_ids"][0]: row for row in record["constructions"] if row["surface_ids"]}
        self.assertEqual((chosen["w"]["construction"], chosen["w"]["chosen"]), ("wall_cavity_brick", "named on the drawings"))
        self.assertEqual(chosen["r"]["construction"], "roof_concrete")
        # Heavier constructions hold the heat longer.
        plain = rooms.room_record(draft_room(), profile_id="retail")
        self.assertLess(record["surface_cts"]["w"][0], plain["surface_cts"]["w"][0])

    def test_a_bedroom_gets_carpet_and_a_room_without_surfaces_gets_typical_ones(self):
        record = rooms.room_record({"room_id": "b", "name": "BED", "area_m2": 12.0, "cooling_load": {}},
                                   dwelling=True, profile_id="house_bedroom")
        self.assertEqual(record["status"], "calculated")
        self.assertEqual(record["floor_covering"], "carpet")
        self.assertEqual(len(record["notes"]), 3)
        self.assertEqual(rooms.room_record({"room_id": "x", "name": "X", "area_m2": 0, "cooling_load": {}})["status"],
                         "not_calculated")

    def test_validation_refuses_a_series_that_does_not_add_up(self):
        record = rooms.room_record(draft_room(), profile_id="retail")
        broken = deepcopy(record)
        broken["nonsolar_rts"] = broken["nonsolar_rts"][:23]
        with self.assertRaises(ValueError):
            rooms.validate(broken)
        broken = deepcopy(record)
        broken["surface_cts"]["w"] = [0.5] * 24
        with self.assertRaises(ValueError):
            rooms.validate(broken)
        with self.assertRaises(ValueError):
            rooms.validate({"method_id": "something_else", "status": "calculated"})
        self.assertEqual(rooms.validate(None), {})


def contributions(hour):
    """One hour of the shapes the hourly calculation produces."""
    open_now = 1.0 if 9 <= hour < 18 else 0.0
    sun = max(0.0, 1.0 - abs(hour - 15) / 5)
    return [
        {"name": "people", "sensible_kw": 2.0 * open_now, "latent_kw": 1.5 * open_now, "total_kw": 3.5 * open_now,
         "schedule_factor": open_now, "inputs": {}},
        {"name": "lighting", "sensible_kw": 1.0 * open_now, "latent_kw": 0.0, "total_kw": open_now, "inputs": {}},
        {"name": "equipment_refrigeration", "sensible_kw": 0.5, "latent_kw": 0.0, "total_kw": 0.5, "inputs": {}},
        {"name": "envelope", "sensible_kw": 0.4 * sun, "latent_kw": 0.0, "total_kw": 0.4 * sun,
         "inputs": {"surfaces": [{"surface_id": "w", "gain_kw": 0.3 * sun}, {"surface_id": "p", "gain_kw": 0.0},
                                 {"surface_id": "r", "gain_kw": 0.1 * sun}]}},
        {"name": "solar", "sensible_kw": 0.2 * sun, "latent_kw": 0.0, "total_kw": 0.2 * sun,
         "inputs": {"surfaces": [{"surface_id": "w", "gain_kw": 0.2 * sun}]}},
        {"name": "glazing_conduction", "sensible_kw": 0.3 * sun, "latent_kw": 0.0, "total_kw": 0.3 * sun, "inputs": {"surface_id": "g"}},
        {"name": "glazing_solar", "sensible_kw": 3.0 * sun, "latent_kw": 0.0, "total_kw": 3.0 * sun, "inputs": {"surface_id": "g"}},
        {"name": "outside_air", "sensible_kw": 1.2 * open_now, "latent_kw": 0.8 * open_now, "total_kw": 2.0 * open_now, "inputs": {}},
    ]


class HourlyStepTests(unittest.TestCase):
    def setUp(self):
        self.room = draft_room()
        self.room["stored_heat"] = rooms.room_record(self.room, profile_id="retail")
        self.gains = [contributions(hour) for hour in range(24)]
        self.loads = rooms.apply(self.room, self.gains)

    def daily(self, hours, name, key="sensible_kw"):
        return sum(item[key] for items in hours for item in items if item["name"] == name)

    def test_a_days_cooling_load_adds_up_to_its_heat_gains(self):
        for name in ("people", "lighting", "envelope", "solar", "glazing_conduction", "glazing_solar"):
            with self.subTest(component=name):
                self.assertAlmostEqual(self.daily(self.loads, name), self.daily(self.gains, name), places=2)

    def test_latent_heat_outside_air_and_equipment_are_unchanged(self):
        for hour in range(24):
            for before, after in zip(self.gains[hour], self.loads[hour]):
                self.assertEqual(before["latent_kw"], after["latent_kw"])
                if before["name"] in {"outside_air", "equipment_refrigeration"}:
                    self.assertEqual(before["sensible_kw"], after["sensible_kw"])

    def test_stored_heat_lowers_the_afternoon_peak_and_carries_heat_into_the_evening(self):
        def total(hours, hour):
            return sum(item["sensible_kw"] for item in hours[hour])
        self.assertLess(max(total(self.loads, h) for h in range(24)), max(total(self.gains, h) for h in range(24)))
        self.assertGreater(total(self.loads, 19), total(self.gains, 19))
        glass = [next(i for i in self.loads[h] if i["name"] == "glazing_solar") for h in range(24)]
        self.assertEqual(glass[15]["inputs"]["stored_heat"]["time_series"], "solar")
        self.assertEqual(glass[15]["heat_gain_sensible_kw"], 3.0)
        self.assertLess(glass[15]["sensible_kw"], 3.0)

    def test_each_outside_surface_keeps_its_own_cooling_load(self):
        envelope = next(i for i in self.loads[16] if i["name"] == "envelope")
        rows = {row["surface_id"]: row for row in envelope["inputs"]["surfaces"]}
        self.assertTrue(rows["w"]["conduction_time_series"])
        self.assertFalse(rows["p"]["conduction_time_series"])
        self.assertAlmostEqual(sum(row["cooling_load_kw"] for row in rows.values()), envelope["sensible_kw"], places=3)

    def test_without_a_calculated_record_or_a_whole_day_nothing_changes(self):
        plain = draft_room()
        self.assertIs(rooms.apply(plain, self.gains), self.gains)
        self.assertEqual(rooms.apply(self.room, self.gains[:12]), self.gains[:12])


if __name__ == "__main__":
    unittest.main()
