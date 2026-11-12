"""Phase 2 audit fixes (12 November 2026): a house's rooms reach the draft with the house values.

Found by running the full calculation on copies of the saved house jobs (sets 15, 16 and 18) with AI blocked:
- the value resolver gave every house room the generic room's values, which replaced the house profile in the draft
  (every house room had 10 W/m² lighting and 75/55 W per person);
- no house room had infiltration: the airflow step looks for outside walls before the traced walls join the proposal;
- house infiltration followed the room's occupied hours (a bedroom leaked no air in the afternoon);
- drawn cooking appliances in a house kitchen ran at 75 % through the living-area hours instead of home cooking hours;
- every typical or generic equipment value was labelled as project evidence at 0.85 confidence.
"""
import unittest

from ai import (ai_preliminary, airflow_resolution, ceiling_volume_resolution, house_household, house_profiles,
                internal_gains_resolution, room_use_resolution, value_resolution)

SPACES = [("MASTER BEDROOM", 16), ("BED 2", 11), ("LIVING", 30), ("KITCHEN", 15), ("HALL", 8)]


def wall(room, area=20.0):
    return {"surface_key": f"test:{room}:external", "label": f"{room} external wall", "owner_room_label": room,
            "owner_level_name": "Ground", "physical_type": "wall", "thermal_role": "external", "external_exposure": "external",
            "orientation": "W", "azimuth_deg": 270.0, "gross_area_m2": area, "confidence": .65, "page": 1,
            "evidence": [{"page": 1, "reference": "Room trace test", "excerpt": "Reviewer-declared room boundary."}],
            "verification_status": "provisional", "rationale": "Test external wall."}


