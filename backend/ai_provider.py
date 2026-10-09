"""The AI provider for the PDF review (pass 1, pass 2 and the skills), chosen by a setting, and a usage log per job.

Providers (ARCHIE_AI_PROVIDER):
- codex_cli  (default) the Codex CLI signed in with the team's ChatGPT subscription; no API key.
- openai     the OpenAI API (OPENAI_API_KEY); model ARCHIE_AI_MODEL, default "gpt-5".
- anthropic  the Anthropic API (ANTHROPIC_API_KEY); model ARCHIE_AI_MODEL, default "claude-sonnet-5-5".
ARCHIE_AI_MODEL also picks the Codex CLI model (else the model in the signed-in user's Codex config). Switching
provider changes nothing else: the same prompts, case files, caching, resume and usage-limit handling apply.

ARCHIE_AI_SMALL_MODEL (unset by default) is a cheaper model for the simple tasks in SMALL_TASKS (reading what a
page is, sheet identity, revisions, site clues); get(task) picks it for those. Set it only once its answers are
checked against the evaluation cases.

The Codex CLI runs lean: without the user's Codex config (plugins, tool servers, browser and computer use,
personality), without AGENTS.md files, with its agent tools and web search off, and with two lines of instructions
in place of its coding-agent instructions, so a call carries little beyond Archie's prompt (about 4,600 tokens
of fixed cost instead of 16,700). Only the config's model and reasoning effort are kept. ARCHIE_CODEX_LEAN=0
turns this off.

Every provider has propose(prompt, image_paths) -> (reply dict, raw record). The raw record carries "usage"
({"input_tokens", "output_tokens"} when the provider reports them) and "seconds". A plan or account limit raises
UsageLimitReached with a plain message; a missing CLI, sign-in or key raises ProviderUnavailable.

recorded(provider, root, purpose) wraps a provider so each call is appended to <job>/ai_usage.jsonl: when, which
pass, provider and model, seconds, tokens, prompt size, images, and whether it succeeded. usage_summary() totals it.
"""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

USAGE_FILE = "ai_usage.jsonl"
PROVIDERS = ("codex_cli", "openai", "anthropic")
DEFAULT_MODELS = {"openai": "gpt-5", "anthropic": "claude-sonnet-5-5"}
TIMEOUT_S = 600
# Replies are stored whole (a retry re-checks a stored reply instead of paying for a new one); this only guards size.
MAX_REPLY_CHARS = 2_000_000
MAX_OUTPUT_TOKENS = 16000
_USAGE_LOCK = threading.Lock()


class ProviderUnavailable(RuntimeError):
    """The chosen provider can't be used on this server (not installed, not signed in, no key)."""


class UsageLimitReached(RuntimeError):
    """The plan's or account's AI allowance is used up; every further call would fail the same way."""


def usage_limit_message(text):
    """A plain message when a provider's error text says the usage limit is reached, else ""."""
    text = " ".join(str(text or "").split())
    if not re.search(r"usage limit|insufficient_quota|exceeded your current quota|credit balance is too low", text, re.I):
        return ""
    when = re.search(r"try again (at|in) ([^.]+)", text, re.I)
    return ("The AI usage limit is reached" + (f"; it resets {when.group(1)} {when.group(2).strip()}" if when else "")
            + ". Pages already read are kept; retry after the reset to read the rest.")


def parse_json_reply(text):
    """The JSON object in a model's reply (a code fence or surrounding words are tolerated)."""
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("The reply was not valid JSON.") from None
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise RuntimeError("The reply was not valid JSON.") from None
    if not isinstance(value, dict):
        raise RuntimeError("The reply was not a JSON object.")
    return value


