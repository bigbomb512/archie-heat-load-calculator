import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("features", Path(__file__).resolve().parents[1] / "tools" / "building_features.py")
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)

FILLER = " ".join(["dimension"] * 30)          # enough words that a page isn't taken as scanned
TITLE = "Smith Studio architects Dwelling A " + FILLER


class DetectTests(unittest.TestCase):
    def test_a_feature_is_reported_with_the_pages_that_name_it(self):
        result = features.detect([TITLE + " GROUND FLOOR PLAN Living VOID over", TITLE + " FIRST FLOOR PLAN Bed 1", TITLE])
        void = result["features"]["void or double-height space"]
        self.assertEqual(void["pages"], [1])
        self.assertTrue(void["printed"])
        self.assertEqual(result["level_names"], ["first floor", "ground floor"])

    def test_text_on_every_page_is_flagged_not_counted(self):
        result = features.detect([TITLE, TITLE, TITLE])
        dwelling = result["features"]["second dwelling (granny flat, dual occupancy)"]
        self.assertTrue(dwelling["every_page"])
        self.assertFalse(dwelling["printed"])
        self.assertIn("every page: check", features.table({"house": result}))

    def test_standard_notes_are_not_features(self):
        pages = [FILLER + " demolition of this building will require care; existing trees to be retained; LOHD lift-off hinge door",
                 FILLER, FILLER]
        result = features.detect(pages)
        self.assertFalse(result["features"]["existing building or demolition plans (alterations, or knock-down)"]["printed"])
        self.assertFalse(result["features"]["lift"]["printed"])
        self.assertTrue(features.detect([FILLER + " A104 Demolition Ground Floor Plan", FILLER, FILLER])
                        ["features"]["existing building or demolition plans (alterations, or knock-down)"]["printed"])

    def test_a_page_without_a_text_layer_counts_as_scanned(self):
        result = features.detect(["", "", FILLER])
        self.assertEqual(result["scanned_pages"], [1, 2])
        self.assertEqual(result["drawing"], "scanned")


if __name__ == "__main__":
    unittest.main()
