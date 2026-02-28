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
import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
EMBEDDINGS_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_embeddings.npy")


def load_memories():
    if not STORAGE_PATH.exists():
        return []
    with open(STORAGE_PATH) as f:
        return json.load(f)


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
    memories = load_memories()
    active = [m for m in memories if m.get("status", "active") == "active"]
    sort_key = lambda m: m.get("created_at", m.get("timestamp", ""))
    return sorted(active, key=sort_key, reverse=True)[:limit]


def search(query, limit=5):
    if not STORAGE_PATH.exists() or not EMBEDDINGS_PATH.exists():
        return []
    memories = load_memories()
    if not memories:
        return []
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = np.load(EMBEDDINGS_PATH)
    query_emb = model.encode([query], show_progress_bar=False)
    sims = np.dot(embeddings, query_emb.T).flatten()
    norms = np.linalg.norm(embeddings, axis=1) * np.linalg.norm(query_emb)
    sims = sims / (norms + 1e-10)

    results = []
    for i in range(len(sims)):
        if sims[i] > 0.3 and memories[i].get("status", "active") == "active":
            results.append((memories[i], float(sims[i])))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]


def strongest(dimension="significance", limit=10):
    memories = load_memories()
    active = [m for m in memories if m.get("status", "active") == "active"]
    active.sort(key=lambda m: m.get("likert", {}).get(dimension, 3), reverse=True)
    return active[:limit]


def review(days=30):
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    memories = load_memories()
    return [m for m in memories
            if m.get("status", "active") == "active"
            and m.get("created_at", m.get("timestamp", "")) >= cutoff]


def get_by_id(memory_id):
    memories = load_memories()
    for m in memories:
        if str(m.get("id")) == str(memory_id):
            return m
    return None


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
