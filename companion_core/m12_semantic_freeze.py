"""M12.5 semantic retrieval final freeze gate (read-only).

Verifies M12.1-M12.4 evidence, intact M7.6/M8.7 memory-adjacent freezes,
boundary flags across the M12 reports, and the default-off config template.
A passing freeze recommends ``m12_semantic_retrieval_frozen``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths

READY_RECOMMENDATION = "m12_semantic_retrieval_frozen"
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_REPORTS = (
    ("m12_semantic_readiness_report.json", "M12.1", "m12_semantic_readiness_ready"),
    ("m12_semantic_retrieval_report.json", "M12.2", "m12_semantic_retrieval_ready"),
    ("m12_semantic_backfill_report.json", "M12.3", "m12_semantic_backfill_ready"),
    ("m12_semantic_observation_report.json", "M12.4", "m12_semantic_observation_ready"),
)
UPSTREAM_FREEZES = (
    ("m7_dialogue_freeze_report.json", "m7_text_dialogue_frozen"),
    ("m8_memory_freeze_report.json", "m8_memory_dialogue_frozen"),
)
REQUIRED_FALSE_BOUNDARIES = (
    "store_mutated",
    "provider_generation_requested",
    "wake_cycle_run",
    "scheduler_mutated",
    "semantic_shadow_authority_promoted",
)


@dataclass
class M12SemanticFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m12_semantic_freeze(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
) -> M12SemanticFreezeResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}
    reports: dict[str, dict | None] = {}

    for name, milestone, recommendation in EXPECTED_SOURCE_REPORTS:
        path = paths.life_loop_dir / name
        report = _load_report(path)
        reports[milestone] = report
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_source_report_stage(report, milestone=milestone, recommendation=recommendation))

    stages.append(_upstream_freeze_stage(paths))
    stages.append(_boundary_stage(reports))
    stages.append(_template_default_off_stage())
    stages.append(_authority_stage(reports))
    stages.append(_freeze_runtime_boundary_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    observation = reports.get("M12.4") or {}
    coverage = observation.get("index_coverage") if isinstance(observation.get("index_coverage"), dict) else {}
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M12.5",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M12 semantic retrieval final freeze",
            "readonly": True,
        },
        "source_reports": source_reports,
        "evidence": {
            "index_coverage_ratio": coverage.get("coverage_ratio"),
            "index_entries": coverage.get("entries"),
            "eligible_memories": coverage.get("eligible_memories"),
            "fallback_drilled": _stage_passed_in_report(observation, "fallback_drill"),
            "policy_immunity_proven": _stage_passed_in_report(reports.get("M12.2") or {}, "policy_immunity"),
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "semantic_retrieval_ready": ok,
            "json_store_authoritative": True,
            "index_reversible": True,
        },
        "boundaries": {
            "store_mutated_by_freeze": False,
            "index_written_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "scheduler_mutated_by_freeze": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "memory_acceptance_policy_changed": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "rollback_semantic_retrieval": (
                "set enabled=false in life-loop/semantic_retrieval_config.json "
                "or delete life-loop/semantic_index.json"
            ),
            "resync_index": ".venv/bin/python scripts/run_m12_semantic_backfill.py",
        },
    }
    return M12SemanticFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m12_semantic_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m12_semantic_freeze_report.json"
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
        if report.get("stop_reasons"):
            problems.append(f"{milestone} report has stop_reasons")
    return _stage(
        f"source_report_{milestone.lower().replace('.', '_')}",
        not problems,
        f"{milestone} evidence still passes" if not problems else "; ".join(problems),
    )


def _upstream_freeze_stage(paths: CompanionPaths) -> dict:
    problems = []
    for name, recommendation in UPSTREAM_FREEZES:
        report = _load_report(paths.life_loop_dir / name)
        if not isinstance(report, dict) or report.get("ok") is not True or report.get("recommendation") != recommendation:
            problems.append(f"{name} is not frozen ({recommendation})")
    return _stage(
        "memory_adjacent_freezes_intact",
        not problems,
        "M7.6 and M8.7 freezes are intact" if not problems else "; ".join(problems),
    )


def _boundary_stage(reports: dict) -> dict:
    problems = []
    for milestone, report in reports.items():
        boundaries = (report or {}).get("boundaries") if isinstance((report or {}).get("boundaries"), dict) else {}
        if not boundaries:
            problems.append(f"{milestone} report has no boundaries payload")
            continue
        for key in REQUIRED_FALSE_BOUNDARIES:
            if key in boundaries and boundaries[key] is not False:
                problems.append(f"{milestone} boundary {key} is not false")
        if boundaries.get("json_store_remains_authoritative") is not True:
            problems.append(f"{milestone} boundary json_store_remains_authoritative is not true")
    return _stage(
        "m12_boundaries_preserved",
        not problems,
        "all M12 reports preserve store authority and runtime boundaries" if not problems else "; ".join(problems),
    )


def _template_default_off_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "semantic_retrieval_config.template.json"
    if not template_path.exists():
        return _stage("template_default_off", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("template_default_off", False, f"template is invalid JSON: {exc.msg}")
    if payload.get("enabled") is not False:
        return _stage("template_default_off", False, "template must ship with enabled=false")
    return _stage("template_default_off", True, "config template ships disabled by default")


def _authority_stage(reports: dict) -> dict:
    retrieval_report = reports.get("M12.2") or {}
    boundaries = retrieval_report.get("boundaries") if isinstance(retrieval_report.get("boundaries"), dict) else {}
    problems = []
    if boundaries.get("policy_filters_before_ranking") is not True:
        problems.append("M12.2 did not prove policy filters run before ranking")
    if boundaries.get("proposal_or_quarantine_prompt_authority") is not False:
        problems.append("M12.2 did not keep proposals/quarantine out of prompt authority")
    return _stage(
        "memory_authority_unchanged",
        not problems,
        "memory acceptance authority is unchanged; ranking only reorders approved memories"
        if not problems
        else "; ".join(problems),
    )


def _freeze_runtime_boundary_stage() -> dict:
    return _stage(
        "freeze_runtime_boundary",
        True,
        "freeze is read-only: no store, index, provider, wake, or scheduler mutation",
    )


def _stage_passed_in_report(report: dict, stage_name: str) -> bool:
    for stage in report.get("stages", []) or []:
        if isinstance(stage, dict) and stage.get("name") == stage_name:
            return stage.get("status") == "pass"
    return False


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
            "stop_reasons": report.get("stop_reasons", []),
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
