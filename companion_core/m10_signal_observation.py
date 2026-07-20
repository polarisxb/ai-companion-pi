"""M10.4 Signal chat observation gate.

Read-only analysis of the live attempt ledger after M10.3 activation. It
verifies reply discipline, dedupe correctness, budget behavior, and hashed-only
storage, and runs a reversible pause-flag drill. It never calls a provider,
never sends a message, and never mutates the service artifact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_SKIP_REASONS,
    SignalChatConfigError,
    evaluate_signal_message,
    load_signal_chat_attempts,
    load_signal_chat_config,
    load_signal_chat_state,
)
from .signal_transport import InboundSignalMessage

READY_RECOMMENDATION = "m10_signal_observation_ready"
M10_ACTIVATION_RECOMMENDATION = "m10_signal_activation_ready"
DEFAULT_MIN_LIVE_ATTEMPTS = 3
OBSERVED_MODES = ("live", "trial")


@dataclass
class M10SignalObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m10_signal_observation(
    paths: CompanionPaths,
    *,
    min_live_attempts: int = DEFAULT_MIN_LIVE_ATTEMPTS,
    perform_pause_drill: bool = True,
    now: datetime | None = None,
) -> M10SignalObservationResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    activation_path = paths.life_loop_dir / "m10_signal_activation_report.json"
    activation_report = _load_report(activation_path)
    source_reports["m10_signal_activation"] = _report_snapshot(paths, activation_path, activation_report)
    stages.append(_activation_stage(activation_report))

    config = None
    try:
        config = load_signal_chat_config(paths)
        stages.append(_stage(
            "config_ready",
            True,
            "signal chat config loaded",
            details={"allowed_sender_count": len(config.allowed_senders)},
        ))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    all_attempts = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    # M10 owns the Signal inbound chat contract; outbound records belong to the
    # M11 gates and feishu-channel records to the M13 gates.
    observed = [
        attempt for attempt in all_attempts
        if attempt.get("mode") in OBSERVED_MODES
        and attempt.get("direction", "inbound") == "inbound"
        and attempt.get("channel", "signal") == "signal"
    ]
    stages.append(_attempt_volume_stage(observed, min_live_attempts=min_live_attempts))
    stages.append(_decision_health_stage(observed))
    stages.append(_reply_discipline_stage(observed, config))
    stages.append(_dedupe_stage(observed))
    stages.append(_budget_stage(observed, config))
    stages.append(_hashed_storage_stage(observed))

    pause_drill = (
        _pause_drill(paths, config, now=current)
        if perform_pause_drill and config is not None
        else {"performed": False, "ready": not perform_pause_drill}
    )
    stages.append(_pause_drill_stage(pause_drill, required=perform_pause_drill))

    stages.append(_runtime_boundary_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M10.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M10 signal chat observation",
            "readonly": True,
            "observed_modes": list(OBSERVED_MODES),
            "min_live_attempts": min_live_attempts,
            "pause_drill_required": perform_pause_drill,
        },
        "source_reports": source_reports,
        "observation": _observation_payload(observed),
        "pause_drill": pause_drill,
        "signal_chat": {
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
            "state_file": _relative(paths, paths.signal_chat_state_file),
            "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
        },
        "boundaries": {
            "provider_generation_requested": False,
            "provider_calls": 0,
            "signal_send_requested": False,
            "proactive_outbound_sent": False,
            "service_mutated": False,
            "scheduler_mutated": False,
            "wake_cycle_run": False,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M10SignalObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m10_signal_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m10_signal_observation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _observation_payload(observed: list[dict]) -> dict:
    decision_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    replied_by_day: dict[str, int] = {}
    for attempt in observed:
        decision = str(attempt.get("decision"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if attempt.get("skip_reason"):
            reason = str(attempt["skip_reason"])
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
        if decision == "replied":
            day = str(attempt.get("created_at", ""))[:10]
            replied_by_day[day] = replied_by_day.get(day, 0) + 1
    return {
        "observed_attempts": len(observed),
        "decision_counts": decision_counts,
        "skip_reason_counts": skip_reason_counts,
        "replied_by_day": replied_by_day,
        "senders": sorted({str(attempt.get("sender")) for attempt in observed}),
    }


def _activation_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M10.3 activation report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M10.3 ok is not true")
        if report.get("milestone") != "M10.3":
            problems.append("milestone is not M10.3")
        if report.get("recommendation") != M10_ACTIVATION_RECOMMENDATION:
            problems.append(f"recommendation is not {M10_ACTIVATION_RECOMMENDATION}")
        service = report.get("service") if isinstance(report.get("service"), dict) else {}
        if service.get("enabled") is not True:
            problems.append("activation service is not enabled")
        if service.get("artifact_count") != 1:
            problems.append("activation must manage exactly one service artifact")
        if not service.get("rollback_command"):
            problems.append("activation is missing a rollback command")
    return _stage(
        "m10_activation_ready",
        not problems,
        "M10.3 activation evidence is ready" if not problems else "; ".join(problems),
    )


def _attempt_volume_stage(observed: list[dict], *, min_live_attempts: int) -> dict:
    if len(observed) < min_live_attempts:
        return _stage(
            "attempt_volume",
            False,
            f"observed {len(observed)} live/trial attempts; need at least {min_live_attempts}",
        )
    return _stage(
        "attempt_volume",
        True,
        f"observed {len(observed)} live/trial attempts",
        details={"observed_attempts": len(observed)},
    )


def _decision_health_stage(observed: list[dict]) -> dict:
    failed = [attempt for attempt in observed if attempt.get("decision") == "failed"]
    if failed:
        samples = sorted({
            str((attempt.get("error") or {}).get("type", "unknown"))
            for attempt in failed
        })
        return _stage(
            "decision_health",
            False,
            f"{len(failed)} observed attempt(s) failed ({samples}); inspect before freezing",
        )
    return _stage("decision_health", True, "no failed attempts in the observation window")


def _reply_discipline_stage(observed: list[dict], config) -> dict:
    problems = []
    if config is None:
        problems.append("config is required to verify the reply allowlist")
    allowed = set(config.allowed_senders) if config is not None else set()
    for attempt in observed:
        decision = attempt.get("decision")
        if decision == "replied":
            if config is not None and attempt.get("sender") not in allowed:
                problems.append(f"replied to non-allowlisted sender {attempt.get('sender')}")
            if not attempt.get("dialogue_event_id"):
                problems.append("a replied attempt is missing dialogue_event_id")
            if not attempt.get("reply_hash"):
                problems.append("a replied attempt is missing reply_hash")
        elif decision == "skipped":
            if attempt.get("skip_reason") not in SIGNAL_CHAT_SKIP_REASONS:
                problems.append(f"unknown skip reason {attempt.get('skip_reason')}")
    if problems:
        return _stage("reply_discipline", False, "; ".join(sorted(set(problems))))
    return _stage("reply_discipline", True, "every reply is allowlisted with dialogue evidence")


def _dedupe_stage(observed: list[dict]) -> dict:
    replied_keys: set[tuple] = set()
    duplicates = []
    for attempt in observed:
        if attempt.get("decision") != "replied":
            continue
        key = (attempt.get("sender"), attempt.get("message_timestamp"))
        if key in replied_keys:
            duplicates.append(key)
        replied_keys.add(key)
    if duplicates:
        return _stage(
            "dedupe_correctness",
            False,
            f"duplicate replies for {duplicates}; dedupe state is not holding",
        )
    return _stage("dedupe_correctness", True, "no message received more than one reply")


def _budget_stage(observed: list[dict], config) -> dict:
    if config is None:
        return _stage("budget_discipline", False, "config is required to verify the daily budget")
    replied_by_day: dict[str, int] = {}
    for attempt in observed:
        if attempt.get("decision") != "replied":
            continue
        day = str(attempt.get("created_at", ""))[:10]
        replied_by_day[day] = replied_by_day.get(day, 0) + 1
    over_budget = {
        day: count
        for day, count in replied_by_day.items()
        if count > config.daily_reply_budget
    }
    if over_budget:
        return _stage(
            "budget_discipline",
            False,
            f"daily reply budget {config.daily_reply_budget} exceeded: {over_budget}",
        )
    return _stage(
        "budget_discipline",
        True,
        f"daily replies stayed within budget {config.daily_reply_budget}",
        details={"replied_by_day": replied_by_day},
    )


def _hashed_storage_stage(observed: list[dict]) -> dict:
    problems = []
    for attempt in observed:
        if "body" in attempt or "reply" in attempt or "text" in attempt:
            problems.append("an attempt record stores raw message text")
        if not str(attempt.get("body_hash", "")).startswith("sha256:"):
            problems.append("an attempt record is missing a sha256 body hash")
        boundaries = attempt.get("boundaries") if isinstance(attempt.get("boundaries"), dict) else {}
        if boundaries.get("raw_signal_envelope_stored") is not False:
            problems.append("an attempt boundary claims raw envelope storage")
    if problems:
        return _stage("hashed_storage", False, "; ".join(sorted(set(problems))))
    return _stage("hashed_storage", True, "attempt records store hashes, never message bodies")


def _pause_drill(paths: CompanionPaths, config, *, now: datetime) -> dict:
    """Reversible pause drill: prove the pause flag suppresses replies."""

    flag = paths.signal_chat_pause_flag
    flag_existed = flag.exists()
    drill = {"performed": True, "flag_existed_before": flag_existed, "ready": False}
    try:
        if not flag_existed:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        state = load_signal_chat_state(paths.signal_chat_state_file)
        synthetic = InboundSignalMessage(
            sender=config.allowed_senders[0],
            timestamp=2**62,
            body="pause drill synthetic message",
        )
        decision = evaluate_signal_message(
            synthetic,
            config=config,
            state=state,
            now=now,
            paused=flag.exists(),
            replies_this_poll=0,
        )
        drill["decision"] = decision
        drill["ready"] = decision == "paused"
        drill["state_mutated"] = False
    finally:
        if not flag_existed and flag.exists():
            flag.unlink()
        drill["flag_restored"] = flag.exists() == flag_existed
    return drill


def _pause_drill_stage(drill: dict, *, required: bool) -> dict:
    if not required:
        return _stage("pause_drill", True, "pause drill skipped by request", details=drill)
    if drill.get("ready") and drill.get("flag_restored", False):
        return _stage(
            "pause_drill",
            True,
            "pause flag suppresses replies and was restored to its original state",
            details=drill,
        )
    return _stage("pause_drill", False, f"pause drill failed: {drill}")


def _runtime_boundary_stage() -> dict:
    return _stage(
        "observation_runtime_boundary",
        True,
        "observation reads the ledger only; no provider calls, sends, or service mutation",
        details={
            "provider_calls": 0,
            "signal_send_requested": False,
            "service_mutated": False,
            "scheduler_mutated": False,
        },
    )


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
