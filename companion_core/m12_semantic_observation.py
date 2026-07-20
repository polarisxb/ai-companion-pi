"""M12.4 semantic retrieval observation gate (read-only).

Observes the enabled semantic retrieval on this machine: config sanity,
backend probe, index coverage over the authoritative store, a read-only live
retrieval probe proving which backend actually served, and a fallback drill.
Never writes the index or the store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .memory import JsonMemoryStore, _is_prompt_eligible_memory
from .memory_retrieval import assemble_dialogue_memory_context
from .paths import CompanionPaths
from .semantic_retrieval import (
    SemanticRetrievalConfig,
    SemanticRetrievalConfigError,
    create_embedding_backend,
    load_semantic_index,
    load_semantic_retrieval_config,
    summarize_index_coverage,
)

READY_RECOMMENDATION = "m12_semantic_observation_ready"
DEFAULT_PROBE_QUERY = "我们之前聊过什么让你印象深刻的事"


@dataclass
class M12SemanticObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m12_semantic_observation(
    paths: CompanionPaths,
    *,
    probe_query: str = DEFAULT_PROBE_QUERY,
    backend=None,
    now: datetime | None = None,
) -> M12SemanticObservationResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    backfill_path = paths.life_loop_dir / "m12_semantic_backfill_report.json"
    backfill_report = _load_report(backfill_path)
    source_reports["m12_semantic_backfill"] = _report_snapshot(paths, backfill_path, backfill_report)
    stages.append(_source_report_stage(
        backfill_report,
        milestone="M12.3",
        recommendation="m12_semantic_backfill_ready",
    ))

    config = None
    try:
        config = load_semantic_retrieval_config(paths)
        problems = []
        if not config.enabled:
            problems.append("semantic retrieval is not enabled on this machine")
        stages.append(_stage(
            "config_enabled",
            not problems,
            f"semantic retrieval enabled with backend={config.backend}" if not problems else "; ".join(problems),
        ))
    except SemanticRetrievalConfigError as exc:
        stages.append(_stage("config_enabled", False, str(exc)))

    memories: list[dict] = []
    try:
        memories = JsonMemoryStore(paths.memory_store).load()
    except ValueError:
        pass
    eligible = [memory for memory in memories if _is_prompt_eligible_memory(memory)]

    backend_probe = {"ok": False}
    if config is not None and config.enabled:
        try:
            backend = backend or create_embedding_backend(config)
            vector = backend.embed(["observation probe 观测探针"])[0]
            backend_probe = {"ok": bool(vector), "backend": backend.name, "model": backend.model_name, "dims": len(vector)}
            stages.append(_stage("backend_probe", bool(vector), f"backend '{backend.name}' is serving embeddings"))
        except Exception as exc:  # noqa: BLE001 - probe failures become stage evidence.
            backend_probe = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            stages.append(_stage("backend_probe", False, backend_probe["error"]))
    else:
        stages.append(_stage("backend_probe", False, "config must be enabled before the backend probe"))

    index = load_semantic_index(paths)
    coverage = summarize_index_coverage(index, config, eligible)
    stages.append(_stage(
        "index_coverage",
        coverage["ok"],
        coverage["message"],
    ))

    store_digest_before = _digest(paths.memory_store)
    index_digest_before = _digest(paths.semantic_index_file)
    probe_result: dict = {}
    if _all_pass(stages):
        result = assemble_dialogue_memory_context(
            paths,
            probe_query,
            semantic_config=config,
            semantic_backend=backend,
        )
        probe_result = {
            "query": probe_query,
            "semantic": dict(result.semantic),
            "retrieved_count": len(result.memories),
        }
        expected_status = "applied" if eligible else "no_query_or_candidates"
        status = result.semantic.get("status")
        stages.append(_stage(
            "live_retrieval_probe",
            status == expected_status,
            f"live retrieval served with status={status} (expected {expected_status})",
        ))
    else:
        stages.append(_stage("live_retrieval_probe", False, "probe skipped because prerequisites failed"))

    fallback = assemble_dialogue_memory_context(
        paths,
        probe_query,
        semantic_config=SemanticRetrievalConfig(enabled=False),
    )
    stages.append(_stage(
        "fallback_drill",
        fallback.semantic.get("status") == "disabled",
        "disabled-config drill degraded to lexical retrieval"
        if fallback.semantic.get("status") == "disabled"
        else f"fallback drill returned unexpected status {fallback.semantic.get('status')}",
    ))

    readonly_ok = (
        _digest(paths.memory_store) == store_digest_before
        and _digest(paths.semantic_index_file) == index_digest_before
    )
    stages.append(_stage(
        "observation_runtime_boundary",
        readonly_ok,
        "observation left store and index byte-identical"
        if readonly_ok
        else "observation mutated the store or index",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M12.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M12 semantic retrieval observation",
            "readonly": True,
        },
        "source_reports": source_reports,
        "backend_probe": backend_probe,
        "index_coverage": coverage,
        "live_probe": probe_result,
        "boundaries": {
            "json_store_remains_authoritative": True,
            "index_written": False,
            "store_mutated": False,
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
    return M12SemanticObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m12_semantic_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m12_semantic_observation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _source_report_stage(report: dict | None, *, milestone: str, recommendation: str) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append(f"{milestone} report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append(f"{milestone} ok is not true")
        if report.get("milestone") != milestone:
            problems.append(f"milestone is not {milestone}")
        if report.get("recommendation") != recommendation:
            problems.append(f"recommendation is not {recommendation}")
    return _stage(
        f"source_report_{milestone.lower().replace('.', '_')}",
        not problems,
        f"{milestone} evidence is ready" if not problems else "; ".join(problems),
    )


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _all_pass(stages: list[dict]) -> bool:
    return all(stage.get("status") == "pass" for stage in stages)


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}


def _load_report(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    snapshot = {"path": _relative(paths, path), "exists": path.exists(), "ok": False, "recommendation": None}
    if isinstance(report, dict):
        snapshot.update({
            "ok": report.get("ok") is True,
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
