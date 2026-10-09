#!/usr/bin/env python3
"""The swappable AI provider (Codex CLI, OpenAI API, Anthropic API) and the per-job usage log. No network is used."""

import base64
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from backend import ai_provider

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def opener(reply, captured):
    def open_(request, timeout=None):
        captured.append({"url": request.full_url, "headers": dict(request.header_items()), "body": json.loads(request.data)})
        return FakeResponse(json.dumps(reply).encode())
    return open_


def failing(code, body):
    def open_(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "error", {}, io.BytesIO(body.encode()))
    return open_


class ChoiceTests(unittest.TestCase):
    def test_the_provider_is_a_setting_and_a_wrong_one_is_refused(self):
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": ""}):
            self.assertEqual(ai_provider.chosen(), "codex_cli")
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "OpenAI"}):
            self.assertEqual(ai_provider.chosen(), "openai")
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "gemini"}):
            with self.assertRaisesRegex(ai_provider.ProviderUnavailable, "must be one of"):
                ai_provider.chosen()
            self.assertFalse(ai_provider.configured())
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}):
            self.assertFalse(ai_provider.configured())
            with self.assertRaisesRegex(ai_provider.ProviderUnavailable, "ANTHROPIC_API_KEY"):
                ai_provider.get()
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k", "ARCHIE_AI_MODEL": ""}):
            self.assertTrue(ai_provider.configured())
            self.assertEqual(ai_provider.get().model, "claude-sonnet-5-5")


class HttpProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.image = Path(self.temp.name) / "page.png"
        self.image.write_bytes(PNG)

    def tearDown(self):
        self.temp.cleanup()

    def test_openai_sends_the_prompt_and_page_image_and_reads_json_and_tokens(self):
        captured = []
        reply = {"model": "gpt-5", "choices": [{"message": {"content": '{"page_type": "schedule"}'}}],
                 "usage": {"prompt_tokens": 1200, "completion_tokens": 80}}
        provider = ai_provider.OpenAIProvider(api_key="sk-test", model="gpt-5", opener=opener(reply, captured))
        result, raw = provider.propose("Read this page.", [self.image])
        self.assertEqual(result, {"page_type": "schedule"})
        self.assertEqual(raw["usage"], {"input_tokens": 1200, "output_tokens": 80})
        sent = captured[0]
        self.assertEqual(sent["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer sk-test")
        content = sent["body"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Read this page."})
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})

    def test_deepseek_sends_the_page_image_to_its_endpoint_and_reads_cached_tokens(self):
        captured = []
        reply = {"model": "deepseek-flash", "choices": [{"message": {"content": '{"page_type": "schedule"}'}}],
                 "usage": {"prompt_tokens": 9000, "completion_tokens": 700, "prompt_cache_hit_tokens": 4000,
                           "prompt_cache_miss_tokens": 5000}}
        with patch.dict(os.environ, {"ARCHIE_AI_MODEL": "", "ARCHIE_DEEPSEEK_BASE_URL": ""}):
            os.environ.pop("ARCHIE_DEEPSEEK_BASE_URL")
            provider = ai_provider.DeepSeekProvider(api_key="ds-test", opener=opener(reply, captured))
        self.assertEqual(provider.model, "deepseek-flash")                     # the model that reads images
        result, raw = provider.propose("Read this page.", [self.image])
        self.assertEqual(result, {"page_type": "schedule"})
        self.assertEqual(raw["usage"], {"input_tokens": 9000, "output_tokens": 700, "cached_input_tokens": 4000})
        sent = captured[0]
        self.assertEqual(sent["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer ds-test")
        self.assertTrue(sent["body"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual((sent["body"]["response_format"], sent["body"]["max_tokens"]), ({"type": "json_object"}, ai_provider.MAX_OUTPUT_TOKENS))
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "ds-test"}):
            self.assertEqual(ai_provider.chosen(), "deepseek")
            self.assertTrue(ai_provider.configured())
        with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""}):
            self.assertFalse(ai_provider.configured())
            with self.assertRaises(ai_provider.ProviderUnavailable):
                ai_provider.get()

    def test_anthropic_sends_images_first_and_reads_a_fenced_json_reply(self):
        captured = []
        reply = {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "```json\n{\"items\": []}\n```"}],
                 "usage": {"input_tokens": 900, "output_tokens": 20}}
        provider = ai_provider.AnthropicProvider(api_key="a-test", opener=opener(reply, captured))
        result, raw = provider.propose("List the equipment.", [self.image])
        self.assertEqual(result, {"items": []})
        self.assertEqual((raw["usage"]["input_tokens"], raw["model"]), (900, "claude-sonnet-5-5"))
        sent = captured[0]
        self.assertEqual((sent["headers"]["X-api-key"], sent["headers"]["Anthropic-version"]), ("a-test", "2023-06-01"))
        content = sent["body"]["messages"][0]["content"]
        self.assertEqual((content[0]["type"], content[0]["source"]["media_type"], content[-1]["text"]), ("image", "image/png", "List the equipment."))

    def test_quota_errors_are_usage_limits_rate_limits_and_other_errors_are_plain_failures(self):
        provider = ai_provider.OpenAIProvider(api_key="k", opener=failing(429, '{"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}'))
        with self.assertRaises(ai_provider.UsageLimitReached):
            provider.propose("x")
        provider = ai_provider.OpenAIProvider(api_key="k", opener=failing(429, '{"error": "slow down"}'))
        with self.assertRaisesRegex(RuntimeError, "rate-limiting"):
            provider.propose("x")
        provider = ai_provider.AnthropicProvider(api_key="k", opener=failing(400, '{"error": {"message": "Your credit balance is too low"}}'))
        with self.assertRaises(ai_provider.UsageLimitReached):
            provider.propose("x")
        provider = ai_provider.AnthropicProvider(api_key="k", opener=failing(500, "server exploded"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            provider.propose("x")
        provider = ai_provider.OpenAIProvider(api_key="k", opener=opener({"choices": [{"message": {"content": "no json here"}}]}, []))
        with self.assertRaisesRegex(RuntimeError, "not valid JSON"):
            provider.propose("x")


class CodexCliTests(unittest.TestCase):
    def setUp(self):
        # The provider reads the user's Codex config for the model; tests use an empty Codex home instead.
        self.home = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"CODEX_HOME": self.home.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.home.cleanup()

    def fake_cli(self, folder, reply, exit_code=0, stderr=""):
        script = Path(folder) / "codex"
        script.write_text("#!/bin/sh\n"
                          'out=""; while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out="$2"; fi; shift; done\n'
                          f"cat > /dev/null\nprintf '%s' '{reply}' > \"$out\"\n"
                          f"echo 'tokens used'; echo '6,693'\necho '{stderr}' >&2\nexit {exit_code}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return str(script)

    def test_the_cli_reply_and_its_token_count_are_read(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = ai_provider.CodexCliProvider(executable=self.fake_cli(folder, '{"page_type": "elevation"}'), model="")
            result, raw = provider.propose("Read this page.")
        self.assertEqual((result, raw["usage"], raw["model"]), ({"page_type": "elevation"}, {"total_tokens": 6693}, "codex default"))

    def test_the_json_events_give_the_token_split(self):
        events = "\n".join(['{"type":"thread.started"}', '{"type":"item.completed","item":{"type":"agent_message","text":"{}"}}',
                            '{"type":"turn.completed","usage":{"input_tokens":20000,"cached_input_tokens":15000,'
                            '"output_tokens":900,"reasoning_output_tokens":300}}'])
        self.assertEqual(ai_provider.codex_event_usage(events), {"input_tokens": 20000, "cached_input_tokens": 15000,
                         "output_tokens": 900, "reasoning_output_tokens": 300, "total_tokens": 20900})
        self.assertEqual(ai_provider.codex_event_usage("not json\ntokens used 5"), {})
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "codex"
            script.write_text("#!/bin/sh\n"
                              'out=""; while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out="$2"; fi; shift; done\n'
                              "cat > /dev/null\nprintf '{}' > \"$out\"\n"
                              "echo '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":10,\"cached_input_tokens\":4,\"output_tokens\":2}}'\n")
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            log = ai_provider.recorded(ai_provider.CodexCliProvider(executable=str(script), model=""), folder, "skill:x")
            log.propose("x")
            summary = ai_provider.usage_summary(folder)["total"]
        self.assertEqual((summary["tokens"], summary["input_tokens"], summary["cached_input_tokens"], summary["output_tokens"]),
                         (12, 10, 4, 2))

    def test_a_reply_ending_a_bracket_short_is_closed_and_other_broken_replies_stay_invalid(self):
        self.assertEqual(ai_provider.parse_json_reply('{"pages": {"2": {"items": [{"what": "a}b"}]}'),
                         {"pages": {"2": {"items": [{"what": "a}b"}]}}})          # a brace inside a string is text
        for broken in ('{"a": [1, 2}', '{"a": "open', '{"a": 1}}'):
            with self.assertRaises(RuntimeError):
                ai_provider.parse_json_reply(broken)

    def test_a_long_reply_is_stored_whole_so_a_retry_can_recheck_it(self):
        long_reply = json.dumps({"rows": ["x" * 50 for _ in range(1000)]})               # about 55,000 characters
        with tempfile.TemporaryDirectory() as folder:
            provider = ai_provider.CodexCliProvider(executable=self.fake_cli(folder, long_reply), model="")
            result, raw = provider.propose("x")
        self.assertEqual(len(result["rows"]), 1000)
        self.assertEqual(json.loads(raw["reply_text"]), result)

    def test_the_cli_runs_outside_the_repository_so_its_agents_file_is_not_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / "codex"
            script.write_text("#!/bin/sh\n"
                              'out=""; while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out="$2"; fi; shift; done\n'
                              'cat > /dev/null\nprintf \'{"cwd": "%s", "agents": "%s"}\' "$PWD" "$(ls AGENTS.md 2>/dev/null)" > "$out"\n')
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            result, _ = ai_provider.CodexCliProvider(executable=str(script), model="").propose("x")
        self.assertNotEqual(Path(result["cwd"]).resolve(), Path.cwd().resolve())
        self.assertEqual(result["agents"], "")

    def test_calls_run_lean_with_the_users_model_and_effort_and_can_be_turned_back(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.toml"
            config.write_text('model = "big-model"\nmodel_reasoning_effort = "low"\npersonality = "pragmatic"\n')
            script = Path(folder) / "codex"
            script.write_text("#!/bin/sh\n"
                              'out=""; args=$(echo "$*" | sed \'s/"/\\\\"/g\'); while [ $# -gt 0 ]; do if [ "$1" = "--output-last-message" ]; then out="$2"; fi; shift; done\n'
                              'cat > /dev/null\nprintf \'{"args": "%s"}\' "$args" > "$out"\n')
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            with patch.dict(os.environ, {}, clear=False):
                for key in ("ARCHIE_AI_MODEL", "ARCHIE_CODEX_MODEL", "ARCHIE_CODEX_LEAN", "ARCHIE_CODEX_REASONING"):
                    os.environ.pop(key, None)
                args = ai_provider.CodexCliProvider(executable=str(script), config_path=config).propose("x")[0]["args"]
                lean = ai_provider.CodexCliProvider(executable=str(script), config_path=config)
                self.assertEqual((lean.model, lean.cli_model), ("", "big-model"))   # page-reading cache keys don't change
                os.environ["ARCHIE_CODEX_LEAN"] = "0"
                plain = ai_provider.CodexCliProvider(executable=str(script), config_path=config).propose("x")[0]["args"]
        for part in ("--ignore-user-config", "project_doc_max_bytes=0", 'model_reasoning_effort="low"', "--disable apps",
                     "--disable computer_use", "--disable shell_tool", "model_instructions_file=", 'web_search="disabled"',
                     'personality="none"', "--model big-model", "--json"):
            self.assertIn(part, args)
        self.assertNotIn("--ignore-user-config", plain)
        self.assertNotIn("--model", plain)                          # the CLI reads the user's config itself

    def test_a_small_model_is_used_only_for_simple_tasks_and_only_when_set(self):
        with patch.dict(os.environ, {"ARCHIE_AI_SMALL_MODEL": ""}):
            self.assertIsNone(ai_provider.model_for("pass1"))
        with patch.dict(os.environ, {"ARCHIE_AI_SMALL_MODEL": "small-model"}):
            self.assertEqual(ai_provider.model_for("pass1"), "small-model")
            self.assertEqual(ai_provider.model_for("skill:sheet_identity"), "small-model")
            for task in ("pass2", "skill:equipment_evidence", "skill:information_needs", ""):
                self.assertIsNone(ai_provider.model_for(task))

    def test_a_used_up_plan_is_a_usage_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = ai_provider.CodexCliProvider(executable=self.fake_cli(folder, "", 1, "ERROR: You have hit your usage limit. try again at 6:01 PM."), model="")
            with self.assertRaisesRegex(ai_provider.UsageLimitReached, "resets at 6:01 PM"):
                provider.propose("x")


class UsageLogTests(unittest.TestCase):
    def test_every_call_is_logged_under_its_pass_and_totalled(self):
        class Fake:
            name, model = "openai", "gpt-5"

            def __init__(self, outcome):
                self.outcome = outcome

            def propose(self, prompt, image_paths=()):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return {"ok": True}, {"model": "gpt-5-2026", "usage": {"input_tokens": 1000, "output_tokens": 50}, "seconds": 3.5}

        with tempfile.TemporaryDirectory() as folder:
            ai_provider.recorded(Fake("ok"), folder, "pass1").propose("prompt text", ["a.png"])
            ai_provider.recorded(Fake("ok"), folder, "skill:equipment_evidence").propose("p")
            for error in (ai_provider.UsageLimitReached("limit"), RuntimeError("boom")):
                with self.assertRaises(type(error)):
                    ai_provider.recorded(Fake(error), folder, "pass2:equipment_appliances").propose("p")
            rows = [json.loads(line) for line in (Path(folder) / ai_provider.USAGE_FILE).read_text().splitlines()]
            summary = ai_provider.usage_summary(folder)
        self.assertEqual((rows[0]["purpose"], rows[0]["images"], rows[0]["prompt_chars"], rows[0]["model"], rows[0]["total_tokens"]),
                         ("pass1", 1, 11, "gpt-5-2026", 1050))
        self.assertEqual([row["error"] for row in rows[2:]], ["usage_limit", "failed"])
        self.assertEqual((summary["total"]["calls"], summary["total"]["failed"], summary["total"]["usage_limit_hits"], summary["total"]["tokens"]),
                         (4, 2, 1, 2100))
        self.assertEqual(set(summary["by_pass"]), {"pass 1 (pages)", "pass 2 (values)", "skills"})
        self.assertEqual(summary["by_pass"]["skills"]["input_tokens"], 1000)
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(ai_provider.usage_summary(empty)["total"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()


class EveryPassOnEveryProviderTests(unittest.TestCase):
    """Pass 1, pass 2 and the skills run unchanged on the API providers (network replaced by a stand-in)."""

    def api_reply(self, provider, payload):
        text = json.dumps(payload)
        if provider == "openai":
            return {"model": "gpt-5", "choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
        return {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 100, "output_tokens": 10}}

    def test_pass_1_and_pass_2_read_pages_through_each_api_and_log_usage(self):
        import shutil
        if not (shutil.which("pdftoppm") and shutil.which("pdftotext")):
            self.skipTest("needs poppler")
        import time
        from backend import page_extraction_service as pass2, page_inventory_service as pass1
        for name, key in (("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
            with self.subTest(provider=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                pdf = root / "set.pdf"
                pdf.write_text("%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                               "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
                (root / "job").mkdir()
                project = {"id": "job-" + name, "pdf": str(pdf), "review_dir": str(root / "job")}
                replies = [{"page_type": "schedule", "information": [{"kind": "equipment_appliances", "what": "E01 Combi oven", "evidence": "row"}]},
                           {"items": [{"name": "Combi oven", "code": "E01", "quantity": 1}]}]

                def urlopen(request, timeout=None):
                    body = json.loads(request.data)
                    text = json.dumps(body)
                    payload = replies[1] if "EQUIPMENT or APPLIANCE" in text else replies[0]
                    return FakeResponse(json.dumps(self.api_reply(name, payload)).encode())

                with patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": name, key: "test-key", "ARCHIE_AI_MODEL": ""}), \
                        patch.object(ai_provider.urllib.request, "urlopen", urlopen):
                    pass1.start(None, project)
                    deadline = time.monotonic() + 20
                    while (pass1._JOB.key(project) in pass1._RUNNING or pass2._JOB.key(project) in pass2._RUNNING) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    read = pass1.status(None, project)
                    values = pass2.status(None, project)
                summary = ai_provider.usage_summary(project["review_dir"])
                self.assertEqual((read["status"], read["read"]), ("done", 1))
                self.assertEqual([row["value"]["name"] for row in values["findings"]], ["Combi oven"])
                self.assertEqual(summary["by_pass"]["pass 1 (pages)"]["calls"], 1)
                self.assertEqual(summary["by_pass"]["pass 2 (values)"]["calls"], 1)
                self.assertEqual(list(summary["by_model"]), [f"{name} / {'gpt-5' if name == 'openai' else 'claude-sonnet-5-5'}"])
                self.assertEqual(summary["total"]["tokens"], 220)

    def test_a_skill_runs_on_its_case_file_through_an_api_and_logs_usage(self):
        from backend import page_inventory_service as pass1, skill_workflow_service as skills
        subskill = next(row for row in skills.load_subskill_registry()["subskills"] if row["id"] == "lighting_evidence")
        from tests.test_skill_workflow_service import empty_typed_proposal
        proposal = {"status": "needs_review", "affected_ids": [], "observations": [], "inferences": [], "citations": [], "confidence": None,
                    "alternatives": [], "unresolved_fields": [], "remediation": [], "proposal_fields": empty_typed_proposal(subskill)}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"ARCHIE_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"}), \
                patch.object(ai_provider.urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(json.dumps(self.api_reply("anthropic", proposal)).encode())):
            (Path(folder) / pass1.RESULT_FILE).write_text(json.dumps({"pages": {"3": {"page_type": "reflected_ceiling_plan", "information": [
                {"kind": "lighting", "what": "12 downlights", "evidence": "legend"}]}}}))
            project = {"id": "skill-api", "review_dir": folder}
            self.assertTrue(skills._case_file_route(project))
            raw = skills._call_case_file_provider(subskill, project, {}, {}, {"raw_record": {}})
            summary = ai_provider.usage_summary(folder)
        self.assertEqual(raw["status"], "needs_review")
        self.assertEqual((summary["by_pass"]["skills"]["calls"], list(summary["by_model"])), (1, ["anthropic / claude-sonnet-5-5"]))
