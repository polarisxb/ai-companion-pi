"""M6.2 local Pi preflight v2 gate."""

from __future__ import annotations

import json
import platform
import shlex
import sys
from pathlib import Path
from typing import Callable

from .m4_guard import run_m4_post_change_guard
from .m5_freeze import run_m5_final_freeze
from .output_archive import STORE_RAW_OUTPUTS_ENV, should_store_raw_outputs
from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority


EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
READY_RECOMMENDATION = "ready_for_real_pi_manual_wake"
MANIFEST_RECOMMENDATION = "migration_manifest_ready"
M5_FREEZE_RECOMMENDATION = "m5_frozen_ready_for_m6"
M4_GUARD_RECOMMENDATION = "m4_still_deployable"

REQUIRED_EXCLUDES = (
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".secrets/",
    ".env",
    ".env.*",
    "life-loop/model_outputs/",
)
REQUIRED_PRESERVE_ARTIFACTS = (
    "life-loop/m5_final_freeze_report.json",
    "life-loop/m6_migration_manifest.json",
    "life-loop/wake_events.jsonl",
    "memory-server/memory_store.json",
    "requests/requests.json",
    "window/status.json",
)


def run_m6_preflight_check(
    paths: CompanionPaths,
    *,
    manifest_path: str | Path | None = None,
    m4_guard_runner: Callable[[CompanionPaths], dict] | None = None,
    m5_freeze_runner: Callable[[CompanionPaths], dict] | None = None,
) -> dict:
    """Validate local readiness for a later real Pi manual wake.

    This gate is intentionally non-generative. It reads local reports and the
    M6.1 migration manifest, runs no wake cycle, creates no provider client,
    does not call DeepSeek, does not install timers, and does not mutate
    scheduler or system configuration.
    """

    manifest_file = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path
        else paths.life_loop_dir / "m6_migration_manifest.json"
    )
    manifest, manifest_load_stage = _load_report_stage(
        paths,
        manifest_file,
        name="m6_migration_manifest",
        expected_milestone="M6.1",
    )
    stages = [manifest_load_stage]

    if isinstance(manifest, dict):
        stages.extend([
            _manifest_result_stage(manifest),
            _manifest_profile_stage(manifest),
            _manifest_source_reports_stage(manifest),
            _package_inventory_stage(paths, manifest),
            _runtime_preserve_policy_stage(manifest),
            _exclude_policy_stage(manifest),
            _secret_boundary_stage(manifest),
            _network_boundary_stage(manifest),
            _scheduler_boundary_stage(manifest),
            _optional_surface_stage(manifest),
        ])
    else:
        stages.extend(_skipped_stages(
            "M6.1 migration manifest did not load",
            (
                "manifest_result",
                "manifest_profile",
                "manifest_source_reports",
                "package_inventory",
                "runtime_preserve_policy",
                "exclude_policy",
                "secret_boundary",
                "network_boundary",
                "scheduler_boundary",
                "optional_surface_boundary",
            ),
        ))

    m4_guard_report, m4_stage = _run_report_stage(
        paths,
        m4_guard_runner or run_m4_post_change_guard,
        name="m4_post_change_guard_current",
        expected_milestone="M4.7",
        expected_recommendation=M4_GUARD_RECOMMENDATION,
        ready_message="current M4 post-change guard remains deployable",
    )
    m5_freeze_report, m5_stage = _run_report_stage(
        paths,
        m5_freeze_runner or run_m5_final_freeze,
        name="m5_final_freeze_current",
        expected_milestone="M5.7",
        expected_recommendation=M5_FREEZE_RECOMMENDATION,
        ready_message="current M5.7 final freeze remains ready for M6",
    )
    stages.extend([
        m4_stage,
        m5_stage,
        _semantic_shadow_authority_stage(paths),
        _raw_output_storage_stage(),
    ])

    platform_identity = _platform_identity()
    stages.append(_platform_identity_stage(platform_identity))

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    recommendation = READY_RECOMMENDATION if not stop_reasons else "inspect"
    return {
        "ok": not stop_reasons,
        "milestone": "M6.2",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "pi_presence": {
            "required": False,
            "detected": platform_identity["raspberry_pi_detected"],
            "evidence": [platform_identity["device_tree_model"]]
            if platform_identity["device_tree_model"]
            else [],
            "claim": "local_preflight_only",
        },
        "profile": {
            "name": "m6-pi-preflight-v2",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
            "signal_voice_hardware_activation_allowed": False,
        },
        "source_reports": {
            "m6_migration_manifest": _report_snapshot(manifest, paths, manifest_file),
            "m4_post_change_guard_current": _report_snapshot_from_payload(m4_guard_report),
            "m5_final_freeze_current": _report_snapshot_from_payload(m5_freeze_report),
        },
        "field_pilot": {
            "deployment_package": _manifest_section_summary(manifest, "deployment_package"),
            "manual_wake": {"requested": False, "next_stage": "M6.3"},
            "observation": {"requested": False, "next_stage": "M6.4"},
            "recovery": {"requested": False, "next_stage": "M6.5"},
            "scheduler_readiness": {"mutated": False, "readiness_stage": "M6.6"},
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": [],
        "next_commands": {
            "m6_preflight": _shell_command([
                "python3",
                "scripts/run_m6_preflight.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m6_3_requirement": "requires real Raspberry Pi and explicit operator command",
            "m6_3_manual_wake_real_pi_only": _shell_command([
                "python3",
                "scripts/run_m6_pi_manual_wake_trial.py",
                "--companion-home",
                str(paths.home),
                "--confirm-real-pi-wake",
            ]),
            "m5_final_freeze_no_write": _shell_command([
                "python3",
                "scripts/run_m5_final_freeze.py",
                "--companion-home",
                str(paths.home),
                "--no-write-report",
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
        return None, _stage(name, False, True, f"{name} is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage(name, False, True, f"{name} is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage(name, False, True, f"{name} could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage(name, False, True, f"{name} must be a JSON object")
    problems = []
    if payload.get("milestone") != expected_milestone:
        problems.append(f"milestone is not {expected_milestone}")
    return payload, _stage(
        name,
        not problems,
        True,
        f"{name} loaded" if not problems else "; ".join(problems),
        details={
            "path": _relative(paths, path),
            "ok": payload.get("ok"),
            "milestone": payload.get("milestone"),
            "recommendation": payload.get("recommendation"),
            "saved_at": payload.get("saved_at"),
        },
    )


def _manifest_result_stage(manifest: dict) -> dict:
    problems = []
    if manifest.get("ok") is not True:
        problems.append("migration manifest ok is not true")
    if manifest.get("recommendation") != MANIFEST_RECOMMENDATION:
        problems.append(f"migration manifest recommendation is not {MANIFEST_RECOMMENDATION}")
    if manifest.get("stop_reasons"):
        problems.append("migration manifest has stop_reasons")
    return _stage(
        "manifest_result",
        not problems,
        True,
        "M6.1 migration manifest is ready" if not problems else "; ".join(problems),
        details={
            "ok": manifest.get("ok"),
            "recommendation": manifest.get("recommendation"),
            "stop_reasons": manifest.get("stop_reasons", []),
        },
    )


def _manifest_profile_stage(manifest: dict) -> dict:
    profile = manifest.get("profile") if isinstance(manifest.get("profile"), dict) else {}
    expected = {
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cron_replacement": False,
        "timer_installation": False,
        "scheduler_mutation_allowed": False,
        "semantic_shadow_authoritative": False,
        "real_wake_requested": False,
        "provider_generation_requested": False,
        "raw_output_storage_required": "hash_only",
        "dashboard_write_allowed": False,
        "system_config_mutation_allowed": False,
        "signal_voice_hardware_activation_allowed": False,
    }
    problems = _profile_problems(profile, expected)
    return _stage(
        "manifest_profile",
        not problems,
        True,
        "M6.1 manifest preserves the frozen M3-M5 contract" if not problems else "; ".join(problems),
        details={"expected": expected, "actual": profile},
    )


def _manifest_source_reports_stage(manifest: dict) -> dict:
    reports = manifest.get("source_reports") if isinstance(manifest.get("source_reports"), dict) else {}
    required = {
        "m4_post_change_guard": ("M4.7", M4_GUARD_RECOMMENDATION),
        "m5_quality_release": ("M5.6", "m5_quality_ready_for_m6"),
        "m5_final_freeze": ("M5.7", M5_FREEZE_RECOMMENDATION),
    }
    problems = []
    details = {}
    for name, (milestone, recommendation) in required.items():
        report = reports.get(name) if isinstance(reports.get(name), dict) else {}
        details[name] = {
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "saved_at": report.get("saved_at"),
        }
        if report.get("ok") is not True:
            problems.append(f"{name} ok is not true")
        if report.get("milestone") != milestone:
            problems.append(f"{name} milestone is not {milestone}")
        if report.get("recommendation") != recommendation:
            problems.append(f"{name} recommendation is not {recommendation}")
    return _stage(
        "manifest_source_reports",
        not problems,
        True,
        "M6.1 manifest snapshots passing M4/M5 evidence" if not problems else "; ".join(problems),
        details=details,
    )


def _package_inventory_stage(paths: CompanionPaths, manifest: dict) -> dict:
    package = manifest.get("deployment_package") if isinstance(manifest.get("deployment_package"), dict) else {}
    required_paths = package.get("required_repository_paths") if isinstance(package.get("required_repository_paths"), list) else []
    checks = []
    for rel_path in required_paths:
        if not isinstance(rel_path, str) or not rel_path:
            checks.append({"path": rel_path, "ok": False, "message": "path must be a non-empty string"})
            continue
        path = paths.home / rel_path.rstrip("/")
        ok = path.exists()
        if rel_path.endswith("/") and ok:
            ok = path.is_dir()
        elif ok:
            ok = path.is_file() or path.is_dir()
        checks.append({
            "path": rel_path,
            "ok": ok,
            "message": f"{rel_path} is present" if ok else f"{rel_path} is missing",
        })
    problems = [check["message"] for check in checks if not check["ok"]]
    if not required_paths:
        problems.append("required_repository_paths is empty or missing")
    return _stage(
        "package_inventory",
        not problems,
        True,
        "M6.1 required repository paths are present" if not problems else "; ".join(problems),
        details={"required_repository_paths": checks},
    )


def _runtime_preserve_policy_stage(manifest: dict) -> dict:
    package = manifest.get("deployment_package") if isinstance(manifest.get("deployment_package"), dict) else {}
    artifacts = package.get("runtime_artifacts_to_preserve") if isinstance(package.get("runtime_artifacts_to_preserve"), list) else []
    missing = [artifact for artifact in REQUIRED_PRESERVE_ARTIFACTS if artifact not in artifacts]
    return _stage(
        "runtime_preserve_policy",
        not missing,
        True,
        "M6.1 preserve policy covers required runtime artifacts"
        if not missing
        else f"preserve policy missing: {', '.join(missing)}",
        details={
            "required": list(REQUIRED_PRESERVE_ARTIFACTS),
            "present": artifacts,
        },
    )


def _exclude_policy_stage(manifest: dict) -> dict:
    package = manifest.get("deployment_package") if isinstance(manifest.get("deployment_package"), dict) else {}
    excludes = package.get("exclude_from_transfer") if isinstance(package.get("exclude_from_transfer"), list) else []
    missing = [item for item in REQUIRED_EXCLUDES if item not in excludes]
    return _stage(
        "exclude_policy",
        not missing,
        True,
        "M6.1 excludes local caches, virtualenvs, secrets, and raw outputs"
        if not missing
        else f"exclude policy missing: {', '.join(missing)}",
        details={
            "required": list(REQUIRED_EXCLUDES),
            "present": excludes,
        },
    )


def _secret_boundary_stage(manifest: dict) -> dict:
    boundary = manifest.get("secret_boundary") if isinstance(manifest.get("secret_boundary"), dict) else {}
    problems = []
    if boundary.get("copy_secret_values") is not False:
        problems.append("copy_secret_values must be false")
    expected_paths = boundary.get("expected_pi_secret_paths")
    if not isinstance(expected_paths, list) or ".secrets/deepseek.env" not in expected_paths:
        problems.append("expected Pi DeepSeek secret path metadata is missing")
    env_names = boundary.get("expected_environment_variables")
    if not isinstance(env_names, list) or "DEEPSEEK_API_KEY" not in env_names:
        problems.append("DEEPSEEK_API_KEY metadata is missing")
    serialized = json.dumps(boundary, ensure_ascii=False)
    if any(token in serialized for token in ("sk-", "m5-secret", "m6-secret")):
        problems.append("secret boundary appears to contain a secret-like value")
    return _stage(
        "secret_boundary",
        not problems,
        True,
        "M6.1 manifest contains secret metadata only" if not problems else "; ".join(problems),
        details={
            "copy_secret_values": boundary.get("copy_secret_values"),
            "expected_pi_secret_paths": expected_paths if isinstance(expected_paths, list) else [],
            "expected_environment_variables": env_names if isinstance(env_names, list) else [],
        },
    )


def _network_boundary_stage(manifest: dict) -> dict:
    boundary = manifest.get("network_boundary") if isinstance(manifest.get("network_boundary"), dict) else {}
    expected = {
        "dashboard_write_allowed": False,
        "new_lan_exposure_allowed": False,
        "firewall_or_router_changes_allowed": False,
    }
    problems = _profile_problems(boundary, expected)
    return _stage(
        "network_boundary",
        not problems,
        True,
        "M6.1 does not add dashboard writes or network exposure" if not problems else "; ".join(problems),
        details={"expected": expected, "actual": boundary},
    )


def _scheduler_boundary_stage(manifest: dict) -> dict:
    boundary = manifest.get("scheduler_boundary") if isinstance(manifest.get("scheduler_boundary"), dict) else {}
    expected = {
        "cron_replacement": False,
        "timer_installation": False,
        "service_enablement": False,
        "crontab_edit_allowed": False,
    }
    problems = _profile_problems(boundary, expected)
    return _stage(
        "scheduler_boundary",
        not problems,
        True,
        "M6.1 does not mutate cron, timers, services, or crontab" if not problems else "; ".join(problems),
        details={"expected": expected, "actual": boundary},
    )


def _optional_surface_stage(manifest: dict) -> dict:
    package = manifest.get("deployment_package") if isinstance(manifest.get("deployment_package"), dict) else {}
    inactive = package.get("inactive_optional_surfaces") if isinstance(package.get("inactive_optional_surfaces"), list) else []
    required = ("Signal", "voice", "camera", "sensors", "hardware", "dashboard write actions")
    missing = [name for name in required if name not in inactive]
    return _stage(
        "optional_surface_boundary",
        not missing,
        True,
        "M6.1 keeps Signal, voice, hardware, and dashboard writes inactive"
        if not missing
        else f"inactive optional surfaces missing: {', '.join(missing)}",
        details={"required_inactive": list(required), "present": inactive},
    )


def _run_report_stage(
    paths: CompanionPaths,
    runner: Callable[[CompanionPaths], dict],
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


def _semantic_shadow_authority_stage(paths: CompanionPaths) -> dict:
    audit = audit_semantic_shadow_authority(paths)
    return _stage(
        "semantic_shadow_authority",
        audit.get("ok") is True,
        True,
        audit.get("message", "semantic shadow authority audit completed"),
        details=audit,
    )


def _raw_output_storage_stage() -> dict:
    raw_enabled = should_store_raw_outputs()
    return _stage(
        "raw_output_storage",
        not raw_enabled,
        True,
        "raw model output storage defaults to hash-only"
        if not raw_enabled
        else f"raw model output storage is enabled; unset {STORE_RAW_OUTPUTS_ENV}",
        details={
            "raw_output_storage": "enabled" if raw_enabled else "hash_only",
            "env_var": STORE_RAW_OUTPUTS_ENV,
        },
    )


def _platform_identity_stage(identity: dict) -> dict:
    raspberry_pi = identity["raspberry_pi_detected"] is True
    return _stage(
        "platform_identity",
        True,
        False,
        "Raspberry Pi platform detected"
        if raspberry_pi
        else "Raspberry Pi platform was not detected; M6.2 is local-only",
        status="passed" if raspberry_pi else "warning",
        details=identity | {"required_for_m6_2": False},
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


def _manifest_section_summary(manifest: dict | None, name: str) -> dict:
    if not isinstance(manifest, dict):
        return {"loaded": False}
    section = manifest.get(name)
    if not isinstance(section, dict):
        return {"loaded": False}
    return {
        "loaded": True,
        "required_repository_path_count": len(section.get("required_repository_paths", []))
        if isinstance(section.get("required_repository_paths"), list)
        else 0,
        "runtime_artifact_count": len(section.get("runtime_artifacts_to_preserve", []))
        if isinstance(section.get("runtime_artifacts_to_preserve"), list)
        else 0,
        "exclude_count": len(section.get("exclude_from_transfer", []))
        if isinstance(section.get("exclude_from_transfer"), list)
        else 0,
    }


def _report_snapshot(report: dict | None, paths: CompanionPaths, path: Path) -> dict:
    snapshot = {"path": _relative(paths, path), "loaded": isinstance(report, dict)}
    if isinstance(report, dict):
        snapshot.update({
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
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


def _profile_problems(actual: dict, expected: dict) -> list[str]:
    problems = []
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            problems.append(f"{key} is {actual.get(key)!r}, expected {expected_value!r}")
    return problems


def _skipped_stages(reason: str, names: tuple[str, ...]) -> list[dict]:
    return [_stage(name, False, True, reason, status="skipped") for name in names]


def _stage(
    name: str,
    ok: bool,
    required: bool,
    message: str,
    *,
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


def _short(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else f"{text[:limit]}..."


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
