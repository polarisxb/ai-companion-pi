#!/usr/bin/env python3
"""
transcribe_audio.py — Transcribe audio to text for the companion.
Used by signal_listener.sh when the human sends a voice note.

Tries multiple backends in order:
  1. Whisper.cpp (local, free, private)
  2. OpenAI Whisper API (fast, costs ~$0.006/min)
  3. Fallback: return empty (audio saved but not transcribed)

Usage: python3 transcribe_audio.py /path/to/audio.ogg [--backend auto|local|api]
Output: Prints transcript to stdout
Exit 0: success (even if empty), Exit 1: error

No pip dependencies for API mode — uses stdlib.
Local mode needs whisper.cpp installed.
"""

import sys
import os
import json
import subprocess
import tempfile
import argparse
import urllib.request
import urllib.error

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_CONFIG = os.path.join(SCRIPT_DIR, "api_config.sh")
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")

# Whisper.cpp paths (adjust if installed elsewhere)
# Modern whisper.cpp uses "whisper-cli" — "main" is a deprecated wrapper that just exits
WHISPER_CPP_MAIN = os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli")
WHISPER_CPP_MODEL = os.path.expanduser("~/whisper.cpp/models/ggml-small.bin")

# Alternate whisper.cpp paths
WHISPER_CPP_ALT_PATHS = [
    os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli"),
    "/usr/local/bin/whisper-cli",
    "/usr/local/bin/whisper-cpp",
    os.path.join(COMPANION_HOME, "tools", "whisper.cpp", "build", "bin", "whisper-cli"),
]

WHISPER_MODEL_ALT_PATHS = [
    os.path.join(COMPANION_HOME, "tools", "whisper.cpp", "models", "ggml-small.bin"),
    os.path.expanduser("~/whisper.cpp/models/ggml-base.bin"),
    os.path.join(COMPANION_HOME, "tools", "whisper.cpp", "models", "ggml-base.bin"),
]


def find_whisper_cpp():
    """Find whisper.cpp binary and model."""
    # Find binary
    binary = None
    if os.path.exists(WHISPER_CPP_MAIN):
        binary = WHISPER_CPP_MAIN
    else:
        for path in WHISPER_CPP_ALT_PATHS:
            if os.path.exists(path):
                binary = path
                break

    if not binary:
        # Try which
        try:
            result = subprocess.run(["which", "whisper-cpp"], capture_output=True, text=True)
            if result.returncode == 0:
                binary = result.stdout.strip()
        except Exception:
            pass

    # Find model
    model = None
    if os.path.exists(WHISPER_CPP_MODEL):
        model = WHISPER_CPP_MODEL
    else:
        for path in WHISPER_MODEL_ALT_PATHS:
            if os.path.exists(path):
                model = path
                break

    return binary, model


