#!/usr/bin/env python3
"""Store a memory from the command line — v2 schema.

Usage:
  python store_memory.py "Something worth remembering"
  python store_memory.py "content" --source wakeup --intensity 4 --valence 5
  python store_memory.py "content" --context the human,trust --contact the human
  echo "Something" | python store_memory.py
"""
import sys
import os
import json
import hashlib
import argparse
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
import numpy as np
from likert_scorer import score_memory

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
EMBEDDINGS_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_embeddings.npy")


def generate_id(content, timestamp):
    hash_input = (content + timestamp).encode('utf-8')
    return "mem_" + hashlib.md5(hash_input).hexdigest()[:6]


def store(content, context=None, intensity=3, valence=3, significance=3,
          source="manual", contact=None):
    memories = []
    if STORAGE_PATH.exists():
        with open(STORAGE_PATH) as f:
            memories = json.load(f)

    now = datetime.now().isoformat()
    memory_id = generate_id(content, now)

    memory = {
        "id": memory_id,
        "content": content,
        "context": context or [],
        "date": now[:10],
        "created_at": now,
        "source": source,
        "contact": contact,
        "likert": {
            "intensity": max(1, min(5, intensity)),
            "valence": max(1, min(5, valence)),
            "significance": max(1, min(5, significance))
        },
        "review_history": [],
        "status": "active",
        "decay_eligible": significance < 4,
        "schema_refs": []
    }

    memories.append(memory)
    tmp_path = STORAGE_PATH.with_suffix('.tmp')
    with open(tmp_path, 'w') as f:
        json.dump(memories, f, indent=2)
    os.replace(str(tmp_path), str(STORAGE_PATH))

    # Update embeddings
    model = SentenceTransformer('all-MiniLM-L6-v2')
    new_emb = model.encode([content], show_progress_bar=False)
    if EMBEDDINGS_PATH.exists():
        existing = np.load(EMBEDDINGS_PATH)
        embeddings = np.vstack([existing, new_emb])
    else:
        embeddings = new_emb
    np.save(EMBEDDINGS_PATH, embeddings)

    print(f"Stored memory {memory_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Store a v2 memory")
    parser.add_argument("content", nargs="*", help="Memory content")
    parser.add_argument("--context", type=str, default=None,
                        help="Comma-separated context tags")
    parser.add_argument("--intensity", type=int, default=3,
                        help="Intensity (1-5, default 3)")
    parser.add_argument("--valence", type=int, default=3,
                        help="Valence (1-5, default 3)")
    parser.add_argument("--significance", type=int, default=3,
                        help="Significance (1-5, default 3)")
    parser.add_argument("--source", type=str, default="manual",
                        help="Source: wakeup, signal, task, cleanup, manual")
    parser.add_argument("--contact", type=str, default=None,
                        help="Contact name (e.g., the human, contact2)")
    parser.add_argument("--auto-score", action="store_true",
                        help="Auto-score Likert values via Claude when all are default (3)")

    args = parser.parse_args()

    # Get content from args or stdin
    content = " ".join(args.content) if args.content else sys.stdin.read().strip()

    if content:
        context = [t.strip() for t in args.context.split(",")] if args.context else None
        intensity, valence, significance = args.intensity, args.valence, args.significance
        if args.auto_score and intensity == 3 and valence == 3 and significance == 3:
            intensity, valence, significance = score_memory(content)
        store(content, context=context, intensity=intensity,
              valence=valence, significance=significance,
              source=args.source, contact=args.contact)
