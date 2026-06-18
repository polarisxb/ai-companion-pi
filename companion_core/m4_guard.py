"""Post-change guard for preserving M4 deployment readiness."""

from __future__ import annotations

import shlex
from typing import Callable

from .deploy_runtime import ImportProbe, run_m4_deploy_check
from .m4_validation import run_m4_runtime_validation
from .paths import CompanionPaths


def run_m4_post_change_guard(
    paths: CompanionPaths,
    *,
    import_probe: ImportProbe | None = None,
    runtime_validator: Callable[..., dict] | None = None,
) -> dict:
    """Check that continued development has not broken the M4 deploy baseline.

    This is intentionally non-generative: it does not run a wake and does not
    call the provider. It expects an existing successful M4 wake-trial report.
    """

    deploy_report = run_m4_deploy_check(paths, import_probe=import_probe)
    validator = runtime_validator or run_m4_runtime_validation
    runtime_report = validator(paths)

    stages = [
        _stage(
            "m4_deploy_check_current",
            deploy_report.get("ok") is True and deploy_report.get("recommendation") == "ready_for_manual_wake",
            required=True,
            message=(
                "current code passes M4 deploy check"
                if deploy_report.get("ok") is True and deploy_report.get("recommendation") == "ready_for_manual_wake"
                else "current code does not pass M4 deploy check"
            ),
            details=_report_snapshot(deploy_report),
        ),
        _stage(
            "m4_runtime_validation_current",
            runtime_report.get("ok") is True and runtime_report.get("recommendation") == "m4_runtime_validated",
            required=True,
            message=(
                "current code preserves M4 runtime validation"
                if runtime_report.get("ok") is True and runtime_report.get("recommendation") == "m4_runtime_validated"
                else "current code does not preserve M4 runtime validation"
            ),
            details=_report_snapshot(runtime_report),
        ),
    ]

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    return {
        "ok": not stop_reasons,
        "milestone": "M4.7",
        "recommendation": "m4_still_deployable" if not stop_reasons else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "m4-post-change-guard",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "requires_existing_wake_trial_report": True,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "next_commands": {
            "post_change_guard": _shell_command([
                "python3",
                "scripts/run_m4_post_change_guard.py",
                "--companion-home",
                str(paths.home),
            ]),
            "full_tests": "python3 -m pytest",
        },
    }


def _report_snapshot(report: dict) -> dict:
    return {
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "stop_reasons": report.get("stop_reasons", []),
    }


def _stage(
    name: str,
    ok: bool,
    *,
    required: bool,
    message: str,
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


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
