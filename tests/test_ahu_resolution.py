import copy
import unittest

from ai import ahu_resolution


PACK = {"ahu_profiles": {"single_zone_constant_volume": {
    "airflow_lps": {"supply": 100, "return": 80, "outside_air": 20},
    "fan_heat_kw": 1.0, "duct_sensible_kw": 0.1, "leakage_lps": 2,
    "heat_recovery": {"sensible_effectiveness": 0, "latent_effectiveness": 0},
    "preconditioning": {"sensible_effectiveness": 0, "latent_effectiveness": 0, "reference_db_c": 24, "reference_wb_c": 18},
    "coil": {"leaving_db_c": 13, "leaving_wb_c": 12},
}}}


def candidate(tag="ahu-1"):
    citation = [{"page": 2, "reference": "M-01", "excerpt": "AHU and duct plan"}]
    return {"tag": tag, "system_type": "constant_volume", "served_zone_ids": ["zone-1"], "served_room_ids": ["room-1"],
            "page": 2, "source": "mechanical plan", "evidence": citation,
            "airflow_records": [
                {"path_type": "supply", "zone_id": "zone-1", "flow_lps": 100, "page": 2, "evidence": citation},
                {"path_type": "return", "zone_id": "zone-1", "flow_lps": 80, "page": 2, "evidence": citation},
                {"path_type": "outside_air", "zone_id": "zone-1", "flow_lps": 20, "page": 2, "evidence": citation},
            ],
            "fans": [{"page": 2, "heat_kw": 1, "evidence": citation}],
            "coils": [{"page": 2, "leaving_db_c": 13, "leaving_wb_c": 12, "evidence": citation}]}


class AhuResolutionTests(unittest.TestCase):
    def test_normalizes_cv_and_is_stable_when_order_changes(self):
        vision = {"air_side_candidates": [candidate()]}
        first = ahu_resolution.resolve(vision, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack=PACK, source_fingerprints={"vision_response": "source"})
        second = ahu_resolution.resolve({"air_side_candidates": [copy.deepcopy(candidate())]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack=PACK, source_fingerprints={"vision_response": "source"})
        self.assertEqual(first["systems"][0]["ahu_id"], second["systems"][0]["ahu_id"])
        self.assertEqual(first["summary"]["paths"], 3)

    def test_unknown_zone_blocks_system(self):
        row = candidate()
        row["served_zone_ids"] = ["missing-zone"]
        result = ahu_resolution.resolve({"air_side_candidates": [row]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack=PACK, source_fingerprints={"vision_response": "source"})
        self.assertEqual(result["systems"][0]["status"], "blocked")

    def test_units_and_controlled_fallback_are_traceable(self):
        row = candidate()
        row["airflow_records"][0].pop("flow_lps")
        row["airflow_records"][0].update({"airflow_value": 0.1, "airflow_unit": "m3/s"})
        result = ahu_resolution.resolve({"air_side_candidates": [row]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack=PACK, source_fingerprints={"vision_response": "source"})
        supply = next(item for item in result["airflow_records"] if item["path_type"] == "supply")
        self.assertEqual(supply["flow_lps"], 100)
        self.assertEqual(supply["flow_basis"]["conversion"], "multiply by 1000.0 to L/s")

    def test_preliminary_materialization_isolated(self):
        result = ahu_resolution.resolve({"air_side_candidates": [candidate()]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack=PACK, source_fingerprints={"vision_response": "source"})
        material = ahu_resolution.materialize(result, preliminary=True)
        self.assertEqual(material["systems"]["systems"][0]["review_status"], "provisional")
        self.assertEqual(material["policy"]["mode"], "ai_preliminary")

    def test_uncited_numeric_candidate_does_not_become_evidence(self):
        row = candidate()
        row.pop("page")
        row.pop("evidence")
        for path in row["airflow_records"]:
            path.pop("page", None)
            path.pop("evidence", None)
        result = ahu_resolution.resolve({"air_side_candidates": [row]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack={}, source_fingerprints={"vision_response": "source"})
        self.assertEqual(result["systems"][0]["origin"], "unresolved")
        self.assertTrue(any(item["status"] in {"blocked", "needs_review"} for item in result["airflow_records"]))

    def test_preliminary_materialization_allows_needs_review_values(self):
        row = candidate()
        # The topology and numeric values are complete, but there is no page
        # citation.  The resolver must keep this as needs_review while the
        # draft path still makes the isolated provisional inputs available.
        row.pop("page")
        row.pop("evidence")
        for path in row["airflow_records"]:
            path.pop("page", None)
            path.pop("evidence", None)
        for component in row["fans"] + row["coils"]:
            component.pop("page", None)
            component.pop("evidence", None)
        result = ahu_resolution.resolve({"air_side_candidates": [row]}, known_zone_ids={"zone-1"}, known_room_ids={"room-1"}, pack={}, source_fingerprints={"vision_response": "source"})
        self.assertEqual(result["systems"][0]["status"], "needs_review")
        material = ahu_resolution.materialize(result, preliminary=True)
        self.assertEqual(len(material["systems"]["systems"]), 1)
        self.assertEqual(material["systems"]["systems"][0]["review_status"], "provisional")
        self.assertEqual(len(material["model"]["airflow_records"]), 3)
        self.assertFalse(material["excluded"])
        reviewed = ahu_resolution.materialize(result, preliminary=False)
        self.assertFalse(reviewed["systems"]["systems"])
        self.assertTrue(reviewed["excluded"])


if __name__ == "__main__":
    unittest.main()
