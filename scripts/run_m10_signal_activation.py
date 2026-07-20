#!/usr/bin/env python3
"""Enable or disable the M10.3 managed Signal chat listener service."""

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
    run_m10_signal_activation,
    write_m10_signal_activation_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enable/disable the M10.3 Signal chat listener service")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true")
    group.add_argument("--disable", action="store_true")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--unit-dir", default=None, help="override the systemd user unit directory")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    result = run_m10_signal_activation(
        paths,
        enable=args.enable,
        unit_dir=Path(args.unit_dir).expanduser() if args.unit_dir else None,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m10_signal_activation_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
