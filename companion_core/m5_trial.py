"""M5.5 controlled real-provider quality trial."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Callable

from .events import load_wake_events
from .lifecycle import LifeLoopRunner
from .llm import DEEPSEEK_API_KEY_ENV, LLMClient, create_llm_client
from .memory import JsonMemoryStore
from .output_archive import should_store_raw_outputs
from .paths import CompanionPaths
from .secrets import load_local_secrets
from .wake_trial import classify_wake_trial_failure

ClientFactory = Callable[[int], LLMClient]

EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
DEFAULT_TRIGGER = "m5-manual-quality-trial"


def run_m5_quality_trial(
    paths: CompanionPaths,
    *,
    cycles: int = 3,
    trigger: str = DEFAULT_TRIGGER,
    timeout_seconds: int = 300,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
    client_factory: ClientFactory | None = None,
) -> dict:
    """Run an explicit M5 real-provider quality trial.

    Tests may pass ``client_factory`` to avoid a provider call. The default path
    creates a real DeepSeek client and runs accepted lifecycle writes through the
    normal gate stack.
    """

    cycles = max(1, cycles)
    secrets = load_local_secrets(paths)
    stages = [
        _m4_guard_stage(paths),
        _m5_quality_stage(paths),
        _profile_stage(cycles, trigger),
        _api_key_stage(api_key_env, secrets),
        _raw_output_storage_stage(),
    ]
    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    if stop_reasons:
        return _report(paths, cycles, trigger, stages, [], stop_reasons)

    attempts = []
    trial_stop_reasons: list[str] = []
    for cycle in range(1, cycles + 1):
        cycle_trigger = f"{trigger}:cycle-{cycle}"
        try:
            client = (
                client_factory(cycle)
                if client_factory is not None
                else create_llm_client(
                    EXPECTED_PROVIDER,
                    timeout_seconds=timeout_seconds,
                    model=model,
                    base_url=base_url,
                    api_key_env=api_key_env,
                )
            )
            result = LifeLoopRunner(
                paths,
                llm_client=client,
                memory_store=JsonMemoryStore(paths.memory_store),
            ).run_once(trigger=cycle_trigger, provider=EXPECTED_PROVIDER)
            event = result.event or {}
            event_stop_reasons = _event_stop_reasons(event)
            attempts.append(_attempt_record(cycle, cycle_trigger, "completed", event))
            if event_stop_reasons:
                trial_stop_reasons.extend(
                    f"cycle {cycle}: {reason}" for reason in event_stop_reasons
                )
                break
        except Exception as exc:
            event = _latest_event(paths)
            failure = classify_wake_trial_failure(exc)
            attempts.append(_attempt_record(
                cycle,
                cycle_trigger,
                "failed",
                event,
                error={
                    "type": type(exc).__name__,
                    "message": _short_message(str(exc)),
                    "category": failure["category"],
                    "retryable": failure["retryable"],
                },
            ))
            trial_stop_reasons.append(f"cycle {cycle}: {failure['reason']}")
            break

    if len(attempts) < cycles and not trial_stop_reasons:
        trial_stop_reasons.append(f"completed cycles {len(attempts)} < requested {cycles}")
    return _report(paths, cycles, trigger, stages, attempts, trial_stop_reasons)


def _m4_guard_stage(paths: CompanionPaths) -> dict:
    report, error = _read_report(paths.life_loop_dir / "m4_post_change_guard_report.json")
    if error:
        return _stage("m4_post_change_guard", False, True, error)
    problems = []
    if report.get("ok") is not True:
        problems.append("M4 post-change guard ok is not true")
    if report.get("milestone") != "M4.7":
        problems.append("M4 post-change guard milestone is not M4.7")
    if report.get("recommendation") != "m4_still_deployable":
        problems.append("M4 post-change guard recommendation is not m4_still_deployable")
    return _stage(
        "m4_post_change_guard",
        not problems,
        True,
        "M4 post-change guard supports M5.5"
        if not problems else "; ".join(problems),
        details=_report_snapshot(report),
    )


def _m5_quality_stage(paths: CompanionPaths) -> dict:
    report, error = _read_report(paths.life_loop_dir / "m5_quality_report.json")
    if error:
        return _stage("m5_quality_report", False, True, error)
    problems = []
    if report.get("ok") is not True:
        problems.append("M5 quality report ok is not true")
    if report.get("milestone") != "M5.1":
        problems.append("M5 quality report milestone is not M5.1")
    if report.get("recommendation") != "ready_for_quality_tuning":
        problems.append("M5 quality report recommendation is not ready_for_quality_tuning")
    return _stage(
        "m5_quality_report",
        not problems,
        True,
        "M5 quality report supports M5.5"
        if not problems else "; ".join(problems),
        details=_report_snapshot(report),
    )


def _profile_stage(cycles: int, trigger: str) -> dict:
    return _stage(
        "trial_profile",
        True,
        True,
        "M5.5 trial profile is explicit deepseek + json",
        details={
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cycles": cycles,
            "trigger": trigger,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": True,
            "provider_generation_requested": True,
        },
    )


def _api_key_stage(api_key_env: str, secrets: dict) -> dict:
    resolved = _resolved_deepseek_api_key_env(api_key_env)
    present = bool(os.environ.get(resolved))
    return _stage(
        "deepseek_api_key",
        present,
        True,
        f"API key loaded from {resolved}" if present else f"{resolved} is not set",
        details={
            "api_key_env": resolved,
            "present": present,
            "secret_loaded_from_file": resolved in secrets.get("loaded", []),
            "secret_file_exists": secrets.get("exists") is True,
        },
    )


def _raw_output_storage_stage() -> dict:
    raw_enabled = should_store_raw_outputs()
    return _stage(
        "raw_output_storage",
        not raw_enabled,
        True,
        "raw model output storage is hash-only"
        if not raw_enabled
        else "raw model output storage is enabled; unset COMPANION_STORE_RAW_OUTPUTS before M5.5",
        details={"raw_output_storage": "enabled" if raw_enabled else "hash_only"},
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


def _report_snapshot(report: dict) -> dict:
    return {
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "saved_at": report.get("saved_at"),
    }


def _resolved_deepseek_api_key_env(api_key_env: str) -> str:
    if api_key_env != "COMPANION_LLM_API_KEY":
        return api_key_env
    if os.environ.get(api_key_env):
        return api_key_env
    return DEEPSEEK_API_KEY_ENV


def _event_stop_reasons(event: dict) -> list[str]:
    reasons = []
    if event.get("status") != "completed":
        reasons.append("wake event did not complete")
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if gate.get("context_eligible") is False:
        reasons.append("quality gate rejected future-context writes")
    blocking = gate.get("blocking_warnings")
    if isinstance(blocking, list) and blocking:
        reasons.append(f"blocking quality warnings ({len(blocking)})")
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        reasons.append(f"unsupported grounding claims ({_count_int(grounding.get('unsupported'))})")
    if event.get("request_errors"):
        reasons.append(f"request errors ({len(event.get('request_errors', []))})")
    memory_failures = [
        result for result in event.get("memory_write_results", [])
        if isinstance(result, dict) and result.get("status") == "failed"
    ]
    if memory_failures:
        reasons.append(f"memory write failures ({len(memory_failures)})")
    output_audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
    if output_audit.get("raw_output_storage") != "hash_only":
        reasons.append("raw output storage is not hash-only")
    if _raw_output_stored(output_audit):
        reasons.append("raw model output was stored")
    return reasons


def _attempt_record(
    cycle: int,
    trigger: str,
    status: str,
    event: dict,
    *,
    error: dict | None = None,
) -> dict:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    quality = event.get("quality") if isinstance(event.get("quality"), dict) else {}
    record = {
        "cycle": cycle,
        "trigger": trigger,
        "status": status,
        "event_id": event.get("id"),
        "event_status": event.get("status"),
        "quality_gate_decision": gate.get("decision"),
        "context_eligible": gate.get("context_eligible"),
        "quality_warnings": quality.get("warnings", []) if isinstance(quality.get("warnings"), list) else [],
    }
    if error:
        record["error"] = error
    return record


def _report(
    paths: CompanionPaths,
    cycles: int,
    trigger: str,
    stages: list[dict],
    attempts: list[dict],
    stop_reasons: list[str],
) -> dict:
    events = _events_for_attempts(paths, attempts)
    latest_event = events[-1] if events else {}
    return {
        "ok": not stop_reasons and len(attempts) == cycles,
        "milestone": "M5.5",
        "recommendation": (
            "continue_quality_observation"
            if not stop_reasons and len(attempts) == cycles
            else "inspect"
        ),
        "companion_home": str(paths.home),
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cycles_requested": cycles,
        "profile": {
            "trigger": trigger,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": True,
            "provider_generation_requested": True,
            "raw_output_storage": "hash_only",
        },
        "stages": stages,
        "attempts": attempts,
        "latest_event": _latest_event_snapshot(latest_event),
        "quality_profile": _quality_profile(events),
        "context_acceptance": _context_acceptance(events),
        "request_discipline": _request_discipline(events),
        "memory_discipline": _memory_discipline(events),
        "grounding": _grounding_summary(events),
        "semantic_shadow": _semantic_shadow_summary(events),
        "output_audit": _output_audit_summary(events),
        "stop_reasons": stop_reasons,
        "next_commands": {
            "quality_trial": _shell_command([
                "python3",
                "scripts/run_m5_quality_trial.py",
                "--companion-home",
                str(paths.home),
                "--cycles",
                str(cycles),
            ]),
            "quality_check": _shell_command([
                "python3",
                "scripts/run_m5_quality_check.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
    }


def _events_for_attempts(paths: CompanionPaths, attempts: list[dict]) -> list[dict]:
    ids = [attempt.get("event_id") for attempt in attempts if attempt.get("event_id")]
    if not ids:
        return []
    by_id = {
        event.get("id"): event
        for event in load_wake_events(paths.wake_events_file, limit=max(50, len(ids)))
    }
    return [by_id[event_id] for event_id in ids if event_id in by_id]


def _quality_profile(events: list[dict]) -> dict:
    warnings = [
        str(warning)
        for event in events
        for warning in ((event.get("quality") or {}).get("warnings") or [])
    ]
    blocking = [
        str(warning)
        for event in events
        for warning in ((event.get("quality_gate") or {}).get("blocking_warnings") or [])
    ]
    advisory = [
        str(warning)
        for event in events
        for warning in ((event.get("quality_gate") or {}).get("advisory_warnings") or [])
    ]
    return {
        "quality_warning_count": len(warnings),
        "quality_warnings": warnings,
        "blocking_warning_count": len(blocking),
        "blocking_warnings": blocking,
        "advisory_warning_count": len(advisory),
        "advisory_warnings": advisory,
    }


def _context_acceptance(events: list[dict]) -> dict:
    return {
        "completed_events": sum(1 for event in events if event.get("status") == "completed"),
        "accepted_events": sum(1 for event in events if (event.get("quality_gate") or {}).get("context_eligible") is True),
        "rejected_events": sum(1 for event in events if (event.get("quality_gate") or {}).get("context_eligible") is False),
    }


def _request_discipline(events: list[dict]) -> dict:
    return {
        "request_count": sum(len(event.get("request_ids", [])) for event in events),
        "request_error_count": sum(len(event.get("request_errors", [])) for event in events),
    }


def _memory_discipline(events: list[dict]) -> dict:
    failures = [
        result
        for event in events
        for result in event.get("memory_write_results", [])
        if isinstance(result, dict) and result.get("status") == "failed"
    ]
    return {
        "memory_count": sum(len(event.get("memory_ids", [])) for event in events),
        "memory_write_failures": len(failures),
    }


def _grounding_summary(events: list[dict]) -> dict:
    return {
        "supported": sum(_count_int((event.get("grounding") or {}).get("supported")) for event in events),
        "unsupported": sum(_count_int((event.get("grounding") or {}).get("unsupported")) for event in events),
        "ignored": sum(_count_int((event.get("grounding") or {}).get("ignored")) for event in events),
    }


def _semantic_shadow_summary(events: list[dict]) -> dict:
    return {
        "attempted": sum(_count_int((event.get("semantic_shadow") or {}).get("attempted")) for event in events),
        "succeeded": sum(_count_int((event.get("semantic_shadow") or {}).get("succeeded")) for event in events),
        "failed": sum(_count_int((event.get("semantic_shadow") or {}).get("failed")) for event in events),
        "skipped": sum(_count_int((event.get("semantic_shadow") or {}).get("skipped")) for event in events),
    }


def _output_audit_summary(events: list[dict]) -> dict:
    audits = [
        event.get("output_audit")
        for event in events
        if isinstance(event.get("output_audit"), dict)
    ]
    return {
        "raw_output_storage": "hash_only"
        if audits and all(audit.get("raw_output_storage") == "hash_only" for audit in audits)
        else "missing" if not audits else "inspect",
        "raw_output_stored_count": sum(1 for audit in audits if _raw_output_stored(audit)),
        "audit_count": len(audits),
    }


def _latest_event_snapshot(event: dict) -> dict:
    return {
        "id": event.get("id"),
        "trigger": event.get("trigger"),
        "status": event.get("status"),
        "provider": event.get("provider"),
        "memory_backend": event.get("memory_backend"),
        "started_at": event.get("started_at"),
        "completed_at": event.get("completed_at"),
        "journal": event.get("journal"),
    }


def _latest_event(paths: CompanionPaths) -> dict:
    events = load_wake_events(paths.wake_events_file, limit=1)
    return events[0] if events else {}


def _raw_output_stored(output_audit: dict) -> bool:
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _stage(
    name: str,
    ok: bool,
    required: bool,
    message: str,
    *,
    details: dict | None = None,
) -> dict:
    stage = {
        "name": name,
        "status": "passed" if ok else "failed",
        "ok": ok,
        "required": required,
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _short_message(value: str, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
