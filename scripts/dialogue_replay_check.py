#!/usr/bin/env python3
"""Validate an M7 dialogue transcript without calling an LLM provider."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_core import CompanionPaths
from companion_core.dialogue_replay import check_dialogue_transcript


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M7 dialogue transcript/replay check.")
    parser.add_argument("transcript", help="Transcript JSONL path, absolute or relative to --companion-home.")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--json", action="store_true", help="Print structured JSON result.")
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = check_dialogue_transcript(paths, Path(args.transcript))
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}: checked {result.rows_checked} transcript rows and {result.events_checked} linked events")
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
