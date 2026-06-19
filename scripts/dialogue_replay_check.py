#!/usr/bin/env python3
"""Validate M7 dialogue transcripts/events without provider calls."""

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
    parser.add_argument("transcript", help="Transcript JSONL path, absolute or relative to companion home.")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--events", default=None, help="Optional conversation_events.jsonl path.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON result.")
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    result = check_dialogue_transcript(paths, args.transcript, events_path=args.events)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}: {payload['recommendation']}")
        print(f"transcript={payload['transcript']} rows={payload['transcript_rows']} events={payload['event_count']}")
        for problem in result.problems:
            print(f"- {problem}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