def house(equipment=None, outside_walls=("MASTER BEDROOM", "LIVING")):
    building = {"spaces": [{"name": name, "level_name": "Ground", "area_m2": area, "ceiling_height_mm": 2700,
                            "evidence": [{"page": 1, "excerpt": name}]} for name, area in SPACES]}
    proposal = {"rooms": [{"kind": "room", "label": name, "level_name": "Ground", "area_m2": area,
                           "preliminary_profile_id": "residential", "confidence": .8, "page": 1,
                           "evidence": [{"page": 1, "excerpt": name}], "ceiling_height_mm": 2700,
                           **({"equipment": equipment, "equipment_catalogue_managed": True} if equipment and name == "KITCHEN" else {})}
                          for name, area in SPACES]}
    uses = room_use_resolution.resolve(building, proposal=proposal,
                                       dwelling_context={"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]})
    rooms = [{"room_id": row["room_id"], "label": row["original_label"], "level": row["level_name"], "category": row["taxonomy_id"],
              "scope": row["space_scope"], "area_m2": dict(SPACES)[row["original_label"]]} for row in uses["records"]]
    pack = ai_preliminary.load_pack()
    gains = internal_gains_resolution.resolve(building, {}, proposal, uses, pack, {}, household=house_household.plan(rooms))
    ceiling = ceiling_volume_resolution.resolve(building, {}, proposal, {}, pack, {})
    # The airflow step sees the proposal before the traced walls are added, as the job does (ai_preliminary_service).
    airflow = airflow_resolution.resolve(building, {}, proposal, uses, ceiling, pack, {})
    traced = {**proposal, "surfaces": [wall(name) for name in outside_walls]}
    input_set = ai_preliminary.assemble(building, preliminary_proposal=traced, room_use_resolution=uses,
                                        internal_gains_resolution=gains, airflow_resolution=airflow)
    return input_set, gains, airflow


class HouseValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.input_set, cls.gains, cls.airflow = house()
        cls.model = {row["name"]: row for row in cls.input_set["material"]["hourly_load_model"]["rooms"]}

    def test_the_value_resolver_gives_house_rooms_the_house_profile(self):
        pack = ai_preliminary.load_pack()
        for profile_id in ("house_bedroom", "house_living", "circulation"):
            expected = house_profiles.profile(pack["profiles"], profile_id)
            _artifact, values = value_resolution.build_value_resolution(
                pack, {}, {"rooms": [{"label": "Room", "level_name": "Ground", "area_m2": 12, "preliminary_profile_id": profile_id}]}, None)
            room = values["rooms"][value_resolution.room_target_id("Room", "Ground")]
            self.assertEqual(room["lighting_w_m2"], expected["lighting_w_m2"], profile_id)
            self.assertEqual(room["outside_air_lps_person"], 0, profile_id)
            self.assertEqual(room["occupancy_density_people_m2"], expected["occupancy_density_people_m2"], profile_id)

    def test_house_rooms_reach_the_draft_with_house_lighting_people_and_no_outside_air(self):
        bedroom, hall = self.model["MASTER BEDROOM"]["cooling_load"], self.model["HALL"]["cooling_load"]
        self.assertEqual((bedroom["lighting_w_m2"], bedroom["people_sensible_w_per_person"], bedroom["people_latent_w_per_person"]),
                         (5, 67, 33))
        self.assertEqual(hall["lighting_w_m2"], 5)
        self.assertEqual({name: row["cooling_load"]["outside_air_lps"] for name, row in self.model.items()},
                         {name: 0 for name in self.model})

    def test_infiltration_follows_the_traced_walls_to_outside_at_every_hour(self):
        by_room = {row["owner_room_id"]: row for row in self.airflow["records"] if row["air_path_type"] == "infiltration"}
        self.assertEqual(by_room[airflow_resolution.room_id("LIVING", "Ground")]["excluded_reason"], "no_outside_wall")
        report = ai_preliminary.calculate(self.input_set)
        rooms = {room["name"]: room for room in report["scenario_results"][0]["rooms"]}

        def infiltration(name, hour):
            return (rooms[name]["hours"][hour]["components"].get("infiltration") or {}).get("total_kw", 0)
        # Living: 30 m² × 2.7 m × 0.45 ACH = 10.1 L/s; at 15:00 that is a cooling load. Bed 2 has no wall to outside.
        self.assertGreater(infiltration("LIVING", 15), 0.05)
        self.assertGreater(infiltration("MASTER BEDROOM", 15), 0.02)       # not only in the bedroom's night hours
        self.assertEqual(infiltration("BED 2", 15), 0)
        self.assertEqual(infiltration("HALL", 15), 0)

    def test_house_infiltration_record_is_decided_by_the_final_walls(self):
        excluded = {"status": "excluded", "value": None, "excluded_reason": "no_outside_wall", "operands": {"ach": 0.36}}
        self.assertEqual(ai_preliminary._house_infiltration(excluded, True)["value"], 0.36)
        self.assertIsNone(ai_preliminary._house_infiltration(excluded, False)["value"])
        kept = {"status": "provisional", "value": 0.36, "operands": {"ach": 0.36}}
        self.assertEqual(ai_preliminary._house_infiltration(kept, False)["status"], "excluded")


class HouseKitchenEquipmentTests(unittest.TestCase):
    def test_drawn_cooking_runs_at_home_cooking_hours_and_values_keep_their_labels(self):
        cited = [{"page": 7, "excerpt": "Cooktop", "reference": "Generic value TV-0054"}]
        equipment = [
            {"name": "Cooktop", "quantity": 1, "rated_input_w": 762.5, "heat_to_space_factor": 1.0, "latent_w": 335.0,
             "origin": "generic_value", "kind": "cooking", "source": "Generic value TV-0054", "evidence": cited},
            {"name": "Refrigerator", "quantity": 1, "rated_input_w": 162.0, "heat_to_space_factor": 1.0, "latent_w": 0.0,
             "origin": "typical_value", "kind": "refrigeration", "source": "Typical value TV-0002", "evidence": cited,
             "equipment_schedule": {day: [1.0] * 24 for day in ("weekday", "saturday", "sunday", "holiday")}, "diversity_factor": 1.0}]
        input_set, gains, _airflow = house(equipment=equipment)
        kitchen = next(row for row in gains["records"] if row["original_label"] == "KITCHEN")
        items = {item["name"]: item for item in kitchen["equipment"]}
        self.assertEqual((items["Cooktop"]["origin"], items["Cooktop"]["confidence"]), ("generic_value", .3))
        self.assertEqual((items["Refrigerator"]["origin"], items["Refrigerator"]["confidence"]), ("typical_value", .5))
        self.assertEqual(kitchen["fields"]["equipment"]["confidence"], .3)
        self.assertIn("1 generic value", kitchen["fields"]["equipment"]["rationale"])
        report = ai_preliminary.calculate(input_set)
        hours = next(room for room in report["scenario_results"][0]["rooms"] if room["name"] == "KITCHEN")["hours"]
        equipment_kw = [hours[hour]["components"]["equipment_refrigeration"]["total_kw"] for hour in (15, 17)]
        # 15:00: only the fridge (0.162 kW); 17:00: the fridge and half the cooktop's sensible and latent heat.
        self.assertAlmostEqual(equipment_kw[0], 0.162, places=3)
        self.assertAlmostEqual(equipment_kw[1], 0.162 + 0.5 * (0.7625 + 0.335), places=3)


if __name__ == "__main__":
    unittest.main()
