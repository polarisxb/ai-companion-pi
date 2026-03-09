#!/usr/bin/env python3
"""
speak.py — Companion speaks into the room through the speakers.

Uses Piper TTS to generate speech, plays through PipeWire (Pebble V3 via Bluetooth).

Usage:
  python3 speak.py "Hello the human"
  python3 speak.py --file /path/to/text.txt
  python3 speak.py "Testing" --save         (also save the WAV file)
"""

import sys
import os
import subprocess
import tempfile
import argparse
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
VOICE_DIR = os.path.expanduser("~/piper-voices")
VOICE_NOTES_DIR = os.path.join(COMPANION_HOME, "senses", "audio", "voice_notes_sent")
DEFAULT_VOICE = "en_US-lessac-medium.onnx"
PIPER_PATHS = [
    "piper",
    os.path.expanduser("~/.local/bin/piper"),
    "/usr/local/bin/piper",
    os.path.join(COMPANION_HOME, "tools", "piper", "piper"),
]

VOICE_SEARCH_PATHS = [
    VOICE_DIR,
    os.path.expanduser("~/.local/share/piper/voices"),
    os.path.join(COMPANION_HOME, "tools", "piper", "voices"),
]


def find_piper():
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
    name = voice_name or DEFAULT_VOICE
    if os.path.exists(name):
        return name
    for search_dir in VOICE_SEARCH_PATHS:
        model_path = os.path.join(search_dir, name)
        if os.path.exists(model_path):
            return model_path
    for search_dir in VOICE_SEARCH_PATHS:
        if os.path.exists(search_dir):
            for f in os.listdir(search_dir):
                if f.endswith(".onnx"):
                    return os.path.join(search_dir, f)
    return None


def speak(text, voice_model=None, save=False):
    """Generate speech and play through speakers. Returns True on success."""
    piper_bin = find_piper()
    if not piper_bin:
        print("ERROR: Piper TTS not found", file=sys.stderr)
        return False

    model = find_voice_model(voice_model)
    if not model:
        print("ERROR: No Piper voice model found", file=sys.stderr)
        return False

    if save:
        os.makedirs(VOICE_NOTES_DIR, exist_ok=True)
        wav_path = os.path.join(
            VOICE_NOTES_DIR,
            f"room_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.wav"
        )
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_path = tmp.name
        tmp.close()

    try:
        result = subprocess.run(
            [piper_bin, "--model", model, "--output_file", wav_path],
            input=text, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"Piper error: {result.stderr}", file=sys.stderr)
            return False

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            print("Piper produced empty output", file=sys.stderr)
            return False

        play_result = subprocess.run(
            ["pw-play", wav_path],
            capture_output=True, text=True, timeout=120
        )
        if play_result.returncode != 0:
            print(f"Playback error: {play_result.stderr}", file=sys.stderr)
            return False

        if save:
            print(f"Saved: {wav_path}")
        return True

    except subprocess.TimeoutExpired:
        print("Timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        return False
    finally:
        if not save and os.path.exists(wav_path):
            os.unlink(wav_path)


def main():
    parser = argparse.ArgumentParser(description="Companion speaks into the room")
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("--file", help="Read text from file")
    parser.add_argument("--voice", help="Piper voice model name or path")
    parser.add_argument("--save", action="store_true", help="Save the audio file")

    args = parser.parse_args()

    text = args.text
    if args.file:
        with open(args.file) as f:
            text = f.read().strip()
    if not text:
        print("No text to speak", file=sys.stderr)
        sys.exit(1)

    print(f"Speaking ({len(text)} chars)...", file=sys.stderr)
    if speak(text, args.voice, args.save):
        print("Done")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
