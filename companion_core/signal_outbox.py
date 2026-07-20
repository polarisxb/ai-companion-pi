"""M11 outbox capture for wake-cycle Signal sections.

Accepted wake cycles may produce a ``===SIGNAL===`` section. Capture turns
that section into one durable, secret-redacted outbox entry; delivery is a
separate policy-gated step owned by the Signal chat bridge. The wake path
itself never touches the network beyond its provider call.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

NOSEND_SENTINEL = "NOSEND"

# Same secret shape the dialogue path redacts before persisting text.
SECRET_LIKE_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*\S+|"
    r"sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}"
)


def normalize_signal_section(text) -> str | None:
    """Return redacted outbound text, or ``None`` when nothing should be sent."""

    cleaned = " ".join(str(text or "").split())
    cleaned = SECRET_LIKE_RE.sub("[REDACTED_SECRET]", cleaned).strip()
    if not cleaned:
        return None
    if cleaned.upper().rstrip(".。!！") == NOSEND_SENTINEL:
        return None
    return cleaned


def build_signal_outbox_entry(
    *,
    content: str,
    source_event_id: str,
    trigger: str,
    now: datetime | None = None,
) -> dict:
    created = now or datetime.now()
    return {
        "id": f"outbox_{created.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}",
        "created_at": created.isoformat(),
        "channel": "signal",
        "content": content,
        "content_hash": _sha256(content),
        "content_length": len(content),
        "source_event_id": source_event_id,
        "trigger": trigger,
    }


def append_signal_outbox_entry(path: Path, entry: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as outbox_fd:
                outbox_fd.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return entry


def load_signal_outbox_entries(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def outbox_event_metadata(entry: dict | None) -> dict | None:
    """Hash-only wake-event metadata; message text never enters the event ledger."""

    if not entry:
        return None
    return {
        "captured": True,
        "entry_id": entry.get("id"),
        "content_hash": entry.get("content_hash"),
        "content_length": entry.get("content_length"),
    }


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()
