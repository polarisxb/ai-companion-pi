#!/usr/bin/env python3
"""Run the M4.8 long-running runtime observation check."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m4_observation_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M4.8 runtime observation check")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument("--hours", type=int, default=24, help="Required observation window in hours")
    parser.add_argument("--min-events", type=int, default=2, help="Required completed wake events in scope")
    parser.add_argument("--since", default=None, help="Observation start timestamp in ISO format")
    parser.add_argument("--trigger-prefix", default=None, help="Only include wake events with this trigger prefix")
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m4_observation_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m4_observation_report.json.",
    )
    args = parser.parse_args()

    if args.hours < 1:
        parser.error("--hours must be at least 1")
    if args.min_events < 1:
        parser.error("--min-events must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m4_observation_check(
        paths,
        observation_hours=args.hours,
        min_completed_events=args.min_events,
        since=args.since,
        trigger_prefix=args.trigger_prefix,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m4_observation_report.json"
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
