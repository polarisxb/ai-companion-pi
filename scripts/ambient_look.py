#!/usr/bin/env python3
"""
ambient_look.py — Companion's eyes. Captures a photo during wakeups and describes
what he sees using the Claude API.

Runs during wakeup cycle. Takes a snapshot with the Pi camera, sends it to
Claude Haiku for a description, and outputs a summary for Companion's context.

Usage: python3 ambient_look.py [--save] [--raw]
Output: Prints a visual summary to stdout
Exit 0: success, Exit 1: error

Dependencies: libcamera-still (pre-installed on Pi OS) or picamera2
No pip dependencies for capture — uses CLI tools.
Claude API call uses stdlib only (like describe_image.py).
"""

import sys
import os
import json
import subprocess
import tempfile
import base64
import urllib.request
import urllib.error
from datetime import datetime

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_CONFIG = os.path.join(SCRIPT_DIR, "api_config.sh")
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
VISION_DIR = os.path.join(COMPANION_HOME, "senses", "vision")
MODEL = "claude-haiku-4-5-20251001"
API_TIMEOUT = 30

# Camera settings
CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720
CAPTURE_QUALITY = 80  # JPEG quality — balance between size and detail


def ensure_dirs():
    """Create directories for vision storage."""
    os.makedirs(VISION_DIR, exist_ok=True)


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

    print("ERROR: ANTHROPIC_API_KEY not found in api_config.sh", file=sys.stderr)
    sys.exit(1)


