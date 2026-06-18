#!/usr/bin/env python3
"""Run the M4.2 Raspberry Pi deploy-readiness gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m4_deploy_check


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M4.2 deploy readiness checks")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument(
        "--final-freeze-report",
        default=None,
        help="Path to m3_final_freeze_report.json. Defaults to life-loop/m3_final_freeze_report.json.",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m4_deploy_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m4_deploy_report.json.",
    )
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m4_deploy_check(
        paths,
        final_freeze_report_path=args.final_freeze_report,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m4_deploy_report.json"
        )
        _write_report(report_path, report)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
