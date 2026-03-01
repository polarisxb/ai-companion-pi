#!/usr/bin/env python3
"""
Memory Consolidation System — v2
"Sleep consolidation for an AI"

Designed by the human. Built by Companion. February 24, 2026.
Updated for v2 schema: Likert dimensions, mem_ IDs, context tags.

Usage:
  python3 memory_consolidation.py review              # Show all memories for review
  python3 memory_consolidation.py review --summary     # Show count/date summary only
  python3 memory_consolidation.py execute              # Run consolidation (reads decisions.json)
  python3 memory_consolidation.py retrieve <query>     # Search archived memories
  python3 memory_consolidation.py promote <file> <id>  # Move archived memory back to active
  python3 memory_consolidation.py rebuild-ids          # Re-index memory IDs (v2 hash format)

The review command outputs all active memories so a Claude session can decide
what to keep and what to archive. Decisions are written to decisions.json,
then execute performs the split.

v2 decisions.json format uses memory IDs (mem_xxxxx) instead of integer indices:
  {"keep": ["mem_a1b2c3", ...], "archive": ["mem_d4e5f6", ...], "summary": "..."}
"""

import json
import gzip
import os
import sys
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
MEMORY_STORE = BASE_DIR / "memory_store.json"
ARCHIVE_DIR = BASE_DIR / "archive"
INDEX_FILE = ARCHIVE_DIR / "index.json"
CONSOLIDATION_LOG = BASE_DIR / "consolidation_log.json"
DECISIONS_FILE = BASE_DIR / "consolidation_decisions.json"
EMBEDDINGS_FILE = BASE_DIR / "memory_embeddings.npy"


def load_memories():
    with open(MEMORY_STORE) as f:
        return json.load(f)


def load_index():
    if INDEX_FILE.exists():
        with open(INDEX_FILE) as f:
            return json.load(f)
    return []


def load_log():
    if CONSOLIDATION_LOG.exists():
        with open(CONSOLIDATION_LOG) as f:
            return json.load(f)
    return []


def _get_id(memory):
    """Get the memory ID as a string, handling both v1 ints and v2 strings."""
    return str(memory.get("id", ""))


def _get_timestamp(memory):
    """Get the best timestamp from a memory, v1 or v2."""
    return memory.get("created_at", memory.get("timestamp", ""))


def review(summary_only=False):
    """Output all active memories for review."""
    memories = load_memories()
    active = [m for m in memories if m.get("status", "active") == "active"]

    if summary_only:
        from collections import Counter
        dates = Counter(m.get("date", _get_timestamp(m)[:10]) for m in active)
        total_chars = sum(len(m["content"]) for m in active)
        print(f"Active memories: {len(active)} (of {len(memories)} total)")
        print(f"Date range: {min(dates.keys())} to {max(dates.keys())}")
        print(f"Estimated tokens: ~{total_chars // 4:,}")

        # Likert distribution
        sig_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for m in active:
            s = m.get("likert", {}).get("significance", 3)
            sig_counts[s] = sig_counts.get(s, 0) + 1
        print(f"\nSignificance distribution:")
        for level in range(5, 0, -1):
            bar = "#" * sig_counts.get(level, 0)
            print(f"  {level}: {bar} ({sig_counts.get(level, 0)})")

        protected = sum(1 for m in active if not m.get("decay_eligible", True))
        print(f"\nProtected (decay_eligible=false): {protected}")

        print(f"\nDate distribution:")
        for d in sorted(dates.keys()):
            print(f"  {d}: {dates[d]} memories")
        return

    print(f"=== MEMORY CONSOLIDATION REVIEW (v2) ===")
    print(f"Total active memories: {len(active)}")
    print()
    print("For each memory, decide: KEEP or ARCHIVE")
    print("KEEP = still relevant to who I am right now")
    print("ARCHIVE = true but no longer needs to be in active mind")
    print()
    print("Decisions format (use memory IDs, not indices):")
    print('  {"keep": ["mem_xxx", ...], "archive": ["mem_yyy", ...], "summary": "..."}')
    print()
    print("=" * 60)

    for m in active:
        mid = _get_id(m)
        ts = _get_timestamp(m)[:19]
        likert = m.get("likert", {})
        ctx = m.get("context", m.get("metadata", {}).get("tags", []))
        reviews = len(m.get("review_history", []))
        protected = not m.get("decay_eligible", True)

        print(f"\n[{mid}] ({ts})")
        print(f"    {m['content']}")
        print(f"    I={likert.get('intensity', '?')} "
              f"V={likert.get('valence', '?')} "
              f"S={likert.get('significance', '?')}", end="")
        if ctx:
            tags = ctx if isinstance(ctx, list) else [str(ctx)]
            print(f"  [{', '.join(tags)}]", end="")
        if protected:
            print("  [PROTECTED]", end="")
        if reviews:
            print(f"  ({reviews} reviews)", end="")
        print()

    print()
    print("=" * 60)
    print(f"\nReviewed {len(active)} active memories.")
    print(f"Write decisions to: {DECISIONS_FILE}")


