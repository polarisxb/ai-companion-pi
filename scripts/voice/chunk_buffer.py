#!/usr/bin/env python3
"""
chunk_buffer.py — Splits text into natural voice-memo-sized chunks.

Ported from AIRI's TTS chunking pipeline. Feeds tokens into a buffer
and emits chunks at sentence boundaries, soft pauses, or max-word limits.
Designed for streaming TTS: feed tokens as they arrive from the LLM,
pull complete chunks when ready.

Usage:
  python3 scripts/voice/chunk_buffer.py          # demo
  from scripts.voice.chunk_buffer import chunk_text
  chunks = chunk_text("Hello! How are you today?")

Stdlib only — no external dependencies.
"""

import re
from typing import Optional


# --- Constants ---

HARD_BOUNDARIES = {'.', '?', '!', '\n'}
SOFT_BOUNDARIES = {',', ';', ':', '\u2014', '\u2013'}  # em-dash, en-dash

MIN_WORDS = 6
MAX_WORDS = 50
SOFT_THRESHOLD = 30  # split at soft boundary if buffer >= this many words

# Abbreviations where trailing period is NOT a sentence boundary
ABBREVIATIONS = {'dr', 'mr', 'mrs', 'ms', 'vs', 'etc', 'e.g', 'i.e',
                 'prof', 'sr', 'jr', 'st', 'ave', 'dept', 'approx'}

