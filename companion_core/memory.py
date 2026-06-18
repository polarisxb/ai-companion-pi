"""Small JSON memory adapter for the internal life-loop milestone."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class MemoryEntry:
    content: str
    source: str = "self"
    context: list[str] | None = None
    intensity: int = 3
    valence: int = 3
    significance: int = 3
    memory_type: str = "semantic"
    source_type: str = "model"
    authority: str = "model_proposed"
    prompt_eligible: bool = False
    evidence_refs: list[dict] | None = None


PROMPT_MEMORY_TYPES = {"semantic", "procedural"}
PROMPT_AUTHORITIES = {
    "user_asserted",
    "system_config",
    "evaluator_approved",
    "derived_summary",
}
LEGACY_PROMPT_SOURCES = {"human", "user", "manual", "system"}


class JsonMemoryStore:
    """V2-schema-compatible JSON store without embedding dependencies."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.mode = "json"
        self.last_write_results: list[dict] = []

    def reset_write_results(self) -> None:
        self.last_write_results = []

    @contextmanager
    def write_lock(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.storage_path.with_name("memory_store.lock")
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.storage_path.read_text())
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid memory store JSON: {self.storage_path}") from exc

    def save(self, memories: list[dict]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(memories, indent=2))
        tmp_path.replace(self.storage_path)

    def recent(self, limit: int = 5) -> list[dict]:
        memories = self.load()
        return sorted(
            memories,
            key=lambda item: item.get("created_at", item.get("timestamp", "")),
            reverse=True,
        )[:limit]

    def recent_for_context(self, limit: int = 5) -> list[dict]:
        memories = [
            memory for memory in self.load()
            if _is_prompt_eligible_memory(memory)
        ]
        return sorted(
            memories,
            key=lambda item: item.get("created_at", item.get("timestamp", "")),
            reverse=True,
        )[:limit]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        query_terms = [term for term in query.lower().split() if term]
        scored = []
        for memory in self.load():
            content = memory.get("content", "")
            haystack = content.lower()
            score = sum(1 for term in query_terms if term in haystack)
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [memory for _, memory in scored[:limit]]

    def store(
        self,
        entry: MemoryEntry,
        *,
        accepted_for_context: bool = False,
        source_event_id: str | None = None,
    ) -> dict:
        with self.write_lock():
            memories = self.load()
            now = datetime.now().isoformat()
            hash_input = (entry.content + now + uuid.uuid4().hex).encode("utf-8")
            prompt_eligible = bool(entry.prompt_eligible and accepted_for_context)
            memory = {
                "id": "mem_" + hashlib.sha256(hash_input).hexdigest()[:12],
                "content": entry.content,
                "context": entry.context or [],
                "date": now[:10],
                "created_at": now,
                "source": entry.source,
                "memory_type": entry.memory_type,
                "source_type": entry.source_type,
                "authority": entry.authority,
                "prompt_eligible": prompt_eligible,
                "evidence_refs": entry.evidence_refs or [],
                "contact": None,
                "likert": {
                    "intensity": max(1, min(5, entry.intensity)),
                    "valence": max(1, min(5, entry.valence)),
                    "significance": max(1, min(5, entry.significance)),
                },
                "review_history": [],
                "status": "active",
                "decay_eligible": entry.significance < 4,
                "schema_refs": [],
                "accepted_for_context": prompt_eligible,
            }
            if source_event_id:
                memory["source_event_id"] = source_event_id
            if prompt_eligible:
                memory["quality_gate"] = "accepted"
            memories.append(memory)
            self.save(memories)
            self.last_write_results.append({
                "backend": "json",
                "status": "completed",
                "id": memory["id"],
            })
        return memory


