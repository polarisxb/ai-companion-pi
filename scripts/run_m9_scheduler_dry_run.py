#!/usr/bin/env python3
"""Run the M9.2 supervised scheduler dry-run gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (  # noqa: E402
    CompanionPaths,
    run_m9_scheduler_dry_run,
    write_m9_scheduler_dry_run_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M9.2 supervised scheduler dry run")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--seed", type=int, default=902)
    parser.add_argument("--base-date", default=None, help="YYYY-MM-DD scenario date. Defaults to today.")
    parser.add_argument("--no-runtime-writes", action="store_true")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    try:
        base_date = date.fromisoformat(args.base_date) if args.base_date else None
    except ValueError:
        parser.error("--base-date must be YYYY-MM-DD")

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m9_scheduler_dry_run(
        paths,
        random_seed=args.seed,
        base_date=base_date,
        write_runtime=not args.no_runtime_writes,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m9_scheduler_dry_run_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
