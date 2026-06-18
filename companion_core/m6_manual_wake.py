"""M6.3 controlled real-Pi manual wake trial wrapper."""

from __future__ import annotations

import json
import platform
import shlex
import sys
from pathlib import Path
from typing import Callable

from .m6_preflight import READY_RECOMMENDATION as M6_PREFLIGHT_READY
from .output_archive import STORE_RAW_OUTPUTS_ENV, should_store_raw_outputs
from .paths import CompanionPaths
from .wake_trial import run_m4_wake_trial


EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
DEFAULT_TRIGGER = "m6-pi-manual-wake"
READY_RECOMMENDATION = "continue_pi_observation"

WakeTrialRunner = Callable[..., dict]
PlatformIdentityProvider = Callable[[], dict]


def run_m6_pi_manual_wake_trial(
    paths: CompanionPaths,
    *,
    confirm_real_pi_wake: bool = False,
    preflight_report_path: str | Path | None = None,
    trigger: str = DEFAULT_TRIGGER,
    timeout_seconds: int = 300,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
    deploy_report_path: str | Path | None = None,
    max_attempts: int = 2,
    platform_identity_provider: PlatformIdentityProvider | None = None,
    wake_trial_runner: WakeTrialRunner | None = None,
) -> dict:
    """Run the M6.3 manual wake only after explicit real-Pi gates pass.

    By default this function is a guard report: it does not create a provider
    client and does not run a wake. A real wake can happen only when the caller
    explicitly sets ``confirm_real_pi_wake=True`` and the platform identity
    identifies the host as a Raspberry Pi with M6.2 preflight ready.
    """

    preflight_file = (
        Path(preflight_report_path).expanduser().resolve()
        if preflight_report_path
        else paths.life_loop_dir / "m6_preflight_report.json"
    )
    preflight_report, preflight_stage = _load_preflight_stage(paths, preflight_file)
    identity = (
        platform_identity_provider() if platform_identity_provider else _platform_identity()
    )
    stages = [
        preflight_stage,
        _operator_confirmation_stage(confirm_real_pi_wake),
        _platform_identity_stage(identity),
        _profile_stage(trigger, confirm_real_pi_wake),
        _raw_output_storage_stage(),
    ]

    prereq_stop_reasons = _stop_reasons(stages)
    wake_report: dict | None = None
    if not prereq_stop_reasons:
        runner = wake_trial_runner or run_m4_wake_trial
        try:
            wake_report = runner(
                paths,
                trigger=trigger,
                timeout_seconds=timeout_seconds,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
                deploy_report_path=deploy_report_path,
                max_attempts=max_attempts,
            )
            stages.append(_wake_report_stage(wake_report, max_attempts=max_attempts))
        except Exception as exc:  # pragma: no cover - defensive guard path
            stages.append(_stage(
                "m4_wake_trial_delegate",
                False,
                True,
                f"M4 wake trial delegate failed: {type(exc).__name__}: {_short(str(exc))}",
            ))

    stop_reasons = _stop_reasons(stages)
    recommendation = _recommendation(stop_reasons, identity)
    return {
        "ok": not stop_reasons,
        "milestone": "M6.3",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "pi_presence": {
            "required": True,
            "detected": identity.get("raspberry_pi_detected") is True,
            "evidence": [identity.get("device_tree_model")]
            if identity.get("device_tree_model")
            else [],
            "claim": (
                "real_pi_manual_wake_trial"
                if identity.get("raspberry_pi_detected") is True
                else "pi_required"
            ),
        },
        "profile": {
            "name": "m6-real-pi-manual-wake-trial",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "trigger": trigger,
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": confirm_real_pi_wake,
            "provider_generation_requested": confirm_real_pi_wake,
            "provider_generation_started": isinstance(wake_report, dict),
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
            "signal_voice_hardware_activation_allowed": False,
        },
        "source_reports": {
            "m6_preflight": _report_snapshot(preflight_report, paths, preflight_file),
            "m4_manual_wake_delegate": _report_snapshot_from_payload(wake_report),
        },
        "field_pilot": {
            "manual_wake": {
                "requested": confirm_real_pi_wake,
                "executed": isinstance(wake_report, dict),
                "trigger": trigger,
                "attempt_count": _attempt_count(wake_report),
                "next_stage": "M6.4",
            },
            "observation": {"requested": False, "next_stage": "M6.4"},
            "recovery": {"requested": False, "next_stage": "M6.5"},
            "scheduler_readiness": {"mutated": False, "readiness_stage": "M6.6"},
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": _pending_reasons(stop_reasons, identity),
        "next_commands": {
            "m6_preflight": _shell_command([
                "python3",
                "scripts/run_m6_preflight.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m6_pi_manual_wake_real_pi_only": _shell_command([
                "python3",
                "scripts/run_m6_pi_manual_wake_trial.py",
                "--companion-home",
                str(paths.home),
                "--confirm-real-pi-wake",
            ]),
            "m6_4_observation_later": "requires successful M6.3 real Pi manual wake report",
        },
    }


def _load_preflight_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage("m6_preflight_report", False, True, f"M6.2 preflight report is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage("m6_preflight_report", False, True, f"M6.2 preflight report is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage("m6_preflight_report", False, True, f"M6.2 preflight report could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage("m6_preflight_report", False, True, "M6.2 preflight report must be a JSON object")

    problems = []
    if payload.get("ok") is not True:
        problems.append("M6.2 preflight ok is not true")
    if payload.get("milestone") != "M6.2":
        problems.append("M6.2 preflight milestone is not M6.2")
    if payload.get("recommendation") != M6_PREFLIGHT_READY:
        problems.append(f"M6.2 preflight recommendation is not {M6_PREFLIGHT_READY}")
    if payload.get("stop_reasons"):
        problems.append("M6.2 preflight has stop_reasons")

    return payload, _stage(
        "m6_preflight_report",
        not problems,
        True,
        "M6.2 preflight is ready for real Pi manual wake" if not problems else "; ".join(problems),
        details=_report_snapshot(payload, paths, path),
    )


def _operator_confirmation_stage(confirm_real_pi_wake: bool) -> dict:
    return _stage(
        "explicit_manual_wake_confirmation",
        confirm_real_pi_wake,
        True,
        (
            "operator explicitly confirmed M6.3 real Pi manual wake"
            if confirm_real_pi_wake
            else "missing --confirm-real-pi-wake; M6.3 will not run a real wake"
        ),
        details={"confirm_real_pi_wake": confirm_real_pi_wake},
    )


def _platform_identity_stage(identity: dict) -> dict:
    raspberry_pi = identity.get("raspberry_pi_detected") is True
    return _stage(
        "platform_identity",
        raspberry_pi,
        True,
        "Raspberry Pi platform detected"
        if raspberry_pi
        else "Raspberry Pi platform was not detected; M6.3 requires the real Pi",
        details=identity,
    )


def _profile_stage(trigger: str, confirm_real_pi_wake: bool) -> dict:
    return _stage(
        "m6_manual_wake_profile",
        True,
        True,
        "M6.3 profile preserves frozen DeepSeek/json authority boundaries",
        details={
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "trigger": trigger,
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": confirm_real_pi_wake,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
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
        else f"raw model output storage is enabled; unset {STORE_RAW_OUTPUTS_ENV} before M6.3",
        details={"raw_output_storage": "enabled" if raw_enabled else "hash_only"},
    )


def _wake_report_stage(report: dict, *, max_attempts: int) -> dict:
    problems = []
    if not isinstance(report, dict):
        return _stage("m4_wake_trial_delegate", False, True, "M4 wake trial delegate did not return a report object")
    if report.get("ok") is not True:
        problems.append("delegate wake report ok is not true")
    if report.get("milestone") != "M4.3":
        problems.append("delegate wake report milestone is not M4.3")
    if report.get("recommendation") != "continue_runtime_validation":
        problems.append("delegate wake report recommendation is not continue_runtime_validation")
    if report.get("stop_reasons"):
        problems.append("delegate wake report has stop_reasons")
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    if not attempts:
        problems.append("delegate wake report has no attempts")
    if len(attempts) > max_attempts:
        problems.append("delegate wake attempts exceed M6.3 max_attempts")
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    if profile.get("provider") != EXPECTED_PROVIDER:
        problems.append("delegate wake provider is not deepseek")
    if profile.get("memory_mode") != EXPECTED_MEMORY_MODE:
        problems.append("delegate wake memory mode is not json")
    if profile.get("cron_replacement") is not False:
        problems.append("delegate wake profile changed cron policy")
    if profile.get("semantic_shadow_authoritative") is not False:
        problems.append("delegate wake profile made semantic shadow authoritative")
    output_audit = report.get("output_audit") if isinstance(report.get("output_audit"), dict) else {}
    if output_audit.get("raw_output_storage") != "hash_only":
        problems.append("delegate wake raw output storage is not hash-only")
    if output_audit.get("initial_raw_output_stored") is True or output_audit.get("final_raw_output_stored") is True:
        problems.append("delegate wake stored raw model output")
    quality_gate = report.get("quality_gate") if isinstance(report.get("quality_gate"), dict) else {}
    if quality_gate.get("context_eligible") is False:
        problems.append("delegate wake rejected future-context writes")
    grounding = report.get("grounding") if isinstance(report.get("grounding"), dict) else {}
    if _count_int(grounding.get("unsupported")):
        problems.append(f"delegate wake unsupported grounding claims ({_count_int(grounding.get('unsupported'))})")

    return _stage(
        "m4_wake_trial_delegate",
        not problems,
        True,
        "M6.3 delegate wake completed within M4/M5 authority boundaries"
        if not problems
        else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "attempt_count": len(attempts),
            "latest_event": report.get("latest_event") if isinstance(report.get("latest_event"), dict) else {},
            "failure_audit": report.get("failure_audit") if isinstance(report.get("failure_audit"), dict) else {},
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _recommendation(stop_reasons: list[str], identity: dict) -> str:
    if not stop_reasons:
        return READY_RECOMMENDATION
    if (
        identity.get("raspberry_pi_detected") is not True
        and stop_reasons
        and all("platform_identity:" in reason for reason in stop_reasons)
    ):
        return "pi_required"
    return "inspect"


def _pending_reasons(stop_reasons: list[str], identity: dict) -> list[str]:
    if identity.get("raspberry_pi_detected") is True:
        return []
    if any("platform_identity:" in reason for reason in stop_reasons):
        return ["real Raspberry Pi required for M6.3"]
    return []


def _stop_reasons(stages: list[dict]) -> list[str]:
    return [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]


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


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    snapshot = {"path": _relative(paths, path), "loaded": isinstance(report, dict)}
    if isinstance(report, dict):
        snapshot.update({
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _report_snapshot_from_payload(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return {"loaded": False}
    return {
        "loaded": True,
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "stop_reasons": report.get("stop_reasons", []),
        "saved_at": report.get("saved_at"),
    }


def _attempt_count(report: dict | None) -> int:
    if not isinstance(report, dict):
        return 0
    attempts = report.get("attempts")
    return len(attempts) if isinstance(attempts, list) else 0


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


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _short(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[:limit]}..."


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
