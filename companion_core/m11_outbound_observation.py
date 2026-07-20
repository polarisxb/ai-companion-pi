"""M11.5 Signal outbound observation gate.

Read-only analysis of outbound delivery records (live/trial) plus a
reversible outbound pause drill. Never sends, never calls a provider, never
mutates the outbox, service, or scheduler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_OUTBOUND_SKIP_REASONS,
    SignalChatConfigError,
    load_signal_chat_attempts,
    load_signal_chat_config,
    load_signal_chat_state,
    outbound_defer_reason,
)
from .signal_chat import _in_quiet_hours, _parse_quiet_time  # noqa: F401 - quiet-hours reuse

READY_RECOMMENDATION = "m11_signal_outbound_observation_ready"
OBSERVED_MODES = ("live", "trial")
DEFAULT_MIN_DELIVERED = 1


@dataclass
class M11OutboundObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m11_outbound_observation(
    paths: CompanionPaths,
    *,
    min_delivered: int = DEFAULT_MIN_DELIVERED,
    perform_pause_drill: bool = True,
    now: datetime | None = None,
) -> M11OutboundObservationResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    trial_path = paths.life_loop_dir / "m11_signal_outbound_trial_report.json"
    trial_report = _load_report(trial_path)
    source_reports["m11_signal_outbound_trial"] = _report_snapshot(paths, trial_path, trial_report)
    stages.append(_trial_stage(trial_report))

    config = None
    try:
        config = load_signal_chat_config(paths)
        stages.append(_stage("config_ready", True, "signal chat config loaded"))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    all_records = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    observed = [
        record for record in all_records
        if record.get("direction") == "outbound" and record.get("mode") in OBSERVED_MODES
    ]
    stages.append(_volume_stage(observed, min_delivered=min_delivered))
    stages.append(_delivery_health_stage(observed))
    stages.append(_recipient_discipline_stage(observed, config))
    stages.append(_budget_stage(observed, config))
    stages.append(_quiet_hours_stage(observed, config))
    stages.append(_dedupe_stage(observed))
    stages.append(_hashed_storage_stage(observed))

    pause_drill = (
        _outbound_pause_drill(paths, config, now=current)
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
        "milestone": "M11.5",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M11 signal outbound observation",
            "readonly": True,
            "observed_modes": list(OBSERVED_MODES),
            "min_delivered": min_delivered,
            "pause_drill_required": perform_pause_drill,
        },
        "source_reports": source_reports,
        "observation": _observation_payload(observed),
        "pause_drill": pause_drill,
        "signal_outbound": {
            "outbox_file": _relative(paths, paths.signal_outbox_file),
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
            "outbound_pause_flag_path": _relative(paths, paths.signal_outbound_pause_flag),
        },
        "boundaries": {
            "provider_generation_requested": False,
            "provider_calls": 0,
            "signal_send_requested": False,
            "outbox_mutated": False,
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
    return M11OutboundObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m11_outbound_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file)
        if report_file
        else paths.life_loop_dir / "m11_signal_outbound_observation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _observation_payload(observed: list[dict]) -> dict:
    decision_counts: dict[str, int] = {}
    skip_reason_counts: dict[str, int] = {}
    delivered_by_day: dict[str, int] = {}
    for record in observed:
        decision = str(record.get("decision"))
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if record.get("skip_reason"):
            reason = str(record["skip_reason"])
            skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
        if decision == "delivered":
            day = str(record.get("created_at", ""))[:10]
            delivered_by_day[day] = delivered_by_day.get(day, 0) + 1
    return {
        "observed_records": len(observed),
        "decision_counts": decision_counts,
        "skip_reason_counts": skip_reason_counts,
        "delivered_by_day": delivered_by_day,
        "recipients": sorted({
            str(record.get("recipient"))
            for record in observed
            if record.get("decision") == "delivered"
        }),
    }


def _trial_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M11.4 outbound trial report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M11.4 ok is not true")
        if report.get("milestone") != "M11.4":
            problems.append("milestone is not M11.4")
        if report.get("recommendation") != "m11_signal_outbound_trial_ready":
            problems.append("recommendation is not m11_signal_outbound_trial_ready")
    return _stage(
        "m11_outbound_trial_ready",
        not problems,
        "M11.4 trial evidence is ready" if not problems else "; ".join(problems),
    )


def _volume_stage(observed: list[dict], *, min_delivered: int) -> dict:
    delivered = sum(1 for record in observed if record.get("decision") == "delivered")
    if delivered < min_delivered:
        return _stage(
            "delivery_volume",
            False,
            f"observed {delivered} delivered record(s); need at least {min_delivered}",
        )
    return _stage("delivery_volume", True, f"observed {delivered} delivered record(s)")


def _delivery_health_stage(observed: list[dict]) -> dict:
    failed = [record for record in observed if record.get("decision") == "failed"]
    abandoned = [
        record for record in observed
        if record.get("skip_reason") == "abandoned_after_max_attempts"
    ]
    problems = []
    if failed:
        problems.append(f"{len(failed)} outbound delivery record(s) failed")
    if abandoned:
        problems.append(f"{len(abandoned)} outbox entr(y/ies) were abandoned")
    if problems:
        return _stage("delivery_health", False, "; ".join(problems))
    return _stage("delivery_health", True, "no failed or abandoned deliveries in the observation window")


def _recipient_discipline_stage(observed: list[dict], config) -> dict:
    if config is None:
        return _stage("recipient_discipline", False, "config is required to verify the outbound recipient")
    expected = config.resolved_outbound_recipient()
    problems = []
    for record in observed:
        if record.get("decision") == "delivered" and record.get("recipient") != expected:
            problems.append(f"delivered to unexpected recipient {record.get('recipient')}")
        if record.get("decision") == "skipped" and record.get("skip_reason") not in SIGNAL_OUTBOUND_SKIP_REASONS:
            problems.append(f"unknown outbound skip reason {record.get('skip_reason')}")
    if problems:
        return _stage("recipient_discipline", False, "; ".join(sorted(set(problems))))
    return _stage("recipient_discipline", True, f"every delivery targeted the configured recipient {expected}")


def _budget_stage(observed: list[dict], config) -> dict:
    if config is None:
        return _stage("outbound_budget_discipline", False, "config is required to verify the outbound budget")
    delivered_by_day: dict[str, int] = {}
    for record in observed:
        if record.get("decision") != "delivered":
            continue
        day = str(record.get("created_at", ""))[:10]
        delivered_by_day[day] = delivered_by_day.get(day, 0) + 1
    over = {
        day: count
        for day, count in delivered_by_day.items()
        if count > config.daily_outbound_budget
    }
    if over:
        return _stage(
            "outbound_budget_discipline",
            False,
            f"daily outbound budget {config.daily_outbound_budget} exceeded: {over}",
        )
    return _stage(
        "outbound_budget_discipline",
        True,
        f"daily deliveries stayed within budget {config.daily_outbound_budget}",
    )


def _quiet_hours_stage(observed: list[dict], config) -> dict:
    if config is None:
        return _stage("outbound_quiet_hours", False, "config is required to verify quiet-hours compliance")
    violations = []
    for record in observed:
        if record.get("decision") != "delivered":
            continue
        try:
            created = datetime.fromisoformat(str(record.get("created_at")))
        except (TypeError, ValueError):
            violations.append(f"record {record.get('id')} has an unparseable created_at")
            continue
        if _in_quiet_hours(created.time(), config.outbound_quiet_hours):
            violations.append(f"record {record.get('id')} was delivered inside quiet hours")
    if violations:
        return _stage("outbound_quiet_hours", False, "; ".join(violations))
    return _stage(
        "outbound_quiet_hours",
        True,
        f"no delivery inside quiet hours {list(config.outbound_quiet_hours)}",
    )


def _dedupe_stage(observed: list[dict]) -> dict:
    delivered_events: dict[str, int] = {}
    for record in observed:
        if record.get("decision") != "delivered":
            continue
        source = str(record.get("source_event_id"))
        delivered_events[source] = delivered_events.get(source, 0) + 1
    duplicates = {source: count for source, count in delivered_events.items() if count > 1}
    if duplicates:
        return _stage(
            "outbound_dedupe_correctness",
            False,
            f"wake events delivered more than once: {duplicates}",
        )
    return _stage("outbound_dedupe_correctness", True, "no wake event was delivered more than once")


def _hashed_storage_stage(observed: list[dict]) -> dict:
    problems = []
    for record in observed:
        if "content" in record or "body" in record or "text" in record:
            problems.append("an outbound record stores raw message text")
        if not str(record.get("content_hash", "")).startswith("sha256:"):
            problems.append("an outbound record is missing a sha256 content hash")
    if problems:
        return _stage("outbound_hashed_storage", False, "; ".join(sorted(set(problems))))
    return _stage("outbound_hashed_storage", True, "outbound records store hashes, never message text")


def _outbound_pause_drill(paths: CompanionPaths, config, *, now: datetime) -> dict:
    flag = paths.signal_outbound_pause_flag
    flag_existed = flag.exists()
    drill = {"performed": True, "flag_existed_before": flag_existed, "ready": False}
    try:
        if not flag_existed:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        state = load_signal_chat_state(paths.signal_chat_state_file)
        probe_now = now.replace(hour=12, minute=0)
        reason = outbound_defer_reason(paths, config, state, probe_now)
        drill["defer_reason"] = reason
        drill["ready"] = reason in ("outbound_paused", "chat_paused")
    finally:
        if not flag_existed and flag.exists():
            flag.unlink()
        drill["flag_restored"] = flag.exists() == flag_existed
    return drill


def _pause_drill_stage(drill: dict, *, required: bool) -> dict:
    if not required:
        return _stage("outbound_pause_drill", True, "outbound pause drill skipped by request")
    if drill.get("ready") and drill.get("flag_restored", False):
        return _stage(
            "outbound_pause_drill",
            True,
            "outbound pause flag defers delivery and was restored to its original state",
        )
    return _stage("outbound_pause_drill", False, f"outbound pause drill failed: {drill}")


def _runtime_boundary_stage() -> dict:
    return _stage(
        "observation_runtime_boundary",
        True,
        "observation reads ledgers only; no sends, provider calls, or outbox mutation",
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