def _codex_user_config(path=None):
    """The signed-in user's Codex config (only its model and reasoning effort are used); {} when unreadable."""
    import tomllib
    path = Path(path) if path else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def codex_event_usage(stdout):
    """The token split from `codex exec --json` events: the turn's usage (summed if there are several turns)."""
    usage = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        found = event.get("usage") if isinstance(event, dict) and event.get("type") == "turn.completed" else None
        if isinstance(found, dict):
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                if isinstance(found.get(key), int):
                    usage[key] = usage.get(key, 0) + found[key]
    if usage:
        usage["total_tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return usage


def _image_parts(image_paths):
    parts = []
    for path in image_paths:
        data = Path(path).read_bytes()
        media = "image/jpeg" if str(path).lower().endswith((".jpg", ".jpeg")) else "image/png"
        parts.append((media, base64.b64encode(data).decode("ascii")))
    return parts


class CodexCliProvider:
    """One `codex exec` call per request, signed in with ChatGPT (read-only sandbox, nothing kept)."""

    name = "codex_cli"

    # Codex's agent tools that a plain JSON task never uses; each adds its definition to every call. Measured on a
    # one-line prompt: 16,713 input tokens as called before, 13,217 without the user config, 4,632 with these off,
    # web search off, no personality and the short instructions below in place of the coding-agent instructions.
    LEAN_DISABLED_FEATURES = ("apps", "browser_use", "browser_use_external", "computer_use", "shell_tool", "unified_exec",
                              "multi_agent", "goals", "image_generation", "plugins", "skill_search", "tool_suggest",
                              "sleep_tool", "view_image", "hooks", "in_app_browser")
    LEAN_INSTRUCTIONS = ("You read architectural drawings and project evidence for Archie, a heat-load tool. Do exactly the "
                         "task in the user message, using only what it supplies and the attached images. Reply with the "
                         "requested JSON only. You have no tools.")

    def __init__(self, executable=None, timeout=TIMEOUT_S, model=None, config_path=None):
        self.executable = executable or shutil.which("codex")
        self.timeout = timeout
        self.lean = os.environ.get("ARCHIE_CODEX_LEAN", "1").strip() != "0"
        user_config = _codex_user_config(config_path)
        self.model = (model if model is not None else os.environ.get("ARCHIE_AI_MODEL", os.environ.get("ARCHIE_CODEX_MODEL", ""))).strip()
        # The model named on the command line. Without an override it is the user's configured model, which must be
        # named once the config is ignored; self.model stays "" then, so page-reading cache keys don't change.
        self.cli_model = self.model or (str(user_config.get("model") or "") if self.lean else "")
        self.reasoning = (os.environ.get("ARCHIE_CODEX_REASONING", "").strip()
                          or str(user_config.get("model_reasoning_effort") or "low"))
        if not self.executable:
            raise ProviderUnavailable("The Codex CLI isn't installed on this computer. Install it and sign in with ChatGPT (codex login).")

    def check_signed_in(self):
        try:
            result = subprocess.run([self.executable, "login", "status"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ProviderUnavailable(f"The Codex CLI couldn't be started: {error}") from error
        if result.returncode != 0 or "logged in" not in (result.stdout + result.stderr).lower():
            raise ProviderUnavailable("The Codex CLI isn't signed in. Run `codex login` and sign in with ChatGPT.")

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="archie-ai-") as folder:
            output = Path(folder) / "reply.json"
            # --json prints events, the last of which carries the token split (input, cached input, output, reasoning).
            command = [self.executable, "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--json",
                       "--output-last-message", str(output)]
            if self.lean:
                instructions = Path(folder) / "instructions.md"
                instructions.write_text(self.LEAN_INSTRUCTIONS, encoding="utf-8")
                command += ["--ignore-user-config", "-c", "project_doc_max_bytes=0",
                            "-c", f"model_reasoning_effort={json.dumps(self.reasoning)}",
                            "-c", f"model_instructions_file={json.dumps(str(instructions))}",
                            "-c", 'web_search="disabled"', "-c", 'personality="none"']
                for feature in self.LEAN_DISABLED_FEATURES:
                    command += ["--disable", feature]
            if self.cli_model:
                command += ["--model", self.cli_model]
            for image in image_paths:
                command += ["--image", str(image)]
            command.append("-")
            # The model process gets no API key from this server's environment.
            env = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
            try:
                # Run in the empty temp folder so the CLI doesn't add this repository's AGENTS.md to every prompt.
                result = subprocess.run(command, input=str(prompt), capture_output=True, text=True,
                                        timeout=self.timeout, env=env, check=False, cwd=folder)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"No reply within {self.timeout} seconds.") from error
            reply_text = output.read_text(encoding="utf-8") if output.is_file() else ""
            usage = codex_event_usage(result.stdout or "")
            if not usage:
                # Older CLIs print only "tokens used N"; it is recorded as input+output combined.
                tokens = re.search(r"tokens used\s*\n?\s*([\d,]+)", (result.stdout or "") + "\n" + (result.stderr or ""), re.I)
                usage = {"total_tokens": int(tokens.group(1).replace(",", ""))} if tokens else {}
            raw = {"provider": self.name, "model": self.model or "codex default", "exit_code": result.returncode,
                   "reply_text": reply_text[:MAX_REPLY_CHARS], "stderr_tail": (result.stderr or "")[-2000:],
                   "usage": usage, "seconds": round(time.monotonic() - started, 2)}
            if result.returncode != 0 or not reply_text.strip():
                limit = usage_limit_message((result.stderr or "") + (result.stdout or ""))
                if limit:
                    raise UsageLimitReached(limit)
                tail = " ".join((result.stderr or result.stdout or "").split())[-300:]
                raise RuntimeError(f"Codex CLI failed (exit {result.returncode}): {tail or 'no reply'}")
            return parse_json_reply(reply_text), raw


class _HttpProvider:
    name = ""
    key_env = ""

    def __init__(self, api_key=None, model=None, timeout=TIMEOUT_S, opener=None):
        self.api_key = api_key if api_key is not None else os.environ.get(self.key_env, "")
        self.model = (model or os.environ.get("ARCHIE_AI_MODEL", "") or DEFAULT_MODELS[self.name]).strip()
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        if not self.api_key:
            raise ProviderUnavailable(f"No {self.key_env} is set on this server, so the {self.name} API can't be used.")

    def check_signed_in(self):
        return None

    def _post(self, url, headers, body):
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json", **headers})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:2000] if hasattr(error, "read") else ""
            limit = usage_limit_message(detail)
            if limit:
                raise UsageLimitReached(limit) from error
            if error.code == 429:
                raise RuntimeError("The AI provider is rate-limiting requests (HTTP 429). Retry shortly.") from error
            raise RuntimeError(f"The {self.name} API returned HTTP {error.code}: {' '.join(detail.split())[:300]}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"The {self.name} API couldn't be reached: {error.reason}") from error


class OpenAIProvider(_HttpProvider):
    name, key_env = "openai", "OPENAI_API_KEY"

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        content = [{"type": "text", "text": str(prompt)}]
        content += [{"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}} for media, data in _image_parts(image_paths)]
        reply = self._post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {self.api_key}"},
                           {"model": self.model, "messages": [{"role": "user", "content": content}],
                            "response_format": {"type": "json_object"}})
        text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        usage = reply.get("usage") or {}
        raw = {"provider": self.name, "model": reply.get("model", self.model), "reply_text": text[:MAX_REPLY_CHARS],
               "usage": {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")},
               "seconds": round(time.monotonic() - started, 2)}
        return parse_json_reply(text), raw


class AnthropicProvider(_HttpProvider):
    name, key_env = "anthropic", "ANTHROPIC_API_KEY"

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        content = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}} for media, data in _image_parts(image_paths)]
        content.append({"type": "text", "text": str(prompt)})
        reply = self._post("https://api.anthropic.com/v1/messages", {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                           {"model": self.model, "max_tokens": MAX_OUTPUT_TOKENS, "messages": [{"role": "user", "content": content}]})
        text = "".join(part.get("text", "") for part in reply.get("content") or [] if isinstance(part, dict))
        usage = reply.get("usage") or {}
        raw = {"provider": self.name, "model": reply.get("model", self.model), "reply_text": text[:MAX_REPLY_CHARS],
               "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
               "seconds": round(time.monotonic() - started, 2)}
        return parse_json_reply(text), raw


CLASSES = {"codex_cli": CodexCliProvider, "openai": OpenAIProvider, "anthropic": AnthropicProvider}


def chosen():
    name = os.environ.get("ARCHIE_AI_PROVIDER", "codex_cli").strip().lower() or "codex_cli"
    if name not in CLASSES:
        raise ProviderUnavailable(f"ARCHIE_AI_PROVIDER must be one of {', '.join(PROVIDERS)} (it is {name!r}).")
    return name


def configured():
    """Cheap check (no sign-in call): the chosen provider has what it needs on this server."""
    try:
        name = chosen()
    except ProviderUnavailable:
        return False
    if name == "codex_cli":
        return bool(shutil.which("codex"))
    return bool(os.environ.get(CLASSES[name].key_env))


# Simple tasks that a cheaper model may do once ARCHIE_AI_SMALL_MODEL is set: page reading and document mapping.
SMALL_TASKS = frozenset({"pass1", "skill:sheet_identity", "skill:revision_scope", "skill:site_clue_extraction"})


def model_for(task=""):
    """The model override for a task: the small model for SMALL_TASKS when one is set, else None (the default)."""
    small = os.environ.get("ARCHIE_AI_SMALL_MODEL", "").strip()
    return small if small and task in SMALL_TASKS else None


def get(task=""):
    """The chosen provider, ready to call (raises ProviderUnavailable with the reason)."""
    model = model_for(task)
    provider = CLASSES[chosen()](model=model) if model else CLASSES[chosen()]()
    provider.check_signed_in()
    return provider


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def record_usage(root, entry):
    path = Path(root) / USAGE_FILE
    with _USAGE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


class recorded:
    """Wrap a provider so every call is logged to the job's usage file under a purpose (e.g. "pass1", "skill:x")."""

    def __init__(self, provider, root, purpose):
        self.provider, self.root, self.purpose = provider, root, purpose
        self.model = getattr(provider, "model", "")
        self.name = getattr(provider, "name", type(provider).__name__)

    def propose(self, prompt, image_paths=()):
        started = time.monotonic()
        entry = {"at": _now(), "purpose": self.purpose, "provider": self.name, "model": self.model or "default",
                 "prompt_chars": len(str(prompt)), "images": len(list(image_paths)), "ok": False}
        try:
            result = self.provider.propose(prompt, image_paths=image_paths)
        except UsageLimitReached as error:
            record_usage(self.root, {**entry, "seconds": round(time.monotonic() - started, 2), "error": "usage_limit", "detail": str(error)[:300]})
            raise
        except Exception as error:
            record_usage(self.root, {**entry, "seconds": round(time.monotonic() - started, 2), "error": "failed", "detail": str(error)[:300]})
            raise
        raw = result[1] if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict) else getattr(result, "raw_record", {}) or {}
        usage = raw.get("usage") or {}
        record_usage(self.root, {**entry, "ok": True, "model": raw.get("model") or entry["model"],
                                 "seconds": raw.get("seconds") or round(time.monotonic() - started, 2),
                                 "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                                 "cached_input_tokens": usage.get("cached_input_tokens"),
                                 "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
                                 "total_tokens": usage.get("total_tokens") or (
                                     (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or None)})
        return result


def _group(purpose):
    if purpose.startswith("skill:"):
        return "skills"
    if purpose.startswith("pass2"):
        return "pass 2 (values)"
    if purpose.startswith("pass1"):
        return "pass 1 (pages)"
    return purpose


def usage_summary(root):
    """Totals for a job: calls, failures, tokens, seconds, by pass and by provider/model."""
    path = Path(root) / USAGE_FILE
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def total(items):
        return {"calls": len(items), "failed": sum(1 for row in items if not row.get("ok")),
                "usage_limit_hits": sum(1 for row in items if row.get("error") == "usage_limit"),
                "tokens": sum(row.get("total_tokens") or 0 for row in items),
                "input_tokens": sum(row.get("input_tokens") or 0 for row in items),
                "output_tokens": sum(row.get("output_tokens") or 0 for row in items),
                "cached_input_tokens": sum(row.get("cached_input_tokens") or 0 for row in items),
                "reasoning_output_tokens": sum(row.get("reasoning_output_tokens") or 0 for row in items),
                "seconds": round(sum(row.get("seconds") or 0 for row in items), 1),
                "calls_without_token_count": sum(1 for row in items if row.get("ok") and not row.get("total_tokens"))}

    groups, models = {}, {}
    for row in rows:
        groups.setdefault(_group(str(row.get("purpose", ""))), []).append(row)
        models.setdefault(f"{row.get('provider', '')} / {row.get('model', '')}", []).append(row)
    return {"total": total(rows), "by_pass": {name: total(items) for name, items in sorted(groups.items())},
            "by_model": {name: total(items) for name, items in sorted(models.items())},
            "first": rows[0]["at"] if rows else "", "last": rows[-1]["at"] if rows else ""}
