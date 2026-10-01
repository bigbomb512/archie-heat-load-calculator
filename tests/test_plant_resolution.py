import copy
import unittest

from ai import ai_preliminary, plant_resolution


def candidate_fixture():
    return {
        "plant_candidates": [
            {
                "tag": "CH-1", "plant_type": "chiller", "served_ahu_ids": ["ahu_1"],
                "circuit_ids": ["CHW-1"], "page": 12,
                "evidence": [{"page": 12, "reference": "M-12", "excerpt": "CH-1"}],
            },
            {
                "tag": "B-1", "plant_type": "boiler", "page": 13,
                "evidence": [{"page": 13, "reference": "M-13", "excerpt": "B-1"}],
            },
        ],
        "plant_circuits": [
            {
                "tag": "CHW-1", "plant_id": "CH-1", "circuit_type": "chilled_water",
                "served_ahu_ids": ["ahu_1"], "flow_value": 0.01, "flow_unit": "m3/s",
                "page": 12, "evidence": [{"page": 12, "reference": "M-12", "excerpt": "CHW-1"}],
            }
        ],
    }


class PlantResolutionTests(unittest.TestCase):
    def resolve(self, proposal=None):
        return plant_resolution.resolve(
            proposal=proposal or candidate_fixture(), known_ahu_ids={"ahu_1"},
            pack=ai_preliminary.load_pack(), source_fingerprints={"vision_response": "vision-fp"},
        )

    def test_source_linked_chiller_and_circuit_are_provisional_and_materialize(self):
        result = self.resolve()
        self.assertEqual(result["summary"]["systems"], 2)
        chiller = next(row for row in result["systems"] if row["plant_type"] == "chiller")
        circuit = result["circuits"][0]
        self.assertEqual(chiller["status"], "provisional")
        self.assertAlmostEqual(circuit["flow_lps"], 10.0)
        self.assertEqual(circuit["status"], "provisional")
        material = plant_resolution.materialize(result, preliminary=True)
        self.assertEqual(len(material["plant_systems"]["systems"]), 2)
        self.assertEqual(len(material["hydraulic_circuits"]["circuits"]), 1)

    def test_reviewed_materialization_excludes_provisional_records(self):
        result = self.resolve()
        material = plant_resolution.materialize(result, preliminary=False)
        self.assertEqual(material["plant_systems"]["systems"], [])
        self.assertTrue(any(item["kind"] == "plant" for item in material["excluded"]))

    def test_boiler_is_visible_but_deferred(self):
        result = self.resolve()
        boiler = next(row for row in result["systems"] if row["plant_type"] == "boiler")
        self.assertEqual(boiler["status"], "provisional")
        self.assertEqual(result["summary"]["deferred_systems"], 1)

    def test_unknown_ahu_blocks_only_affected_topology(self):
        proposal = candidate_fixture()
        proposal["plant_candidates"][0]["served_ahu_ids"] = ["ahu_missing"]
        proposal["plant_circuits"][0]["served_ahu_ids"] = ["ahu_missing"]
        result = self.resolve(proposal)
        self.assertTrue(any("unknown served AHU IDs" in value for value in next(row for row in result["systems"] if row["plant_type"] == "chiller")["unresolved_fields"]))
        material = plant_resolution.materialize(result, preliminary=True)
        self.assertEqual(material["plant_systems"]["systems"], [row for row in material["plant_systems"]["systems"] if row["plant_type"] == "boiler"])

    def test_stable_ids_survive_evidence_reordering(self):
        first = self.resolve()
        proposal = candidate_fixture()
        proposal["plant_candidates"] = list(reversed(proposal["plant_candidates"]))
        proposal["plant_circuits"] = list(reversed(proposal["plant_circuits"]))
        second = self.resolve(proposal)
        self.assertEqual({row["plant_id"] for row in first["systems"]}, {row["plant_id"] for row in second["systems"]})
        self.assertEqual({row["circuit_id"] for row in first["circuits"]}, {row["circuit_id"] for row in second["circuits"]})

    def test_duplicate_circuit_ownership_is_blocked(self):
        proposal = candidate_fixture()
        duplicate = copy.deepcopy(proposal["plant_circuits"][0])
        duplicate["tag"] = "CHW-2"
        proposal["plant_circuits"].append(duplicate)
        result = self.resolve(proposal)
        self.assertTrue(any(item["type"] == "duplicate_ahu_circuit_owner" for item in result["issues"]))
        self.assertTrue(any(row["status"] == "blocked" for row in result["circuits"]))

    def test_uncited_numeric_values_are_not_used_as_physical_inputs(self):
        proposal = candidate_fixture()
        proposal["plant_candidates"][0]["evidence"] = []
        proposal["plant_circuits"][0]["evidence"] = []
        proposal["plant_circuits"][0]["flow_value"] = 999
        result = self.resolve(proposal)
        circuit = result["circuits"][0]
        self.assertNotEqual(circuit["flow_lps"], 999)
        self.assertEqual(circuit["origin"], "controlled_fallback")
        self.assertTrue(circuit["preliminary_assumption"])

    def test_override_wins_and_clear_returns_review_state(self):
        result = self.resolve()
        plant_id = next(row["plant_id"] for row in result["systems"] if row["plant_type"] == "chiller")
        overridden = plant_resolution.apply_override(result, "plant", plant_id, {"diversity_factor": 0.8}, "reviewer", "draft test")
        row = next(item for item in overridden["systems"] if item["plant_id"] == plant_id)
        self.assertEqual(row["origin"], "contractor_override")
        self.assertEqual(row["diversity_factor"], 0.8)
        cleared = plant_resolution.clear_override(overridden, "plant", plant_id)
        row = next(item for item in cleared["systems"] if item["plant_id"] == plant_id)
        self.assertEqual(row["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
