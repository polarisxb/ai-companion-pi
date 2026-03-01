#!/usr/bin/env python3
"""Query memories from the command line — v2 schema.

Usage:
  python query_memories.py recent [limit]                # Get recent memories
  python query_memories.py search "query" [limit]        # Semantic search
  python query_memories.py strongest [dimension] [limit]  # Ranked by Likert dimension
  python query_memories.py review [days]                  # Memories for consolidation review
  python query_memories.py get <memory_id>                # Get a specific memory
"""
import sys
from semantic_memory import SemanticMemoryStore, STORAGE_PATH


def _store():
    return SemanticMemoryStore(STORAGE_PATH)


def format_memory(m, score=None):
    """Format a single memory for display."""
    mid = m.get("id", "?")
    ts = m.get("created_at", m.get("timestamp", ""))[:19]
    content = m["content"]
    likert = m.get("likert", {})
    ctx = m.get("context", m.get("metadata", {}).get("tags", []))

    line = ""
    if score is not None:
        line += f"[{score:.2f}] "
    line += f"[{mid}] ({ts})\n"
    line += f"  {content}\n"

    if likert:
        line += (f"  I={likert.get('intensity', '?')} "
                 f"V={likert.get('valence', '?')} "
                 f"S={likert.get('significance', '?')}")
    if ctx:
        tags = ctx if isinstance(ctx, list) else [str(ctx)]
        line += f"  [{', '.join(tags)}]"

    source = m.get("source", "")
    contact = m.get("contact", "")
    if source or contact:
        line += f"\n  source={source}"
        if contact:
            line += f" contact={contact}"

    reviews = len(m.get("review_history", []))
    if reviews:
        line += f" ({reviews} reviews)"

    return line


def get_recent(limit=10):
    return _store().get_recent_memories(limit)


def search(query, limit=5):
    return _store().semantic_search(query, limit)


def strongest(dimension="significance", limit=10):
    return _store().get_strongest(dimension, limit)


def review(days=30):
    return _store().get_for_review(days)


def get_by_id(memory_id):
    return _store().get_by_id(memory_id)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "recent"

    if cmd == "recent":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for m in get_recent(limit):
            print(format_memory(m))

    elif cmd == "search" and len(sys.argv) > 2:
        query = sys.argv[2]
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        for m, score in search(query, limit):
            print(format_memory(m, score))

    elif cmd == "strongest":
        dimension = sys.argv[2] if len(sys.argv) > 2 else "significance"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        if dimension not in ("intensity", "valence", "significance"):
            print(f"Invalid dimension: {dimension}. Use: intensity, valence, significance")
            sys.exit(1)
        print(f"Top {limit} by {dimension}:")
        for m in strongest(dimension, limit):
            print(format_memory(m))

    elif cmd == "review":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        mems = review(days)
        print(f"Memories for review (last {days} days): {len(mems)}")
        for m in mems:
            print(format_memory(m))

    elif cmd == "get" and len(sys.argv) > 2:
        m = get_by_id(sys.argv[2])
        if m:
            print(format_memory(m))
            history = m.get("review_history", [])
            if history:
                print(f"\n  Review history ({len(history)}):")
                for entry in history:
                    print(f"    [{entry['reviewed_at'][:10]}]", end="")
                    for dim in ["intensity", "valence", "significance"]:
                        if dim in entry:
                            print(f" {dim[0].upper()}={entry[dim]}", end="")
                    if "note" in entry:
                        print(f" — {entry['note']}", end="")
                    print()
        else:
            print(f"Memory not found: {sys.argv[2]}")

    else:
        print(__doc__)
