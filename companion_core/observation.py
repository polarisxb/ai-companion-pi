"""M4 long-running runtime observation checks."""

from __future__ import annotations

from datetime import datetime
import shlex
from pathlib import Path

from .events import load_wake_events
from .paths import CompanionPaths


def run_m4_observation_check(
    paths: CompanionPaths,
    *,
    observation_hours: int = 24,
    min_completed_events: int = 2,
    since: str | None = None,
    trigger_prefix: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Summarize whether a long-running M4 observation window is stable."""

    now = now or datetime.now()
    since_dt = _parse_datetime(since) if since else None
    events = [
        event
        for event in load_wake_events(paths.wake_events_file)
        if _event_in_scope(event, since_dt=since_dt, trigger_prefix=trigger_prefix)
    ]
    events.sort(key=lambda event: _event_time(event) or datetime.min)

    completed = [event for event in events if event.get("status") == "completed"]
    failures = [_event_failure(event) for event in events]
    failures = [failure for failure in failures if failure]
    advisory = [_semantic_shadow_advisory(event) for event in events]
    advisory = [item for item in advisory if item]

    observed_hours = _observed_hours(events, now)
    pending_reasons = []
    if not events:
        pending_reasons.append("no wake events in observation scope")
    if len(completed) < min_completed_events:
        pending_reasons.append(f"completed wake events {len(completed)} < required {min_completed_events}")
    if observed_hours < observation_hours:
        pending_reasons.append(f"observed hours {observed_hours:.2f} < required {observation_hours}")

    stages = [
        _stage(
            "observation_scope",
            bool(events),
            required=False,
            status="passed" if events else "pending",
            message="wake events found in observation scope" if events else "no wake events in observation scope",
            details={
                "event_count": len(events),
                "completed_count": len(completed),
                "since": since,
                "trigger_prefix": trigger_prefix,
                "observation_hours": observation_hours,
                "min_completed_events": min_completed_events,
            },
        ),
        _stage(
            "observation_window",
            not pending_reasons,
            required=False,
            status="passed" if not pending_reasons else "pending",
            message="observation window is complete" if not pending_reasons else "; ".join(pending_reasons),
            details={
                "observed_hours": round(observed_hours, 3),
                "required_hours": observation_hours,
                "completed_count": len(completed),
                "required_completed_events": min_completed_events,
            },
        ),
        _stage(
            "event_health",
            not failures,
            required=True,
            message="wake events are healthy" if not failures else f"wake event problems ({len(failures)})",
            details={"failures": failures},
        ),
        _stage(
            "semantic_shadow_observation",
            True,
            required=False,
            status="warning" if advisory else "passed",
            message="semantic shadow observation has advisory failures" if advisory else "semantic shadow observation is clean",
            details={"advisory": advisory},
        ),
    ]

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    recommendation = (
        "inspect"
        if stop_reasons
        else "continue_observation"
        if pending_reasons
        else "stable_runtime_observed"
    )
    return {
        "ok": recommendation == "stable_runtime_observed",
        "milestone": "M4.8",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "name": "m4-runtime-observation",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "observation_hours": observation_hours,
            "min_completed_events": min_completed_events,
            "since": since,
            "trigger_prefix": trigger_prefix,
        },
        "summary": {
            "event_count": len(events),
            "completed_count": len(completed),
            "failed_count": len([event for event in events if event.get("status") != "completed"]),
            "observed_hours": round(observed_hours, 3),
            "first_event": events[0].get("id") if events else None,
            "latest_event": events[-1].get("id") if events else None,
            "semantic_shadow_advisory_count": len(advisory),
        },
        "stages": stages,
        "pending_reasons": pending_reasons,
        "stop_reasons": stop_reasons,
        "next_commands": {
            "observation_check": _shell_command([
                "python3",
                "scripts/run_m4_observation_check.py",
                "--companion-home",
                str(paths.home),
                "--hours",
                str(observation_hours),
            ]),
            "post_change_guard": _shell_command([
                "python3",
                "scripts/run_m4_post_change_guard.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
    }


def _event_in_scope(event: dict, *, since_dt: datetime | None, trigger_prefix: str | None) -> bool:
    event_time = _event_time(event)
    if since_dt and (event_time is None or event_time < since_dt):
        return False
    if trigger_prefix and not str(event.get("trigger", "")).startswith(trigger_prefix):
        return False
    return True


def _event_time(event: dict) -> datetime | None:
    for key in ("started_at", "completed_at"):
        parsed = _parse_datetime(event.get(key))
        if parsed:
            return parsed
    return None


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _observed_hours(events: list[dict], now: datetime) -> float:
    if not events:
        return 0.0
    first = _event_time(events[0])
    last = _event_time(events[-1]) or now
    if first is None:
        return 0.0
    return max((last - first).total_seconds() / 3600, 0.0)


def _event_failure(event: dict) -> dict | None:
    problems = []
    if event.get("status") != "completed":
        problems.append(f"status={event.get('status')!r}")
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
    if output_audit.get("raw_output_storage") not in (None, "hash_only"):
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


def _semantic_shadow_advisory(event: dict) -> dict | None:
    shadow = event.get("semantic_shadow") if isinstance(event.get("semantic_shadow"), dict) else {}
    if _count_int(shadow.get("failed")) <= 0:
        return None
    return {
        "id": event.get("id"),
        "trigger": event.get("trigger"),
        "failed": shadow.get("failed"),
    }


def _raw_output_stored(output_audit: dict) -> bool:
    if output_audit.get("initial_raw_output_stored") is True or output_audit.get("final_raw_output_stored") is True:
        return True
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _stage(
    name: str,
    ok: bool,
    *,
    required: bool,
    message: str,
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


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
