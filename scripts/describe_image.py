#!/usr/bin/env python3
"""
describe_image.py — Sends an image to Claude API (Haiku) for description.
Used by signal_listener.sh when the human sends an image via Signal.

Usage: python3 describe_image.py /path/to/image.jpg [optional caption text]
Output: Prints description to stdout
Exit 0: success, Exit 1: error

No pip dependencies — uses only stdlib (urllib, json, base64).
"""

import sys
import os
import base64
import json
import urllib.request
import urllib.error

# Config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_CONFIG = os.path.join(SCRIPT_DIR, "api_config.sh")
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (we auto-compress, so generous limit)
API_TIMEOUT = 30  # seconds
MODEL = "claude-haiku-4-5-20251001"


def load_api_key():
    """Read ANTHROPIC_API_KEY from api_config.sh"""
    if not os.path.exists(API_CONFIG):
        print("ERROR: api_config.sh not found at " + API_CONFIG, file=sys.stderr)
        print("Create it with: echo 'ANTHROPIC_API_KEY=sk-ant-...' > " + API_CONFIG,
              file=sys.stderr)
        sys.exit(1)

    with open(API_CONFIG) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip('"').strip("'")
                if key and key.startswith("sk-ant-"):
                    return key
                else:
                    print("ERROR: API key looks malformed", file=sys.stderr)
                    sys.exit(1)

    print("ERROR: ANTHROPIC_API_KEY not found in " + API_CONFIG, file=sys.stderr)
    sys.exit(1)


def get_media_type(filepath):
    """Determine image media type from magic bytes (signal-cli saves without extensions)"""
    try:
        with open(filepath, 'rb') as f:
            header = f.read(12)
    except IOError:
        return None

    # Check magic bytes
    if header[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    elif header[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    elif header[:4] == b'GIF8':
        return 'image/gif'
    elif len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image/webp'

    # Fallback: try file extension
    ext = os.path.splitext(filepath)[1].lower()
    ext_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
    }
    return ext_map.get(ext, None)


def is_image_file(filepath):
    """Check if a file is a supported image type"""
    return get_media_type(filepath) is not None


def compress_image(filepath, max_bytes=4_500_000):
    """Compress/resize an image to fit under the API size limit.
    Returns (base64_data, media_type) tuple.
    Uses Pillow (PIL) which is already available on the Pi."""
    from PIL import Image
    import io

    file_size = os.path.getsize(filepath)
    media_type = get_media_type(filepath)

    # If already under limit, just read raw bytes
    if file_size <= max_bytes:
        with open(filepath, 'rb') as f:
            return base64.standard_b64encode(f.read()).decode('utf-8'), media_type

    print(f"Image is {file_size / 1024 / 1024:.1f}MB — compressing...",
          file=sys.stderr)

    img = Image.open(filepath)

    # Convert RGBA to RGB for JPEG output
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')

    # Resize if dimensions are huge (keep under 1568px on long edge)
    max_dim = 1568
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        print(f"Resized to {new_size[0]}x{new_size[1]}", file=sys.stderr)

    # Encode as JPEG with decreasing quality until under limit
    for quality in [85, 70, 50, 30]:
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            print(f"Compressed to {len(data) / 1024:.0f}KB (quality={quality})",
                  file=sys.stderr)
            return base64.standard_b64encode(data).decode('utf-8'), 'image/jpeg'

    # Last resort: already smallest quality, just use it
    return base64.standard_b64encode(data).decode('utf-8'), 'image/jpeg'


def describe_image(filepath, context="", sender="YOUR_HUMAN"):
    """Send image to Claude Haiku for description. Returns description string."""
    api_key = load_api_key()

    # Read and compress image if needed (API limit is 5MB)
    image_data, media_type = compress_image(filepath)

    if not media_type:
        media_type = get_media_type(filepath)
    if not media_type:
        print("ERROR: Could not determine image type for " + filepath,
              file=sys.stderr)
        sys.exit(1)

    # Build the prompt — give Haiku context about who/what
    if context:
        text_prompt = (
            f'{sender} sent this image to Companion (an AI companion), along with '
            f'the message: "{context}"\n\n'
            f'Describe what you see in the image. Be vivid but concise — '
            f'2-3 sentences. Focus on what would be meaningful to respond to.'
        )
    else:
        text_prompt = (
            f'{sender} sent this image to Companion (an AI companion), no caption.\n\n'
            f'Describe what you see in the image. Be vivid but concise — '
            f'2-3 sentences. Focus on what would be meaningful to respond to.'
        )

    # Build API request payload
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data
                    }
                },
                {
                    "type": "text",
                    "text": text_prompt
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

            print("ERROR: No text in API response", file=sys.stderr)
            sys.exit(1)

    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f"API error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: describe_image.py <image_path> [--sender NAME] [caption_text]",
              file=sys.stderr)
        sys.exit(1)

    args = sys.argv[1:]
    filepath = args[0]
    sender = "YOUR_HUMAN"
    caption_parts = []

    i = 1
    while i < len(args):
        if args[i] == "--sender" and i + 1 < len(args):
            sender = args[i + 1]
            i += 2
        else:
            caption_parts.append(args[i])
            i += 1

    context = " ".join(caption_parts)

    # Validate file exists
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    # Check file size
    file_size = os.path.getsize(filepath)
    if file_size > MAX_FILE_SIZE:
        print("(Image too large to process — over 50MB)")
        sys.exit(0)
    if file_size == 0:
        print("(Empty file — nothing to describe)")
        sys.exit(0)

    # Verify it's an image
    if not is_image_file(filepath):
        print(f"Not a supported image format: {filepath}", file=sys.stderr)
        sys.exit(1)

    description = describe_image(filepath, context, sender)
    print(description)


if __name__ == "__main__":
    main()
