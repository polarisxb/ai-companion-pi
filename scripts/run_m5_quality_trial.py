#!/usr/bin/env python3
"""Run the M5.5 controlled DeepSeek quality trial."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, run_m5_quality_trial


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M5.5 controlled quality trial")
    parser.add_argument("--companion-home", default=None, help="Target CompanionHome")
    parser.add_argument("--cycles", type=int, default=3, help="Number of DeepSeek wake cycles")
    parser.add_argument("--trigger", default="m5-manual-quality-trial", help="Trigger prefix")
    parser.add_argument("--timeout", type=int, default=300, help="DeepSeek provider timeout in seconds")
    parser.add_argument("--model", default=None, help="DeepSeek model override")
    parser.add_argument("--base-url", default=None, help="DeepSeek-compatible base URL override")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key when not using DEEPSEEK_API_KEY",
    )
    parser.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write life-loop/m5_quality_trial_report.json.",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Override report path. Defaults to life-loop/m5_quality_trial_report.json.",
    )
    args = parser.parse_args()

    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m5_quality_trial(
        paths,
        cycles=args.cycles,
        trigger=args.trigger,
        timeout_seconds=args.timeout,
        model=args.model or os.environ.get("COMPANION_LLM_MODEL"),
        base_url=args.base_url or os.environ.get("COMPANION_LLM_BASE_URL"),
        api_key_env=args.api_key_env,
    )
    report["saved_at"] = datetime.now().isoformat()
    if not args.no_write_report:
        report_path = (
            Path(args.report_file).expanduser()
            if args.report_file
            else paths.life_loop_dir / "m5_quality_trial_report.json"
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
