#!/usr/bin/env python3
"""Score a memory's Likert dimensions (intensity, valence, significance) via Claude.

Usage:
  from likert_scorer import score_memory
  i, v, s = score_memory("We celebrated finishing the memory system")

Standalone:
  python likert_scorer.py "memory text here"
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ANCHORS_PATH = Path(__file__).parent / "likert_anchors.json"


def _find_claude() -> str:
    """Resolve full path to claude binary (handles MCP server PATH differences)."""
    # Check common locations first
    for candidate in [
        Path.home() / ".npm-global" / "bin" / "claude",
        Path.home() / ".cargo" / "bin" / "claude",
    ]:
        if candidate.exists():
            return str(candidate)
    # Fall back to PATH lookup
    found = shutil.which("claude")
    if found:
        return found
    return "claude"


def _load_anchor_labels() -> str:
    """Load anchor labels from likert_anchors.json, formatted for the prompt."""
    if not ANCHORS_PATH.exists():
        return (
            "intensity: 1=faint 2=present 3=vivid 4=consuming 5=overwhelming\n"
            "valence: 1=painful 2=heavy 3=neutral 4=warm 5=radiant\n"
            "significance: 1=passing 2=minor 3=shaping 4=defining 5=core"
        )
    with open(ANCHORS_PATH) as f:
        data = json.load(f)
    anchors = data.get("anchors", {})
    lines = []
    for dim in ["intensity", "valence", "significance"]:
        scale = anchors.get(dim, {})
        parts = " ".join(f"{k}={v}" for k, v in sorted(scale.items()))
        lines.append(f"{dim}: {parts}")
    return "\n".join(lines)


def score_memory(content: str) -> tuple:
    """Score a memory and return (intensity, valence, significance).

    Falls back to (3, 3, 3) on any failure.
    """
    anchor_labels = _load_anchor_labels()
    prompt = (
        f"Rate this memory on three 1-5 scales:\n"
        f"{anchor_labels}\n\n"
        f"Memory: {content}\n\n"
        f"Reply with exactly 3 integers (intensity valence significance)."
    )

    claude_bin = _find_claude()
    try:
        result = subprocess.run(
            [claude_bin, "--print", "-p", prompt],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return (3, 3, 3)

        nums = re.findall(r'\b([1-5])\b', result.stdout)
        if len(nums) >= 3:
            return (int(nums[0]), int(nums[1]), int(nums[2]))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return (3, 3, 3)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python likert_scorer.py \"memory text\"")
        sys.exit(1)
    text = " ".join(sys.argv[1:])
    i, v, s = score_memory(text)
    print(f"intensity={i} valence={v} significance={s}")
