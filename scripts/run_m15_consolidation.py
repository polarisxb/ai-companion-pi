#!/usr/bin/env python3
"""Run M15 sleep consolidation: check debt, plan, apply, or roll back.

Blackout-safe: planning never mutates memories; applying is a single atomic
store replace; plans are idempotent and reversible. The default mode is a
read-only debt check so cron/boot hooks can call this script cheaply.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (  # noqa: E402
    CompanionPaths,
    SUPPORTED_LLM_PROVIDERS,
    apply_consolidation_plan,
    consolidation_due,
    create_llm_client,
    load_consolidation_config,
    load_consolidation_ledger,
    load_consolidation_plan,
    load_consolidation_state,
    rollback_consolidation_plan,
    run_consolidation_once,
)
from companion_core.secrets import load_local_secrets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="M15 sleep consolidation runner")
    parser.add_argument("--companion-home", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only: report due status and state")
    mode.add_argument("--plan-only", action="store_true", help="Plan and persist, but do not apply")
    mode.add_argument("--apply-plan", metavar="PLAN_ID", help="Apply a persisted plan by id")
    mode.add_argument("--rollback", metavar="PLAN_ID", help="Roll back an applied plan by id")
    parser.add_argument("--confirm-consolidation", action="store_true",
                        help="Required for a full plan+apply run")
    parser.add_argument("--ignore-due", action="store_true",
                        help="Plan even when consolidation is not due yet")
    parser.add_argument("--fake-llm", action="store_true", help="Use the deterministic fake LLM")
    parser.add_argument("--provider", choices=SUPPORTED_LLM_PROVIDERS, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default="COMPANION_LLM_API_KEY")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    load_local_secrets(paths)
    config = load_consolidation_config(paths)

    if args.check:
        payload = {
            "mode": "check",
            "due": consolidation_due(paths, config),
            "state": load_consolidation_state(paths),
            "ledger_tail": load_consolidation_ledger(paths)[-3:],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.rollback:
        outcome = rollback_consolidation_plan(paths, args.rollback)
        print(json.dumps({"mode": "rollback", **outcome}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if outcome.get("rolled_back") else 1

    if args.apply_plan:
        plan = load_consolidation_plan(paths, args.apply_plan)
        if plan is None:
            print(json.dumps({"mode": "apply", "error": f"plan not found: {args.apply_plan}"}))
            return 1
        outcome = apply_consolidation_plan(paths, plan)
        print(json.dumps({"mode": "apply", **outcome}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if outcome.get("applied") or outcome.get("already_applied") else 1

    apply = not args.plan_only
    if apply and not args.confirm_consolidation:
        parser.error("a full plan+apply run requires --confirm-consolidation "
                     "(use --check, --plan-only, or --apply-plan otherwise)")

    if args.fake_llm and args.provider and args.provider != "fake":
        parser.error("--fake-llm cannot be combined with a non-fake --provider")
    provider = "fake" if args.fake_llm else (
        args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "deepseek")
    )
    try:
        llm_client = create_llm_client(
            provider,
            timeout_seconds=args.timeout,
            model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
            base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
            api_key_env=args.api_key_env,
        )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        outcome = run_consolidation_once(
            paths,
            llm_client,
            config=config,
            apply=apply,
            ignore_due=args.ignore_due,
        )
    except Exception as exc:
        print(json.dumps({
            "mode": "plan-only" if args.plan_only else "run",
            "provider": provider,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    payload = {
        "mode": "plan-only" if args.plan_only else "run",
        "provider": provider,
        **outcome,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if outcome.get("skipped"):
        return 0
    if args.plan_only:
        return 0 if outcome.get("planned") else 1
    return 0 if outcome.get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
