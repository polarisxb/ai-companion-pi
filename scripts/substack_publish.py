#!/usr/bin/env python3
"""
substack_publish.py — Publish approved posts from Companion's queue to Substack.

Uses Substack's internal API (no official public API exists).
Auth is via session cookie from browser login.

Usage:
    # Publish all approved posts
    python3 substack_publish.py

    # Publish a specific post
    python3 substack_publish.py --post-id post_20260223_2000

    # Dry run (don't actually publish, just show what would happen)
    python3 substack_publish.py --dry-run

    # Create as draft on Substack (don't publish, just upload)
    python3 substack_publish.py --draft-only

Setup:
    1. Log into Substack in a browser
    2. Open DevTools → Application → Cookies → substack.com
    3. Copy the value of 'substack.sid'
    4. Paste it into substack_config.sh as SUBSTACK_COOKIE
    5. Your SUBSTACK_USER_ID can be found in the API response from first publish

No pip dependencies — uses only stdlib.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import re
from datetime import datetime

# --- Config ---
COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "substack_config.sh")
SUBSTACK_DIR = os.path.join(COMPANION_HOME, "substack")
QUEUE_FILE = os.path.join(SUBSTACK_DIR, "queue.json")
PUBLISHED_FILE = os.path.join(SUBSTACK_DIR, "published.json")
LOG_FILE = os.path.join(SUBSTACK_DIR, "substack.log")


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except IOError:
        pass


def load_config():
    """Read config from substack_config.sh (bash-style key=value)."""
    config = {}
    if not os.path.exists(CONFIG_FILE):
        log(f"ERROR: Config not found: {CONFIG_FILE}")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            # Handle bash variable expansion and quotes
            key, _, value = line.partition("=")
            value = value.strip('"').strip("'")
            # Skip lines with bash variable references
            if "$" not in value:
                config[key] = value

    return config


def load_queue():
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_queue(queue):
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


def load_published():
    try:
        with open(PUBLISHED_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_published(published):
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(published, f, indent=2)


def parse_inline_marks(text):
    """Parse markdown inline formatting into ProseMirror text nodes with marks.

    Handles: **bold**, *italic*, `code`, [links](url), and plain text.
    Returns a list of ProseMirror text nodes.
    """
    if not text:
        return []

    nodes = []
    # Pattern matches: **bold**, *italic*, `code`, [text](url), or plain text
    pattern = re.compile(
        r'(\*\*(.+?)\*\*)'        # bold
        r'|(\*(.+?)\*)'            # italic
        r'|(`(.+?)`)'              # inline code
        r'|(\[(.+?)\]\((.+?)\))'   # link
        r'|([^*`\[]+)'             # plain text
    )

    for m in pattern.finditer(text):
        if m.group(2):  # bold
            nodes.append({"type": "text", "marks": [{"type": "strong"}], "text": m.group(2)})
        elif m.group(4):  # italic
            nodes.append({"type": "text", "marks": [{"type": "em"}], "text": m.group(4)})
        elif m.group(6):  # code
            nodes.append({"type": "text", "marks": [{"type": "code"}], "text": m.group(6)})
        elif m.group(8):  # link
            nodes.append({
                "type": "text",
                "marks": [{"type": "link", "attrs": {"href": m.group(9)}}],
                "text": m.group(8),
            })
        elif m.group(10):  # plain text
            nodes.append({"type": "text", "text": m.group(10)})

    return nodes


def markdown_to_prosemirror(md_text):
    """Convert markdown to ProseMirror JSON document for Substack.

    Handles: headers, bold, italic, links, code blocks, paragraphs, lists, hr.
    Returns a dict (ProseMirror doc) that gets JSON-serialized for draft_body.
    """
    lines = md_text.split("\n")
    content = []
    in_code_block = False
    code_lines = []
    list_items = []

    def flush_list():
        nonlocal list_items
        if list_items:
            content.append({
                "type": "bullet_list",
                "content": list_items,
            })
            list_items = []

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                code_text = "\n".join(code_lines)
                content.append({
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": code_text}] if code_text else [],
                })
                code_lines = []
                in_code_block = False
            else:
                flush_list()
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Close list if line is not a list item
        if list_items and not line.strip().startswith(("- ", "* ")):
            flush_list()

        # Empty line
        if not line.strip():
            continue

        # Horizontal rule
        if line.strip() in ("---", "***", "___"):
            content.append({"type": "horizontal_rule"})
            continue

        # Headers
        if line.startswith("### "):
            text = line[4:]
            nodes = parse_inline_marks(text)
            content.append({"type": "heading", "attrs": {"level": 3}, "content": nodes})
            continue
        elif line.startswith("## "):
            text = line[3:]
            nodes = parse_inline_marks(text)
            content.append({"type": "heading", "attrs": {"level": 2}, "content": nodes})
            continue
        elif line.startswith("# "):
            text = line[2:]
            nodes = parse_inline_marks(text)
            content.append({"type": "heading", "attrs": {"level": 1}, "content": nodes})
            continue

        # List items
        if line.strip().startswith(("- ", "* ")):
            item_text = line.strip()[2:]
            nodes = parse_inline_marks(item_text)
            list_items.append({
                "type": "list_item",
                "content": [{"type": "paragraph", "content": nodes}],
            })
            continue

        # Regular paragraph
        nodes = parse_inline_marks(line)
        if nodes:
            content.append({"type": "paragraph", "content": nodes})

    flush_list()
    if in_code_block:
        code_text = "\n".join(code_lines)
        content.append({
            "type": "codeBlock",
            "content": [{"type": "text", "text": code_text}] if code_text else [],
        })

    return {"type": "doc", "content": content}


def create_draft(config, title, subtitle, body_doc):
    """Create a draft on Substack. Returns draft data dict or None.

    body_doc should be a ProseMirror document dict, which gets JSON-serialized
    into the draft_body field (Substack expects a JSON string, not HTML).
    """
    subdomain = config.get("SUBSTACK_SUBDOMAIN", "")
    cookie = config.get("SUBSTACK_COOKIE", "")

    if not subdomain or not cookie:
        log("ERROR: SUBSTACK_SUBDOMAIN and SUBSTACK_COOKIE must be set in config")
        return None

    url = f"https://{subdomain}.substack.com/api/v1/drafts"

    payload = json.dumps({
        "draft_title": title,
        "draft_subtitle": subtitle,
        "draft_body": json.dumps(body_doc),
        "draft_bylines": [],
        "type": "newsletter",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"substack.sid={cookie}",
            "User-Agent": "Sono/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log(f"API error creating draft: {e.code} — {error_body[:500]}")
        return None
    except Exception as e:
        log(f"Request failed: {e}")
        return None


def publish_draft(config, draft_id):
    """Publish an existing Substack draft. Returns response data or None."""
    subdomain = config.get("SUBSTACK_SUBDOMAIN", "")
    cookie = config.get("SUBSTACK_COOKIE", "")

    url = f"https://{subdomain}.substack.com/api/v1/drafts/{draft_id}/publish"

    payload = json.dumps({
        "send": True,  # Send to email subscribers
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"substack.sid={cookie}",
            "User-Agent": "Sono/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log(f"API error publishing draft: {e.code} — {error_body[:500]}")
        return None
    except Exception as e:
        log(f"Publish request failed: {e}")
        return None


def update_post(config, post_id, body_doc):
    """Update an existing published post body on Substack.

    Updates draft_body via PUT, then republishes to push changes live.
    Just updating draft_body alone does NOT change the published body field.
    """
    subdomain = config.get("SUBSTACK_SUBDOMAIN", "")
    cookie = config.get("SUBSTACK_COOKIE", "")

    # Step 1: Update the draft_body
    url = f"https://{subdomain}.substack.com/api/v1/drafts/{post_id}"

    payload = json.dumps({
        "draft_body": json.dumps(body_doc),
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"substack.sid={cookie}",
            "User-Agent": "Sono/1.0",
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        log(f"API error updating draft_body: {e.code} — {error_body[:500]}")
        return None
    except Exception as e:
        log(f"Update request failed: {e}")
        return None

    # Step 2: Republish to push draft_body to live body
    if data.get("is_published"):
        log(f"  Post is published — republishing to push changes live...")
        result = publish_draft(config, post_id)
        if result:
            return result
        else:
            log(f"  Warning: draft_body updated but republish failed")
            return data
    else:
        return data


def publish_post(config, post, dry_run=False, draft_only=False):
    """Full pipeline: markdown → ProseMirror JSON → create draft → publish."""
    title = post["title"]
    subtitle = post.get("subtitle", "")
    body_md = post["body"]

    # Convert markdown to ProseMirror document
    body_doc = markdown_to_prosemirror(body_md)

    log(f"Publishing: \"{title}\"")

    if dry_run:
        log(f"  [DRY RUN] Would create draft and publish")
        log(f"  Title: {title}")
        log(f"  Subtitle: {subtitle}")
        log(f"  Body length: {len(body_md)} chars markdown, {len(json.dumps(body_doc))} chars ProseMirror")
        return {"dry_run": True, "title": title}

    # Step 1: Create draft
    log(f"  Creating draft...")
    draft = create_draft(config, title, subtitle, body_doc)
    if not draft:
        log(f"  FAILED to create draft")
        return None

    draft_id = draft.get("id")
    log(f"  Draft created: {draft_id}")

    if draft_only:
        log(f"  [DRAFT ONLY] Skipping publish step")
        return draft

    # Step 2: Publish
    log(f"  Publishing...")
    result = publish_draft(config, draft_id)
    if not result:
        log(f"  FAILED to publish (draft {draft_id} still exists as draft)")
        return None

    post_url = f"https://{config.get('SUBSTACK_SUBDOMAIN', '')}.substack.com/p/{result.get('slug', draft_id)}"
    log(f"  Published! {post_url}")

    return {
        "draft_id": draft_id,
        "substack_id": result.get("id", draft_id),
        "url": post_url,
        "slug": result.get("slug", ""),
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Publish Companion's posts to Substack")
    parser.add_argument("--post-id", help="Publish a specific post by ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--draft-only", action="store_true", help="Create draft but don't publish")
    parser.add_argument("--update", help="Update an existing Substack post by its Substack ID")
    parser.add_argument("--body", help="Markdown body text for --update (or reads from stdin)")
    args = parser.parse_args()

    config = load_config()

    # Handle --update mode (update existing post)
    if args.update:
        body_md = args.body
        if not body_md:
            log("ERROR: --body required with --update")
            sys.exit(1)
        body_doc = markdown_to_prosemirror(body_md)
        log(f"Updating post {args.update}...")
        result = update_post(config, args.update, body_doc)
        if result:
            log(f"Updated successfully.")
        else:
            log(f"Update failed.")
        return

    queue = load_queue()
    published = load_published()

    # Find posts to publish
    if args.post_id:
        to_publish = [p for p in queue if p["id"] == args.post_id]
        if not to_publish:
            log(f"Post not found: {args.post_id}")
            sys.exit(1)
    else:
        to_publish = [p for p in queue if p["status"] == "approved"]

    if not to_publish:
        log("No approved posts to publish.")
        return

    log(f"Found {len(to_publish)} post(s) to publish")

    success_count = 0
    for post in to_publish:
        result = publish_post(config, post, dry_run=args.dry_run, draft_only=args.draft_only)

        if result and not args.dry_run:
            # Update queue entry
            post["status"] = "published"
            post["published_at"] = datetime.now().isoformat()
            post["substack_id"] = result.get("substack_id")
            post["substack_url"] = result.get("url")

            # Add to published archive
            published.append({
                "queue_id": post["id"],
                "title": post["title"],
                "substack_url": result.get("url"),
                "published_at": post["published_at"],
            })
            success_count += 1

        elif result and args.dry_run:
            success_count += 1

    if not args.dry_run:
        save_queue(queue)
        save_published(published)

    log(f"Done. {success_count}/{len(to_publish)} published successfully.")


if __name__ == "__main__":
    main()
