#!/usr/bin/env python3
"""
voice_reply.py — Multi-chunk voice reply pipeline for Companion.

Takes text, chunks it with ChunkBuffer, synthesizes each chunk via Piper TTS,
and sends as Signal voice notes. Replaces the simple single-note path for
VOICE_REPLY in handle_message.sh.

Short text (1-2 chunks): combined into a single voice note.
Long text (3+ chunks): sent as separate voice notes with pacing.

Usage:
  python3 scripts/voice_reply.py "Hello, this is a voice reply"
  python3 scripts/voice_reply.py --file /path/to/text.txt
  echo "text" | python3 scripts/voice_reply.py

Flags:
  --recipient NUMBER   Recipient phone number (default: HUMAN_NUMBER)
  --voice MODEL        Piper voice model name or path
  --no-send            Generate audio only, save to senses/audio/voice_notes_sent/
  --save               Keep audio files after sending
  --caption TEXT        Text message sent with first voice note
"""

import sys
import os
import subprocess
import tempfile
import argparse
import time
from datetime import datetime
from pathlib import Path

# Add parent directory to path so we can import from scripts.voice
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from voice.chunk_buffer import chunk_text

# Config
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
SIGNAL_CONFIG = os.path.join(SCRIPT_DIR, "signal_config.sh")
VOICE_DIR = os.path.expanduser("~/piper-voices")
VOICE_NOTES_DIR = os.path.join(COMPANION_HOME, "senses", "audio", "voice_notes_sent")

# Default voice model
DEFAULT_VOICE = "en_US-lessac-medium.onnx"

# Piper paths to search
PIPER_PATHS = [
    "piper",
    os.path.expanduser("~/.local/bin/piper"),
    "/usr/local/bin/piper",
    os.path.join(COMPANION_HOME, "tools", "piper", "piper"),
]

# Voice model search paths
VOICE_SEARCH_PATHS = [
    VOICE_DIR,
    os.path.expanduser("~/.local/share/piper/voices"),
    os.path.join(COMPANION_HOME, "tools", "piper", "voices"),
]

# Pacing between multi-chunk sends (seconds)
CHUNK_SEND_DELAY = 1.5


