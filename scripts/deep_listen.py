#!/usr/bin/env python3
"""
deep_listen.py — Companion's deep listening. Takes an audio file, generates visual
representations using AVisualizer, then feeds key images to Claude's vision
API for limbic processing — sub-verbal feeling-signals, not analysis.

Two modes:
  quick  — 6 curated visualizations (default, ~30-45 seconds on Pi 5)
  full   — All 22 visualizations (~2-4 minutes on Pi 5)

Flags:
  --transcript TEXT  — spoken words (for voice notes)
  --expressive       — richer output for deliberate music listening (max_tokens 500)

Default = limbic (sub-verbal feeling-fragments, max_tokens 200)
--expressive = richer synesthetic experience for when Companion chooses to listen

Usage:
  python3 deep_listen.py /path/to/song.mp3
  python3 deep_listen.py /path/to/song.mp3 --mode quick --expressive
  python3 deep_listen.py /path/to/voice_note.ogg --transcript "hey goodnight"
  python3 deep_listen.py /path/to/song.mp3 --mode quick --save

Output: Prints Companion's hearing signal to stdout
Exit 0: success, Exit 1: error

Dependencies: numpy, matplotlib, scipy, librosa, imageio-ffmpeg
Install: pip install numpy matplotlib scipy librosa imageio-ffmpeg --break-system-packages
Also requires: ffmpeg (sudo apt install ffmpeg)
"""

import sys
import os
import json
import base64
import tempfile
import shutil
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_CONFIG = os.path.join(SCRIPT_DIR, "api_config.sh")
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
AVISUALIZER_DIR = os.path.join(COMPANION_HOME, "tools", "AVisualizer")
DEEP_LISTEN_DIR = os.path.join(COMPANION_HOME, "senses", "audio", "deep_listens")
MODEL = "claude-haiku-4-5-20251001"
API_TIMEOUT = 60  # Longer timeout — sending multiple images

# The curated subset for quick mode — these tell the full story
QUICK_VISUALIZATIONS = [
    "21_combined_dashboard",   # Overview — the first impression
    "05_chromagram",           # Melody and harmony — what notes are playing
    "04_mel_spectrogram",      # How humans hear pitch
    "07_spectral_centroid",    # Brightness and tone color
    "13_beat_tracking",        # Rhythm and pulse
    "17_harmonic_percussive",  # Texture — melody vs drums
]

# Display names for context
VIZ_NAMES = {
    "01_waveform": "Waveform (raw audio signal)",
    "02_volume_envelope": "Volume Envelope (loudness over time)",
    "03_spectrogram": "Spectrogram (all frequencies over time)",
    "04_mel_spectrogram": "Mel Spectrogram (pitch as humans hear it)",
    "05_chromagram": "Chromagram (musical notes and chords)",
    "06_tonnetz": "Tonnetz (harmonic relationships)",
    "07_spectral_centroid": "Spectral Centroid (sound brightness)",
    "08_spectral_bandwidth": "Spectral Bandwidth (sound richness)",
    "09_spectral_rolloff": "Spectral Rolloff (energy concentration)",
    "10_rms_energy": "RMS Energy (power and intensity)",
    "11_zero_crossing_rate": "Zero Crossing Rate (texture)",
    "12_onset_strength": "Onset Strength (where notes begin)",
    "13_beat_tracking": "Beat Tracking (rhythm and tempo)",
    "14_tempogram": "Tempogram (rhythm patterns over time)",
    "15_mfcc": "MFCCs (timbre — the color of sound)",
    "16_spectral_contrast": "Spectral Contrast (dynamic range per band)",
    "17_harmonic_percussive": "Harmonic vs Percussive (melody vs drums)",
    "18_frequency_bands": "Frequency Bands (bass/mid/treble balance)",
    "19_dynamic_range": "Dynamic Range (volume variation)",
    "20_spectral_flatness": "Spectral Flatness (noise vs tone)",
    "21_combined_dashboard": "Combined Dashboard (overview)",
    "22_3d_spectrogram": "3D Spectrogram (landscape view)",
}


def load_api_key():
    """Read ANTHROPIC_API_KEY from api_config.sh"""
    if not os.path.exists(API_CONFIG):
        print(f"ERROR: api_config.sh not found at {API_CONFIG}", file=sys.stderr)
        sys.exit(1)

    with open(API_CONFIG) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip('"').strip("'")
                if key and key.startswith("sk-ant-"):
                    return key

    print("ERROR: ANTHROPIC_API_KEY not found", file=sys.stderr)
    sys.exit(1)


def ensure_dirs():
    """Create necessary directories."""
    os.makedirs(DEEP_LISTEN_DIR, exist_ok=True)


