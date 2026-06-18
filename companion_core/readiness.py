"""Runtime readiness checks for Pi and local companion trials."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Callable

from .llm import DEEPSEEK_API_KEY_ENV, SUPPORTED_LLM_PROVIDERS
from .paths import CompanionPaths
from .provider_check import check_llm_provider
from .secrets import load_local_secrets

ImportProbe = Callable[[str], bool]
ProviderChecker = Callable[..., dict]
SemanticModuleLoader = Callable[[Path], ModuleType]

CORE_IMPORTS = ("flask", "markdown")
OPTIONAL_IMPORTS = ("mcp",)
SEMANTIC_IMPORTS = ("numpy", "sentence_transformers")
CONTEXT_FILES = ("who_is_companion.txt", "who_is_human.txt", "now.txt")
PLACEHOLDER_MARKERS = (
    "YOUR_",
    "TODO",
    "TBD",
    "/path/to",
)


def check_runtime_readiness(
    paths: CompanionPaths,
    *,
    provider: str = "deepseek",
    memory_mode: str = "dual",
    claude_bin: str = "claude",
    timeout_seconds: int = 10,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
    run_provider_check: bool = True,
    import_probe: ImportProbe | None = None,
    provider_checker: ProviderChecker = check_llm_provider,
    semantic_module_loader: SemanticModuleLoader | None = None,
) -> dict:
    """Return a JSON-serializable readiness report.

    ``ok`` is intentionally strict: it is true only when no check failed.
    Warnings identify recoverable or non-fatal trial issues.
    """

    checks: list[dict] = []
    import_probe = import_probe or _module_is_available
    semantic_module_loader = semantic_module_loader or _load_semantic_module
    secret_load = load_local_secrets(paths)

    checks.append(_check_python_runtime())
    checks.append(_check_local_secrets(secret_load))
    checks.extend(_check_imports(CORE_IMPORTS, import_probe, missing_status="failed"))
    checks.extend(_check_imports(OPTIONAL_IMPORTS, import_probe, missing_status="warning"))
    checks.extend(_check_semantic_imports(memory_mode, import_probe))
    checks.extend(_check_deepseek_api_key(provider, api_key_env))
    checks.extend(_check_context_files(paths))
    checks.extend(_check_json_file(paths.memory_store, "memory_store", missing_status="warning"))
    checks.extend(_check_json_file(paths.requests_file, "requests_file", missing_status="warning"))
    checks.extend(_check_writable_runtime_paths(paths))
    checks.extend(_check_semantic_module(paths, memory_mode, semantic_module_loader))
    checks.append(_check_memory_mode(memory_mode, checks))
    if run_provider_check:
        checks.extend(_flatten_provider_check(
            provider_checker(
                provider,
                claude_bin=claude_bin,
                timeout_seconds=timeout_seconds,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            )
        ))
    else:
        checks.append(_check("provider.preflight", "warning", "provider preflight was skipped"))

    return {
        "ok": all(check["status"] != "failed" for check in checks),
        "companion_home": str(paths.home),
        "provider": provider,
        "memory_mode": memory_mode,
        "checks": checks,
    }


def _check_python_runtime() -> dict:
    version = sys.version_info
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 10):
        return _check("python.version", "failed", f"Python {version_text} is too old; use Python 3.10+")

    venv_active = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or bool(os.environ.get("VIRTUAL_ENV"))
    if not venv_active:
        return _check(
            "python.runtime",
            "warning",
            f"Python {version_text} is supported, but no virtualenv is active ({sys.executable})",
        )
    return _check("python.runtime", "passed", f"Python {version_text} in virtualenv ({sys.executable})")


def _check_local_secrets(secret_load: dict) -> dict:
    path = secret_load.get("path")
    if not secret_load.get("exists"):
        return _check("local_secrets", "warning", f"local secrets file is not present: {path}")
    loaded = secret_load.get("loaded", [])
    if loaded:
        return _check("local_secrets", "passed", f"loaded secret keys from {path}: {', '.join(loaded)}")
    return _check("local_secrets", "warning", f"local secrets file is present but no new supported keys were loaded: {path}")


def _check_imports(names: tuple[str, ...], import_probe: ImportProbe, *, missing_status: str) -> list[dict]:
    checks = []
    for name in names:
        if import_probe(name):
            checks.append(_check(f"import.{name}", "passed", f"{name} is importable"))
        else:
            checks.append(_check(f"import.{name}", missing_status, f"{name} is not importable"))
    return checks


def _check_semantic_imports(memory_mode: str, import_probe: ImportProbe) -> list[dict]:
    missing_status = "failed" if memory_mode == "dual" else "warning"
    checks = _check_imports(SEMANTIC_IMPORTS, import_probe, missing_status=missing_status)
    if memory_mode != "dual":
        for check in checks:
            if check["status"] == "warning":
                check["message"] += "; needed for semantic-first dual memory, not JSON mode"
    return checks


def _check_deepseek_api_key(provider: str, api_key_env: str) -> list[dict]:
    if os.environ.get(DEEPSEEK_API_KEY_ENV):
        return [_check("deepseek.api_key", "passed", f"API key loaded from {DEEPSEEK_API_KEY_ENV}")]
    if provider == "deepseek" and api_key_env != DEEPSEEK_API_KEY_ENV and os.environ.get(api_key_env):
        return [_check("deepseek.api_key", "passed", f"API key loaded from {api_key_env}")]
    status = "failed" if provider == "deepseek" else "warning"
    return [_check("deepseek.api_key", status, f"{DEEPSEEK_API_KEY_ENV} is not set")]


def _check_context_files(paths: CompanionPaths) -> list[dict]:
    checks = []
    for filename in CONTEXT_FILES:
        path = paths.context_file(filename)
        template_path = paths.context_file(filename.replace(".txt", ".template.txt"))
        checks.append(_check_context_file(path, template_path))
    return checks


def _check_context_file(path: Path, template_path: Path) -> dict:
    name = f"context.{path.name}"
    try:
        content = path.read_text().strip()
    except FileNotFoundError:
        return _check(name, "failed", f"{path} is missing")
    except OSError as exc:
        return _check(name, "failed", f"{path} cannot be read: {exc}")
    if not content:
        return _check(name, "failed", f"{path} is empty")

    try:
        template = template_path.read_text().strip()
    except FileNotFoundError:
        template = ""
    except OSError:
        template = ""
    if template and content == template:
        return _check(name, "failed", f"{path} still matches {template_path.name}")
    marker = _first_placeholder_marker(content)
    if marker:
        return _check(name, "failed", f"{path} still contains placeholder marker {marker!r}")
    return _check(name, "passed", f"{path} is present and customized")


def _first_placeholder_marker(content: str) -> str | None:
    lowered = content.lower()
    for marker in PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return marker
    return None


def _check_json_file(path: Path, name: str, *, missing_status: str) -> list[dict]:
    if not path.exists():
        return [_check(name, missing_status, f"{path} does not exist yet; first wake can create it")]
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [_check(name, "failed", f"{path} is invalid JSON: {exc.msg}")]
    except OSError as exc:
        return [_check(name, "failed", f"{path} cannot be read: {exc}")]
    if not isinstance(data, list):
        return [_check(name, "failed", f"{path} must contain a JSON list")]
    return [_check(name, "passed", f"{path} is valid JSON")]


def _check_writable_runtime_paths(paths: CompanionPaths) -> list[dict]:
    targets = (
        paths.life_loop_dir,
        paths.journals_dir,
        paths.requests_dir,
        paths.window_dir,
        paths.window_dir / "content",
        paths.memory_dir,
    )
    return [_check_writable_dir(path) for path in targets]


def _check_writable_dir(path: Path) -> dict:
    name = f"writable.{path.name}" if path.name else f"writable.{path}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe_path = path / f".readiness_{uuid.uuid4().hex}.tmp"
        probe_path.write_text("ok")
        probe_path.unlink()
    except OSError as exc:
        return _check(name, "failed", f"{path} is not writable: {exc}")
    return _check(name, "passed", f"{path} is writable")


def _check_semantic_module(
    paths: CompanionPaths,
    memory_mode: str,
    semantic_module_loader: SemanticModuleLoader,
) -> list[dict]:
    semantic_path = _semantic_module_path(paths)
    status_if_missing = "failed" if memory_mode == "dual" else "warning"
    if not semantic_path.exists():
        return [_check(
            "semantic_memory_module",
            status_if_missing,
            f"semantic memory module is missing: {semantic_path}",
        )]
    try:
        module = semantic_module_loader(semantic_path)
    except Exception as exc:
        return [_check(
            "semantic_memory_module",
            status_if_missing,
            f"semantic memory module cannot import: {type(exc).__name__}: {exc}",
        )]
    if not hasattr(module, "SemanticMemoryStore"):
        return [_check("semantic_memory_module", "failed", "SemanticMemoryStore class is missing")]
    return [_check("semantic_memory_module", "passed", f"semantic memory module imports: {semantic_path}")]


def _semantic_module_path(paths: CompanionPaths) -> Path:
    companion_home_module = paths.memory_dir / "semantic_memory.py"
    if companion_home_module.exists():
        return companion_home_module
    return Path(__file__).resolve().parents[1] / "memory-server" / "semantic_memory.py"


def _load_semantic_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("companion_semantic_memory_readiness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load semantic memory module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_memory_mode(memory_mode: str, checks: list[dict]) -> dict:
    if memory_mode not in {"json", "dual"}:
        return _check("memory_mode", "failed", f"unsupported memory mode: {memory_mode}")
    if memory_mode == "json":
        return _check("memory_mode", "passed", "JSON memory mode selected")

    blockers = [
        check["message"]
        for check in checks
        if check["status"] == "failed"
        and (
            check["name"].startswith("import.numpy")
            or check["name"].startswith("import.sentence_transformers")
            or check["name"] == "semantic_memory_module"
        )
    ]
    if blockers:
        return _check(
            "memory_mode",
            "failed",
            "dual memory selected, but semantic-first is not ready; JSON fallback would be used. "
            + " | ".join(blockers),
        )
    return _check("memory_mode", "passed", "dual memory selected and semantic-first dependencies are importable")


def _flatten_provider_check(result: dict) -> list[dict]:
    checks = []
    provider = result.get("provider", "unknown")
    for check in result.get("checks", []):
        checks.append(_check(
            f"provider.{provider}.{check.get('name', 'check')}",
            check.get("status", "failed"),
            check.get("message", "provider check did not include a message"),
        ))
    return checks


def _module_is_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _check(name: str, status: str, message: str) -> dict:
    return {
        "name": name,
        "status": status,
        "message": message,
    }
