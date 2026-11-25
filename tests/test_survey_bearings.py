"""North from a drawing set's boundary bearings (ai/survey_bearings.py) and its use with north arrows
(ai/autonomous_tasks.true_north), and the north-arrow crops (north_crop_boxes)."""

import json
import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai import autonomous_tasks, survey_bearings
from tests.test_page_pool import write_text_pdf


def label(bearing, direction, page=1):
    return {"page": page, "bearing_deg": bearing, "text_direction_deg": direction, "text": "", "x": 0, "y": 0}


# Set 18's site plan: sides 137°15' and 317°15' written left to right, the rear 227°15' written downwards... and a
# sloping 28°36' boundary; sheet-up is 47.25° from north (worked by hand, 2026-11-08).
SET18 = [label(137.25, 90.0), label(317.25, 90.0), label(227.25, 180.0), label(28.6, 161.0), label(137.25, 90.0, page=9)]


class AxisTests(unittest.TestCase):
    def test_labels_along_different_boundaries_agree_on_the_sheet_orientation(self):
        result = survey_bearings.axis(SET18)
        self.assertAlmostEqual(result["axis_deg"], 47.34, places=1)
        self.assertEqual((result["labels_agreeing"], result["labels_distinct"]), (4, 4))   # the repeat on page 9 counts once

    def test_a_stray_label_is_outvoted_and_one_label_alone_sets_nothing(self):
        result = survey_bearings.axis([label(134.997, 188.9), label(126.097, 180.0), label(227.363, 68.2)])   # set 15
        self.assertAlmostEqual(result["axis_deg"], 126.1, places=1)
        self.assertEqual(result["labels_agreeing"], 2)
        self.assertIsNone(survey_bearings.axis([label(137.25, 90.0)]))
        self.assertIsNone(survey_bearings.axis([label(10.0, 90.0), label(80.0, 90.0)]))      # two boundaries that disagree

    def test_an_axis_near_zero_averages_across_the_wrap(self):
        result = survey_bearings.axis([label(90.2, 90.0), label(179.8, 180.0)])   # 0.2° and 179.8°
        self.assertLess(min(result["axis_deg"], 180 - result["axis_deg"]), 0.01)

    def test_the_bearing_pattern_reads_the_ways_drawings_print_it(self):
        texts = ["137°15'00\"", "126?05'48\"", "205°   05'   25\"", "313°54'", "28º36′"]
        self.assertEqual([survey_bearings.BEARING.search(text) is not None for text in texts], [True] * 5)
        self.assertIsNone(survey_bearings.BEARING.search("22.5° roof pitch"))

    def test_refine_picks_the_axis_direction_nearest_the_arrow(self):
        self.assertEqual(survey_bearings.refine(45.0, 47.25, 10), (47.25, 2.25))
        self.assertEqual(survey_bearings.refine(230.0, 47.25, 10), (227.25, 2.75))
        self.assertEqual(survey_bearings.refine(70.0, 47.25, 10)[0], None)


class TrueNorthTests(unittest.TestCase):
    ARROW = {"found": True, "plan_up_azimuth_deg": 45.0}
    SURVEY = {"axis_deg": 43.85}

    def test_a_project_north_arrow_counts_only_when_the_bearings_confirm_it(self):
        self.assertIsNone(autonomous_tasks.true_north({**self.ARROW, "label": "project"})[0])
        bearing, basis = autonomous_tasks.true_north({**self.ARROW, "label": "project"}, None, self.SURVEY)
        self.assertEqual(bearing, 43.85)
        self.assertIn("confirmed by the survey", basis["reason"])
        bearing, basis = autonomous_tasks.true_north({**self.ARROW, "plan_up_azimuth_deg": 80.0, "label": "project"}, None, self.SURVEY)
        self.assertIsNone(bearing)
        self.assertIn("not used", basis["reason"])

    def test_an_arrow_takes_the_bearings_exact_angle_unless_they_disagree(self):
        bearing, basis = autonomous_tasks.true_north({**self.ARROW, "label": "unlabelled"}, None, self.SURVEY)
        self.assertEqual(bearing, 43.85)
        self.assertIn("Refined to the survey", basis["reason"])
        flipped = autonomous_tasks.true_north({**self.ARROW, "plan_up_azimuth_deg": 220.0, "label": "true"}, None, self.SURVEY)
        self.assertEqual(flipped[0], 223.85)          # the arrow says which way round the axis is
        far, basis = autonomous_tasks.true_north({**self.ARROW, "plan_up_azimuth_deg": 100.0, "label": "true"}, None, self.SURVEY)
        self.assertEqual(far, 100.0)
        self.assertIn("arrow is used", basis["reason"])

    def test_bearings_alone_do_not_set_north(self):
        self.assertIsNone(autonomous_tasks.true_north({"found": False}, None, self.SURVEY)[0])


