"""A house's occupants by the bedroom rule, and the household counted once in the whole-house total."""

import unittest

from ai import (ai_preliminary, airflow_resolution, ceiling_volume_resolution, hourly_loads, house_household,
                internal_gains_resolution, room_use_resolution)
from backend.ai_preliminary_service import _workspace_report


def room(room_id, label, category, area=12, scope="comfort_hvac", level="Ground Floor"):
    return {"room_id": room_id, "label": label, "level": level, "category": category, "scope": scope, "area_m2": area}


class PlanTests(unittest.TestCase):
    def test_main_bedroom_by_label_then_ensuite_then_size(self):
        rooms = [room("b1", "Bed 1", "house_bedroom", 14), room("m", "Master Bedroom", "house_bedroom", 12),
                 room("b2", "Bed 2", "house_bedroom", 10), room("liv", "Living", "house_living", 30),
                 room("kit", "Kitchen", "house_living", 15), room("hall", "Hall", "circulation"),
                 room("ens", "Ensuite", "house_wet", scope="not_cooled")]
        plan = house_household.plan(rooms, ensuite_bedrooms={"b1"})
        self.assertEqual({key: row["count"] for key, row in plan.items()}, {"m": 2, "b1": 1, "b2": 1, "liv": 4, "kit": 4})
        self.assertIn("labelled Master Bedroom", plan["m"]["rationale"])
        unlabelled = [dict(row, label=row["label"].replace("Master Bedroom", "Bed 3")) for row in rooms]
        plan = house_household.plan(unlabelled, ensuite_bedrooms={"b2"})
        self.assertEqual(plan["b2"]["count"], 2)                         # the bedroom with the ensuite
        self.assertIn("ensuite", plan["b2"]["rationale"])
        plan = house_household.plan(unlabelled)
        self.assertEqual(plan["b1"]["count"], 2)                         # else the largest
        self.assertIn("largest", plan["b1"]["rationale"])

    def test_two_dwellings_each_count_their_own_household_and_no_bedroom_means_no_household(self):
        rooms = [room("a1", "Bed 1", "house_bedroom", level="Dwelling A ground"), room("a2", "Living", "house_living", level="Dwelling A ground"),
                 room("b1", "Bed 1", "house_bedroom", level="Dwelling B ground"), room("b2", "Bed 2", "house_bedroom", 9, level="Dwelling B ground"),
                 room("b3", "Living", "house_living", level="Dwelling B ground")]
        plan = house_household.plan(rooms)
        self.assertEqual((plan["a2"]["count"], plan["b3"]["count"]), (2, 3))
        self.assertEqual({plan["a2"]["group_id"], plan["b3"]["group_id"]}, {"household:Dwelling A", "household:Dwelling B"})
        self.assertEqual(house_household.plan([room("liv", "Living", "house_living")]), {})


class CountOnceTests(unittest.TestCase):
    @staticmethod
    def room_result(room_id, occupancy, active_hours, people_kw_per_person=0.1):
        hours = []
        for hour in range(24):
            factor = 1.0 if hour in active_hours else 0.0
            kw = round(occupancy * people_kw_per_person * factor, 4)
            hours.append({"hour": hour, "components": {"people": {"sensible_kw": kw, "latent_kw": 0.0, "total_kw": kw,
                                                                  "schedule_factors": [factor]}}})
        return ({"room_id": room_id, "occupancy": occupancy,
                 "cooling_load": {"occupancy_group": {"group_id": "household:the house", "people": 3, "role": "living"}}},
                {"room_id": room_id, "hours": hours})

    def test_the_household_is_counted_once_per_hour_and_room_results_are_untouched(self):
        living, kitchen = self.room_result("liv", 3, {19}), self.room_result("kit", 3, {19})
        bed = self.room_result("bed", 2, {23})
        bed[0]["cooling_load"]["occupancy_group"]["role"] = "bedroom"
        project = [{"hour": hour, "components": {"people": {
                        "sensible_kw": round(sum(r[1]["hours"][hour]["components"]["people"]["sensible_kw"] for r in (living, kitchen, bed)), 4),
                        "latent_kw": 0.0, "total_kw": 0.0}},
                    "subtotal_sensible_kw": 0.0, "subtotal_latent_kw": 0.0, "subtotal_kw": 0.0, "safety_allowance_kw": 0.0,
                    "design_total_kw": 0.0} for hour in range(24)]
        for row in project:
            row["subtotal_sensible_kw"] = row["subtotal_kw"] = row["design_total_kw"] = row["components"]["people"]["sensible_kw"]
        rooms = {r[0]["room_id"]: r[0] for r in (living, kitchen, bed)}
        adjusted, groups = hourly_loads.count_shared_occupants_once(project, [living[1], kitchen[1], bed[1]], rooms)
        # 7 pm: 3 people in each of two living rooms = 6, but the household is 3: half the people heat comes off.
        self.assertAlmostEqual(adjusted[19]["components"]["people"]["sensible_kw"], 0.3)
        self.assertAlmostEqual(adjusted[19]["design_total_kw"], 0.3)
        self.assertEqual(adjusted[19]["occupancy_adjustments"][0]["people_in_rooms"], 6)
        # 11 pm: two people in the bedroom, fewer than the household: nothing changes. Room results are untouched.
        self.assertAlmostEqual(adjusted[23]["components"]["people"]["sensible_kw"], 0.2)
        self.assertEqual(living[1]["hours"][19]["components"]["people"]["sensible_kw"], 0.3)
        self.assertEqual([row["hour"] for row in groups[0]["adjusted_hours"]], [19])

    def test_rooms_without_a_group_change_nothing(self):
        result = {"room_id": "shop", "hours": [{"hour": h, "components": {}} for h in range(24)]}
        project = [{"hour": h, "components": {}, "design_total_kw": 1.0} for h in range(24)]
        self.assertEqual(hourly_loads.count_shared_occupants_once(project, [result], {"shop": {"room_id": "shop", "cooling_load": {}}}),
                         (project, []))

    def test_workspace_report_carries_peak_adjustment_for_house_only(self):
        hours = [{"hour": 19, "occupancy_adjustments": [{
            "group_id": "household:home", "people_in_rooms": 6, "people_counted": 3,
            "removed_sensible_kw": 0.45, "removed_latent_kw": 0.0,
        }]}]
        house = _workspace_report({
            "included_scope_peak": {"scenario_id": "summer", "display_hour": 19},
            "scenario_results": [{"scenario_id": "summer", "included_scope_hours": hours,
                                  "occupancy_groups": [{"group_id": "household:home", "people": 3,
                                                        "basis": "Household counted once."}]}],
        })
        self.assertEqual(house["included_scope_peak"]["household_counted_once"], {
            "people_in_rooms": 6, "people_counted": 3, "removed_sensible_kw": 0.45,
            "removed_latent_kw": 0.0, "basis": "Household counted once.",
        })
        shop = _workspace_report({"included_scope_peak": {"scenario_id": "summer", "display_hour": 19},
                                  "scenario_results": [{"scenario_id": "summer", "included_scope_hours": hours}]})
        self.assertNotIn("household_counted_once", shop["included_scope_peak"])


