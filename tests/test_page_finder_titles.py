#!/usr/bin/env python3
"""Page finder: printed view titles decide page roles first; a last-resort main plan when none is titled.

Cases are synthetic, shaped after pages in the 14 permitted architect sets (evaluations/page_roles).
"""

import unittest
from unittest.mock import patch

from pdf_pipeline import page_finder as pf

TOP_DOWN = {"likely_view": "top_down_plan", "plan_confidence": 0.85, "top_down_score": 0.9}
RENDER = {"likely_view": "render_or_photo", "plan_confidence": 0.2, "top_down_score": 0.3}
UNCERTAIN = {"likely_view": "uncertain", "plan_confidence": 0.6, "top_down_score": 0.7}


def analyse(pages, visual=None):
    """Run analyze_pages on synthetic page texts (no PDF needed)."""
    with patch.object(pf, "extract_text_pages", return_value=pages), patch.object(pf, "extract_pdf_title", return_value=""):
        packet = pf.analyze_pages("synthetic.pdf", visual_features=visual or {0: {}})
    roles = {}
    for group in ("kept_pages", "discarded_pages", "reference_pages", "primary_pages"):
        for row in packet[group]:
            roles[row["page"]] = {"group": group.removesuffix("_pages"), "type": row.get("type"), "role": row.get("plan_role")}
    return roles


class ViewTitleTests(unittest.TestCase):
    def kind(self, text):
        found = pf.view_title(text)
        return found and found[0]

    def test_the_view_title_beats_legend_headings(self):
        self.assertEqual(self.kind("SERVICE SCHEDULE\nwall legend\nPP             PROPOSED PLAN\n1:100"), "main_plan")
        self.assertEqual(self.kind("1     DIMENSION PLAN                    SCALE 1:100"), "main_plan")
        self.assertEqual(self.kind("1.Layout Plan"), "main_plan")
        self.assertEqual(self.kind("General Arrangement Plan  01.01"), "main_plan")

    def test_ceiling_plans_in_their_usual_wordings(self):
        for line in ("RC         REFLECTED CEILING PLAN", "PROPOSED REFLECTED CEILING SET OUT PLAN",
                     "REFLECTIVE CEILING PLAN", "A-131  PROPOSED RCP (GF)"):
            self.assertEqual(self.kind(line), "rcp", line)

    def test_notes_and_list_rows_are_not_titles(self):
        self.assertIsNone(pf.view_title("Refer to reflected ceiling plan for light positions"))
        self.assertIsNone(pf.view_title("Read in conjunction with the floor plan"))
        self.assertIsNone(pf.view_title("01.01  Layout Plan  03.18  Detail - Round Table"))

    def test_existing_context_and_part_plans_are_not_main_plans(self):
        self.assertEqual(self.kind("EP   EXISTING FLOOR PLAN"), "existing_plan")
        self.assertEqual(self.kind("OUTDOOR FLOOR PLAN"), "context_plan")
        self.assertEqual(self.kind("BAR LAYOUT PLAN"), "part_plan")      # "BAR" is a word, not a drawing code
        self.assertEqual(self.kind("1.Layout Plan Bathroom"), "part_plan")
        self.assertEqual(self.kind("FRONT COUNTER PLAN"), "part_plan")
        self.assertEqual(self.kind("1     STOREFRONT ELEVATION"), "shopfront")

    def test_drawing_lists_are_recognised_but_plan_legends_are_not(self):
        index = "\n".join(f"A-{n}    {title}             A" for n, title in [
            ("000", "COVER PAGE"), ("001", "GENERAL NOTES"), ("101", "PROPOSED GF"), ("131", "PROPOSED RCP (GF)"),
            ("200", "SECTIONS"), ("210", "INTERNAL ELEVATION 1")])
        self.assertEqual(pf.view_title(index), ("drawing_list", ""))
        mixed = "\n".join(["202  -  DIMENSION PLAN  A", "203  -  FLOOR FINISH PLAN  A", "204  -  REFLECTIVE CEILING PLAN  A",
                           "303  -  INTERNAL ELEVATION 3  A", "004  -  EQUIPMENT SCHEDULE  A"])
        self.assertEqual(pf.view_title(mixed), ("drawing_list", ""))
        legend = "\n".join(f"FL 0{n}    FLOOR TILES TYPE {n}" for n in range(1, 8)) + "\n1     DIMENSION PLAN"
        self.assertEqual(self.kind(legend), "main_plan")
        elevations = "\n".join(f"{n}  INTERNAL ELEVATION {n}" for n in range(1, 6))
        self.assertEqual(self.kind(elevations), "elevation")     # one kind repeated is not a drawing list


