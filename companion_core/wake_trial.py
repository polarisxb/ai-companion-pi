"""M4 manual wake-trial wrapper with bounded infrastructure retry."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable

from .events import load_wake_events
from .lifecycle import LifeLoopRunner
from .llm import (
    HttpLLMError,
    LLMClient,
    LLMProviderConfigError,
    create_llm_client,
)
from .memory import JsonMemoryStore
from .output_archive import should_store_raw_outputs
from .paths import CompanionPaths
from .secrets import load_local_secrets

ClientFactory = Callable[[int], LLMClient]

DEFAULT_TRIGGER = "m4-pi-manual-wake"
EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
RETRYABLE_INFRASTRUCTURE_MARKERS = (
    "timed out",
    "timeout",
    "request failed",
    "connection",
    "temporarily",
    "reset",
    "unreachable",
    "HTTP 500",
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
)


def run_m4_wake_trial(
    paths: CompanionPaths,
    *,
    trigger: str = DEFAULT_TRIGGER,
    timeout_seconds: int = 300,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
    deploy_report_path: str | Path | None = None,
    require_deploy_ready: bool = True,
    client_factory: ClientFactory | None = None,
    max_attempts: int = 2,
) -> dict:
    """Run one manual M4 DeepSeek JSON wake trial.

    The default path creates a real DeepSeek client. Tests can pass
    ``client_factory`` to exercise the wrapper without a provider call.
    """

    stages: list[dict] = []
    deploy_report, deploy_stage = _deploy_prerequisite_stage(
        paths,
        deploy_report_path=deploy_report_path,
        required=require_deploy_ready,
    )
    stages.append(deploy_stage)
    profile_stage = _profile_stage()
    stages.append(profile_stage)
    raw_stage = _raw_output_storage_stage()
    stages.append(raw_stage)

    prereq_stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    if prereq_stop_reasons:
        return _report(
            paths,
            stages=stages,
            attempts=[],
            stop_reasons=prereq_stop_reasons,
            deploy_report=deploy_report,
            trigger=trigger,
            max_attempts=max_attempts,
            latest_event={},
            failure_audit=_failure_audit("provider", False, "; ".join(prereq_stop_reasons)),
        )

    load_local_secrets(paths)
    attempts = []
    latest_event: dict = {}
    failure_audit = _failure_audit("unknown", False, "no attempt completed")
    stop_reasons: list[str] = []

    for attempt_number in range(1, max_attempts + 1):
        attempt_trigger = f"{trigger}:attempt-{attempt_number}"
        try:
            client = (
                client_factory(attempt_number)
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
            ).run_once(trigger=attempt_trigger, provider=EXPECTED_PROVIDER)
            latest_event = result.event or {}
            event_stop_reasons = _event_stop_reasons(latest_event)
            attempts.append(_attempt_record(
                attempt=attempt_number,
                trigger=attempt_trigger,
                status="completed",
                event=latest_event,
                retryable=False,
                failure_category="none" if not event_stop_reasons else _event_failure_category(latest_event),
            ))
            if event_stop_reasons:
                failure_audit = _failure_audit(
                    _event_failure_category(latest_event),
                    False,
                    "; ".join(event_stop_reasons),
                )
                stop_reasons.extend(f"attempt {attempt_number}: {reason}" for reason in event_stop_reasons)
            else:
                failure_audit = _failure_audit("none", False, "")
            break
        except Exception as exc:
            latest_event = _latest_event(paths)
            audit = classify_wake_trial_failure(exc)
            retryable = audit["retryable"] and attempt_number < max_attempts
            attempts.append(_attempt_record(
                attempt=attempt_number,
                trigger=attempt_trigger,
                status="failed",
                event=latest_event,
                retryable=retryable,
                failure_category=audit["category"],
                error={
                    "type": type(exc).__name__,
                    "message": _short_message(str(exc)),
                },
            ))
            failure_audit = audit
            if retryable:
                continue
            stop_reasons.append(f"attempt {attempt_number}: {audit['reason']}")
            break

    if attempts and attempts[-1]["status"] == "failed" and not stop_reasons:
        stop_reasons.append(f"attempt {attempts[-1]['attempt']}: wake failed")

    return _report(
        paths,
        stages=stages,
        attempts=attempts,
        stop_reasons=stop_reasons,
        deploy_report=deploy_report,
        trigger=trigger,
        max_attempts=max_attempts,
        latest_event=latest_event,
        failure_audit=failure_audit,
    )


def classify_wake_trial_failure(exc: Exception) -> dict:
    error_type = type(exc).__name__
    message = str(exc)
    if isinstance(exc, LLMProviderConfigError):
        return _failure_audit("provider", False, _short_message(message) or error_type)
    if isinstance(exc, HttpLLMError):
        retryable = _has_retryable_marker(message)
        return _failure_audit("infrastructure" if retryable else "provider", retryable, _short_message(message))
    if error_type in {"TimeoutError", "ConnectionError", "URLError", "ClaudeCliTimeoutError"}:
        return _failure_audit("infrastructure", True, _short_message(message) or error_type)
    if isinstance(exc, ValueError):
        return _failure_audit("parser", False, _short_message(message) or error_type)
    if isinstance(exc, OSError):
        return _failure_audit("infrastructure", True, _short_message(message) or error_type)
    return _failure_audit("unknown", False, _short_message(message) or error_type)


def _deploy_prerequisite_stage(
    paths: CompanionPaths,
    *,
    deploy_report_path: str | Path | None,
    required: bool,
) -> tuple[dict | None, dict]:
    if not required:
        return None, _stage(
            "m4_deploy_report",
            True,
            required=False,
            status="skipped",
            message="M4 deploy report prerequisite was not required",
        )
    path = (
        Path(deploy_report_path).expanduser().resolve()
        if deploy_report_path
        else paths.life_loop_dir / "m4_deploy_report.json"
    )
    try:
        report = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage(
            "m4_deploy_report",
            False,
            required=True,
            message=f"M4 deploy report is missing: {path}",
        )
    except json.JSONDecodeError as exc:
        return None, _stage(
            "m4_deploy_report",
            False,
            required=True,
            message=f"M4 deploy report is invalid JSON: {exc.msg}",
        )
    except OSError as exc:
        return None, _stage(
            "m4_deploy_report",
            False,
            required=True,
            message=f"M4 deploy report could not be read: {exc}",
        )
    problems = []
    if report.get("ok") is not True:
        problems.append("M4 deploy report ok is not true")
    if report.get("milestone") != "M4.2":
        problems.append("M4 deploy report milestone is not M4.2")
    if report.get("recommendation") != "ready_for_manual_wake":
        problems.append("M4 deploy report recommendation is not ready_for_manual_wake")
    return report, _stage(
        "m4_deploy_report",
        not problems,
        required=True,
        message="M4 deploy report is ready" if not problems else "; ".join(problems),
        details={
            "path": _relative(paths, path),
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "saved_at": report.get("saved_at"),
        },
    )


def _profile_stage() -> dict:
    return _stage(
        "wake_trial_profile",
        True,
        required=True,
        message="M4 wake trial profile is deepseek + json",
        details={
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "trigger": DEFAULT_TRIGGER,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
        },
    )


def _raw_output_storage_stage() -> dict:
    raw_enabled = should_store_raw_outputs()
    return _stage(
        "raw_output_storage",
        not raw_enabled,
        required=True,
        message=(
            "raw model output storage is hash-only"
            if not raw_enabled
            else "raw model output storage is enabled; unset COMPANION_STORE_RAW_OUTPUTS before M4 wake trial"
        ),
        details={"raw_output_storage": "enabled" if raw_enabled else "hash_only"},
    )


def _event_stop_reasons(event: dict) -> list[str]:
    reasons = []
    if event.get("status") != "completed":
        reasons.append("latest wake event did not complete")
    quality_gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if quality_gate.get("context_eligible") is False:
        reasons.append("quality gate rejected future-context writes")
    blocking = quality_gate.get("blocking_warnings")
    if isinstance(blocking, list) and blocking:
        reasons.append(f"blocking quality warnings ({len(blocking)})")
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        reasons.append(f"unsupported grounding claims ({_count_int(grounding.get('unsupported'))})")
    if event.get("request_errors"):
        reasons.append(f"request errors ({len(event.get('request_errors', []))})")
    memory_failures = [
        result
        for result in event.get("memory_write_results", [])
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


def _event_failure_category(event: dict) -> str:
    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        return "grounding"
    quality_gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if quality_gate.get("context_eligible") is False:
        return "authority"
    if event.get("request_errors"):
        return "request"
    if any(
        isinstance(result, dict) and result.get("status") == "failed"
        for result in event.get("memory_write_results", [])
    ):
        return "memory"
    output_audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
    if output_audit.get("raw_output_storage") != "hash_only" or _raw_output_stored(output_audit):
        return "authority"
    return "unknown"


def _raw_output_stored(output_audit: dict) -> bool:
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _attempt_record(
    *,
    attempt: int,
    trigger: str,
    status: str,
    event: dict,
    retryable: bool,
    failure_category: str,
    error: dict | None = None,
) -> dict:
    record = {
        "attempt": attempt,
        "trigger": trigger,
        "status": status,
        "event_id": event.get("id"),
        "event_status": event.get("status"),
        "failure_category": failure_category,
        "retryable": retryable,
    }
    if error:
        record["error"] = error
    return record


def _report(
    paths: CompanionPaths,
    *,
    stages: list[dict],
    attempts: list[dict],
    stop_reasons: list[str],
    deploy_report: dict | None,
    trigger: str,
    max_attempts: int,
    latest_event: dict,
    failure_audit: dict,
) -> dict:
    return {
        "ok": not stop_reasons and bool(attempts),
        "milestone": "M4.3",
        "recommendation": "continue_runtime_validation" if not stop_reasons and attempts else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "trigger": trigger,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "raw_output_storage": "hash_only",
        },
        "deploy_report": {
            "ok": deploy_report.get("ok"),
            "recommendation": deploy_report.get("recommendation"),
            "saved_at": deploy_report.get("saved_at"),
        } if isinstance(deploy_report, dict) else None,
        "retry_policy": {
            "max_attempts": max_attempts,
            "retryable_categories": ["infrastructure"],
            "non_retryable_categories": ["provider", "parser", "grounding", "authority", "memory", "request", "unknown"],
        },
        "attempts": attempts,
        "latest_event": _latest_event_snapshot(latest_event),
        "quality_gate": latest_event.get("quality_gate") if isinstance(latest_event.get("quality_gate"), dict) else {},
        "grounding": latest_event.get("grounding") if isinstance(latest_event.get("grounding"), dict) else {},
        "semantic_shadow": latest_event.get("semantic_shadow") if isinstance(latest_event.get("semantic_shadow"), dict) else {},
        "output_audit": _output_audit_snapshot(latest_event.get("output_audit")),
        "failure_audit": failure_audit,
        "stages": stages,
        "stop_reasons": stop_reasons,
        "next_commands": {
            "deploy_check": _shell_command([
                "python3",
                "scripts/run_m4_deploy_check.py",
                "--companion-home",
                str(paths.home),
            ]),
            "wake_trial": _shell_command([
                "python3",
                "scripts/run_m4_wake_trial.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
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


def _output_audit_snapshot(output_audit) -> dict:
    if not isinstance(output_audit, dict):
        return {}
    initial = output_audit.get("initial") if isinstance(output_audit.get("initial"), dict) else {}
    final = output_audit.get("final") if isinstance(output_audit.get("final"), dict) else {}
    return {
        "raw_output_storage": output_audit.get("raw_output_storage"),
        "initial_hash": initial.get("content_hash"),
        "final_hash": final.get("content_hash"),
        "initial_raw_output_stored": initial.get("raw_output_stored") is True,
        "final_raw_output_stored": final.get("raw_output_stored") is True,
        "repair_attempt_count": len(output_audit.get("repair_attempts", []))
        if isinstance(output_audit.get("repair_attempts"), list)
        else 0,
    }


def _latest_event(paths: CompanionPaths) -> dict:
    events = load_wake_events(paths.wake_events_file, limit=1)
    return events[0] if events else {}


def _failure_audit(category: str, retryable: bool, reason: str) -> dict:
    return {
        "category": category,
        "retryable": retryable,
        "reason": reason,
    }


def _has_retryable_marker(message: str) -> bool:
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in RETRYABLE_INFRASTRUCTURE_MARKERS)


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _short_message(value: str, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
