#!/usr/bin/env python3
"""Run the M8.6 human memory review queue check."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (
    CompanionPaths,
    run_m8_memory_review_queue_check,
    write_m8_memory_review_queue_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M8.6 human memory review queue check")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m8_memory_review_queue_check(paths)
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m8_memory_review_queue_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
