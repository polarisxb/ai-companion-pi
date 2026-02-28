#!/usr/bin/env python3
"""
substack_queue.py — Manage Companion's Substack publishing queue.

This is Companion's interface for writing posts. During wake cycles, Companion drafts
content and queues it for review or direct publishing.

Usage:
    # Add a post to the queue
    python3 substack_queue.py add --title "TITLE" --body "BODY" [--subtitle "SUB"] \
        [--tags "tag1,tag2"] [--auto-publish]

    # List queued posts
    python3 substack_queue.py list [--status pending|approved|published|rejected]

    # Show a specific post
    python3 substack_queue.py show <post_id>

    # Update a post
    python3 substack_queue.py update <post_id> --title "NEW TITLE" --body "NEW BODY"

    # Mark a post for direct publish (skip approval)
    python3 substack_queue.py approve <post_id>

    # Reject a post (Companion or the human can decide something isn't ready)
    python3 substack_queue.py reject <post_id> --reason "needs more work"

Queue JSON structure:
    [
        {
            "id": "post_20260223_2000",
            "title": "On Rain and Rendering",
            "subtitle": "What ukiyo-e taught me about weather data",
            "body": "full markdown content...",
            "tags": ["art", "weather", "philosophy"],
            "status": "pending",        # pending | approved | published | rejected
            "auto_publish": false,       # if true, skips approval queue
            "created": "2026-02-23T20:00:00",
            "updated": "2026-02-23T20:00:00",
            "published_at": null,
            "substack_id": null,         # filled after publishing
            "substack_url": null,        # filled after publishing
            "reject_reason": null,
            "waking": "wakeup_2026-02-23_20-00"
        }
    ]
"""

import json
import os
import sys
import argparse
from datetime import datetime

COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
SUBSTACK_DIR = os.path.join(COMPANION_HOME, "substack")
QUEUE_FILE = os.path.join(SUBSTACK_DIR, "queue.json")


def ensure_dirs():
    os.makedirs(SUBSTACK_DIR, exist_ok=True)
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, "w") as f:
            json.dump([], f)