class HouseThroughTheModelTests(unittest.TestCase):
    def test_household_counts_reach_the_model_and_the_house_total_counts_them_once(self):
        spaces = [("MASTER BEDROOM", 16), ("BED 2", 11), ("LIVING", 30), ("KITCHEN", 15)]
        building = {"spaces": [{"name": name, "level_name": "Ground", "area_m2": area, "ceiling_height_mm": 2700,
                                "evidence": [{"page": 1, "excerpt": name}]} for name, area in spaces]}
        proposal = {"rooms": [{"kind": "room", "label": name, "level_name": "Ground", "area_m2": area,
                               "preliminary_profile_id": "residential", "confidence": .8, "page": 1,
                               "evidence": [{"page": 1, "excerpt": name}], "ceiling_height_mm": 2700} for name, area in spaces]}
        uses = room_use_resolution.resolve(building, proposal=proposal,
                                           dwelling_context={"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]})
        rooms = [{"room_id": row["room_id"], "label": row["original_label"], "level": row["level_name"], "category": row["taxonomy_id"],
                  "scope": row["space_scope"], "area_m2": dict(spaces)[row["original_label"]]} for row in uses["records"]]
        household = house_household.plan(rooms)
        pack = ai_preliminary.load_pack()
        gains = internal_gains_resolution.resolve(building, {}, proposal, uses, pack, {}, household=household)
        by_label = {row["original_label"]: row for row in gains["records"]}
        self.assertEqual({label: row["occupancy_count"] for label, row in by_label.items()},
                         {"MASTER BEDROOM": 2, "BED 2": 1, "LIVING": 3, "KITCHEN": 3})
        self.assertEqual(by_label["LIVING"]["fields"]["occupancy_count"]["origin"], "household_rule")
        self.assertEqual(by_label["LIVING"]["occupancy_group"]["people"], 3)
        ceiling = ceiling_volume_resolution.resolve(building, {}, proposal, {}, pack, {})
        airflow = airflow_resolution.resolve(building, {}, proposal, uses, ceiling, pack, {})
        input_set = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=uses,
                                            internal_gains_resolution=gains, airflow_resolution=airflow)
        model = {row["name"]: row for row in input_set["material"]["hourly_load_model"]["rooms"]}
        self.assertEqual({name: row["occupancy"] for name, row in model.items()},
                         {"MASTER BEDROOM": 2, "BED 2": 1, "LIVING": 3, "KITCHEN": 3})
        self.assertEqual(model["KITCHEN"]["cooling_load"]["occupancy_group"]["group_id"], "household:the house")
        report = ai_preliminary.calculate(input_set)
        scenario = report["scenario_results"][0]
        evening = scenario["included_scope_hours"][19]
        adjustment = evening["occupancy_adjustments"][0]
        self.assertEqual((adjustment["people_in_rooms"], adjustment["people_counted"]), (6, 3))
        rooms_people = sum(room["hours"][19]["components"]["people"]["total_kw"] for room in scenario["rooms"])
        self.assertAlmostEqual(evening["components"]["people"]["total_kw"], rooms_people / 2, places=3)
        self.assertEqual(scenario["occupancy_groups"][0]["people"], 3)
        # The kitchen has no appliances on the drawings: labelled typical home appliances, the fridge all day.
        kitchen = by_label["KITCHEN"]
        self.assertEqual(kitchen["fields"]["equipment"]["origin"], "typical_home_value")
        self.assertEqual([item["name"].split(" (")[0] for item in kitchen["equipment"]], ["Refrigerator", "Cooking: cooktop and oven"])
        kitchen_hours = next(room for room in scenario["rooms"] if room["name"] == "KITCHEN")["hours"]
        at_3am = kitchen_hours[3]["components"]["equipment_refrigeration"]["total_kw"]
        self.assertAlmostEqual(at_3am, 0.06, places=3)                    # only the fridge, in the middle of the night
        self.assertGreater(kitchen_hours[17]["components"]["equipment_refrigeration"]["total_kw"], 0.5)


if __name__ == "__main__":
    unittest.main()