def convert_to_wav(input_path):
    """Convert any audio format to 16kHz mono WAV for Whisper.
    Signal voice notes are typically .ogg or .aac."""
    wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = wav_tmp.name
    wav_tmp.close()

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_path,
                "-ar", "16000",     # 16kHz sample rate
                "-ac", "1",         # Mono
                "-f", "wav",
                wav_path
            ],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(wav_path):
            return wav_path
        else:
            print(f"ffmpeg conversion error: {result.stderr}", file=sys.stderr)
            return None
    except FileNotFoundError:
        print("ffmpeg not found — install with: sudo apt install ffmpeg", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("ffmpeg conversion timed out", file=sys.stderr)
        return None


def transcribe_local(audio_path):
    """Transcribe using local Whisper.cpp."""
    binary, model = find_whisper_cpp()
    if not binary or not model:
        return None

    # Convert to WAV if needed
    ext = os.path.splitext(audio_path)[1].lower()
    if ext != ".wav":
        wav_path = convert_to_wav(audio_path)
        if not wav_path:
            return None
        cleanup_wav = True
    else:
        wav_path = audio_path
        cleanup_wav = False

    try:
        print("Transcribing with Whisper.cpp (local)...", file=sys.stderr)
        result = subprocess.run(
            [
                binary,
                "-m", model,
                "-f", wav_path,
                "--no-timestamps",
                "--language", "en",
                "--threads", "4",
            ],
            capture_output=True, text=True, timeout=120  # 2 min timeout
        )

        if result.returncode == 0:
            # whisper.cpp outputs to stderr for progress, stdout for transcript
            transcript = result.stdout.strip()
            if not transcript:
                # Some versions output everything to stderr
                transcript = result.stderr.strip()
                # Filter out progress lines
                lines = []
                for line in transcript.split("\n"):
                    # Skip whisper.cpp metadata/progress lines
                    if line.startswith("[") or line.startswith("whisper_") or \
                       line.startswith("main:") or line.startswith("system_info"):
                        continue
                    if line.strip():
                        lines.append(line.strip())
                transcript = " ".join(lines)

            return transcript if transcript else None
        else:
            print(f"Whisper.cpp error: {result.stderr[:200]}", file=sys.stderr)
            return None

    except subprocess.TimeoutExpired:
        print("Whisper.cpp timed out (>2 minutes)", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Whisper.cpp failed: {e}", file=sys.stderr)
        return None
    finally:
        if cleanup_wav and wav_path and os.path.exists(wav_path):
            os.unlink(wav_path)


def load_api_key():
    """Read API key — used for potential future Whisper API support."""
    if not os.path.exists(API_CONFIG):
        return None
    with open(API_CONFIG) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip('"').strip("'")
    return None


def transcribe_api_whisper(audio_path):
    """Transcribe using OpenAI Whisper API.
    Requires OPENAI_API_KEY in api_config.sh.
    This is a fallback — local is preferred."""

    # Check for OpenAI key
    openai_key = None
    if os.path.exists(API_CONFIG):
        with open(API_CONFIG) as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    openai_key = line.split("=", 1)[1].strip('"').strip("'")

    if not openai_key:
        print("No OPENAI_API_KEY found — cannot use Whisper API", file=sys.stderr)
        return None

    # Convert to WAV for consistent handling
    ext = os.path.splitext(audio_path)[1].lower()
    if ext not in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
        wav_path = convert_to_wav(audio_path)
        if not wav_path:
            return None
        upload_path = wav_path
        cleanup = True
    else:
        upload_path = audio_path
        cleanup = False

    try:
        print("Transcribing with Whisper API...", file=sys.stderr)

        # Build multipart form data manually (no requests dependency)
        boundary = "----the companion_Whisper_Boundary"
        body = b""

        # Model field
        body += f"--{boundary}\r\n".encode()
        body += b"Content-Disposition: form-data; name=\"model\"\r\n\r\n"
        body += b"whisper-1\r\n"

        # File field
        filename = os.path.basename(upload_path)
        body += f"--{boundary}\r\n".encode()
        body += f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n".encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        with open(upload_path, 'rb') as f:
            body += f.read()
        body += b"\r\n"

        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get("text", "").strip() or None

    except Exception as e:
        print(f"Whisper API error: {e}", file=sys.stderr)
        return None
    finally:
        if cleanup and upload_path and os.path.exists(upload_path):
            os.unlink(upload_path)


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio for the companion")
    parser.add_argument("audio", help="Path to audio file")
    parser.add_argument("--backend", choices=["auto", "local", "api"],
                        default="auto",
                        help="Transcription backend (default: auto — tries local first)")
    args = parser.parse_args()

    audio_path = args.audio
    if not os.path.exists(audio_path):
        print(f"File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(audio_path)
    if file_size == 0:
        print("(Empty audio file)", file=sys.stderr)
        sys.exit(0)

    transcript = None

    if args.backend in ("auto", "local"):
        transcript = transcribe_local(audio_path)
        if transcript:
            print(transcript)
            return

        if args.backend == "local":
            print("(Local transcription unavailable — whisper.cpp not found)",
                  file=sys.stderr)
            sys.exit(0)

    if args.backend in ("auto", "api"):
        transcript = transcribe_api_whisper(audio_path)
        if transcript:
            print(transcript)
            return

    # Both failed — not an error, just unavailable
    print("(Voice note received but transcription is not available yet)",
          file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
