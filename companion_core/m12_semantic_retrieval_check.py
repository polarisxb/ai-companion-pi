"""M12.2 semantic retrieval behavior gate.

Proves, in an isolated smoke home with the deterministic hashing backend,
that semantic ranking finds meaning-adjacent memories lexical scoring misses,
that M8 policy filters stay in charge at any similarity, that every failure
mode falls back deterministically, and that retrieval never writes. No
provider, no signal-cli, no real home mutation beyond the report/evidence.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .m12_semantic_backfill import run_m12_semantic_backfill
from .memory import JsonMemoryStore
from .memory_retrieval import assemble_dialogue_memory_context
from .paths import CompanionPaths
from .semantic_retrieval import (
    HashingEmbeddingBackend,
    SemanticRetrievalConfig,
    load_semantic_index,
    save_semantic_index,
)

READY_RECOMMENDATION = "m12_semantic_retrieval_ready"

# Fixture texts avoid lexical style/project bonus words so the baseline ranks
# purely by recency and the semantic gain is attributable to similarity alone.
TARGET_MEMORY = "海边散步让人放松，那天的浪声一直记得。"
DECOY_MEMORY = "今天整理了房间，把书架擦干净了。"
QUARANTINE_MEMORY = "想去海边玩"
SEMANTIC_QUERY = "想去海边玩"


class FailingEmbeddingBackend:
    name = "hashing"
    model_name = "hashing-v1"
    dims = 0

    def embed(self, texts):
        raise RuntimeError("embedding backend intentionally unavailable")


@dataclass
class M12SemanticRetrievalCheckResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m12_semantic_retrieval_check(paths: CompanionPaths, *, now: datetime | None = None) -> M12SemanticRetrievalCheckResult:
    current = now or datetime.now()
    stages: list[dict] = []
    scenarios: dict = {}

    with tempfile.TemporaryDirectory(prefix="m12-semantic-smoke-") as smoke_dir:
        smoke_paths = CompanionPaths(Path(smoke_dir))
        smoke_paths.ensure_runtime_dirs()
        _write_fixture_memories(smoke_paths)
        config = SemanticRetrievalConfig(
            enabled=True,
            backend="hashing",
            min_similarity=0.05,
            semantic_scale=10,
        )
        backend = HashingEmbeddingBackend()

        backfill = run_m12_semantic_backfill(smoke_paths, backend=backend)
        stages.append(_stage(
            "smoke_backfill",
            backfill.ok,
            "hashing index built in the smoke home" if backfill.ok else f"backfill failed: {backfill.errors}",
        ))

        if backfill.ok:
            baseline = assemble_dialogue_memory_context(
                smoke_paths,
                SEMANTIC_QUERY,
                semantic_config=SemanticRetrievalConfig(enabled=False),
            )
            semantic = assemble_dialogue_memory_context(
                smoke_paths,
                SEMANTIC_QUERY,
                semantic_config=config,
                semantic_backend=backend,
            )
            scenarios["baseline"] = baseline.to_dict()
            scenarios["semantic"] = semantic.to_dict()
            stages.append(_semantic_gain_stage(baseline, semantic))
            stages.append(_policy_immunity_stage(smoke_paths, config, backend))
            stages.append(_fallback_stage(smoke_paths, config, backend))
            stages.append(_readonly_stage(smoke_paths, config, backend))
        else:
            for name in ("semantic_gain", "policy_immunity", "deterministic_fallback", "retrieval_readonly"):
                stages.append(_stage(name, False, "skipped because the smoke backfill failed"))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M12.2",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M12 semantic retrieval behavior gate",
            "backend": "hashing",
            "smoke_home_isolated": True,
            "provider_calls": 0,
        },
        "scenarios": scenarios,
        "boundaries": {
            "json_store_remains_authoritative": True,
            "retrieval_writes_index": False,
            "policy_filters_before_ranking": True,
            "proposal_or_quarantine_prompt_authority": False,
            "semantic_shadow_authority_promoted": False,
            "provider_generation_requested": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M12SemanticRetrievalCheckResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m12_semantic_retrieval_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m12_semantic_retrieval_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _write_fixture_memories(paths: CompanionPaths) -> None:
    def row(memory_id: str, content: str, *, eligible: bool, created_at: str) -> dict:
        return {
            "id": memory_id,
            "content": content,
            "context": [],
            "date": created_at[:10],
            "created_at": created_at,
            "source": "human",
            "memory_type": "semantic",
            "source_type": "user",
            "authority": "user_asserted" if eligible else "model_proposed",
            "prompt_eligible": eligible,
            "accepted_for_context": eligible,
            "evidence_refs": [],
            "status": "active",
            "schema_refs": [],
        }

    JsonMemoryStore(paths.memory_store).save([
        row("mem_target", TARGET_MEMORY, eligible=True, created_at="2026-07-01T10:00:00"),
        row("mem_decoy", DECOY_MEMORY, eligible=True, created_at="2026-07-02T10:00:00"),
        row("mem_quarantine", QUARANTINE_MEMORY, eligible=False, created_at="2026-07-03T10:00:00"),
    ])


def _semantic_gain_stage(baseline, semantic) -> dict:
    problems = []
    baseline_first = (baseline.memories or [{}])[0].get("id")
    if baseline_first == "mem_target":
        problems.append("lexical baseline already ranks the target first; fixture no longer proves semantic gain")
    semantic_first = (semantic.memories or [{}])[0].get("id")
    if semantic_first != "mem_target":
        problems.append(f"semantic ranking should surface mem_target first, got {semantic_first}")
    if semantic.semantic.get("status") != "applied":
        problems.append(f"semantic status should be applied, got {semantic.semantic.get('status')}")
    if not semantic.semantic.get("scored"):
        problems.append("semantic ranking scored nothing")
    target = next((item for item in semantic.retrieved if item.memory.get("id") == "mem_target"), None)
    if target is None or not any(str(reason).startswith("semantic_similarity:") for reason in target.reasons):
        problems.append("target memory is missing its semantic_similarity reason")
    if problems:
        return _stage("semantic_gain", False, "; ".join(problems))
    return _stage(
        "semantic_gain",
        True,
        "semantic ranking surfaced a meaning-adjacent memory that lexical scoring missed",
    )


def _policy_immunity_stage(smoke_paths: CompanionPaths, config, backend) -> dict:
    # Force the quarantined memory into the index with a perfect-match vector:
    # even then, the policy filter must keep it out of retrieval.
    index = load_semantic_index(smoke_paths)
    quarantine_vector = backend.embed([QUARANTINE_MEMORY])[0]
    index["entries"]["mem_quarantine"] = {
        "content_hash": _content_hash(QUARANTINE_MEMORY),
        "vector": quarantine_vector,
    }
    save_semantic_index(smoke_paths, index)

    result = assemble_dialogue_memory_context(
        smoke_paths,
        SEMANTIC_QUERY,
        semantic_config=config,
        semantic_backend=backend,
    )
    retrieved_ids = {memory.get("id") for memory in result.memories}
    filtered_ids = {item.get("id") for item in result.filtered}
    problems = []
    if "mem_quarantine" in retrieved_ids:
        problems.append("quarantined memory entered retrieval despite policy filters")
    if "mem_quarantine" not in filtered_ids:
        problems.append("quarantined memory is missing from the filtered audit list")
    if problems:
        return _stage("policy_immunity", False, "; ".join(problems))
    return _stage(
        "policy_immunity",
        True,
        "identical-content quarantined memory stays excluded at similarity 1.0",
    )


def _fallback_stage(smoke_paths: CompanionPaths, config, backend) -> dict:
    problems = []
    disabled = assemble_dialogue_memory_context(
        smoke_paths,
        SEMANTIC_QUERY,
        semantic_config=SemanticRetrievalConfig(enabled=False),
    )
    if disabled.semantic.get("status") != "disabled":
        problems.append(f"disabled config should report status disabled, got {disabled.semantic.get('status')}")

    index_path = smoke_paths.semantic_index_file
    preserved = index_path.read_text()
    index_path.unlink()
    missing = assemble_dialogue_memory_context(
        smoke_paths,
        SEMANTIC_QUERY,
        semantic_config=config,
        semantic_backend=backend,
    )
    if missing.semantic.get("status") != "index_missing_fallback":
        problems.append(f"missing index should fall back, got {missing.semantic.get('status')}")
    if not missing.memories:
        problems.append("missing-index fallback must still return lexical results")
    index_path.write_text(preserved)

    unavailable = assemble_dialogue_memory_context(
        smoke_paths,
        SEMANTIC_QUERY,
        semantic_config=config,
        semantic_backend=FailingEmbeddingBackend(),
    )
    if unavailable.semantic.get("status") != "backend_unavailable_fallback":
        problems.append(
            f"failing backend should fall back, got {unavailable.semantic.get('status')}"
        )
    if not unavailable.memories:
        problems.append("backend-unavailable fallback must still return lexical results")

    if problems:
        return _stage("deterministic_fallback", False, "; ".join(problems))
    return _stage(
        "deterministic_fallback",
        True,
        "disabled config, missing index, and failing backend all degrade to lexical retrieval",
    )


def _readonly_stage(smoke_paths: CompanionPaths, config, backend) -> dict:
    store_before = _digest(smoke_paths.memory_store)
    index_before = _digest(smoke_paths.semantic_index_file)
    assemble_dialogue_memory_context(
        smoke_paths,
        SEMANTIC_QUERY,
        semantic_config=config,
        semantic_backend=backend,
    )
    problems = []
    if _digest(smoke_paths.memory_store) != store_before:
        problems.append("retrieval mutated the authoritative memory store")
    if _digest(smoke_paths.semantic_index_file) != index_before:
        problems.append("retrieval mutated the semantic index")
    if problems:
        return _stage("retrieval_readonly", False, "; ".join(problems))
    return _stage("retrieval_readonly", True, "retrieval left the store and index byte-identical")


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}