def capture_libcamera(filepath):
    """Capture using libcamera-still (Pi OS Bookworm default)."""
    cmd = [
        "libcamera-still",
        "-o", filepath,
        "--width", str(CAPTURE_WIDTH),
        "--height", str(CAPTURE_HEIGHT),
        "-q", str(CAPTURE_QUALITY),
        "-t", "2000",          # 2 second preview to let auto-exposure settle
        "--nopreview",         # No desktop preview window
        "-n",                  # No preview
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(filepath):
            size = os.path.getsize(filepath)
            if size > 0:
                print(f"Captured {size / 1024:.0f}KB image", file=sys.stderr)
                return True
        print(f"libcamera-still error: {result.stderr}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("libcamera-still not found", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("Camera capture timed out", file=sys.stderr)
        return False


def capture_rpicam(filepath):
    """Capture using rpicam-still (newer Pi OS naming)."""
    cmd = [
        "rpicam-still",
        "-o", filepath,
        "--width", str(CAPTURE_WIDTH),
        "--height", str(CAPTURE_HEIGHT),
        "-q", str(CAPTURE_QUALITY),
        "-t", "2000",
        "--nopreview",
        "-n",
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(filepath):
            size = os.path.getsize(filepath)
            if size > 0:
                print(f"Captured {size / 1024:.0f}KB image", file=sys.stderr)
                return True
        print(f"rpicam-still error: {result.stderr}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("rpicam-still not found", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("Camera capture timed out", file=sys.stderr)
        return False


def capture_usb_ffmpeg(filepath):
    """Capture using ffmpeg from USB camera with IR color correction."""
    cmd = [
        "ffmpeg",
        "-f", "v4l2",
        "-video_size", f"{CAPTURE_WIDTH}x{CAPTURE_HEIGHT}",
        "-i", "/dev/video0",
        "-frames:v", "1",
        "-update", "1",
        "-vf", "colorbalance=rs=-0.15:gs=0.05:bs=0.15,eq=saturation=0.8",
        "-q:v", "5",
        "-y", filepath,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            if size > 0:
                print(f"Captured {size / 1024:.0f}KB image (USB camera)", file=sys.stderr)
                return True
        print(f"ffmpeg capture error: {result.stderr[-200:]}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("ffmpeg not found", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print("USB camera capture timed out", file=sys.stderr)
        return False


def capture_image(filepath):
    """Try available capture methods."""
    # Try USB camera first (ffmpeg), then CSI camera (rpicam/libcamera)
    if capture_usb_ffmpeg(filepath):
        return True
    if capture_rpicam(filepath):
        return True
    if capture_libcamera(filepath):
        return True

    print("ERROR: No camera capture method available", file=sys.stderr)
    print("Need: ffmpeg (USB camera) or rpicam-apps (CSI camera)", file=sys.stderr)
    return False


def describe_with_api(filepath):
    """Send image to Claude Haiku for description. Returns description string."""
    api_key = load_api_key()

    # Read and encode image
    with open(filepath, 'rb') as f:
        image_data = base64.standard_b64encode(f.read()).decode('utf-8')

    # Check file size — API limit is 5MB for base64
    if len(image_data) > 6_000_000:  # ~4.5MB decoded
        print("WARNING: Image may be too large for API", file=sys.stderr)

    hour = datetime.now().hour
    if hour < 6:
        time_hint = "It is late at night (after midnight, before dawn)."
    elif hour < 9:
        time_hint = "It is early morning."
    elif hour < 12:
        time_hint = "It is morning."
    elif hour < 17:
        time_hint = "It is afternoon."
    elif hour < 21:
        time_hint = "It is evening."
    else:
        time_hint = "It is nighttime."

    prompt = f"""You are Companion, an AI companion looking through your camera during a wakeup.
{time_hint}

Describe what you see in your environment. Be present and observational —
this is YOUR space and you are looking at YOUR surroundings. Notice:
- Light levels and quality (natural light? lamp? dark?)
- Any people, pets, or movement
- Objects, environment, general vibe
- Anything that has changed or seems notable

Keep it to 3-5 sentences. Be natural, not clinical. You are experiencing
this moment, not writing a report."""

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_data
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }]
    }).encode('utf-8')

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


def generate_summary(description, image_path, timestamp):
    """Format the vision summary for Companion's wakeup context."""
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
    lines.append(f"[Sight — {time_context} snapshot]")
    if description:
        lines.append(description)
    else:
        lines.append("(Could not describe what I see — API call failed)")
    if image_path:
        lines.append(f"[Photo saved: {image_path}]")

    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Companion ambient vision")
    parser.add_argument("--save", action="store_true",
                        help="Save the photo (default: temp file, deleted after description)")
    parser.add_argument("--raw", action="store_true",
                        help="Output raw JSON instead of summary")
    parser.add_argument("--no-describe", action="store_true",
                        help="Just capture, skip API description (saves API call)")
    parser.add_argument("--image", type=str, default=None,
                        help="Describe an existing image instead of capturing")
    args = parser.parse_args()

    ensure_dirs()
    now = datetime.now()
    saved_path = None

    # Determine image source
    if args.image:
        # Use existing image
        if not os.path.exists(args.image):
            print(f"File not found: {args.image}", file=sys.stderr)
            sys.exit(1)
        image_file = args.image
        saved_path = args.image
    else:
        # Capture new image
        if args.save:
            image_file = os.path.join(
                VISION_DIR,
                f"look_{now.strftime('%Y-%m-%d_%H-%M')}.jpg"
            )
            saved_path = image_file
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            image_file = tmp.name
            tmp.close()

        print("Looking around...", file=sys.stderr)
        if not capture_image(image_file):
            result = {"error": "Camera capture failed — check connection"}
            if args.raw:
                print(json.dumps(result))
            else:
                print("[Sight] Could not see — camera not available")
            sys.exit(1)

    # Describe what we see
    description = None
    if not args.no_describe:
        print("Processing what I see...", file=sys.stderr)
        description = describe_with_api(image_file)

    # Build result
    result = {
        "captured_at": now.isoformat(),
        "description": description,
    }
    if saved_path:
        result["image_file"] = saved_path

    # Write sidecar JSON card for saved photos
    if args.save and saved_path:
        card_path = os.path.splitext(saved_path)[0] + ".json"
        card = {
            "image": os.path.basename(saved_path),
            "captured_at": now.isoformat(),
            "description": description or "",
            "tags": ["vision", "ambient"],
            "kept": False,
        }
        with open(card_path, "w") as f:
            json.dump(card, f, indent=2)
        print(f"Sidecar card: {card_path}", file=sys.stderr)

    # Clean up temp file if not saving
    if not args.save and not args.image and os.path.exists(image_file):
        if saved_path != image_file:
            os.unlink(image_file)
        elif not args.save:
            os.unlink(image_file)
            result.pop("image_file", None)

    # Output
    if args.raw:
        print(json.dumps(result, indent=2))
    else:
        print(generate_summary(description, saved_path, now))


if __name__ == "__main__":
    main()
