"""M5.6 companion-quality release gate."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from .events import load_wake_events
from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority


EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
DEFAULT_TRIAL_TRIGGER = "m5-manual-quality-trial"
REQUIRED_M5_QUALITY_STAGES = (
    "m4_baseline",
    "event_health",
    "quality_warning_profile",
    "relationship_continuity",
    "emotion_status_continuity",
    "language_surface",
    "request_discipline",
    "memory_discipline",
    "grounding_integrity",
    "semantic_shadow_isolation",
    "output_storage_policy",
    "dashboard_read_only",
)


def run_m5_quality_release_gate(
    paths: CompanionPaths,
    *,
    min_trial_cycles: int = 3,
    m4_post_change_guard_report_path: str | Path | None = None,
    m5_quality_report_path: str | Path | None = None,
    m5_trial_report_path: str | Path | None = None,
) -> dict:
    """Decide whether M5 quality evidence is ready to freeze for M6.

    This gate is intentionally non-generative. It reads local reports and wake
    events only; it does not call a provider, run a wake, install timers, or
    change semantic-memory authority.
    """

    min_trial_cycles = max(1, min_trial_cycles)
    m4_path = _resolve(paths, m4_post_change_guard_report_path, "m4_post_change_guard_report.json")
    quality_path = _resolve(paths, m5_quality_report_path, "m5_quality_report.json")
    trial_path = _resolve(paths, m5_trial_report_path, "m5_quality_trial_report.json")

    m4_report, m4_stage = _load_report_stage(
        paths,
        m4_path,
        name="m4_post_change_guard",
        expected_milestone="M4.7",
        expected_recommendation="m4_still_deployable",
        ready_message="M4 post-change guard is still deployable",
    )
    quality_report, quality_stage = _load_report_stage(
        paths,
        quality_path,
        name="m5_quality_report",
        expected_milestone="M5.1",
        expected_recommendation="ready_for_quality_tuning",
        ready_message="M5.1 quality report is ready",
    )
    trial_report, trial_stage = _load_report_stage(
        paths,
        trial_path,
        name="m5_trial_report",
        expected_milestone="M5.5",
        expected_recommendation="continue_quality_observation",
        ready_message="M5.5 trial report is ready",
    )

    stages = [m4_stage, quality_stage, trial_stage]
    stages.append(_m5_quality_stages_stage(quality_report))
    stages.append(_m5_trial_profile_stage(trial_report, min_trial_cycles=min_trial_cycles))
    stages.append(_m5_trial_attempts_stage(trial_report, min_trial_cycles=min_trial_cycles))
    stages.append(_m5_trial_quality_contract_stage(trial_report))
    stages.append(_semantic_shadow_authority_stage(paths))
    audit_stage = _audit_anomalies_stage(paths, trial_report)
    stages.append(audit_stage)

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    ok = not stop_reasons
    return {
        "ok": ok,
        "milestone": "M5.6",
        "recommendation": "m5_quality_ready_for_m6" if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "m5-quality-release-gate",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "min_trial_cycles": min_trial_cycles,
        },
        "source_reports": {
            "m4_post_change_guard": _report_snapshot(paths, m4_path, m4_report),
            "m5_quality": _report_snapshot(paths, quality_path, quality_report),
            "m5_trial": _report_snapshot(paths, trial_path, trial_report),
        },
        "audit_anomalies": audit_stage.get("details", {}),
        "stages": stages,
        "stop_reasons": stop_reasons,
        "next_commands": {
            "quality_release_gate": _shell_command([
                "python3",
                "scripts/run_m5_quality_release_gate.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m5_final_freeze": _shell_command([
                "python3",
                "scripts/run_m5_final_freeze.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m4_post_change_guard": _shell_command([
                "python3",
                "scripts/run_m4_post_change_guard.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
    }


def _resolve(paths: CompanionPaths, override: str | Path | None, filename: str) -> Path:
    return Path(override).expanduser().resolve() if override else paths.life_loop_dir / filename


def _load_report_stage(
    paths: CompanionPaths,
    path: Path,
    *,
    name: str,
    expected_milestone: str,
    expected_recommendation: str,
    ready_message: str,
) -> tuple[dict | None, dict]:
    report, error = _read_report(path)
    if error:
        return None, _stage(name, False, True, error)

    problems = []
    if report.get("ok") is not True:
        problems.append(f"{name} ok is not true")
    if report.get("milestone") != expected_milestone:
        problems.append(f"{name} milestone is not {expected_milestone}")
    if report.get("recommendation") != expected_recommendation:
        problems.append(f"{name} recommendation is not {expected_recommendation}")
    return report, _stage(
        name,
        not problems,
        True,
        ready_message if not problems else "; ".join(problems),
        details=_report_snapshot(paths, path, report),
    )


def _read_report(path: Path) -> tuple[dict, str | None]:
    try:
        report = json.loads(path.read_text())
    except FileNotFoundError:
        return {}, f"report is missing: {path}"
    except json.JSONDecodeError as exc:
        return {}, f"report is invalid JSON: {exc.msg}"
    except OSError as exc:
        return {}, f"report could not be read: {exc}"
    if not isinstance(report, dict):
        return {}, f"report must be a JSON object: {path}"
    return report, None


def _m5_quality_stages_stage(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return _stage(
            "m5_quality_stages",
            False,
            True,
            "M5.1 quality report did not load",
            status="skipped",
        )
    stages = _stages_by_name(report)
    problems = []
    for name in REQUIRED_M5_QUALITY_STAGES:
        stage = stages.get(name)
        if stage is None:
            problems.append(f"missing M5.1 quality stage: {name}")
        elif stage.get("ok") is not True:
            problems.append(f"M5.1 quality stage did not pass: {name}")
    return _stage(
        "m5_quality_stages",
        not problems,
        True,
        "M5.1 quality stages support release" if not problems else "; ".join(problems),
        details={
            "required": list(REQUIRED_M5_QUALITY_STAGES),
            "present": sorted(stages),
        },
    )


def _m5_trial_profile_stage(report: dict | None, *, min_trial_cycles: int) -> dict:
    if not isinstance(report, dict):
        return _stage(
            "m5_trial_profile",
            False,
            True,
            "M5.5 trial report did not load",
            status="skipped",
        )
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    problems = []
    if report.get("provider") != EXPECTED_PROVIDER:
        problems.append(f"provider is {report.get('provider')!r}, expected {EXPECTED_PROVIDER!r}")
    if report.get("memory_mode") != EXPECTED_MEMORY_MODE:
        problems.append(f"memory_mode is {report.get('memory_mode')!r}, expected {EXPECTED_MEMORY_MODE!r}")
    if _count_int(report.get("cycles_requested")) < min_trial_cycles:
        problems.append(f"cycles_requested must be at least {min_trial_cycles}")
    if profile.get("cron_replacement") is not False:
        problems.append("cron_replacement must be false")
    if profile.get("semantic_shadow_authoritative") is not False:
        problems.append("semantic_shadow_authoritative must be false")
    if profile.get("real_wake_requested") is not True:
        problems.append("M5.5 must be an explicit real wake trial")
    if profile.get("provider_generation_requested") is not True:
        problems.append("M5.5 must be an explicit provider-generation trial")
    if profile.get("raw_output_storage") != "hash_only":
        problems.append("raw_output_storage must be hash_only")
    return _stage(
        "m5_trial_profile",
        not problems,
        True,
        "M5.5 profile preserves the frozen runtime contract" if not problems else "; ".join(problems),
        details={
            "provider": report.get("provider"),
            "memory_mode": report.get("memory_mode"),
            "cycles_requested": report.get("cycles_requested"),
            "profile": profile,
        },
    )


def _m5_trial_attempts_stage(report: dict | None, *, min_trial_cycles: int) -> dict:
    if not isinstance(report, dict):
        return _stage(
            "m5_trial_attempts",
            False,
            True,
            "M5.5 trial report did not load",
            status="skipped",
        )
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    problems = []
    if len(attempts) < min_trial_cycles:
        problems.append(f"attempts {len(attempts)} < required {min_trial_cycles}")
    if len(attempts) != _count_int(report.get("cycles_requested")):
        problems.append("attempt count does not match cycles_requested")
    for attempt in attempts:
        cycle = attempt.get("cycle", "?") if isinstance(attempt, dict) else "?"
        if not isinstance(attempt, dict):
            problems.append("attempt must be an object")
            continue
        if not attempt.get("event_id"):
            problems.append(f"cycle {cycle} event_id is missing")
        if attempt.get("status") != "completed":
            problems.append(f"cycle {cycle} attempt status is not completed")
        if attempt.get("event_status") != "completed":
            problems.append(f"cycle {cycle} event status is not completed")
        if attempt.get("quality_gate_decision") != "accepted":
            problems.append(f"cycle {cycle} quality gate decision is not accepted")
        if attempt.get("context_eligible") is not True:
            problems.append(f"cycle {cycle} context_eligible is not true")
        if attempt.get("quality_warnings"):
            problems.append(f"cycle {cycle} has quality warnings")
    return _stage(
        "m5_trial_attempts",
        not problems,
        True,
        "M5.5 canonical attempts all completed and were accepted"
        if not problems else "; ".join(problems),
        details={
            "cycles_requested": report.get("cycles_requested"),
            "attempt_count": len(attempts),
            "event_ids": [attempt.get("event_id") for attempt in attempts if isinstance(attempt, dict)],
        },
    )


def _m5_trial_quality_contract_stage(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return _stage(
            "m5_quality_contract",
            False,
            True,
            "M5.5 trial report did not load",
            status="skipped",
        )
    quality = report.get("quality_profile") if isinstance(report.get("quality_profile"), dict) else {}
    context = report.get("context_acceptance") if isinstance(report.get("context_acceptance"), dict) else {}
    requests = report.get("request_discipline") if isinstance(report.get("request_discipline"), dict) else {}
    memory = report.get("memory_discipline") if isinstance(report.get("memory_discipline"), dict) else {}
    grounding = report.get("grounding") if isinstance(report.get("grounding"), dict) else {}
    semantic = report.get("semantic_shadow") if isinstance(report.get("semantic_shadow"), dict) else {}
    output = report.get("output_audit") if isinstance(report.get("output_audit"), dict) else {}

    problems = []
    if _count_int(quality.get("quality_warning_count")) != 0:
        problems.append("quality_warning_count must be 0")
    if _count_int(quality.get("blocking_warning_count")) != 0:
        problems.append("blocking_warning_count must be 0")
    if _count_int(context.get("rejected_events")) != 0:
        problems.append("rejected_events must be 0")
    if _count_int(context.get("accepted_events")) < _count_int(report.get("cycles_requested")):
        problems.append("accepted_events must cover all requested cycles")
    if _count_int(requests.get("request_error_count")) != 0:
        problems.append("request_error_count must be 0")
    if _count_int(memory.get("memory_write_failures")) != 0:
        problems.append("memory_write_failures must be 0")
    if _count_int(grounding.get("unsupported")) != 0:
        problems.append("unsupported grounding must be 0")
    if _count_int(semantic.get("failed")) != 0:
        problems.append("semantic shadow failed count must be 0")
    if output.get("raw_output_storage") != "hash_only":
        problems.append("raw_output_storage must be hash_only")
    if _count_int(output.get("raw_output_stored_count")) != 0:
        problems.append("raw_output_stored_count must be 0")
    if _count_int(output.get("audit_count")) < _count_int(report.get("cycles_requested")):
        problems.append("output audit must cover all requested cycles")
    if report.get("stop_reasons"):
        problems.append("M5.5 stop_reasons must be empty")
    return _stage(
        "m5_quality_contract",
        not problems,
        True,
        "M5.5 quality, grounding, memory, request, and output contracts passed"
        if not problems else "; ".join(problems),
        details={
            "quality_profile": quality,
            "context_acceptance": context,
            "request_discipline": requests,
            "memory_discipline": memory,
            "grounding": grounding,
            "semantic_shadow": semantic,
            "output_audit": output,
            "stop_reasons": report.get("stop_reasons", []),
        },
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


def _audit_anomalies_stage(paths: CompanionPaths, report: dict | None) -> dict:
    if not isinstance(report, dict):
        return _stage(
            "audit_anomalies",
            False,
            True,
            "M5.5 trial report did not load",
            status="skipped",
        )
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    canonical_ids = [
        attempt.get("event_id")
        for attempt in attempts
        if isinstance(attempt, dict) and attempt.get("event_id")
    ]
    trigger_prefix = (
        (report.get("profile") or {}).get("trigger")
        if isinstance(report.get("profile"), dict)
        else None
    ) or DEFAULT_TRIAL_TRIGGER
    events = [
        (index, event)
        for index, event in enumerate(load_wake_events(paths.wake_events_file))
        if str(event.get("trigger", "")).startswith(trigger_prefix)
    ]
    by_id = {event.get("id"): (index, event) for index, event in events if event.get("id")}
    missing_canonical = [event_id for event_id in canonical_ids if event_id not in by_id]
    canonical_indexes = [by_id[event_id][0] for event_id in canonical_ids if event_id in by_id]
    latest_canonical_index = max(canonical_indexes) if canonical_indexes else -1

    anomalies = []
    for index, event in events:
        event_id = event.get("id")
        if event_id in canonical_ids:
            continue
        reasons = _event_problem_reasons(event)
        if not reasons:
            severity = "info"
            reasons = ["extra same-trigger event outside canonical M5.5 attempts"]
        elif index > latest_canonical_index:
            severity = "blocking"
        else:
            severity = "advisory"
        anomalies.append(_event_anomaly(index, event, severity=severity, reasons=reasons))

    blocking = [anomaly for anomaly in anomalies if anomaly["severity"] == "blocking"]
    problems = []
    if missing_canonical:
        problems.append(f"canonical M5.5 event ids missing from wake_events: {', '.join(missing_canonical)}")
    if blocking:
        problems.append(f"blocking post-report M5.5 audit anomalies: {len(blocking)}")
    details = {
        "trigger_prefix": trigger_prefix,
        "canonical_event_ids": canonical_ids,
        "canonical_event_count": len(canonical_ids),
        "same_trigger_event_count": len(events),
        "extra_event_count": len(anomalies),
        "advisory_anomaly_count": sum(1 for anomaly in anomalies if anomaly["severity"] == "advisory"),
        "blocking_anomaly_count": len(blocking),
        "info_anomaly_count": sum(1 for anomaly in anomalies if anomaly["severity"] == "info"),
        "missing_canonical_event_ids": missing_canonical,
        "anomalies": anomalies,
    }
    status = "failed" if problems else "advisory" if anomalies else "passed"
    return _stage(
        "audit_anomalies",
        not problems,
        True,
        "M5.5 audit trace is canonical"
        if not anomalies and not problems
        else "M5.5 audit trace has advisory anomalies"
        if anomalies and not problems
        else "; ".join(problems),
        status=status,
        details=details,
    )


def _event_problem_reasons(event: dict) -> list[str]:
    reasons = []
    if event.get("status") != "completed":
        reasons.append(f"status is {event.get('status')!r}")
    if event.get("error"):
        reasons.append("event has provider/runtime error")
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if gate.get("decision") == "rejected" or gate.get("context_eligible") is False:
        reasons.append("quality gate rejected future-context writes")
    blocking = gate.get("blocking_warnings") if isinstance(gate.get("blocking_warnings"), list) else []
    if blocking:
        reasons.append(f"blocking quality warnings ({len(blocking)})")
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        reasons.append(f"unsupported grounding claims ({_count_int(grounding.get('unsupported'))})")
    if event.get("request_errors"):
        reasons.append(f"request errors ({len(event.get('request_errors', []))})")
    memory_failures = [
        result
        for result in event.get("memory_write_results", [])
        if isinstance(result, dict) and result.get("status") == "failed"
    ]
    if memory_failures:
        reasons.append(f"memory write failures ({len(memory_failures)})")
    output = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
    if output:
        if output.get("raw_output_storage") != "hash_only":
            reasons.append("raw_output_storage is not hash_only")
        if _raw_output_stored(output):
            reasons.append("raw model output was stored")
    return reasons


def _event_anomaly(index: int, event: dict, *, severity: str, reasons: list[str]) -> dict:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    return {
        "ledger_index": index,
        "severity": severity,
        "id": event.get("id"),
        "trigger": event.get("trigger"),
        "status": event.get("status"),
        "started_at": event.get("started_at"),
        "completed_at": event.get("completed_at"),
        "quality_gate_decision": gate.get("decision"),
        "context_eligible": gate.get("context_eligible"),
        "blocking_warning_count": len(gate.get("blocking_warnings", []))
        if isinstance(gate.get("blocking_warnings"), list)
        else 0,
        "grounding_unsupported": _count_int(grounding.get("unsupported")),
        "error_type": error.get("type"),
        "reasons": reasons,
    }


def _raw_output_stored(output_audit: dict) -> bool:
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    snapshot = {
        "path": _relative(paths, path),
        "loaded": isinstance(report, dict),
    }
    if isinstance(report, dict):
        snapshot.update({
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _stages_by_name(report: dict) -> dict[str, dict]:
    stages = report.get("stages")
    if not isinstance(stages, list):
        return {}
    return {
        stage["name"]: stage
        for stage in stages
        if isinstance(stage, dict) and isinstance(stage.get("name"), str)
    }


def _stage(
    name: str,
    ok: bool,
    required: bool,
    message: str,
    *,
    status: str | None = None,
    details: dict | None = None,
) -> dict:
    stage = {
        "name": name,
        "status": status or ("passed" if ok else "failed"),
        "ok": ok,
        "required": required,
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
