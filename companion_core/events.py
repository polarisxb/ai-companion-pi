"""Wake event ledger for internal life-loop observability."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path


def append_wake_event(events_file: Path, event: dict) -> None:
    events_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = events_file.with_suffix(".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(events_file, "a") as events_fd:
                events_fd.write(json.dumps(event, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def load_wake_events(events_file: Path, limit: int | None = None) -> list[dict]:
    try:
        lines = events_file.read_text().splitlines()
    except FileNotFoundError:
        return []

    events = []
    for line in lines:
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events[-limit:] if limit else events


def load_accepted_contexts(events_file: Path, limit: int = 3) -> list[tuple[str, str]]:
    contexts = []
    for event in reversed(load_wake_events(events_file)):
        gate = event.get("quality_gate", {})
        accepted_context = event.get("accepted_context")
        if not gate.get("context_eligible") or not isinstance(accepted_context, dict):
            continue
        summary = str(accepted_context.get("summary", "")).strip()
        if summary:
            contexts.append((str(event.get("id", "accepted wake")), summary))
        if len(contexts) >= limit:
            break
    return contexts
