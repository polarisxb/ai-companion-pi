"""M13.4 Feishu chat observation gate (read-only).

Mirrors M10.4 scoped to ``channel=feishu`` live/trial records: volume,
decision health, allowlist discipline, dedupe, budget, hashed-only storage,
and a reversible pause drill against the shared pause flag.
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
    load_feishu_chat_config,
    load_signal_chat_attempts,
    load_signal_chat_state,
)
from .signal_transport import InboundSignalMessage

READY_RECOMMENDATION = "m13_feishu_observation_ready"
OBSERVED_MODES = ("live", "trial")
DEFAULT_MIN_LIVE_ATTEMPTS = 3


@dataclass
class M13FeishuObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m13_feishu_observation(
    paths: CompanionPaths,
    *,
    min_live_attempts: int = DEFAULT_MIN_LIVE_ATTEMPTS,
    perform_pause_drill: bool = True,
    now: datetime | None = None,
) -> M13FeishuObservationResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    activation_path = paths.life_loop_dir / "m13_feishu_activation_report.json"
    activation_report = _load_report(activation_path)
    source_reports["m13_feishu_activation"] = _report_snapshot(paths, activation_path, activation_report)
    stages.append(_activation_stage(activation_report))

    config = None
    try:
        config = load_feishu_chat_config(paths)
        stages.append(_stage("config_ready", True, "feishu chat config loaded"))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    all_attempts = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    observed = [
        attempt for attempt in all_attempts
        if attempt.get("mode") in OBSERVED_MODES
        and attempt.get("direction", "inbound") == "inbound"
        and attempt.get("channel") == "feishu"
    ]
    stages.append(_stage(
        "attempt_volume",
        len(observed) >= min_live_attempts,
        f"observed {len(observed)} feishu live/trial attempts (need {min_live_attempts})",
    ))
    failed = [attempt for attempt in observed if attempt.get("decision") == "failed"]
    stages.append(_stage(
        "decision_health",
        not failed,
        "no failed attempts in the observation window" if not failed else f"{len(failed)} attempt(s) failed",
    ))
    stages.append(_reply_discipline_stage(observed, config))
    stages.append(_dedupe_stage(observed))
    stages.append(_budget_stage(observed, config))
    stages.append(_hashed_storage_stage(observed))

    pause_drill = (
        _pause_drill(paths, config, now=current)
        if perform_pause_drill and config is not None
        else {"performed": False, "ready": not perform_pause_drill}
    )
    if perform_pause_drill:
        stages.append(_stage(
            "pause_drill",
            bool(pause_drill.get("ready") and pause_drill.get("flag_restored", False)),
            "pause flag suppresses feishu replies and was restored"
            if pause_drill.get("ready")
            else f"pause drill failed: {pause_drill}",
        ))
    else:
        stages.append(_stage("pause_drill", True, "pause drill skipped by request"))

    stages.append(_stage(
        "observation_runtime_boundary",
        True,
        "observation reads the ledger only; no provider calls, sends, or service mutation",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M13.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M13 feishu chat observation",
            "channel": "feishu",
            "readonly": True,
            "observed_modes": list(OBSERVED_MODES),
            "min_live_attempts": min_live_attempts,
        },
        "source_reports": source_reports,
        "observation": _observation_payload(observed),
        "pause_drill": pause_drill,
        "boundaries": {
            "provider_generation_requested": False,
            "provider_calls": 0,
            "feishu_send_requested": False,
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
    return M13FeishuObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m13_feishu_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m13_feishu_observation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _observation_payload(observed: list[dict]) -> dict:
    decision_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    for attempt in observed:
        decision = str(attempt.get("decision"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if attempt.get("skip_reason"):
            reason = str(attempt["skip_reason"])
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    return {
        "observed_attempts": len(observed),
        "decision_counts": decision_counts,
        "skip_reason_counts": skip_reason_counts,
        "senders": sorted({str(attempt.get("sender")) for attempt in observed}),
    }


def _activation_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M13.3 activation report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M13.3 ok is not true")
        if report.get("milestone") != "M13.3":
            problems.append("milestone is not M13.3")
        if report.get("recommendation") != "m13_feishu_activation_ready":
            problems.append("recommendation is not m13_feishu_activation_ready")
        service = report.get("service") if isinstance(report.get("service"), dict) else {}
        if service.get("enabled") is not True:
            problems.append("activation service is not enabled")
        if not service.get("rollback_command"):
            problems.append("activation is missing a rollback command")
    return _stage(
        "m13_activation_ready",
        not problems,
        "M13.3 activation evidence is ready" if not problems else "; ".join(problems),
    )


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
            if not str(attempt.get("conversation_id", "")).startswith("feishu_"):
                problems.append("a replied attempt is missing the feishu_ conversation prefix")
        elif decision == "skipped" and attempt.get("skip_reason") not in SIGNAL_CHAT_SKIP_REASONS:
            problems.append(f"unknown skip reason {attempt.get('skip_reason')}")
    if problems:
        return _stage("reply_discipline", False, "; ".join(sorted(set(problems))))
    return _stage("reply_discipline", True, "every feishu reply is allowlisted with dialogue evidence")


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
        return _stage("dedupe_correctness", False, f"duplicate replies for {duplicates}")
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
    over = {day: count for day, count in replied_by_day.items() if count > config.daily_reply_budget}
    if over:
        return _stage("budget_discipline", False, f"daily reply budget {config.daily_reply_budget} exceeded: {over}")
    return _stage("budget_discipline", True, f"daily replies stayed within budget {config.daily_reply_budget}")


def _hashed_storage_stage(observed: list[dict]) -> dict:
    problems = []
    for attempt in observed:
        if "body" in attempt or "reply" in attempt or "text" in attempt:
            problems.append("an attempt record stores raw message text")
        if not str(attempt.get("body_hash", "")).startswith("sha256:"):
            problems.append("an attempt record is missing a sha256 body hash")
    if problems:
        return _stage("hashed_storage", False, "; ".join(sorted(set(problems))))
    return _stage("hashed_storage", True, "attempt records store hashes, never message bodies")


def _pause_drill(paths: CompanionPaths, config, *, now: datetime) -> dict:
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
            body="feishu pause drill synthetic message",
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
    finally:
        if not flag_existed and flag.exists():
            flag.unlink()
        drill["flag_restored"] = flag.exists() == flag_existed
    return drill


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
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
