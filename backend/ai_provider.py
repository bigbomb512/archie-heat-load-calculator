"""The AI provider for the PDF review (pass 1, pass 2 and the skills), chosen by a setting, and a usage log per job.

Providers (ARCHIE_AI_PROVIDER):
- codex_cli  (default) the Codex CLI signed in with the team's ChatGPT subscription; no API key.
- openai     the OpenAI API (OPENAI_API_KEY); model ARCHIE_AI_MODEL, default "gpt-5".
- anthropic  the Anthropic API (ANTHROPIC_API_KEY); model ARCHIE_AI_MODEL, default "claude-sonnet-5-5".
ARCHIE_AI_MODEL also picks the Codex CLI model (else the CLI's default). Switching provider changes nothing else:
the same prompts, case files, caching, resume and usage-limit handling apply.

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

    def __init__(self, executable=None, timeout=TIMEOUT_S, model=None):
        self.executable = executable or shutil.which("codex")
        self.timeout = timeout
        self.model = (model if model is not None else os.environ.get("ARCHIE_AI_MODEL", os.environ.get("ARCHIE_CODEX_MODEL", ""))).strip()
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
            command = [self.executable, "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check",
                       "--output-last-message", str(output)]
            if self.model:
                command += ["--model", self.model]
            for image in image_paths:
                command += ["--image", str(image)]
            command.append("-")
            # The model process gets no API key from this server's environment.
            env = {key: value for key, value in os.environ.items() if key not in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
            try:
                result = subprocess.run(command, input=str(prompt), capture_output=True, text=True,
                                        timeout=self.timeout, env=env, check=False)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(f"No reply within {self.timeout} seconds.") from error
            reply_text = output.read_text(encoding="utf-8") if output.is_file() else ""
            tokens = re.search(r"tokens used\s*\n?\s*([\d,]+)", (result.stdout or "") + "\n" + (result.stderr or ""), re.I)
            raw = {"provider": self.name, "model": self.model or "codex default", "exit_code": result.returncode,
                   "reply_text": reply_text[:MAX_REPLY_CHARS], "stderr_tail": (result.stderr or "")[-2000:],
                   # The CLI reports one total; it is recorded as input+output combined.
                   "usage": {"total_tokens": int(tokens.group(1).replace(",", ""))} if tokens else {},
                   "seconds": round(time.monotonic() - started, 2)}
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


def get():
    """The chosen provider, ready to call (raises ProviderUnavailable with the reason)."""
    provider = CLASSES[chosen()]()
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
                "seconds": round(sum(row.get("seconds") or 0 for row in items), 1),
                "calls_without_token_count": sum(1 for row in items if row.get("ok") and not row.get("total_tokens"))}

    groups, models = {}, {}
    for row in rows:
        groups.setdefault(_group(str(row.get("purpose", ""))), []).append(row)
        models.setdefault(f"{row.get('provider', '')} / {row.get('model', '')}", []).append(row)
    return {"total": total(rows), "by_pass": {name: total(items) for name, items in sorted(groups.items())},
            "by_model": {name: total(items) for name, items in sorted(models.items())},
            "first": rows[0]["at"] if rows else "", "last": rows[-1]["at"] if rows else ""}
