"""Quality checks for real-provider wake output."""

from __future__ import annotations

import re

from .parser import ParsedWakeOutput
from .state import has_state_update

MIN_JOURNAL_CHARS = 120
MAX_REQUESTS_PER_WAKE = 3
MAX_TRIAL_FRAMING_MATCHES = 1
MIN_CONTEXT_ANCHOR_CHARS = 8
CONTEXT_DELTA_ANCHOR_KEYS = ("current_focus", "open_threads", "next_intent")
TRUSTED_ONLY_CONTEXT_DELTA_FIELDS = ("human_near_status", "human_emotion")
MIN_REPEAT_TOKENS = 8
REPEAT_OVERLAP_THRESHOLD = 0.62
MIN_SHARED_PHRASES = 2
PHRASE_SIZE = 4
CJK_MIN_NGRAMS = 18
CJK_NGRAM_SIZE = 4
CJK_REPEAT_OVERLAP_THRESHOLD = 0.34
CJK_MIN_SHARED_NGRAMS = 10
REPETITION_STOPWORDS = {
    "about",
    "again",
    "because",
    "before",
    "being",
    "between",
    "could",
    "each",
    "from",
    "have",
    "into",
    "just",
    "more",
    "needed",
    "only",
    "same",
    "that",
    "their",
    "there",
    "this",
    "through",
    "wake",
    "what",
    "when",
    "with",
    "without",
}

WAKE_COUNT_RE = re.compile(
    r"\b("
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"\d+(?:st|nd|rd|th)"
    r")\s+wake\b",
    re.IGNORECASE,
)
TRIAL_FRAMING_RE = re.compile(
    r"\b(trials?|testbeds?|providers?|backends?|triggers?|deepseek)\b|internal life loop",
    re.IGNORECASE,
)


def build_quality_report(
    parsed: ParsedWakeOutput,
    *,
    memory_count: int,
    request_count: int,
    request_error_count: int,
    memory_write_results: list[dict] | None = None,
    recent_journals: list[tuple[str, str]] | None = None,
    recent_memories: list[dict] | None = None,
    grounding_warnings: list[str] | None = None,
) -> dict:
    journal_chars = len(parsed.journal.strip())
    companion_state_updated = has_state_update(parsed.companion_state)
    warnings = []

    if not parsed.journal.strip():
        warnings.append("missing journal")
    else:
        if journal_chars < MIN_JOURNAL_CHARS:
            warnings.append(f"journal is short ({journal_chars} chars)")
        warnings.extend(_journal_style_warnings(parsed.journal))
        warnings.extend(_context_delta_anchor_warnings(parsed))
        if _repeats_recent_self_narrative(parsed.journal, recent_journals or [], recent_memories or []):
            warnings.append("journal repeats recent self-narrative phrasing")

    if "COMPANION_STATE" not in parsed.raw_sections:
        warnings.append("missing companion state section")
    elif not companion_state_updated:
        warnings.append("companion state section did not contain an update")
    else:
        status = parsed.companion_state.get("status")
        if isinstance(status, str) and WAKE_COUNT_RE.search(status):
            warnings.append("companion status uses explicit wake-count framing")

    if request_count > MAX_REQUESTS_PER_WAKE:
        warnings.append(f"too many requests ({request_count})")
    if request_error_count:
        warnings.append(f"request errors ({request_error_count})")
    memory_failures = [
        result for result in memory_write_results or []
        if result.get("status") == "failed"
    ]
    if memory_failures:
        warnings.append(f"memory backend failures ({len(memory_failures)})")
    warnings.extend(grounding_warnings or [])

    return {
        "journal_chars": journal_chars,
        "memory_count": memory_count,
        "request_count": request_count,
        "request_error_count": request_error_count,
        "companion_state_updated": companion_state_updated,
        "warnings": warnings,
    }


def _journal_style_warnings(journal: str) -> list[str]:
    warnings = []
    if WAKE_COUNT_RE.search(journal):
        warnings.append("journal uses explicit wake-count framing")

    trial_framing_matches = TRIAL_FRAMING_RE.findall(journal)
    trial_framing_count = len(trial_framing_matches)
    if trial_framing_count > MAX_TRIAL_FRAMING_MATCHES:
        warnings.append(f"journal overuses trial/process framing ({trial_framing_count} matches)")
    return warnings