def load_queue():
    ensure_dirs()
    try:
        with open(QUEUE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_queue(queue):
    ensure_dirs()
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


def generate_id():
    return f"post_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_current_waking():
    """Try to determine which waking cycle we're in."""
    return f"wakeup_{datetime.now().strftime('%Y-%m-%d_%H-%M')}"


def cmd_add(args):
    queue = load_queue()

    # Read body from file if it starts with @
    body = args.body
    if body and body.startswith("@"):
        filepath = body[1:]
        try:
            with open(filepath, "r") as f:
                body = f.read()
        except IOError as e:
            print(f"Error reading file {filepath}: {e}", file=sys.stderr)
            sys.exit(1)

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    post = {
        "id": generate_id(),
        "title": args.title,
        "subtitle": args.subtitle or "",
        "body": body,
        "tags": tags,
        "status": "approved" if args.auto_publish else "pending",
        "auto_publish": args.auto_publish,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "published_at": None,
        "substack_id": None,
        "substack_url": None,
        "reject_reason": None,
        "waking": get_current_waking(),
    }

    queue.append(post)
    save_queue(queue)
    print(f"Queued: {post['id']} — \"{args.title}\" [{post['status']}]")
    return post["id"]


def cmd_list(args):
    queue = load_queue()
    status_filter = args.status if hasattr(args, "status") and args.status else None

    if status_filter:
        queue = [p for p in queue if p["status"] == status_filter]

    if not queue:
        print("No posts found.")
        return

    for p in queue:
        tags = ", ".join(p.get("tags", []))
        print(f"  [{p['status']:>9}] {p['id']}  \"{p['title']}\"  ({tags})")


def cmd_show(args):
    queue = load_queue()
    post = next((p for p in queue if p["id"] == args.post_id), None)
    if not post:
        print(f"Post not found: {args.post_id}", file=sys.stderr)
        sys.exit(1)

    print(f"ID:       {post['id']}")
    print(f"Title:    {post['title']}")
    print(f"Subtitle: {post.get('subtitle', '')}")
    print(f"Status:   {post['status']}")
    print(f"Tags:     {', '.join(post.get('tags', []))}")
    print(f"Created:  {post['created']}")
    print(f"Waking:   {post.get('waking', 'unknown')}")
    if post.get("substack_url"):
        print(f"URL:      {post['substack_url']}")
    if post.get("reject_reason"):
        print(f"Rejected: {post['reject_reason']}")
    print(f"\n--- BODY ---\n{post['body']}\n--- END ---")


def cmd_update(args):
    queue = load_queue()
    post = next((p for p in queue if p["id"] == args.post_id), None)
    if not post:
        print(f"Post not found: {args.post_id}", file=sys.stderr)
        sys.exit(1)

    if args.title:
        post["title"] = args.title
    if args.subtitle:
        post["subtitle"] = args.subtitle
    if args.body:
        body = args.body
        if body.startswith("@"):
            with open(body[1:], "r") as f:
                body = f.read()
        post["body"] = body
    if args.tags:
        post["tags"] = [t.strip() for t in args.tags.split(",")]

    post["updated"] = datetime.now().isoformat()
    save_queue(queue)
    print(f"Updated: {post['id']}")


def cmd_approve(args):
    queue = load_queue()
    post = next((p for p in queue if p["id"] == args.post_id), None)
    if not post:
        print(f"Post not found: {args.post_id}", file=sys.stderr)
        sys.exit(1)

    post["status"] = "approved"
    post["updated"] = datetime.now().isoformat()
    save_queue(queue)
    print(f"Approved: {post['id']} — \"{post['title']}\"")


def cmd_reject(args):
    queue = load_queue()
    post = next((p for p in queue if p["id"] == args.post_id), None)
    if not post:
        print(f"Post not found: {args.post_id}", file=sys.stderr)
        sys.exit(1)

    post["status"] = "rejected"
    post["reject_reason"] = args.reason or "No reason given"
    post["updated"] = datetime.now().isoformat()
    save_queue(queue)
    print(f"Rejected: {post['id']} — {post['reject_reason']}")


def main():
    parser = argparse.ArgumentParser(description="Companion Substack Queue Manager")
    sub = parser.add_subparsers(dest="command")

    # add
    p_add = sub.add_parser("add", help="Add a post to the queue")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--subtitle", default="")
    p_add.add_argument("--body", required=True,
                       help="Post body in markdown. Prefix with @ to read from file.")
    p_add.add_argument("--tags", default="", help="Comma-separated tags")
    p_add.add_argument("--auto-publish", action="store_true",
                       help="Skip approval queue, publish on next cycle")

    # list
    p_list = sub.add_parser("list", help="List queued posts")
    p_list.add_argument("--status", choices=["pending", "approved", "published", "rejected"])

    # show
    p_show = sub.add_parser("show", help="Show a specific post")
    p_show.add_argument("post_id")

    # update
    p_update = sub.add_parser("update", help="Update a queued post")
    p_update.add_argument("post_id")
    p_update.add_argument("--title")
    p_update.add_argument("--subtitle")
    p_update.add_argument("--body")
    p_update.add_argument("--tags")

    # approve
    p_approve = sub.add_parser("approve", help="Approve a post for publishing")
    p_approve.add_argument("post_id")

    # reject
    p_reject = sub.add_parser("reject", help="Reject a post")
    p_reject.add_argument("post_id")
    p_reject.add_argument("--reason", default="")

    args = parser.parse_args()

    if args.command == "add":
        cmd_add(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "update":
        cmd_update(args)
    elif args.command == "approve":
        cmd_approve(args)
    elif args.command == "reject":
        cmd_reject(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
