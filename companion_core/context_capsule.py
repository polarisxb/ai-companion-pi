"""Structured future-context capsule for accepted wake cycles."""

from __future__ import annotations

import fcntl
import json
import re
from datetime import datetime
from pathlib import Path

LIST_FIELDS = (
    "current_focus",
    "facts",
    "human_preferences",
    "human_near_status",
    "human_emotion",
    "open_threads",
)
SCALAR_FIELDS = ("next_intent",)
ALL_FIELDS = (*LIST_FIELDS, *SCALAR_FIELDS)
MODEL_WRITABLE_LIST_FIELDS = ("current_focus", "open_threads")
MODEL_WRITABLE_FIELDS = (*MODEL_WRITABLE_LIST_FIELDS, "next_intent")
TRUSTED_SHORT_TERM_FIELDS = ("human_near_status", "human_emotion")
SHORT_TERM_FIELDS = (*MODEL_WRITABLE_FIELDS, *TRUSTED_SHORT_TERM_FIELDS)
DURABLE_FIELDS = ("facts", "human_preferences")
MAX_ITEMS_PER_FIELD = 8
FIELD_LIMITS = {
    "current_focus": 3,
    "facts": 5,
    "human_preferences": 5,
    "human_near_status": 2,
    "human_emotion": 2,
    "open_threads": 3,
    "next_intent": 1,
}
MAX_TEXT_CHARS = 180
SHORT_TERM_TTL_WAKES = 3
PROMPT_AUTHORITIES = {
    "user_asserted",
    "system_config",
    "evaluator_approved",
    "derived_summary",
}
SHORT_TERM_AUTHORITIES = {*PROMPT_AUTHORITIES, "model_proposed"}


def default_context_capsule() -> dict:
    return {
        "version": 2,
        "updated_at": None,
        "items": [],
    }


def load_context_capsule(capsule_file: Path) -> dict:
    try:
        payload = json.loads(capsule_file.read_text())
    except FileNotFoundError:
        return default_context_capsule()
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid context capsule JSON: {capsule_file}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"context capsule must be a JSON object: {capsule_file}")
    return normalize_context_capsule(payload)


