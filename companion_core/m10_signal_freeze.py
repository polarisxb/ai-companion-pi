"""M10.5 Signal chat final freeze gate.

Read-only. Verifies that M10.1-M10.4 evidence still passes, upstream M7/M8/M9
freezes are intact, the service artifact is bounded and reversible, and live
attempt records respect every chat boundary. A passing freeze recommends
``m10_signal_chat_frozen`` and closes M10 before any voice, camera, or
hardware-body milestone opens.
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

READY_RECOMMENDATION = "m10_signal_chat_frozen"
EXPECTED_SOURCE_REPORTS = (
    ("m10_signal_dry_run_report.json", "M10.1", "m10_signal_dry_run_ready"),
    ("m10_signal_trial_report.json", "M10.2", "m10_signal_trial_ready"),
    ("m10_signal_activation_report.json", "M10.3", "m10_signal_activation_ready"),
    ("m10_signal_observation_report.json", "M10.4", "m10_signal_observation_ready"),
)
CONFIRM_FLAG = "--confirm-real-signal-send"


@dataclass
class M10SignalFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m10_signal_freeze(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
) -> M10SignalFreezeResult:
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
    stages.append(_upstream_freeze_stage(upstream))

    activation_report = reports.get("M10.3")
    stages.append(_service_artifact_stage(activation_report))

    attempts = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    # M10 freezes the Signal inbound chat contract; outbound records are M11's
    # scope and feishu-channel records are M13's.
    live_attempts = [
        attempt for attempt in attempts
        if attempt.get("mode") in ("live", "trial")
        and attempt.get("direction", "inbound") == "inbound"
        and attempt.get("channel", "signal") == "signal"
    ]
    stages.append(_chat_boundary_stage(live_attempts))

    observation_report = reports.get("M10.4")
    stages.append(_pause_rollback_stage(activation_report, observation_report))
    stages.append(_static_boundary_stage(paths, reports.get("M10.1")))
    stages.append(_freeze_runtime_boundary_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    service = (activation_report or {}).get("service") if isinstance((activation_report or {}).get("service"), dict) else {}
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M10.5",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M10 signal chat final freeze",
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
            "live_attempts_observed": len(live_attempts),
            "replied_observed": sum(1 for attempt in live_attempts if attempt.get("decision") == "replied"),
            "failed_observed": sum(1 for attempt in live_attempts if attempt.get("decision") == "failed"),
            "pause_drill_ready": _pause_drill_ready(observation_report),
            "rollback_documented": bool(service.get("rollback_command")),
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "signal_chat_ready": ok,
            "service_reversible": bool(service.get("rollback_command")),
        },
        "boundaries": {
            "service_mutated_by_freeze": False,
            "scheduler_mutated_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "signal_send_requested_by_freeze": False,
            "proactive_outbound_sent": False,
            "raw_provider_payload_stored": False,
            "raw_signal_envelope_stored": False,
            "semantic_shadow_authority_promoted": False,
            "memory_authority_expanded": False,
            "voice_camera_hardware_activation_allowed": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_signal_chat": f"touch {paths.signal_chat_pause_flag}",
            "rollback_signal_chat": service.get("rollback_command") or "see m10_signal_activation_report.json",
        },
    }
    return M10SignalFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m10_signal_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m10_signal_freeze_report.json"
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


def _upstream_freeze_stage(upstream: dict) -> dict:
    if upstream.get("ok") is True:
        return _stage("upstream_freezes_intact", True, "M7/M8/M9 freezes are still intact")
    broken = [
        name
        for name, snapshot in (upstream.get("reports") or {}).items()
        if not snapshot.get("ok")
    ]
    return _stage("upstream_freezes_intact", False, f"upstream freeze evidence broken: {broken}")


def _service_artifact_stage(activation_report: dict | None) -> dict:
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
        if not service.get("pause_flag_path"):
            problems.append("service pause flag path is missing")
    return _stage(
        "service_artifact_bounded_and_reversible",
        not problems,
        "service artifact is bounded, observable, and reversible" if not problems else "; ".join(problems),
    )


def _chat_boundary_stage(live_attempts: list[dict]) -> dict:
    problems = []
    for attempt in live_attempts:
        boundaries = attempt.get("boundaries") if isinstance(attempt.get("boundaries"), dict) else {}
        for key in (
            "wake_cycle_run",
            "scheduler_mutated",
            "proactive_outbound_sent",
            "raw_provider_payload_stored",
            "raw_signal_envelope_stored",
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
        f"{len(live_attempts)} live/trial attempts preserve every chat boundary",
    )


def _pause_rollback_stage(activation_report: dict | None, observation_report: dict | None) -> dict:
    problems = []
    service = (activation_report or {}).get("service") if isinstance((activation_report or {}).get("service"), dict) else {}
    if not service.get("rollback_command"):
        problems.append("rollback command is not documented in activation evidence")
    if not _pause_drill_ready(observation_report):
        problems.append("observation pause drill is not ready")
    return _stage(
        "pause_and_rollback_ready",
        not problems,
        "pause and rollback are documented and drilled" if not problems else "; ".join(problems),
    )


def _static_boundary_stage(paths: CompanionPaths, dry_run_report: dict | None) -> dict:
    problems = []
    runner_script = paths.home / "scripts" / "run_m10_signal_chat.py"
    if not runner_script.exists():
        problems.append("scripts/run_m10_signal_chat.py is missing")
    elif CONFIRM_FLAG not in runner_script.read_text():
        problems.append(f"chat runner no longer requires {CONFIRM_FLAG}")
    stages = (dry_run_report or {}).get("stages") if isinstance((dry_run_report or {}).get("stages"), list) else []
    guard_passed = any(
        isinstance(stage, dict) and stage.get("name") == "real_mode_guard" and stage.get("status") == "pass"
        for stage in stages
    )
    if not guard_passed:
        problems.append("M10.1 real_mode_guard stage did not pass")
    return _stage(
        "static_boundary",
        not problems,
        "confirmation gate and scheduler boundaries hold in source" if not problems else "; ".join(problems),
    )


def _freeze_runtime_boundary_stage() -> dict:
    return _stage(
        "freeze_runtime_boundary",
        True,
        "freeze is read-only: no sends, provider calls, service or scheduler mutation",
        details={
            "provider_calls": 0,
            "signal_send_requested": False,
            "service_mutated": False,
            "scheduler_mutated": False,
        },
    )


def _pause_drill_ready(observation_report: dict | None) -> bool:
    drill = (observation_report or {}).get("pause_drill")
    return isinstance(drill, dict) and drill.get("ready") is True


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


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
