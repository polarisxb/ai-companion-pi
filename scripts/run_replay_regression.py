#!/usr/bin/env python3
"""Run built-in replay regression cases without committing state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths
from companion_core.replay_regression import build_replay_regression_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run replay regression cases")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    payload = build_replay_regression_report(paths)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
