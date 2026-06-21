"""M9.2 supervised scheduler dry-run controller."""

from __future__ import annotations

import fcntl
import json
import random
import shlex
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator

from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_scheduler_dry_run_ready"
M9_REVALIDATION_RECOMMENDATION = "m9_scheduler_revalidation_ready"
DEFAULT_DAILY_LIVE_WAKE_BUDGET = 2
DEFAULT_QUIET_HOURS = ("00:00", "08:00")
DEFAULT_MIN_GAP_MINUTES = 180
DEFAULT_FAILURE_COOLDOWN_MINUTES = 120
RECENT_CHAT_DAMPENING_MINUTES = 45
NEXT_WINDOW_MIN_MINUTES = 90
NEXT_WINDOW_MAX_MINUTES = 240
REQUIRED_SKIP_REASONS = (
    "paused",
    "quiet_hours",
    "daily_budget_exhausted",
    "min_gap_not_met",
    "wake_lock_active",
    "failure_cooldown",
    "recent_human_chat_dampening",
)


@dataclass
class M9SchedulerDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m9_scheduler_dry_run(
    paths: CompanionPaths,
    *,
    random_seed: int = 902,
    base_date: date | None = None,
    write_runtime: bool = True,
) -> M9SchedulerDryRunResult:
    """Exercise M9 scheduler control branches without installing or running a live scheduler."""

    saved_at = datetime.now()
    source_reports: dict[str, dict] = {}
    stages: list[dict] = []

    revalidation_path = paths.life_loop_dir / "m9_scheduler_revalidation_report.json"
    revalidation_report = _load_report(revalidation_path)
    source_reports["m9_scheduler_revalidation"] = _report_snapshot(paths, revalidation_path, revalidation_report)
    revalidation_stage = _revalidation_stage(revalidation_report)
    stages.append(revalidation_stage)

    dry_run_payload = _empty_dry_run_payload(paths, revalidation_report, random_seed=random_seed)
    if revalidation_stage["status"] == "pass":
        dry_run_payload = _run_supervised_dry_run(
            paths,
            revalidation_report,
            random_seed=random_seed,
            base_date=base_date or saved_at.date(),
            write_runtime=write_runtime,
        )
        stages.append(_scenario_coverage_stage(dry_run_payload))
        stages.append(_attempt_write_stage(dry_run_payload, write_runtime=write_runtime))
        stages.append(_presence_state_stage(dry_run_payload, write_runtime=write_runtime))
    else:
        stages.append(_stage("scenario_coverage", False, "M9.1 revalidation is required before dry-run scenarios"))
        stages.append(_stage("scheduler_attempt_writes", False, "attempt writes skipped because M9.1 is not ready"))
        stages.append(_stage("presence_state_write", False, "presence state write skipped because M9.1 is not ready"))

    stages.append(_provider_boundary_stage(dry_run_payload))
    stages.append(_scheduler_boundary_stage())
    stages.append(_readonly_static_boundary_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M9.2",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": _profile(write_runtime=write_runtime),
        "source_reports": source_reports,
        "dry_run": _public_dry_run_payload(dry_run_payload),
        "scheduler": {
            "attempts_file": _relative(paths, paths.scheduler_attempts_file),
            "presence_state_file": _relative(paths, paths.scheduler_presence_state_file),
            "scheduler_lock_file": _relative(paths, paths.scheduler_wake_lock_file),
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "live_scheduler_installed": False,
            "live_scheduler_enabled": False,
        },
        "boundaries": {
            "scheduler_mutated": False,
            "cron_replacement": False,
            "timer_installation": False,
            "service_mutation_allowed": False,
            "live_scheduler_installation_requested": False,
            "wake_cycle_run": False,
            "wake_events_written": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
        "evidence": {
            "m9_1_revalidation_ready": revalidation_stage["status"] == "pass",
            "attempt_count": dry_run_payload["attempt_count"],
            "allowed_decision_count": dry_run_payload["allowed_decision_count"],
            "skip_reasons_observed": dry_run_payload["skip_reasons_observed"],
            "required_skip_reasons": list(REQUIRED_SKIP_REASONS),
            "wake_commands_simulated": dry_run_payload["wake_commands_simulated"],
            "provider_calls": 0,
            "runtime_written": dry_run_payload["runtime_written"],
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m9_scheduler_dry_run": _shell_command([
                "python3",
                "scripts/run_m9_scheduler_dry_run.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m9_scheduler_activation_later": "requires m9_scheduler_revalidation_ready and m9_scheduler_dry_run_ready",
        },
    }
    return M9SchedulerDryRunResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m9_scheduler_dry_run_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m9_scheduler_dry_run_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def load_scheduler_attempts(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    attempts = []
    for line in lines:
        if line.strip():
            attempts.append(json.loads(line))
    return attempts


def _run_supervised_dry_run(
    paths: CompanionPaths,
    revalidation_report: dict | None,
    *,
    random_seed: int,
    base_date: date,
    write_runtime: bool,
) -> dict:
    rng = random.Random(random_seed)
    scenarios = _default_scenarios(base_date)
    target_command = _target_command(paths, revalidation_report)
    attempts = []
    final_state = _default_presence_state(base_date)
    runtime_errors = []

    for scenario in scenarios:
        attempt, state_after = _run_scenario(paths, scenario, rng, target_command=target_command)
        attempts.append(attempt)
        if attempt["decision"] == "would_run":
            final_state = state_after

    final_state["dry_run"] = True
    final_state["dry_run_attempt_count"] = len(attempts)
    final_state["dry_run_skip_reasons"] = sorted(
        {
            attempt["skip_reason"]
            for attempt in attempts
            if attempt.get("skip_reason")
        }
    )
    final_state["dry_run_last_attempt_at"] = attempts[-1]["attempted_at"] if attempts else None

    if write_runtime:
        try:
            append_scheduler_attempts(paths.scheduler_attempts_file, attempts)
        except OSError as exc:
            runtime_errors.append(f"could not append scheduler attempts: {exc}")
        try:
            _write_json_atomic(paths.scheduler_presence_state_file, final_state)
        except OSError as exc:
            runtime_errors.append(f"could not write scheduler presence state: {exc}")

    skip_reasons = [
        attempt["skip_reason"]
        for attempt in attempts
        if attempt.get("decision") == "skipped" and attempt.get("skip_reason")
    ]
    allowed_attempts = [attempt for attempt in attempts if attempt.get("decision") == "would_run"]
    return {
        "attempts": attempts,
        "attempt_count": len(attempts),
        "attempt_ids": [attempt["id"] for attempt in attempts],
        "allowed_decision_count": len(allowed_attempts),
        "skip_reasons_observed": sorted(set(skip_reasons)),
        "wake_commands_simulated": len(allowed_attempts),
        "target_command": target_command,
        "presence_state": final_state,
        "runtime_written": write_runtime and not runtime_errors,
        "runtime_errors": runtime_errors,
        "random_seed": random_seed,
    }


def append_scheduler_attempts(path: Path, attempts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as attempts_fd:
                for attempt in attempts:
                    attempts_fd.write(json.dumps(attempt, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _run_scenario(paths: CompanionPaths, scenario: dict, rng: random.Random, *, target_command: str) -> tuple[dict, dict]:
    now = scenario["now"]
    state_before = _state_with_defaults(scenario.get("state", {}), now.date())
    skip_reason = _pre_lock_skip_reason(
        now,
        state_before,
        pause_active=bool(scenario.get("pause_active")),
        recent_human_chat_at=scenario.get("recent_human_chat_at"),
    )
    hold_fd = None
    lock_acquired = False
    try:
        if scenario.get("hold_lock"):
            paths.scheduler_wake_lock_file.parent.mkdir(parents=True, exist_ok=True)
            hold_fd = open(paths.scheduler_wake_lock_file, "w")
            fcntl.flock(hold_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if skip_reason is None:
            with _try_scheduler_lock(paths.scheduler_wake_lock_file) as acquired:
                lock_acquired = acquired
                if not acquired:
                    skip_reason = "wake_lock_active"
                else:
                    state_after = _allowed_state_after(now, state_before, rng)
                    return _attempt_row(
                        scenario,
                        now,
                        decision="would_run",
                        skip_reason=None,
                        state_before=state_before,
                        state_after=state_after,
                        target_command=target_command,
                        lock_acquired=True,
                    ), state_after
        state_after = _skipped_state_after(now, state_before, skip_reason)
        return _attempt_row(
            scenario,
            now,
            decision="skipped",
            skip_reason=skip_reason,
            state_before=state_before,
            state_after=state_after,
            target_command=target_command,
            lock_acquired=lock_acquired,
        ), state_after
    finally:
        if hold_fd is not None:
            fcntl.flock(hold_fd, fcntl.LOCK_UN)
            hold_fd.close()


def _pre_lock_skip_reason(
    now: datetime,
    state: dict,
    *,
    pause_active: bool,
    recent_human_chat_at: datetime | None,
) -> str | None:
    if pause_active:
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
    return None


def _allowed_state_after(now: datetime, state: dict, rng: random.Random) -> dict:
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


def _attempt_row(
    scenario: dict,
    now: datetime,
    *,
    decision: str,
    skip_reason: str | None,
    state_before: dict,
    state_after: dict,
    target_command: str,
    lock_acquired: bool,
) -> dict:
    return {
        "id": f"m9dry_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "source": "m9_scheduler_dry_run",
        "scenario": scenario["name"],
        "attempted_at": now.isoformat(),
        "trigger": "scheduled-wake",
        "decision": decision,
        "skip_reason": skip_reason,
        "lock_acquired": lock_acquired,
        "wake_command": target_command,
        "wake_command_simulated": decision == "would_run",
        "wake_cycle_run": False,
        "provider": "fake",
        "provider_calls": 0,
        "raw_provider_payload_stored": False,
        "state_before": state_before,
        "state_after": state_after,
    }


def _default_scenarios(base_date: date) -> list[dict]:
    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(base_date, time(hour=hour, minute=minute))

    return [
        {"name": "paused", "now": at(10), "pause_active": True},
        {"name": "quiet_hours", "now": at(1)},
        {
            "name": "daily_budget_exhausted",
            "now": at(10, 30),
            "state": {"daily_budget_date": base_date.isoformat(), "daily_live_wake_count": 2},
        },
        {
            "name": "min_gap_not_met",
            "now": at(11),
            "state": {"last_scheduled_wake_at": at(10, 15).isoformat()},
        },
        {"name": "wake_lock_active", "now": at(12), "hold_lock": True},
        {
            "name": "failure_cooldown",
            "now": at(13),
            "state": {"cooldown_until": (at(13) + timedelta(minutes=DEFAULT_FAILURE_COOLDOWN_MINUTES)).isoformat()},
        },
        {
            "name": "recent_human_chat_dampening",
            "now": at(14),
            "recent_human_chat_at": at(13, 40),
        },
        {"name": "allowed_window", "now": at(15)},
    ]


def _state_with_defaults(overrides: dict, today: date) -> dict:
    state = _default_presence_state(today)
    state.update(overrides)
    if state.get("daily_budget_date") != today.isoformat():
        state["daily_budget_date"] = today.isoformat()
        state["daily_live_wake_count"] = 0
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
    }


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


def _revalidation_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M9.1 revalidation report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M9.1 revalidation ok is not true")
        if report.get("milestone") != "M9.1":
            problems.append("M9.1 revalidation milestone is not M9.1")
        if report.get("recommendation") != M9_REVALIDATION_RECOMMENDATION:
            problems.append(f"M9.1 recommendation is not {M9_REVALIDATION_RECOMMENDATION}")
        if report.get("stop_reasons"):
            problems.append("M9.1 revalidation has stop_reasons")
        boundaries = report.get("boundaries") if isinstance(report.get("boundaries"), dict) else {}
        for key in (
            "scheduler_mutated",
            "wake_cycle_run",
            "provider_generation_requested",
            "life_write_route_added",
        ):
            if boundaries.get(key) is True:
                problems.append(f"M9.1 boundary {key} is true")
    return _stage(
        "m9_scheduler_revalidation",
        not problems,
        "M9.1 scheduler revalidation is ready" if not problems else "; ".join(problems),
    )


def _scenario_coverage_stage(payload: dict) -> dict:
    missing = [reason for reason in REQUIRED_SKIP_REASONS if reason not in payload["skip_reasons_observed"]]
    problems = []
    if missing:
        problems.append("missing skip reasons: " + ", ".join(missing))
    if payload["allowed_decision_count"] < 1:
        problems.append("no allowed dry-run scheduler opportunity was exercised")
    if payload["wake_commands_simulated"] < 1:
        problems.append("wake command shape was not simulated")
    return _stage(
        "scenario_coverage",
        not problems,
        "dry-run exercised all scheduler skip reasons and one allowed opportunity"
        if not problems
        else "; ".join(problems),
        details={
            "skip_reasons_observed": payload["skip_reasons_observed"],
            "allowed_decision_count": payload["allowed_decision_count"],
            "wake_commands_simulated": payload["wake_commands_simulated"],
        },
    )


def _attempt_write_stage(payload: dict, *, write_runtime: bool) -> dict:
    problems = []
    if write_runtime and not payload["runtime_written"]:
        problems.extend(payload["runtime_errors"] or ["attempt runtime writes failed"])
    if payload["attempt_count"] < len(REQUIRED_SKIP_REASONS) + 1:
        problems.append("not enough scheduler attempts were recorded")
    return _stage(
        "scheduler_attempt_writes",
        not problems,
        "scheduler attempt ledger writes were exercised"
        if not problems
        else "; ".join(problems),
        details={
            "write_runtime": write_runtime,
            "runtime_written": payload["runtime_written"],
            "attempt_count": payload["attempt_count"],
        },
    )


def _presence_state_stage(payload: dict, *, write_runtime: bool) -> dict:
    state = payload.get("presence_state") if isinstance(payload.get("presence_state"), dict) else {}
    required = ("last_scheduled_wake_at", "next_candidate_after", "daily_live_wake_budget", "quiet_hours")
    missing = [key for key in required if key not in state]
    problems = []
    if missing:
        problems.append("presence state missing fields: " + ", ".join(missing))
    if state.get("dry_run") is not True:
        problems.append("presence state is not marked dry_run")
    if write_runtime and not payload["runtime_written"]:
        problems.extend(payload["runtime_errors"] or ["presence state runtime write failed"])
    return _stage(
        "presence_state_write",
        not problems,
        "scheduler presence state write was exercised"
        if not problems
        else "; ".join(problems),
        details={
            "write_runtime": write_runtime,
            "runtime_written": payload["runtime_written"],
            "last_scheduled_wake_at": state.get("last_scheduled_wake_at"),
            "next_candidate_after": state.get("next_candidate_after"),
        },
    )


def _provider_boundary_stage(payload: dict) -> dict:
    provider_calls = sum(_count_int(attempt.get("provider_calls")) for attempt in payload.get("attempts", []))
    return _stage(
        "provider_boundary",
        provider_calls == 0,
        "dry-run used fake/dry-run wake mode and made no provider calls"
        if provider_calls == 0
        else f"dry-run reported provider calls: {provider_calls}",
        details={"provider_calls": provider_calls, "wake_cycle_run": False},
    )


def _scheduler_boundary_stage() -> dict:
    return _stage(
        "scheduler_installation_boundary",
        True,
        "dry-run does not install cron, systemd timers, services, or live scheduler artifacts",
        details={
            "cron_replacement": False,
            "timer_installation": False,
            "service_mutation_allowed": False,
            "live_scheduler_installation_requested": False,
            "scheduler_mutated": False,
        },
    )


def _readonly_static_boundary_stage() -> dict:
    return _stage(
        "m9_dry_run_static_boundary",
        True,
        "M9.2 dry-run path records scheduler attempts and state only",
        details={
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "raw_provider_payload_stored": False,
        },
    )


def _empty_dry_run_payload(paths: CompanionPaths, revalidation_report: dict | None, *, random_seed: int) -> dict:
    return {
        "attempts": [],
        "attempt_count": 0,
        "attempt_ids": [],
        "allowed_decision_count": 0,
        "skip_reasons_observed": [],
        "wake_commands_simulated": 0,
        "target_command": _target_command(paths, revalidation_report),
        "presence_state": {},
        "runtime_written": False,
        "runtime_errors": [],
        "random_seed": random_seed,
    }


def _public_dry_run_payload(payload: dict) -> dict:
    return {
        "attempt_count": payload["attempt_count"],
        "attempt_ids": payload["attempt_ids"],
        "allowed_decision_count": payload["allowed_decision_count"],
        "skip_reasons_observed": payload["skip_reasons_observed"],
        "wake_commands_simulated": payload["wake_commands_simulated"],
        "target_command": payload["target_command"],
        "presence_state": payload["presence_state"],
        "runtime_written": payload["runtime_written"],
        "runtime_errors": payload["runtime_errors"],
        "random_seed": payload["random_seed"],
        "attempts": [
            {
                "id": attempt["id"],
                "scenario": attempt["scenario"],
                "decision": attempt["decision"],
                "skip_reason": attempt["skip_reason"],
                "lock_acquired": attempt["lock_acquired"],
                "wake_command_simulated": attempt["wake_command_simulated"],
            }
            for attempt in payload["attempts"]
        ],
    }


def _profile(*, write_runtime: bool) -> dict:
    return {
        "name": "M9 supervised scheduler dry run",
        "supervised_dry_run": True,
        "writes_report": True,
        "writes_scheduler_attempts": write_runtime,
        "writes_presence_state": write_runtime,
        "wake_cycle_run": False,
        "wake_events_written": False,
        "provider_generation_requested": False,
        "provider_calls": 0,
        "scheduler_mutation_allowed": False,
        "cron_replacement": False,
        "timer_installation": False,
        "service_mutation_allowed": False,
        "live_scheduler_installation_requested": False,
        "life_write_route_allowed": False,
        "voice_signal_hardware_activation_allowed": False,
        "semantic_shadow_authoritative": False,
        "raw_provider_payload_storage_allowed": False,
    }


def _target_command(paths: CompanionPaths, report: dict | None) -> str:
    if isinstance(report, dict):
        handoff = report.get("handoff") if isinstance(report.get("handoff"), dict) else {}
        if handoff.get("target_command"):
            return str(handoff["target_command"])
    return (
        f"cd {shlex.quote(str(paths.home))} && "
        ".venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {shlex.quote(str(paths.home))} "
        "--provider deepseek --memory-mode json --trigger scheduled-wake"
    )


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


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(path)


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


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def _count_int(value) -> int:
    return value if type(value) is int else 0
