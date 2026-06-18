"""M4 runtime validation seal for completed deploy and wake reports."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shlex
import sys
import uuid
from pathlib import Path
from types import ModuleType

from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority

EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"


def run_m4_runtime_validation(
    paths: CompanionPaths,
    *,
    deploy_report_path: str | Path | None = None,
    wake_trial_report_path: str | Path | None = None,
    require_raspberry_pi: bool = False,
) -> dict:
    """Validate the M4 runtime surface without running another wake."""

    deploy_path = (
        Path(deploy_report_path).expanduser().resolve()
        if deploy_report_path
        else paths.life_loop_dir / "m4_deploy_report.json"
    )
    wake_path = (
        Path(wake_trial_report_path).expanduser().resolve()
        if wake_trial_report_path
        else paths.life_loop_dir / "m4_wake_trial_report.json"
    )

    deploy_report, deploy_load_stage = _load_report_stage(
        paths,
        deploy_path,
        name="m4_deploy_report",
        expected_milestone="M4.2",
    )
    wake_report, wake_load_stage = _load_report_stage(
        paths,
        wake_path,
        name="m4_wake_trial_report",
        expected_milestone="M4.3",
    )

    stages = [deploy_load_stage]
    if isinstance(deploy_report, dict):
        stages.append(_deploy_result_stage(deploy_report))
        stages.append(_deploy_profile_stage(deploy_report))
    else:
        stages.extend(_skipped_stages("deploy report did not load", ("m4_deploy_result", "m4_deploy_profile")))

    stages.append(wake_load_stage)
    if isinstance(wake_report, dict):
        stages.append(_wake_result_stage(wake_report))
        stages.append(_wake_profile_stage(wake_report))
        stages.append(_wake_attempt_stage(wake_report))
        stages.append(_output_audit_stage(wake_report))
        stages.append(_latest_event_stage(paths, wake_report))
    else:
        stages.extend(_skipped_stages(
            "wake-trial report did not load",
            (
                "m4_wake_result",
                "m4_wake_profile",
                "m4_wake_attempt",
                "m4_output_audit",
                "m4_latest_event",
            ),
        ))

    stages.append(_semantic_shadow_authority_stage(paths))
    stages.append(_dashboard_read_only_stage(paths))
    stages.append(_platform_identity_stage(require_raspberry_pi=require_raspberry_pi))

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]

    return {
        "ok": not stop_reasons,
        "milestone": "M4.6",
        "recommendation": "m4_runtime_validated" if not stop_reasons else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "m4-runtime-validation",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "require_raspberry_pi": require_raspberry_pi,
        },
        "source_reports": {
            "deploy": _report_snapshot(deploy_report, paths, deploy_path),
            "wake_trial": _report_snapshot(wake_report, paths, wake_path),
        },
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
                "--timeout",
                "300",
            ]),
            "runtime_validation": _shell_command([
                "python3",
                "scripts/run_m4_runtime_validation.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
    }


def _load_report_stage(
    paths: CompanionPaths,
    path: Path,
    *,
    name: str,
    expected_milestone: str,
) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage(name, False, required=True, message=f"{name} is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage(name, False, required=True, message=f"{name} is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage(name, False, required=True, message=f"{name} could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage(name, False, required=True, message=f"{name} must be a JSON object")
    problems = []
    if payload.get("milestone") != expected_milestone:
        problems.append(f"milestone is not {expected_milestone}")
    return payload, _stage(
        name,
        not problems,
        required=True,
        message=f"{name} loaded" if not problems else "; ".join(problems),
        details={
            "path": _relative(paths, path),
            "ok": payload.get("ok"),
            "milestone": payload.get("milestone"),
            "recommendation": payload.get("recommendation"),
            "saved_at": payload.get("saved_at"),
        },
    )


def _deploy_result_stage(report: dict) -> dict:
    problems = []
    if report.get("ok") is not True:
        problems.append("deploy report ok is not true")
    if report.get("recommendation") != "ready_for_manual_wake":
        problems.append("deploy report recommendation is not ready_for_manual_wake")
    if report.get("stop_reasons"):
        problems.append("deploy report has stop_reasons")
    return _stage(
        "m4_deploy_result",
        not problems,
        required=True,
        message="M4 deploy report is ready" if not problems else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _deploy_profile_stage(report: dict) -> dict:
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    expected = {
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cron_replacement": False,
        "semantic_shadow_authoritative": False,
        "real_wake_requested": False,
        "provider_generation_requested": False,
        "raw_output_storage_required": "hash_only",
    }
    problems = _profile_problems(profile, expected)
    return _stage(
        "m4_deploy_profile",
        not problems,
        required=True,
        message="M4 deploy profile preserves the frozen runtime contract" if not problems else "; ".join(problems),
        details={"expected": expected, "actual": profile},
    )


def _wake_result_stage(report: dict) -> dict:
    problems = []
    if report.get("ok") is not True:
        problems.append("wake-trial report ok is not true")
    if report.get("recommendation") != "continue_runtime_validation":
        problems.append("wake-trial recommendation is not continue_runtime_validation")
    if report.get("stop_reasons"):
        problems.append("wake-trial report has stop_reasons")
    failure_audit = report.get("failure_audit") if isinstance(report.get("failure_audit"), dict) else {}
    if failure_audit.get("category") not in (None, "none"):
        problems.append(f"failure_audit category is {failure_audit.get('category')!r}")
    return _stage(
        "m4_wake_result",
        not problems,
        required=True,
        message="M4 wake trial completed successfully" if not problems else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "recommendation": report.get("recommendation"),
            "failure_audit": failure_audit,
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _wake_profile_stage(report: dict) -> dict:
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    expected = {
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cron_replacement": False,
        "semantic_shadow_authoritative": False,
        "raw_output_storage": "hash_only",
    }
    problems = _profile_problems(profile, expected)
    return _stage(
        "m4_wake_profile",
        not problems,
        required=True,
        message="M4 wake profile preserves the frozen runtime contract" if not problems else "; ".join(problems),
        details={"expected": expected, "actual": profile},
    )


def _wake_attempt_stage(report: dict) -> dict:
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    retry_policy = report.get("retry_policy") if isinstance(report.get("retry_policy"), dict) else {}
    problems = []
    if not attempts:
        problems.append("wake-trial report has no attempts")
    if retry_policy.get("max_attempts", 0) > 2:
        problems.append("wake-trial retry policy exceeds two attempts")
    if attempts:
        final_attempt = attempts[-1] if isinstance(attempts[-1], dict) else {}
        if final_attempt.get("status") != "completed":
            problems.append("final wake attempt did not complete")
        if final_attempt.get("failure_category") not in (None, "none"):
            problems.append(f"final wake failure_category is {final_attempt.get('failure_category')!r}")
    return _stage(
        "m4_wake_attempt",
        not problems,
        required=True,
        message="M4 wake attempt history is successful and bounded" if not problems else "; ".join(problems),
        details={
            "attempt_count": len(attempts),
            "retry_policy": retry_policy,
            "final_attempt": attempts[-1] if attempts else {},
        },
    )


def _output_audit_stage(report: dict) -> dict:
    audit = report.get("output_audit") if isinstance(report.get("output_audit"), dict) else {}
    problems = []
    if audit.get("raw_output_storage") != "hash_only":
        problems.append("raw_output_storage is not hash_only")
    if audit.get("initial_raw_output_stored") is True:
        problems.append("initial raw model output was stored")
    if audit.get("final_raw_output_stored") is True:
        problems.append("final raw model output was stored")
    return _stage(
        "m4_output_audit",
        not problems,
        required=True,
        message="M4 wake output audit is hash-only" if not problems else "; ".join(problems),
        details=audit,
    )


def _latest_event_stage(paths: CompanionPaths, report: dict) -> dict:
    event = report.get("latest_event") if isinstance(report.get("latest_event"), dict) else {}
    problems = []
    if event.get("status") != "completed":
        problems.append("latest event did not complete")
    if event.get("provider") != EXPECTED_PROVIDER:
        problems.append(f"latest event provider is {event.get('provider')!r}")
    if event.get("memory_backend") != EXPECTED_MEMORY_MODE:
        problems.append(f"latest event memory_backend is {event.get('memory_backend')!r}")
    journal = event.get("journal")
    journal_exists = None
    if journal:
        journal_path = paths.home / journal
        journal_exists = journal_path.exists()
        if not journal_exists:
            problems.append(f"latest event journal is missing: {journal}")
    else:
        problems.append("latest event has no journal path")
    return _stage(
        "m4_latest_event",
        not problems,
        required=True,
        message="latest M4 wake event is complete and journaled" if not problems else "; ".join(problems),
        details={
            "id": event.get("id"),
            "trigger": event.get("trigger"),
            "status": event.get("status"),
            "provider": event.get("provider"),
            "memory_backend": event.get("memory_backend"),
            "journal": journal,
            "journal_exists": journal_exists,
        },
    )


def _semantic_shadow_authority_stage(paths: CompanionPaths) -> dict:
    audit = audit_semantic_shadow_authority(paths)
    return _stage(
        "semantic_shadow_authority",
        audit.get("ok") is True,
        required=True,
        message=audit.get("message", "semantic shadow authority audit completed"),
        details=audit,
    )


def _dashboard_read_only_stage(paths: CompanionPaths) -> dict:
    module_path = paths.window_dir / "window.py"
    module, error = _load_window_module(paths, module_path)
    if error:
        return _stage(
            "dashboard_life_read_only",
            False,
            required=True,
            message=error,
            details={"path": _relative(paths, module_path)},
        )

    app = getattr(module, "app", None)
    if app is None or not hasattr(app, "url_map"):
        return _stage(
            "dashboard_life_read_only",
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
        if _is_m4_dashboard_route(rule.rule, rule.endpoint) and methods != ["GET"]:
            relevant_non_get.append(route)

    problems = []
    if not has_life_get:
        problems.append("/life GET route is missing")
    if relevant_non_get:
        problems.append("M3/M4 dashboard routes expose non-GET methods")

    return _stage(
        "dashboard_life_read_only",
        not problems,
        required=True,
        message="/life M3/M4 dashboard surface is read-only" if not problems else "; ".join(problems),
        details={
            "path": _relative(paths, module_path),
            "life_get_route": has_life_get,
            "m3_m4_non_get_routes": relevant_non_get,
            "non_get_routes": [route for route in routes if route["methods"] != ["GET"]],
        },
    )


def _platform_identity_stage(*, require_raspberry_pi: bool) -> dict:
    identity = _platform_identity()
    raspberry_pi = identity["raspberry_pi_detected"] is True
    ok = raspberry_pi or not require_raspberry_pi
    return _stage(
        "platform_identity",
        ok,
        required=require_raspberry_pi,
        status="passed" if raspberry_pi else "warning",
        message=(
            "Raspberry Pi platform detected"
            if raspberry_pi
            else "Raspberry Pi platform was not detected; this is advisory unless --require-raspberry-pi is set"
        ),
        details=identity | {"require_raspberry_pi": require_raspberry_pi},
    )


def _platform_identity() -> dict:
    model_path = Path("/proc/device-tree/model")
    model = None
    try:
        model = model_path.read_text(errors="ignore").strip("\x00\n ")
    except OSError:
        pass
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "device_tree_model": model,
        "raspberry_pi_detected": bool(model and "raspberry pi" in model.lower()),
    }


def _load_window_module(paths: CompanionPaths, module_path: Path) -> tuple[ModuleType | None, str | None]:
    if not module_path.exists():
        return None, f"window module is missing: {module_path}"
    module_name = f"_m4_window_validation_{uuid.uuid4().hex}"
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


def _is_m4_dashboard_route(rule: str, endpoint: str) -> bool:
    route_text = f"{rule} {endpoint}".lower()
    route_tokens = ("m3", "m4", "deploy", "wake_trial", "life")
    return any(token in route_text for token in route_tokens)


def _profile_problems(profile: dict, expected: dict) -> list[str]:
    problems = []
    for key, expected_value in expected.items():
        if profile.get(key) != expected_value:
            problems.append(f"{key} is {profile.get(key)!r}, expected {expected_value!r}")
    return problems


def _skipped_stages(reason: str, names: tuple[str, ...]) -> list[dict]:
    return [
        _stage(name, False, required=True, status="skipped", message=reason)
        for name in names
    ]


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    if not isinstance(report, dict):
        return {"path": _relative(paths, path), "loaded": False}
    return {
        "path": _relative(paths, path),
        "loaded": True,
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "saved_at": report.get("saved_at"),
    }


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