def _context_delta_anchor_warnings(parsed: ParsedWakeOutput) -> list[str]:
    if "CONTEXT_DELTA" not in parsed.raw_sections:
        return []
    warnings = []
    trusted_only_fields = [
        field for field in TRUSTED_ONLY_CONTEXT_DELTA_FIELDS
        if isinstance(parsed.context_delta, dict) and field in parsed.context_delta
    ]
    if trusted_only_fields:
        warnings.append(
            "context delta proposes trusted-only near-status fields"
        )
    anchor_text = " ".join(_context_anchor_texts(parsed.context_delta))
    if not anchor_text:
        return warnings
    if _meaningful_char_count(anchor_text) < MIN_CONTEXT_ANCHOR_CHARS:
        warnings.append("context delta current anchor is too thin")
    return warnings


def _context_anchor_texts(context_delta: dict) -> list[str]:
    texts = []
    for key in CONTEXT_DELTA_ANCHOR_KEYS:
        value = context_delta.get(key) if isinstance(context_delta, dict) else None
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
        elif isinstance(value, list):
            texts.extend(str(item).strip() for item in value if str(item).strip())
    return texts


def _meaningful_char_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", text))


def _repeats_recent_self_narrative(
    journal: str,
    recent_journals: list[tuple[str, str]],
    recent_memories: list[dict],
) -> bool:
    current_tokens = _meaningful_tokens(journal)
    current_cjk_ngrams = _cjk_ngrams(journal)
    if len(current_tokens) < MIN_REPEAT_TOKENS and len(current_cjk_ngrams) < CJK_MIN_NGRAMS:
        return False

    recent_texts = [body for _name, body in recent_journals]
    recent_texts.extend(
        str(memory.get("content", ""))
        for memory in recent_memories
        if isinstance(memory, dict)
    )
    for text in recent_texts:
        if _is_repeated_text(current_tokens, current_cjk_ngrams, journal, text):
            return True
    return False


def _is_repeated_text(
    current_tokens: set[str],
    current_cjk_ngrams: set[str],
    journal: str,
    recent_text: str,
) -> bool:
    recent_tokens = _meaningful_tokens(recent_text)
    if len(recent_tokens) >= MIN_REPEAT_TOKENS and len(current_tokens) >= MIN_REPEAT_TOKENS:
        overlap = len(current_tokens & recent_tokens) / min(len(current_tokens), len(recent_tokens))
        if overlap >= REPEAT_OVERLAP_THRESHOLD:
            return True

    current_phrases = _phrases(journal)
    recent_phrases = _phrases(recent_text)
    if len(current_phrases & recent_phrases) >= MIN_SHARED_PHRASES:
        return True

    recent_cjk_ngrams = _cjk_ngrams(recent_text)
    if len(current_cjk_ngrams) < CJK_MIN_NGRAMS or len(recent_cjk_ngrams) < CJK_MIN_NGRAMS:
        return False
    shared = len(current_cjk_ngrams & recent_cjk_ngrams)
    overlap = shared / min(len(current_cjk_ngrams), len(recent_cjk_ngrams))
    return shared >= CJK_MIN_SHARED_NGRAMS and overlap >= CJK_REPEAT_OVERLAP_THRESHOLD


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 3 and token not in REPETITION_STOPWORDS
    }


def _phrases(text: str) -> set[tuple[str, ...]]:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2
    ]
    return {
        tuple(tokens[index:index + PHRASE_SIZE])
        for index in range(0, max(0, len(tokens) - PHRASE_SIZE + 1))
    }


def _cjk_ngrams(text: str) -> set[str]:
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    if len(chars) < CJK_NGRAM_SIZE:
        return set()
    compact = "".join(chars)
    return {
        compact[index:index + CJK_NGRAM_SIZE]
        for index in range(0, len(compact) - CJK_NGRAM_SIZE + 1)
    }
