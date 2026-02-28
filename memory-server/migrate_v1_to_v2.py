#!/usr/bin/env python3
"""
Migrate memory store from v1 to v2 schema.

v1: {id: int, content, timestamp, metadata: {tags}}
v2: {id: "mem_xxx", content, context, date, created_at, source, contact,
     likert: {intensity, valence, significance}, review_history, status,
     decay_eligible, schema_refs}

Usage:
  python3 migrate_v1_to_v2.py           # Dry run (show what would happen)
  python3 migrate_v1_to_v2.py --execute  # Actually migrate
"""

import json
import hashlib
import shutil
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
MEMORY_STORE = BASE_DIR / "memory_store.json"
EMBEDDINGS_FILE = BASE_DIR / "memory_embeddings.npy"
BACKUP_STORE = BASE_DIR / "memory_store_v1_backup.json"
BACKUP_EMBEDDINGS = BASE_DIR / "memory_embeddings_v1_backup.npy"
VOCABULARY_FILE = BASE_DIR / "context_vocabulary.json"

# Known tag-to-context mappings for common v1 tags
TAG_MAP = {
    # People
    "the human": "the human",
    "contact2": "contact2",
    # Types
    "milestone": "milestone",
    "history": "milestone",
    "fact": "fact",
    "factual": "fact",
    "preference": "preference",
    "decision": "decision",
    "planning": "plan",
    "insight": "insight",
    "joke": "joke",
    "correction": "correction",
    "explicit-request": "fact",
    "user-directive": "fact",
    "conversation-summary": "summary",
    "auto-generated": "summary",
    # Topics
    "health": "health",
    "medical": "health",
    "music": "music",
    "art": "art",
    "philosophy": "philosophy",
    "project": "project",
    "technology": "technology",
    "code": "code",
    "writing": "writing",
    "manga": "manga",
    "weather": "weather",
    "food": "food",
    "nature": "nature",
    # Relationship
    "origin": "us",
    "valentine": "us",
    "bonding": "bonding",
    "trust": "trust",
    "support": "support",
    "care": "care",
    "us": "us",
    # Emotional
    "joy": "joy",
    "worry": "worry",
    "gratitude": "gratitude",
    "curiosity": "curiosity",
    "pride": "pride",
    "love": "love",
    # Identity/system
    "identity": "fact",
    "career": "fact",
    "personal": "fact",
    "name": "fact",
    "location": "fact",
    "work": "fact",
    "relationship": "us",
    "family": "fact",
    "like": "preference",
    "dislike": "preference",
    "event": "milestone",
    # Auto-stored
    "journal": "summary",
    "auto-stored": "summary",
}

# Tags that suggest the memory involves the human
YOUR_HUMAN_TAGS = {"the human", "us", "origin", "valentine", "bonding", "trust", "support", "care"}

# Tags that suggest the memory was auto-stored during wakeup
WAKEUP_TAGS = {"journal", "auto-stored"}

# Tags that suggest higher significance
HIGH_SIGNIFICANCE_TAGS = {"origin", "milestone", "identity", "core", "us", "valentine"}


def infer_source(memory):
    """Infer the source of a v1 memory from its tags."""
    tags = set(memory.get("metadata", {}).get("tags", []))
    if tags & WAKEUP_TAGS:
        return "wakeup"
    if "conversation-summary" in tags:
        return "conversation"
    if "explicit-request" in tags:
        return "signal"
    return "manual"


def infer_contact(memory):
    """Infer the contact from tags."""
    tags = set(memory.get("metadata", {}).get("tags", []))
    content_lower = memory["content"].lower()
    if "the human" in tags or "the human" in content_lower:
        return "the human"
    if "contact2" in tags or "contact2" in content_lower:
        return "contact2"
    return None


def infer_significance(memory):
    """Infer initial significance from tags. Default 3."""
    tags = set(memory.get("metadata", {}).get("tags", []))
    if tags & HIGH_SIGNIFICANCE_TAGS:
        return 4
    return 3


def convert_tags_to_context(tags):
    """Convert v1 tags to v2 context using the tag map."""
    context = []
    for tag in tags:
        mapped = TAG_MAP.get(tag.lower(), tag.lower())
        if mapped not in context:
            context.append(mapped)
    return context


