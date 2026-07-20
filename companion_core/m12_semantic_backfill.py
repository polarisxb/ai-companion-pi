"""M12.3 semantic index backfill/sync.

Idempotent incremental derivation of the semantic index from the
authoritative JSON memory store: embed missing or stale prompt-eligible
memories, prune entries whose memory disappeared or lost eligibility, and
replace the index atomically. The store itself is never mutated; deleting
the index file is a complete rollback.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .memory import JsonMemoryStore, _is_prompt_eligible_memory
from .paths import CompanionPaths
from .semantic_retrieval import (
    SemanticRetrievalConfigError,
    content_hash,
    create_embedding_backend,
    empty_semantic_index,
    index_matches_config,
    load_semantic_index,
    load_semantic_retrieval_config,
    save_semantic_index,
)

READY_RECOMMENDATION = "m12_semantic_backfill_ready"


@dataclass
class M12SemanticBackfillResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m12_semantic_backfill(
    paths: CompanionPaths,
    *,
    backend=None,
    write_index: bool = True,
    now: datetime | None = None,
) -> M12SemanticBackfillResult:
    current = now or datetime.now()
    stages: list[dict] = []

    config = None
    try:
        config = load_semantic_retrieval_config(paths)
        stages.append(_stage(
            "config_valid",
            True,
            f"semantic retrieval config loaded (enabled={config.enabled}, backend={config.backend})",
        ))
    except SemanticRetrievalConfigError as exc:
        stages.append(_stage("config_valid", False, str(exc)))

    store = JsonMemoryStore(paths.memory_store)
    memories: list[dict] = []
    store_digest_before = _file_digest(paths.memory_store)
    try:
        memories = store.load()
        stages.append(_stage(
            "store_integrity",
            True,
            f"authoritative store loaded with {len(memories)} memories",
        ))
    except ValueError as exc:
        stages.append(_stage("store_integrity", False, str(exc)))

    counts = {
        "memories_total": len(memories),
        "prompt_eligible": 0,
        "embedded_new": 0,
        "refreshed_stale": 0,
        "unchanged": 0,
        "pruned": 0,
        "index_entries": 0,
    }
    index_payload = None
    if _all_pass(stages):
        try:
            backend = backend or create_embedding_backend(config)
            probe = backend.embed(["backfill readiness probe"])
            if not probe or not probe[0]:
                raise RuntimeError("backend returned an empty vector")
            stages.append(_stage(
                "backend_ready",
                True,
                f"embedding backend '{backend.name}' ready (model={backend.model_name})",
            ))
        except Exception as exc:  # noqa: BLE001 - backend failures become stage evidence.
            stages.append(_stage("backend_ready", False, f"{type(exc).__name__}: {exc}"))

    if _all_pass(stages):
        existing = load_semantic_index(paths)
        if not index_matches_config(existing, config):
            existing = None
        existing_entries = (existing or {}).get("entries") or {}
        eligible = [memory for memory in memories if _is_prompt_eligible_memory(memory)]
        counts["prompt_eligible"] = len(eligible)

        new_entries: dict[str, dict] = {}
        to_embed: list[tuple[str, str, str]] = []
        for memory in eligible:
            memory_id = str(memory.get("id") or "")
            if not memory_id:
                continue
            text = str(memory.get("content") or "")
            digest = content_hash(text)
            entry = existing_entries.get(memory_id)
            if isinstance(entry, dict) and entry.get("content_hash") == digest and isinstance(entry.get("vector"), list):
                new_entries[memory_id] = entry
                counts["unchanged"] += 1
                continue
            to_embed.append((memory_id, text, digest))
            if isinstance(entry, dict):
                counts["refreshed_stale"] += 1
            else:
                counts["embedded_new"] += 1

        try:
            if to_embed:
                vectors = backend.embed([text for _, text, _ in to_embed])
                for (memory_id, _, digest), vector in zip(to_embed, vectors):
                    new_entries[memory_id] = {"content_hash": digest, "vector": list(vector)}
            counts["pruned"] = len(set(existing_entries) - set(new_entries))
            counts["index_entries"] = len(new_entries)
            index_payload = empty_semantic_index(backend)
            index_payload["updated_at"] = current.isoformat()
            index_payload["entries"] = new_entries
            if index_payload["dims"] == 0 and new_entries:
                index_payload["dims"] = len(next(iter(new_entries.values()))["vector"])
            stages.append(_stage(
                "sync_execution",
                True,
                (
                    f"synced {counts['index_entries']} entr(y/ies): "
                    f"{counts['embedded_new']} new, {counts['refreshed_stale']} refreshed, "
                    f"{counts['unchanged']} unchanged, {counts['pruned']} pruned"
                ),
            ))
        except Exception as exc:  # noqa: BLE001 - embedding failures become stage evidence.
            stages.append(_stage("sync_execution", False, f"{type(exc).__name__}: {exc}"))

    if _all_pass(stages):
        if write_index:
            index_path = save_semantic_index(paths, index_payload)
            stages.append(_stage(
                "index_written",
                True,
                f"semantic index written atomically to {_relative(paths, index_path)}",
            ))
        else:
            stages.append(_stage("index_written", True, "index write skipped by request"))
    else:
        stages.append(_stage("index_written", False, "index write skipped because sync failed"))

    store_digest_after = _file_digest(paths.memory_store)
    stages.append(_stage(
        "authoritative_store_untouched",
        store_digest_before == store_digest_after,
        "memory store content is byte-identical after backfill"
        if store_digest_before == store_digest_after
        else "backfill mutated the authoritative memory store",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M12.3",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M12 semantic index backfill",
            "backend": getattr(backend, "name", None),
            "model": getattr(backend, "model_name", None),
            "write_index": write_index,
            "idempotent": True,
            "rollback": f"delete {_relative(paths, paths.semantic_index_file)}",
        },
        "counts": counts,
        "semantic_index": {
            "path": _relative(paths, paths.semantic_index_file),
            "exists": paths.semantic_index_file.exists(),
        },
        "boundaries": {
            "json_store_remains_authoritative": True,
            "store_mutated": store_digest_before != store_digest_after,
            "provider_generation_requested": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
            "semantic_shadow_authority_promoted": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M12SemanticBackfillResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m12_semantic_backfill_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m12_semantic_backfill_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _all_pass(stages: list[dict]) -> bool:
    return all(stage.get("status") == "pass" for stage in stages)


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
