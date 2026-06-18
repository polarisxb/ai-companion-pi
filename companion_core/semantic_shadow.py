"""Semantic memory shadow writer.

Shadow mode probes the semantic-memory path without making it authoritative for
future prompt context. It writes only to an isolated life-loop shadow store and
records event-level hashes/statuses for observability.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from .memory import MemoryEntry
from .memory_policy import PolicyDecision
from .paths import CompanionPaths
from .provenance import content_hash

FALSE_VALUES = {"0", "false", "no", "off"}
SEMANTIC_MEMORY_TYPES = {"semantic"}


class SemanticShadowWriter:
    def __init__(self, paths: CompanionPaths, *, semantic_factory=None, enabled: bool | None = None):
        self.paths = paths
        self.semantic_factory = semantic_factory
        self.enabled = semantic_shadow_enabled() if enabled is None else enabled
        self._semantic_store_instance = None

    def write_from_policy(
        self,
        decisions: list[PolicyDecision],
        *,
        event_id: str,
    ) -> dict:
        candidates = [
            decision.normalized_entry
            for decision in decisions
            if _is_shadow_candidate(decision)
        ]
        if not self.enabled:
            return _summary(
                enabled=False,
                store_path=self._relative_store_path(),
                results=[],
                skipped=len(candidates),
            )
        if not candidates:
            return _summary(
                enabled=True,
                store_path=self._relative_store_path(),
                results=[],
                skipped=0,
            )

        results = []
        try:
            store = self._get_semantic_store()
        except Exception as exc:
            error = _error_text(exc)
            return _summary(
                enabled=True,
                store_path=self._relative_store_path(),
                results=[
                    _result_for_entry(entry, status="failed", error=error)
                    for entry in candidates
                ],
                skipped=0,
            )

        for entry in candidates:
            try:
                metadata = _shadow_metadata(entry, event_id=event_id)
                memory = store.store_memory(
                    content=entry.content,
                    context=entry.context,
                    intensity=entry.intensity,
                    valence=entry.valence,
                    significance=entry.significance,
                    source=entry.source,
                    metadata=metadata,
                )
                if isinstance(memory, dict):
                    memory.update(metadata)
                if hasattr(store, "save"):
                    store.save()
                results.append(_result_for_entry(
                    entry,
                    status="completed",
                    shadow_id=memory.get("id") if isinstance(memory, dict) else None,
                ))
            except Exception as exc:
                results.append(_result_for_entry(entry, status="failed", error=_error_text(exc)))

        return _summary(
            enabled=True,
            store_path=self._relative_store_path(),
            results=results,
            skipped=0,
        )

    def _get_semantic_store(self):
        if self._semantic_store_instance is not None:
            return self._semantic_store_instance
        if self.semantic_factory:
            self._semantic_store_instance = self.semantic_factory(self.paths.semantic_shadow_store)
            return self._semantic_store_instance

        semantic_path = self.paths.memory_dir / "semantic_memory.py"
        if not semantic_path.exists():
            semantic_path = Path(__file__).resolve().parents[1] / "memory-server" / "semantic_memory.py"
        memory_dir = str(semantic_path.parent)
        if memory_dir not in sys.path:
            sys.path.insert(0, memory_dir)
        spec = importlib.util.spec_from_file_location("companion_semantic_shadow_memory", semantic_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load semantic memory module: {semantic_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.paths.semantic_shadow_dir.mkdir(parents=True, exist_ok=True)
        self._semantic_store_instance = module.SemanticMemoryStore(self.paths.semantic_shadow_store)
        return self._semantic_store_instance

    def _relative_store_path(self) -> str:
        return str(self.paths.semantic_shadow_store.relative_to(self.paths.home))


def semantic_shadow_enabled() -> bool:
    value = os.environ.get("COMPANION_SEMANTIC_SHADOW", "").strip().lower()
    return value not in FALSE_VALUES


def _is_shadow_candidate(decision: PolicyDecision) -> bool:
    entry = decision.normalized_entry
    return (
        decision.accepted
        and decision.prompt_eligible
        and entry is not None
        and entry.memory_type in SEMANTIC_MEMORY_TYPES
        and entry.prompt_eligible is True
    )


def _shadow_metadata(entry: MemoryEntry, *, event_id: str) -> dict:
    return {
        "memory_type": entry.memory_type,
        "source_type": entry.source_type,
        "authority": entry.authority,
        "prompt_eligible": False,
        "accepted_for_context": False,
        "shadow_mode": True,
        "shadow_of_prompt_eligible": True,
        "evidence_refs": entry.evidence_refs or [],
        "source_event_id": event_id,
    }


def _result_for_entry(
    entry: MemoryEntry,
    *,
    status: str,
    shadow_id: str | None = None,
    error: str | None = None,
) -> dict:
    result = {
        "status": status,
        "content_hash": content_hash(entry.content),
        "memory_type": entry.memory_type,
        "source_type": entry.source_type,
        "authority": entry.authority,
    }
    if shadow_id:
        result["shadow_id"] = shadow_id
    if error:
        result["error"] = error
    return result


def _summary(*, enabled: bool, store_path: str, results: list[dict], skipped: int) -> dict:
    return {
        "enabled": enabled,
        "store_path": store_path,
        "attempted": len(results),
        "succeeded": sum(1 for result in results if result.get("status") == "completed"),
        "failed": sum(1 for result in results if result.get("status") == "failed"),
        "skipped": skipped,
        "results": results,
    }


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
