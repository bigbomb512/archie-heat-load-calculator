"""Heat per person follows the activity typical of each room use (AIRAH handbook values, room at 24 °C)."""

import unittest

from ai import ai_preliminary, internal_gains_resolution, room_use_resolution


class PeopleActivityTests(unittest.TestCase):
    def test_kitchen_staff_and_diners_give_off_different_heat_and_it_is_recorded(self):
        spaces = [("Kitchen", 60, "hospitality"), ("Dining", 120, "hospitality")]
        building = {"spaces": [{"name": name, "level_name": "Ground", "area_m2": area, "evidence": [{"page": 2, "excerpt": name.upper()}]}
                               for name, area, _profile in spaces]}
        proposal = {"rooms": [{"kind": "room", "label": name, "level_name": "Ground", "area_m2": area, "preliminary_profile_id": profile, "page": 2}
                              for name, area, profile in spaces]}
        use = room_use_resolution.resolve(building, proposal=proposal, source_fingerprints={})
        gains = internal_gains_resolution.resolve(building, proposal=proposal, room_use=use, pack=ai_preliminary.load_pack(),
                                                  source_fingerprints={"proposal": "a"})
        out = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=use, internal_gains_resolution=gains)
        people = {zone["name"]: (zone["cooling_load"]["people_sensible_w_per_person"], zone["cooling_load"]["people_latent_w_per_person"])
                  for zone in out["material"]["requirements"]["zones"]}
        self.assertEqual(people["Kitchen"], (85, 135))          # light bench work
        self.assertEqual(people["Dining"], (70, 50))            # seated, very light work
        recorded = [row for row in out["materialized_fields"] if row.get("field") == "people_activity"]
        self.assertEqual(sorted(row["value"] for row in recorded), ["Light bench work", "Seated, very light work"])
        self.assertTrue(all("AIRAH Technical Handbook" in row["rationale"] for row in recorded))


if __name__ == "__main__":
    unittest.main()
