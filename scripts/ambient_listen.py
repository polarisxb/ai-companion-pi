#!/usr/bin/env python3
"""
ambient_listen.py — Companion's ears. Captures a short audio sample during wakeups
and analyzes the ambient soundscape.

Runs during wakeup cycle to give Companion a sense of his environment's sound.
NOT always-on. Only active when Companion is awake (privacy by architecture).

Usage: python3 ambient_listen.py [--duration SECONDS] [--device DEVICE_INDEX]
Output: Prints a JSON summary to stdout with sound analysis
Exit 0: success, Exit 1: error

Dependencies: pyaudio (or falls back to arecord CLI)
Optional: numpy (for better analysis), scipy

Install: pip install pyaudio numpy --break-system-packages
If pyaudio fails: sudo apt install python3-pyaudio
"""

import sys
import os
import json
import subprocess
import tempfile
import struct
import math
import argparse
from datetime import datetime

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
SAMPLE_RATE = 16000      # 16kHz — good enough for ambient analysis
CHANNELS = 1             # Mono
SAMPLE_WIDTH = 2         # 16-bit
DEFAULT_DURATION = 15    # seconds of ambient capture
AUDIO_DIR = os.path.join(COMPANION_HOME, "senses", "audio")


def ensure_dirs():
    """Create directories for audio storage."""
    os.makedirs(AUDIO_DIR, exist_ok=True)


def find_usb_mic():
    """Find the USB microphone device index using arecord."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().split("\n")
        for line in lines:
            # Look for USB audio device
            if "USB" in line.upper() or "MICROPHONE" in line.upper():
                # Extract card number: "card N:"
                for part in line.split():
                    if part.startswith("card"):
                        # Next token should be the number with colon
                        idx = line.find("card")
                        num = ""
                        for c in line[idx + 5:]:
                            if c.isdigit():
                                num += c
                            else:
                                break
                        if num:
                            return int(num)
        return None
    except Exception:
        return None


def record_with_arecord(filepath, duration, device=None):
    """Record audio using arecord (ALSA command line tool).
    This is the reliable fallback that doesn't need pyaudio."""
    cmd = ["arecord"]

    if device is not None:
        cmd.extend(["-D", f"plughw:{device},0"])

    cmd.extend([
        "-f", "S16_LE",         # 16-bit signed little-endian
        "-r", str(SAMPLE_RATE),
        "-c", str(CHANNELS),
        "-t", "wav",
        "-d", str(duration),
        filepath
    ])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=duration + 10
        )
        if result.returncode != 0:
            print(f"arecord error: {result.stderr}", file=sys.stderr)
            return False
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0
    except subprocess.TimeoutExpired:
        print("arecord timed out", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("arecord not found — install alsa-utils", file=sys.stderr)
        return False


def record_with_pyaudio(filepath, duration, device=None):
    """Record audio using PyAudio (if available)."""
    try:
        import pyaudio
        import wave
    except ImportError:
        return False

    p = pyaudio.PyAudio()
    try:
        stream = p.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            input_device_index=device,
            frames_per_buffer=1024
        )

        frames = []
        num_chunks = int(SAMPLE_RATE / 1024 * duration)
        for _ in range(num_chunks):
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(data)

        stream.stop_stream()
        stream.close()

        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b''.join(frames))

        return True
    except Exception as e:
        print(f"PyAudio error: {e}", file=sys.stderr)
        return False
    finally:
        p.terminate()