class PageRoleTests(unittest.TestCase):
    def test_titled_pages_get_their_role_before_the_word_anywhere_rules(self):
        roles = analyse(
            ["PP   PROPOSED PLAN\nloose furniture as shown\nsection A", "RC   REFLECTED CEILING PLAN",
             "1   STOREFRONT ELEVATION\nsignage by others", "OUTDOOR FLOOR PLAN"],
            {1: TOP_DOWN, 2: RENDER, 3: TOP_DOWN, 4: TOP_DOWN})
        self.assertEqual((roles[1]["group"], roles[1]["role"]), ("primary", "main_floor_plan"))  # not "furniture" or "detail"
        self.assertEqual((roles[2]["group"], roles[2]["type"]), ("primary", "reflected_ceiling_plan"))  # title beats "render"
        self.assertEqual((roles[3]["group"], roles[3]["type"]), ("reference", "elevation"))      # never discarded for "signage"
        self.assertEqual(roles[4]["role"], "site_plan")

    def test_a_schedule_page_is_a_table_unless_the_page_clearly_shows_a_plan(self):
        schedule_only = analyse(["LIGHTING SCHEDULE\nL1  downlight  12"], {1: UNCERTAIN})
        self.assertEqual((schedule_only[1]["group"], schedule_only[1]["type"]), ("reference", "equipment_or_fixture_schedule"))
        plan_with_legend = analyse(["LIGHTING SCHEDULE\nL1  downlight  12"], {1: TOP_DOWN})
        self.assertNotEqual(plan_with_legend[1]["type"], "equipment_or_fixture_schedule")

    def test_a_cover_with_a_drawing_list_is_not_a_ceiling_plan(self):
        index = "\n".join(f"A-{n}    {title}    A" for n, title in [
            ("000", "COVER PAGE"), ("101", "PROPOSED GF"), ("131", "PROPOSED RCP (GF)"), ("132", "PROPOSED RCP (MEZ)"),
            ("200", "SECTIONS"), ("410", "BAR LAYOUT PLAN")])
        roles = analyse([index], {1: TOP_DOWN})
        self.assertEqual((roles[1]["group"], roles[1]["type"]), ("discarded", "cover_or_drawing_list"))

    def test_with_no_titled_main_plan_the_most_plan_like_page_is_used(self):
        pages = ["", "SHS  Square Hollow Section\nFCL Finished Ceiling Level", "SECTIONS\n1   SECTION 1",
                 "FITOUT MECHANICAL SERVICES PIPEWORK PLAN"]
        visual = {1: {**TOP_DOWN, "top_down_score": 0.8}, 2: {**TOP_DOWN, "top_down_score": 0.87},
                  3: {**TOP_DOWN, "top_down_score": 0.95}, 4: {**TOP_DOWN, "top_down_score": 0.99}}
        with patch.object(pf, "guess_title", side_effect=lambda text: text.splitlines()[0] if text else ""):
            roles = analyse(pages, visual)
        mains = [page for page, row in roles.items() if row["group"] == "primary" and row["role"] == "main_floor_plan"]
        # Page 3 is titled as sections and page 4 is a services plan, though both score higher.
        self.assertEqual(mains, [2])

    def test_no_fallback_when_a_page_is_titled_as_the_main_plan(self):
        roles = analyse(["1   DIMENSION PLAN", "unlabelled sketch"], {1: UNCERTAIN, 2: {**TOP_DOWN, "top_down_score": 0.99}})
        mains = [page for page, row in roles.items() if row["group"] == "primary" and row["role"] == "main_floor_plan"]
        self.assertEqual(mains, [1])

    def test_no_fallback_onto_weak_or_non_plan_pages(self):
        roles = analyse(["unlabelled page"], {1: {"likely_view": "uncertain", "plan_confidence": 0.5, "top_down_score": 0.6}})
        self.assertNotEqual(roles[1].get("role"), "main_floor_plan")


if __name__ == "__main__":
    unittest.main()
