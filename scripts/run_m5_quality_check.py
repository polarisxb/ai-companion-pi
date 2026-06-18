#!/usr/bin/env python3
"""Run the M5.1 companion quality observation gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m5_quality_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M5.1 companion quality observation check")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument("--min-events", type=int, default=1, help="Required wake events in scope")
    parser.add_argument("--min-accepted-events", type=int, default=1, help="Required accepted wake events in scope")
    parser.add_argument("--limit", type=int, default=10, help="Limit selected events after scoping; 0 means no limit")
    parser.add_argument("--since", default=None, help="Only include events at or after this ISO timestamp")
    parser.add_argument("--trigger-prefix", default=None, help="Only include wake events with this trigger prefix")
    parser.add_argument(
        "--all-events",
        action="store_true",
        help="Do not scope from the latest successful M4 wake-trial event.",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m5_quality_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m5_quality_report.json.",
    )
    args = parser.parse_args()

    if args.min_events < 1:
        parser.error("--min-events must be at least 1")
    if args.min_accepted_events < 1:
        parser.error("--min-accepted-events must be at least 1")
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m5_quality_check(
        paths,
        min_events=args.min_events,
        min_accepted_events=args.min_accepted_events,
        limit=args.limit,
        since=args.since,
        trigger_prefix=args.trigger_prefix,
        use_m4_wake_baseline=not args.all_events,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m5_quality_report.json"
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
