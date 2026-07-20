"""M12.1 semantic retrieval readiness audit (read-only).

Audits everything semantic retrieval depends on without writing anything:
authoritative store integrity, prompt-eligible census, config validity,
embedding backend probe, derived index coverage/staleness, and the M3.23
semantic shadow telemetry from recent wake events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .events import load_wake_events
from .memory import JsonMemoryStore, _is_prompt_eligible_memory
from .paths import CompanionPaths
from .semantic_retrieval import (
    SemanticRetrievalConfigError,
    create_embedding_backend,
    load_semantic_index,
    load_semantic_retrieval_config,
    summarize_index_coverage,
)

READY_RECOMMENDATION = "m12_semantic_readiness_ready"


@dataclass
class M12SemanticReadinessResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m12_semantic_readiness(
    paths: CompanionPaths,
    *,
    backend=None,
    require_index: bool = False,
    now: datetime | None = None,
) -> M12SemanticReadinessResult:
    current = now or datetime.now()
    stages: list[dict] = []

    config = None
    try:
        config = load_semantic_retrieval_config(paths)
        stages.append(_stage(
            "config_valid",
            True,
            f"config loaded (enabled={config.enabled}, backend={config.backend}, model={config.resolved_model()})",
        ))
    except SemanticRetrievalConfigError as exc:
        stages.append(_stage("config_valid", False, str(exc)))

    memories: list[dict] = []
    census = {"memories_total": 0, "prompt_eligible": 0, "quarantined_or_proposal": 0}
    try:
        memories = JsonMemoryStore(paths.memory_store).load()
        census["memories_total"] = len(memories)
        census["prompt_eligible"] = sum(1 for memory in memories if _is_prompt_eligible_memory(memory))
        census["quarantined_or_proposal"] = census["memories_total"] - census["prompt_eligible"]
        stages.append(_stage(
            "store_integrity",
            True,
            f"{census['memories_total']} memories loaded; {census['prompt_eligible']} prompt-eligible",
        ))
    except ValueError as exc:
        stages.append(_stage("store_integrity", False, str(exc)))

    backend_probe = {"ok": False, "backend": None, "model": None, "dims": 0}
    if config is not None:
        try:
            backend = backend or create_embedding_backend(config)
            vector = backend.embed(["readiness probe 探针"])[0]
            backend_probe.update(
                ok=bool(vector),
                backend=backend.name,
                model=backend.model_name,
                dims=len(vector),
            )
            stages.append(_stage(
                "backend_probe",
                bool(vector),
                f"backend '{backend.name}' embedded a probe ({len(vector)} dims)",
            ))
        except Exception as exc:  # noqa: BLE001 - probe failures become stage evidence.
            backend_probe["error"] = f"{type(exc).__name__}: {exc}"
            stages.append(_stage("backend_probe", False, backend_probe["error"]))
    else:
        stages.append(_stage("backend_probe", False, "config must load before the backend probe"))

    index = load_semantic_index(paths)
    index_summary = _index_summary(index, config, memories)
    if require_index:
        stages.append(_stage(
            "index_coverage",
            index_summary["ok"],
            index_summary["message"],
        ))
    else:
        stages.append(_stage(
            "index_coverage_visibility",
            True,
            f"informational before backfill: {index_summary['message']}",
        ))

    shadow_summary = _shadow_telemetry(paths)
    stages.append(_stage(
        "shadow_telemetry_visibility",
        True,
        (
            "informational: semantic shadow stays isolated telemetry "
            f"(events={shadow_summary['events_with_shadow']}, failed={shadow_summary['failed']})"
        ),
    ))

    stages.append(_stage(
        "readiness_runtime_boundary",
        True,
        "readiness audit is read-only: no index writes, no store writes, no provider calls",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M12.1",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M12 semantic retrieval readiness audit",
            "readonly": True,
            "require_index": require_index,
        },
        "census": census,
        "backend_probe": backend_probe,
        "semantic_index": index_summary,
        "semantic_shadow": shadow_summary,
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
    return M12SemanticReadinessResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m12_semantic_readiness_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m12_semantic_readiness_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _index_summary(index: dict | None, config, memories: list[dict]) -> dict:
    eligible = [memory for memory in memories if _is_prompt_eligible_memory(memory)]
    return summarize_index_coverage(index, config, eligible)


def _shadow_telemetry(paths: CompanionPaths, limit: int = 50) -> dict:
    try:
        events = load_wake_events(paths.wake_events_file)
    except Exception:  # noqa: BLE001 - telemetry is informational only.
        events = []
    recent = events[-limit:]
    summary = {"events_scanned": len(recent), "events_with_shadow": 0, "succeeded": 0, "failed": 0}
    for event in recent:
        shadow = event.get("semantic_shadow")
        if not isinstance(shadow, dict):
            continue
        summary["events_with_shadow"] += 1
        summary["succeeded"] += int(shadow.get("succeeded") or 0)
        summary["failed"] += int(shadow.get("failed") or 0)
    return summary


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}
