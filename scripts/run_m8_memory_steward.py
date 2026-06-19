#!/usr/bin/env python3
"""Run the M8.2 read-only memory steward pass."""

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
    run_m8_memory_steward_readonly,
    write_m8_memory_steward_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M8.2 read-only memory steward")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--transcript", default=None, help="Optional transcript path to inspect")
    parser.add_argument("--transcript-limit", type=int, default=3)
    parser.add_argument("--turn-limit", type=int, default=20)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    if args.transcript_limit < 0:
        parser.error("--transcript-limit must be >= 0")
    if args.turn_limit < 0:
        parser.error("--turn-limit must be >= 0")

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m8_memory_steward_readonly(
        paths,
        transcript_path=args.transcript,
        transcript_limit=args.transcript_limit,
        turn_limit=args.turn_limit,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m8_memory_steward_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
