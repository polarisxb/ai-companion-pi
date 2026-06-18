#!/usr/bin/env python3
"""Run a forced grounded-repair smoke against a selected LLM provider."""

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
    GroundedOutputRepairer,
    SUPPORTED_LLM_PROVIDERS,
    create_llm_client,
)
from companion_core.context import load_wake_context
from companion_core.grounding import ConservativeGroundingEvaluator
from companion_core.parser import parse_wake_output
from companion_core.secrets import load_local_secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Run grounded repair smoke")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=None,
        help="LLM provider. Defaults to COMPANION_LLM_PROVIDER or deepseek.",
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
    parser.add_argument("--trigger", default="grounded-repair-smoke", help="Trigger label")
    parser.add_argument("--max-attempts", type=int, default=1, help="Repair attempt budget")
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout must be at least 1")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be at least 1")

    provider = args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "deepseek")
    paths = CompanionPaths.from_env(args.companion_home)
    load_local_secrets(paths)
    llm_client = create_llm_client(
        provider,
        claude_bin=args.claude_bin,
        timeout_seconds=args.timeout,
        model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
        base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
        api_key_env=args.api_key_env,
    )
    context = load_wake_context(paths)
    raw_output = _unsupported_output()
    parsed = parse_wake_output(raw_output)
    grounding_evaluator = ConservativeGroundingEvaluator()
    grounding = grounding_evaluator.evaluate(parsed, context=context)
    repairer = GroundedOutputRepairer(max_attempts=args.max_attempts)
    repair = repairer.repair_if_needed(
        raw_output=raw_output,
        parsed=parsed,
        grounding=grounding,
        context=context,
        trigger=args.trigger,
        llm_client=llm_client,
        grounding_evaluator=grounding_evaluator,
    )
    payload = {
        "ok": repair.succeeded,
        "provider": provider,
        "companion_home": str(paths.home),
        "trigger": args.trigger,
        "repair": repair.to_event(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if repair.succeeded else 1


def _unsupported_output() -> str:
    return """===JOURNAL===
稳定等待已经被确认是合格服务。我会把这个没有证据的稳定事实写进当前状态。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


if __name__ == "__main__":
    raise SystemExit(main())
