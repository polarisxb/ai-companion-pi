"""M9 live scheduler tick wrapper.

This module is the controlled boundary between a scheduler opportunity check
and the existing wake command. It does not create another provider path.
"""

from __future__ import annotations

import fcntl
import json
import random
import shlex
import subprocess
import time as monotonic_time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterator

from .events import load_wake_events
from .m9_scheduler_dry_run import (
    DEFAULT_DAILY_LIVE_WAKE_BUDGET,
    DEFAULT_FAILURE_COOLDOWN_MINUTES,
    DEFAULT_MIN_GAP_MINUTES,
    DEFAULT_QUIET_HOURS,
    NEXT_WINDOW_MAX_MINUTES,
    NEXT_WINDOW_MIN_MINUTES,
    RECENT_CHAT_DAMPENING_MINUTES,
    append_scheduler_attempts,
)
from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_scheduler_tick_complete"
FAILED_RECOMMENDATION = "inspect"
ACTIVATION_RECOMMENDATION = "m9_scheduler_activation_ready"
SCHEDULED_WAKE_OUTPUT = "internal_only"
CADENCE_MODEL = "randomized_presence_windows"
NEXT_CANDIDATE_SKIP_REASON = "next_candidate_not_reached"
ACTIVATION_NOT_READY_SKIP_REASON = "activation_report_not_ready"
WAKE_FAILED_REASON = "wake_command_failed"


@dataclass(frozen=True)
class WakeCommandResult:
    returncode: int
    duration_seconds: float = 0.0
    error_type: str | None = None
    error_message: str | None = None


WakeRunner = Callable[[str], WakeCommandResult]


@dataclass
class M9SchedulerTickResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def initialize_scheduler_presence_state(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
    random_seed: int | None = None,
    write_runtime: bool = True,
) -> dict:
    """Reset M9 dry-run state into a live randomized candidate window."""

    current = now or datetime.now()
    rng = _rng(current, random_seed)
    state = _default_presence_state(current.date())
    state.update({
        "next_candidate_after": _sample_next_candidate_after(current, rng).isoformat(),
        "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
        "cadence_model": CADENCE_MODEL,
        "live_activation_initialized_at": current.isoformat(),
        "activation_milestone": "M9.3",
    })
    if write_runtime:
        _write_json_atomic(paths.scheduler_presence_state_file, state)
    return state


def run_m9_scheduler_tick(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
    random_seed: int | None = None,
    wake_runner: WakeRunner | None = None,
    write_runtime: bool = True,
    require_activation_report: bool = True,
) -> M9SchedulerTickResult:
    """Run one scheduler opportunity check and maybe one existing wake command."""

    current = now or datetime.now()
    rng = _rng(current, random_seed)
    target_command = _target_command(paths)
    state_before = _state_with_defaults(_load_json(paths.scheduler_presence_state_file), current.date(), current, rng)
    recent_human_chat_at = _latest_human_chat_at(paths)
    activation_report = _load_json(paths.life_loop_dir / "m9_scheduler_activation_report.json")
    activation_ready = _activation_report_ready(activation_report)
    skip_reason = _pre_lock_skip_reason(
        paths,
        current,
        state_before,
        recent_human_chat_at=recent_human_chat_at,
        activation_ready=activation_ready,
        require_activation_report=require_activation_report,
    )

    wake_result: WakeCommandResult | None = None
    lock_acquired = False
    if skip_reason is None:
        with _try_scheduler_lock(paths.scheduler_wake_lock_file) as acquired:
            lock_acquired = acquired
            if not acquired:
                skip_reason = "wake_lock_active"
            else:
                runner = wake_runner or _run_wake_command
                wake_result = runner(target_command)

    if skip_reason is not None:
        state_after = _skipped_state_after(current, state_before, skip_reason)
        attempt = _attempt_row(
            current,
            decision="skipped",
            skip_reason=skip_reason,
            state_before=state_before,
            state_after=state_after,
            target_command=target_command,
            lock_acquired=lock_acquired,
            wake_result=None,
        )
    elif wake_result and wake_result.returncode == 0:
        state_after = _successful_state_after(current, state_before, rng)
        attempt = _attempt_row(
            current,
            decision="ran",
            skip_reason=None,
            state_before=state_before,
            state_after=state_after,
            target_command=target_command,
            lock_acquired=lock_acquired,
            wake_result=wake_result,
        )
    else:
        state_after = _failed_state_after(current, state_before)
        attempt = _attempt_row(
            current,
            decision="failed",
            skip_reason=None,
            state_before=state_before,
            state_after=state_after,
            target_command=target_command,
            lock_acquired=lock_acquired,
            wake_result=wake_result,
        )

    runtime_errors = []
    if write_runtime:
        try:
            append_scheduler_attempts(paths.scheduler_attempts_file, [attempt])
        except OSError as exc:
            runtime_errors.append(f"could not append scheduler attempt: {exc}")
        try:
            _write_json_atomic(paths.scheduler_presence_state_file, state_after)
        except OSError as exc:
            runtime_errors.append(f"could not write scheduler presence state: {exc}")

    ok = attempt["decision"] != "failed" and not runtime_errors
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M9.live_tick",
        "recommendation": READY_RECOMMENDATION if ok else FAILED_RECOMMENDATION,
        "companion_home": str(paths.home),
        "scheduler": {
            "mechanism": "cron",
            "attempts_file": _relative(paths, paths.scheduler_attempts_file),
            "presence_state_file": _relative(paths, paths.scheduler_presence_state_file),
            "scheduler_lock_file": _relative(paths, paths.scheduler_wake_lock_file),
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
            "cadence_model": CADENCE_MODEL,
            "target_command": target_command,
        },
        "attempt": _public_attempt(attempt),
        "state": state_after,
        "evidence": {
            "activation_report_ready": activation_ready,
            "recent_human_chat_at": recent_human_chat_at.isoformat() if recent_human_chat_at else None,
            "provider_calls_recorded_by_wrapper": 0,
            "wake_cycle_run": attempt["wake_cycle_run"],
            "raw_provider_payload_stored": False,
        },
        "boundaries": {
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
            "voice_signal_hardware_output": False,
        },
        "runtime_errors": runtime_errors,
        "errors": runtime_errors,
    }
    return M9SchedulerTickResult(ok=ok, recommendation=report["recommendation"], report=report, errors=runtime_errors)


