"""Parse structured wake-cycle model output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .memory import MemoryEntry
from .requests import RequestProposal


SECTION_RE = re.compile(r"^===(?P<name>[A-Z_]+)===\s*$", re.MULTILINE)


@dataclass
class ParsedWakeOutput:
    journal: str = ""
    signal: str = ""
    companion_state: dict = field(default_factory=dict)
    context_delta: dict = field(default_factory=dict)
    grounding_claims: list[dict] = field(default_factory=list)
    memories: list[MemoryEntry] = field(default_factory=list)
    requests: list[RequestProposal] = field(default_factory=list)
    raw_sections: dict[str, str] = field(default_factory=dict)


def split_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group("name")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def parse_memory_lines(text: str) -> list[MemoryEntry]:
    if not text or text.strip().upper() == "NOMEMORY":
        return []
    memories = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.upper() == "NOMEMORY":
            continue
        if "|" in line:
            source, content = [part.strip() for part in line.split("|", 1)]
        else:
            source, content = "self", line
        if _is_meaningful_memory_content(content):
            memories.append(_memory_entry_from_model_line(source, content))
    return memories


def _is_meaningful_memory_content(content: str) -> bool:
    cleaned = content.strip()
    if not cleaned:
        return False
    if re.fullmatch(r"[-_*#\s]+", cleaned):
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned))


def _memory_entry_from_model_line(source: str, content: str) -> MemoryEntry:
    source_key = (source or "self").strip().lower()
    if source_key in {"self", "model", "companion", "assistant"}:
        return MemoryEntry(
            content=content,
            source=source_key,
            memory_type="reflection",
            source_type="model",
            authority="model_proposed",
            prompt_eligible=False,
        )
    if source_key in {"user", "human"}:
        return MemoryEntry(
            content=content,
            source=source_key,
            memory_type="semantic",
            source_type="model",
            authority="model_proposed",
            prompt_eligible=False,
        )
    if source_key in {"system", "runtime"}:
        return MemoryEntry(
            content=content,
            source=source_key,
            memory_type="semantic",
            source_type="model",
            authority="model_proposed",
            prompt_eligible=False,
        )
    return MemoryEntry(
        content=content,
        source=source_key,
        memory_type="reflection",
        source_type="model",
        authority="model_proposed",
        prompt_eligible=False,
    )


def parse_request_blocks(text: str) -> list[RequestProposal]:
    if not text or text.strip().upper() in {"NOREQUEST", "NOREQUESTS"}:
        return []

    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        items = payload if isinstance(payload, list) else [payload]
        return [_proposal_from_mapping(item) for item in items if isinstance(item, dict)]

    proposals = []
    for block in re.split(r"\n\s*---+\s*\n", stripped):
        mapping: dict[str, str] = {}
        current_key = None
        for raw_line in block.splitlines():
            if ":" in raw_line:
                key, value = raw_line.split(":", 1)
                current_key = key.strip().lower()
                mapping[current_key] = value.strip()
            elif current_key:
                mapping[current_key] += "\n" + raw_line.strip()
        if mapping:
            proposals.append(_proposal_from_mapping(mapping))
    return proposals


def _proposal_from_mapping(mapping: dict) -> RequestProposal:
    return RequestProposal(
        type=str(mapping.get("type", "fyi")).strip().lower(),
        title=str(mapping.get("title", "Untitled request")).strip(),
        body=str(mapping.get("body", "")).strip(),
        priority=str(mapping.get("priority", "normal")).strip().lower(),
        requested_time=mapping.get("requested_time") or mapping.get("time"),
    )


def parse_companion_state(text: str) -> dict:
    if not text or text.strip().upper() in {"NOSTATE", "NOCOMPANIONSTATE"}:
        return {}

    stripped = text.strip()
    payload = _decode_json_object(stripped)
    if payload is not None:
        return payload

    mapping: dict[str, str | list[str]] = {}
    list_keys = {
        "relationship_note": "relationship_notes",
        "relationship_notes": "relationship_notes",
        "preference_note": "preference_notes",
        "preference_notes": "preference_notes",
        "self_note": "self_notes",
        "self_notes": "self_notes",
    }
    for raw_line in stripped.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        normalized_key = key.strip().lower()
        cleaned_value = value.strip()
        if not cleaned_value:
            continue
        if normalized_key in {"mood", "status"}:
            mapping[normalized_key] = cleaned_value
        elif normalized_key in list_keys:
            target = list_keys[normalized_key]
            mapping.setdefault(target, [])
            mapping[target].append(cleaned_value)
    return mapping


def parse_context_delta(text: str) -> dict:
    if not text or text.strip().upper() in {"NOCONTEXT", "NOCONTEXTDELTA"}:
        return {}
    payload = _decode_json_object(text.strip())
    return payload if isinstance(payload, dict) else {}


def parse_grounding_claims(text: str) -> list[dict]:
    if not text or text.strip().upper() in {
        "NOGROUNDING",
        "NO_GROUNDING",
        "NO_GROUNDING_CLAIMS",
    }:
        return []

    stripped = text.strip()
    payload = None
    if stripped.startswith("["):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            payload = decoded
    if payload is None:
        decoded_object = _decode_json_object(stripped)
        if isinstance(decoded_object, dict):
            claims = decoded_object.get("claims")
            payload = claims if isinstance(claims, list) else [decoded_object]
    if not isinstance(payload, list):
        return []
    return [
        normalized
        for item in payload
        if isinstance(item, dict)
        if (normalized := _normalize_grounding_claim(item)) is not None
    ]


def _normalize_grounding_claim(item: dict) -> dict | None:
    claim = _clean_text(item.get("claim") or item.get("content"))
    if not claim:
        return None
    claim_type = _clean_text(
        item.get("claim_type")
        or item.get("type")
        or item.get("classification")
    ).lower()
    evidence_refs = item.get("evidence_refs")
    if evidence_refs is None:
        evidence_refs = item.get("evidence_ref")
    refs = _normalize_evidence_refs(evidence_refs)
    return {
        "claim": claim,
        "claim_type": claim_type or "unspecified",
        "evidence_refs": refs,
    }


def _normalize_evidence_refs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        refs = []
        seen = set()
        for item in value:
            cleaned = _clean_text(item)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                refs.append(cleaned)
        return refs
    return []


def _clean_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _decode_json_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"{", text):
        try:
            payload, _end = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def parse_wake_output(text: str) -> ParsedWakeOutput:
    sections = split_sections(text)
    return ParsedWakeOutput(
        journal=sections.get("JOURNAL", "").strip(),
        signal=sections.get("SIGNAL", "").strip(),
        companion_state=parse_companion_state(sections.get("COMPANION_STATE", "")),
        context_delta=parse_context_delta(sections.get("CONTEXT_DELTA", "")),
        grounding_claims=parse_grounding_claims(sections.get("GROUNDING", "")),
        memories=parse_memory_lines(sections.get("MEMORY", "")),
        requests=parse_request_blocks(sections.get("REQUESTS", "")),
        raw_sections=sections,
    )