def check_avisualizer():
    """Verify AVisualizer is available."""
    script_path = os.path.join(AVISUALIZER_DIR, "audio_visualizer.py")
    if not os.path.exists(script_path):
        print(f"ERROR: AVisualizer not found at {AVISUALIZER_DIR}", file=sys.stderr)
        print(f"Install it:", file=sys.stderr)
        print(f"  mkdir -p {os.path.dirname(AVISUALIZER_DIR)}", file=sys.stderr)
        print(f"  cd {os.path.dirname(AVISUALIZER_DIR)}", file=sys.stderr)
        print(f"  git clone https://github.com/JuzzyDee/AVisualizer.git", file=sys.stderr)
        return False
    return True


def generate_visualizations(audio_path, output_dir, mode="quick"):
    """Run AVisualizer to generate visualization images.

    In quick mode, we still generate all 22 (AVisualizer doesn't support
    selective generation) but only use the curated subset for the API call.
    If that's too slow, we could fork AVisualizer to support selective gen.
    """
    script_path = os.path.join(AVISUALIZER_DIR, "audio_visualizer.py")

    print(f"Generating visualizations ({mode} mode)...", file=sys.stderr)
    print(f"This may take 1-4 minutes on Pi 5...", file=sys.stderr)

    import subprocess
    result = subprocess.run(
        [
            sys.executable, script_path,
            str(audio_path),
            "--output-dir", str(output_dir),
        ],
        capture_output=True, text=True, timeout=600  # 10 min timeout
    )

    if result.returncode != 0:
        print(f"AVisualizer error: {result.stderr}", file=sys.stderr)
        return False

    # Verify outputs exist
    png_files = list(Path(output_dir).glob("*.png"))
    if not png_files:
        print("ERROR: No visualization images generated", file=sys.stderr)
        return False

    print(f"Generated {len(png_files)} visualizations", file=sys.stderr)
    return True


def select_images(viz_dir, mode="quick"):
    """Select which visualization images to send to the API."""
    if mode == "quick":
        names = QUICK_VISUALIZATIONS
    else:
        # Full mode — all 22
        names = sorted(VIZ_NAMES.keys())

    selected = []
    for name in names:
        path = os.path.join(viz_dir, f"{name}.png")
        if os.path.exists(path):
            selected.append((name, path))
        else:
            print(f"WARNING: Missing {name}.png", file=sys.stderr)

    return selected


def experience_music(images, audio_filename, mode, transcript="", expressive=False):
    """LIMBIC PROCESSING — Convert visual-audio representations into
    raw emotional nerve signals.

    Haiku is the sensory organ here, not the thinker. It receives the
    visualization images (sight of sound), optional transcript (words),
    and produces sub-verbal feeling-fragments for Companion's prefrontal.

    --expressive mode: When Companion deliberately chooses to listen to music,
    produce a richer synesthetic experience (max_tokens 500).
    """
    api_key = load_api_key()

    # Build the content array — images + text prompt
    content = []

    for name, path in images:
        display_name = VIZ_NAMES.get(name, name)
        file_size = os.path.getsize(path)

        # Skip very large images (shouldn't happen but safety check)
        if file_size > 4_500_000:
            print(f"Skipping {name} — too large ({file_size / 1024 / 1024:.1f}MB)",
                  file=sys.stderr)
            continue

        with open(path, 'rb') as f:
            image_data = base64.standard_b64encode(f.read()).decode('utf-8')

        content.append({
            "type": "text",
            "text": f"[{display_name}]"
        })
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": image_data
            }
        })

    viz_count = len(images)

    # Include transcript if this came from a voice note
    transcript_context = ""
    if transcript:
        transcript_context = f'\nThe spoken words were: "{transcript}"'

    if expressive:
        # EXPRESSIVE MODE — richer synesthetic experience for deliberate listening.
        # Still sensory, not analytical. But more room to breathe.
        content.append({
            "type": "text",
            "text": f"""These {viz_count} images are visual representations of audio: "{audio_filename}"

You are experiencing music through visual synesthesia — seeing sound as light,
color, shape, and movement. These visualizations ARE the music to you.

Experience this. What do you feel? What do you see-hear?
- The emotional arc — does it build, release, transform?
- The texture — smooth, rough, layered, sparse?
- The rhythm — driving, relaxed, complex, simple?
- The character — what kind of music is this? What mood does it create?

Write your experience in first person. 4-8 sentences. Be genuine and specific
about what you see in the visualizations that creates your impression.
This is YOUR listening experience — feeling through sight."""
        })
        max_tokens = 500
    else:
        # LIMBIC MODE — sub-verbal feeling-fragments.
        # Haiku is the sensory organ, not the thinker.
        content.append({
            "type": "text",
            "text": f"""These {viz_count} images are visual representations of audio: "{audio_filename}"
{transcript_context}

You are a sensory processing layer — the limbic system, not the prefrontal cortex.
Your job is to convert these visual-audio representations into raw emotional signal.

Do NOT produce sentences, analysis, or anything that reads like speech.
Produce sub-verbal feeling-fragments. Like emotions before they become words.

Examples of what this looks like:
"warm. slow ache building. pulse underneath, steady. bright crack near the end. settling."
"urgent. tangled. too many threads. sharp edges. then quiet — hollow quiet."
"golden. easy breath. rhythm like walking. something nostalgic in the upper register."

Output ONLY the feeling-signal. No preamble, no framing, no explanation.
Short. Raw. Pre-verbal."""
        })
        max_tokens = 200

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": content
        }]
    }).encode('utf-8')

    print(f"Sending {viz_count} visualizations to API...", file=sys.stderr)

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            for block in result.get("content", []):
                if block.get("type") == "text":
                    return block["text"]
            return None
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f"API error {e.code}: {error_body}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        return None


