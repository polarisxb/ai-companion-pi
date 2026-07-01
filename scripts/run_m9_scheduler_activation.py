#!/usr/bin/env python3
"""Run the M9.3 limited live scheduler activation gate."""

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
    run_m9_scheduler_activation,
    write_m9_scheduler_activation_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M9.3 scheduler activation")
    parser.add_argument("--companion-home", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--enable", action="store_true", help="Enable the managed M9.3 cron artifact")
    mode.add_argument("--disable", action="store_true", help="Disable the managed M9.3 cron artifact")
    parser.add_argument("--seed", type=int, default=None, help="Seed for initial randomized presence window")
    parser.add_argument("--report-file", default=None)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--write-report", action="store_true", help="Also write a report for --disable")
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

    result = run_m9_scheduler_activation(
        paths,
        enable=args.enable,
        crontab_reader=reader,
        crontab_writer=writer,
        random_seed=args.seed,
    )
    report = result.to_dict()
    should_write_report = (args.enable and not args.no_write_report) or args.write_report
    if should_write_report:
        report_path = write_m9_scheduler_activation_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
