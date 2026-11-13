"""Two faults found on set 22008 (139a Burns Road, 13 November 2026).

1. Its drawings print "m²" with the ² as a word of its own. "²".isdigit() is True but int("²") fails, so preparing the
   drawing set stopped with "invalid literal for int() with base 10: '²'". Numbers read from drawing text use
   isdecimal(), which int() can always read.
2. A drawing question sent just before the laptop's lid was closed waited 25 minutes of clock time against a 5-minute
   limit: the monotonic clock stops while a Mac sleeps. The limit is now also kept on the wall clock.
"""
import threading
import time
import unittest
from unittest.mock import patch

from ai import page_extraction, room_use_resolution, vector_geometry
from backend import ai_provider


class SuperscriptTwoTests(unittest.TestCase):
    def test_a_lone_superscript_two_is_not_a_dimension(self):
        self.assertIsNone(vector_geometry.dimension_value("²"))
        self.assertIsNone(vector_geometry.dimension_value("12²"))
        self.assertEqual(vector_geometry.dimension_value("3,600"), 3600)

    def test_matching_two_plans_by_their_dimensions_skips_it(self):
        words = [{"text": "²", "x0": 1, "top": 1}, {"text": "(m²)", "x0": 2, "top": 2}]
        room_use_resolution.plan_text_translation(words, words)     # no error

    def test_an_equipment_quantity_of_superscript_two_is_refused_as_a_quantity(self):
        with self.assertRaisesRegex(ValueError, "Quantity"):
            page_extraction.validate_equipment_item({"name": "FRIDGE", "quantity": "²"})
        self.assertEqual(page_extraction.validate_equipment_item({"name": "FRIDGE", "quantity": "2"})["quantity"], 2)


class SiteLabelTests(unittest.TestCase):
    def names(self, text):
        from ai import site_location_resolution as site
        return [match.group(1).strip() for match in site.SITE_LABEL_PATTERN.finditer(text)]

    def test_a_basix_sentence_is_not_a_site_name(self):
        self.assertEqual(self.names("Ventilation in the development: At least 1 Bathroom: individual fan, not ducted"), [])

    def test_title_block_labels_still_name_the_site(self):
        self.assertEqual(self.names("PROJECT: 139A BURNS ROAD TURRAMURRA"), ["139A BURNS ROAD TURRAMURRA"])
        self.assertEqual(self.names("Project Name - Kids Cafe"), ["Kids Cafe"])
        self.assertEqual(self.names("SITE: Shop 12, Melrose Central"), ["Shop 12, Melrose Central"])


class _Provider(ai_provider._HttpProvider):
    name = "openrouter"
    key_env = "TEST_PROVIDER_KEY"


class SleepTests(unittest.TestCase):
    def test_a_request_across_a_sleep_is_given_up_when_the_computer_wakes(self):
        provider = _Provider(api_key="test", timeout=600)
        provider.deadline_s = 300
        release = threading.Event()
        self.addCleanup(release.set)
        clock = {"jump": 0.0}
        real_time = time.time

        def hang(*_args):
            release.wait(30)
            return b"{}"

        started = real_time()
        with patch.object(provider, "_post_once", side_effect=hang), \
                patch("backend.ai_provider.time.time", side_effect=lambda: real_time() + clock["jump"]):
            timer = threading.Timer(0.3, lambda: clock.update(jump=25 * 60))   # the lid closes for 25 minutes
            timer.start()
            with self.assertRaises(ai_provider.ProviderTimeout):
                provider._post_within("https://example.invalid", {}, {}, time.monotonic() + 300)
        self.assertLess(real_time() - started, 5)       # given up on waking, not 5 more minutes later

    def test_a_reply_inside_the_limit_still_comes_back(self):
        provider = _Provider(api_key="test", timeout=600)
        with patch.object(provider, "_post_once", return_value={"ok": True}):
            self.assertEqual(provider._post_within("https://example.invalid", {}, {}, time.monotonic() + 300), {"ok": True})


if __name__ == "__main__":
    unittest.main()