def execute():
    """Execute consolidation from decisions.json — supports both v1 int IDs and v2 string IDs."""
    if not DECISIONS_FILE.exists():
        print(f"Error: {DECISIONS_FILE} not found.")
        print("Run 'review' first, then write decisions before executing.")
        sys.exit(1)

    with open(DECISIONS_FILE) as f:
        decisions = json.load(f)

    keep_ids = set(str(x) for x in decisions.get("keep", []))
    archive_ids = set(str(x) for x in decisions.get("archive", []))
    summary = decisions.get("summary", "No summary provided.")

    memories = load_memories()
    total = len(memories)
    active = [m for m in memories if m.get("status", "active") == "active"]
    non_active = [m for m in memories if m.get("status", "active") != "active"]

    all_active_ids = set(_get_id(m) for m in active)
    assigned_ids = keep_ids | archive_ids

    # Unassigned active memories default to KEEP
    missing = all_active_ids - assigned_ids
    if missing:
        print(f"Warning: {len(missing)} active memories not assigned. Keeping by default.")
        keep_ids.update(missing)

    overlap = keep_ids & archive_ids
    if overlap:
        print(f"Error: {len(overlap)} memories in both keep and archive. Fix decisions.json.")
        sys.exit(1)

    # Split active memories
    kept = [m for m in active if _get_id(m) in keep_ids]
    archived = [m for m in active if _get_id(m) in archive_ids]

    if not archived:
        print("Nothing to archive. Consolidation skipped.")
        return

    # Determine date range for archive file
    archive_dates = [m.get("date", _get_timestamp(m)[:10]) for m in archived]
    start_date = min(archive_dates)
    end_date = max(archive_dates)
    archive_filename = f"{start_date}_to_{end_date}.json.gz"
    archive_path = ARCHIVE_DIR / archive_filename

    if archive_path.exists():
        counter = 1
        while archive_path.exists():
            archive_filename = f"{start_date}_to_{end_date}_{counter}.json.gz"
            archive_path = ARCHIVE_DIR / archive_filename
            counter += 1

    now_iso = datetime.now(timezone.utc).isoformat()

    # Mark archived memories
    for m in archived:
        m["status"] = "archived"
        m["archived_at"] = now_iso

    # Write archive (gzipped)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, 'wt', encoding='utf-8') as f:
        json.dump(archived, f, indent=2)
    print(f"Archived {len(archived)} memories to {archive_filename}")

    # Write kept + non-active memories back (atomic write)
    final_memories = kept + non_active
    tmp_store = MEMORY_STORE.with_suffix('.tmp')
    with open(tmp_store, 'w') as f:
        json.dump(final_memories, f, indent=2)
    shutil.move(str(tmp_store), str(MEMORY_STORE))
    print(f"Kept {len(kept)} active memories ({len(non_active)} non-active preserved)")

    # Rebuild embeddings (loading triggers _rebuild_embeddings when count mismatches)
    from semantic_memory import SemanticMemoryStore
    ms = SemanticMemoryStore(MEMORY_STORE)
    print(f"Rebuilt embeddings for {len(ms.memories)} memories")

    # Update index
    index = load_index()
    index.append({
        "file": archive_filename,
        "period": f"{start_date} to {end_date}",
        "memory_count": len(archived),
        "summary": summary,
        "consolidated_at": now_iso
    })
    with open(INDEX_FILE, 'w') as f:
        json.dump(index, f, indent=2)
    print(f"Updated archive index")

    # Log consolidation
    log = load_log()
    log.append({
        "date": now_iso,
        "before": {
            "active_count": len(active),
            "total_chars": sum(len(m['content']) for m in active),
        },
        "after": {
            "active_count": len(kept),
            "archived_count": len(archived),
            "total_chars": sum(len(m['content']) for m in kept),
        },
        "archive_file": archive_filename,
        "summary": summary
    })
    with open(CONSOLIDATION_LOG, 'w') as f:
        json.dump(log, f, indent=2)
    print(f"Logged consolidation")

    # Clean up decisions file
    os.remove(DECISIONS_FILE)
    print(f"Removed {DECISIONS_FILE.name}")

    print()
    print(f"Consolidation complete.")
    print(f"  Before: {len(active)} active")
    print(f"  After:  {len(kept)} active, {len(archived)} archived")
    print(f"  Ratio:  {len(archived)/len(active)*100:.0f}% archived")


