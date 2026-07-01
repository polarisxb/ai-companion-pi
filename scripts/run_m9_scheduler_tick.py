#!/usr/bin/env python3
"""Run one M9 live scheduler opportunity check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m9_scheduler_tick  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one M9 scheduler tick")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-runtime-writes", action="store_true")
    parser.add_argument(
        "--allow-before-activation-report",
        action="store_true",
        help="Test hook: do not require life-loop/m9_scheduler_activation_report.json.",
    )
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m9_scheduler_tick(
        paths,
        random_seed=args.seed,
        write_runtime=not args.no_runtime_writes,
        require_activation_report=not args.allow_before_activation_report,
    )
    report = result.to_dict()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
