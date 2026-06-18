"""Small provenance helpers for memory and context artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def content_hash(value: Any, *, length: int = 16) -> str:
    text = _canonical_text(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def evidence_ref(
    *,
    event_id: str | None,
    artifact: str,
    content: Any,
    path: str | Path | None = None,
) -> dict:
    ref = {
        "artifact": artifact,
        "content_hash": content_hash(content),
    }
    if event_id:
        ref["event_id"] = event_id
    if path:
        ref["path"] = str(path)
    return ref


def _canonical_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return " ".join(str(value).split())
