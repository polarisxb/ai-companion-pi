"""Read-only validation for M7 dialogue transcripts and event ledgers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES, _sha256
from .paths import CompanionPaths

RAW_PAYLOAD_KEYS = {
    "raw_output",
    "raw_provider_payload",
    "provider_payload",
    "raw_response",
    "provider_response",
    "request_payload",
    "response_payload",
}


@dataclass
class DialogueReplayCheckResult:
    ok: bool
    transcript: str
    event_log: str
    rows_checked: int
    events_checked: int
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "recommendation": "m7_dialogue_transcript_ready" if self.ok else "inspect",
            "transcript": self.transcript,
            "event_log": self.event_log,
            "rows_checked": self.rows_checked,
            "events_checked": self.events_checked,
            "errors": self.errors,
            "provider_calls": 0,
            "boundaries": dict(DIALOGUE_BOUNDARIES),
        }


def check_dialogue_transcript(paths: CompanionPaths, transcript_path: Path) -> DialogueReplayCheckResult:
    """Validate a transcript and linked dialogue events without provider calls."""

    transcript_path = transcript_path.expanduser()
    if not transcript_path.is_absolute():
        transcript_path = paths.home / transcript_path
    event_log = paths.conversation_events_file
    errors: list[str] = []
    rows = _read_jsonl(transcript_path, errors, label="transcript")
    events = _read_jsonl(event_log, errors, label="event_log") if event_log.exists() else []

    conversation_ids = {row.get("conversation_id") for row in rows if isinstance(row, dict)}
    if len(conversation_ids) > 1:
        errors.append(f"transcript mixes conversation ids: {sorted(conversation_ids)}")
    conversation_id = next(iter(conversation_ids), None) if conversation_ids else None

    for idx, row in enumerate(rows, start=1):
        _validate_turn_row(row, idx, errors)

    for idx, row in enumerate(rows[:-1], start=1):
        next_row = rows[idx]
        if row.get("role") == "human" and row.get("status") == "failed" and next_row.get("role") == "assistant":
            errors.append(f"line {idx + 1}: assistant turn follows failed human input")
        if row.get("role") == "human" and row.get("status") == "completed" and next_row.get("role") != "assistant":
            errors.append(f"line {idx}: completed human turn is not followed by assistant turn")
        if row.get("role") == "assistant" and next_row.get("role") == "assistant":
            errors.append(f"line {idx + 1}: consecutive assistant turns")

    if rows and rows[-1].get("role") == "human" and rows[-1].get("status") == "completed":
        errors.append(f"line {len(rows)}: completed human turn has no assistant reply")

    transcript_ref = _relative_to_home(paths, transcript_path)
    linked_events = [
        event for event in events
        if event.get("transcript") in {transcript_ref, str(transcript_path)}
        and (conversation_id is None or event.get("conversation_id") == conversation_id)
    ]
    _validate_events(linked_events, rows, errors)

    return DialogueReplayCheckResult(
        ok=not errors,
        transcript=str(transcript_path),
        event_log=str(event_log),
        rows_checked=len(rows),
        events_checked=len(linked_events),
        errors=errors,
    )


def _read_jsonl(path: Path, errors: list[str], *, label: str) -> list[dict]:
    rows = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        errors.append(f"{label} missing: {path}")
        return rows
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{label} line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(row, dict):
            errors.append(f"{label} line {line_number}: row must be an object")
            continue
        rows.append(row)
    return rows


def _validate_turn_row(row: dict, line_number: int, errors: list[str]) -> None:
    role = row.get("role")
    status = row.get("status", "completed")
    content = row.get("content")
    if role not in {"human", "assistant"}:
        errors.append(f"line {line_number}: invalid role {role!r}")
    if status not in {"completed", "failed"}:
        errors.append(f"line {line_number}: invalid status {status!r}")
    for key in ("id", "conversation_id", "created_at"):
        if not row.get(key):
            errors.append(f"line {line_number}: missing {key}")
    # M7.3 added event_id/turn_ids after early M7.1 real transcripts had already
    # been captured. Keep replay read-only and backward-compatible for those
    # legacy artifacts; when event_id is present, _validate_events still checks
    # the exact row/event linkage.
    if not isinstance(content, str) or not content.strip():
        errors.append(f"line {line_number}: content must be non-empty text")
    if any(key in row for key in RAW_PAYLOAD_KEYS):
        errors.append(f"line {line_number}: raw provider payload field is not allowed")
    if row.get("raw_output_stored") is not False:
        errors.append(f"line {line_number}: raw_output_stored must be false")
    if role == "human":
        if row.get("input_hash") != _sha256(content or ""):
            errors.append(f"line {line_number}: input_hash mismatch")
        if row.get("output_hash") is not None:
            errors.append(f"line {line_number}: human output_hash must be null")
    if role == "assistant":
        if status != "completed":
            errors.append(f"line {line_number}: assistant turn must be completed")
        if row.get("output_hash") != _sha256(content or ""):
            errors.append(f"line {line_number}: output_hash mismatch")


def _validate_events(events: list[dict], rows: list[dict], errors: list[str]) -> None:
    if not events:
        errors.append("no linked dialogue events found")
        return
    completed_rows = [row for row in rows if row.get("role") == "assistant" and row.get("status", "completed") == "completed"]
    failed_rows = [row for row in rows if row.get("role") == "human" and row.get("status") == "failed"]
    completed_events = [event for event in events if event.get("status") == "completed"]
    failed_events = [event for event in events if event.get("status") == "failed"]
    if len(completed_events) != len(completed_rows):
        errors.append(f"completed event count {len(completed_events)} does not match assistant rows {len(completed_rows)}")
    if len(failed_events) != len(failed_rows):
        errors.append(f"failed event count {len(failed_events)} does not match failed human rows {len(failed_rows)}")
    for event in events:
        event_turn_ids = event.get("turn_ids")
        linked_rows = [row for row in rows if row.get("event_id") == event.get("id")]
        if isinstance(event_turn_ids, list) and event_turn_ids:
            linked_row_ids = [row.get("id") for row in linked_rows]
            if linked_rows and event_turn_ids != linked_row_ids:
                errors.append(f"event {event.get('id')}: turn_ids do not match transcript rows")
            if event.get("first_turn_id") and event.get("first_turn_id") != event_turn_ids[0]:
                errors.append(f"event {event.get('id')}: first_turn_id mismatch")
            if event.get("last_turn_id") and event.get("last_turn_id") != event_turn_ids[-1]:
                errors.append(f"event {event.get('id')}: last_turn_id mismatch")
        elif linked_rows:
            errors.append(f"event {event.get('id')}: missing turn_ids")
        if any(key in event for key in RAW_PAYLOAD_KEYS):
            errors.append(f"event {event.get('id')}: raw provider payload field is not allowed")
        if event.get("raw_output_stored") is not False:
            errors.append(f"event {event.get('id')}: raw_output_stored must be false")
        if event.get("boundaries") != DIALOGUE_BOUNDARIES:
            errors.append(f"event {event.get('id')}: dialogue boundaries changed")
        if event.get("status") == "failed" and event.get("turn_count") != 1:
            errors.append(f"event {event.get('id')}: failed event turn_count must be 1")
        if event.get("status") == "completed" and event.get("turn_count") != 2:
            errors.append(f"event {event.get('id')}: completed event turn_count must be 2")


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
