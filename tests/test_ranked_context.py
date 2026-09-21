import unittest

from ai.vision_extraction import MAX_MAIN_CONTEXT_PAGES, build_ranked_context, estimate, select_page_groups, validate_settings


def role(page, kind, score, selection="primary_context", identity="confirmed"):
    relevance = {key: 0.0 for key in (
        "room_geometry", "openings_windows", "vertical_heights", "ceiling_lighting",
        "equipment", "occupancy_schedules", "construction_boundaries", "3d_cross_check",
    )}
    relevance[kind] = score
    return {
        "page": page, "proposed_role": {
            "room_geometry": "main_floor_plan", "openings_windows": "opening_elevation",
            "vertical_heights": "section", "ceiling_lighting": "services_or_lighting_plan",
            "equipment": "equipment_schedule", "occupancy_schedules": "schedule",
            "construction_boundaries": "construction_or_detail", "3d_cross_check": "3d_render",
        }[kind], "selection": selection, "identity": {"status": identity},
        "relevance": relevance, "selection_reasons": [kind], "related_pages": [],
    }


class RankedContextTests(unittest.TestCase):
    def setUp(self):
        self.pages = [{"page": page, "drawing_number": f"A-{page:03d}", "title": "Evidence",
                       "structured_content": {"markdown": ""}} for page in range(1, 31)]
        self.roles = [role(page, kind, 0.85 if page < 20 else 0.55)
                      for page, kind in enumerate((
                          "room_geometry", "openings_windows", "vertical_heights", "ceiling_lighting",
                          "equipment", "occupancy_schedules", "construction_boundaries", "3d_cross_check",
                      ), start=1)]
        self.roles.extend(role(page, "room_geometry", 0.55) for page in range(9, 31))

    def test_context_is_bounded_and_covers_available_categories(self):
        context = build_ranked_context({"drawing_set": {"pages": self.pages}}, {"page_roles": self.roles})
        self.assertLessEqual(len(context["main_context_pages"]), MAX_MAIN_CONTEXT_PAGES)
        for category in ("room_geometry", "openings_windows", "vertical_heights", "ceiling_lighting",
                         "equipment", "occupancy_schedules", "construction_boundaries", "3d_cross_check"):
            self.assertTrue(context["category_coverage"].get(category), category)
        self.assertLessEqual(len(context["cross_check_pages"]), 3)

    def test_ambiguous_pages_are_appendix_only(self):
        roles = list(self.roles)
        roles[0] = role(1, "room_geometry", 0.95, identity="ambiguous")
        context = build_ranked_context({"drawing_set": {"pages": self.pages}}, {"page_roles": roles})
        self.assertIn(1, context["exception_pages"])
        self.assertNotIn(1, context["main_context_pages"])

    def test_reordering_pages_and_roles_does_not_change_selection(self):
        first = build_ranked_context({"drawing_set": {"pages": self.pages}}, {"page_roles": self.roles})
        second = build_ranked_context({"drawing_set": {"pages": list(reversed(self.pages))}}, {"page_roles": list(reversed(self.roles))})
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["main_context_pages"], second["main_context_pages"])

    def test_groups_use_only_main_context_pages(self):
        pages = self.pages[:2]
        coverage = {"page_roles": [role(1, "room_geometry", 0.9), role(2, "openings_windows", 0.9, identity="ambiguous")]}
        groups = select_page_groups({"drawing_set": {"pages": pages}}, coverage)
        selected = {page["page"] for group in groups for page in group["pages"]}
        self.assertIn(1, selected)
        self.assertNotIn(2, selected)

    def test_estimate_reports_only_main_context_pages(self):
        pages = self.pages[:2]
        coverage = {"page_roles": [role(1, "room_geometry", 0.9), role(2, "openings_windows", 0.9, identity="ambiguous")]}
        groups = select_page_groups({"drawing_set": {"pages": pages}}, coverage)
        summary = estimate(validate_settings({"owner_opt_in": True, "max_budget_aud": 10}), groups, 1.0)
        self.assertEqual(summary["page_count"], 1)


if __name__ == "__main__":
    unittest.main()
