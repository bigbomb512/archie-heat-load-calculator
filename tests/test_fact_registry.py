#!/usr/bin/env python3

import unittest

from ai.fact_registry import activate_safe_facts, validate_fact, validate_registry


class FactRegistryTests(unittest.TestCase):
    def fact(self, category="floor", value=None, activation="proposed"):
        return {
            "fact_id": "fact-1", "category": category, "value": {"level_name": "Level 1"} if value is None else value,
            "unit": "", "affected_id": "level_1", "source_type": "architect_pdf",
            "source": {"page": 1, "drawing_number": "202"}, "excerpt": "Level 1",
            "extraction_confidence": 0.99, "validation_status": "valid",
            "activation_status": activation, "dependencies": [], "conflicts": [],
            "candidate_fingerprint": "abc", "evidence_ids": ["ev-1"],
        }

    def test_exact_floor_can_activate(self):
        result = activate_safe_facts({"schema_version": 2, "facts": [self.fact()], "conflicts": [], "review_items": []})
        self.assertEqual(result["facts"][0]["activation_status"], "active")

    def test_area_requires_positive_units(self):
        with self.assertRaises(ValueError):
            validate_fact(self.fact("area", 0))

    def test_ambiguous_room_stays_proposed(self):
        result = activate_safe_facts({"schema_version": 2, "facts": [self.fact("room")], "conflicts": [], "review_items": []})
        self.assertEqual(result["facts"][0]["activation_status"], "proposed")


if __name__ == "__main__":
    unittest.main()