def find_piper():
    """Find the Piper TTS binary."""
    for path in PIPER_PATHS:
        if os.path.exists(path):
            return path
        try:
            result = subprocess.run(["which", path], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            continue
    return None


def find_voice_model(voice_name=None):
    """Find a Piper voice model file."""
    name = voice_name or DEFAULT_VOICE

    # Direct path check
    if os.path.exists(name):
        return name

    # Search in known directories
    for search_dir in VOICE_SEARCH_PATHS:
        model_path = os.path.join(search_dir, name)
        if os.path.exists(model_path):
            return model_path

    # Search for any .onnx voice model
    for search_dir in VOICE_SEARCH_PATHS:
        if os.path.exists(search_dir):
            for f in os.listdir(search_dir):
                if f.endswith(".onnx"):
                    return os.path.join(search_dir, f)

    return None


def load_signal_config():
    """Load Signal configuration."""
    config = {}
    if not os.path.exists(SIGNAL_CONFIG):
        print(f"ERROR: Signal config not found at {SIGNAL_CONFIG}", file=sys.stderr)
        return None

    with open(SIGNAL_CONFIG) as f:
        for line in f:
            line = line.strip()
            if line.startswith("COMPANION_NUMBER="):
                config["companion"] = line.split("=", 1)[1].strip('"').strip("'")
            elif line.startswith("HUMAN_NUMBER="):
                config["human"] = line.split("=", 1)[1].strip('"').strip("'")

    if "companion" in config and "human" in config:
        return config
    return None


def synthesize_chunk(text, output_path, piper_bin, model):
    """Synthesize a single text chunk to an OGG audio file.

    Pipeline: text -> piper (WAV) -> ffmpeg (OGG/opus).
    Returns True on success.
    """
    wav_path = output_path + ".wav"

    try:
        # Piper TTS: text -> WAV
        result = subprocess.run(
            [piper_bin, "--model", model, "--output_file", wav_path],
            input=text,
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"Piper error: {result.stderr}", file=sys.stderr)
            return False

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            print("Piper produced empty output", file=sys.stderr)
            return False

        # Convert WAV -> OGG (opus)
        convert_result = subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "64k", output_path],
            capture_output=True, text=True, timeout=30
        )

        # Clean up WAV
        if os.path.exists(wav_path):
            os.unlink(wav_path)

        if convert_result.returncode == 0 and os.path.exists(output_path):
            return True
        else:
            print("OGG conversion failed", file=sys.stderr)
            return False

    except subprocess.TimeoutExpired:
        print("TTS timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"TTS failed: {e}", file=sys.stderr)
        return False
    finally:
        # Always clean up WAV
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def send_voice_note(audio_path, config, recipient, caption=""):
    """Send an audio file as a Signal voice note.

    Returns True on success.
    """
    cmd = [
        "flock", "-w", "30", "/tmp/signal_send.lock",
        "signal-cli", "-a", config["companion"],
        "send", recipient,
        "-m", caption,
        "-a", audio_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        print(f"Signal send error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Companion voice reply — chunked text-to-speech via Signal"
    )
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("--file", help="Read text from file instead")
    parser.add_argument("--voice", help="Piper voice model name or path")
    parser.add_argument("--no-send", action="store_true",
                        help="Generate audio only, don't send via Signal")
    parser.add_argument("--save", action="store_true",
                        help="Keep audio files after sending")
    parser.add_argument("--recipient",
                        help="Recipient phone number (default: HUMAN_NUMBER)")
    parser.add_argument("--caption", default="",
                        help="Text message sent with first voice note")

    args = parser.parse_args()

    # Get text: positional arg > --file > stdin
    text = args.text
    if args.file:
        with open(args.file) as f:
            text = f.read().strip()
    if not text and not sys.stdin.isatty():
        text = sys.stdin.read().strip()
    if not text:
        print("No text to speak", file=sys.stderr)
        sys.exit(1)

    # Resolve recipient
    config = load_signal_config()
    if not args.no_send and not config:
        print("ERROR: Could not load signal config", file=sys.stderr)
        sys.exit(1)
    recipient = args.recipient or (config["human"] if config else None)

    # Find TTS tools
    piper_bin = find_piper()
    if not piper_bin:
        print("ERROR: Piper TTS not found", file=sys.stderr)
        sys.exit(1)

    model = find_voice_model(args.voice)
    if not model:
        print("ERROR: No Piper voice model found", file=sys.stderr)
        sys.exit(1)

    print(f"Voice: {os.path.basename(model)}", file=sys.stderr)

    # Chunk the text
    chunks = chunk_text(text)
    if not chunks:
        print("No chunks produced from text", file=sys.stderr)
        sys.exit(1)

    # Short text (1-2 chunks): combine into single voice note
    if len(chunks) <= 2:
        chunks = [" ".join(chunks)]

    print(f"Chunked into {len(chunks)} segment{'s' if len(chunks) != 1 else ''}",
          file=sys.stderr)

    # Ensure output directory
    os.makedirs(VOICE_NOTES_DIR, exist_ok=True)

    total_start = time.time()
    audio_files = []
    success_count = 0

    for i, chunk in enumerate(chunks, 1):
        label = f"Chunk {i}/{len(chunks)}" if len(chunks) > 1 else "Voice note"
        print(f"{label}: synthesizing...", end=" ", file=sys.stderr, flush=True)

        # Generate audio file path
        now = datetime.now()
        if args.save or args.no_send:
            suffix = f"_{i}" if len(chunks) > 1 else ""
            audio_path = os.path.join(
                VOICE_NOTES_DIR,
                f"voice_{now.strftime('%Y-%m-%d_%H-%M-%S')}{suffix}.ogg"
            )
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
            audio_path = tmp.name
            tmp.close()

        audio_files.append(audio_path)

        # Synthesize
        if not synthesize_chunk(chunk, audio_path, piper_bin, model):
            print("FAILED", file=sys.stderr)
            continue

        # Send or save
        if args.no_send:
            print(f"saved", file=sys.stderr)
            success_count += 1
        else:
            print("sending...", end=" ", file=sys.stderr, flush=True)

            # Caption only on first chunk
            caption = args.caption if i == 1 else ""
            if send_voice_note(audio_path, config, recipient, caption):
                print("done", file=sys.stderr)
                success_count += 1
            else:
                print("SEND FAILED", file=sys.stderr)

            # Clean up temp file if not saving
            if not args.save and os.path.exists(audio_path):
                os.unlink(audio_path)

        # Pace multi-chunk sends to maintain Signal ordering
        if len(chunks) > 1 and i < len(chunks) and not args.no_send:
            time.sleep(CHUNK_SEND_DELAY)

    elapsed = time.time() - total_start
    print(f"Voice reply complete: {success_count} chunk{'s' if success_count != 1 else ''} "
          f"{'saved' if args.no_send else 'sent'} ({elapsed:.1f}s total)", file=sys.stderr)

    if args.no_send and audio_files:
        for f in audio_files:
            if os.path.exists(f):
                print(f"Audio saved: {f}")

    if success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
