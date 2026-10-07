#!/usr/bin/env python3
"""Page-role scorecard: answer-sheet checks, scoring rules and the cross-set summary."""

import unittest

from ai import page_role_evaluation as evaluation

PACKET = {
    "primary_pages": [{"page": 2, "type": "floor_plan", "plan_role": "main_floor_plan", "title": "Outdoor Floor Plan"},
                      {"page": 7, "type": "floor_plan", "plan_role": "detail_plan", "title": "Banquette"}],
    "reference_pages": [{"page": 3, "type": "reflected_ceiling_plan", "plan_role": "reference_context"}],
    "kept_pages": [{"page": 1, "type": "detail", "plan_role": None, "title": "Proposed Floor Plan"},
                   {"page": 2, "type": "floor_plan", "plan_role": "main_floor_plan"},
                   {"page": 3, "type": "reflected_ceiling_plan", "plan_role": "reference_context"}],
    "discarded_pages": [{"page": 5, "type": "render_or_photo", "plan_role": None, "title": "Reflected Ceiling Plan"}],
}


class PageRoleEvaluationTests(unittest.TestCase):
    def test_answer_sheets_are_checked(self):
        for bad in ({}, {"case_id": "c"}, {"case_id": "c", "pages": {"walls": [1]}},
                    {"case_id": "c", "pages": {"geometry": ["1"]}}, {"case_id": "c", "pages": {"geometry": [0]}},
                    {"case_id": "c", "pages": {"geometry": [1], "not_geometry": [1]}}):
            with self.assertRaises(ValueError):
                evaluation.validate_answer_sheet(bad)

    def test_roles_take_the_strongest_group_and_main_plans_are_primary_main_floor_plans(self):
        roles = evaluation.app_page_roles(PACKET)
        self.assertEqual(roles[2]["group"], "primary")
        self.assertEqual(roles[3]["group"], "reference")
        self.assertEqual(roles[1]["group"], "kept")
        self.assertEqual(roles[5]["group"], "discarded")
        self.assertEqual(evaluation.main_plan_pages(roles), [2])

    def test_each_fact_passes_or_fails_by_its_rule(self):
        sheet = {"case_id": "caseX", "confirmed": True,
                 "pages": {"geometry": [1], "rcp": [3, 5], "must_keep": [1, 3, 5, 9], "not_geometry": [2, 7]}}
        report = evaluation.score(sheet, PACKET)
        results = {(fact["fact"], str(fact["page"])): fact["passed"] for fact in report["facts"]}
        self.assertFalse(results[("geometry", "[1]")])          # the outdoor plan was chosen instead
        self.assertTrue(results[("rcp", "3")])                   # recognised, kept as reference
        self.assertFalse(results[("rcp", "5")])                  # the real RCP was discarded as a render
        self.assertTrue(results[("must_keep", "1")])
        self.assertFalse(results[("must_keep", "5")])
        self.assertFalse(results[("must_keep", "9")])            # not in the packet at all
        self.assertFalse(results[("not_geometry", "2")])
        self.assertTrue(results[("not_geometry", "7")])           # a detail plan isn't a main plan
        self.assertEqual(report["totals"], {"passed": 4, "failed": 5, "facts": 9, "accuracy_percent": 44.4})
        self.assertTrue(report["confirmed"])
        self.assertEqual(report["main_plan_pages"], [2])

    def test_any_listed_geometry_page_counts_and_the_summary_adds_up(self):
        good = evaluation.score({"case_id": "a", "pages": {"geometry": [1, 2]}}, PACKET)
        bad = evaluation.score({"case_id": "b", "pages": {"geometry": [1], "must_keep": [5]}}, PACKET)
        self.assertTrue(good["facts"][0]["passed"])
        summary = evaluation.summarise([good, bad])
        self.assertEqual((summary["cases"], summary["confirmed_cases"], summary["passed"], summary["facts"]), (2, 0, 1, 3))
        self.assertEqual(summary["by_fact"]["geometry"], {"passed": 1, "facts": 2, "accuracy_percent": 50.0})
        markdown = evaluation.render_markdown([good, bad], summary)
        self.assertIn("(draft, not yet confirmed)", markdown)
        self.assertIn("1 of 3 facts right", markdown)


if __name__ == "__main__":
    unittest.main()
