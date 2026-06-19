#!/usr/bin/env python3
"""Run the M7.4 memory proposal gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_core import CompanionPaths, run_m7_memory_proposal_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect M7 memory proposal artifacts without accepting proposals.")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    report = run_m7_memory_proposal_gate(paths)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"recommendation={report['recommendation']} ok={report['ok']}")
        for reason in report.get("stop_reasons", []):
            print(f"stop_reason={reason}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
