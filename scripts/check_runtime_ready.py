#!/usr/bin/env python3
"""Check whether the companion runtime is ready for a Pi/local trial."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, SUPPORTED_LLM_PROVIDERS, check_runtime_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Check companion runtime readiness")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"),
        help="Provider to preflight. Defaults to COMPANION_LLM_PROVIDER or deepseek.",
    )
    parser.add_argument(
        "--memory-mode",
        choices=("json", "dual"),
        default=os.environ.get("COMPANION_MEMORY_MODE", "dual"),
        help="Memory mode to validate. Defaults to COMPANION_MEMORY_MODE or dual.",
    )
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable for claude-cli checks")
    parser.add_argument("--model", default=None, help="Model name for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key for OpenAI-compatible providers",
    )
    parser.add_argument("--timeout", type=int, default=10, help="Provider preflight timeout in seconds")
    parser.add_argument(
        "--skip-provider-check",
        action="store_true",
        help="Skip provider-specific preflight checks",
    )
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = check_runtime_readiness(
        paths,
        provider=args.provider,
        memory_mode=args.memory_mode,
        claude_bin=args.claude_bin,
        timeout_seconds=args.timeout,
        model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
        base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
        api_key_env=args.api_key_env,
        run_provider_check=not args.skip_provider_check,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
