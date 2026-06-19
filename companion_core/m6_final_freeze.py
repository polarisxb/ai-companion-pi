"""M6.7 read-only final freeze for Pi scheduler handoff."""

from __future__ import annotations

import json
import platform
import shlex
import sys
from pathlib import Path
from typing import Callable

from .m4_guard import run_m4_post_change_guard
from .m5_freeze import run_m5_final_freeze
from .m6_preflight import READY_RECOMMENDATION as M6_PREFLIGHT_READY
from .m6_recovery import READY_RECOMMENDATION as M6_RECOVERY_READY
from .m6_scheduler import READY_RECOMMENDATION as M6_SCHEDULER_READY
from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority


READY_RECOMMENDATION = "m6_frozen_ready_for_scheduler_handoff"
EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
M4_GUARD_RECOMMENDATION = "m4_still_deployable"
M5_FREEZE_RECOMMENDATION = "m5_frozen_ready_for_m6"

PlatformIdentityProvider = Callable[[], dict]
ReportRunner = Callable[[CompanionPaths], dict]


def run_m6_final_freeze_check(
    paths: CompanionPaths,
    *,
    preflight_report_path: str | Path | None = None,
    recovery_report_path: str | Path | None = None,
    scheduler_report_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    require_raspberry_pi: bool = True,
    platform_identity_provider: PlatformIdentityProvider | None = None,
    m4_guard_runner: ReportRunner | None = None,
    m5_freeze_runner: ReportRunner | None = None,
) -> dict:
    """Freeze M6 after the Pi field-pilot evidence chain is ready.

    M6.7 is deliberately read-only except for the CLI writing its own report.
    This function does not run a wake, create a provider client, edit scheduler
    state, install timers/services, perform a restore, or change memory
    authority.
    """

    preflight_file = _resolve_report_path(paths, preflight_report_path, "m6_preflight_report.json")
    recovery_file = _resolve_report_path(paths, recovery_report_path, "m6_recovery_drill_report.json")
    scheduler_file = _resolve_report_path(paths, scheduler_report_path, "m6_scheduler_readiness_report.json")
    manifest_file = _resolve_report_path(paths, manifest_path, "m6_migration_manifest.json")

    preflight_report, preflight_stage = _preflight_stage(paths, preflight_file)
    recovery_report, recovery_stage = _recovery_stage(paths, recovery_file)
    scheduler_report, scheduler_stage = _scheduler_stage(paths, scheduler_file)
    manifest_report, manifest_stage = _manifest_m6_7_stage(paths, manifest_file)
    identity = platform_identity_provider() if platform_identity_provider else _platform_identity()

    stages = [
        preflight_stage,
        recovery_stage,
        scheduler_stage,
        manifest_stage,
        _platform_identity_stage(identity, require_raspberry_pi=require_raspberry_pi),
        _readonly_boundary_stage(),
        _scheduler_mutation_flags_stage(preflight_report, recovery_report, scheduler_report),
        _rollback_backup_evidence_stage(paths, recovery_report, scheduler_report),
        _semantic_shadow_authority_stage(paths),
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
        ready_message="current M5.7 final freeze remains ready for scheduler handoff",
    )
    stages.extend([m4_stage, m5_stage])

    stop_reasons = _stop_reasons(stages)
    recommendation = _recommendation(stop_reasons, identity, require_raspberry_pi=require_raspberry_pi)
    ready = recommendation == READY_RECOMMENDATION
    handoff = scheduler_report.get("handoff") if isinstance(scheduler_report, dict) and isinstance(scheduler_report.get("handoff"), dict) else {}
    rollback = scheduler_report.get("rollback") if isinstance(scheduler_report, dict) and isinstance(scheduler_report.get("rollback"), dict) else {}
    return {
        "ok": ready,
        "milestone": "M6.7",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "pi_presence": {
            "required": require_raspberry_pi,
            "detected": identity.get("raspberry_pi_detected") is True,
            "evidence": [identity.get("device_tree_model")] if identity.get("device_tree_model") else [],
            "claim": (
                "real_pi_m6_final_freeze"
                if identity.get("raspberry_pi_detected") is True
                else "pi_required"
            ),
        },
        "profile": _readonly_profile(),
        "source_reports": {
            "m6_migration_manifest": _report_snapshot(manifest_report, paths, manifest_file),
            "m6_preflight": _report_snapshot(preflight_report, paths, preflight_file),
            "m6_recovery_drill": _report_snapshot(recovery_report, paths, recovery_file),
            "m6_scheduler_readiness": _report_snapshot(scheduler_report, paths, scheduler_file),
            "m4_post_change_guard_current": _report_snapshot_from_payload(m4_guard_report),
            "m5_final_freeze_current": _report_snapshot_from_payload(m5_freeze_report),
        },
        "final_freeze": {
            "frozen": ready,
            "readonly": True,
            "scheduler_handoff_ready": handoff.get("ready") is True,
            "scheduler_mutated": handoff.get("mutated") is True,
            "scheduler_mutation_attempted": _profile_value(scheduler_report, "scheduler_mutation_attempted") is True,
            "target_command": handoff.get("target_command"),
            "next_stage": "M7" if ready else "M6.7",
        },
        "handoff": {
            "ready": handoff.get("ready") is True and ready,
            "mutated": handoff.get("mutated") is True,
            "target_command": handoff.get("target_command"),
            "recommended_trigger": handoff.get("recommended_trigger"),
        },
        "rollback": {
            "ready": _stage_ok(stages, "rollback_backup_evidence"),
            "instructions_present": rollback.get("instructions_present") is True,
            "latest_verified_backup": rollback.get("latest_verified_backup"),
            "live_restore_executed": rollback.get("live_restore_executed") is True,
        },
        "field_pilot": {
            "preflight": {"ready": _report_ready(preflight_report), "source_stage": "M6.2"},
            "recovery": {"ready": _report_ready(recovery_report), "source_stage": "M6.5"},
            "scheduler_readiness": {
                "ready": _report_ready(scheduler_report),
                "mutated": handoff.get("mutated") is True,
                "source_stage": "M6.6",
            },
            "final_freeze": {
                "requested": True,
                "ready": ready,
                "next_stage": "M7" if ready else "M6.7",
            },
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": [],
        "next_commands": {
            "m6_final_freeze": _shell_command([
                "python3",
                "scripts/run_m6_final_freeze.py",
                "--companion-home",
                str(paths.home),
            ]),
            "scheduler_handoff_target": handoff.get("target_command"),
            "m7_scheduler_pilot_later": "requires m6_frozen_ready_for_scheduler_handoff",
        },
    }


def _resolve_report_path(paths: CompanionPaths, override: str | Path | None, name: str) -> Path:
    return Path(override).expanduser().resolve() if override else paths.life_loop_dir / name


def _preflight_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_preflight_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.2", expected_recommendation=M6_PREFLIGHT_READY)
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    problems.extend(_profile_false_problems(
        profile,
        (
            "cron_replacement",
            "timer_installation",
            "scheduler_mutation_allowed",
            "semantic_shadow_authoritative",
            "real_wake_requested",
            "provider_generation_requested",
            "dashboard_write_allowed",
            "system_config_mutation_allowed",
            "signal_voice_hardware_activation_allowed",
        ),
    ))
    if profile.get("provider") != EXPECTED_PROVIDER:
        problems.append(f"M6.2 provider is not {EXPECTED_PROVIDER}")
    if profile.get("memory_mode") != EXPECTED_MEMORY_MODE:
        problems.append(f"M6.2 memory_mode is not {EXPECTED_MEMORY_MODE}")
    if profile.get("raw_output_storage_required") != "hash_only":
        problems.append("M6.2 raw_output_storage_required is not hash_only")
    return report, _stage(
        "m6_preflight_report",
        not problems,
        True,
        "M6.2 preflight is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _recovery_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_recovery_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.5", expected_recommendation=M6_RECOVERY_READY)
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    backup = report.get("backup") if isinstance(report.get("backup"), dict) else {}
    restore = report.get("restore_sandbox") if isinstance(report.get("restore_sandbox"), dict) else {}
    secret = report.get("secret_boundary") if isinstance(report.get("secret_boundary"), dict) else {}
    problems.extend(_profile_false_problems(
        profile,
        (
            "cron_replacement",
            "timer_installation",
            "scheduler_mutation_allowed",
            "semantic_shadow_authoritative",
            "real_wake_requested",
            "provider_generation_requested",
            "dashboard_write_allowed",
            "system_config_mutation_allowed",
            "signal_voice_hardware_activation_allowed",
            "live_restore_requested",
            "live_restore_executed",
        ),
    ))
    if backup.get("executed") is not True:
        problems.append("M6.5 backup was not executed")
    if not backup.get("path"):
        problems.append("M6.5 backup path is missing")
    if not backup.get("manifest"):
        problems.append("M6.5 backup manifest is missing")
    if restore.get("executed") is not True:
        problems.append("M6.5 restore sandbox was not executed")
    if _count_int(restore.get("checksum_mismatch_count")) != 0:
        problems.append("M6.5 restore checksum mismatches are present")
    if _count_int(restore.get("invalid_json_count")) != 0:
        problems.append("M6.5 restored JSON validation failures are present")
    if secret.get("metadata_only") is not True:
        problems.append("M6.5 secret boundary is not metadata-only")
    if secret.get("secret_values_copied") is not False:
        problems.append("M6.5 copied secret values")
    return report, _stage(
        "m6_recovery_report",
        not problems,
        True,
        "M6.5 recovery evidence is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _scheduler_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_scheduler_readiness_report")
    if not isinstance(report, dict):
        return report, load_stage
    problems = _base_report_problems(report, expected_milestone="M6.6", expected_recommendation=M6_SCHEDULER_READY)
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    handoff = report.get("handoff") if isinstance(report.get("handoff"), dict) else {}
    rollback = report.get("rollback") if isinstance(report.get("rollback"), dict) else {}
    sources = report.get("source_reports") if isinstance(report.get("source_reports"), dict) else {}
    problems.extend(_profile_false_problems(
        profile,
        (
            "cron_replacement",
            "timer_installation",
            "service_enablement",
            "crontab_edit_allowed",
            "scheduler_mutation_allowed",
            "scheduler_mutation_attempted",
            "semantic_shadow_authoritative",
            "real_wake_requested",
            "provider_generation_requested",
            "dashboard_write_allowed",
            "system_config_mutation_allowed",
            "signal_voice_hardware_activation_allowed",
            "live_restore_requested",
            "live_restore_executed",
        ),
    ))
    if handoff.get("ready") is not True:
        problems.append("M6.6 handoff is not ready")
    if handoff.get("mutated") is not False:
        problems.append("M6.6 handoff mutated scheduler state")
    if rollback.get("instructions_present") is not True:
        problems.append("M6.6 rollback instructions are not present")
    if not rollback.get("latest_verified_backup"):
        problems.append("M6.6 latest verified backup is missing")
    if rollback.get("live_restore_executed") is True:
        problems.append("M6.6 live restore was executed")
    required_sources = {
        "m6_pi_manual_wake": ("M6.3", "continue_pi_observation"),
        "m6_pi_observation": ("M6.4", "stable_pi_field_observed"),
        "m6_recovery_drill": ("M6.5", M6_RECOVERY_READY),
    }
    for name, (milestone, recommendation) in required_sources.items():
        source = sources.get(name) if isinstance(sources.get(name), dict) else {}
        if source.get("loaded") is not True:
            problems.append(f"M6.6 source {name} was not loaded")
        if source.get("ok") is not True:
            problems.append(f"M6.6 source {name} ok is not true")
        if source.get("milestone") != milestone:
            problems.append(f"M6.6 source {name} milestone is not {milestone}")
        if source.get("recommendation") != recommendation:
            problems.append(f"M6.6 source {name} recommendation is not {recommendation}")
        if source.get("stop_reasons"):
            problems.append(f"M6.6 source {name} has stop_reasons")
    return report, _stage(
        "m6_scheduler_readiness_report",
        not problems,
        True,
        "M6.6 scheduler handoff evidence is ready" if not problems else "; ".join(problems),
        details=_report_snapshot(report, paths, path),
    )


def _manifest_m6_7_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    report, load_stage = _load_report(paths, path, "m6_migration_manifest")
    if not isinstance(report, dict):
        return report, load_stage
    package = report.get("deployment_package") if isinstance(report.get("deployment_package"), dict) else {}
    required_paths = package.get("required_repository_paths") if isinstance(package.get("required_repository_paths"), list) else []
    preserve = package.get("runtime_artifacts_to_preserve") if isinstance(package.get("runtime_artifacts_to_preserve"), list) else []
    required_repo = (
        "scripts/run_m6_final_freeze.py",
        "docs/m6-pi-final-freeze-design.md",
    )
    required_runtime = ("life-loop/m6_final_freeze_report.json",)
    problems = _base_report_problems(report, expected_milestone="M6.1", expected_recommendation="migration_manifest_ready")
    for rel_path in required_repo:
        if rel_path not in required_paths:
            problems.append(f"M6.1 manifest required_repository_paths missing {rel_path}")
        elif not (paths.home / rel_path).exists():
            problems.append(f"M6.7 repository artifact is missing: {rel_path}")
    for rel_path in required_runtime:
        if rel_path not in preserve:
            problems.append(f"M6.1 manifest runtime_artifacts_to_preserve missing {rel_path}")
    return report, _stage(
        "m6_manifest_m6_7_artifacts",
        not problems,
        True,
        "M6.1 manifest carries M6.7 script, design, and report evidence"
        if not problems
        else "; ".join(problems),
        details={
            "path": _relative(paths, path),
            "required_repository_paths": list(required_repo),
            "runtime_artifacts_to_preserve": list(required_runtime),
        },
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
        "Raspberry Pi platform detected"
        if raspberry_pi
        else "Raspberry Pi platform was not detected; M6.7 requires the real Pi",
        details=identity,
    )


def _readonly_boundary_stage() -> dict:
    return _stage(
        "final_freeze_readonly_boundary",
        True,
        True,
        "M6.7 reads evidence only and does not mutate runtime authority or scheduler state",
        details=_readonly_profile(),
    )


def _scheduler_mutation_flags_stage(*reports: dict | None) -> dict:
    details = []
    problems = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        label = str(report.get("milestone", "unknown"))
        profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
        for key in (
            "cron_replacement",
            "timer_installation",
            "service_enablement",
            "crontab_edit_allowed",
            "scheduler_mutation_allowed",
            "scheduler_mutation_attempted",
            "dashboard_write_allowed",
            "system_config_mutation_allowed",
            "semantic_shadow_authoritative",
            "provider_generation_requested",
            "live_restore_requested",
            "live_restore_executed",
        ):
            if key in profile:
                value = profile.get(key)
                details.append({"report": label, "field": f"profile.{key}", "value": value})
                if value is not False:
                    problems.append(f"{label} profile.{key} is not false")
        handoff = report.get("handoff") if isinstance(report.get("handoff"), dict) else {}
        if "mutated" in handoff:
            value = handoff.get("mutated")
            details.append({"report": label, "field": "handoff.mutated", "value": value})
            if value is not False:
                problems.append(f"{label} handoff.mutated is not false")
        field = report.get("field_pilot") if isinstance(report.get("field_pilot"), dict) else {}
        scheduler = field.get("scheduler_readiness") if isinstance(field.get("scheduler_readiness"), dict) else {}
        if "mutated" in scheduler:
            value = scheduler.get("mutated")
            details.append({"report": label, "field": "field_pilot.scheduler_readiness.mutated", "value": value})
            if value is not False:
                problems.append(f"{label} field_pilot.scheduler_readiness.mutated is not false")
    return _stage(
        "scheduler_mutation_flags",
        not problems,
        True,
        "scheduler, service, dashboard, provider, restore, and authority mutation flags are false"
        if not problems
        else "; ".join(problems),
        details={"flags": details},
    )


def _rollback_backup_evidence_stage(
    paths: CompanionPaths,
    recovery_report: dict | None,
    scheduler_report: dict | None,
) -> dict:
    problems = []
    recovery_backup = recovery_report.get("backup") if isinstance(recovery_report, dict) and isinstance(recovery_report.get("backup"), dict) else {}
    recovery_restore = recovery_report.get("restore_sandbox") if isinstance(recovery_report, dict) and isinstance(recovery_report.get("restore_sandbox"), dict) else {}
    scheduler_rollback = scheduler_report.get("rollback") if isinstance(scheduler_report, dict) and isinstance(scheduler_report.get("rollback"), dict) else {}
    backup_path = _coerce_path(paths, recovery_backup.get("path"))
    manifest_path = _coerce_path(paths, recovery_backup.get("manifest"))
    latest_verified = scheduler_rollback.get("latest_verified_backup")
    latest_verified_path = _coerce_path(paths, latest_verified)
    if backup_path is None:
        problems.append("M6.5 backup path is missing")
    elif not backup_path.exists():
        problems.append(f"M6.5 backup path does not exist: {_relative(paths, backup_path)}")
    if manifest_path is None:
        problems.append("M6.5 backup manifest path is missing")
    elif not manifest_path.exists():
        problems.append(f"M6.5 backup manifest does not exist: {_relative(paths, manifest_path)}")
    if latest_verified_path is None:
        problems.append("M6.6 latest verified backup is missing")
    elif backup_path is not None and _relative(paths, latest_verified_path) != _relative(paths, backup_path):
        problems.append("M6.6 latest verified backup does not match M6.5 backup path")
    if recovery_restore.get("executed") is not True:
        problems.append("M6.5 restore sandbox was not executed")
    if _count_int(recovery_restore.get("checksum_mismatch_count")) != 0:
        problems.append("M6.5 restore checksum mismatches are present")
    if _count_int(recovery_restore.get("invalid_json_count")) != 0:
        problems.append("M6.5 restored JSON validation failures are present")
    return _stage(
        "rollback_backup_evidence",
        not problems,
        True,
        "rollback backup and restore evidence exists" if not problems else "; ".join(problems),
        details={
            "backup_path": _relative(paths, backup_path) if backup_path else None,
            "manifest_path": _relative(paths, manifest_path) if manifest_path else None,
            "latest_verified_backup": latest_verified,
            "verified_artifact_count": recovery_restore.get("verified_artifact_count"),
            "checksum_mismatch_count": recovery_restore.get("checksum_mismatch_count"),
            "invalid_json_count": recovery_restore.get("invalid_json_count"),
        },
    )


def _semantic_shadow_authority_stage(paths: CompanionPaths) -> dict:
    audit = audit_semantic_shadow_authority(paths)
    return _stage(
        "semantic_shadow_authority",
        audit.get("ok") is True,
        True,
        audit.get("message", "semantic shadow authority audit completed"),
        details=audit,
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
    if not isinstance(report, dict):
        return None, _stage(name, False, True, f"{name} did not return a report object")
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


def _readonly_profile() -> dict:
    return {
        "name": "m6-final-freeze",
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cron_replacement": False,
        "timer_installation": False,
        "service_enablement": False,
        "crontab_edit_allowed": False,
        "scheduler_mutation_allowed": False,
        "scheduler_mutation_attempted": False,
        "scheduler_handoff_performed": False,
        "semantic_shadow_authoritative": False,
        "real_wake_requested": False,
        "provider_generation_requested": False,
        "raw_output_storage_required": "hash_only",
        "dashboard_write_allowed": False,
        "system_config_mutation_allowed": False,
        "signal_voice_hardware_activation_allowed": False,
        "live_restore_requested": False,
        "live_restore_executed": False,
    }


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    if not isinstance(report, dict):
        return {"path": _relative(paths, path), "loaded": False}
    snapshot = _report_snapshot_from_payload(report)
    snapshot["path"] = _relative(paths, path)
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


def _profile_false_problems(profile: dict, keys: tuple[str, ...]) -> list[str]:
    return [f"{key} is not false" for key in keys if profile.get(key) is not False]


def _profile_value(report: dict | None, key: str):
    profile = report.get("profile") if isinstance(report, dict) and isinstance(report.get("profile"), dict) else {}
    return profile.get(key)


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


def _coerce_path(paths: CompanionPaths, value) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = paths.home / path
    return path.resolve()


def _relative(paths: CompanionPaths, path: Path | None) -> str | None:
    if path is None:
        return None
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
