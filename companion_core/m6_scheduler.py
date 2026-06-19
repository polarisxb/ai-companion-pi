"""M6.6 read-only scheduler handoff readiness gate."""

from __future__ import annotations

import json
import platform
import shlex
import sys
from pathlib import Path
from typing import Callable

from .m4_guard import run_m4_post_change_guard
from .m5_freeze import run_m5_final_freeze
from .m6_manual_wake import READY_RECOMMENDATION as M6_MANUAL_WAKE_READY
from .m6_observation import READY_RECOMMENDATION as M6_OBSERVATION_READY
from .m6_recovery import READY_RECOMMENDATION as M6_RECOVERY_READY
from .paths import CompanionPaths


READY_RECOMMENDATION = "ready_for_scheduler_handoff"
EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
M4_GUARD_RECOMMENDATION = "m4_still_deployable"
M5_FREEZE_RECOMMENDATION = "m5_frozen_ready_for_m6"

PlatformIdentityProvider = Callable[[], dict]
ReportRunner = Callable[[CompanionPaths], dict]


def run_m6_scheduler_readiness_check(
    paths: CompanionPaths,
    *,
    manual_wake_report_path: str | Path | None = None,
    observation_report_path: str | Path | None = None,
    recovery_report_path: str | Path | None = None,
    rollback_instructions_path: str | Path | None = None,
    require_raspberry_pi: bool = True,
    platform_identity_provider: PlatformIdentityProvider | None = None,
    m4_guard_runner: ReportRunner | None = None,
    m5_freeze_runner: ReportRunner | None = None,
) -> dict:
    """Decide whether the frozen wake path is ready for scheduler handoff."""

    manual_file = _resolve_report_path(paths, manual_wake_report_path, "m6_pi_manual_wake_report.json")
    observation_file = _resolve_report_path(paths, observation_report_path, "m6_pi_observation_report.json")
    recovery_file = _resolve_report_path(paths, recovery_report_path, "m6_recovery_drill_report.json")
    rollback_file = (
        Path(rollback_instructions_path).expanduser().resolve()
        if rollback_instructions_path
        else paths.home / "docs" / "m6-pi-scheduler-readiness-design.md"
    )

    manual_report, manual_stage = _manual_wake_stage(paths, manual_file)
    observation_report, observation_stage = _observation_stage(paths, observation_file)
    recovery_report, recovery_stage = _recovery_stage(paths, recovery_file)
    identity = platform_identity_provider() if platform_identity_provider else _platform_identity()
    handoff_command = _handoff_command(paths)

    stages = [
        manual_stage,
        observation_stage,
        recovery_stage,
        _platform_identity_stage(identity, require_raspberry_pi=require_raspberry_pi),
        _handoff_target_stage(paths, handoff_command),
        _rollback_instructions_stage(paths, rollback_file),
        _scheduler_boundary_stage(),
    ]

    m4_guard_report, m4_stage = _run_current_report_stage(
        paths,
        m4_guard_runner or run_m4_post_change_guard,
        name="m4_post_change_guard_current",
        expected_milestone="M4.7",
        expected_recommendation=M4_GUARD_RECOMMENDATION,
        ready_message="current M4 post-change guard remains deployable",
    )
    m5_freeze_report, m5_stage = _run_current_report_stage(
        paths,
        m5_freeze_runner or run_m5_final_freeze,
        name="m5_final_freeze_current",
        expected_milestone="M5.7",
        expected_recommendation=M5_FREEZE_RECOMMENDATION,
        ready_message="current M5.7 final freeze remains ready for M6",
    )
    stages.extend([m4_stage, m5_stage])

    stop_reasons = _stop_reasons(stages)
    recommendation = _recommendation(stop_reasons, identity, require_raspberry_pi=require_raspberry_pi)
    ready = recommendation == READY_RECOMMENDATION
    return {
        "ok": ready,
        "milestone": "M6.6",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "pi_presence": {
            "required": require_raspberry_pi,
            "detected": identity.get("raspberry_pi_detected") is True,
            "evidence": [identity.get("device_tree_model")] if identity.get("device_tree_model") else [],
            "claim": (
                "real_pi_scheduler_handoff_readiness"
                if identity.get("raspberry_pi_detected") is True
                else "pi_required"
            ),
        },
        "profile": {
            "name": "m6-scheduler-handoff-readiness",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "timer_installation": False,
            "service_enablement": False,
            "crontab_edit_allowed": False,
            "scheduler_mutation_allowed": False,
            "scheduler_mutation_attempted": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
            "signal_voice_hardware_activation_allowed": False,
            "live_restore_requested": False,
            "live_restore_executed": False,
        },
        "source_reports": {
            "m6_pi_manual_wake": _report_snapshot(manual_report, paths, manual_file),
            "m6_pi_observation": _report_snapshot(observation_report, paths, observation_file),
            "m6_recovery_drill": _report_snapshot(recovery_report, paths, recovery_file),
            "m4_post_change_guard_current": _report_snapshot_from_payload(m4_guard_report),
            "m5_final_freeze_current": _report_snapshot_from_payload(m5_freeze_report),
        },
        "handoff": {
            "ready": ready,
            "mutated": False,
            "target_command": handoff_command,
            "target_script": _relative(paths, paths.home / "scripts" / "run_wake_cycle.py"),
            "recommended_trigger": "scheduled-wake",
            "next_stage": "M6.7" if ready else "M6.6",
        },
        "rollback": {
            "instructions_present": _stage_ok(stages, "rollback_instructions"),
            "instructions_path": _relative(paths, rollback_file),
            "latest_verified_backup": _latest_verified_backup(paths, recovery_report),
            "live_restore_executed": _live_restore_executed(recovery_report),
        },
        "field_pilot": {
            "manual_wake": {"ready": _report_ready(manual_report), "next_stage": "M6.4"},
            "observation": {"ready": _report_ready(observation_report), "next_stage": "M6.5"},
            "recovery": {"ready": _report_ready(recovery_report), "next_stage": "M6.6"},
            "scheduler_readiness": {
                "requested": True,
                "ready": ready,
                "mutated": False,
                "next_stage": "M6.7" if ready else "M6.6",
            },
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": [],
        "next_commands": {
            "m6_scheduler_readiness": _shell_command([
                "python3",
                "scripts/run_m6_scheduler_readiness.py",
                "--companion-home",
                str(paths.home),
            ]),
            "scheduler_handoff_target": handoff_command,
            "m6_final_freeze_later": "requires ready_for_scheduler_handoff",
        },
    }


def _resolve_report_path(paths: CompanionPaths, override: str | Path | None, name: str) -> Path:
    return Path(override).expanduser().resolve() if override else paths.life_loop_dir / name


def _manual_wake_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_manual_wake_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.3", expected_recommendation=M6_MANUAL_WAKE_READY)
    pi = report.get("pi_presence") if isinstance(report.get("pi_presence"), dict) else {}
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    field = report.get("field_pilot") if isinstance(report.get("field_pilot"), dict) else {}
    manual = field.get("manual_wake") if isinstance(field.get("manual_wake"), dict) else {}
    if pi.get("detected") is not True:
        problems.append("M6.3 report was not produced on a detected Raspberry Pi")
    if manual.get("executed") is not True:
        problems.append("M6.3 manual wake was not executed")
    if profile.get("provider_generation_started") is not True:
        problems.append("M6.3 provider generation did not start")
    return report, _stage(
        "m6_manual_wake_report",
        not problems,
        True,
        "M6.3 real Pi manual wake is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _observation_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_observation_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.4", expected_recommendation=M6_OBSERVATION_READY)
    return report, _stage(
        "m6_observation_report",
        not problems,
        True,
        "M6.4 observation is stable" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _recovery_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_recovery_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.5", expected_recommendation=M6_RECOVERY_READY)
    backup = report.get("backup") if isinstance(report.get("backup"), dict) else {}
    restore = report.get("restore_sandbox") if isinstance(report.get("restore_sandbox"), dict) else {}
    secret = report.get("secret_boundary") if isinstance(report.get("secret_boundary"), dict) else {}
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    if backup.get("executed") is not True:
        problems.append("M6.5 backup was not executed")
    if restore.get("executed") is not True:
        problems.append("M6.5 restore sandbox was not executed")
    if _count_int(restore.get("checksum_mismatch_count")) != 0:
        problems.append("M6.5 restore checksum mismatches are present")
    if _count_int(restore.get("invalid_json_count")) != 0:
        problems.append("M6.5 restored JSON validation failures are present")
    if secret.get("secret_values_copied") is True:
        problems.append("M6.5 copied secret values")
    if profile.get("live_restore_executed") is True:
        problems.append("M6.5 executed live restore")
    return report, _stage(
        "m6_recovery_report",
        not problems,
        True,
        "M6.5 recovery readiness is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _load_report(paths: CompanionPaths, path: Path, name: str) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage(name, False, True, f"{name} is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage(name, False, True, f"{name} is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage(name, False, True, f"{name} could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage(name, False, True, f"{name} must be a JSON object")
    return payload, _stage(name, True, True, f"{name} loaded", details=_report_snapshot(payload, paths, path))


def _base_report_problems(report: dict, *, expected_milestone: str, expected_recommendation: str) -> list[str]:
    problems = []
    if report.get("ok") is not True:
        problems.append(f"{expected_milestone} report ok is not true")
    if report.get("milestone") != expected_milestone:
        problems.append(f"milestone is not {expected_milestone}")
    if report.get("recommendation") != expected_recommendation:
        problems.append(f"recommendation is not {expected_recommendation}")
    if report.get("stop_reasons"):
        problems.append(f"{expected_milestone} report has stop_reasons")
    return problems


def _platform_identity_stage(identity: dict, *, require_raspberry_pi: bool) -> dict:
    raspberry_pi = identity.get("raspberry_pi_detected") is True
    ok = raspberry_pi or not require_raspberry_pi
    return _stage(
        "platform_identity",
        ok,
        require_raspberry_pi,
        "Raspberry Pi platform detected" if raspberry_pi else "Raspberry Pi platform was not detected; M6.6 requires the real Pi",
        details=identity,
    )


def _handoff_target_stage(paths: CompanionPaths, command: str) -> dict:
    script = paths.home / "scripts" / "run_wake_cycle.py"
    ok = script.exists() and script.is_file()
    return _stage(
        "handoff_target",
        ok,
        True,
        "scheduler handoff target command is available" if ok else "scheduler handoff target scripts/run_wake_cycle.py is missing",
        details={"target_script": _relative(paths, script), "target_command": command, "mutated": False},
    )


def _rollback_instructions_stage(paths: CompanionPaths, path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return _stage("rollback_instructions", False, True, f"rollback instructions are missing: {path}")
    except OSError as exc:
        return _stage("rollback_instructions", False, True, f"rollback instructions could not be read: {exc}")
    required_markers = (
        "Pause Instructions",
        "Rollback Instructions",
        "backups/m6/",
        "Live restore is still outside M6.6",
    )
    missing = [marker for marker in required_markers if marker not in text]
    return _stage(
        "rollback_instructions",
        not missing,
        True,
        "operator-visible pause and rollback instructions are present"
        if not missing
        else "rollback instructions are missing markers: " + ", ".join(missing),
        details={"path": _relative(paths, path), "missing_markers": missing},
    )


def _scheduler_boundary_stage() -> dict:
    details = {
        "cron_replacement": False,
        "timer_installation": False,
        "service_enablement": False,
        "crontab_edit_allowed": False,
        "scheduler_mutation_allowed": False,
        "scheduler_mutation_attempted": False,
    }
    return _stage(
        "scheduler_boundary",
        True,
        True,
        "M6.6 produces readiness only and does not mutate scheduler state",
        details=details,
    )


def _run_current_report_stage(
    paths: CompanionPaths,
    runner: ReportRunner,
    *,
    name: str,
    expected_milestone: str,
    expected_recommendation: str,
    ready_message: str,
) -> tuple[dict | None, dict]:
    try:
        report = runner(paths)
    except Exception as exc:  # pragma: no cover - defensive guard path
        return None, _stage(name, False, True, f"{name} failed: {type(exc).__name__}: {_short(str(exc))}")
    problems = []
    if report.get("ok") is not True:
        problems.append(f"{name} ok is not true")
    if report.get("milestone") != expected_milestone:
        problems.append(f"{name} milestone is not {expected_milestone}")
    if report.get("recommendation") != expected_recommendation:
        problems.append(f"{name} recommendation is not {expected_recommendation}")
    if report.get("stop_reasons"):
        problems.append(f"{name} has stop_reasons")
    return report, _stage(
        name,
        not problems,
        True,
        ready_message if not problems else "; ".join(problems),
        details=_report_snapshot_from_payload(report),
    )


def _recommendation(stop_reasons: list[str], identity: dict, *, require_raspberry_pi: bool) -> str:
    if require_raspberry_pi and identity.get("raspberry_pi_detected") is not True:
        return "pi_required"
    if stop_reasons:
        return "inspect"
    return READY_RECOMMENDATION


def _stop_reasons(stages: list[dict]) -> list[str]:
    return [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]


def _stage(name: str, ok: bool, required: bool, message: str, *, details: dict | None = None) -> dict:
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


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    if not isinstance(report, dict):
        return {"path": _relative(paths, path), "loaded": False}
    return {
        "path": _relative(paths, path),
        "loaded": True,
        "ok": report.get("ok"),
        "milestone": report.get("milestone"),
        "recommendation": report.get("recommendation"),
        "stop_reasons": report.get("stop_reasons", []),
        "saved_at": report.get("saved_at"),
    }


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


def _handoff_command(paths: CompanionPaths) -> str:
    return (
        f"cd {shlex.quote(str(paths.home))} && "
        ".venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {shlex.quote(str(paths.home))} "
        "--provider deepseek --memory-mode json --trigger scheduled-wake"
    )


def _latest_verified_backup(paths: CompanionPaths, report: dict | None) -> str | None:
    if not isinstance(report, dict):
        return None
    backup = report.get("backup") if isinstance(report.get("backup"), dict) else {}
    path = backup.get("path")
    if not path:
        return None
    return _relative(paths, Path(path))


def _live_restore_executed(report: dict | None) -> bool:
    profile = report.get("profile") if isinstance(report, dict) and isinstance(report.get("profile"), dict) else {}
    return profile.get("live_restore_executed") is True


def _report_ready(report: dict | None) -> bool:
    return isinstance(report, dict) and report.get("ok") is True and not report.get("stop_reasons")


def _stage_ok(stages: list[dict], name: str) -> bool:
    return any(stage.get("name") == name and stage.get("ok") is True for stage in stages)


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


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _short(value: str, limit: int = 240) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