class NorthCropTests(unittest.TestCase):
    def test_a_printed_north_gets_its_own_crop_then_corners_without_one(self):
        boxes = autonomous_tasks.north_crop_boxes(1000, 700, [(0.48, 0.92)])
        self.assertEqual(len(boxes), 3)
        left, top, right, bottom = boxes[0]
        self.assertTrue(left <= 480 <= right and top <= 644 <= bottom)
        self.assertNotIn((0, 546, 620, 700), boxes)          # the bottom band already holds the word's crop

    def test_without_a_printed_north_the_bottom_band_is_checked(self):
        boxes = autonomous_tasks.north_crop_boxes(1000, 700)
        self.assertEqual(boxes, [(620, 434, 1000, 700), (0, 546, 620, 700), (620, 0, 1000, 266)])



class LabelReadingTests(unittest.TestCase):
    def test_labels_read_in_worker_processes_match_one_page_at_a_time(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "survey.pdf"
            write_text_pdf(source, ["SITE 137?15'00\" BOUNDARY", "NO BEARINGS HERE", "REAR 227?15' FENCE 28?36'"])
            with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "1"}):
                sequential = survey_bearings.labels(source)
                only_third = survey_bearings.labels(source, pages={3})
            with patch.dict(os.environ, {"ARCHIE_PAGE_WORKERS": "3"}):
                parallel = survey_bearings.labels(source)
        self.assertEqual([(row["page"], row["bearing_deg"]) for row in sequential],
                         [(1, 137.25), (3, 227.25), (3, 28.6)])
        self.assertTrue(all(row["text_direction_deg"] == 90.0 for row in sequential))
        self.assertEqual(parallel, sequential)
        self.assertEqual(only_third, sequential[1:])



class WarmSurveyAxisTests(unittest.TestCase):
    def setUp(self):
        from backend import autonomous_tasks_service, survey_axis_service
        self.service, self.survey = autonomous_tasks_service, survey_axis_service
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "set.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 stand-in")
        (self.root / "ai_input.json").write_text(json.dumps({"source_pdf": str(self.pdf)}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_a_warmed_job_answers_the_north_check_without_reading_the_pdf_again(self):
        with patch.object(survey_bearings, "labels", return_value=SET18) as read:
            self.survey.warm(self.root, self.pdf)
            axis = self.service._survey_axis(self.root)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(axis, survey_bearings.axis(SET18))

    def test_the_check_waits_for_a_reading_under_way_instead_of_repeating_it(self):
        started, release = threading.Event(), threading.Event()

        def slow_labels(pdf, pages=None):
            started.set()
            release.wait(5)
            return SET18
        with patch.object(survey_bearings, "labels", side_effect=slow_labels) as read:
            warm = threading.Thread(target=self.survey.warm, args=(self.root, self.pdf))
            warm.start()
            started.wait(5)
            answers = []
            check = threading.Thread(target=lambda: answers.append(self.service._survey_axis(self.root)))
            check.start()
            release.set()
            warm.join(5)
            check.join(5)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(answers, [survey_bearings.axis(SET18)])


if __name__ == "__main__":
    unittest.main()
