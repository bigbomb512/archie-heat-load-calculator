import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from ai import ai_preliminary, airflow_resolution, ceiling_volume_resolution, room_use_resolution
from ai import room_outline


ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "tests" / "fixtures" / "house_room_labels.json"


class HouseFoundationTests(unittest.TestCase):
    def test_house_aliases_and_combined_labels(self):
        expected = {
            "BED": "house_bedroom", "BED 2": "house_bedroom", "BR": "house_bedroom", "MBR": "house_bedroom",
            "Non-trafficable roof": "outdoor", "ROOF": "outdoor", "LIVING/DINING": "house_living", "KITCHEN/DINING": "house_living", "LIVING DINING": "house_living",
            "KITCH": "house_living", "PANTRY": "house_living", "SCULLERY": "house_living",
            "ENS": "house_wet", "PDR": "house_wet", "L'DRY": "house_service", "W.I.R.": "house_service",
            "CARPORT": "garage", "ALFRESCO": "outdoor", "LIFT": "lift_shaft", "ENTRY": "circulation",
        }
        for label, category in expected.items():
            self.assertEqual(room_use_resolution._house_label_matches(label), [category], label)

    def test_all_named_house_set_uses_are_one_category_when_in_scope(self):
        data = json.loads(SCAN.read_text())["cases"]
        labels = {label for case in data.values() for label in case.get("rooms", {})}
        # The scan includes non-room annotations (storage, office, plant, and VOID); every
        # actual named house function in the requested taxonomy must resolve deterministically.
        for label in labels:
            old_use = next((case.get("room_use", {}).get(label) for case in data.values() if label in case.get("rooms", {})), "")
            row = room_use_resolution.resolve({"spaces": [{"name": label}]}, dwelling_context={"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]})["records"][0]
            if any(word in label.casefold() for word in ("bed", "br ", "mbr", "ens", "bath", "wc", "powder", "laundry", "dry", "wir", "robe", "linen", "garage", "carport", "balcony", "alfresco", "deck", "terrace", "patio", "pool", "lift", "living", "lounge", "family", "rumpus", "media", "cinema", "theatre", "games", "study", "gym", "kitchen", "dining", "meals", "pantry", "scullery", "entry", "hall", "stair", "lobby")):
                self.assertEqual(len(room_use_resolution._house_label_matches(label)), 1, label)
            self.assertEqual(row["taxonomy_id"], room_use_resolution._house_label_matches(label)[0] if room_use_resolution._house_label_matches(label) else "generic_conditioned", label)
            if old_use in {"dining", "kitchen", "CONFLICT:dining/residential"}:
                self.assertEqual(row["taxonomy_id"], "house_living", label)
            if old_use == "residential":
                expected = "house_bedroom" if re.search(r"\b(master bedroom|bedroom|bed\s*\d+|br\s*\d+|mbr)\b", label, re.I) else "house_living"
                self.assertEqual(row["taxonomy_id"], expected, label)

    def test_non_dwelling_keeps_shop_label_classification(self):
        shop = room_use_resolution.resolve({"spaces": [{"name": "Kitchen", "area": 20}, {"name": "Dining", "area": 40}]})
        self.assertEqual({row["original_label"]: row["taxonomy_id"] for row in shop["records"]}, {"Kitchen": "kitchen", "Dining": "dining"})
        self.assertFalse(shop.get("dwelling_context", {}).get("is_dwelling", False))

    def test_dwelling_context_requires_textual_or_bedroom_evidence_and_excludes_shops(self):
        from backend import ai_provider
        with patch.object(ai_provider, "get", side_effect=RuntimeError("provider calls disabled")):
            from backend.room_use_resolution_service import _dwelling_context
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "page_inventory.json").write_text(json.dumps({"pages": {"1": {"title": "BASIX certificate"}}}))
                self.assertTrue(_dwelling_context(root)["is_dwelling"])
                (root / "page_inventory.json").write_text(json.dumps({"pages": {"1": {"title": "Plan"}}}))
                (root / "building_evidence.json").write_text(json.dumps({"spaces": [
                    {"name": "BED 1", "evidence": [{"page": 1}]}, {"name": "BED 2", "evidence": [{"page": 1}]}
                ]}))
                self.assertTrue(_dwelling_context(root)["is_dwelling"])
                (root / "page_inventory.json").write_text(json.dumps({"pages": {"1": {"title": "Shop fitout"}}}))
                self.assertFalse(_dwelling_context(root)["is_dwelling"])

    def test_dwelling_uses_house_uses_and_persists_context(self):
        context = {"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX"}]}
        artifact = room_use_resolution.resolve(
            {"spaces": [{"name": "BED 2", "level_name": "Ground", "area": 14},
                        {"name": "ENS", "level_name": "Ground", "area": 5}]},
            dwelling_context=context,
        )
        self.assertTrue(artifact["dwelling_context"]["is_dwelling"])
        bedroom = next(row for row in artifact["records"] if row["original_label"] == "BED 2")
        ensuite = next(row for row in artifact["records"] if row["original_label"] == "ENS")
        self.assertEqual((bedroom["taxonomy_id"], bedroom["preliminary_profile_id"], bedroom["space_scope"]), ("house_bedroom", "house_bedroom", "comfort_hvac"))
        self.assertEqual((ensuite["taxonomy_id"], ensuite["space_scope"]), ("house_wet", "not_cooled"))
        self.assertTrue(bedroom["dwelling"])

    def test_house_config_is_separate_and_values_have_basis_label(self):
        house = json.loads((ROOT / "config" / "house_profiles.json").read_text())
        self.assertIn("typical home value", house["basis_label"])
        self.assertTrue(house["basis"])
        self.assertEqual(set(house["profiles"]), room_use_resolution.HOUSE_USES)
        self.assertEqual((house["profiles"]["house_bedroom"]["outside_air_lps_person"], house["profiles"]["house_bedroom"]["outside_air_lps_m2"]), (0, 0))

    def test_unnamed_house_names_are_opt_in_for_dwelling_tasks(self):
        self.assertNotIn("Bedroom", room_outline.unnamed_space_prompt([]))
        self.assertIn("Bedroom", room_outline.unnamed_space_prompt([], dwelling=True))
        reply = {"areas": [{"number": 1, "room": "Bedroom"}], "splits": []}
        transform = {"factor": 1, "offset_px": [0, 0]}
        with self.assertRaises(ValueError):
            room_outline.validate_naming_reply(reply, 1, [], transform, require_all=True)
        names, _ = room_outline.validate_naming_reply(reply, 1, [], transform, require_all=True, dwelling=True)
        self.assertEqual(names[0], "Bedroom")

    def test_synthetic_dwelling_resolver_to_assembler(self):
        building = {"spaces": [
            {"name": "BED 2", "level_name": "Ground", "area_m2": 14, "ceiling_height_mm": 2700, "evidence": [{"page": 1, "excerpt": "BED 2"}]},
            {"name": "LIVING/DINING", "level_name": "Ground", "area_m2": 30, "ceiling_height_mm": 2700, "evidence": [{"page": 1, "excerpt": "LIVING/DINING"}]},
            {"name": "BATH", "level_name": "Ground", "area_m2": 5, "ceiling_height_mm": 2700, "evidence": [{"page": 1, "excerpt": "BATH"}]},
            {"name": "ENTRY", "level_name": "Ground", "area_m2": 5, "ceiling_height_mm": 2700, "evidence": [{"page": 1, "excerpt": "ENTRY"}]},
        ]}
        proposal = {"rooms": [{"kind": "room", "label": row["name"], "level_name": "Ground", "area_m2": row["area_m2"],
                               "preliminary_profile_id": "residential", "confidence": .8, "page": 1,
                               "evidence": row["evidence"], "ceiling_height_mm": 2700} for row in building["spaces"]],
                    "surfaces": [{"owner_room_label": label, "owner_level_name": "Ground", "physical_type": "wall",
                                  "thermal_role": "external", "external_exposure": "external", "orientation": "E",
                                  "gross_area_m2": 10, "evidence": [{"page": 1, "excerpt": f"{label} east wall"}]}
                                 for label in ("BED 2", "LIVING/DINING")]}
        context = {"is_dwelling": True, "evidence": [{"page": 1, "excerpt": "BASIX certificate"}]}
        uses = room_use_resolution.resolve(building, proposal=proposal, dwelling_context=context)
        pack = ai_preliminary.load_pack()
        ceiling = ceiling_volume_resolution.resolve(building, {}, proposal, {}, pack, {})
        airflow = airflow_resolution.resolve(building, {}, proposal, uses, ceiling, pack, {})
        assembled = ai_preliminary.assemble(building, preliminary_proposal=proposal, room_use_resolution=uses, airflow_resolution=airflow)
        model = assembled["material"]["hourly_load_model"]
        rooms = {row["name"]: row for row in model["rooms"]}
        self.assertEqual(set(rooms), {"BED 2", "LIVING/DINING", "ENTRY"})
        self.assertEqual(rooms["BED 2"]["cooling_load"]["outside_air_lps"], 0)
        self.assertEqual(rooms["LIVING/DINING"]["cooling_load"]["outside_air_lps"], 0)
        entry_zone = next(row for row in assembled["material"]["requirements"]["zones"] if row["name"] == "ENTRY")
        self.assertEqual(entry_zone["occupancy"], 0)
        for name in ("BED 2", "LIVING/DINING"):
            infiltration = next(row for row in rooms[name]["unapproved_components"] if row["component_type"] == "infiltration")
            self.assertEqual((infiltration["value"], infiltration["unit"]), (.36, "ACH"))
            self.assertIn("House profile", infiltration["source"])
        excluded = next(row for row in assembled["excluded_spaces"] if row["room_name"] == "BATH")
        self.assertEqual(excluded["scope"], "not_cooled")
        self.assertIn("not cooled", excluded["reason"])
        self.assertIn("BATH", {row["label"] for row in assembled["proposal"]["rooms"]})
        fields = {row["field"]: row for row in assembled["materialized_fields"] if row.get("room_id") == rooms["BED 2"]["room_id"]}
        self.assertEqual(fields["room_profile"]["profile_id"], "house_bedroom")
        self.assertEqual(fields["room_profile"]["value"], "house_bedroom")


if __name__ == "__main__":
    unittest.main()
