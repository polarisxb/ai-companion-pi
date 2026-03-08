#!/usr/bin/env python3
"""
Semantic Memory Store — shared module.

Extracted from memory_server.py so that CLI tools (store_memory.py,
query_memories.py) and the MCP server all use the same class.
"""

import fcntl
import json
import os
import hashlib
import numpy as np
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from sentence_transformers import SentenceTransformer

STORAGE_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.json")
LOCK_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/memory_store.lock")
LEXICON_PATH = Path("/media/YOUR_USERNAME/CompanionHome/memory-server/lexicon.json")


@contextmanager
def memory_write_lock(timeout=10):
    """File lock for CLI writers to memory_store.json."""
    lock_fd = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


class SemanticMemoryStore:
    def __init__(self, storage_path: Path, embeddings_path: Path = None):
        self.storage_path = storage_path
        self.embeddings_path = embeddings_path or storage_path.parent / "memory_embeddings.npy"
        self.anchors_path = storage_path.parent / "likert_anchors.json"
        self.lexicon_path = storage_path.parent / "lexicon.json"
        self.memories = []
        self.embeddings = None
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.load()

    def load(self):
        if self.storage_path.exists():
            with open(self.storage_path, 'r') as f:
                self.memories = json.load(f)
        if self.embeddings_path.exists() and self.memories:
            try:
                self.embeddings = np.load(self.embeddings_path)
                if len(self.embeddings) != len(self.memories):
                    self._rebuild_embeddings()
            except Exception:
                self._rebuild_embeddings()
        elif self.memories:
            self._rebuild_embeddings()

    def save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix('.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(self.memories, f, indent=2)
        os.replace(str(tmp_path), str(self.storage_path))
        if self.embeddings is not None:
            np.save(self.embeddings_path, self.embeddings)

    def _rebuild_embeddings(self):
        if not self.memories:
            self.embeddings = None
            return
        contents = [m["content"] for m in self.memories]
        self.embeddings = self.model.encode(contents, show_progress_bar=False)
        np.save(self.embeddings_path, self.embeddings)

    def _add_embedding(self, content: str):
        new_embedding = self.model.encode([content], show_progress_bar=False)
        if self.embeddings is None:
            self.embeddings = new_embedding
        else:
            self.embeddings = np.vstack([self.embeddings, new_embedding])

    def _generate_id(self, content: str, timestamp: str) -> str:
        hash_input = (content + timestamp).encode('utf-8')
        return "mem_" + hashlib.md5(hash_input).hexdigest()[:6]

    def _is_v2(self, memory: dict) -> bool:
        return isinstance(memory.get("id"), str) and memory["id"].startswith("mem_")

    def load_anchors(self) -> dict:
        if self.anchors_path.exists():
            with open(self.anchors_path) as f:
                return json.load(f)
        return {}

    def format_anchors(self) -> str:
        anchors_data = self.load_anchors()
        anchors = anchors_data.get("anchors", {})
        lines = []
        for dim in ["intensity", "valence", "significance"]:
            if dim in anchors:
                scale = anchors[dim]
                parts = [f"{k}={v}" for k, v in sorted(scale.items())]
                lines.append(f"  {dim}: {' '.join(parts)}")
        return "\n".join(lines) if lines else "  (no anchors configured)"

    # --- Lexicon (Personal Vocabulary) ---

    def load_lexicon(self) -> dict:
        """Load the personal lexicon from disk."""
        if self.lexicon_path.exists():
            with open(self.lexicon_path, 'r') as f:
                return json.load(f)
        return {"entries": [], "version": 1, "last_updated": None}

    def save_lexicon(self, lexicon: dict):
        """Save the personal lexicon to disk."""
        lexicon["last_updated"] = datetime.now().isoformat()
        tmp_path = self.lexicon_path.with_suffix('.tmp')
        with open(tmp_path, 'w') as f:
            json.dump(lexicon, f, indent=2)
        os.replace(str(tmp_path), str(self.lexicon_path))

    def add_lexicon_entry(self, canonical: str, variants: list,
                          learned_from: str = "self", context: str = None) -> dict:
        """Add or update a vocabulary mapping in the lexicon."""
        lexicon = self.load_lexicon()
        canonical_lower = canonical.lower()

        # Check if canonical already exists — merge variants
        for entry in lexicon["entries"]:
            if entry["canonical"].lower() == canonical_lower:
                existing = set(v.lower() for v in entry["variants"])
                for v in variants:
                    if v.lower() not in existing:
                        entry["variants"].append(v)
                if context:
                    entry["context"] = context
                entry["learned_from"] = learned_from
                entry["learned_at"] = datetime.now().strftime("%Y-%m-%d")
                self.save_lexicon(lexicon)
                return entry

        # New entry
        entry = {
            "canonical": canonical,
            "variants": variants,
            "learned_from": learned_from,
            "context": context or "",
            "learned_at": datetime.now().strftime("%Y-%m-%d")
        }
        lexicon["entries"].append(entry)
        self.save_lexicon(lexicon)
        return entry

    def get_lexicon_entry(self, canonical: str = None) -> list:
        """Get one or all lexicon entries."""
        lexicon = self.load_lexicon()
        if canonical:
            canonical_lower = canonical.lower()
            return [e for e in lexicon["entries"]
                    if e["canonical"].lower() == canonical_lower]
        return lexicon["entries"]

    def expand_from_lexicon(self, query: str) -> str:
        """Expand a search query using the personal lexicon.

        If any word in the query matches a canonical term or variant,
        appends the other terms from that entry to the query.
        This is language acquisition, not a thesaurus.
        """
        lexicon = self.load_lexicon()
        entries = lexicon.get("entries", [])
        if not entries:
            return query

        query_lower = query.lower()
        query_words = set(query_lower.split())
        expansions = []

        for entry in entries:
            canonical = entry["canonical"].lower()
            variants = [v.lower() for v in entry.get("variants", [])]
            all_terms = [canonical] + variants

            # Check if any term from this entry appears in the query
            matched = False
            for term in all_terms:
                # Check both as whole word and as substring for multi-word terms
                if term in query_words or term in query_lower:
                    matched = True
                    break

            if matched:
                for other in all_terms:
                    if other not in query_lower:
                        expansions.append(other)

        if expansions:
            return query + " " + " ".join(expansions)
        return query

    def store_memory(self, content: str, context: list = None, intensity: int = 3,
                     valence: int = 3, significance: int = 3, source: str = "manual",
                     contact: str = None, metadata: dict = None):
        """Store a new v2 memory."""
        now = datetime.now().isoformat()
        memory_id = self._generate_id(content, now)

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

        # Handle legacy metadata if passed from autonomous_memory
        if metadata:
            if "tags" in metadata and not context:
                memory["context"] = metadata["tags"]
            if "source" in metadata:
                memory["source"] = metadata["source"]

        self.memories.append(memory)
        self._add_embedding(content)
        self.save()
        return memory

    def semantic_search(self, query: str, limit: int = 5, threshold: float = 0.3,
                        min_intensity: int = None, min_significance: int = None,
                        valence_range: tuple = None, status: str = "active"):
        if not self.memories or self.embeddings is None:
            return []
        # Expand query using personal lexicon before encoding
        expanded_query = self.expand_from_lexicon(query)
        query_embedding = self.model.encode([expanded_query], show_progress_bar=False)
        similarities = np.dot(self.embeddings, query_embedding.T).flatten()
        norms = np.linalg.norm(self.embeddings, axis=1) * np.linalg.norm(query_embedding)
        similarities = similarities / (norms + 1e-10)

        matches = []
        for idx, score in enumerate(similarities):
            if score <= threshold:
                continue
            mem = self.memories[idx]

            # Status filter
            if status and mem.get("status", "active") != status:
                continue

            # Likert filters (handle v1 memories gracefully)
            likert = mem.get("likert", {})
            if min_intensity and likert.get("intensity", 3) < min_intensity:
                continue
            if min_significance and likert.get("significance", 3) < min_significance:
                continue
            if valence_range:
                v = likert.get("valence", 3)
                if v < valence_range[0] or v > valence_range[1]:
                    continue

            matches.append((mem, float(score)))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:limit]

    def get_recent_memories(self, limit: int = 10, status: str = "active"):
        filtered = self.memories
        if status:
            filtered = [m for m in filtered if m.get("status", "active") == status]
        sort_key = lambda m: m.get("created_at", m.get("timestamp", ""))
        return sorted(filtered, key=sort_key, reverse=True)[:limit]

    def get_by_id(self, memory_id: str) -> dict:
        for m in self.memories:
            mid = m.get("id")
            # Handle both v2 string IDs and v1 int IDs
            if str(mid) == str(memory_id):
                return m
        return None

    def update_memory(self, memory_id: str, updates: dict):
        mem = self.get_by_id(memory_id)
        if not mem:
            return None
        for key, value in updates.items():
            if key != "id":  # Never update the ID
                mem[key] = value
        self.save()
        return mem

    def get_strongest(self, dimension: str = "significance", limit: int = 10,
                      period_days: int = None):
        filtered = [m for m in self.memories if m.get("status", "active") == "active"]
        if period_days:
            cutoff = (datetime.now() - timedelta(days=period_days)).isoformat()
            filtered = [m for m in filtered
                        if m.get("created_at", m.get("timestamp", "")) >= cutoff]
        filtered.sort(
            key=lambda m: m.get("likert", {}).get(dimension, 3),
            reverse=True
        )
        return filtered[:limit]

    def get_for_review(self, since_days: int = 30):
        cutoff = (datetime.now() - timedelta(days=since_days)).isoformat()
        return [m for m in self.memories
                if m.get("status", "active") == "active"
                and m.get("created_at", m.get("timestamp", "")) >= cutoff]

    def get_emotional_timeline(self, period_days: int = 30, dimension: str = "valence"):
        cutoff = (datetime.now() - timedelta(days=period_days)).isoformat()
        filtered = [m for m in self.memories
                    if m.get("status", "active") == "active"
                    and m.get("created_at", m.get("timestamp", "")) >= cutoff]
        sort_key = lambda m: m.get("created_at", m.get("timestamp", ""))
        filtered.sort(key=sort_key)
        return [(m.get("date", m.get("created_at", "")[:10]),
                 m.get("likert", {}).get(dimension, 3),
                 m["content"][:80]) for m in filtered]
