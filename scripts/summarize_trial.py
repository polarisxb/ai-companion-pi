#!/usr/bin/env python3
"""Summarize recent internal life-loop trial events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, build_trial_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize recent companion trial wakes")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument("--limit", type=int, default=10, help="Number of recent wake events to summarize")
    parser.add_argument(
        "--since-trigger",
        default=None,
        help="Only summarize events from the first trigger matching this prefix",
    )
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    summary = build_trial_summary(paths, limit=args.limit, since_trigger=args.since_trigger)
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