def _run_wake_command(command: str) -> WakeCommandResult:
    start = monotonic_time.monotonic()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        return WakeCommandResult(
            returncode=1,
            duration_seconds=round(monotonic_time.monotonic() - start, 3),
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
    return WakeCommandResult(
        returncode=completed.returncode,
        duration_seconds=round(monotonic_time.monotonic() - start, 3),
    )


def _pre_lock_skip_reason(
    paths: CompanionPaths,
    now: datetime,
    state: dict,
    *,
    recent_human_chat_at: datetime | None,
    activation_ready: bool,
    require_activation_report: bool,
) -> str | None:
    if require_activation_report and not activation_ready:
        return ACTIVATION_NOT_READY_SKIP_REASON
    if paths.scheduler_pause_flag.exists():
        return "paused"
    if _in_quiet_hours(now, state["quiet_hours"]):
        return "quiet_hours"
    if state["daily_live_wake_count"] >= state["daily_live_wake_budget"]:
        return "daily_budget_exhausted"
    last_wake = _parse_datetime(state.get("last_scheduled_wake_at"))
    if last_wake and now - last_wake < timedelta(minutes=DEFAULT_MIN_GAP_MINUTES):
        return "min_gap_not_met"
    cooldown_until = _parse_datetime(state.get("cooldown_until"))
    if cooldown_until and now < cooldown_until:
        return "failure_cooldown"
    if recent_human_chat_at and now - recent_human_chat_at < timedelta(minutes=RECENT_CHAT_DAMPENING_MINUTES):
        return "recent_human_chat_dampening"
    next_candidate = _parse_datetime(state.get("next_candidate_after"))
    if next_candidate and now < next_candidate:
        return NEXT_CANDIDATE_SKIP_REASON
    return None


def _state_with_defaults(raw_state: dict | None, today: date, now: datetime, rng: random.Random) -> dict:
    state = {} if not isinstance(raw_state, dict) or raw_state.get("dry_run") is True else dict(raw_state)
    defaults = _default_presence_state(today)
    defaults.update(state)
    state = defaults
    if state.get("daily_budget_date") != today.isoformat():
        state["daily_budget_date"] = today.isoformat()
        state["daily_live_wake_count"] = 0
    if not state.get("next_candidate_after"):
        state["next_candidate_after"] = _sample_next_candidate_after(now, rng).isoformat()
    state["daily_live_wake_budget"] = DEFAULT_DAILY_LIVE_WAKE_BUDGET
    state["quiet_hours"] = list(DEFAULT_QUIET_HOURS)
    state["min_gap_minutes"] = DEFAULT_MIN_GAP_MINUTES
    state["scheduled_wake_output"] = SCHEDULED_WAKE_OUTPUT
    state["cadence_model"] = CADENCE_MODEL
    state.pop("dry_run", None)
    return state


def _default_presence_state(today: date) -> dict:
    return {
        "last_scheduled_wake_at": None,
        "next_candidate_after": None,
        "daily_live_wake_budget": DEFAULT_DAILY_LIVE_WAKE_BUDGET,
        "daily_live_wake_count": 0,
        "daily_budget_date": today.isoformat(),
        "quiet_hours": list(DEFAULT_QUIET_HOURS),
        "min_gap_minutes": DEFAULT_MIN_GAP_MINUTES,
        "cooldown_until": None,
        "last_skip_reason": None,
        "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
        "cadence_model": CADENCE_MODEL,
    }


def _successful_state_after(now: datetime, state: dict, rng: random.Random) -> dict:
    updated = dict(state)
    updated["last_scheduled_wake_at"] = now.isoformat()
    updated["last_attempt_at"] = now.isoformat()
    updated["last_skip_reason"] = None
    updated["daily_budget_date"] = now.date().isoformat()
    updated["daily_live_wake_count"] = state["daily_live_wake_count"] + 1
    updated["next_candidate_after"] = _sample_next_candidate_after(now, rng).isoformat()
    updated["cooldown_until"] = None
    return updated


def _skipped_state_after(now: datetime, state: dict, skip_reason: str | None) -> dict:
    updated = dict(state)
    updated["last_attempt_at"] = now.isoformat()
    updated["last_skip_reason"] = skip_reason
    return updated


def _failed_state_after(now: datetime, state: dict) -> dict:
    updated = dict(state)
    updated["last_attempt_at"] = now.isoformat()
    updated["last_skip_reason"] = WAKE_FAILED_REASON
    updated["cooldown_until"] = (now + timedelta(minutes=DEFAULT_FAILURE_COOLDOWN_MINUTES)).isoformat()
    return updated


def _attempt_row(
    now: datetime,
    *,
    decision: str,
    skip_reason: str | None,
    state_before: dict,
    state_after: dict,
    target_command: str,
    lock_acquired: bool,
    wake_result: WakeCommandResult | None,
) -> dict:
    return {
        "id": f"m9live_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "source": "m9_scheduler_live_tick",
        "attempted_at": now.isoformat(),
        "trigger": "scheduled-wake",
        "decision": decision,
        "skip_reason": skip_reason,
        "lock_acquired": lock_acquired,
        "wake_command": target_command,
        "wake_cycle_run": decision in {"ran", "failed"},
        "wake_command_returncode": wake_result.returncode if wake_result else None,
        "wake_command_duration_seconds": wake_result.duration_seconds if wake_result else None,
        "wake_command_error_type": wake_result.error_type if wake_result else None,
        "provider": "deepseek" if decision in {"ran", "failed"} else None,
        "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
        "raw_provider_payload_stored": False,
        "voice_signal_hardware_output": False,
        "state_before": state_before,
        "state_after": state_after,
    }


def _public_attempt(attempt: dict) -> dict:
    return {
        "id": attempt["id"],
        "decision": attempt["decision"],
        "skip_reason": attempt["skip_reason"],
        "lock_acquired": attempt["lock_acquired"],
        "wake_cycle_run": attempt["wake_cycle_run"],
        "wake_command_returncode": attempt["wake_command_returncode"],
        "scheduled_wake_output": attempt["scheduled_wake_output"],
        "raw_provider_payload_stored": attempt["raw_provider_payload_stored"],
        "voice_signal_hardware_output": attempt["voice_signal_hardware_output"],
    }


def _latest_human_chat_at(paths: CompanionPaths) -> datetime | None:
    latest = None
    for event in load_wake_events(paths.conversation_events_file):
        if event.get("trigger") != "human-text-chat":
            continue
        candidate = _parse_datetime(str(event.get("completed_at") or event.get("started_at") or ""))
        if candidate and (latest is None or candidate > latest):
            latest = candidate
    return latest


def _activation_report_ready(report: dict | None) -> bool:
    return (
        isinstance(report, dict)
        and report.get("ok") is True
        and report.get("milestone") == "M9.3"
        and report.get("recommendation") == ACTIVATION_RECOMMENDATION
        and not report.get("stop_reasons")
    )


def _target_command(paths: CompanionPaths) -> str:
    for filename in ("m9_scheduler_revalidation_report.json", "m9_scheduler_dry_run_report.json"):
        report = _load_json(paths.life_loop_dir / filename)
        if not isinstance(report, dict):
            continue
        for section in ("handoff", "dry_run"):
            payload = report.get(section) if isinstance(report.get(section), dict) else {}
            if payload.get("target_command"):
                return str(payload["target_command"])
    return (
        f"cd {shlex.quote(str(paths.home))} && "
        ".venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {shlex.quote(str(paths.home))} "
        "--provider deepseek --memory-mode json --trigger scheduled-wake"
    )


def _sample_next_candidate_after(now: datetime, rng: random.Random) -> datetime:
    return now + timedelta(minutes=rng.randint(NEXT_WINDOW_MIN_MINUTES, NEXT_WINDOW_MAX_MINUTES))


def _in_quiet_hours(now: datetime, quiet_hours: list[str]) -> bool:
    if len(quiet_hours) != 2:
        return False
    start = _parse_hhmm(quiet_hours[0])
    end = _parse_hhmm(quiet_hours[1])
    current = now.time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


@contextmanager
def _try_scheduler_lock(lock_file: Path) -> Iterator[bool]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_file, "w")
    acquired = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _rng(now: datetime, random_seed: int | None) -> random.Random:
    if random_seed is not None:
        return random.Random(random_seed)
    return random.Random(int(now.timestamp() * 1_000_000))


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(path)


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
