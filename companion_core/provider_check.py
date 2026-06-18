"""Provider preflight checks for the internal life-loop runner."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from urllib import error, request

from .llm import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    LLMProviderConfigError,
    SUPPORTED_LLM_PROVIDERS,
    create_llm_client,
)


def check_llm_provider(
    provider: str,
    *,
    claude_bin: str = "claude",
    timeout_seconds: int = 10,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
) -> dict:
    checks: list[dict] = []
    if provider not in SUPPORTED_LLM_PROVIDERS:
        checks.append(_check("provider", "failed", f"unsupported provider: {provider}"))
        return _result(provider, checks)

    try:
        create_llm_client(
            provider,
            claude_bin=claude_bin,
            timeout_seconds=timeout_seconds,
            model=model,
            base_url=base_url,
            api_key_env=api_key_env,
        )
    except LLMProviderConfigError as exc:
        checks.append(_check("configuration", "failed", str(exc)))
        return _result(provider, checks)

    checks.append(_check("configuration", "passed", "required provider settings are present"))
    if provider == "fake":
        checks.append(_check("availability", "passed", "fake provider is always available"))
    elif provider == "claude-cli":
        checks.append(_check_claude_cli(claude_bin))
    elif provider == "openai-compatible":
        checks.extend(_check_openai_compatible(base_url or "", api_key_env, timeout_seconds))
    elif provider == "deepseek":
        checks.extend(_check_deepseek(base_url or DEEPSEEK_BASE_URL, model or DEEPSEEK_DEFAULT_MODEL, api_key_env))
    elif provider == "ollama":
        checks.extend(_check_ollama(base_url or "http://localhost:11434", model or "", timeout_seconds))

    return _result(provider, checks)


def _check_claude_cli(claude_bin: str) -> dict:
    if "/" in claude_bin:
        candidate = Path(claude_bin).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return _check("availability", "passed", f"Claude CLI executable found at {candidate}")
        return _check("availability", "failed", f"Claude CLI executable is not runnable: {candidate}")

    resolved = shutil.which(claude_bin)
    if resolved:
        return _check("availability", "passed", f"Claude CLI executable found at {resolved}")
    return _check("availability", "failed", f"Claude CLI executable not found on PATH: {claude_bin}")


def _check_openai_compatible(base_url: str, api_key_env: str, timeout_seconds: int) -> list[dict]:
    checks = []
    api_key = os.environ.get(api_key_env) if api_key_env else None
    if api_key:
        checks.append(_check("api_key", "passed", f"API key loaded from {api_key_env}"))
    else:
        checks.append(_check("api_key", "warning", f"{api_key_env} is not set"))

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    checks.append(_probe_json_endpoint(f"{base_url.rstrip('/')}/models", headers, timeout_seconds))
    return checks


def _check_deepseek(base_url: str, model: str, api_key_env: str) -> list[dict]:
    resolved_api_key_env = api_key_env
    if api_key_env == "COMPANION_LLM_API_KEY" and not os.environ.get(api_key_env):
        resolved_api_key_env = DEEPSEEK_API_KEY_ENV

    checks = [
        _check("base_url", "passed", f"DeepSeek base URL: {base_url.rstrip('/')}"),
        _check("model", "passed", f"DeepSeek model: {model}"),
    ]
    if os.environ.get(resolved_api_key_env):
        checks.append(_check("api_key", "passed", f"API key loaded from {resolved_api_key_env}"))
    else:
        checks.append(_check("api_key", "failed", f"{resolved_api_key_env} is not set"))
    return checks


def _check_ollama(base_url: str, model: str, timeout_seconds: int) -> list[dict]:
    check = _probe_json_endpoint(f"{base_url.rstrip('/')}/api/tags", {"Accept": "application/json"}, timeout_seconds)
    checks = [check]
    data = check.get("data")
    if check["status"] != "passed" or not isinstance(data, dict):
        return checks

    available_models = [
        item.get("name")
        for item in data.get("models", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    if model in available_models:
        checks.append(_check("model", "passed", f"Ollama model is available: {model}"))
    else:
        checks.append(_check("model", "failed", f"Ollama model is not available: {model}"))
    return checks


def _probe_json_endpoint(url: str, headers: dict[str, str], timeout_seconds: int) -> dict:
    http_request = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        return _check("endpoint", "failed", f"HTTP {exc.code}: {_read_http_error(exc)}")
    except error.URLError as exc:
        return _check("endpoint", "failed", f"request failed: {exc.reason}")
    except TimeoutError:
        return _check("endpoint", "failed", f"timed out after {timeout_seconds} seconds")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return _check("endpoint", "failed", "endpoint returned invalid JSON")
    return _check("endpoint", "passed", f"endpoint reachable: {url}", data=data)


def _read_http_error(exc: error.HTTPError) -> str:
    try:
        return " ".join(exc.read().decode("utf-8").split())[:300] or "no response body"
    except (OSError, UnicodeDecodeError, ValueError):
        return "no response body"


def _check(name: str, status: str, message: str, *, data: dict | None = None) -> dict:
    item = {
        "name": name,
        "status": status,
        "message": message,
    }
    if data is not None:
        item["data"] = data
    return item


def _result(provider: str, checks: list[dict]) -> dict:
    return {
        "provider": provider,
        "ok": all(check["status"] != "failed" for check in checks),
        "checks": [_without_probe_data(check) for check in checks],
    }


def _without_probe_data(check: dict) -> dict:
    sanitized = dict(check)
    sanitized.pop("data", None)
    return sanitized
