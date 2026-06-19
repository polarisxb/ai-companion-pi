"""M6.5 non-destructive Pi backup and recovery drill."""

from __future__ import annotations

import hashlib
import json
import platform
import shlex
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from .m6_observation import READY_RECOMMENDATION as M6_OBSERVATION_READY
from .paths import CompanionPaths


READY_RECOMMENDATION = "rollback_recovery_ready"
EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"

PlatformIdentityProvider = Callable[[], dict]


def run_m6_recovery_drill(
    paths: CompanionPaths,
    *,
    backup_root: str | Path,
    restore_sandbox: str | Path | None = None,
    observation_report_path: str | Path | None = None,
    require_raspberry_pi: bool = True,
    platform_identity_provider: PlatformIdentityProvider | None = None,
) -> dict:
    """Run the non-destructive M6.5 backup and restore-sandbox drill."""

    backup_root_path = Path(backup_root).expanduser().resolve()
    observation_file = (
        Path(observation_report_path).expanduser().resolve()
        if observation_report_path
        else paths.life_loop_dir / "m6_pi_observation_report.json"
    )
    observation_report, observation_stage = _observation_report_stage(paths, observation_file)
    identity = (
        platform_identity_provider() if platform_identity_provider else _platform_identity()
    )
    stages = [
        observation_stage,
        _platform_identity_stage(identity, require_raspberry_pi=require_raspberry_pi),
        _backup_root_stage(paths, backup_root_path),
        _raw_output_boundary_stage(paths),
    ]

    backup_summary: dict = {"requested": True, "executed": False}
    restore_summary: dict = {"requested": True, "executed": False}
    secret_summary: dict = {"metadata_only": True, "secret_values_copied": False}

    if not _stop_reasons(stages):
        backup_dir = backup_root_path / datetime.now().strftime("%Y%m%d-%H%M%S")
        sandbox_path = (
            Path(restore_sandbox).expanduser().resolve()
            if restore_sandbox
            else backup_dir / "restore-sandbox"
        )
        backup_stage, manifest_entries, backup_summary = _create_backup_stage(paths, backup_dir)
        stages.append(backup_stage)
        if backup_stage["ok"]:
            secret_stage, secret_summary = _secret_metadata_stage(paths, backup_dir)
            stages.append(secret_stage)
            restore_stage, restore_summary = _restore_sandbox_stage(
                backup_dir,
                sandbox_path,
                manifest_entries,
            )
            stages.append(restore_stage)

    stop_reasons = _stop_reasons(stages)
    recommendation = _recommendation(stop_reasons, identity, require_raspberry_pi=require_raspberry_pi)
    return {
        "ok": recommendation == READY_RECOMMENDATION,
        "milestone": "M6.5",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "pi_presence": {
            "required": require_raspberry_pi,
            "detected": identity.get("raspberry_pi_detected") is True,
            "evidence": [identity.get("device_tree_model")]
            if identity.get("device_tree_model")
            else [],
            "claim": (
                "real_pi_recovery_drill"
                if identity.get("raspberry_pi_detected") is True
                else "pi_required"
            ),
        },
        "profile": {
            "name": "m6-recovery-drill",
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
            "live_restore_requested": False,
            "live_restore_executed": False,
        },
        "source_reports": {
            "m6_pi_observation": _report_snapshot(observation_report, paths, observation_file),
        },
        "backup": backup_summary,
        "restore_sandbox": restore_summary,
        "secret_boundary": secret_summary,
        "field_pilot": {
            "manual_wake": {"requested": False, "next_stage": "M6.4"},
            "observation": {"requested": False, "next_stage": "M6.5"},
            "recovery": {
                "requested": True,
                "executed": recommendation == READY_RECOMMENDATION,
                "next_stage": "M6.6" if recommendation == READY_RECOMMENDATION else "M6.5",
            },
            "scheduler_readiness": {"mutated": False, "readiness_stage": "M6.6"},
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "pending_reasons": [],
        "next_commands": {
            "m6_recovery_drill": _shell_command([
                "python3",
                "scripts/run_m6_recovery_drill.py",
                "--companion-home",
                str(paths.home),
                "--backup-root",
                str(backup_root_path),
            ]),
            "m6_scheduler_readiness_later": "requires rollback_recovery_ready",
        },
    }


def _observation_report_stage(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage("m6_observation_report", False, True, f"M6.4 report is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage("m6_observation_report", False, True, f"M6.4 report is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage("m6_observation_report", False, True, f"M6.4 report could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage("m6_observation_report", False, True, "M6.4 report must be a JSON object")

    problems = []
    if payload.get("ok") is not True:
        problems.append("M6.4 report ok is not true")
    if payload.get("milestone") != "M6.4":
        problems.append("M6.4 report milestone is not M6.4")
    if payload.get("recommendation") != M6_OBSERVATION_READY:
        problems.append(f"M6.4 recommendation is not {M6_OBSERVATION_READY}")
    if payload.get("stop_reasons"):
        problems.append("M6.4 report has stop_reasons")

    return payload, _stage(
        "m6_observation_report",
        not problems,
        True,
        "M6.4 observation report is stable" if not problems else "; ".join(problems),
        details=_report_snapshot(payload, paths, path),
    )


def _platform_identity_stage(identity: dict, *, require_raspberry_pi: bool) -> dict:
    raspberry_pi = identity.get("raspberry_pi_detected") is True
    ok = raspberry_pi or not require_raspberry_pi
    return _stage(
        "platform_identity",
        ok,
        require_raspberry_pi,
        "Raspberry Pi platform detected"
        if raspberry_pi
        else "Raspberry Pi platform was not detected; M6.5 requires the real Pi",
        details=identity,
    )


def _backup_root_stage(paths: CompanionPaths, backup_root: Path) -> dict:
    problems = []
    if _is_protected_live_path(paths, backup_root):
        problems.append("backup root is inside a protected live runtime path")
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        probe = backup_root / ".m6-write-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        problems.append(f"backup root is not writable: {exc}")
    return _stage(
        "backup_root",
        not problems,
        True,
        "backup root is writable and outside protected live runtime paths"
        if not problems
        else "; ".join(problems),
        details={"path": str(backup_root)},
    )


def _raw_output_boundary_stage(paths: CompanionPaths) -> dict:
    raw_files = []
    if paths.model_outputs_dir.exists():
        raw_files = [
            _relative(paths, path)
            for path in paths.model_outputs_dir.rglob("*")
            if path.is_file()
        ]
    return _stage(
        "raw_output_boundary",
        not raw_files,
        True,
        "no raw model output files are present"
        if not raw_files
        else "raw model output files are present and must not enter M6.5 backup",
        details={"raw_output_files": raw_files},
    )


def _create_backup_stage(paths: CompanionPaths, backup_dir: Path) -> tuple[dict, list[dict], dict]:
    runtime_dir = backup_dir / "runtime"
    manifest_path = backup_dir / "manifest.json"
    try:
        runtime_dir.mkdir(parents=True, exist_ok=False)
        entries = []
        for source in _backup_sources(paths):
            relative = source.relative_to(paths.home)
            target = runtime_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append(_manifest_entry(paths, source))
        manifest = {
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "companion_home": str(paths.home),
            "artifact_count": len(entries),
            "artifacts": entries,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        summary = {
            "requested": True,
            "executed": True,
            "path": str(backup_dir),
            "runtime_dir": str(runtime_dir),
            "manifest": str(manifest_path),
            "artifact_count": len(entries),
            "byte_count": sum(entry["size"] for entry in entries),
        }
        return (
            _stage("backup_create", True, True, "backup package created", details=summary),
            entries,
            summary,
        )
    except OSError as exc:
        summary = {"requested": True, "executed": False, "path": str(backup_dir), "error": str(exc)}
        return (
            _stage("backup_create", False, True, f"backup package could not be created: {exc}", details=summary),
            [],
            summary,
        )


def _secret_metadata_stage(paths: CompanionPaths, backup_dir: Path) -> tuple[dict, dict]:
    secrets_dir = paths.home / ".secrets"
    entries = []
    try:
        if secrets_dir.exists():
            for path in sorted(secrets_dir.glob("*.env")):
                if not path.is_file():
                    continue
                keys = []
                try:
                    for line in path.read_text(errors="ignore").splitlines():
                        stripped = line.strip()
                        if not stripped or stripped.startswith("#") or "=" not in stripped:
                            continue
                        keys.append(stripped.split("=", 1)[0])
                except OSError:
                    keys = []
                stat_result = path.stat()
                entries.append({
                    "path": _relative(paths, path),
                    "present": True,
                    "mode": stat.filemode(stat_result.st_mode),
                    "size": stat_result.st_size,
                    "key_names": sorted(set(keys)),
                })
        metadata = {
            "metadata_only": True,
            "secret_values_copied": False,
            "entries": entries,
        }
        metadata_path = backup_dir / "secret_metadata.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        copied_values = _contains_secret_values(paths, backup_dir)
        metadata["secret_values_copied"] = copied_values
        return (
            _stage(
                "secret_boundary",
                not copied_values,
                True,
                "secret metadata recorded without values"
                if not copied_values
                else "secret value appeared in backup output",
                details=metadata,
            ),
            metadata,
        )
    except OSError as exc:
        metadata = {"metadata_only": True, "secret_values_copied": False, "error": str(exc)}
        return (
            _stage("secret_boundary", False, True, f"secret metadata could not be written: {exc}", details=metadata),
            metadata,
        )


def _restore_sandbox_stage(backup_dir: Path, sandbox_path: Path, manifest_entries: list[dict]) -> tuple[dict, dict]:
    try:
        if sandbox_path.exists():
            raise OSError(f"restore sandbox already exists: {sandbox_path}")
        restored_runtime = sandbox_path / "runtime"
        shutil.copytree(backup_dir / "runtime", restored_runtime)
        mismatches = []
        invalid_json = []
        for entry in manifest_entries:
            restored = restored_runtime / entry["path"]
            if not restored.exists():
                mismatches.append({"path": entry["path"], "reason": "missing"})
                continue
            digest = _sha256(restored)
            if digest != entry["sha256"]:
                mismatches.append({"path": entry["path"], "reason": "checksum_mismatch"})
            if _should_validate_json(entry["path"]):
                try:
                    json.loads(restored.read_text())
                except json.JSONDecodeError as exc:
                    invalid_json.append({"path": entry["path"], "error": exc.msg})
                except OSError as exc:
                    invalid_json.append({"path": entry["path"], "error": str(exc)})
        summary = {
            "requested": True,
            "executed": True,
            "path": str(sandbox_path),
            "verified_artifact_count": len(manifest_entries),
            "checksum_mismatch_count": len(mismatches),
            "invalid_json_count": len(invalid_json),
            "mismatches": mismatches,
            "invalid_json": invalid_json,
        }
        ok = not mismatches and not invalid_json
        return (
            _stage(
                "restore_sandbox_verify",
                ok,
                True,
                "restore sandbox verified" if ok else "restore sandbox verification failed",
                details=summary,
            ),
            summary,
        )
    except OSError as exc:
        summary = {"requested": True, "executed": False, "path": str(sandbox_path), "error": str(exc)}
        return (
            _stage("restore_sandbox_verify", False, True, f"restore sandbox could not be verified: {exc}", details=summary),
            summary,
        )


def _backup_sources(paths: CompanionPaths) -> list[Path]:
    sources = []
    if paths.life_loop_dir.exists():
        sources.extend(sorted(path for path in paths.life_loop_dir.glob("*.json") if path.is_file()))
    for path in (
        paths.wake_events_file,
        paths.memory_store,
        paths.requests_file,
        paths.status_file,
        paths.context_file("who_is_companion.txt"),
        paths.context_file("who_is_human.txt"),
        paths.context_file("now.txt"),
    ):
        if path.exists() and path.is_file():
            sources.append(path)
    if paths.journals_dir.exists():
        sources.extend(sorted(path for path in paths.journals_dir.rglob("*") if path.is_file()))

    filtered = []
    for path in sources:
        relative = path.relative_to(paths.home)
        if _is_excluded_relative(relative):
            continue
        filtered.append(path)
    return filtered


def _manifest_entry(paths: CompanionPaths, path: Path) -> dict:
    return {
        "path": str(path.relative_to(paths.home)),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _is_excluded_relative(relative: Path) -> bool:
    text = str(relative)
    parts = set(relative.parts)
    if text.endswith(".lock") or text.endswith(".log"):
        return True
    if "model_outputs" in parts:
        return True
    if ".secrets" in parts or ".venv" in parts or ".omx" in parts or ".codex" in parts or ".agents" in parts:
        return True
    if relative.name == ".env" or relative.name.startswith(".env."):
        return True
    return False


def _is_protected_live_path(paths: CompanionPaths, candidate: Path) -> bool:
    protected = (
        paths.life_loop_dir,
        paths.journals_dir,
        paths.memory_dir,
        paths.requests_dir,
        paths.window_dir,
        paths.context_dir,
        paths.home / ".secrets",
    )
    return any(_is_relative_to(candidate, path) for path in protected)


def _is_relative_to(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _should_validate_json(relative_path: str) -> bool:
    return relative_path.endswith(".json")


def _contains_secret_values(paths: CompanionPaths, backup_dir: Path) -> bool:
    values = []
    secrets_dir = paths.home / ".secrets"
    if not secrets_dir.exists():
        return False
    for path in secrets_dir.glob("*.env"):
        try:
            for line in path.read_text(errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                value = stripped.split("=", 1)[1].strip().strip("\"'")
                if value:
                    values.append(value)
        except OSError:
            continue
    if not values:
        return False
    for path in backup_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if any(value in text for value in values):
            return True
    return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
