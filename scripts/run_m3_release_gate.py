#!/usr/bin/env python3
"""Run the M3 deployment-candidate release gate."""

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

from companion_core import CompanionPaths, SUPPORTED_LLM_PROVIDERS, run_m3_release_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M3 release gate checks")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument(
        "--smoke-home",
        default="/tmp/companion-m325-release-gate-smoke",
        help="Isolated home for predeploy smoke checks",
    )
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"),
        help="Target real provider profile. Defaults to COMPANION_LLM_PROVIDER or deepseek.",
    )
    parser.add_argument(
        "--memory-mode",
        choices=("json", "dual"),
        default=os.environ.get("COMPANION_MEMORY_MODE", "json"),
        help="Target memory mode. M3 release gate defaults to json.",
    )
    parser.add_argument(
        "--since-trigger",
        default=None,
        help="Require a successful recent trial summary from the first matching trigger.",
    )
    parser.add_argument("--trial-limit", type=int, default=5, help="Number of trial events to consider")
    parser.add_argument(
        "--run-provider-check",
        action="store_true",
        help="Run provider network preflight during release gate readiness.",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m3_release_gate_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m3_release_gate_report.json.",
    )
    args = parser.parse_args()

    if args.trial_limit < 1:
        parser.error("--trial-limit must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m3_release_gate(
        paths,
        smoke_paths=CompanionPaths.from_env(args.smoke_home),
        provider=args.provider,
        memory_mode=args.memory_mode,
        trial_since_trigger=args.since_trigger,
        trial_limit=args.trial_limit,
        run_provider_check=args.run_provider_check,
    )
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m3_release_gate_report.json"
        )
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