# Markdown formatting to strip
_BOLD_ITALIC_RE = re.compile(r'[*_]{1,3}')
_HEADING_RE = re.compile(r'^#{1,6}\s+', re.MULTILINE)
_BULLET_RE = re.compile(r'^\s*[-*+]\s+', re.MULTILINE)
_NUMBERED_RE = re.compile(r'^\s*\d+\.\s+', re.MULTILINE)
_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_CODE_INLINE_RE = re.compile(r'`([^`]+)`')
_BLOCKQUOTE_RE = re.compile(r'^\s*>\s*', re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Remove markdown formatting so Piper doesn't read syntax aloud."""
    text = _HEADING_RE.sub('', text)
    text = _BLOCKQUOTE_RE.sub('', text)
    text = _BULLET_RE.sub('', text)
    text = _NUMBERED_RE.sub('', text)
    text = _LINK_RE.sub(r'\1', text)          # [text](url) -> text
    text = _CODE_INLINE_RE.sub(r'\1', text)   # `code` -> code
    text = _BOLD_ITALIC_RE.sub('', text)
    return text


def _word_count(text: str) -> int:
    """Count words in text."""
    return len(text.split())


def _is_abbreviation(buffer: str) -> bool:
    """Check if the buffer ends with a known abbreviation (e.g. 'Dr.')."""
    # Grab the last token before the trailing period
    stripped = buffer.rstrip()
    if not stripped.endswith('.'):
        return False
    # Find the last word (including the period)
    words = stripped.split()
    if not words:
        return False
    last = words[-1]
    # Remove the trailing period, lowercase, check
    base = last.rstrip('.').lower()
    return base in ABBREVIATIONS


def _is_number_decimal(buffer: str) -> bool:
    """Check if trailing period is a decimal point (e.g. '3.14' in progress)."""
    stripped = buffer.rstrip()
    if not stripped.endswith('.'):
        return False
    # Look for digit immediately before the period
    if len(stripped) >= 2 and stripped[-2].isdigit():
        return True
    return False


class ChunkBuffer:
    """Accumulates tokens and emits natural voice-sized chunks.

    Usage:
        buf = ChunkBuffer()
        for token in stream:
            buf.feed(token)
            for chunk in buf.get_chunks():
                send_to_tts(chunk)
        leftover = buf.flush()
        if leftover:
            send_to_tts(leftover)
    """

    def __init__(self):
        self._buffer = ""
        self._ready: list[str] = []

    def feed(self, token: str) -> None:
        """Accept a token and accumulate in internal buffer."""
        self._buffer += token
        self._scan()

    def get_chunks(self) -> list[str]:
        """Return complete chunks ready to emit, clearing them."""
        chunks = self._ready
        self._ready = []
        return chunks

    def flush(self) -> Optional[str]:
        """Force-emit whatever remains in the buffer."""
        self._emit_all_remaining()
        chunks = self._ready
        self._ready = []
        # Return the last chunk (or None)
        if chunks:
            # Could be multiple if max-word splitting happened
            return ' '.join(chunks) if len(chunks) > 1 else chunks[0]
        return None

    def _emit(self, text: str) -> None:
        """Emit a chunk if it's non-empty and meets min length."""
        cleaned = text.strip()
        if not cleaned:
            return
        if _word_count(cleaned) < MIN_WORDS:
            # Too short — push back into buffer to merge with next content
            self._buffer = cleaned + ' ' + self._buffer.lstrip()
            return
        self._ready.append(cleaned)

    def _force_emit(self, text: str) -> None:
        """Emit a chunk regardless of min-word count (used for flush)."""
        cleaned = text.strip()
        if cleaned:
            self._ready.append(cleaned)

    def _scan(self) -> None:
        """Scan buffer for chunk boundaries and emit complete chunks."""
        while True:
            split_pos = self._find_split()
            if split_pos is None:
                # Check max-word overflow
                if _word_count(self._buffer.strip()) >= MAX_WORDS:
                    self._split_at_max()
                break
            # Split at the found position
            chunk = self._buffer[:split_pos]
            self._buffer = self._buffer[split_pos:]
            self._emit(chunk)

    def _find_split(self) -> Optional[int]:
        """Find the best position to split the buffer, or None."""
        buf = self._buffer

        # Handle ellipsis: treat '...' as a single hard boundary
        ellipsis_pos = buf.find('...')
        if ellipsis_pos >= 0:
            # Split after the ellipsis
            end = ellipsis_pos + 3
            # Skip any extra dots
            while end < len(buf) and buf[end] == '.':
                end += 1
            candidate_chunk = buf[:end]
            if _word_count(candidate_chunk.strip()) >= MIN_WORDS:
                return end

        best_hard = None
        best_soft = None

        i = 0
        while i < len(buf):
            ch = buf[i]

            # Skip ellipsis (handled above)
            if ch == '.' and i + 2 < len(buf) and buf[i+1] == '.' and buf[i+2] == '.':
                i += 3
                continue

            if ch in HARD_BOUNDARIES:
                if ch == '.':
                    # Check if it's a decimal or abbreviation
                    prefix = buf[:i+1]
                    if _is_number_decimal(prefix):
                        i += 1
                        continue
                    if _is_abbreviation(prefix):
                        i += 1
                        continue

                # Valid hard boundary — record position after it
                split_at = i + 1
                candidate = buf[:split_at]
                if _word_count(candidate.strip()) >= MIN_WORDS:
                    best_hard = split_at
                    break  # Hard boundaries are definitive
                else:
                    # Too short — remember but keep scanning
                    i += 1
                    continue

            if ch in SOFT_BOUNDARIES:
                candidate = buf[:i+1]
                if _word_count(candidate.strip()) >= SOFT_THRESHOLD:
                    best_soft = i + 1

            i += 1

        if best_hard is not None:
            return best_hard
        if best_soft is not None:
            return best_soft
        return None

    def _split_at_max(self) -> None:
        """Force split at MAX_WORDS boundary."""
        words = self._buffer.split()
        if len(words) <= MAX_WORDS:
            return
        chunk_words = words[:MAX_WORDS]
        remaining_words = words[MAX_WORDS:]
        chunk = ' '.join(chunk_words)
        self._buffer = ' '.join(remaining_words)
        self._force_emit(chunk)

    def _emit_all_remaining(self) -> None:
        """Flush everything in the buffer."""
        text = self._buffer.strip()
        self._buffer = ""
        if text:
            self._force_emit(text)


def chunk_text(text: str) -> list[str]:
    """Split complete text into voice-memo-sized chunks.

    Convenience function — strips markdown, feeds all tokens, then flushes.
    """
    # Strip markdown before chunking so syntax doesn't interfere with boundaries
    text = _strip_markdown(text)
    buf = ChunkBuffer()
    # Feed word-by-word to simulate streaming
    words = text.split()
    for i, word in enumerate(words):
        token = word if i == 0 else ' ' + word
        buf.feed(token)
    chunks = buf.get_chunks()
    leftover = buf.flush()
    if leftover:
        chunks.append(leftover)
    return chunks


# --- Demo ---

if __name__ == '__main__':
    sample = (
        "Good morning! I've been thinking about your question, "
        "and I have a few thoughts. First, the weather today looks "
        "beautiful — perfect for a walk. Second, I noticed Dr. Smith "
        "mentioned something interesting about 3.14 being significant. "
        "Isn't that fascinating?\n"
        "Also... I wanted to remind you about the meeting at noon. "
        "Don't forget to bring your notes; they'll be important. "
        "Have a **wonderful** day!"
    )

    print("=== ChunkBuffer Demo ===")
    print(f"Input text ({len(sample.split())} words):\n")
    print(f"  {sample!r}\n")
    print("Chunks:")

    chunks = chunk_text(sample)
    for i, chunk in enumerate(chunks, 1):
        print(f"  {i}. [{len(chunk.split()):2d}w] {chunk}")

    print(f"\nTotal: {len(chunks)} chunks")
