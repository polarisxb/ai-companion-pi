"""Local secret-file loading for provider credentials."""

from __future__ import annotations

import os
from pathlib import Path

from .paths import CompanionPaths

DEFAULT_SECRET_RELATIVE_PATH = Path(".secrets") / "deepseek.env"
SECRET_FILE_ENV = "COMPANION_SECRETS_FILE"
ALLOWED_SECRET_KEYS = {
    "DEEPSEEK_API_KEY",
    "COMPANION_LLM_API_KEY",
}


def load_local_secrets(paths: CompanionPaths) -> dict:
    """Load supported provider secrets from a local ignored env file.

    Existing environment variables win over file values. The returned mapping is
    only metadata about what was loaded, never the secret values themselves.
    """

    secret_file = _secret_file_path(paths)
    loaded = []
    if not secret_file.exists():
        return {
            "path": str(secret_file),
            "loaded": loaded,
            "exists": False,
        }

    for line in secret_file.read_text().splitlines():
        key, value = _parse_env_line(line)
        if not key or key not in ALLOWED_SECRET_KEYS:
            continue
        if key not in os.environ and value:
            os.environ[key] = value
            loaded.append(key)
    return {
        "path": str(secret_file),
        "loaded": loaded,
        "exists": True,
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
