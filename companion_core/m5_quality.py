"""M5 companion quality observation gate."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
from types import ModuleType
import uuid

from .events import load_wake_events
from .paths import CompanionPaths
from .quality import MAX_REQUESTS_PER_WAKE, MIN_JOURNAL_CHARS
from .release_gate import audit_semantic_shadow_authority

EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
MIN_CJK_JOURNAL_CHARS = 20


def run_m5_quality_check(
    paths: CompanionPaths,
    *,
    min_events: int = 1,
    min_accepted_events: int = 1,
    limit: int = 10,
    since: str | None = None,
    trigger_prefix: str | None = None,
    use_m4_wake_baseline: bool = True,
) -> dict:
    """Read local runtime evidence and decide whether M5 quality tuning can start.

    This gate is intentionally non-generative: it does not run a wake, create a
    provider client, call DeepSeek, install timers, or change memory authority.
    """

    reports = _load_source_reports(paths)
    all_events = load_wake_events(paths.wake_events_file)
    baseline_event_id = _m4_baseline_event_id(reports) if use_m4_wake_baseline else None
    events = _select_events(
        all_events,
        since=since,
        trigger_prefix=trigger_prefix,
        baseline_event_id=baseline_event_id if not since and not trigger_prefix else None,
        limit=limit,
    )
    events.sort(key=lambda event: _event_time(event) or datetime.min)
    completed = [event for event in events if event.get("status") == "completed"]
    accepted = [event for event in completed if _context_eligible(event)]
    rejected = [event for event in events if _context_rejected(event)]

    quality_profile = _quality_profile(events)
    pending_reasons = _pending_reasons(
        event_count=len(events),
        accepted_count=len(accepted),
        min_events=min_events,
        min_accepted_events=min_accepted_events,
    )

    stages = [
        _m4_baseline_stage(reports),
        _event_sample_stage(
            events,
            accepted,
            rejected,
            min_events=min_events,
            min_accepted_events=min_accepted_events,
            pending_reasons=pending_reasons,
            baseline_event_id=baseline_event_id,
            since=since,
            trigger_prefix=trigger_prefix,
            limit=limit,
        ),
        _event_health_stage(events),
        _quality_warning_stage(quality_profile, rejected),
        _relationship_continuity_stage(accepted),
        _emotion_status_stage(accepted),
        _language_surface_stage(paths, accepted),
        _request_discipline_stage(events),
        _memory_discipline_stage(events),
        _grounding_integrity_stage(events),
        _semantic_shadow_stage(paths),
        _output_storage_stage(events),
        _dashboard_read_only_stage(paths),
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
        else "ready_for_quality_tuning"
    )

    return {
        "ok": recommendation == "ready_for_quality_tuning",
        "milestone": "M5.1",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "name": "m5-quality-observation",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "min_events": min_events,
            "min_accepted_events": min_accepted_events,
            "limit": limit,
            "since": since,
            "trigger_prefix": trigger_prefix,
            "m4_wake_baseline_scope": use_m4_wake_baseline,
            "baseline_event_id": baseline_event_id,
        },
        "source_reports": {
            name: source["snapshot"]
            for name, source in reports.items()
        },
        "sample": {
            "events_considered": len(events),
            "completed_events": len(completed),
            "accepted_events": len(accepted),
            "rejected_events": len(rejected),
            "first_event": events[0].get("id") if events else None,
            "latest_event": events[-1].get("id") if events else None,
            "since": since,
            "trigger_prefix": trigger_prefix,
            "baseline_event_id": baseline_event_id,
        },
        "quality_profile": quality_profile,
        "stages": stages,
        "pending_reasons": pending_reasons,
        "stop_reasons": stop_reasons,
        "next_commands": {
            "quality_check": _shell_command([
                "python3",
                "scripts/run_m5_quality_check.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m4_post_change_guard": _shell_command([
                "python3",
                "scripts/run_m4_post_change_guard.py",
                "--companion-home",
                str(paths.home),
            ]),
            "targeted_tests": "python3 -m pytest tests/test_internal_life_loop.py -q",
        },
    }


def _load_source_reports(paths: CompanionPaths) -> dict[str, dict]:
    return {
        "m4_post_change_guard": _load_report(
            paths,
            paths.life_loop_dir / "m4_post_change_guard_report.json",
            expected_milestone="M4.7",
        ),
        "m4_runtime_validation": _load_report(
            paths,
            paths.life_loop_dir / "m4_runtime_validation_report.json",
            expected_milestone="M4.6",
        ),
        "m4_observation": _load_report(
            paths,
            paths.life_loop_dir / "m4_observation_report.json",
            expected_milestone="M4.8",
            missing_ok=True,
        ),
        "m4_wake_trial": _load_report(
            paths,
            paths.life_loop_dir / "m4_wake_trial_report.json",
            expected_milestone="M4.3",
            missing_ok=True,
        ),
    }


def _load_report(
    paths: CompanionPaths,
    path: Path,
    *,
    expected_milestone: str,
    missing_ok: bool = False,
) -> dict:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        message = f"report is missing: {path}"
        return {
            "payload": None,
            "snapshot": {
                "path": _relative(paths, path),
                "loaded": False,
                "missing_ok": missing_ok,
                "message": message,
            },
        }
    except json.JSONDecodeError as exc:
        return {
            "payload": None,
            "snapshot": {
                "path": _relative(paths, path),
                "loaded": False,
                "message": f"invalid JSON: {exc.msg}",
            },
        }
    except OSError as exc:
        return {
            "payload": None,
            "snapshot": {
                "path": _relative(paths, path),
                "loaded": False,
                "message": str(exc),
            },
        }
    loaded = isinstance(payload, dict)
    milestone_ok = loaded and payload.get("milestone") == expected_milestone
    message = "report loaded" if loaded and milestone_ok else "report milestone mismatch"
    if not loaded:
        message = "report must be a JSON object"
    return {
        "payload": payload if loaded else None,
        "snapshot": {
            "path": _relative(paths, path),
            "loaded": loaded,
            "ok": payload.get("ok") if loaded else None,
            "milestone": payload.get("milestone") if loaded else None,
            "recommendation": payload.get("recommendation") if loaded else None,
            "saved_at": payload.get("saved_at") if loaded else None,
            "expected_milestone": expected_milestone,
            "message": message,
        },
    }


def _m4_baseline_stage(reports: dict[str, dict]) -> dict:
    guard = reports["m4_post_change_guard"]["payload"]
    observation = reports["m4_observation"]["payload"]
    problems = []
    if not isinstance(guard, dict):
        problems.append("m4_post_change_guard_report is missing or invalid")
    else:
        if guard.get("ok") is not True:
            problems.append("M4 post-change guard ok is not true")
        if guard.get("recommendation") != "m4_still_deployable":
            problems.append("M4 post-change guard recommendation is not m4_still_deployable")
        if guard.get("stop_reasons"):
            problems.append("M4 post-change guard has stop_reasons")
    if isinstance(observation, dict) and observation.get("recommendation") == "inspect":
        problems.append("M4 observation report recommends inspect")

    return _stage(
        "m4_baseline",
        not problems,
        required=True,
        message="M4 baseline supports M5 quality work" if not problems else "; ".join(problems),
        details={
            "post_change_guard": reports["m4_post_change_guard"]["snapshot"],
            "runtime_validation": reports["m4_runtime_validation"]["snapshot"],
            "observation": reports["m4_observation"]["snapshot"],
        },
    )


def _event_sample_stage(
    events: list[dict],
    accepted: list[dict],
    rejected: list[dict],
    *,
    min_events: int,
    min_accepted_events: int,
    pending_reasons: list[str],
    baseline_event_id: str | None,
    since: str | None,
    trigger_prefix: str | None,
    limit: int,
) -> dict:
    ok = not pending_reasons
    return _stage(
        "event_sample",
        ok,
        required=False,
        status="passed" if ok else "pending",
        message="quality sample is sufficient" if ok else "; ".join(pending_reasons),
        details={
            "event_count": len(events),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "min_events": min_events,
            "min_accepted_events": min_accepted_events,
            "baseline_event_id": baseline_event_id,
            "since": since,
            "trigger_prefix": trigger_prefix,
            "limit": limit,
        },
    )


def _event_health_stage(events: list[dict]) -> dict:
    failures = [
        {
            "id": event.get("id"),
            "trigger": event.get("trigger"),
            "status": event.get("status"),
            "error_type": (event.get("error") or {}).get("type") if isinstance(event.get("error"), dict) else None,
        }
        for event in events
        if event.get("status") != "completed"
    ]
    return _stage(
        "event_health",
        not failures,
        required=True,
        message="selected wake events completed" if not failures else f"failed wake events ({len(failures)})",
        details={"failures": failures},
    )


def _quality_warning_stage(quality_profile: dict, rejected: list[dict]) -> dict:
    blocking_count = quality_profile["blocking_warning_count"]
    unexpected_rejections = [
        event.get("id")
        for event in rejected
        if not _blocking_warnings(event)
    ]
    problems = []
    if blocking_count:
        problems.append(f"blocking quality warnings ({blocking_count})")
    if unexpected_rejections:
        problems.append(f"context rejected without blocking warnings ({len(unexpected_rejections)})")
    status = "warning" if quality_profile["advisory_warning_count"] and not problems else None
    return _stage(
        "quality_warning_profile",
        not problems,
        required=True,
        status=status,
        message="quality warnings are non-blocking" if not problems else "; ".join(problems),
        details=quality_profile | {"unexpected_rejections": unexpected_rejections},
    )


def _relationship_continuity_stage(accepted: list[dict]) -> dict:
    capsule_updates = [
        event.get("id")
        for event in accepted
        if isinstance(event.get("accepted_context"), dict)
        and event["accepted_context"].get("context_capsule_updated") is True
    ]
    short_journals = [
        event.get("id")
        for event in accepted
        if _count_int((event.get("quality") or {}).get("journal_chars")) < MIN_JOURNAL_CHARS
    ]
    ok = bool(accepted) and not short_journals
    status = "passed" if ok else "pending" if not accepted else "failed"
    return _stage(
        "relationship_continuity",
        ok or not accepted,
        required=True,
        status=status,
        message=(
            "accepted events have usable journal continuity"
            if ok
            else "no accepted events to assess"
            if not accepted
            else f"accepted events have short journals ({len(short_journals)})"
        ),
        details={
            "accepted_count": len(accepted),
            "context_capsule_update_count": len(capsule_updates),
            "short_journal_event_ids": short_journals,
        },
    )


def _emotion_status_stage(accepted: list[dict]) -> dict:
    missing_state = [
        event.get("id")
        for event in accepted
        if event.get("companion_state_updated") is not True
        and (event.get("quality") or {}).get("companion_state_updated") is not True
    ]
    ok = not missing_state
    return _stage(
        "emotion_status_continuity",
        ok,
        required=True,
        status="passed" if ok else "failed",
        message="accepted events update companion mood/status" if ok else f"accepted events missing state updates ({len(missing_state)})",
        details={
            "accepted_count": len(accepted),
            "missing_state_event_ids": missing_state,
        },
    )


def _language_surface_stage(paths: CompanionPaths, accepted: list[dict]) -> dict:
    findings = [_journal_language(paths, event) for event in accepted]
    problems = [
        finding
        for finding in findings
        if finding.get("problem")
    ]
    ok = not problems
    return _stage(
        "language_surface",
        ok,
        required=True,
        status="passed" if ok else "failed",
        message="accepted journals contain Chinese-visible prose" if ok else f"language surface problems ({len(problems)})",
        details={
            "checked_count": len(findings),
            "findings": findings,
        },
    )


def _request_discipline_stage(events: list[dict]) -> dict:
    request_errors = [
        {"id": event.get("id"), "errors": event.get("request_errors", [])}
        for event in events
        if event.get("request_errors")
    ]
    noisy_requests = [
        {"id": event.get("id"), "request_count": len(event.get("request_ids", []))}
        for event in events
        if len(event.get("request_ids", [])) > MAX_REQUESTS_PER_WAKE
    ]
    problems = []
    if request_errors:
        problems.append(f"request errors ({len(request_errors)})")
    if noisy_requests:
        problems.append(f"request count exceeds {MAX_REQUESTS_PER_WAKE} ({len(noisy_requests)})")
    return _stage(
        "request_discipline",
        not problems,
        required=True,
        message="requests remain disciplined" if not problems else "; ".join(problems),
        details={
            "request_count": sum(len(event.get("request_ids", [])) for event in events),
            "request_errors": request_errors,
            "noisy_requests": noisy_requests,
        },
    )


def _memory_discipline_stage(events: list[dict]) -> dict:
    failures = [
        {
            "id": event.get("id"),
            "results": [
                result
                for result in event.get("memory_write_results", [])
                if isinstance(result, dict) and result.get("status") == "failed"
            ],
        }
        for event in events
        if any(
            isinstance(result, dict) and result.get("status") == "failed"
            for result in event.get("memory_write_results", [])
        )
    ]
    return _stage(
        "memory_discipline",
        not failures,
        required=True,
        message="memory writes have no backend failures" if not failures else f"memory write failures ({len(failures)})",
        details={
            "memory_count": sum(len(event.get("memory_ids", [])) for event in events),
            "memory_write_failures": failures,
        },
    )


def _grounding_integrity_stage(events: list[dict]) -> dict:
    unsupported = [
        {
            "id": event.get("id"),
            "unsupported": _count_int((event.get("grounding") or {}).get("unsupported")),
        }
        for event in events
        if _count_int((event.get("grounding") or {}).get("unsupported"))
    ]
    return _stage(
        "grounding_integrity",
        not unsupported,
        required=True,
        message="grounding has no unsupported final claims" if not unsupported else f"unsupported grounding claims ({len(unsupported)})",
        details={
            "unsupported": unsupported,
            "supported": sum(_count_int((event.get("grounding") or {}).get("supported")) for event in events),
            "ignored": sum(_count_int((event.get("grounding") or {}).get("ignored")) for event in events),
        },
    )


def _semantic_shadow_stage(paths: CompanionPaths) -> dict:
    audit = audit_semantic_shadow_authority(paths)
    return _stage(
        "semantic_shadow_isolation",
        audit.get("ok") is True,
        required=True,
        message=audit.get("message", "semantic shadow authority audit completed"),
        details=audit,
    )


def _output_storage_stage(events: list[dict]) -> dict:
    problems = []
    missing = []
    for event in events:
        output_audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
        if not output_audit:
            missing.append(event.get("id"))
            continue
        if output_audit.get("raw_output_storage") not in (None, "hash_only"):
            problems.append({"id": event.get("id"), "problem": "raw_output_storage is not hash_only"})
        if _raw_output_stored(output_audit):
            problems.append({"id": event.get("id"), "problem": "raw model output was stored"})
    status = "warning" if missing and not problems else None
    return _stage(
        "output_storage_policy",
        not problems,
        required=True,
        status=status,
        message="selected output audit is hash-only" if not problems else f"raw output storage problems ({len(problems)})",
        details={
            "problems": problems,
            "events_without_output_audit": missing,
        },
    )


def _dashboard_read_only_stage(paths: CompanionPaths) -> dict:
    module_path = paths.window_dir / "window.py"
    module, error = _load_window_module(paths, module_path)
    if error:
        return _stage(
            "dashboard_read_only",
            False,
            required=True,
            message=error,
            details={"path": _relative(paths, module_path)},
        )
    app = getattr(module, "app", None)
    if app is None or not hasattr(app, "url_map"):
        return _stage(
            "dashboard_read_only",
            False,
            required=True,
            message="window app has no Flask url_map",
            details={"path": _relative(paths, module_path)},
        )

    routes = []
    relevant_non_get = []
    has_life_get = False
    for rule in app.url_map.iter_rules():
        methods = sorted(method for method in rule.methods if method not in {"HEAD", "OPTIONS"})
        route = {
            "rule": rule.rule,
            "endpoint": rule.endpoint,
            "methods": methods,
        }
        routes.append(route)
        if rule.rule == "/life" and methods == ["GET"]:
            has_life_get = True
        if _is_m5_life_route(rule.rule, rule.endpoint) and methods != ["GET"]:
            relevant_non_get.append(route)

    problems = []
    if not has_life_get:
        problems.append("/life GET route is missing")
    if relevant_non_get:
        problems.append("M5 life dashboard routes expose non-GET methods")
    return _stage(
        "dashboard_read_only",
        not problems,
        required=True,
        message="/life M5 dashboard surface is read-only" if not problems else "; ".join(problems),
        details={
            "path": _relative(paths, module_path),
            "life_get_route": has_life_get,
            "m5_life_non_get_routes": relevant_non_get,
            "non_get_routes": [route for route in routes if route["methods"] != ["GET"]],
        },
    )


def _quality_profile(events: list[dict]) -> dict:
    warnings = [
        str(warning)
        for event in events
        for warning in (event.get("quality") or {}).get("warnings", [])
    ]
    blocking = [
        warning
        for event in events
        for warning in _blocking_warnings(event)
    ]
    advisory = [
        warning
        for event in events
        for warning in _advisory_warnings(event)
    ]
    return {
        "quality_warning_count": len(warnings),
        "quality_warnings": warnings,
        "blocking_warning_count": len(blocking),
        "blocking_warnings": blocking,
        "advisory_warning_count": len(advisory),
        "advisory_warnings": advisory,
        "warning_categories": dict(Counter(_warning_category(warning) for warning in warnings)),
    }


def _blocking_warnings(event: dict) -> list[str]:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    values = gate.get("blocking_warnings")
    if isinstance(values, list):
        return [str(value) for value in values]
    if gate.get("context_eligible") is False:
        return [
            str(value)
            for value in (event.get("quality") or {}).get("warnings", [])
        ]
    return []


def _advisory_warnings(event: dict) -> list[str]:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    values = gate.get("advisory_warnings")
    if isinstance(values, list):
        return [str(value) for value in values]
    return []


def _warning_category(warning: str) -> str:
    if warning.startswith("journal is short"):
        return "journal_short"
    if "wake-count framing" in warning:
        return "wake_count_framing"
    if "trial/process framing" in warning:
        return "process_framing"
    if "repeats recent self-narrative" in warning:
        return "repeated_self_narrative"
    if "companion state" in warning:
        return "companion_state"
    if "request" in warning:
        return "request"
    if "memory" in warning:
        return "memory"
    if "ground" in warning:
        return "grounding"
    return "other"


def _pending_reasons(
    *,
    event_count: int,
    accepted_count: int,
    min_events: int,
    min_accepted_events: int,
) -> list[str]:
    reasons = []
    if event_count < min_events:
        reasons.append(f"wake events {event_count} < required {min_events}")
    if accepted_count < min_accepted_events:
        reasons.append(f"accepted wake events {accepted_count} < required {min_accepted_events}")
    return reasons


def _select_events(
    events: list[dict],
    *,
    since: str | None,
    trigger_prefix: str | None,
    baseline_event_id: str | None,
    limit: int,
) -> list[dict]:
    selected = list(events)
    since_dt = _parse_datetime(since) if since else None
    if since_dt:
        selected = [
            event
            for event in selected
            if (_event_time(event) or datetime.min) >= since_dt
        ]
    if trigger_prefix:
        selected = [
            event
            for event in selected
            if str(event.get("trigger", "")).startswith(trigger_prefix)
        ]
    if baseline_event_id:
        for index, event in enumerate(selected):
            if event.get("id") == baseline_event_id:
                selected = selected[index:]
                break
    if limit:
        selected = selected[-limit:]
    return selected


def _m4_baseline_event_id(reports: dict[str, dict]) -> str | None:
    wake_report = reports["m4_wake_trial"]["payload"]
    if not isinstance(wake_report, dict) or wake_report.get("ok") is not True:
        return None
    latest_event = wake_report.get("latest_event") if isinstance(wake_report.get("latest_event"), dict) else {}
    event_id = latest_event.get("id")
    return str(event_id) if event_id else None


def _context_eligible(event: dict) -> bool:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    return gate.get("context_eligible") is True


def _context_rejected(event: dict) -> bool:
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    return gate.get("context_eligible") is False


def _journal_language(paths: CompanionPaths, event: dict) -> dict:
    journal = event.get("journal")
    finding = {
        "event_id": event.get("id"),
        "journal": journal,
        "cjk_chars": 0,
        "problem": None,
    }
    if not journal:
        finding["problem"] = "accepted event has no journal path"
        return finding
    journal_path = paths.home / str(journal)
    try:
        body = journal_path.read_text()
    except FileNotFoundError:
        finding["problem"] = "journal file is missing"
        return finding
    except OSError as exc:
        finding["problem"] = f"journal file could not be read: {exc}"
        return finding
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", body))
    finding["cjk_chars"] = cjk_chars
    if cjk_chars < MIN_CJK_JOURNAL_CHARS:
        finding["problem"] = f"journal has {cjk_chars} CJK chars, expected at least {MIN_CJK_JOURNAL_CHARS}"
    return finding


def _raw_output_stored(output_audit: dict) -> bool:
    if output_audit.get("initial_raw_output_stored") is True or output_audit.get("final_raw_output_stored") is True:
        return True
    snapshots = [
        output_audit.get("initial"),
        output_audit.get("final"),
        *(output_audit.get("repair_attempts") if isinstance(output_audit.get("repair_attempts"), list) else []),
    ]
    return any(isinstance(snapshot, dict) and snapshot.get("raw_output_stored") is True for snapshot in snapshots)


def _load_window_module(paths: CompanionPaths, module_path: Path) -> tuple[ModuleType | None, str | None]:
    if not module_path.exists():
        return None, f"window module is missing: {module_path}"
    module_name = f"_m5_window_quality_{uuid.uuid4().hex}"
    old_home = os.environ.get("COMPANION_HOME")
    old_scripts = os.environ.get("COMPANION_SCRIPTS_DIR")
    try:
        os.environ["COMPANION_HOME"] = str(paths.home)
        os.environ.setdefault("COMPANION_SCRIPTS_DIR", str(paths.home / "scripts"))
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return None, f"window module could not be loaded: {module_path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as exc:
        return None, f"window module import failed: {type(exc).__name__}: {_short_message(str(exc))}"
    finally:
        if old_home is None:
            os.environ.pop("COMPANION_HOME", None)
        else:
            os.environ["COMPANION_HOME"] = old_home
        if old_scripts is None:
            os.environ.pop("COMPANION_SCRIPTS_DIR", None)
        else:
            os.environ["COMPANION_SCRIPTS_DIR"] = old_scripts


def _is_m5_life_route(rule: str, endpoint: str) -> bool:
    text = f"{rule} {endpoint}".lower()
    return rule == "/life" or rule.startswith("/life/m5") or "m5" in text and "life" in text


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


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _short_message(value: str, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
