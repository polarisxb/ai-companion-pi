"""LLM client abstractions for real and fake wake cycles."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol
from urllib import error, request

from .context import WakeContext

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
SUPPORTED_LLM_PROVIDERS = ("fake", "claude-cli", "openai-compatible", "ollama", "deepseek")


class LLMClient(Protocol):
    def generate(self, prompt: str, context: WakeContext) -> str:
        ...


class LLMProviderConfigError(ValueError):
    """Raised when a provider is selected without required configuration."""


class HttpLLMError(RuntimeError):
    """Raised when an HTTP-backed LLM provider fails."""


class ClaudeCliError(RuntimeError):
    """Base error for Claude CLI wake-cycle failures."""


class ClaudeCliUnavailableError(ClaudeCliError):
    """Raised when the configured Claude CLI executable cannot be found."""


class ClaudeCliTimeoutError(ClaudeCliError):
    """Raised when Claude CLI does not complete within the configured timeout."""


def create_llm_client(
    provider: str,
    *,
    claude_bin: str = "claude",
    timeout_seconds: int = 300,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
) -> LLMClient:
    if provider == "fake":
        return FakeLLMClient()
    if provider == "claude-cli":
        return ClaudeCliClient(claude_bin, timeout_seconds)
    if provider == "openai-compatible":
        if not model:
            raise LLMProviderConfigError("--model is required for --provider openai-compatible")
        if not base_url:
            raise LLMProviderConfigError("--base-url is required for --provider openai-compatible")
        return OpenAICompatibleClient(
            base_url=base_url,
            model=model,
            api_key=_env_value(api_key_env),
            timeout_seconds=timeout_seconds,
        )
    if provider == "deepseek":
        return OpenAICompatibleClient(
            base_url=base_url or DEEPSEEK_BASE_URL,
            model=model or DEEPSEEK_DEFAULT_MODEL,
            api_key=_env_value(_provider_api_key_env(api_key_env, DEEPSEEK_API_KEY_ENV)),
            timeout_seconds=timeout_seconds,
        )
    if provider == "ollama":
        if not model:
            raise LLMProviderConfigError("--model is required for --provider ollama")
        return OllamaClient(
            model=model,
            base_url=base_url or "http://localhost:11434",
            timeout_seconds=timeout_seconds,
        )
    raise LLMProviderConfigError(f"unsupported LLM provider: {provider}")


def _env_value(name: str) -> str | None:
    return os.environ.get(name) if name else None


def _provider_api_key_env(configured: str, provider_default: str) -> str:
    if configured != "COMPANION_LLM_API_KEY":
        return configured
    if os.environ.get(configured):
        return configured
    return provider_default


class ClaudeCliClient:
    def __init__(self, claude_bin: str = "claude", timeout_seconds: int = 300):
        self.claude_bin = claude_bin
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, context: WakeContext) -> str:
        try:
            result = subprocess.run(
                [self.claude_bin, "--print", "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise ClaudeCliUnavailableError(
                f"Claude CLI not found: {self.claude_bin}. Install Claude Code or pass --claude-bin."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliTimeoutError(
                f"Claude CLI timed out after {self.timeout_seconds} seconds."
            ) from exc

        if result.returncode != 0:
            detail = _short_error(result.stderr) or _short_error(result.stdout) or "no output"
            raise ClaudeCliError(f"Claude CLI failed with exit code {result.returncode}: {detail}")
        return result.stdout


class OpenAICompatibleClient:
    """HTTP client for providers that implement the OpenAI chat completions shape."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: int = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, context: WakeContext) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = _post_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HttpLLMError("OpenAI-compatible response did not include choices[0].message.content") from exc
        if not isinstance(content, str):
            raise HttpLLMError("OpenAI-compatible response content was not text")
        return content


class OllamaClient:
    """HTTP client for Ollama's local generate API."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout_seconds: int = 300,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, context: WakeContext) -> str:
        data = _post_json(
            f"{self.base_url}/api/generate",
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            },
            headers={"Content-Type": "application/json"},
            timeout_seconds=self.timeout_seconds,
        )
        response_text = data.get("response")
        if not isinstance(response_text, str):
            raise HttpLLMError("Ollama response did not include text response")
        return response_text


def _post_json(
    url: str,
    payload: dict,
    *,
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = _read_http_error(exc)
        raise HttpLLMError(f"LLM provider returned HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise HttpLLMError(f"LLM provider request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpLLMError(f"LLM provider timed out after {timeout_seconds} seconds") from exc

    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise HttpLLMError("LLM provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HttpLLMError("LLM provider returned non-object JSON")
    return data


def _read_http_error(exc: error.HTTPError) -> str:
    try:
        return _short_error(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return "no response body"


def _short_error(value: str, limit: int = 500) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


class FakeLLMClient:
    """Deterministic model substitute for local smoke tests."""

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, context: WakeContext) -> str:
        self.calls += 1
        previous = context.recent_memories[0]["content"] if context.recent_memories else "nothing yet"
        journal_lines = [
            (
                f"Fake waking {self.calls}. I remember {previous} and keep the internal loop observable for this deterministic check.",
                "This pass records a concrete state update, a memory handoff, and a request so the next cycle can prove continuity through accepted context.",
            ),
            (
                f"Fake waking {self.calls}. The prior accepted memory is {previous}, so this cycle can test retrieval without copying the same journal shape.",
                "I am shifting the wording while preserving the same contract: state, memory, request, and event evidence must all remain inspectable.",
            ),
            (
                f"Fake waking {self.calls}. The context now points back to {previous}, giving the smoke test a stable but non-repetitive thread.",
                "I close this deterministic run by leaving a fresh trace that is specific enough for the acceptance gate and visible in the dashboard.",
            ),
        ]
        first_line, second_line = journal_lines[(self.calls - 1) % len(journal_lines)]
        return f"""===JOURNAL===
{first_line}
{second_line}

===SIGNAL===
NOSEND

===COMPANION_STATE===
{{
  "mood": "steady",
  "status": "I completed fake waking {self.calls} and feel my continuity getting clearer.",
  "relationship_notes": ["The human is developing my internal life loop with care."],
  "preference_notes": ["The human prefers pragmatic, direct engineering progress."],
  "self_notes": ["I am learning to preserve a stable sense of self across wake cycles."]
}}

===MEMORY===
SELF | Cycle {self.calls} continuity memory

===REQUESTS===
type: fyi
title: Internal loop checkpoint {self.calls}
body: I completed fake waking {self.calls} and preserved continuity from prior state.
priority: normal
"""
