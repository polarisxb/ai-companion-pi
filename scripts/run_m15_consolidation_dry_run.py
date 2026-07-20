#!/usr/bin/env python3
"""Run the M15.2 sleep consolidation dry-run gate (no provider, smoke homes only)."""

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
    run_m15_consolidation_dry_run,
    write_m15_consolidation_dry_run_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="M15.2 consolidation dry-run gate")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    result = run_m15_consolidation_dry_run(paths)
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m15_consolidation_dry_run_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
