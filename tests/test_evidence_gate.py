#!/usr/bin/env python3

import copy
import unittest

from ai.building_evidence import build_building_evidence
from ai.drawing_coverage import build_drawing_coverage, source_fingerprint
from tools.create_reviewed_cooling_case import coverage_is_current


def source(pages=None):
    return {
        "source_pdf": "/private/drawing.pdf",
        "drawing_set": {"pages": pages or []},
        "confirmed_pages": {},
        "page_triage": {"pages": []},
    }


class EvidenceGateTests(unittest.TestCase):
    def test_coverage_has_source_fingerprint_and_page_roles(self):
        ai_input = source([{
            "page": 23, "title": "Dimensioned Top View Plan", "drawing_number": "A-101",
            "detected_type": "floor_plan", "thermal_role": "primary_geometry",
            "plan_role": "detail_plan", "level_name": "Level 1", "rooms": [],
        }])
        coverage = build_drawing_coverage(ai_input)
        self.assertEqual(coverage["source_fingerprint"], source_fingerprint(ai_input))
        self.assertEqual(coverage["page_roles"][0]["proposed_role"], "supporting_geometry_plan")
        self.assertEqual(coverage["page_roles"][0]["authority_status"], "proposed")
        self.assertTrue(coverage_is_current(coverage, ai_input))

    def test_empty_or_stale_coverage_is_not_current(self):
        ai_input = source([{"page": 1, "title": "Level 1 Plan", "detected_type": "floor_plan"}])
        self.assertFalse(coverage_is_current({}, ai_input))
        coverage = build_drawing_coverage(ai_input)
        changed = copy.deepcopy(ai_input)
        changed["source_pdf"] = "/private/other.pdf"
        self.assertFalse(coverage_is_current(coverage, changed))

    def test_word_samples_are_used_without_inventing_loads(self):
        ai_input = source([{
            "page": 23, "title": "Services Plan", "drawing_number": "RCP-01",
            "detected_type": "floor_plan", "thermal_role": "primary_geometry",
            "level_name": "Level 1", "rooms": [],
        }])
        coverage = build_drawing_coverage(ai_input)
        building = build_building_evidence(ai_input, coverage, {
            "pages": [{"page": 23, "title_blocks": [],
                       "word_samples": [{"text": "CH:3150MM"}, {"text": "OVEN"}, {"text": "W1"}]}]
        })
        self.assertTrue(building["openings"])
        self.assertTrue(building["equipment"])
        self.assertTrue(all(item.get("watts") is None for item in building["equipment"]))
        self.assertFalse(building["spaces"], "room identity must not be invented from a ceiling/equipment label")


if __name__ == "__main__":
    unittest.main()
