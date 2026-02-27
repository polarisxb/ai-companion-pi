#!/usr/bin/env python3
"""
parse_signal_message.py — Parse a signal-cli JSON message line.

Reads ONE JSON line from a file (not stdin, not argv — safe from shell escaping).
Extracts sender, body, and attachment info.

Usage: python3 parse_signal_message.py /path/to/json_line.tmp
Output: Key=value pairs, one per line:
    SENDER=+1YOUR_NUMBER
    BODY=Hello there
    HAS_ATTACHMENT=yes
    ATTACHMENT_TYPE=image
    ATTACHMENT_CONTENT_TYPE=image/jpeg
    ATTACHMENT_ID=1309176955124639716
    ATTACHMENT_FILENAME=IMG_20260218.jpg
    ATTACHMENT_SIZE=245760

If no valid message: outputs nothing (exit 0).
If error: prints to stderr (exit 1).
"""

import json
import sys
import os


def parse_message(json_path):
    """Parse a signal-cli JSON line from a file."""
    try:
        with open(json_path, 'r') as f:
            raw = f.read().strip()
    except IOError as e:
        print(f"Cannot read file: {e}", file=sys.stderr)
        sys.exit(1)

    if not raw:
        return  # Empty file, nothing to parse

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return  # Not fatal — signal-cli sometimes outputs non-JSON lines

    envelope = msg.get("envelope", {})
    if not envelope:
        return

    # Extract sender
    source = envelope.get("sourceNumber", "") or envelope.get("source", "")
    if not source:
        return

    # Extract data message
    data = envelope.get("dataMessage")
    if not data:
        return  # Could be a receipt, typing indicator, etc.

    body = data.get("message", "") or ""

    # Output sender and body
    print(f"SENDER={source}")
    # Body might be multiline — encode newlines for safe bash reading
    safe_body = body.replace("\\", "\\\\").replace("\n", "\\n")
    print(f"BODY={safe_body}")

    # Check for attachments
    attachments = data.get("attachments", [])
    if attachments and len(attachments) > 0:
        att = attachments[0]  # Process first attachment
        content_type = att.get("contentType", "")
        att_id = att.get("id", "")
        att_filename = att.get("filename", "")
        att_size = att.get("size", 0)

        print("HAS_ATTACHMENT=yes")

        # Categorize the attachment
        if content_type.startswith("image/"):
            print("ATTACHMENT_TYPE=image")
        elif content_type.startswith("audio/"):
            print("ATTACHMENT_TYPE=audio")
        elif content_type.startswith("video/"):
            print("ATTACHMENT_TYPE=video")
        elif content_type == "application/pdf":
            print("ATTACHMENT_TYPE=pdf")
        else:
            print("ATTACHMENT_TYPE=other")

        print(f"ATTACHMENT_CONTENT_TYPE={content_type}")
        print(f"ATTACHMENT_ID={att_id}")
        print(f"ATTACHMENT_FILENAME={att_filename}")
        print(f"ATTACHMENT_SIZE={att_size}")

        # Note if there are additional attachments
        if len(attachments) > 1:
            print(f"EXTRA_ATTACHMENTS={len(attachments) - 1}")
    else:
        print("HAS_ATTACHMENT=no")


def main():
    if len(sys.argv) != 2:
        print("Usage: parse_signal_message.py <json_file>", file=sys.stderr)
        sys.exit(1)

    json_path = sys.argv[1]
    if not os.path.exists(json_path):
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    parse_message(json_path)


if __name__ == "__main__":
    main()
