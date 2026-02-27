#!/usr/bin/env python3
"""Query memories from the command line.

Usage:
  python query_memories.py recent [limit]     # Get recent memories
  python query_memories.py search "query"     # Semantic search
"""
import sys
import json
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
EMBEDDINGS_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_embeddings.npy")

def get_recent(limit=10):
    if not STORAGE_PATH.exists():
        return []
    with open(STORAGE_PATH) as f:
        memories = json.load(f)
    return sorted(memories, key=lambda m: m["timestamp"], reverse=True)[:limit]

def search(query, limit=5):
    if not STORAGE_PATH.exists() or not EMBEDDINGS_PATH.exists():
        return []
    with open(STORAGE_PATH) as f:
        memories = json.load(f)
    if not memories:
        return []
    model = SentenceTransformer('all-MiniLM-L6-v2')
    embeddings = np.load(EMBEDDINGS_PATH)
    query_emb = model.encode([query], show_progress_bar=False)
    sims = np.dot(embeddings, query_emb.T).flatten()
    norms = np.linalg.norm(embeddings, axis=1) * np.linalg.norm(query_emb)
    sims = sims / (norms + 1e-10)
    results = [(memories[i], float(sims[i])) for i in range(len(sims)) if sims[i] > 0.3]
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:limit]

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "recent"
    if cmd == "recent":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for m in get_recent(limit):
            print(f"[{m['timestamp']}] {m['content']}")
    elif cmd == "search" and len(sys.argv) > 2:
        query = " ".join(sys.argv[2:])
        for m, score in search(query):
            print(f"[{score:.2f}] {m['content']}")