def update_context_capsule(
    capsule_file: Path,
    delta: dict | None,
    *,
    source_refs: list[dict] | None = None,
) -> tuple[dict, bool]:
    normalized_delta = normalize_context_delta(delta)
    has_delta = _has_delta(normalized_delta)
    if not has_delta and not capsule_file.exists():
        return load_context_capsule(capsule_file), False

    capsule_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = capsule_file.with_suffix(".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            capsule = load_context_capsule(capsule_file)
            aged_items, ttl_changed = age_short_term_items(capsule["items"])
            if has_delta:
                items = [
                    item for item in aged_items
                    if item["field"] not in MODEL_WRITABLE_FIELDS
                ]
                for field in MODEL_WRITABLE_LIST_FIELDS:
                    values = _text_list(normalized_delta.get(field))[-FIELD_LIMITS[field]:]
                    items.extend(
                        _item(
                            field=field,
                            content=value,
                            source_refs=source_refs,
                            source_type="model",
                            authority="model_proposed",
                            prompt_eligible=True,
                            ttl_wakes=SHORT_TERM_TTL_WAKES,
                        )
                        for value in values
                    )
                if normalized_delta.get("next_intent"):
                    items.append(_item(
                        field="next_intent",
                        content=normalized_delta["next_intent"],
                        source_refs=source_refs,
                        source_type="model",
                        authority="model_proposed",
                        prompt_eligible=True,
                        ttl_wakes=SHORT_TERM_TTL_WAKES,
                    ))
            else:
                items = aged_items
                if not ttl_changed:
                    return capsule, False
            capsule = {
                "version": 2,
                "updated_at": datetime.now().isoformat(),
                "items": _limit_items(items),
            }
            capsule_file.write_text(json.dumps(capsule, indent=2, sort_keys=True))
            return capsule, True
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def normalize_context_capsule(payload: dict) -> dict:
    if payload.get("version") == 2 and isinstance(payload.get("items"), list):
        items = [
            normalized
            for item in payload.get("items", [])
            if isinstance(item, dict)
            if (normalized := _normalize_item(item)) is not None
        ]
        return {
            "version": 2,
            "updated_at": _clean_text(payload.get("updated_at")) or None,
            "items": _limit_items(items),
        }
    return _normalize_v1_capsule(payload)


def normalize_context_delta(delta: dict | None) -> dict:
    if not isinstance(delta, dict):
        return {}
    normalized: dict[str, list[str] | str] = {}
    for field in MODEL_WRITABLE_LIST_FIELDS:
        values = _text_list(delta.get(field))
        if values:
            normalized[field] = values
    next_intent = _clean_text(delta.get("next_intent"))
    if next_intent:
        normalized["next_intent"] = next_intent[:MAX_TEXT_CHARS]
    return normalized


def age_short_term_items(items: list[dict]) -> tuple[list[dict], bool]:
    """Consume one accepted-wake TTL from short-term capsule items."""

    aged: list[dict] = []
    changed = False
    for item in items:
        normalized = _normalize_item(item)
        if not normalized:
            changed = True
            continue
        if normalized["field"] not in SHORT_TERM_FIELDS:
            aged.append(normalized)
            continue

        ttl_wakes = normalized.get("ttl_wakes")
        if type(ttl_wakes) is not int or ttl_wakes <= 0:
            changed = True
            continue

        normalized = {**normalized, "ttl_wakes": ttl_wakes - 1}
        changed = True
        if normalized["ttl_wakes"] > 0:
            aged.append(normalized)
    return aged, changed


def render_context_capsule(capsule: dict) -> str:
    normalized = normalize_context_capsule(capsule)
    lines: list[str] = []
    labels = {
        "current_focus": "Current focus",
        "facts": "Facts",
        "human_preferences": "Human preferences",
        "human_near_status": "Human near status",
        "human_emotion": "Human emotion",
        "open_threads": "Open threads",
    }
    grouped = _renderable_items_by_field(normalized["items"])
    for field in LIST_FIELDS:
        values = grouped.get(field, [])
        if values:
            lines.append(f"{labels[field]}:")
            lines.extend(f"- {value}" for value in values)
    next_intent = grouped.get("next_intent", [])
    if next_intent:
        lines.append(f"Next intent: {next_intent[-1]}")
    return "\n".join(lines) if lines else "(empty)"


def count_context_capsule_items(capsule: dict) -> int:
    normalized = normalize_context_capsule(capsule)
    grouped = _renderable_items_by_field(normalized["items"])
    return sum(len(values) for values in grouped.values())


def _normalize_v1_capsule(payload: dict) -> dict:
    items = []
    for field in MODEL_WRITABLE_LIST_FIELDS:
        items.extend(
            _item(
                field=field,
                content=value,
                source_refs=[],
                source_type="model",
                authority="model_proposed",
                prompt_eligible=True,
                ttl_wakes=SHORT_TERM_TTL_WAKES,
            )
            for value in _text_list(payload.get(field))
        )
    for field in DURABLE_FIELDS:
        items.extend(
            _item(
                field=field,
                content=value,
                source_refs=[],
                source_type="legacy",
                authority="legacy_unverified",
                prompt_eligible=False,
                ttl_wakes=None,
            )
            for value in _text_list(payload.get(field))
        )
    next_intent = _clean_text(payload.get("next_intent"))
    if next_intent:
        items.append(_item(
            field="next_intent",
            content=next_intent[:MAX_TEXT_CHARS],
            source_refs=[],
            source_type="model",
            authority="model_proposed",
            prompt_eligible=True,
            ttl_wakes=SHORT_TERM_TTL_WAKES,
        ))
    return {
        "version": 2,
        "updated_at": _clean_text(payload.get("updated_at")) or None,
        "items": _limit_items(items),
    }


def _normalize_item(item: dict) -> dict | None:
    field = _clean_text(item.get("field"))
    if field not in ALL_FIELDS:
        return None
    content = _clean_text(item.get("content"))[:MAX_TEXT_CHARS]
    if not content:
        return None
    source_type = _clean_text(item.get("source_type")) or "unknown"
    authority = _clean_text(item.get("authority")) or "legacy_unverified"
    prompt_eligible = item.get("prompt_eligible") is True
    ttl_wakes = item.get("ttl_wakes")
    if type(ttl_wakes) is not int:
        ttl_wakes = None
    return _item(
        field=field,
        content=content,
        source_refs=_source_refs(item.get("source_refs")),
        source_type=source_type,
        authority=authority,
        prompt_eligible=prompt_eligible,
        ttl_wakes=ttl_wakes,
    )


def _item(
    *,
    field: str,
    content: str,
    source_refs: list[dict] | None,
    source_type: str,
    authority: str,
    prompt_eligible: bool,
    ttl_wakes: int | None,
) -> dict:
    return {
        "field": field,
        "content": _clean_text(content)[:MAX_TEXT_CHARS],
        "source_refs": _source_refs(source_refs),
        "source_type": source_type,
        "authority": authority,
        "prompt_eligible": bool(prompt_eligible),
        "ttl_wakes": ttl_wakes,
    }


def _limit_items(items: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {field: [] for field in ALL_FIELDS}
    seen: dict[str, set[str]] = {field: set() for field in ALL_FIELDS}
    for item in items:
        normalized = _normalize_item(item)
        if not normalized:
            continue
        field = normalized["field"]
        key = _text_key(normalized["content"])
        if key in seen[field]:
            continue
        seen[field].add(key)
        grouped[field].append(normalized)

    limited: list[dict] = []
    for field in ALL_FIELDS:
        limited.extend(grouped[field][-FIELD_LIMITS[field]:])
    return limited


def _renderable_items_by_field(items: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in items:
        if not _is_prompt_renderable(item):
            continue
        grouped.setdefault(item["field"], []).append(item["content"])
    return grouped


def _is_prompt_renderable(item: dict) -> bool:
    if item.get("prompt_eligible") is not True:
        return False
    field = item.get("field")
    authority = item.get("authority")
    if field in DURABLE_FIELDS:
        return authority in PROMPT_AUTHORITIES and bool(item.get("source_refs"))
    if field in TRUSTED_SHORT_TERM_FIELDS:
        return (
            authority in PROMPT_AUTHORITIES
            and bool(item.get("source_refs"))
            and _has_positive_ttl(item)
        )
    if field in MODEL_WRITABLE_FIELDS:
        return authority in SHORT_TERM_AUTHORITIES and _has_positive_ttl(item)
    return False


def _has_positive_ttl(item: dict) -> bool:
    ttl_wakes = item.get("ttl_wakes")
    return type(ttl_wakes) is int and ttl_wakes > 0


def _source_refs(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    refs = []
    for item in value:
        if isinstance(item, dict):
            refs.append(dict(item))
    return refs


def _text_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned[:MAX_TEXT_CHARS]] if cleaned else []
    if isinstance(value, list):
        items = []
        seen = set()
        for item in value:
            cleaned = _clean_text(item)
            key = _text_key(cleaned)
            if cleaned and key not in seen:
                seen.add(key)
                items.append(cleaned[:MAX_TEXT_CHARS])
        return items
    return []


def _clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _text_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def _has_delta(delta: dict) -> bool:
    return any(delta.get(field) for field in ALL_FIELDS)
