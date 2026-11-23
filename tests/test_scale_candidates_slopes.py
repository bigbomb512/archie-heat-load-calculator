"""A slope printed as a ratio is not the sheet's scale; a scale printed beside "Scale" wins (Casa Nova M110, 2026-11-23)."""
import unittest

from ai.dimension_wall_matcher import page_mm_per_px, page_scale_denominator
from pdf_pipeline.spatial_ocr import scale_candidates


def words(text):
    return [{"text": word} for word in text.split()]


class ScaleCandidateTests(unittest.TestCase):
    def test_a_duct_grade_is_not_a_scale(self):
        found = scale_candidates(words("KITCHEN EXHAUST DUCT SHALL BE GRADED (1:200) IN DIRECTION OF AIRFLOW "
                                       "Scale @ A3 1:80"), [])
        self.assertEqual([row["text"] for row in found], ["1:80"])
        self.assertEqual(page_scale_denominator(found), 80)
        # 1:80 at the 180 dpi page image: 11.29 mm per pixel, not the 1:200's 28.2.
        self.assertAlmostEqual(page_mm_per_px(found, render_dpi=180), 25.4 / 180 * 80)

    def test_falls_and_slopes_are_skipped(self):
        for note in ("FALL 1:100 TO DRAIN", "FALLS TO WASTE 1:50", "ROOF PITCHED AT 1:20", "SLOPE 1:100"):
            with self.subTest(note=note):
                self.assertEqual(scale_candidates(words(note), []), [])

    def test_a_labelled_scale_wins_over_a_larger_bare_ratio(self):
        rows = [{"text": "1:200"}, {"text": "1:80", "labelled": True}, {"text": "1:20"}]
        self.assertEqual(page_scale_denominator(rows), 80)

    def test_without_labels_the_largest_scale_still_governs(self):
        self.assertEqual(page_scale_denominator([{"text": "1:5"}, {"text": "1:100"}, {"text": "1:20"}]), 100)


if __name__ == "__main__":
    unittest.main()
