"""M4 deploy-readiness checks for the Raspberry Pi runtime surface."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import sys
import uuid
from pathlib import Path
from typing import Callable

from .llm import DEEPSEEK_API_KEY_ENV
from .output_archive import STORE_RAW_OUTPUTS_ENV, should_store_raw_outputs
from .paths import CompanionPaths
from .release_gate import audit_semantic_shadow_authority
from .secrets import load_local_secrets

ImportProbe = Callable[[str], bool]

EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
FROZEN_RECOMMENDATION = "m3_frozen_ready_for_m4"
CONTEXT_FILES = ("who_is_companion.txt", "who_is_human.txt", "now.txt")
PLACEHOLDER_MARKERS = ("YOUR_", "TODO", "TBD", "/path/to")
JSON_MODE_IMPORTS = (
    "json",
    "companion_core.lifecycle",
    "companion_core.llm",
    "companion_core.memory",
    "companion_core.parser",
    "flask",
    "markdown",
)


def run_m4_deploy_check(
    paths: CompanionPaths,
    *,
    final_freeze_report_path: str | Path | None = None,
    import_probe: ImportProbe | None = None,
) -> dict:
    """Return the M4.2 deploy-readiness report without running a wake."""

    import_probe = import_probe or _module_is_available
    report_path = (
        Path(final_freeze_report_path).expanduser().resolve()
        if final_freeze_report_path
        else paths.life_loop_dir / "m3_final_freeze_report.json"
    )
    final_freeze, load_stage = _load_final_freeze_report(report_path)
    stages = [load_stage]

    if isinstance(final_freeze, dict):
        stages.append(_final_freeze_result_stage(final_freeze))
        stages.append(_frozen_contract_stage(final_freeze))
    else:
        stages.append(_stage(
            "final_freeze_result",
            False,
            required=True,
            status="skipped",
            message="final-freeze report did not load",
        ))
        stages.append(_stage(
            "frozen_deployment_contract",
            False,
            required=True,
            status="skipped",
            message="final-freeze report did not load",
        ))

    secret_load = load_local_secrets(paths)
    stages.extend([
        _semantic_shadow_authority_stage(paths),
        _python_runtime_stage(),
        _json_mode_imports_stage(import_probe),
        _deepseek_api_key_stage(secret_load),
        _context_files_stage(paths),
        _writable_runtime_paths_stage(paths),
        _raw_output_storage_stage(),
        _runtime_files_stage(paths),
        _dashboard_reachability_stage(),
    ])

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    return {
        "ok": not stop_reasons,
        "milestone": "M4.2",
        "recommendation": "ready_for_manual_wake" if not stop_reasons else "inspect",
        "companion_home": str(paths.home),
        "final_freeze_report": _relative(paths, report_path),
        "profile": {
            "name": "pi-deploy-check",
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "provider_preflight_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_reachability_required": False,
        },
        "frozen_commands": (
            final_freeze.get("frozen_commands", {})
            if isinstance(final_freeze, dict) and isinstance(final_freeze.get("frozen_commands"), dict)
            else {}
        ),
        "next_commands": {
            "deploy_check": _shell_command([
                "python3",
                "scripts/run_m4_deploy_check.py",
                "--companion-home",
                str(paths.home),
            ]),
            "manual_wake_after_m4_3_wrapper": _shell_command([
                "python3",
                "scripts/run_m4_wake_trial.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
    }


def _load_final_freeze_report(path: Path) -> tuple[dict | None, dict]:
    if not path.exists():
        return None, _stage(
            "final_freeze_report",
            False,
            required=True,
            message=f"final-freeze report is missing: {path}",
        )
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return None, _stage(
            "final_freeze_report",
            False,
            required=True,
            message=f"final-freeze report is invalid JSON: {exc.msg}",
        )
    except OSError as exc:
        return None, _stage(
            "final_freeze_report",
            False,
            required=True,
            message=f"final-freeze report could not be read: {exc}",
        )
    if not isinstance(payload, dict):
        return None, _stage(
            "final_freeze_report",
            False,
            required=True,
            message="final-freeze report must be a JSON object",
        )
    return payload, _stage(
        "final_freeze_report",
        True,
        required=True,
        message="final-freeze report loaded",
        details={
            "path": str(path),
            "milestone": payload.get("milestone"),
            "recommendation": payload.get("recommendation"),
            "saved_at": payload.get("saved_at"),
        },
    )


def _final_freeze_result_stage(report: dict) -> dict:
    problems = []
    if report.get("ok") is not True:
        problems.append("final-freeze ok is not true")
    if report.get("milestone") != "M3.26":
        problems.append("final-freeze milestone is not M3.26")
    if report.get("recommendation") != FROZEN_RECOMMENDATION:
        problems.append(f"final-freeze recommendation is not {FROZEN_RECOMMENDATION}")
    return _stage(
        "final_freeze_result",
        not problems,
        required=True,
        message="M3 final freeze is ready for M4" if not problems else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _frozen_contract_stage(report: dict) -> dict:
    contract = report.get("deployment_contract")
    if not isinstance(contract, dict):
        return _stage(
            "frozen_deployment_contract",
            False,
            required=True,
            message="final-freeze deployment_contract is missing",
        )

    expected = {
        "provider": EXPECTED_PROVIDER,
        "memory_mode": EXPECTED_MEMORY_MODE,
        "cron_replacement": False,
        "semantic_shadow_authoritative": False,
        "raw_output_storage": "hash_only",
    }
    problems = []
    for key, expected_value in expected.items():
        if contract.get(key) != expected_value:
            problems.append(f"{key} is {contract.get(key)!r}, expected {expected_value!r}")
    if contract.get("real_wake_in_freeze") is not False:
        problems.append("real_wake_in_freeze must be false")

    return _stage(
        "frozen_deployment_contract",
        not problems,
        required=True,
        message="M3 frozen deployment contract is intact" if not problems else "; ".join(problems),
        details={
            "expected": expected | {"real_wake_in_freeze": False},
            "actual": contract,
        },
    )


def _python_runtime_stage() -> dict:
    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    executable = Path(sys.executable)
    problems = []
    if version < (3, 10):
        problems.append(f"Python {version_text} is too old; use Python 3.10+")
    if not executable.exists():
        problems.append(f"Python executable does not exist: {sys.executable}")
    venv_active = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or bool(os.environ.get("VIRTUAL_ENV"))
    if problems:
        return _stage(
            "python_runtime",
            False,
            required=True,
            message="; ".join(problems),
            details={
                "version": version_text,
                "executable": sys.executable,
                "virtualenv_active": venv_active,
            },
        )
    return _stage(
        "python_runtime",
        True,
        required=True,
        status="passed" if venv_active else "warning",
        message=(
            f"Python {version_text} runtime is usable"
            if venv_active
            else f"Python {version_text} is usable, but no virtualenv is active"
        ),
        details={
            "version": version_text,
            "executable": sys.executable,
            "virtualenv_active": venv_active,
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


def _json_mode_imports_stage(import_probe: ImportProbe) -> dict:
    checks = []
    for name in JSON_MODE_IMPORTS:
        available = import_probe(name)
        checks.append({
            "name": name,
            "ok": available,
            "status": "passed" if available else "failed",
        })
    missing = [check["name"] for check in checks if not check["ok"]]
    return _stage(
        "json_mode_imports",
        not missing,
        required=True,
        message="JSON-mode runtime imports are available" if not missing else f"missing imports: {', '.join(missing)}",
        details={"imports": checks},
    )


def _deepseek_api_key_stage(secret_load: dict) -> dict:
    accepted_env_names = ("COMPANION_LLM_API_KEY", DEEPSEEK_API_KEY_ENV)
    present_env_names = [name for name in accepted_env_names if os.environ.get(name)]
    local_secrets = {
        "exists": bool(secret_load.get("exists")),
        "path": secret_load.get("path"),
        "loaded": list(secret_load.get("loaded", [])),
    }
    return _stage(
        "deepseek_api_key",
        bool(present_env_names),
        required=True,
        message=(
            "DeepSeek API key metadata is present"
            if present_env_names
            else f"DeepSeek API key is missing; set {DEEPSEEK_API_KEY_ENV}"
        ),
        details={
            "accepted_env_names": list(accepted_env_names),
            "present_env_names": present_env_names,
            "local_secrets": local_secrets,
            "secret_values": "redacted",
        },
    )


def _context_files_stage(paths: CompanionPaths) -> dict:
    checks = [_context_file_check(paths, filename) for filename in CONTEXT_FILES]
    failures = [check["message"] for check in checks if not check["ok"]]
    return _stage(
        "context_files",
        not failures,
        required=True,
        message="context files are present and customized" if not failures else "; ".join(failures),
        details={"files": checks},
    )


def _context_file_check(paths: CompanionPaths, filename: str) -> dict:
    path = paths.context_file(filename)
    template_path = paths.context_file(filename.replace(".txt", ".template.txt"))
    rel_path = _relative(paths, path)
    try:
        content = path.read_text().strip()
    except FileNotFoundError:
        return _check(rel_path, False, f"{rel_path} is missing")
    except OSError as exc:
        return _check(rel_path, False, f"{rel_path} cannot be read: {exc}")
    if not content:
        return _check(rel_path, False, f"{rel_path} is empty")

    try:
        template = template_path.read_text().strip()
    except OSError:
        template = ""
    if template and content == template:
        return _check(rel_path, False, f"{rel_path} still matches {template_path.name}")
    marker = _first_placeholder_marker(content)
    if marker:
        return _check(rel_path, False, f"{rel_path} still contains placeholder marker {marker!r}")
    return _check(rel_path, True, f"{rel_path} is present and customized")


def _writable_runtime_paths_stage(paths: CompanionPaths) -> dict:
    targets = (
        paths.life_loop_dir,
        paths.journals_dir,
        paths.requests_dir,
        paths.memory_dir,
        paths.window_dir,
        paths.window_dir / "content",
    )
    checks = [_writable_dir_check(paths, path) for path in targets]
    failures = [check["message"] for check in checks if not check["ok"]]
    return _stage(
        "writable_runtime_paths",
        not failures,
        required=True,
        message="runtime directories are writable" if not failures else "; ".join(failures),
        details={"paths": checks},
    )


def _writable_dir_check(paths: CompanionPaths, path: Path) -> dict:
    rel_path = _relative(paths, path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_path = path / f".m4_deploy_{uuid.uuid4().hex}.tmp"
        probe_path.write_text("ok")
        probe_path.unlink()
    except OSError as exc:
        return _check(rel_path, False, f"{rel_path} is not writable: {exc}")
    return _check(rel_path, True, f"{rel_path} is writable")


def _raw_output_storage_stage() -> dict:
    raw_enabled = should_store_raw_outputs()
    return _stage(
        "raw_output_storage",
        not raw_enabled,
        required=True,
        message=(
            "raw model output storage defaults to hash-only"
            if not raw_enabled
            else f"raw model output storage is enabled; unset {STORE_RAW_OUTPUTS_ENV}"
        ),
        details={
            "raw_output_storage": "enabled" if raw_enabled else "hash_only",
            "env_var": STORE_RAW_OUTPUTS_ENV,
        },
    )


def _runtime_files_stage(paths: CompanionPaths) -> dict:
    files = (
        paths.home / "scripts" / "run_wake_cycle.py",
        paths.home / "scripts" / "start_window.sh",
        paths.home / "scripts" / "start_memory_http.sh",
        paths.home / "window" / "window.py",
        paths.home / "memory-server" / "memory_server_http.py",
    )
    checks = [_file_exists_check(paths, path) for path in files]
    failures = [check["message"] for check in checks if not check["ok"]]
    return _stage(
        "runtime_files",
        not failures,
        required=True,
        message="dashboard/window runtime files are present" if not failures else "; ".join(failures),
        details={"files": checks},
    )


def _file_exists_check(paths: CompanionPaths, path: Path) -> dict:
    rel_path = _relative(paths, path)
    if not path.exists():
        return _check(rel_path, False, f"{rel_path} is missing")
    if not path.is_file():
        return _check(rel_path, False, f"{rel_path} is not a file")
    return _check(rel_path, True, f"{rel_path} is present")


def _dashboard_reachability_stage() -> dict:
    return _stage(
        "dashboard_reachability",
        True,
        required=False,
        status="skipped",
        message="dashboard/window reachability is advisory and not required for M4.2",
        details={
            "checked": False,
            "required_for_m4_2": False,
        },
    )


def _first_placeholder_marker(content: str) -> str | None:
    lowered = content.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def _module_is_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _check(name: str, ok: bool, message: str) -> dict:
    return {
        "name": name,
        "ok": ok,
        "status": "passed" if ok else "failed",
        "message": message,
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


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
