#!/usr/bin/env python3
"""
speak_and_send.py — the companion's voice. Converts text to speech and sends it
as a Signal voice note to the human.

Uses Piper TTS (local, free, offline) to generate audio, then sends
via signal-cli as an attachment.

Usage:
  python3 speak_and_send.py "Hello the human, I heard your voice note"
  python3 speak_and_send.py --file /path/to/text_file.txt
  python3 speak_and_send.py "Testing" --no-send   (generate audio only, don't send)

Output: Sends voice note via Signal, prints status to stdout
Exit 0: success, Exit 1: error

Dependencies:
  - piper-tts: pip install piper-tts --break-system-packages
  - A Piper voice model (see setup instructions below)
  - signal-cli configured (already done for the companion)
  - ffmpeg for audio conversion

Voice setup (run once):
  mkdir -p ~/piper-voices
  # Download a voice — browse https://rhasspy.github.io/piper-samples/
  # Recommended: en_US-lessac-medium (natural, warm)
  pip install piper-tts --break-system-packages
"""

import sys
import os
import json
import subprocess
import tempfile
import argparse
from datetime import datetime
from pathlib import Path

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
SIGNAL_CONFIG = os.path.join(SCRIPT_DIR, "signal_config.sh")
VOICE_DIR = os.path.expanduser("~/piper-voices")
VOICE_NOTES_DIR = os.path.join(COMPANION_HOME, "senses", "audio", "voice_notes_sent")

# Default voice model — change this after auditioning voices
DEFAULT_VOICE = "en_US-lessac-medium.onnx"

# Piper paths to search
PIPER_PATHS = [
    "piper",  # If installed via pip and in PATH
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


def find_piper():
    """Find the Piper TTS binary."""
    for path in PIPER_PATHS:
        if os.path.exists(path):
            return path
        # Check if it's in PATH
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


def text_to_speech(text, output_path, voice_model=None):
    """Convert text to speech using Piper TTS.
    Returns True on success."""
    piper_bin = find_piper()
    if not piper_bin:
        print("ERROR: Piper TTS not found", file=sys.stderr)
        print("Install: pip install piper-tts --break-system-packages", file=sys.stderr)
        return False

    model = find_voice_model(voice_model)
    if not model:
        print("ERROR: No Piper voice model found", file=sys.stderr)
        print(f"Download one to {VOICE_DIR}/", file=sys.stderr)
        print("Browse: https://rhasspy.github.io/piper-samples/", file=sys.stderr)
        return False

    print(f"Voice: {os.path.basename(model)}", file=sys.stderr)

    # Piper outputs WAV
    wav_path = output_path if output_path.endswith(".wav") else output_path + ".wav"

    try:
        # Pipe text to piper via stdin
        result = subprocess.run(
            [
                piper_bin,
                "--model", model,
                "--output_file", wav_path,
            ],
            input=text,
            capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            print(f"Piper error: {result.stderr}", file=sys.stderr)
            return False

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            print("Piper produced empty output", file=sys.stderr)
            return False

        # Convert WAV to OGG for smaller Signal attachment
        if not output_path.endswith(".wav"):
            ogg_path = output_path
            convert_result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", wav_path,
                    "-c:a", "libopus",
                    "-b:a", "64k",
                    ogg_path
                ],
                capture_output=True, text=True, timeout=30
            )
            # Clean up WAV
            if os.path.exists(wav_path) and wav_path != output_path:
                os.unlink(wav_path)

            if convert_result.returncode == 0 and os.path.exists(ogg_path):
                return True
            else:
                print("OGG conversion failed, using WAV", file=sys.stderr)
                # Fall back to WAV — still works as Signal attachment
                return os.path.exists(wav_path)

        return True

    except subprocess.TimeoutExpired:
        print("TTS timed out", file=sys.stderr)
        return False
    except Exception as e:
        print(f"TTS failed: {e}", file=sys.stderr)
        return False


def send_via_signal(audio_path, caption="", recipient=None):
    """Send audio file as Signal attachment."""
    config = load_signal_config()
    if not config:
        return False

    target = recipient or config["human"]

    cmd = [
        "flock", "-w", "30", "/tmp/signal_send.lock",
        "signal-cli", "-a", config["companion"],
        "send", target,
        "-m", caption,
        "-a", audio_path,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Signal send error: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="the companion speaks — convert text to voice note and send via Signal"
    )
    parser.add_argument("text", nargs="?", help="Text to speak")
    parser.add_argument("--file", help="Read text from file instead")
    parser.add_argument("--voice", help="Piper voice model name or path")
    parser.add_argument("--no-send", action="store_true",
                        help="Generate audio only, don't send via Signal")
    parser.add_argument("--save", action="store_true",
                        help="Keep the audio file after sending")
    parser.add_argument("--recipient",
                        help="Recipient phone number (default: the human)")
    parser.add_argument("--caption", default="",
                        help="Optional text caption to send with the voice note")

    args = parser.parse_args()

    # Get text to speak
    text = args.text
    if args.file:
        with open(args.file) as f:
            text = f.read().strip()
    if not text:
        print("No text to speak", file=sys.stderr)
        sys.exit(1)

    # Ensure output dirs
    os.makedirs(VOICE_NOTES_DIR, exist_ok=True)

    # Generate audio
    now = datetime.now()
    if args.save or args.no_send:
        audio_path = os.path.join(
            VOICE_NOTES_DIR,
            f"voice_{now.strftime('%Y-%m-%d_%H-%M-%S')}.ogg"
        )
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        audio_path = tmp.name
        tmp.close()

    print(f"Generating speech ({len(text)} chars)...", file=sys.stderr)
    if not text_to_speech(text, audio_path, args.voice):
        sys.exit(1)

    file_size = os.path.getsize(audio_path)
    print(f"Audio: {file_size / 1024:.1f}KB", file=sys.stderr)

    # Send via Signal
    if not args.no_send:
        print("Sending voice note...", file=sys.stderr)
        if send_via_signal(audio_path, args.caption, args.recipient):
            print("Voice note sent")
        else:
            print("Failed to send voice note", file=sys.stderr)
            sys.exit(1)

        # Clean up if not saving
        if not args.save and os.path.exists(audio_path):
            os.unlink(audio_path)
    else:
        print(f"Audio saved: {audio_path}")


if __name__ == "__main__":
    main()
