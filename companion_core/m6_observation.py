"""M6.4 real-Pi field observation gate."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from .events import load_wake_events
from .m6_manual_wake import READY_RECOMMENDATION as M6_MANUAL_WAKE_READY
from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority


DEFAULT_TRIGGER_PREFIX = "m6-pi-manual-wake"
READY_RECOMMENDATION = "stable_pi_field_observed"
CONTINUE_RECOMMENDATION = "continue_pi_observation"

STALE_MANUAL_WAKE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        "not a real wake",
        "not truly awake",
        "preflight is pending",
        "configuration is not complete",
        "还不是真正的唤醒",
        "不是真正的唤醒",
        "前置配置还没完成",
        "等待基础设施就绪",
        "先跑 fake wake",
        "配置顺序是对的",
        "完成依赖安装",
    )
)


def run_m6_pi_observation_check(
    paths: CompanionPaths,
    *,
    manual_wake_report_path: str | Path | None = None,
    trigger_prefix: str = DEFAULT_TRIGGER_PREFIX,
    min_completed_events: int = 1,
) -> dict:
    """Read real-Pi M6.3 artifacts and decide whether field observation is stable.

    M6.4 is deliberately non-generative. It does not run a wake, call a model,
    mutate scheduler/system configuration, or promote semantic memory.
    """

    report_file = (
        Path(manual_wake_report_path).expanduser().resolve()
        if manual_wake_report_path
        else paths.life_loop_dir / "m6_pi_manual_wake_report.json"
    )
    manual_report, report_stage = _manual_wake_report_stage(paths, report_file)
    latest_report_event = _latest_report_event(manual_report)
    events = [
        event
        for event in load_wake_events(paths.wake_events_file)
        if str(event.get("trigger", "")).startswith(trigger_prefix)
    ]
    completed = [event for event in events if event.get("status") == "completed"]
    matching_event = _matching_event(events, latest_report_event)

    stages = [
        report_stage,
        _platform_identity_stage(manual_report),
        _observation_scope_stage(
            events,
            completed,
            trigger_prefix=trigger_prefix,
            min_completed_events=min_completed_events,
        ),
        _manual_wake_event_stage(latest_report_event, matching_event),
        _event_health_stage([matching_event] if matching_event else completed),
        _journal_presence_stage(paths, matching_event),
        _journal_m6_consistency_stage(paths, manual_report, matching_event),
        _runtime_json_stage(paths),
        _semantic_shadow_authority_stage(paths),
    ]

    stop_reasons = _stop_reasons(stages)
    pending_reasons: list[str] = []
    recommendation = _recommendation(stop_reasons, pending_reasons, manual_report)
    return {
        "ok": recommendation == READY_RECOMMENDATION,
        "milestone": "M6.4",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "name": "m6-pi-observation-gate",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
            "signal_voice_hardware_activation_allowed": False,
            "trigger_prefix": trigger_prefix,
            "min_completed_events": min_completed_events,
        },
        "source_reports": {
            "m6_pi_manual_wake": _report_snapshot(manual_report, paths, report_file),
        },
        "field_pilot": {
            "manual_wake": {
                "observed": bool(matching_event),
                "event_id": matching_event.get("id") if matching_event else None,
                "journal": matching_event.get("journal") if matching_event else None,
            },
            "observation": {
                "requested": True,
                "event_count": len(events),
                "completed_count": len(completed),
                "next_stage": "M6.5" if not stop_reasons else "M6.4",
            },
            "recovery": {"requested": False, "next_stage": "M6.5"},
            "scheduler_readiness": {"mutated": False, "readiness_stage": "M6.6"},
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": pending_reasons,
        "next_commands": {
            "m6_observation": _shell_command([
                "python3",
                "scripts/run_m6_pi_observation_check.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m6_recovery_later": "requires stable M6.4 observation report",
        },
    }


def _manual_wake_report_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage("m6_manual_wake_report", False, True, f"M6.3 report is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage("m6_manual_wake_report", False, True, f"M6.3 report is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage("m6_manual_wake_report", False, True, f"M6.3 report could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage("m6_manual_wake_report", False, True, "M6.3 report must be a JSON object")

    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    field_pilot = payload.get("field_pilot") if isinstance(payload.get("field_pilot"), dict) else {}
    manual_wake = field_pilot.get("manual_wake") if isinstance(field_pilot.get("manual_wake"), dict) else {}
    problems = []
    if payload.get("ok") is not True:
        problems.append("M6.3 report ok is not true")
    if payload.get("milestone") != "M6.3":
        problems.append("M6.3 report milestone is not M6.3")
    if payload.get("recommendation") != M6_MANUAL_WAKE_READY:
        problems.append(f"M6.3 recommendation is not {M6_MANUAL_WAKE_READY}")
    if payload.get("stop_reasons"):
        problems.append("M6.3 report has stop_reasons")
    if profile.get("real_wake_requested") is not True:
        problems.append("M6.3 did not request a real wake")
    if profile.get("provider_generation_started") is not True:
        problems.append("M6.3 did not start provider generation")
    if manual_wake.get("executed") is not True:
        problems.append("M6.3 manual wake did not execute")

    return payload, _stage(
        "m6_manual_wake_report",
        not problems,
        True,
        "M6.3 real Pi manual wake report is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(payload, paths, path),
    )


def _platform_identity_stage(report: dict | None) -> dict:
    pi_presence = report.get("pi_presence") if isinstance(report, dict) and isinstance(report.get("pi_presence"), dict) else {}
    detected = pi_presence.get("detected") is True
    return _stage(
        "platform_identity",
        detected,
        True,
        "M6.3 report was produced on a Raspberry Pi" if detected else "real Raspberry Pi evidence is missing",
        details=pi_presence,
    )


def _observation_scope_stage(
    events: list[dict],
    completed: list[dict],
    *,
    trigger_prefix: str,
    min_completed_events: int,
) -> dict:
    ok = len(completed) >= min_completed_events
    return _stage(
        "observation_scope",
        ok,
        True,
        (
            "M6 wake events found in observation scope"
            if ok
            else f"completed M6 wake events {len(completed)} < required {min_completed_events}"
        ),
        details={
            "trigger_prefix": trigger_prefix,
            "event_count": len(events),
            "completed_count": len(completed),
            "min_completed_events": min_completed_events,
        },
    )


def _manual_wake_event_stage(report_event: dict, event: dict | None) -> dict:
    problems = []
    if not report_event:
        problems.append("M6.3 report has no latest delegate event")
    if report_event and event is None:
        problems.append(f"wake event not found: {report_event.get('id')}")
    return _stage(
        "m6_manual_wake_event",
        not problems,
        True,
        "M6.3 delegate event is present in wake_events" if not problems else "; ".join(problems),
        details={
            "report_event": report_event,
            "observed_event": {
                "id": event.get("id"),
                "trigger": event.get("trigger"),
                "status": event.get("status"),
                "journal": event.get("journal"),
            } if event else None,
        },
    )


def _event_health_stage(events: list[dict]) -> dict:
    failures = [_event_failure(event) for event in events]
    failures = [failure for failure in failures if failure]
    return _stage(
        "event_health",
        not failures,
        True,
        "M6 wake event health is clean" if not failures else f"M6 wake event problems ({len(failures)})",
        details={"failures": failures},
    )


def _journal_presence_stage(paths: CompanionPaths, event: dict | None) -> dict:
    journal = event.get("journal") if isinstance(event, dict) else None
    journal_path = paths.home / journal if journal else None
    exists = journal_path.exists() if journal_path else False
    size = journal_path.stat().st_size if exists else 0
    ok = exists and size > 0
    return _stage(
        "journal_presence",
        ok,
        True,
        "M6 journal exists" if ok else "M6 journal is missing or empty",
        details={"journal": journal, "exists": exists, "size": size},
    )


def _journal_m6_consistency_stage(paths: CompanionPaths, report: dict | None, event: dict | None) -> dict:
    real_started = False
    if isinstance(report, dict):
        profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
        real_started = (
            profile.get("real_wake_requested") is True
            and profile.get("provider_generation_started") is True
        )
    journal = event.get("journal") if isinstance(event, dict) else None
    journal_text = ""
    if journal:
        try:
            journal_text = (paths.home / journal).read_text(errors="ignore")
        except OSError:
            journal_text = ""

    contradictions = []
    if real_started and journal_text:
        contradictions = [
            pattern.pattern
            for pattern in STALE_MANUAL_WAKE_PATTERNS
            if pattern.search(journal_text)
        ]
    ok = not contradictions
    return _stage(
        "journal_m6_consistency",
        ok,
        True,
        "M6 journal is consistent with a completed real manual wake"
        if ok
        else "M6 journal contradicts completed real manual wake state",
        details={"contradiction_patterns": contradictions, "journal": journal},
    )


def _runtime_json_stage(paths: CompanionPaths) -> dict:
    targets = {
        "memory_store": paths.memory_store,
        "requests_file": paths.requests_file,
        "companion_state": paths.companion_state_file,
        "context_capsule": paths.context_capsule_file,
    }
    results = {}
    problems = []
    for name, path in targets.items():
        try:
            json.loads(path.read_text())
            results[name] = {"path": _relative(paths, path), "valid_json": True}
        except FileNotFoundError:
            results[name] = {"path": _relative(paths, path), "valid_json": False, "missing": True}
            if name in {"memory_store", "requests_file"}:
                problems.append(f"{name} is missing")
        except json.JSONDecodeError as exc:
            results[name] = {"path": _relative(paths, path), "valid_json": False, "error": exc.msg}
            problems.append(f"{name} is invalid JSON")
        except OSError as exc:
            results[name] = {"path": _relative(paths, path), "valid_json": False, "error": str(exc)}
            problems.append(f"{name} could not be read")
    return _stage(
        "runtime_json_artifacts",
        not problems,
        True,
        "runtime JSON artifacts are readable" if not problems else "; ".join(problems),
        details=results,
    )


def _semantic_shadow_authority_stage(paths: CompanionPaths) -> dict:
    audit = audit_semantic_shadow_authority(paths)
    return _stage(
        "semantic_shadow_authority",
        audit.get("ok") is True,
        True,
        audit.get("message", "semantic shadow authority audit completed"),
        details=audit,
    )


def _event_failure(event: dict) -> dict | None:
    problems = []
    if event.get("status") != "completed":
        problems.append(f"status={event.get('status')!r}")
    if event.get("provider") != "deepseek":
        problems.append(f"provider={event.get('provider')!r}")
    if event.get("memory_backend") != "json":
        problems.append(f"memory_backend={event.get('memory_backend')!r}")
    quality_gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if quality_gate.get("context_eligible") is False:
        problems.append("quality gate rejected future-context writes")
    blocking = quality_gate.get("blocking_warnings")
    if isinstance(blocking, list) and blocking:
        problems.append(f"blocking quality warnings ({len(blocking)})")
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        problems.append(f"unsupported grounding claims ({_count_int(grounding.get('unsupported'))})")
    if event.get("request_errors"):
        problems.append(f"request errors ({len(event.get('request_errors', []))})")
    if any(
        isinstance(result, dict) and result.get("status") == "failed"
        for result in event.get("memory_write_results", [])
    ):
        problems.append("memory write failure")
    output_audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
    if output_audit.get("raw_output_storage") != "hash_only":
        problems.append("raw output storage is not hash-only")
    if _raw_output_stored(output_audit):
        problems.append("raw model output was stored")
    if not problems:
        return None
    return {
        "id": event.get("id"),
        "trigger": event.get("trigger"),
        "started_at": event.get("started_at"),
        "problems": problems,
    }


def _raw_output_stored(output_audit: dict) -> bool:
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _latest_report_event(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return {}
    for stage in report.get("stages", []) or []:
        if isinstance(stage, dict) and stage.get("name") == "m4_wake_trial_delegate":
            details = stage.get("details") if isinstance(stage.get("details"), dict) else {}
            latest = details.get("latest_event") if isinstance(details.get("latest_event"), dict) else {}
            if latest:
                return latest
    source = report.get("source_reports") if isinstance(report.get("source_reports"), dict) else {}
    delegate = source.get("m4_manual_wake_delegate") if isinstance(source.get("m4_manual_wake_delegate"), dict) else {}
    latest = delegate.get("latest_event") if isinstance(delegate.get("latest_event"), dict) else {}
    return latest


def _matching_event(events: list[dict], report_event: dict) -> dict | None:
    event_id = report_event.get("id")
    if not event_id:
        return None
    for event in events:
        if event.get("id") == event_id:
            return event
    return None


def _recommendation(stop_reasons: list[str], pending_reasons: list[str], report: dict | None) -> str:
    pi_presence = report.get("pi_presence") if isinstance(report, dict) and isinstance(report.get("pi_presence"), dict) else {}
    if pi_presence.get("detected") is not True:
        return "pi_required"
    if stop_reasons:
        return "inspect"
    if pending_reasons:
        return CONTINUE_RECOMMENDATION
    return READY_RECOMMENDATION


def _stop_reasons(stages: list[dict]) -> list[str]:
    return [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]


def _stage(name: str, ok: bool, required: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {
        "name": name,
        "status": "passed" if ok else "failed",
        "ok": ok,
        "required": required,
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    if not isinstance(report, dict):
        return {"path": _relative(paths, path), "loaded": False}
    return {
        "path": _relative(paths, path),
        "loaded": True,
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "stop_reasons": report.get("stop_reasons", []),
        "saved_at": report.get("saved_at"),
    }


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
