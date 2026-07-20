"""Local secret-file loading for provider credentials."""

from __future__ import annotations

import os
from pathlib import Path

from .paths import CompanionPaths

DEFAULT_SECRET_RELATIVE_PATH = Path(".secrets") / "deepseek.env"
EXTRA_SECRET_RELATIVE_PATHS = (Path(".secrets") / "feishu.env",)
SECRET_FILE_ENV = "COMPANION_SECRETS_FILE"
ALLOWED_SECRET_KEYS = {
    "DEEPSEEK_API_KEY",
    "COMPANION_LLM_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
}


def load_local_secrets(paths: CompanionPaths) -> dict:
    """Load supported provider secrets from local ignored env files.

    Existing environment variables win over file values. The returned mapping is
    only metadata about what was loaded, never the secret values themselves.
    """

    primary_file = _secret_file_path(paths)
    loaded: list[str] = []
    checked_files = [primary_file]
    checked_files.extend(paths.home / relative for relative in EXTRA_SECRET_RELATIVE_PATHS)
    seen: set[Path] = set()
    any_exists = False
    for secret_file in checked_files:
        if secret_file in seen:
            continue
        seen.add(secret_file)
        if not secret_file.exists():
            continue
        any_exists = True
        for line in secret_file.read_text().splitlines():
            key, value = _parse_env_line(line)
            if not key or key not in ALLOWED_SECRET_KEYS:
                continue
            if key not in os.environ and value:
                os.environ[key] = value
                loaded.append(key)
    return {
        "path": str(primary_file),
        "loaded": loaded,
        "exists": any_exists,
    }


def _secret_file_path(paths: CompanionPaths) -> Path:
    configured = os.environ.get(SECRET_FILE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return paths.home / DEFAULT_SECRET_RELATIVE_PATH


def _parse_env_line(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value
