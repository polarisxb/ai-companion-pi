#!/usr/bin/env python3
"""Store a memory from the command line.

Usage:
  python store_memory.py "Something worth remembering"
  echo "Something" | python store_memory.py
"""
import sys
import json
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
EMBEDDINGS_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_embeddings.npy")

def store(content, tags=None):
    memories = []
    if STORAGE_PATH.exists():
        with open(STORAGE_PATH) as f:
            memories = json.load(f)
    memory = {
        "id": len(memories),
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "metadata": {"tags": tags or ["journal", "auto-stored"]}
    }
    memories.append(memory)
    with open(STORAGE_PATH, 'w') as f:
        json.dump(memories, f, indent=2)
    model = SentenceTransformer('all-MiniLM-L6-v2')
    new_emb = model.encode([content], show_progress_bar=False)
    if EMBEDDINGS_PATH.exists():
        existing = np.load(EMBEDDINGS_PATH)
        embeddings = np.vstack([existing, new_emb])
    else:
        embeddings = new_emb
    np.save(EMBEDDINGS_PATH, embeddings)
    print(f"Stored memory ID {memory['id']}")

if __name__ == "__main__":
    content = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if content:
        store(content)
