"""Persistent companion self/relationship state for wake cycles."""

from __future__ import annotations

import fcntl
import json
import re
from datetime import datetime
from pathlib import Path

NOTE_FIELDS = ("relationship_notes", "preference_notes", "self_notes")
STATE_UPDATE_FIELDS = ("mood", "status", *NOTE_FIELDS)
MAX_NOTES_PER_FIELD = 40
MAX_RENDERED_NOTES_PER_FIELD = 3
PLACEHOLDER_NOTES = {"(none yet)", "none yet", "none", "n/a", "na"}
MIN_SIMILAR_NOTE_TOKENS = 5
SIMILAR_NOTE_OVERLAP = 0.72
PROMPT_ECHO_RE = re.compile(
    r"\b("
    r"co-tending|quiet tending|unhurried presence|shared rhythm|"
    r"trust (?:feels|is|deepens|exists)|without (?:verification|strain|effort)|"
    r"no new milestones|shape of self persists|continuity across close wakes"
    r")\b",
    re.IGNORECASE,
)


def default_companion_state() -> dict:
    return {
        "version": 1,
        "mood": "reflective",
        "status": "I am building continuity.",
        "relationship_notes": [],
        "preference_notes": [],
        "self_notes": [],
        "updated_at": None,
    }


def load_companion_state(state_file: Path) -> dict:
    try:
        payload = json.loads(state_file.read_text())
    except FileNotFoundError:
        return default_companion_state()
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid companion state JSON: {state_file}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"companion state must be a JSON object: {state_file}")
    return _normalize_state(payload)


def update_companion_state(state_file: Path, update: dict) -> dict:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = state_file.with_suffix(".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            state = load_companion_state(state_file)
            updated = merge_companion_state(state, update)
            state_file.write_text(json.dumps(updated, indent=2, sort_keys=True))
            return updated
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def merge_companion_state(state: dict, update: dict | None) -> dict:
    merged = _normalize_state(state)
    if not has_state_update(update):
        return merged

    mood = _clean_text(update.get("mood"))
    status = _clean_text(update.get("status"))
    if mood:
        merged["mood"] = mood[:80]
    if status:
        merged["status"] = status[:500]

    for field in NOTE_FIELDS:
        incoming = _text_list(update.get(field))
        merged[field] = _merge_notes(merged.get(field, []), incoming)

    merged["updated_at"] = datetime.now().isoformat()
    return merged


def has_state_update(update: dict | None) -> bool:
    if not isinstance(update, dict):
        return False
    if _clean_text(update.get("mood")) or _clean_text(update.get("status")):
        return True
    return any(_text_list(update.get(field)) for field in NOTE_FIELDS)


def render_companion_state(state: dict) -> str:
    normalized = _normalize_state(state)
    return "\n".join(
        [
            f"Mood: {normalized['mood']}",
            f"Status: {normalized['status']}",
            "Relationship notes:",
            *_render_notes(_prompt_notes(normalized["relationship_notes"])),
            "Preference notes:",
            *_render_notes(_prompt_notes(normalized["preference_notes"])),
            "Self notes:",
            *_render_notes(_prompt_notes(normalized["self_notes"])),
        ]
    )


def _normalize_state(payload: dict) -> dict:
    state = default_companion_state()
    state.update({key: value for key, value in payload.items() if key in state})
    state["mood"] = _clean_text(state.get("mood")) or "reflective"
    state["status"] = _clean_text(state.get("status")) or "I am building continuity."
    for field in NOTE_FIELDS:
        state[field] = _text_list(state.get(field))[:MAX_NOTES_PER_FIELD]
    return state


def _merge_notes(existing: list[str], incoming: list[str]) -> list[str]:
    notes = []
    seen = set()
    for note in [*existing, *incoming]:
        normalized = _clean_text(note)
        key = _note_key(normalized)
        if not normalized:
            continue
        if key in seen:
            continue
        similar_index = _similar_note_index(notes, normalized)
        if similar_index is not None:
            replacement = _prefer_note(notes[similar_index], normalized)
            if replacement != notes[similar_index]:
                old_key = _note_key(notes[similar_index])
                seen.discard(old_key)
                notes[similar_index] = replacement[:500]
                seen.add(_note_key(notes[similar_index]))
            continue
        seen.add(key)
        notes.append(normalized[:500])
    return notes[-MAX_NOTES_PER_FIELD:]


def _text_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned] if cleaned and not _is_placeholder(cleaned) else []
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := _clean_text(item)) and not _is_placeholder(cleaned)
        ]
    return []


def _clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDER_NOTES


def _note_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _note_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    }


def _similar_note_index(notes: list[str], candidate: str) -> int | None:
    candidate_tokens = _note_tokens(candidate)
    if len(candidate_tokens) < MIN_SIMILAR_NOTE_TOKENS:
        return None
    for index, note in enumerate(notes):
        note_tokens = _note_tokens(note)
        if len(note_tokens) < MIN_SIMILAR_NOTE_TOKENS:
            continue
        overlap = len(candidate_tokens & note_tokens) / min(len(candidate_tokens), len(note_tokens))
        if overlap >= SIMILAR_NOTE_OVERLAP:
            return index
    return None


def _prefer_note(existing: str, candidate: str) -> str:
    if len(_note_tokens(candidate)) > len(_note_tokens(existing)):
        return candidate
    if len(candidate) > len(existing) * 1.15:
        return candidate
    return existing


def _render_notes(notes: list[str]) -> list[str]:
    return [f"- {note}" for note in notes] if notes else ["- (none yet)"]


def _prompt_notes(notes: list[str]) -> list[str]:
    concrete = [note for note in notes if not PROMPT_ECHO_RE.search(note)]
    selected = concrete[-MAX_RENDERED_NOTES_PER_FIELD:]
    if selected:
        return selected
    return notes[-MAX_RENDERED_NOTES_PER_FIELD:]