class SemanticFirstMemoryStore:
    """Semantic write path with JSON-compatible fallback.

    The semantic memory implementation stores v2 memories in the same
    memory_store.json file and maintains embeddings beside it. A successful
    semantic write is therefore also a JSON-compatible write.
    """

    def __init__(self, storage_path: Path, semantic_factory=None):
        self.storage_path = storage_path
        self.json_store = JsonMemoryStore(storage_path)
        self.semantic_factory = semantic_factory
        self._semantic_store_instance = None
        self.mode = "semantic-first"
        self.last_write_results: list[dict] = []

    def reset_write_results(self) -> None:
        self.last_write_results = []
        self.json_store.reset_write_results()

    def load(self) -> list[dict]:
        return self.json_store.load()

    def recent(self, limit: int = 5) -> list[dict]:
        return self.json_store.recent(limit)

    def recent_for_context(self, limit: int = 5) -> list[dict]:
        return self.json_store.recent_for_context(limit)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        return self.json_store.search(query, limit)

    def store(
        self,
        entry: MemoryEntry,
        *,
        accepted_for_context: bool = False,
        source_event_id: str | None = None,
    ) -> dict:
        try:
            metadata = _entry_metadata(
                entry,
                accepted_for_context=accepted_for_context,
                source_event_id=source_event_id,
            )
            memory = self._get_semantic_store().store_memory(
                content=entry.content,
                context=entry.context,
                intensity=entry.intensity,
                valence=entry.valence,
                significance=entry.significance,
                source=entry.source,
                metadata=metadata,
            )
            memory.update(metadata)
            semantic_store = self._get_semantic_store()
            if hasattr(semantic_store, "save"):
                semantic_store.save()
            self.last_write_results.append({
                "backend": "semantic",
                "status": "completed",
                "id": memory["id"],
            })
            self.last_write_results.append({
                "backend": "json-compatible",
                "status": "shared",
                "id": memory["id"],
            })
            return memory
        except Exception as exc:
            self.last_write_results.append({
                "backend": "semantic",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
            memory = self.json_store.store(
                entry,
                accepted_for_context=accepted_for_context,
                source_event_id=source_event_id,
            )
            self.last_write_results.extend(self.json_store.last_write_results)
            return memory

    def _get_semantic_store(self):
        if self._semantic_store_instance is not None:
            return self._semantic_store_instance
        if self.semantic_factory:
            self._semantic_store_instance = self.semantic_factory(self.storage_path)
            return self._semantic_store_instance

        semantic_path = self.storage_path.parent / "semantic_memory.py"
        if not semantic_path.exists():
            semantic_path = Path(__file__).resolve().parents[1] / "memory-server" / "semantic_memory.py"
        memory_dir = str(semantic_path.parent)
        if memory_dir not in sys.path:
            sys.path.insert(0, memory_dir)
        spec = importlib.util.spec_from_file_location("companion_semantic_memory", semantic_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load semantic memory module: {semantic_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._semantic_store_instance = module.SemanticMemoryStore(self.storage_path)
        return self._semantic_store_instance


def _entry_metadata(
    entry: MemoryEntry,
    *,
    accepted_for_context: bool,
    source_event_id: str | None,
) -> dict:
    prompt_eligible = bool(entry.prompt_eligible and accepted_for_context)
    metadata = {
        "memory_type": entry.memory_type,
        "source_type": entry.source_type,
        "authority": entry.authority,
        "prompt_eligible": prompt_eligible,
        "accepted_for_context": prompt_eligible,
        "evidence_refs": entry.evidence_refs or [],
    }
    if source_event_id:
        metadata["source_event_id"] = source_event_id
    if prompt_eligible:
        metadata["quality_gate"] = "accepted"
    return metadata


def _is_prompt_eligible_memory(memory: dict) -> bool:
    if memory.get("status", "active") != "active":
        return False

    if _has_authority_metadata(memory):
        return (
            memory.get("prompt_eligible") is True
            and memory.get("memory_type") in PROMPT_MEMORY_TYPES
            and memory.get("authority") in PROMPT_AUTHORITIES
        )

    return (
        memory.get("accepted_for_context") is True
        and str(memory.get("source", "")).lower() in LEGACY_PROMPT_SOURCES
    )


def _has_authority_metadata(memory: dict) -> bool:
    return any(
        key in memory
        for key in ("memory_type", "source_type", "authority", "prompt_eligible")
    )
