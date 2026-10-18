"""The per-step thinking setting, and the model comparison tool's spending cap and equipment scoring (no AI calls)."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import ai_provider
import tools.compare_vision_models as compare


class ThinkingSettingTests(unittest.TestCase):
    def body(self, step="", effort=None, env=None):
        with patch.dict(os.environ, env or {}, clear=False):
            for name in ("ARCHIE_OPENROUTER_REASONING", "ARCHIE_OPENROUTER_REASONING_DRAWINGS"):
                if name not in (env or {}):
                    os.environ.pop(name, None)
            provider = ai_provider.OpenRouterProvider(api_key="or-test")
            provider.step = step
            if effort:
                provider.reasoning_effort = effort
            return provider.extra_body()

    def test_drawings_and_equipment_think_at_low_effort_by_default_and_it_can_be_changed(self):
        self.assertEqual(self.body("DRAWINGS")["reasoning"], {"effort": "low"})
        self.assertEqual(self.body("PASS2")["reasoning"], {"effort": "low"})
        self.assertNotIn("reasoning", self.body("PASS1"))
        self.assertNotIn("reasoning", self.body("SKILLS"))
        self.assertEqual(self.body("DRAWINGS", env={"ARCHIE_OPENROUTER_REASONING_DRAWINGS": "high"})["reasoning"],
                         {"effort": "high"})
        # "default" leaves the thinking to the model, as before 2026-10-18.
        self.assertNotIn("reasoning", self.body("DRAWINGS", env={"ARCHIE_OPENROUTER_REASONING_DRAWINGS": "default"}))

    def test_the_thinking_effort_can_be_set_per_step_or_per_provider(self):
        self.assertEqual(self.body("DRAWINGS", env={"ARCHIE_OPENROUTER_REASONING_DRAWINGS": "medium"})["reasoning"], {"effort": "medium"})
        self.assertNotIn("reasoning", self.body("PASS1", env={"ARCHIE_OPENROUTER_REASONING_DRAWINGS": "medium"}))
        self.assertEqual(self.body("PASS1", env={"ARCHIE_OPENROUTER_REASONING": "high"})["reasoning"], {"effort": "high"})
        self.assertEqual(self.body("DRAWINGS", effort="minimal", env={"ARCHIE_OPENROUTER_REASONING_DRAWINGS": "low"})["reasoning"],
                         {"effort": "minimal"})


class SpendingCapTests(unittest.TestCase):
    def test_a_call_is_refused_once_the_sandbox_has_spent_the_cap(self):
        calls = []

        class Fake:
            model = "x"

            def propose(self, prompt, image_paths=()):
                calls.append(prompt)
                return {}, {}

        with tempfile.TemporaryDirectory() as folder, patch.object(compare, "SANDBOX", Path(folder)):
            job = Path(folder) / "jobs" / "caseA-qwen-drawings"
            job.mkdir(parents=True)
            log = job / "ai_usage.jsonl"
            log.write_text(json.dumps({"cost_usd": 1.0}) + "\n")
            compare.Budgeted(Fake(), cap=3.0).propose("first")
            log.write_text(json.dumps({"cost_usd": 1.5}) + "\n" + json.dumps({"cost_usd": 1.45}) + "\n")
            with self.assertRaises(ai_provider.UsageLimitReached):
                compare.Budgeted(Fake(), cap=3.0).propose("second")     # 2.95 spent: within the margin of the cap
        self.assertEqual(calls, ["first"])


class EquipmentScoreTests(unittest.TestCase):
    def test_codes_names_and_uncoded_items_are_counted(self):
        key = {"E01": "POS", "E02": "UB FRIDGE", "E03": "S/S BENCH"}
        findings = [{"value": {"code": "E01", "name": "POS terminal"}}, {"value": {"code": "e02", "name": "Freezer"}},
                    {"value": {"code": "", "name": "Hand basin"}}]
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "page_extraction.json").write_text(json.dumps({"equipment_appliances": {"findings": findings}}))
            result = compare.score_equipment(Path(folder), key)
        self.assertEqual(result, {"schedule_items": 3, "codes_found": 2, "names_match": 1,
                                  "items_without_a_schedule_code": 1, "items_total": 3})


if __name__ == "__main__":
    unittest.main()
