#!/usr/bin/env python3
"""Run the M11.6 read-only Signal outbound final freeze gate."""

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
    run_m11_outbound_freeze,
    write_m11_outbound_freeze_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M11.6 Signal outbound final freeze gate")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m11_outbound_freeze(paths)
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m11_outbound_freeze_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
