"""When nobody has chosen the weather location, the AI picks it from the site details, labelled; a choice always wins."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import weather_pick_service as weather

TABLE = {"source": "AIRAH (test)", "locations": {"Sydney": {"db": 32}, "Adelaide": {"db": 36}, "Melbourne": {"db": 34}}}


class FakeProvider:
    model = "fake/text"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def propose(self, prompt, image_paths=()):
        self.prompts.append(prompt)
        return self.reply, {}


def job(folder, setup=None, site=None):
    root = Path(folder)
    (root / "job_setup.json").write_text(json.dumps(setup or {}), encoding="utf-8")
    if site is not None:
        (root / "site_location_ai_determination.json").write_text(json.dumps(site), encoding="utf-8")
    return {"id": "w", "review_dir": folder, "name": "20260226 Butcher Buffet @ Melrose Park CONSTRUCTION REV B(2).pdf"}


class WeatherPickTests(unittest.TestCase):
    def test_the_ai_reads_the_site_clues_and_its_pick_is_used_and_labelled(self):
        with TemporaryDirectory() as folder, patch.object(weather.design_weather, "load_table", return_value=TABLE):
            project = job(folder, site={"site_text": "TENANCY MZ01,M38, MELROSE CENTRAL", "consultant_addresses": [
                {"text": "211-223 Pacific Hwy, North Sydney NSW 2060"}]})
            provider = FakeProvider({"location": "sydney", "site": "Melrose Park NSW", "why": "Melrose Park is in Sydney."})
            saved = weather.pick(None, project, provider=provider)
            self.assertEqual((saved["location"], saved["origin"]), ("Sydney", "ai_picked"))
            self.assertIn("site on the drawings: TENANCY MZ01,M38, MELROSE CENTRAL", provider.prompts[0])
            self.assertIn("job name: 20260226 Butcher Buffet @ Melrose Park", provider.prompts[0])
            self.assertIn("consultant's office (not the site): 211-223 Pacific Hwy", provider.prompts[0])
            location, note = weather.effective(folder)
            self.assertEqual(location, "Sydney")
            self.assertIn("picked by the AI from the site details (site read as Melrose Park NSW)", note)
            weather.pick(None, project, provider=provider)
            self.assertEqual(len(provider.prompts), 1)                       # same clues: no second call

    def test_a_listed_place_or_state_in_the_site_details_needs_no_ai_call(self):
        with TemporaryDirectory() as folder, patch.object(weather.design_weather, "load_table", return_value=TABLE):
            project = job(folder, setup={"address": "Shop 3, 22 King William St, Kent Town SA 5067"})
            provider = FakeProvider({"location": "Sydney"})
            self.assertEqual(weather.pick(None, project, provider=provider)["location"], "Adelaide")
            self.assertEqual(provider.prompts, [])

    def test_a_choice_on_the_project_tab_wins_and_an_unlisted_reply_is_not_used(self):
        with TemporaryDirectory() as folder, patch.object(weather.design_weather, "load_table", return_value=TABLE):
            project = job(folder, setup={"weather_location": "Melbourne"}, site={"site_text": "Melrose Central"})
            provider = FakeProvider({"location": "Sydney"})
            self.assertEqual(weather.pick(None, project, provider=provider), {})
            self.assertEqual(weather.effective(folder), ("Melbourne", ""))
            (Path(folder) / "job_setup.json").write_text("{}", encoding="utf-8")
            weather.pick(None, project, provider=FakeProvider({"location": "Gotham"}))
            self.assertEqual(weather.effective(folder), ("", ""))             # the labelled generic design day

    def test_without_the_handbook_table_nothing_is_picked(self):
        with TemporaryDirectory() as folder, patch.object(weather.design_weather, "load_table", return_value=None):
            provider = FakeProvider({"location": "Sydney"})
            self.assertEqual(weather.pick(None, job(folder, site={"site_text": "Melrose Central"}), provider=provider), {})
            self.assertEqual(provider.prompts, [])


if __name__ == "__main__":
    unittest.main()
