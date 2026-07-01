"""M9.5 controlled presence final freeze gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .events import load_wake_events
from .m9_scheduler_activation import CRON_MARKER, build_m9_cron_line, disable_command, enable_command, read_user_crontab
from .m9_scheduler_dry_run import REQUIRED_SKIP_REASONS, load_scheduler_attempts
from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_controlled_presence_frozen"
EXPECTED_REPORTS = {
    "m8_memory_freeze": ("m8_memory_freeze_report.json", "M8.7", "m8_memory_dialogue_frozen"),
    "m9_scheduler_revalidation": ("m9_scheduler_revalidation_report.json", "M9.1", "m9_scheduler_revalidation_ready"),
    "m9_scheduler_dry_run": ("m9_scheduler_dry_run_report.json", "M9.2", "m9_scheduler_dry_run_ready"),
    "m9_scheduler_activation": ("m9_scheduler_activation_report.json", "M9.3", "m9_scheduler_activation_ready"),
    "m9_presence_observation": ("m9_presence_observation_report.json", "M9.4", "m9_presence_observation_ready"),
}
LIFE_WRITE_ROUTE_RE = re.compile(
    r"@app\.(?:post|put|patch|delete)\([\"']/life(?:[\"'/)]|/)|"
    r"@app\.route\([\"']/life[^)]*methods\s*=\s*\[[^\]]*[\"'](?:POST|PUT|PATCH|DELETE)[\"']",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class M9PresenceFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m9_presence_freeze(
    paths: CompanionPaths,
    *,
    crontab_reader=None,
    now: datetime | None = None,
) -> M9PresenceFreezeResult:
    """Freeze controlled scheduled presence after M9.1-M9.4 evidence passes."""

    saved_at = now or datetime.now()
    reader = crontab_reader or read_user_crontab
    reports = {
        name: _load_report(paths.life_loop_dir / filename)
        for name, (filename, _milestone, _recommendation) in EXPECTED_REPORTS.items()
    }
    activation_at = _parse_datetime(
        reports["m9_scheduler_activation"].get("saved_at")
        if isinstance(reports["m9_scheduler_activation"], dict)
        else None
    )
    live_attempts = _live_attempts(paths, activation_at)
    scheduled_wake_events = _scheduled_wake_events(paths, activation_at)
    presence_state = _load_report(paths.scheduler_presence_state_file)
    try:
        crontab_text = reader()
        crontab_error = None
    except Exception as exc:
        crontab_text = ""
        crontab_error = f"{type(exc).__name__}: {exc}"

    stages = [
        *_report_stages(reports),
        _scheduler_artifact_stage(paths, reports, crontab_text, crontab_error),
        _cadence_stage(reports),
        _observation_and_drill_stage(reports["m9_presence_observation"], live_attempts),
        _runtime_attempt_stage(live_attempts),
        _scheduled_wake_event_stage(scheduled_wake_events),
        _memory_boundary_stage(reports["m8_memory_freeze"], scheduled_wake_events),
        _static_boundary_stage(paths),
        _freeze_runtime_boundary_stage(),
    ]
    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M9.5",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "source_reports": {
            name: _report_snapshot(paths, paths.life_loop_dir / filename, reports[name])
            for name, (filename, _milestone, _recommendation) in EXPECTED_REPORTS.items()
        },
        "final_freeze": {
            "frozen": ok,
            "controlled_presence_ready": ok,
            "m9_1_to_m9_4_ready": all(_stage_passed(stages, name) for name in EXPECTED_REPORTS),
            "scheduler_artifact_known": _stage_passed(stages, "scheduler_artifact_current"),
            "scheduler_observable": _stage_passed(stages, "observation_and_drills"),
            "scheduler_reversible": _stage_passed(stages, "observation_and_drills"),
            "readonly": True,
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
            "presence_state": _public_presence_state(presence_state),
        },
        "observation": {
            "activation_saved_at": activation_at.isoformat() if activation_at else None,
            "live_attempt_count": len(live_attempts),
            "live_attempts": [_public_attempt(attempt) for attempt in live_attempts[-20:]],
            "scheduled_wake_event_count": len(scheduled_wake_events),
            "scheduled_wake_events": [_public_wake_event(event) for event in scheduled_wake_events[-20:]],
        },
        "boundaries": {
            "scheduler_mutated_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "provider_calls_by_freeze": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
        "evidence": {
            "scheduler_artifact_count": _marker_count(crontab_text),
            "live_attempts_observed": len(live_attempts),
            "scheduled_wake_events_observed": len(scheduled_wake_events),
            "required_skip_reasons": list(REQUIRED_SKIP_REASONS),
            "pause_drill_ready": _pause_drill_ready(reports["m9_presence_observation"]),
            "rollback_drill_ready": _rollback_drill_ready(reports["m9_presence_observation"]),
            "provider_calls_by_freeze": 0,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "rollback_scheduler": disable_command(paths),
            "pause_scheduler": _shell_command(["touch", str(paths.scheduler_pause_flag)]),
        },
    }
    return M9PresenceFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m9_presence_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m9_presence_freeze_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _report_stages(reports: dict[str, dict | None]) -> list[dict]:
    stages = []
    for name, (_filename, milestone, recommendation) in EXPECTED_REPORTS.items():
        report = reports.get(name)
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
            if report.get("provider_calls", 0) not in (0, None):
                problems.append(f"{milestone} report has provider_calls")
        stages.append(_stage(
            name,
            not problems,
            f"{milestone} report is ready" if not problems else "; ".join(problems),
        ))
    return stages


def _scheduler_artifact_stage(paths: CompanionPaths, reports: dict, crontab_text: str, crontab_error: str | None) -> dict:
    expected = build_m9_cron_line(paths)
    marker_lines = [line for line in crontab_text.splitlines() if CRON_MARKER in line]
    activation = reports.get("m9_scheduler_activation") if isinstance(reports.get("m9_scheduler_activation"), dict) else {}
    observation = reports.get("m9_presence_observation") if isinstance(reports.get("m9_presence_observation"), dict) else {}
    problems = []
    if crontab_error:
        problems.append(crontab_error)
    if len(marker_lines) != 1:
        problems.append(f"expected exactly one {CRON_MARKER} artifact, found {len(marker_lines)}")
    elif marker_lines[0] != expected:
        problems.append("current cron artifact does not match expected M9 command")
    for label, report in (("M9.3", activation), ("M9.4", observation)):
        scheduler = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
        if scheduler.get("artifact_count") != 1:
            problems.append(f"{label} did not record exactly one scheduler artifact")
        if scheduler.get("enabled") is not True:
            problems.append(f"{label} did not record scheduler enabled")
    return _stage(
        "scheduler_artifact_current",
        not problems,
        "scheduler artifact is known, bounded, and currently enabled" if not problems else "; ".join(problems),
        details={"artifact_count": len(marker_lines), "artifact_name": CRON_MARKER},
    )


def _cadence_stage(reports: dict) -> dict:
    activation = reports.get("m9_scheduler_activation") if isinstance(reports.get("m9_scheduler_activation"), dict) else {}
    cadence = activation.get("cadence") if isinstance(activation.get("cadence"), dict) else {}
    problems = []
    if cadence.get("model") != "randomized_presence_windows":
        problems.append("cadence model is not randomized_presence_windows")
    if cadence.get("quiet_hours") != ["00:00", "08:00"]:
        problems.append("quiet hours are not 00:00-08:00")
    if cadence.get("daily_live_wake_budget") != 2:
        problems.append("daily_live_wake_budget is not 2")
    if cadence.get("scheduled_wake_output") != "internal_only":
        problems.append("scheduled_wake_output is not internal_only")
    missing = [reason for reason in REQUIRED_SKIP_REASONS if reason not in cadence.get("skip_reasons", [])]
    if missing:
        problems.append("cadence missing skip reasons: " + ", ".join(missing))
    return _stage(
        "cadence_contract",
        not problems,
        "controlled randomized presence cadence remains frozen" if not problems else "; ".join(problems),
        details={"cadence": cadence},
    )


def _observation_and_drill_stage(observation: dict | None, live_attempts: list[dict]) -> dict:
    drills = observation.get("drills") if isinstance(observation, dict) and isinstance(observation.get("drills"), dict) else {}
    pause = drills.get("pause") if isinstance(drills.get("pause"), dict) else {}
    rollback = drills.get("rollback") if isinstance(drills.get("rollback"), dict) else {}
    problems = []
    if not live_attempts:
        problems.append("no live scheduler attempts observed")
    if pause.get("ok") is not True or pause.get("performed") is not True:
        problems.append("pause drill is not ready")
    if rollback.get("ok") is not True or rollback.get("performed") is not True:
        problems.append("rollback drill is not ready")
    return _stage(
        "observation_and_drills",
        not problems,
        "scheduler is observable, pause-tested, and rollback-tested" if not problems else "; ".join(problems),
        details={
            "live_attempt_count": len(live_attempts),
            "pause_drill_ready": pause.get("ok") is True,
            "rollback_drill_ready": rollback.get("ok") is True,
        },
    )


def _runtime_attempt_stage(attempts: list[dict]) -> dict:
    problems = []
    for attempt in attempts:
        if attempt.get("raw_provider_payload_stored") is True:
            problems.append(f"attempt {attempt.get('id')} stored raw provider payload")
        if attempt.get("voice_signal_hardware_output") is True:
            problems.append(f"attempt {attempt.get('id')} emitted voice/signal/hardware output")
        if attempt.get("wake_cycle_run") is True and attempt.get("lock_acquired") is not True:
            problems.append(f"attempt {attempt.get('id')} ran wake without lock")
        if attempt.get("scheduled_wake_output") != "internal_only":
            problems.append(f"attempt {attempt.get('id')} output is not internal_only")
    return _stage(
        "scheduler_attempt_boundaries",
        not problems,
        "observed scheduler attempts preserve M9 boundaries" if not problems else "; ".join(problems),
        details={"attempt_count": len(attempts)},
    )


def _scheduled_wake_event_stage(events: list[dict]) -> dict:
    problems = []
    intervals = []
    for event in events:
        audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
        if audit.get("raw_output_storage") not in (None, "hash_only"):
            problems.append(f"event {event.get('id')} raw_output_storage is not hash_only")
        for section in ("initial", "final"):
            payload = audit.get(section) if isinstance(audit.get(section), dict) else {}
            if payload.get("raw_output_stored") is True:
                problems.append(f"event {event.get('id')} stored raw output in {section}")
        if event.get("raw_provider_payload") is not None or event.get("raw_provider_payload_stored") is True:
            problems.append(f"event {event.get('id')} stored raw provider payload")
        start = _parse_datetime(event.get("started_at"))
        duration = _number(event.get("duration_seconds"))
        if start and duration is not None:
            intervals.append((start, start + timedelta(seconds=duration), event.get("id")))
    for index, (start, end, event_id) in enumerate(intervals):
        for other_start, other_end, other_id in intervals[index + 1:]:
            if start < other_end and other_start < end:
                problems.append(f"scheduled wake events overlap: {event_id}, {other_id}")
    return _stage(
        "scheduled_wake_event_boundaries",
        not problems,
        "scheduled wake events have no overlap or raw payload violations" if not problems else "; ".join(problems),
        details={"scheduled_wake_event_count": len(events)},
    )


def _memory_boundary_stage(m8_report: dict | None, events: list[dict]) -> dict:
    problems = []
    boundaries = m8_report.get("boundaries") if isinstance(m8_report, dict) and isinstance(m8_report.get("boundaries"), dict) else {}
    for key in (
        "raw_provider_payload_stored",
        "life_write_route_added",
        "semantic_shadow_authority_promoted",
        "proposal_or_quarantine_prompt_authority",
    ):
        if boundaries.get(key) is True:
            problems.append(f"M8.7 boundary {key} is true")
    for event in events:
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
        "memory authority remains M8-compliant" if not problems else "; ".join(problems),
        details={"scheduled_wake_event_count": len(events)},
    )


def _static_boundary_stage(paths: CompanionPaths) -> dict:
    window_source = _read_text(paths.home / "window" / "window.py")
    problems = []
    if LIFE_WRITE_ROUTE_RE.search(window_source):
        problems.append("/life write route detected")
    return _stage(
        "static_runtime_boundaries",
        not problems,
        "source scan found no /life write route" if not problems else "; ".join(problems),
    )


def _freeze_runtime_boundary_stage() -> dict:
    return _stage(
        "freeze_runtime_boundary",
        True,
        "M9.5 freeze is read-only and does not call provider or mutate scheduler",
        details={
            "scheduler_mutated_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "provider_calls_by_freeze": 0,
        },
    )


def _live_attempts(paths: CompanionPaths, activation_at: datetime | None) -> list[dict]:
    attempts = []
    for attempt in load_scheduler_attempts(paths.scheduler_attempts_file):
        if attempt.get("source") != "m9_scheduler_live_tick":
            continue
        attempted_at = _parse_datetime(attempt.get("attempted_at"))
        if activation_at and attempted_at and attempted_at < activation_at:
            continue
        attempts.append(attempt)
    return attempts


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


def _pause_drill_ready(report: dict | None) -> bool:
    drills = report.get("drills") if isinstance(report, dict) and isinstance(report.get("drills"), dict) else {}
    pause = drills.get("pause") if isinstance(drills.get("pause"), dict) else {}
    return pause.get("performed") is True and pause.get("ok") is True


def _rollback_drill_ready(report: dict | None) -> bool:
    drills = report.get("drills") if isinstance(report, dict) and isinstance(report.get("drills"), dict) else {}
    rollback = drills.get("rollback") if isinstance(drills.get("rollback"), dict) else {}
    return rollback.get("performed") is True and rollback.get("ok") is True


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


def _marker_count(crontab_text: str) -> int:
    return sum(1 for line in crontab_text.splitlines() if CRON_MARKER in line)


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stage_passed(stages: list[dict], name: str) -> bool:
    return any(stage.get("name") == name and stage.get("status") == "pass" for stage in stages)


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


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(str(arg)) for arg in args)
