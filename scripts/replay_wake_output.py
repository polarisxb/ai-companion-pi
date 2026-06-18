#!/usr/bin/env python3
"""Replay a raw wake output through local gates without committing state."""

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
    SUPPORTED_LLM_PROVIDERS,
    create_llm_client,
    load_wake_events,
)
from companion_core.replay import ReplayRunner
from companion_core.secrets import load_local_secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay raw companion wake output")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument("--raw-output-file", default=None, help="Raw output file to replay")
    parser.add_argument("--event-id", default=None, help="Wake event id with stored raw output")
    parser.add_argument("--trigger", default="replay", help="Replay trigger label")
    parser.add_argument(
        "--expect",
        choices=("any", "accepted", "rejected"),
        default="any",
        help="Expected context gate result for process exit status",
    )
    parser.add_argument(
        "--repair-provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=None,
        help="Optional provider used only for repair/regenerate during replay",
    )
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable")
    parser.add_argument("--model", default=None, help="Model name for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key for OpenAI-compatible providers",
    )
    parser.add_argument("--timeout", type=int, default=300, help="LLM provider timeout in seconds")
    args = parser.parse_args()

    if bool(args.raw_output_file) == bool(args.event_id):
        parser.error("pass exactly one of --raw-output-file or --event-id")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    raw_output = _load_raw_output(paths, args)
    repair_llm = None
    provider = "replay"
    if args.repair_provider:
        load_local_secrets(paths)
        provider = args.repair_provider
        repair_llm = create_llm_client(
            args.repair_provider,
            claude_bin=args.claude_bin,
            timeout_seconds=args.timeout,
            model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
            base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
            api_key_env=args.api_key_env,
        )
    result = ReplayRunner(paths).replay_raw_output(
        raw_output,
        trigger=args.trigger,
        provider=provider,
        repair_llm_client=repair_llm,
    ).to_dict()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if _matches_expectation(result, args.expect) else 1


def _load_raw_output(paths: CompanionPaths, args: argparse.Namespace) -> str:
    if args.raw_output_file:
        return Path(args.raw_output_file).expanduser().read_text()
    event = _find_event(paths, args.event_id)
    raw_path = event.get("output_audit", {}).get("initial", {}).get("raw_output_path")
    if not raw_path:
        raise SystemExit(f"event {args.event_id} does not have stored raw output")
    return (paths.home / raw_path).read_text()


def _find_event(paths: CompanionPaths, event_id: str) -> dict:
    for event in load_wake_events(paths.wake_events_file):
        if event.get("id") == event_id:
            return event
    raise SystemExit(f"event not found: {event_id}")


def _matches_expectation(result: dict, expectation: str) -> bool:
    if expectation == "any":
        return True
    accepted = result.get("quality_gate", {}).get("context_eligible") is True
    return accepted if expectation == "accepted" else not accepted


if __name__ == "__main__":
    raise SystemExit(main())
