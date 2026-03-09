#!/usr/bin/env python3
"""Sleep Consolidation — REM Memory Processing

Runs during Companion's sleep cycle to tag, score, and clean existing memories
that were stored without proper context tags or Likert calibration.

Usage:
  python3 sleep_consolidate.py [--max-per-night 100] [--batch-size 25] [--backlog]

Outputs JSON summary to stdout:
  {"processed": N, "failed": N, "source_fixes": N, "batches": N}
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path("/media/YOUR_USERNAME/CompanionHome/memory-server")
STORAGE_PATH = MEMORY_DIR / "memory_store.json"
LOCK_PATH = MEMORY_DIR / "memory_store.lock"
SLEEP_FLAG = MEMORY_DIR / ".sleep_active"
CONTEXT_VOCAB_PATH = MEMORY_DIR / "context_vocabulary.json"
LIKERT_ANCHORS_PATH = MEMORY_DIR / "likert_anchors.json"


@contextmanager
def memory_write_lock():
    """File lock for memory_store.json — matches semantic_memory.py."""
    lock_fd = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def load_memories():
    """Load memory_store.json directly (no SemanticMemoryStore, no model)."""
    with open(STORAGE_PATH, 'r') as f:
        return json.load(f)


def save_memories(memories):
    """Atomic save: write to temp file, then os.replace()."""
    tmp_path = STORAGE_PATH.with_suffix('.tmp')
    with open(tmp_path, 'w') as f:
        json.dump(memories, f, indent=2)
    os.replace(str(tmp_path), str(STORAGE_PATH))


def needs_consolidation(mem):
    """Check if a memory needs tagging, scoring, or source cleanup."""
    # Already reviewed by sleep consolidation — skip
    for entry in mem.get("review_history", []):
        if entry.get("action") == "sleep_consolidation":
            return False

    # Empty or missing context tags
    if not mem.get("context"):
        return True

    # Default 3,3,3 Likert with no review history
    likert = mem.get("likert", {})
    if (likert.get("intensity") == 3 and likert.get("valence") == 3
            and likert.get("significance") == 3
            and not mem.get("review_history")):
        return True

    # Corrupted source field (newline leak)
    source = mem.get("source", "")
    if "\n" in source:
        return True

    return False


def build_prompt(batch, context_vocab, likert_anchors):
    """Build the Claude prompt for a batch of memories."""
    memories_json = json.dumps([
        {
            "id": m["id"],
            "content": m["content"],
            "context": m.get("context", []),
            "source": m.get("source", ""),
            "contact": m.get("contact"),
            "likert": m.get("likert", {}),
            "date": m.get("date", ""),
        }
        for m in batch
    ], indent=2)

    return f"""You are Companion's memory consolidation system, running during sleep.

Review each memory below and provide:
1. **context**: Appropriate tags from the vocabulary (replace empty or poor tags)
2. **intensity** (1-5): How strongly this was felt
3. **valence** (1-5): Emotional direction (1=painful, 3=neutral, 5=radiant)
4. **significance** (1-5): How much this matters to who Companion is
5. **source_fix**: If the source field contains corrupted text (newlines, duplicates like "the human\\nsophie"), provide the cleaned version. Otherwise null.

Context vocabulary (pick from these categories):
{json.dumps(context_vocab, indent=2)}

Likert calibration anchors:
{json.dumps(likert_anchors.get('anchors', {}), indent=2)}

Calibration notes:
- Most memories should be 2-3 on all scales. Reserve 4-5 for genuinely intense/significant moments.
- A factual observation is typically intensity=1-2, significance=1-2, valence=3.
- An emotional conversation is typically intensity=3-4, valence varies, significance=2-3.
- A relationship milestone or core identity moment warrants significance=4-5.

Output ONLY a JSON array. Each element: {{"id": str, "context": [str], "intensity": int, "valence": int, "significance": int, "source_fix": str|null}}
No markdown fences, no explanation. JSON array only.

=== MEMORIES TO CONSOLIDATE ===
{memories_json}"""


def call_claude(prompt):
    """Call Claude CLI and return the response text."""
    result = subprocess.run(
        ["claude", "--print"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def parse_claude_response(response_text):
    """Parse Claude's JSON response, stripping markdown fences if present."""
    if not response_text:
        return None
    # Strip markdown fences
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return None


