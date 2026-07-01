#!/usr/bin/env python3
"""Run the M9.4 presence observation and rollback drill gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (  # noqa: E402
    CompanionPaths,
    run_m9_presence_observation,
    write_m9_presence_observation_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M9.4 presence observation")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--observation-limit", type=int, default=20)
    parser.add_argument("--no-live-attempt-required", action="store_true")
    parser.add_argument("--perform-pause-drill", action="store_true")
    parser.add_argument("--perform-rollback-drill", action="store_true")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--report-file", default=None)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument(
        "--crontab-file",
        default=None,
        help="Test hook: read/write this file instead of the real user crontab.",
    )
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    reader = writer = None
    if args.crontab_file:
        crontab_path = Path(args.crontab_file)

        def reader() -> str:
            try:
                return crontab_path.read_text()
            except FileNotFoundError:
                return ""

        def writer(text: str) -> None:
            crontab_path.parent.mkdir(parents=True, exist_ok=True)
            crontab_path.write_text(text)

    result = run_m9_presence_observation(
        paths,
        observation_limit=args.observation_limit,
        require_live_attempt=not args.no_live_attempt_required,
        perform_pause_drill=args.perform_pause_drill,
        perform_rollback_drill=args.perform_rollback_drill,
        crontab_reader=reader,
        crontab_writer=writer,
        random_seed=args.seed,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m9_presence_observation_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
