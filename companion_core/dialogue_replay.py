"""Read-only validation for M7 dialogue transcripts and events."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dialogue import _clean_visible_text, _relative_to_home, _sha256  # internal validation helpers
from .paths import CompanionPaths


@dataclass
class DialogueReplayCheckResult:
    ok: bool
    transcript_path: Path
    transcript_rows: int = 0
    conversation_id: str | None = None
    event_count: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "transcript": str(self.transcript_path),
            "transcript_rows": self.transcript_rows,
            "conversation_id": self.conversation_id,
            "event_count": self.event_count,
            "problems": self.problems,
            "recommendation": "m7_dialogue_transcript_ready" if self.ok else "inspect",
        }


def check_dialogue_transcript(
    paths: CompanionPaths,
    transcript_path: str | Path,
    *,
    events_path: str | Path | None = None,
) -> DialogueReplayCheckResult:
    """Validate a dialogue transcript and matching events without provider calls or writes."""

    transcript = Path(transcript_path).expanduser()
    if not transcript.is_absolute():
        transcript = paths.home / transcript
    result = DialogueReplayCheckResult(ok=False, transcript_path=transcript)

    rows = _read_jsonl(transcript, result.problems, label="transcript")
    if rows is None:
        return result
    result.transcript_rows = len(rows)
    _validate_rows(rows, result)

    event_file = Path(events_path).expanduser() if events_path is not None else paths.conversation_events_file
    if not event_file.is_absolute():
        event_file = paths.home / event_file
    events = _read_jsonl(event_file, result.problems, label="events", missing_ok=True)
    if events is not None:
        _validate_events(paths, transcript, rows, events, result)

    result.ok = not result.problems
    return result


def _read_jsonl(path: Path, problems: list[str], *, label: str, missing_ok: bool = False) -> list[dict[str, Any]] | None:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        if missing_ok:
            return []
        problems.append(f"{label}:missing:{path}")
        return None
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{label}:line_{line_no}:invalid_json:{exc.msg}")
            continue
        if not isinstance(record, dict):
            problems.append(f"{label}:line_{line_no}:not_object")
            continue
        records.append(record)
    return records


def _validate_rows(rows: list[dict[str, Any]], result: DialogueReplayCheckResult) -> None:
    if not rows:
        result.problems.append("transcript:empty")
        return
    expected_conversation_id = rows[0].get("conversation_id")
    if not expected_conversation_id:
        result.problems.append("transcript:line_1:missing_conversation_id")
    result.conversation_id = expected_conversation_id

    previous_human: dict[str, Any] | None = None
    failed_human_pending = False
    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        row_id = row.get("id")
        role = row.get("role")
        content = row.get("content")
        if not row_id:
            result.problems.append(f"transcript:line_{index}:missing_id")
        elif row_id in seen_ids:
            result.problems.append(f"transcript:line_{index}:duplicate_id:{row_id}")
        else:
            seen_ids.add(str(row_id))
        if row.get("conversation_id") != expected_conversation_id:
            result.problems.append(f"transcript:line_{index}:conversation_id_mismatch")
        if role not in {"human", "assistant"}:
            result.problems.append(f"transcript:line_{index}:invalid_role:{role}")
        if not isinstance(content, str) or not content:
            result.problems.append(f"transcript:line_{index}:missing_content")
        if row.get("raw_output_stored") is not False:
            result.problems.append(f"transcript:line_{index}:raw_output_stored_not_false")
        if "raw_provider_payload" in row or "raw_output" in row:
            result.problems.append(f"transcript:line_{index}:raw_provider_payload_present")
        if content and _clean_visible_text(content) != content:
            result.problems.append(f"transcript:line_{index}:secret_like_content_not_redacted")

        if role == "human":
            if row.get("input_hash") != _sha256(content or ""):
                result.problems.append(f"transcript:line_{index}:input_hash_mismatch")
            if row.get("output_hash") is not None:
                result.problems.append(f"transcript:line_{index}:human_output_hash_not_null")
            previous_human = row
            failed_human_pending = row.get("turn_status") == "failed"
        elif role == "assistant":
            if failed_human_pending:
                result.problems.append(f"transcript:line_{index}:assistant_after_failed_human_turn")
            if previous_human is None:
                result.problems.append(f"transcript:line_{index}:assistant_without_human_turn")
            elif row.get("input_hash") != previous_human.get("input_hash"):
                result.problems.append(f"transcript:line_{index}:assistant_input_hash_mismatch")
            if row.get("output_hash") != _sha256(content or ""):
                result.problems.append(f"transcript:line_{index}:output_hash_mismatch")
            if row.get("turn_status") == "failed":
                result.problems.append(f"transcript:line_{index}:failed_assistant_turn")
            previous_human = None
            failed_human_pending = False


def _validate_events(
    paths: CompanionPaths,
    transcript: Path,
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    result: DialogueReplayCheckResult,
) -> None:
    transcript_ref = _relative_to_home(paths, transcript)
    matching = [event for event in events if event.get("transcript") in {transcript_ref, str(transcript)}]
    result.event_count = len(matching)
    seen_event_ids: set[str] = set()
    for index, event in enumerate(events, start=1):
        event_id = event.get("id")
        if not event_id:
            result.problems.append(f"events:line_{index}:missing_id")
        elif event_id in seen_event_ids:
            result.problems.append(f"events:line_{index}:duplicate_id:{event_id}")
        else:
            seen_event_ids.add(str(event_id))
        if event.get("raw_output_stored") is not False:
            result.problems.append(f"events:line_{index}:raw_output_stored_not_false")
        if "raw_provider_payload" in event or "raw_output" in event:
            result.problems.append(f"events:line_{index}:raw_provider_payload_present")
        boundaries = event.get("boundaries")
        if isinstance(boundaries, dict):
            for name in ("wake_cycle_run", "scheduler_mutated", "raw_provider_payload_stored", "semantic_shadow_authority_promoted"):
                if boundaries.get(name) is not False:
                    result.problems.append(f"events:line_{index}:boundary_{name}_not_false")
    if not matching:
        result.problems.append(f"events:no_event_for_transcript:{transcript_ref}")
        return
    completed_assistant_rows = [row for row in rows if row.get("role") == "assistant" and row.get("turn_status") != "failed"]
    failed_human_rows = [row for row in rows if row.get("role") == "human" and row.get("turn_status") == "failed"]
    completed_events = [event for event in matching if event.get("status") == "completed"]
    failed_events = [event for event in matching if event.get("status") == "failed"]
    if completed_events and not completed_assistant_rows:
        result.problems.append("events:completed_event_without_assistant_turn")
    if len(completed_events) != len(completed_assistant_rows):
        result.problems.append(
            f"events:completed_event_count_mismatch:events={len(completed_events)} assistant_turns={len(completed_assistant_rows)}"
        )
    if len(failed_events) != len(failed_human_rows):
        result.problems.append(
            f"events:failed_event_count_mismatch:events={len(failed_events)} failed_human_turns={len(failed_human_rows)}"
        )
    for event in completed_events:
        if result.conversation_id and event.get("conversation_id") != result.conversation_id:
            result.problems.append(f"events:{event.get('id')}:conversation_id_mismatch")
        if event.get("turn_count") not in {None, 2} and event.get("turn_count") != len(rows):
            result.problems.append(f"events:{event.get('id')}:unexpected_turn_count:{event.get('turn_count')}")
    for event in failed_events:
        if result.conversation_id and event.get("conversation_id") != result.conversation_id:
            result.problems.append(f"events:{event.get('id')}:conversation_id_mismatch")
        if event.get("turn_count") not in {None, 0}:
            result.problems.append(f"events:{event.get('id')}:failed_event_turn_count_not_zero:{event.get('turn_count')}")
