"""M9.4 presence observation and rollback drill gate."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .events import load_wake_events
from .m9_scheduler_activation import (
    CRON_MARKER,
    build_m9_cron_line,
    disable_command,
    enable_command,
    read_user_crontab,
    run_m9_scheduler_activation,
    run_m9_scheduler_disable,
    write_user_crontab,
)
from .m9_scheduler_tick import run_m9_scheduler_tick
from .m9_scheduler_dry_run import load_scheduler_attempts
from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_presence_observation_ready"
ACTIVATION_RECOMMENDATION = "m9_scheduler_activation_ready"
M8_FREEZE_RECOMMENDATION = "m8_memory_dialogue_frozen"


@dataclass
class M9PresenceObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m9_presence_observation(
    paths: CompanionPaths,
    *,
    observation_limit: int = 20,
    require_live_attempt: bool = True,
    perform_pause_drill: bool = False,
    perform_rollback_drill: bool = False,
    crontab_reader=None,
    crontab_writer=None,
    now: datetime | None = None,
    random_seed: int | None = None,
) -> M9PresenceObservationResult:
    """Observe M9 live scheduler evidence and optionally run pause/rollback drills."""

    saved_at = now or datetime.now()
    reader = crontab_reader or read_user_crontab
    writer = crontab_writer or write_user_crontab
    activation_path = paths.life_loop_dir / "m9_scheduler_activation_report.json"
    m8_freeze_path = paths.life_loop_dir / "m8_memory_freeze_report.json"
    activation_report = _load_report(activation_path)
    m8_freeze_report = _load_report(m8_freeze_path)
    activation_at = _parse_datetime(activation_report.get("saved_at") if isinstance(activation_report, dict) else None)

    pause_drill = _pause_drill(paths, saved_at, random_seed=random_seed) if perform_pause_drill else _drill_skipped("pause")
    rollback_drill = (
        _rollback_drill(paths, reader, writer, saved_at, random_seed=random_seed)
        if perform_rollback_drill
        else _drill_skipped("rollback")
    )

    attempts = _live_attempts(paths, activation_at, limit=observation_limit)
    scheduled_wake_events = _scheduled_wake_events(paths, activation_at)
    current_state = _load_report(paths.scheduler_presence_state_file)
    try:
        crontab_text = reader()
        crontab_error = None
    except Exception as exc:
        crontab_text = ""
        crontab_error = f"{type(exc).__name__}: {exc}"

    stages = [
        _activation_stage(activation_report),
        _m8_freeze_stage(m8_freeze_report),
        _cron_artifact_stage(paths, crontab_text, crontab_error),
        _attempt_observation_stage(attempts, require_live_attempt=require_live_attempt),
        _overlap_stage(attempts, scheduled_wake_events),
        _wake_event_boundary_stage(scheduled_wake_events),
        _memory_boundary_stage(m8_freeze_report, scheduled_wake_events),
        _pause_drill_stage(pause_drill, required=perform_pause_drill),
        _rollback_drill_stage(rollback_drill, required=perform_rollback_drill),
        _runtime_boundary_stage(),
    ]
    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M9.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "source_reports": {
            "m9_scheduler_activation": _report_snapshot(paths, activation_path, activation_report),
            "m8_memory_freeze": _report_snapshot(paths, m8_freeze_path, m8_freeze_report),
        },
        "observation": {
            "activation_saved_at": activation_at.isoformat() if activation_at else None,
            "attempt_count": len(attempts),
            "attempts": [_public_attempt(attempt) for attempt in attempts],
            "scheduled_wake_event_count": len(scheduled_wake_events),
            "scheduled_wake_events": [_public_wake_event(event) for event in scheduled_wake_events[-observation_limit:]],
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
            "presence_state": _public_presence_state(current_state),
        },
        "drills": {
            "pause": pause_drill,
            "rollback": rollback_drill,
        },
        "scheduler": {
            "mechanism": "cron",
            "artifact_name": CRON_MARKER,
            "artifact_count": _marker_count(crontab_text),
            "artifact_line": build_m9_cron_line(paths),
            "enabled": _marker_count(crontab_text) == 1,
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
            "attempts_file": _relative(paths, paths.scheduler_attempts_file),
            "enable_command": enable_command(paths),
            "disable_command": disable_command(paths),
            "rollback_command": disable_command(paths),
        },
        "boundaries": {
            "wake_cycle_run_by_observation_gate": False,
            "provider_generation_requested_by_observation_gate": False,
            "provider_calls_by_observation_gate": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
        "evidence": {
            "live_attempts_observed": len(attempts),
            "scheduled_wake_events_observed": len(scheduled_wake_events),
            "pause_drill_performed": pause_drill.get("performed") is True,
            "rollback_drill_performed": rollback_drill.get("performed") is True,
            "scheduler_artifact_count": _marker_count(crontab_text),
            "provider_calls_by_observation_gate": 0,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m9_presence_observation": _shell_command([
                "python3",
                "scripts/run_m9_presence_observation.py",
                "--companion-home",
                str(paths.home),
                "--perform-pause-drill",
                "--perform-rollback-drill",
            ]),
            "m9_presence_freeze_later": "requires m9_presence_observation_ready",
        },
    }
    return M9PresenceObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m9_presence_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m9_presence_observation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _pause_drill(paths: CompanionPaths, now: datetime, *, random_seed: int | None) -> dict:
    existed = paths.scheduler_pause_flag.exists()
    previous = paths.scheduler_pause_flag.read_text() if existed else None
    paths.scheduler_pause_flag.write_text(f"m9.4 pause drill at {now.isoformat()}\n")
    try:
        tick_result = run_m9_scheduler_tick(paths, now=now, random_seed=random_seed)
        tick_report = tick_result.to_dict()
    finally:
        if existed:
            paths.scheduler_pause_flag.write_text(previous or "")
        else:
            try:
                paths.scheduler_pause_flag.unlink()
            except FileNotFoundError:
                pass
    attempt = tick_report.get("attempt", {}) if isinstance(tick_report, dict) else {}
    return {
        "performed": True,
        "ok": tick_result.ok is True and attempt.get("skip_reason") == "paused",
        "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
        "pause_flag_preexisting": existed,
        "attempt": attempt,
        "wake_cycle_run": attempt.get("wake_cycle_run") is True,
        "provider_calls": 0,
        "errors": tick_report.get("errors", []) if isinstance(tick_report, dict) else ["tick report missing"],
    }


def _rollback_drill(paths: CompanionPaths, reader, writer, now: datetime, *, random_seed: int | None) -> dict:
    original = reader()
    restore_error = None
    disable_result = run_m9_scheduler_disable(paths, crontab_reader=reader, crontab_writer=writer, now=now)
    disabled_text = reader()
    reenable_result = run_m9_scheduler_activation(
        paths,
        crontab_reader=reader,
        crontab_writer=writer,
        now=now,
        random_seed=random_seed,
    )
    reenabled_text = reader()
    if reenable_result.ok is not True or _marker_count(reenabled_text) != 1:
        try:
            writer(original)
        except Exception as exc:  # pragma: no cover - best-effort production recovery.
            restore_error = f"{type(exc).__name__}: {exc}"
    return {
        "performed": True,
        "ok": (
            disable_result.ok is True
            and reenable_result.ok is True
            and _marker_count(disabled_text) == 0
            and _marker_count(reenabled_text) == 1
            and restore_error is None
        ),
        "disable_command": disable_command(paths),
        "enable_command": enable_command(paths),
        "artifact_count_after_disable": _marker_count(disabled_text),
        "artifact_count_after_restore": _marker_count(reenabled_text),
        "disable_recommendation": disable_result.recommendation,
        "restore_recommendation": reenable_result.recommendation,
        "restore_error": restore_error,
    }


def _drill_skipped(name: str) -> dict:
    return {
        "performed": False,
        "ok": False,
        "reason": f"{name}_drill_not_requested",
        "provider_calls": 0,
        "wake_cycle_run": False,
    }


def _activation_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M9.3 activation report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M9.3 activation ok is not true")
        if report.get("milestone") != "M9.3":
            problems.append("M9.3 activation milestone is not M9.3")
        if report.get("recommendation") != ACTIVATION_RECOMMENDATION:
            problems.append(f"M9.3 activation recommendation is not {ACTIVATION_RECOMMENDATION}")
        if report.get("stop_reasons"):
            problems.append("M9.3 activation report has stop_reasons")
        boundaries = report.get("boundaries") if isinstance(report.get("boundaries"), dict) else {}
        for key in ("wake_cycle_run", "provider_generation_requested", "raw_provider_payload_stored"):
            if boundaries.get(key) is True:
                problems.append(f"M9.3 activation boundary {key} is true")
    return _stage(
        "m9_scheduler_activation",
        not problems,
        "M9.3 activation report is ready" if not problems else "; ".join(problems),
    )


def _m8_freeze_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M8.7 freeze report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M8.7 freeze ok is not true")
        if report.get("milestone") != "M8.7":
            problems.append("M8.7 freeze milestone is not M8.7")
        if report.get("recommendation") != M8_FREEZE_RECOMMENDATION:
            problems.append(f"M8.7 recommendation is not {M8_FREEZE_RECOMMENDATION}")
        if report.get("stop_reasons"):
            problems.append("M8.7 freeze report has stop_reasons")
        boundaries = report.get("boundaries") if isinstance(report.get("boundaries"), dict) else {}
        for key in (
            "raw_provider_payload_stored",
            "life_write_route_added",
            "semantic_shadow_authority_promoted",
            "proposal_or_quarantine_prompt_authority",
        ):
            if boundaries.get(key) is True:
                problems.append(f"M8.7 boundary {key} is true")
    return _stage(
        "m8_memory_dialogue_freeze",
        not problems,
        "M8.7 memory/dialogue freeze remains ready" if not problems else "; ".join(problems),
    )


def _cron_artifact_stage(paths: CompanionPaths, crontab_text: str, crontab_error: str | None) -> dict:
    expected = build_m9_cron_line(paths)
    marker_lines = [line for line in crontab_text.splitlines() if CRON_MARKER in line]
    problems = []
    if crontab_error:
        problems.append(crontab_error)
    if len(marker_lines) != 1:
        problems.append(f"expected exactly one {CRON_MARKER} cron artifact, found {len(marker_lines)}")
    elif marker_lines[0] != expected:
        problems.append("managed cron artifact does not match expected M9.4 command")
    return _stage(
        "cron_artifact_current",
        not problems,
        "current crontab contains exactly one expected M9 scheduler artifact"
        if not problems
        else "; ".join(problems),
        details={"artifact_count": len(marker_lines), "artifact_name": CRON_MARKER},
    )


def _attempt_observation_stage(attempts: list[dict], *, require_live_attempt: bool) -> dict:
    problems = []
    if require_live_attempt and not attempts:
        problems.append("no live M9 scheduler attempts observed after activation")
    for attempt in attempts:
        if attempt.get("source") != "m9_scheduler_live_tick":
            problems.append(f"attempt {attempt.get('id')} has unexpected source")
        if attempt.get("trigger") != "scheduled-wake":
            problems.append(f"attempt {attempt.get('id')} trigger is not scheduled-wake")
        if attempt.get("scheduled_wake_output") != "internal_only":
            problems.append(f"attempt {attempt.get('id')} output is not internal_only")
        if attempt.get("raw_provider_payload_stored") is True:
            problems.append(f"attempt {attempt.get('id')} stored raw provider payload")
        if attempt.get("voice_signal_hardware_output") is True:
            problems.append(f"attempt {attempt.get('id')} emitted voice/signal/hardware output")
        if attempt.get("decision") == "skipped" and not attempt.get("skip_reason"):
            problems.append(f"attempt {attempt.get('id')} skipped without reason")
    return _stage(
        "scheduler_attempt_observation",
        not problems,
        "bounded live scheduler attempts are observable and controlled" if not problems else "; ".join(problems),
        details={
            "attempt_count": len(attempts),
            "decisions": _counts(attempt.get("decision") for attempt in attempts),
            "skip_reasons": sorted({str(attempt.get("skip_reason")) for attempt in attempts if attempt.get("skip_reason")}),
        },
    )


def _overlap_stage(attempts: list[dict], wake_events: list[dict]) -> dict:
    intervals = []
    for event in wake_events:
        start = _parse_datetime(event.get("started_at"))
        duration = _number(event.get("duration_seconds"))
        if start and duration is not None:
            intervals.append((start, start + timedelta(seconds=duration), event.get("id")))
    overlaps = []
    for index, (start, end, event_id) in enumerate(intervals):
        for other_start, other_end, other_id in intervals[index + 1:]:
            if start < other_end and other_start < end:
                overlaps.append([event_id, other_id])
    concurrent_attempts = [
        attempt.get("id")
        for attempt in attempts
        if attempt.get("wake_cycle_run") is True and attempt.get("lock_acquired") is not True
    ]
    problems = []
    if overlaps:
        problems.append(f"overlapping scheduled wake events detected: {overlaps}")
    if concurrent_attempts:
        problems.append("wake attempts ran without lock acquisition: " + ", ".join(str(item) for item in concurrent_attempts))
    return _stage(
        "no_overlapping_wake_cycles",
        not problems,
        "no overlapping scheduled wake cycles detected" if not problems else "; ".join(problems),
        details={"scheduled_wake_event_count": len(wake_events), "wake_run_attempt_count": len(concurrent_attempts)},
    )


def _wake_event_boundary_stage(wake_events: list[dict]) -> dict:
    problems = []
    for event in wake_events:
        audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
        if audit.get("raw_output_storage") not in (None, "hash_only"):
            problems.append(f"event {event.get('id')} raw_output_storage is not hash_only")
        for section in ("initial", "final"):
            payload = audit.get(section) if isinstance(audit.get(section), dict) else {}
            if payload.get("raw_output_stored") is True:
                problems.append(f"event {event.get('id')} stored raw output in {section}")
        if event.get("raw_provider_payload") is not None or event.get("raw_provider_payload_stored") is True:
            problems.append(f"event {event.get('id')} stored raw provider payload")
    return _stage(
        "scheduled_wake_event_boundaries",
        not problems,
        "scheduled wake events contain no raw payload violations" if not problems else "; ".join(problems),
        details={"scheduled_wake_event_count": len(wake_events)},
    )


def _memory_boundary_stage(m8_report: dict | None, wake_events: list[dict]) -> dict:
    problems = []
    if not isinstance(m8_report, dict) or m8_report.get("recommendation") != M8_FREEZE_RECOMMENDATION:
        problems.append("M8.7 freeze is not ready")
    for event in wake_events:
        semantic_shadow = event.get("semantic_shadow") if isinstance(event.get("semantic_shadow"), dict) else {}
        if semantic_shadow.get("authoritative") is True:
            problems.append(f"event {event.get('id')} promoted semantic shadow authority")
        policy = event.get("memory_policy") if isinstance(event.get("memory_policy"), dict) else {}
        for decision in policy.get("decisions", []) if isinstance(policy.get("decisions"), list) else []:
            status = decision.get("status") or decision.get("decision")
            if status in {"proposal", "quarantine", "rejected", "audit-only"} and decision.get("prompt_eligible") is True:
                problems.append(f"event {event.get('id')} made {status} memory prompt eligible")
    return _stage(
        "memory_authority_boundaries",
        not problems,
        "M8 memory authority boundaries remain intact" if not problems else "; ".join(problems),
        details={"scheduled_wake_event_count": len(wake_events)},
    )


def _pause_drill_stage(drill: dict, *, required: bool) -> dict:
    problems = []
    if required and drill.get("performed") is not True:
        problems.append("pause drill was not performed")
    if drill.get("performed") is True:
        if drill.get("ok") is not True:
            problems.append("pause drill did not produce a paused skip")
        if drill.get("wake_cycle_run") is True:
            problems.append("pause drill ran wake")
    return _stage(
        "pause_flag_drill",
        not problems,
        "pause flag suppresses scheduled wake attempts" if not problems else "; ".join(problems),
        details=drill,
    )


def _rollback_drill_stage(drill: dict, *, required: bool) -> dict:
    problems = []
    if required and drill.get("performed") is not True:
        problems.append("rollback drill was not performed")
    if drill.get("performed") is True and drill.get("ok") is not True:
        problems.append("rollback drill did not disable and restore exactly one artifact")
    return _stage(
        "rollback_drill",
        not problems,
        "rollback disables and restores the managed scheduler artifact" if not problems else "; ".join(problems),
        details=drill,
    )


def _runtime_boundary_stage() -> dict:
    return _stage(
        "observation_runtime_boundary",
        True,
        "observation gate does not request provider generation or add external output channels",
        details={
            "provider_generation_requested_by_observation_gate": False,
            "provider_calls_by_observation_gate": 0,
            "voice_signal_hardware_activation_allowed": False,
            "life_write_route_added": False,
        },
    )


def _live_attempts(paths: CompanionPaths, activation_at: datetime | None, *, limit: int) -> list[dict]:
    attempts = []
    for attempt in load_scheduler_attempts(paths.scheduler_attempts_file):
        if attempt.get("source") != "m9_scheduler_live_tick":
            continue
        attempted_at = _parse_datetime(attempt.get("attempted_at"))
        if activation_at and attempted_at and attempted_at < activation_at:
            continue
        attempts.append(attempt)
    return attempts[-limit:]


def _scheduled_wake_events(paths: CompanionPaths, activation_at: datetime | None) -> list[dict]:
    events = []
    for event in load_wake_events(paths.wake_events_file):
        if not str(event.get("trigger", "")).startswith("scheduled-wake"):
            continue
        started_at = _parse_datetime(event.get("started_at"))
        if activation_at and started_at and started_at < activation_at:
            continue
        events.append(event)
    return events


def _public_attempt(attempt: dict) -> dict:
    return {
        "id": attempt.get("id"),
        "attempted_at": attempt.get("attempted_at"),
        "decision": attempt.get("decision"),
        "skip_reason": attempt.get("skip_reason"),
        "wake_cycle_run": attempt.get("wake_cycle_run"),
        "lock_acquired": attempt.get("lock_acquired"),
        "scheduled_wake_output": attempt.get("scheduled_wake_output"),
    }


def _public_wake_event(event: dict) -> dict:
    return {
        "id": event.get("id"),
        "trigger": event.get("trigger"),
        "status": event.get("status"),
        "started_at": event.get("started_at"),
        "completed_at": event.get("completed_at"),
        "duration_seconds": event.get("duration_seconds"),
        "provider": event.get("provider"),
    }


def _public_presence_state(state: dict | None) -> dict:
    if not isinstance(state, dict):
        return {}
    keys = (
        "last_attempt_at",
        "last_scheduled_wake_at",
        "next_candidate_after",
        "daily_live_wake_budget",
        "daily_live_wake_count",
        "daily_budget_date",
        "quiet_hours",
        "last_skip_reason",
        "cooldown_until",
        "scheduled_wake_output",
    )
    return {key: state.get(key) for key in keys if key in state}


def _counts(values) -> dict:
    counts = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _marker_count(crontab_text: str) -> int:
    return sum(1 for line in crontab_text.splitlines() if CRON_MARKER in line)


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(str(arg)) for arg in args)