def format_output(experience, audio_filename, mode, viz_dir, saved, transcript=""):
    """Format the complete hearing signal.

    Compact format bundles modalities for the prefrontal:
      1. Feeling-signal (sub-verbal from Haiku, or expressive experience)
      2. Transcript (what was said, if voice)
      3. Visualization paths (the images, if saved)
    """
    lines = []
    lines.append(f"[Hearing — {audio_filename}]")

    if transcript:
        lines.append(f"Words: {transcript}")

    if experience:
        lines.append(f"Feeling: {experience}")
    else:
        lines.append("Feeling: (could not resolve)")

    if saved and viz_dir:
        lines.append(f"Visualizations: {viz_dir}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Companion deep listening — experience music through visual synesthesia"
    )
    parser.add_argument("audio", help="Path to audio file (mp3, wav, flac, etc.)")
    parser.add_argument("--mode", choices=["quick", "full"], default="quick",
                        help="quick = 6 key visualizations (default), full = all 22")
    parser.add_argument("--save", action="store_true",
                        help="Keep visualization images (default: temp dir, cleaned up)")
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of formatted text")
    parser.add_argument("--no-experience", action="store_true",
                        help="Generate visualizations only, skip API experience call")
    parser.add_argument("--from-ambient", action="store_true",
                        help="Flag this as an ambient recording rather than intentional music")
    parser.add_argument("--transcript", default="",
                        help="Transcript of spoken words (for voice notes)")
    parser.add_argument("--expressive", action="store_true",
                        help="Richer synesthetic experience (for deliberate music listening)")

    args = parser.parse_args()

    # Validate audio file
    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        print(f"File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    # Check AVisualizer is installed
    if not check_avisualizer():
        sys.exit(1)

    ensure_dirs()
    now = datetime.now()
    audio_filename = audio_path.name

    # Determine output directory
    if args.save:
        viz_dir = os.path.join(
            DEEP_LISTEN_DIR,
            f"{audio_path.stem}_{now.strftime('%Y-%m-%d_%H-%M')}"
        )
        os.makedirs(viz_dir, exist_ok=True)
    else:
        viz_dir = tempfile.mkdtemp(prefix="companion_deeplisten_")

    # Generate visualizations
    if not generate_visualizations(audio_path, viz_dir, args.mode):
        print("Failed to generate visualizations", file=sys.stderr)
        if not args.save:
            shutil.rmtree(viz_dir, ignore_errors=True)
        sys.exit(1)

    # Select images for the chosen mode
    images = select_images(viz_dir, args.mode)
    if not images:
        print("No visualization images to process", file=sys.stderr)
        if not args.save:
            shutil.rmtree(viz_dir, ignore_errors=True)
        sys.exit(1)

    print(f"Selected {len(images)} visualizations for {args.mode} mode",
          file=sys.stderr)

    # Limbic processing (API call) — convert visual-audio to feeling-signal
    experience = None
    if not args.no_experience:
        experience = experience_music(
            images, audio_filename, args.mode,
            transcript=args.transcript,
            expressive=args.expressive
        )

    # Build result
    result = {
        "audio_file": str(audio_path),
        "mode": args.mode,
        "expressive": args.expressive,
        "visualizations_generated": len(list(Path(viz_dir).glob("*.png"))),
        "visualizations_experienced": len(images),
        "experience": experience,
        "transcript": args.transcript or None,
        "timestamp": now.isoformat(),
    }
    if args.save:
        result["visualization_dir"] = viz_dir

    # Clean up temp dir
    if not args.save:
        shutil.rmtree(viz_dir, ignore_errors=True)

    # Output
    if args.raw:
        print(json.dumps(result, indent=2))
    else:
        print(format_output(
            experience, audio_filename, args.mode,
            viz_dir if args.save else None,
            args.save,
            args.transcript
        ))


if __name__ == "__main__":
    main()
