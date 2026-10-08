import unittest

from ai.page_inventory_scoring import score_information, summarise_information, render_markdown


def sheet(case_id, status, information):
    return {"case_id": case_id, "pages": {}, "information_status": status, "information": information}


def reading(*kinds):
    return {"information": [{"kind": kind, "what": kind, "evidence": "visible"} for kind in kinds]}


class PageInventoryScoringTests(unittest.TestCase):
    def test_perfect_information_lists_have_full_precision_and_recall(self):
        key = sheet("perfect", "confirmed_blind", {"1": ["room_geometry"], "2": []})
        report = score_information(key, {1: reading("room_geometry"), 2: reading()}, 2)
        self.assertEqual(report["overall"]["recall"], 1.0)
        self.assertEqual(report["overall"]["precision"], 1.0)
        self.assertEqual(report["worst_pages"], [])

    def test_missed_kind_reduces_recall_and_names_the_page(self):
        key = sheet("miss", "confirmed_blind", {"1": ["room_geometry", "ceiling_height"]})
        report = score_information(key, {1: reading("room_geometry")}, 1)
        self.assertEqual(report["by_kind"]["ceiling_height"]["false_negative"], 1)
        self.assertEqual(report["overall"]["recall"], 0.5)
        self.assertEqual(report["worst_pages"], [{"page": 1, "unread": False,
                                                   "missed": ["ceiling_height"], "added": []}])

    def test_extra_kind_reduces_precision_and_names_the_page(self):
        key = sheet("extra", "confirmed_blind", {"1": ["room_geometry"]})
        report = score_information(key, {1: reading("room_geometry", "lighting")}, 1)
        self.assertEqual(report["by_kind"]["lighting"]["false_positive"], 1)
        self.assertEqual(report["overall"]["precision"], 0.5)
        self.assertEqual(report["worst_pages"][0]["added"], ["lighting"])

    def test_unread_page_is_a_miss_not_a_skipped_page(self):
        key = sheet("unread", "confirmed_blind", {"1": ["room_geometry"]})
        report = score_information(key, {1: None}, 1)
        self.assertEqual(report["by_kind"]["room_geometry"]["false_negative"], 1)
        self.assertTrue(report["worst_pages"][0]["unread"])

    def test_summary_separates_blind_and_draft_confirmations(self):
        blind = score_information(sheet("blind", "confirmed_blind", {"1": ["lighting"]}),
                                  {1: reading("lighting")}, 1)
        drafted = score_information(sheet("draft", "confirmed_from_draft", {"1": ["lighting"]}),
                                    {1: reading("lighting")}, 1)
        unconfirmed = score_information(sheet("drafted", "drafted_from_ai", {"1": ["lighting"]}),
                                         {1: reading("lighting")}, 1)
        summary = summarise_information([blind, drafted, unconfirmed])
        self.assertEqual(summary["confirmed_blind_sets"], 1)
        self.assertEqual(summary["confirmed_from_draft_sets"], 1)
        self.assertEqual(summary["unconfirmed_sets"], 1)
        markdown = render_markdown([blind, drafted, unconfirmed], summary)
        self.assertIn("likely overstates accuracy", markdown)
        self.assertIn("not scored (drafted_from_ai)", markdown)


if __name__ == "__main__":
    unittest.main()