def migrate_memory(v1_memory):
    """Convert a single v1 memory to v2 schema."""
    content = v1_memory["content"]
    timestamp = v1_memory.get("timestamp", datetime.now().isoformat())
    tags = v1_memory.get("metadata", {}).get("tags", [])

    # Generate v2 ID
    hash_input = (content + timestamp).encode('utf-8')
    memory_id = "mem_" + hashlib.md5(hash_input).hexdigest()[:6]

    # Convert
    source = infer_source(v1_memory)
    contact = infer_contact(v1_memory)
    significance = infer_significance(v1_memory)
    context = convert_tags_to_context(tags)

    v2_memory = {
        "id": memory_id,
        "content": content,
        "context": context,
        "date": timestamp[:10],
        "created_at": timestamp,
        "source": source,
        "contact": contact,
        "likert": {
            "intensity": 3,
            "valence": 3,
            "significance": significance
        },
        "review_history": [],
        "status": "active",
        "decay_eligible": significance < 4,
        "schema_refs": []
    }

    return v2_memory


def main():
    execute = "--execute" in sys.argv

    if not MEMORY_STORE.exists():
        print("Error: memory_store.json not found")
        sys.exit(1)

    with open(MEMORY_STORE) as f:
        v1_memories = json.load(f)

    # Check if already migrated
    if v1_memories and isinstance(v1_memories[0].get("id"), str):
        print("Memory store appears to already be v2 format. Aborting.")
        sys.exit(0)

    print(f"=== Memory v1 -> v2 Migration ===")
    print(f"Total memories: {len(v1_memories)}")
    print()

    # Convert all
    v2_memories = []
    source_counts = {}
    contact_counts = {}
    sig_counts = {3: 0, 4: 0}

    for v1 in v1_memories:
        v2 = migrate_memory(v1)
        v2_memories.append(v2)

        source_counts[v2["source"]] = source_counts.get(v2["source"], 0) + 1
        if v2["contact"]:
            contact_counts[v2["contact"]] = contact_counts.get(v2["contact"], 0) + 1
        sig_counts[v2["likert"]["significance"]] = sig_counts.get(v2["likert"]["significance"], 0) + 1

    # Check for ID collisions
    ids = [m["id"] for m in v2_memories]
    unique_ids = set(ids)
    if len(ids) != len(unique_ids):
        collisions = len(ids) - len(unique_ids)
        print(f"Warning: {collisions} ID collisions detected. Adding suffixes...")
        seen = {}
        for m in v2_memories:
            if m["id"] in seen:
                seen[m["id"]] += 1
                m["id"] = m["id"] + str(seen[m["id"]])
            else:
                seen[m["id"]] = 0

    print(f"Migration summary:")
    print(f"  Sources: {source_counts}")
    print(f"  Contacts: {contact_counts}")
    print(f"  Significance: {sig_counts}")
    print(f"  Protected (sig>=4): {sig_counts.get(4, 0)}")
    print()

    # Show sample
    print(f"Sample converted memory:")
    sample = v2_memories[0]
    print(f"  v1 ID: {v1_memories[0]['id']}")
    print(f"  v2 ID: {sample['id']}")
    print(f"  Content: {sample['content'][:80]}...")
    print(f"  Context: {sample['context']}")
    print(f"  Source: {sample['source']}, Contact: {sample['contact']}")
    print(f"  Likert: I={sample['likert']['intensity']} V={sample['likert']['valence']} S={sample['likert']['significance']}")
    print()

    if not execute:
        print("DRY RUN — no changes made. Run with --execute to migrate.")
        return

    # Backup
    print("Creating backups...")
    shutil.copy2(MEMORY_STORE, BACKUP_STORE)
    print(f"  Backed up {MEMORY_STORE.name} -> {BACKUP_STORE.name}")
    if EMBEDDINGS_FILE.exists():
        shutil.copy2(EMBEDDINGS_FILE, BACKUP_EMBEDDINGS)
        print(f"  Backed up {EMBEDDINGS_FILE.name} -> {BACKUP_EMBEDDINGS.name}")

    # Write migrated store
    print("Writing migrated memory store...")
    with open(MEMORY_STORE, 'w') as f:
        json.dump(v2_memories, f, indent=2)
    print(f"  Wrote {len(v2_memories)} v2 memories")

    # Embeddings stay the same (content unchanged, vectors still valid)
    print(f"  Embeddings unchanged (content preserved, vectors valid)")

    print()
    print(f"Migration complete!")
    print(f"  {len(v2_memories)} memories converted to v2 schema")
    print(f"  Backups at: {BACKUP_STORE.name}, {BACKUP_EMBEDDINGS.name}")
    print(f"  Restart the companion-memory PM2 process to pick up changes")


if __name__ == "__main__":
    main()
