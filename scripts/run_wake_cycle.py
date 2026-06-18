#!/usr/bin/env python3
"""Run the companion internal life loop manually or in fake-LLM smoke mode."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (
    CompanionPaths,
    JsonMemoryStore,
    LifeLoopRunner,
    SUPPORTED_LLM_PROVIDERS,
    SemanticFirstMemoryStore,
    check_llm_provider,
    create_llm_client,
    load_wake_events,
)
from companion_core.secrets import load_local_secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Run companion wake cycle")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument("--cycles", type=int, default=1, help="Number of wake cycles to run")
    parser.add_argument("--fake-llm", action="store_true", help="Use deterministic fake LLM")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=None,
        help="LLM provider. Defaults to COMPANION_LLM_PROVIDER or claude-cli.",
    )
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable for real wake cycles")
    parser.add_argument("--model", default=None, help="Model name for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key for OpenAI-compatible providers",
    )
    parser.add_argument(
        "--check-provider",
        action="store_true",
        help="Validate selected provider configuration and reachability, then exit",
    )
    parser.add_argument("--timeout", type=int, default=300, help="LLM provider timeout in seconds")
    parser.add_argument("--trigger", default="manual", help="Trigger label for prompt context")
    parser.add_argument(
        "--memory-mode",
        choices=("json", "dual"),
        default=None,
        help="Memory write mode. Defaults to COMPANION_MEMORY_MODE or json.",
    )
    args = parser.parse_args()

    if args.fake_llm and args.provider and args.provider != "fake":
        parser.error("--fake-llm cannot be combined with a non-fake --provider")
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    provider = _resolve_provider(args)
    paths = CompanionPaths.from_env(args.companion_home)
    load_local_secrets(paths)
    model = args.model or os.environ.get("COMPANION_LLM_MODEL")
    base_url = args.base_url or os.environ.get("COMPANION_LLM_BASE_URL")
    if args.check_provider:
        check = check_llm_provider(
            provider,
            claude_bin=args.claude_bin,
            timeout_seconds=args.timeout,
            model=model,
            base_url=base_url,
            api_key_env=args.api_key_env,
        )
        print(json.dumps(check, indent=2))
        return 0 if check["ok"] else 1

    memory_mode = args.memory_mode or os.environ.get("COMPANION_MEMORY_MODE", "json")
    try:
        llm_client = create_llm_client(
            provider,
            claude_bin=args.claude_bin,
            timeout_seconds=args.timeout,
            model=model,
            base_url=base_url,
            api_key_env=args.api_key_env,
        )
    except ValueError as exc:
        parser.error(str(exc))
    runner = LifeLoopRunner(paths, llm_client=llm_client, memory_store=_create_memory_store(paths, memory_mode))

    results = []
    for cycle in range(1, args.cycles + 1):
        try:
            result = runner.run_once(trigger=f"{args.trigger}:{cycle}", provider=provider)
        except Exception as exc:
            failed_event = _latest_event(paths)
            results.append({
                "cycle": cycle,
                "status": "failed",
                "journal": None,
                "memories": [],
                "requests": [],
                "event": failed_event.get("id"),
                "semantic_shadow": _semantic_shadow_summary(failed_event),
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            })
            print(_format_output(paths, provider, memory_mode, results))
            return 1
        results.append({
            "cycle": cycle,
            "status": "completed",
            "journal": str(result.journal_path),
            "memories": [memory["id"] for memory in result.memories],
            "requests": [request["id"] for request in result.requests],
            "quality_warnings": (result.quality or {}).get("warnings", []),
            "event": result.event["id"] if result.event else None,
            "semantic_shadow": _semantic_shadow_summary(result.event or {}),
        })

    print(_format_output(paths, provider, memory_mode, results))
    return 0


def _resolve_provider(args: argparse.Namespace) -> str:
    if args.fake_llm:
        return "fake"
    return args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "claude-cli")


def _format_output(paths: CompanionPaths, provider: str, memory_mode: str, results: list[dict]) -> str:
    return json.dumps(
        {
            "companion_home": str(paths.home),
            "provider": provider,
            "memory_mode": memory_mode,
            "results": results,
        },
        indent=2,
    )


def _latest_event(paths: CompanionPaths) -> dict:
    events = load_wake_events(paths.wake_events_file, limit=1)
    return events[0] if events else {}


def _semantic_shadow_summary(event: dict) -> dict | None:
    shadow = event.get("semantic_shadow")
    if not isinstance(shadow, dict):
        return None
    return {
        "enabled": shadow.get("enabled") is True,
        "attempted": _count_int(shadow.get("attempted")),
        "succeeded": _count_int(shadow.get("succeeded")),
        "failed": _count_int(shadow.get("failed")),
        "skipped": _count_int(shadow.get("skipped")),
        "store_path": str(shadow.get("store_path") or ""),
    }


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _create_memory_store(paths: CompanionPaths, memory_mode: str):
    if memory_mode == "dual":
        return SemanticFirstMemoryStore(paths.memory_store)
    return JsonMemoryStore(paths.memory_store)


if __name__ == "__main__":
    raise SystemExit(main())
