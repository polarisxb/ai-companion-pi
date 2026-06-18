#!/usr/bin/env python3
"""Run the M5.6 companion-quality release gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m5_quality_release_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M5.6 companion-quality release gate")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument("--min-trial-cycles", type=int, default=3, help="Minimum accepted M5.5 cycles")
    parser.add_argument(
        "--m4-post-change-guard-report",
        default=None,
        help="Override m4_post_change_guard_report.json path.",
    )
    parser.add_argument(
        "--m5-quality-report",
        default=None,
        help="Override m5_quality_report.json path.",
    )
    parser.add_argument(
        "--m5-trial-report",
        default=None,
        help="Override m5_quality_trial_report.json path.",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m5_quality_release_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m5_quality_release_report.json.",
    )
    args = parser.parse_args()

    if args.min_trial_cycles < 1:
        parser.error("--min-trial-cycles must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m5_quality_release_gate(
        paths,
        min_trial_cycles=args.min_trial_cycles,
        m4_post_change_guard_report_path=args.m4_post_change_guard_report,
        m5_quality_report_path=args.m5_quality_report,
        m5_trial_report_path=args.m5_trial_report,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m5_quality_release_report.json"
        )
        _write_report(report_path, report)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
