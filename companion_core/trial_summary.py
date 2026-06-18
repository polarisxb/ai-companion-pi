"""Summaries for low-risk real wake trials."""

from __future__ import annotations

from collections import Counter

from .events import load_wake_events
from .paths import CompanionPaths


def build_trial_summary(
    paths: CompanionPaths,
    *,
    limit: int = 10,
    since_trigger: str | None = None,
) -> dict:
    events = _select_events(load_wake_events(paths.wake_events_file), limit, since_trigger)
    providers = Counter(event.get("provider", "unknown") for event in events)
    memory_backends = Counter(event.get("memory_backend", "unknown") for event in events)
    quality_warnings = [
        warning
        for event in events
        for warning in event.get("quality", {}).get("warnings", [])
    ]
    blocking_quality_warnings = _quality_gate_warnings(events, "blocking_warnings")
    advisory_quality_warnings = _quality_gate_warnings(events, "advisory_warnings")
    request_errors = [
        error
        for event in events
        for error in event.get("request_errors", [])
    ]
    memory_write_failures = [
        result
        for event in events
        for result in event.get("memory_write_results", [])
        if result.get("status") == "failed"
    ]
    failed = sum(1 for event in events if event.get("status") != "completed")
    request_count = sum(len(event.get("request_ids", [])) for event in events)
    context_rejections = [
        event for event in events
        if event.get("quality_gate", {}).get("context_eligible") is False
    ]
    memory_policy = _sum_event_counts(events, "memory_policy", (
        "accepted",
        "rejected",
        "prompt_eligible",
    ))
    memory_evaluations = _sum_event_counts(events, "memory_evaluations", (
        "approved",
        "rejected",
        "unchanged",
    ))
    grounding = _sum_event_counts(events, "grounding", (
        "supported",
        "unsupported",
        "ignored",
    ))
    repairs = _sum_repairs(events)
    semantic_shadow = _sum_semantic_shadow(events)
    context_capsule_updates = sum(
        1
        for event in events
        if isinstance(event.get("accepted_context"), dict)
        and event["accepted_context"].get("context_capsule_updated") is True
    )
    stop_reasons = _stop_reasons(
        event_count=len(events),
        failed=failed,
        blocking_quality_warning_count=len(blocking_quality_warnings),
        request_error_count=len(request_errors),
        memory_write_failure_count=len(memory_write_failures),
        context_rejection_count=len(context_rejections),
    )

    return {
        "ok": bool(events) and not stop_reasons,
        "recommendation": "continue" if events and not stop_reasons else "stop",
        "events_considered": len(events),
        "completed": len(events) - failed,
        "failed": failed,
        "providers": dict(providers),
        "memory_backends": dict(memory_backends),
        "memory_count": sum(len(event.get("memory_ids", [])) for event in events),
        "request_count": request_count,
        "request_error_count": len(request_errors),
        "quality_warning_count": len(quality_warnings),
        "quality_warnings": quality_warnings,
        "blocking_quality_warning_count": len(blocking_quality_warnings),
        "blocking_quality_warnings": blocking_quality_warnings,
        "advisory_quality_warning_count": len(advisory_quality_warnings),
        "advisory_quality_warnings": advisory_quality_warnings,
        "memory_write_failures": len(memory_write_failures),
        "context_rejection_count": len(context_rejections),
        "context_capsule_updates": context_capsule_updates,
        "memory_policy": memory_policy,
        "memory_evaluations": memory_evaluations,
        "grounding": grounding,
        "repairs": repairs,
        "semantic_shadow": semantic_shadow,
        "stop_reasons": stop_reasons,
        "since_trigger": since_trigger,
        "latest_event": events[-1].get("id") if events else None,
        "latest_trigger": events[-1].get("trigger") if events else None,
    }


def _select_events(events: list[dict], limit: int, since_trigger: str | None) -> list[dict]:
    selected = events
    if since_trigger:
        for index, event in enumerate(events):
            if str(event.get("trigger", "")).startswith(since_trigger):
                selected = events[index:]
                break
        else:
            selected = []
    return selected[-limit:] if limit else selected


def _sum_event_counts(events: list[dict], key: str, fields: tuple[str, ...]) -> dict:
    return {
        field: sum(
            _count_value(event.get(key, {}).get(field, 0))
            for event in events
            if isinstance(event.get(key), dict)
        )
        for field in fields
    }


def _count_value(value) -> int:
    if type(value) is int:
        return value
    return 0


def _quality_gate_warnings(events: list[dict], key: str) -> list[str]:
    warnings = []
    for event in events:
        quality_gate = event.get("quality_gate")
        if isinstance(quality_gate, dict):
            values = quality_gate.get(key, [])
            if isinstance(values, list):
                warnings.extend(str(value) for value in values)
            continue
        if key == "blocking_warnings":
            warnings.extend(
                str(value)
                for value in event.get("quality", {}).get("warnings", [])
            )
    return warnings


def _sum_repairs(events: list[dict]) -> dict:
    repair_events = [
        event.get("repair")
        for event in events
        if isinstance(event.get("repair"), dict)
    ]
    return {
        "attempted": sum(1 for repair in repair_events if repair.get("attempted") is True),
        "succeeded": sum(1 for repair in repair_events if repair.get("succeeded") is True),
        "failed": sum(
            1
            for repair in repair_events
            if repair.get("attempted") is True and repair.get("succeeded") is not True
        ),
    }


def _sum_semantic_shadow(events: list[dict]) -> dict:
    shadows = [
        event.get("semantic_shadow")
        for event in events
        if isinstance(event.get("semantic_shadow"), dict)
    ]
    return {
        "events": len(shadows),
        "enabled": sum(1 for shadow in shadows if shadow.get("enabled") is True),
        "attempted": sum(_count_value(shadow.get("attempted", 0)) for shadow in shadows),
        "succeeded": sum(_count_value(shadow.get("succeeded", 0)) for shadow in shadows),
        "failed": sum(_count_value(shadow.get("failed", 0)) for shadow in shadows),
        "skipped": sum(_count_value(shadow.get("skipped", 0)) for shadow in shadows),
    }


def _stop_reasons(
    *,
    event_count: int,
    failed: int,
    blocking_quality_warning_count: int,
    request_error_count: int,
    memory_write_failure_count: int,
    context_rejection_count: int,
) -> list[str]:
    reasons = []
    if event_count == 0:
        reasons.append("no wake events in selected window")
    if failed:
        reasons.append(f"failed wakes ({failed})")
    if blocking_quality_warning_count:
        reasons.append(f"blocking quality warnings ({blocking_quality_warning_count})")
    if request_error_count:
        reasons.append(f"request errors ({request_error_count})")
    if memory_write_failure_count:
        reasons.append(f"memory write failures ({memory_write_failure_count})")
    if context_rejection_count:
        reasons.append(f"context rejected ({context_rejection_count})")
    return reasons