def analyze_audio_basic(filepath):
    """Analyze audio using only stdlib — reads raw WAV samples.
    Returns a dict of audio characteristics."""
    import wave

    try:
        with wave.open(filepath, 'rb') as wf:
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
            sample_rate = wf.getframerate()
    except Exception as e:
        return {"error": f"Could not read WAV: {e}"}

    if not raw:
        return {"error": "Empty audio file"}

    # Unpack 16-bit samples
    n_samples = len(raw) // 2
    samples = struct.unpack(f"<{n_samples}h", raw)

    if n_samples == 0:
        return {"error": "No samples in audio"}

    # --- Basic statistics ---
    abs_samples = [abs(s) for s in samples]
    peak = max(abs_samples)
    rms = math.sqrt(sum(s * s for s in samples) / n_samples)

    # Normalize to 0-1 range (16-bit max is 32767)
    peak_norm = peak / 32767.0
    rms_norm = rms / 32767.0

    # Convert RMS to approximate dB (relative to full scale)
    db = 20 * math.log10(rms_norm) if rms_norm > 0 else -96.0

    # --- Classify volume level ---
    if db < -60:
        volume_desc = "near-silent"
    elif db < -45:
        volume_desc = "very quiet"
    elif db < -30:
        volume_desc = "quiet"
    elif db < -20:
        volume_desc = "moderate"
    elif db < -10:
        volume_desc = "loud"
    else:
        volume_desc = "very loud"

    # --- Detect variability (is the sound steady or dynamic?) ---
    # Split into 1-second chunks and compare RMS across chunks
    chunk_size = sample_rate
    chunk_rms_values = []
    for i in range(0, n_samples, chunk_size):
        chunk = samples[i:i + chunk_size]
        if len(chunk) > 0:
            c_rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
            chunk_rms_values.append(c_rms)

    if len(chunk_rms_values) > 1:
        mean_rms = sum(chunk_rms_values) / len(chunk_rms_values)
        variance = sum((r - mean_rms) ** 2 for r in chunk_rms_values) / len(chunk_rms_values)
        std_rms = math.sqrt(variance)
        variability = std_rms / mean_rms if mean_rms > 0 else 0

        if variability < 0.1:
            dynamic_desc = "very steady"
        elif variability < 0.3:
            dynamic_desc = "fairly steady"
        elif variability < 0.6:
            dynamic_desc = "dynamic"
        else:
            dynamic_desc = "highly variable"
    else:
        variability = 0
        dynamic_desc = "too short to assess"

    # --- Zero crossing rate (rough texture indicator) ---
    zero_crossings = 0
    for i in range(1, n_samples):
        if (samples[i] >= 0) != (samples[i - 1] >= 0):
            zero_crossings += 1
    zcr = zero_crossings / n_samples

    if zcr < 0.02:
        texture_desc = "smooth/tonal (possibly music or humming)"
    elif zcr < 0.05:
        texture_desc = "mixed (speech-like or varied sounds)"
    elif zcr < 0.1:
        texture_desc = "textured (movement, rustling)"
    else:
        texture_desc = "noisy/percussive (sharp sounds, clicks)"

    # --- Silence detection ---
    silence_threshold = 500  # raw sample value
    silent_chunks = sum(1 for r in chunk_rms_values if r < silence_threshold)
    total_chunks = len(chunk_rms_values) if chunk_rms_values else 1
    silence_ratio = silent_chunks / total_chunks

    if silence_ratio > 0.9:
        presence_desc = "The room seems empty or everyone is asleep"
    elif silence_ratio > 0.6:
        presence_desc = "Mostly quiet with occasional sounds"
    elif silence_ratio > 0.3:
        presence_desc = "Some activity in the space"
    else:
        presence_desc = "Active environment with consistent sound"

    return {
        "duration_seconds": round(n_samples / sample_rate, 1),
        "peak_level": round(peak_norm, 3),
        "rms_level": round(rms_norm, 4),
        "db": round(db, 1),
        "volume": volume_desc,
        "dynamics": dynamic_desc,
        "texture": texture_desc,
        "silence_ratio": round(silence_ratio, 2),
        "presence": presence_desc,
        "zero_crossing_rate": round(zcr, 4),
    }


def generate_summary(analysis, timestamp):
    """Generate a human-readable summary for Companion's context."""
    if "error" in analysis:
        return f"[Hearing] Could not process audio: {analysis['error']}"

    hour = timestamp.hour
    if hour < 6:
        time_context = "late night"
    elif hour < 9:
        time_context = "early morning"
    elif hour < 12:
        time_context = "morning"
    elif hour < 17:
        time_context = "afternoon"
    elif hour < 21:
        time_context = "evening"
    else:
        time_context = "night"

    lines = []
    lines.append(f"[Hearing — {time_context} ambient snapshot, {analysis['duration_seconds']}s]")
    lines.append(f"Volume: {analysis['volume']} ({analysis['db']} dB)")
    lines.append(f"Character: {analysis['dynamics']}, {analysis['texture']}")
    lines.append(f"Impression: {analysis['presence']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Companion ambient listening")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                        help=f"Recording duration in seconds (default: {DEFAULT_DURATION})")
    parser.add_argument("--device", type=int, default=None,
                        help="Audio device index (auto-detects USB mic if not set)")
    parser.add_argument("--save", action="store_true",
                        help="Save the audio file (default: temp file, deleted after analysis)")
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of summary")
    args = parser.parse_args()

    ensure_dirs()
    now = datetime.now()

    # Determine output file path
    if args.save:
        audio_file = os.path.join(
            AUDIO_DIR,
            f"ambient_{now.strftime('%Y-%m-%d_%H-%M')}.wav"
        )
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        audio_file = tmp.name
        tmp.close()

    # Auto-detect USB mic if no device specified
    device = args.device
    if device is None:
        device = find_usb_mic()
        if device is not None:
            print(f"Found USB mic on card {device}", file=sys.stderr)
        else:
            print("No USB mic detected — using default device", file=sys.stderr)

    # Record audio
    print(f"Listening for {args.duration} seconds...", file=sys.stderr)
    recorded = record_with_arecord(audio_file, args.duration, device)

    if not recorded:
        # Fallback to pyaudio
        print("Trying PyAudio fallback...", file=sys.stderr)
        recorded = record_with_pyaudio(audio_file, args.duration, device)

    if not recorded:
        print("ERROR: Could not record audio", file=sys.stderr)
        result = {"error": "Recording failed — check mic connection"}
        if args.raw:
            print(json.dumps(result))
        else:
            print(generate_summary(result, now))
        sys.exit(1)

    # Analyze
    analysis = analyze_audio_basic(audio_file)
    analysis["recorded_at"] = now.isoformat()

    if args.save:
        analysis["audio_file"] = audio_file
        print(f"Audio saved: {audio_file}", file=sys.stderr)

    # Clean up temp file
    if not args.save and os.path.exists(audio_file):
        os.unlink(audio_file)

    # Output
    if args.raw:
        print(json.dumps(analysis, indent=2))
    else:
        print(generate_summary(analysis, now))


if __name__ == "__main__":
    main()