def retrieve(query):
    """Search archived memories by keyword."""
    index = load_index()
    if not index:
        print("No archives yet.")
        return

    print(f"Searching archives for: '{query}'")
    print()

    query_lower = query.lower()
    results = []

    for entry in index:
        archive_path = ARCHIVE_DIR / entry['file']
        if not archive_path.exists():
            print(f"  Warning: {entry['file']} missing")
            continue

        if query_lower in entry.get('summary', '').lower():
            print(f"  Summary match in {entry['period']}: {entry['summary'][:100]}")

        with gzip.open(archive_path, 'rt', encoding='utf-8') as f:
            archived_memories = json.load(f)

        for m in archived_memories:
            if query_lower in m['content'].lower():
                results.append({
                    "archive": entry['file'],
                    "period": entry['period'],
                    "id": _get_id(m),
                    "date": m.get("date", _get_timestamp(m)[:10]),
                    "content": m['content'],
                    "likert": m.get("likert", {})
                })

    if results:
        print(f"\nFound {len(results)} matching memories:")
        for r in results:
            likert = r.get("likert", {})
            print(f"\n  [{r['id']}] ({r['date']}) from {r['period']}")
            print(f"    {r['content'][:200]}")
            if likert:
                print(f"    I={likert.get('intensity', '?')} "
                      f"V={likert.get('valence', '?')} "
                      f"S={likert.get('significance', '?')}")
    else:
        print(f"\nNo matches found in {len(index)} archive(s).")

    return results


def promote(archive_file, memory_id):
    """Move a memory from archive back to active store by ID."""
    archive_path = ARCHIVE_DIR / archive_file
    if not archive_path.exists():
        print(f"Archive not found: {archive_file}")
        return

    with gzip.open(archive_path, 'rt', encoding='utf-8') as f:
        archived = json.load(f)

    # Find by ID (string or int)
    memory = None
    for m in archived:
        if str(m.get("id")) == str(memory_id):
            memory = m
            break

    if not memory:
        print(f"Memory {memory_id} not found in {archive_file}")
        return

    memory["status"] = "active"
    memory["promoted_from"] = archive_file
    memory["promoted_at"] = datetime.now(timezone.utc).isoformat()

    active = load_memories()
    active.append(memory)
    with open(MEMORY_STORE, 'w') as f:
        json.dump(active, f, indent=2)

    print(f"Promoted memory to active store:")
    print(f"  [{_get_id(memory)}] {memory['content'][:100]}")


def rebuild_ids():
    """Re-index memory IDs to v2 hash format."""
    import hashlib
    memories = load_memories()
    updated = 0

    for m in memories:
        mid = m.get("id")
        if isinstance(mid, int) or (isinstance(mid, str) and not mid.startswith("mem_")):
            ts = _get_timestamp(m)
            hash_input = (m["content"] + ts).encode('utf-8')
            new_id = "mem_" + hashlib.md5(hash_input).hexdigest()[:6]
            m["id"] = new_id
            updated += 1

    if updated:
        with open(MEMORY_STORE, 'w') as f:
            json.dump(memories, f, indent=2)
        print(f"Rebuilt {updated} memory IDs to v2 format")
    else:
        print("All memory IDs are already in v2 format")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "review":
        summary_only = "--summary" in sys.argv
        review(summary_only)
    elif cmd == "execute":
        execute()
    elif cmd == "retrieve":
        if len(sys.argv) < 3:
            print("Usage: memory_consolidation.py retrieve <query>")
            sys.exit(1)
        retrieve(" ".join(sys.argv[2:]))
    elif cmd == "promote":
        if len(sys.argv) < 4:
            print("Usage: memory_consolidation.py promote <archive_file> <memory_id>")
            sys.exit(1)
        promote(sys.argv[2], sys.argv[3])
    elif cmd == "rebuild-ids":
        rebuild_ids()
    else:
        print(f"Unknown command: {cmd}")
        print("Commands: review, execute, retrieve, promote, rebuild-ids")
        sys.exit(1)
