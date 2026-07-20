"""M13.5 Feishu chat final freeze gate (read-only).

Verifies M13.1-M13.4 evidence, upstream M7/M8/M9 freezes, a bounded
reversible service artifact, boundary-clean ``channel=feishu`` ledger
records, and the confirmation gate in source. A passing freeze recommends
``m13_feishu_chat_frozen``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_SKIP_REASONS,
    load_m10_freeze_evidence,
    load_signal_chat_attempts,
)

READY_RECOMMENDATION = "m13_feishu_chat_frozen"
CONFIRM_FLAG = "--confirm-real-feishu-send"
EXPECTED_SOURCE_REPORTS = (
    ("m13_feishu_dry_run_report.json", "M13.1", "m13_feishu_dry_run_ready"),
    ("m13_feishu_trial_report.json", "M13.2", "m13_feishu_trial_ready"),
    ("m13_feishu_activation_report.json", "M13.3", "m13_feishu_activation_ready"),
    ("m13_feishu_observation_report.json", "M13.4", "m13_feishu_observation_ready"),
)


@dataclass
class M13FeishuFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m13_feishu_freeze(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
) -> M13FeishuFreezeResult:
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

    upstream = load_m10_freeze_evidence(paths)
    if upstream.get("ok") is True:
        stages.append(_stage("upstream_freezes_intact", True, "M7/M8/M9 freezes are still intact"))
    else:
        broken = [name for name, snap in (upstream.get("reports") or {}).items() if not snap.get("ok")]
        stages.append(_stage("upstream_freezes_intact", False, f"upstream freeze evidence broken: {broken}"))

    activation_report = reports.get("M13.3")
    service = (activation_report or {}).get("service") if isinstance((activation_report or {}).get("service"), dict) else {}
    problems = []
    if not service:
        problems.append("activation report has no service payload")
    else:
        if service.get("enabled") is not True:
            problems.append("service is not enabled")
        if service.get("artifact_count") != 1:
            problems.append("service must manage exactly one artifact")
        if not service.get("rollback_command"):
            problems.append("service rollback command is missing")
    stages.append(_stage(
        "service_artifact_bounded_and_reversible",
        not problems,
        "service artifact is bounded, observable, and reversible" if not problems else "; ".join(problems),
    ))

    attempts = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    feishu_live = [
        attempt for attempt in attempts
        if attempt.get("mode") in ("live", "trial")
        and attempt.get("direction", "inbound") == "inbound"
        and attempt.get("channel") == "feishu"
    ]
    stages.append(_channel_boundary_stage(feishu_live))

    observation_report = reports.get("M13.4")
    drill = (observation_report or {}).get("pause_drill")
    drill_ready = isinstance(drill, dict) and drill.get("ready") is True
    stages.append(_stage(
        "pause_and_rollback_ready",
        bool(drill_ready and service.get("rollback_command")),
        "pause and rollback are documented and drilled"
        if drill_ready and service.get("rollback_command")
        else "pause drill or rollback command evidence is missing",
    ))

    stages.append(_static_boundary_stage(paths, reports.get("M13.1")))
    stages.append(_stage(
        "freeze_runtime_boundary",
        True,
        "freeze is read-only: no sends, provider calls, service or scheduler mutation",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M13.5",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M13 feishu chat final freeze",
            "channel": "feishu",
            "readonly": True,
        },
        "source_reports": source_reports,
        "upstream_freeze_evidence": upstream,
        "service": {
            "mechanism": service.get("mechanism"),
            "unit_name": service.get("unit_name"),
            "enabled": service.get("enabled"),
            "artifact_count": service.get("artifact_count"),
            "rollback_command": service.get("rollback_command"),
            "pause_flag_path": service.get("pause_flag_path"),
        },
        "evidence": {
            "feishu_attempts_observed": len(feishu_live),
            "replied_observed": sum(1 for attempt in feishu_live if attempt.get("decision") == "replied"),
            "failed_observed": sum(1 for attempt in feishu_live if attempt.get("decision") == "failed"),
            "pause_drill_ready": drill_ready,
            "rollback_documented": bool(service.get("rollback_command")),
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "feishu_chat_ready": ok,
            "service_reversible": bool(service.get("rollback_command")),
        },
        "boundaries": {
            "service_mutated_by_freeze": False,
            "scheduler_mutated_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "feishu_send_requested_by_freeze": False,
            "raw_provider_payload_stored": False,
            "secrets_in_reports_or_ledger": False,
            "memory_authority_expanded": False,
            "voice_camera_hardware_activation_allowed": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_feishu_chat": f"touch {paths.signal_chat_pause_flag}",
            "rollback_feishu_chat": service.get("rollback_command") or "see m13_feishu_activation_report.json",
        },
    }
    return M13FeishuFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m13_feishu_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m13_feishu_freeze_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _channel_boundary_stage(feishu_live: list[dict]) -> dict:
    problems = []
    for attempt in feishu_live:
        boundaries = attempt.get("boundaries") if isinstance(attempt.get("boundaries"), dict) else {}
        for key in (
            "wake_cycle_run",
            "scheduler_mutated",
            "proactive_outbound_sent",
            "raw_provider_payload_stored",
            "semantic_shadow_authority_promoted",
            "memory_authority_expanded",
            "voice_output",
        ):
            if boundaries.get(key) is not False:
                problems.append(f"attempt boundary {key} is not false")
        if attempt.get("decision") == "skipped" and attempt.get("skip_reason") not in SIGNAL_CHAT_SKIP_REASONS:
            problems.append(f"unknown skip reason {attempt.get('skip_reason')}")
        if "body" in attempt:
            problems.append("an attempt record stores a raw body")
    if problems:
        return _stage("chat_boundaries_preserved", False, "; ".join(sorted(set(problems))))
    return _stage(
        "chat_boundaries_preserved",
        True,
        f"{len(feishu_live)} feishu live/trial attempts preserve every chat boundary",
    )


def _static_boundary_stage(paths: CompanionPaths, dry_run_report: dict | None) -> dict:
    problems = []
    runner_script = paths.home / "scripts" / "run_m13_feishu_chat.py"
    if not runner_script.exists():
        problems.append("scripts/run_m13_feishu_chat.py is missing")
    elif CONFIRM_FLAG not in runner_script.read_text():
        problems.append(f"feishu runner no longer requires {CONFIRM_FLAG}")
    stages = (dry_run_report or {}).get("stages") if isinstance((dry_run_report or {}).get("stages"), list) else []
    guard_passed = any(
        isinstance(stage, dict) and stage.get("name") == "real_mode_guard" and stage.get("status") == "pass"
        for stage in stages
    )
    if not guard_passed:
        problems.append("M13.1 real_mode_guard stage did not pass")
    if problems:
        return _stage("static_boundary", False, "; ".join(problems))
    return _stage("static_boundary", True, "confirmation gate and scheduler boundaries hold in source")


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