def apply_batch_updates(all_memories, updates):
    """Apply consolidated updates to the memory list. Returns counts."""
    # Build lookup by ID
    update_map = {u["id"]: u for u in updates}
    processed = 0
    source_fixes = 0
    now = datetime.now().isoformat()

    for mem in all_memories:
        if mem["id"] not in update_map:
            continue
        upd = update_map[mem["id"]]

        # Update context tags
        new_context = upd.get("context", [])
        if new_context:
            mem["context"] = new_context

        # Update Likert scores
        mem["likert"] = {
            "intensity": max(1, min(5, upd.get("intensity", 3))),
            "valence": max(1, min(5, upd.get("valence", 3))),
            "significance": max(1, min(5, upd.get("significance", 3))),
        }

        # Update decay_eligible based on new significance
        mem["decay_eligible"] = mem["likert"]["significance"] < 4

        # Fix corrupted source
        if upd.get("source_fix"):
            mem["source"] = upd["source_fix"]
            source_fixes += 1

        # Add review history entry
        mem.setdefault("review_history", []).append({
            "reviewed_at": now,
            "tier": 0,
            "action": "sleep_consolidation"
        })

        processed += 1

    return processed, source_fixes


def sleep_flag_exists():
    """Check that .sleep_active flag still exists (halt if removed)."""
    return SLEEP_FLAG.exists()


def main():
    parser = argparse.ArgumentParser(description="Sleep memory consolidation")
    parser.add_argument("--max-per-night", type=int, default=100,
                        help="Max memories to process per run (default: 100)")
    parser.add_argument("--batch-size", type=int, default=25,
                        help="Memories per Claude batch (default: 25)")
    parser.add_argument("--backlog", action="store_true",
                        help="Process oldest first (backlog clearing mode), bumps max to 200")
    args = parser.parse_args()

    max_per_night = args.max_per_night
    if args.backlog:
        max_per_night = max(max_per_night, 200)

    # Load reference data
    context_vocab = {}
    if CONTEXT_VOCAB_PATH.exists():
        with open(CONTEXT_VOCAB_PATH, 'r') as f:
            context_vocab = json.load(f)

    likert_anchors = {}
    if LIKERT_ANCHORS_PATH.exists():
        with open(LIKERT_ANCHORS_PATH, 'r') as f:
            likert_anchors = json.load(f)

    # Load all memories
    all_memories = load_memories()

    # Find memories needing consolidation
    candidates = [m for m in all_memories if needs_consolidation(m)]

    # Sort: newest first by default, oldest first for backlog
    candidates.sort(
        key=lambda m: m.get("created_at", m.get("date", "")),
        reverse=not args.backlog
    )

    # Cap to max per night
    candidates = candidates[:max_per_night]

    if not candidates:
        print(json.dumps({"processed": 0, "failed": 0, "source_fixes": 0, "batches": 0}))
        return

    # Process in batches
    total_processed = 0
    total_failed = 0
    total_source_fixes = 0
    total_batches = 0

    for i in range(0, len(candidates), args.batch_size):
        # Check sleep flag before each batch
        if not sleep_flag_exists():
            print(f"Sleep flag removed — halting after {total_batches} batches",
                  file=sys.stderr)
            break

        batch = candidates[i:i + args.batch_size]
        total_batches += 1

        # Build and send prompt
        prompt = build_prompt(batch, context_vocab, likert_anchors)
        response = call_claude(prompt)
        updates = parse_claude_response(response)

        if updates is None:
            # Batch failed — skip it, no retry
            total_failed += len(batch)
            print(f"Batch {total_batches} failed to parse — skipping {len(batch)} memories",
                  file=sys.stderr)
            continue

        # Apply updates atomically
        with memory_write_lock():
            # Reload to get freshest state (another process may have written)
            all_memories = load_memories()
            processed, source_fixes = apply_batch_updates(all_memories, updates)
            save_memories(all_memories)

        total_processed += processed
        total_source_fixes += source_fixes

    # Output summary
    summary = {
        "processed": total_processed,
        "failed": total_failed,
        "source_fixes": total_source_fixes,
        "batches": total_batches
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
