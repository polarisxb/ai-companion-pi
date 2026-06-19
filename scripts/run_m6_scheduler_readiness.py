#!/usr/bin/env python3
"""Run the M6.6 scheduler handoff readiness gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m6_scheduler_readiness_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M6.6 scheduler handoff readiness")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument(
        "--manual-wake-report",
        default=None,
        help="Path to m6_pi_manual_wake_report.json. Defaults to life-loop/m6_pi_manual_wake_report.json.",
    )
    parser.add_argument(
        "--observation-report",
        default=None,
        help="Path to m6_pi_observation_report.json. Defaults to life-loop/m6_pi_observation_report.json.",
    )
    parser.add_argument(
        "--recovery-report",
        default=None,
        help="Path to m6_recovery_drill_report.json. Defaults to life-loop/m6_recovery_drill_report.json.",
    )
    parser.add_argument(
        "--rollback-instructions",
        default=None,
        help="Path to rollback instructions. Defaults to docs/m6-pi-scheduler-readiness-design.md.",
    )
    parser.add_argument(
        "--fixture-allow-non-pi",
        action="store_true",
        help="Test-only escape hatch: do not require Raspberry Pi platform identity.",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m6_scheduler_readiness_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m6_scheduler_readiness_report.json.",
    )
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m6_scheduler_readiness_check(
        paths,
        manual_wake_report_path=args.manual_wake_report,
        observation_report_path=args.observation_report,
        recovery_report_path=args.recovery_report,
        rollback_instructions_path=args.rollback_instructions,
        require_raspberry_pi=not args.fixture_allow_non_pi,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m6_scheduler_readiness_report.json"
        )
        _write_report(report_path, report)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["recommendation"] != "inspect" else 1


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
