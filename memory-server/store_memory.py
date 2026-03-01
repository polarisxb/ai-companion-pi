#!/usr/bin/env python3
"""Store a memory from the command line — v2 schema.

Usage:
  python store_memory.py "Something worth remembering"
  python store_memory.py "content" --source wakeup --intensity 4 --valence 5
  python store_memory.py "content" --context the human,trust --contact the human
  echo "Something" | python store_memory.py
"""
import sys
import argparse
from semantic_memory import SemanticMemoryStore, memory_write_lock, STORAGE_PATH
from likert_scorer import score_memory


def store(content, context=None, intensity=3, valence=3, significance=3,
          source="manual", contact=None):
    with memory_write_lock():
        ms = SemanticMemoryStore(STORAGE_PATH)
        memory = ms.store_memory(content=content, context=context,
                                  intensity=intensity, valence=valence,
                                  significance=significance, source=source,
                                  contact=contact)
    print(f"Stored memory {memory['id']}")


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
