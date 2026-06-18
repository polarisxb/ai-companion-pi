#!/usr/bin/env python3
"""Run the M3.21 Pi predeploy readiness and smoke profile."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, SUPPORTED_LLM_PROVIDERS, run_pi_predeploy_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Run companion Pi predeploy checks")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument(
        "--smoke-home",
        default="/tmp/companion-m321-pi-predeploy-smoke",
        help="Isolated home for fake wake and replay regression",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"),
        help="Real provider profile. Defaults to COMPANION_LLM_PROVIDER or deepseek.",
    )
    parser.add_argument(
        "--memory-mode",
        choices=("json", "dual"),
        default=os.environ.get("COMPANION_MEMORY_MODE", "json"),
        help="Target memory mode. Pi predeploy defaults to json.",
    )
    parser.add_argument("--trigger", default="m321-pi-predeploy", help="Trigger prefix for smoke events")
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable")
    parser.add_argument("--model", default=None, help="Model name for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key for OpenAI-compatible providers",
    )
    parser.add_argument("--readiness-timeout", type=int, default=10, help="Provider preflight timeout")
    parser.add_argument("--wake-timeout", type=int, default=300, help="Real wake provider timeout")
    parser.add_argument(
        "--skip-provider-check",
        action="store_true",
        help="Skip provider-specific readiness preflight",
    )
    parser.add_argument(
        "--run-real-wake",
        action="store_true",
        help="Run one real provider wake in the target home after local checks pass",
    )
    parser.add_argument(
        "--allow-raw-output-storage",
        action="store_true",
        help="Allow COMPANION_STORE_RAW_OUTPUTS=1 during this predeploy run",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/predeploy_report.json in the target home",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override the predeploy report path. Defaults to life-loop/predeploy_report.json.",
    )
    args = parser.parse_args()

    if args.readiness_timeout < 1:
        parser.error("--readiness-timeout must be at least 1")
    if args.wake_timeout < 1:
        parser.error("--wake-timeout must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_pi_predeploy_check(
        paths,
        smoke_paths=CompanionPaths.from_env(args.smoke_home),
        provider=args.provider,
        memory_mode=args.memory_mode,
        trigger=args.trigger,
        run_provider_check=not args.skip_provider_check,
        run_real_wake=args.run_real_wake,
        allow_raw_output_storage=args.allow_raw_output_storage,
        claude_bin=args.claude_bin,
        readiness_timeout_seconds=args.readiness_timeout,
        wake_timeout_seconds=args.wake_timeout,
        model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
        base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
        api_key_env=args.api_key_env,
    )
    if not args.no_write_report:
        report_path = Path(args.report_file).expanduser() if args.report_file else paths.life_loop_dir / "predeploy_report.json"
        _write_report(report_path, report)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write_report(path: Path, report: dict) -> None:
    payload = dict(report)
    payload["saved_at"] = datetime.now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
