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
    parser = argparse.ArgumentParser(description="Validate M7 dialogue memory proposals without accepting them.")
    parser.add_argument("--companion-home", default=None)
    args = parser.parse_args()
    result = run_m7_memory_proposal_gate(CompanionPaths.from_env(args.companion_home))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
