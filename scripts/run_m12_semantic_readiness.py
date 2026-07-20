#!/usr/bin/env python3
"""Run the M12.1 semantic retrieval readiness audit."""

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
    run_m12_semantic_readiness,
    write_m12_semantic_readiness_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M12.1 semantic readiness audit")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--require-index", action="store_true", help="fail unless the index fully covers eligible memories")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = run_m12_semantic_readiness(paths, require_index=args.require_index)
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m12_semantic_readiness_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
