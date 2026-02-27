#!/usr/bin/env python3
"""
subtitle_parser.py — Parse .vtt subtitles into timed segments for date night reactions.

Usage:
    subtitle_parser.py input.vtt [--segment-length 150] [--output segments.json]

Outputs JSON segments of ~150 seconds each:
[
  {
    "index": 0,
    "start_sec": 0,
    "end_sec": 150,
    "start_display": "0:00",
    "end_display": "2:30",
    "text": "..."
  }
]
"""

import sys
import json
import re
import argparse


def parse_timestamp(ts):
    """Convert VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to seconds."""
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return 0.0


def format_display(seconds):
    """Convert seconds to display format like 2:30 or 1:05:30."""
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def strip_tags(text):
    """Remove HTML/VTT tags like <c>, </c>, <b>, etc."""
    return re.sub(r"<[^>]+>", "", text)


def deduplicate_lines(lines):
    """Remove consecutive duplicate lines common in auto-generated subs."""
    deduped = []
    prev = None
    for line in lines:
        cleaned = line.strip()
        if cleaned and cleaned != prev:
            deduped.append(cleaned)
            prev = cleaned
    return deduped


def clean_auto_sub_text(text):
    """Clean up auto-generated subtitle artifacts: repeated phrases, excess tags."""
    # Remove [Music], [Applause] etc. duplicates
    text = re.sub(r"(\[(?:Music|Applause|Laughter)\]\s*){2,}", r"\1", text)
    # Remove lines that are just tags
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are only VTT metadata
        if re.match(r"^(align:|position:|size:)", stripped):
            continue
        cleaned.append(stripped)
    return " ".join(cleaned)


def parse_vtt(vtt_path):
    """Parse a VTT file into a list of (start_sec, end_sec, text) cues."""
    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split into cue blocks
    # VTT format: optional cue id, then timestamp line, then text lines
    cue_pattern = re.compile(
        r"(\d[\d:.]*)\s*-->\s*(\d[\d:.]*)[^\n]*\n((?:(?!\n\n|\d[\d:.]*\s*-->).+\n?)*)",
        re.MULTILINE,
    )

    cues = []
    for match in cue_pattern.finditer(content):
        start = parse_timestamp(match.group(1))
        end = parse_timestamp(match.group(2))
        text = strip_tags(match.group(3).strip())
        if text:
            cues.append((start, end, text))

    return cues


def build_segments(cues, segment_length=150):
    """Group cues into timed segments of approximately segment_length seconds."""
    if not cues:
        return []

    segments = []
    current_texts = []
    seg_start = 0.0
    seg_end = segment_length

    for start, end, text in cues:
        # If this cue starts past the current segment boundary, finalize segment
        while start >= seg_end and current_texts:
            combined = clean_auto_sub_text(" ".join(deduplicate_lines(current_texts)))
            segments.append({
                "index": len(segments),
                "start_sec": seg_start,
                "end_sec": seg_end,
                "start_display": format_display(seg_start),
                "end_display": format_display(seg_end),
                "text": combined if combined.strip() else "[no dialogue]",
            })
            seg_start = seg_end
            seg_end = seg_start + segment_length
            current_texts = []

        current_texts.append(text)

    # Finalize last segment
    if current_texts:
        actual_end = max(seg_end, cues[-1][1]) if cues else seg_end
        combined = clean_auto_sub_text(" ".join(deduplicate_lines(current_texts)))
        segments.append({
            "index": len(segments),
            "start_sec": seg_start,
            "end_sec": actual_end,
            "start_display": format_display(seg_start),
            "end_display": format_display(actual_end),
            "text": combined if combined.strip() else "[no dialogue]",
        })

    # Mark empty segments
    for seg in segments:
        if not seg["text"].strip() or len(seg["text"].strip()) < 5:
            seg["text"] = "[no dialogue]"

    return segments


def main():
    parser = argparse.ArgumentParser(description="Parse VTT subtitles into timed segments")
    parser.add_argument("vtt_file", help="Path to .vtt subtitle file")
    parser.add_argument("--segment-length", type=int, default=150,
                        help="Target segment length in seconds (default: 150)")
    parser.add_argument("--output", help="Output JSON file (default: stdout)")

    args = parser.parse_args()

    cues = parse_vtt(args.vtt_file)
    segments = build_segments(cues, args.segment_length)

    output = json.dumps(segments, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(json.dumps({"segments": len(segments), "output": args.output}))
    else:
        print(output)


if __name__ == "__main__":
    main()
