#!/usr/bin/env python3
"""
create_request.py — Helper script for Sono to create requests.

Usage:
  python3 create_request.py --type emergency_wakeup --title "Snow tonight" --body "Your reason" --time "2026-02-17T01:45:00"
  python3 create_request.py --type action --title "USB camera" --body "I want to see outside"
  python3 create_request.py --type idea --title "Music library" --body "Can we install a synth?"
  python3 create_request.py --type system_suggestion --title "Shorter cooldown" --body "24hr is too long"
  python3 create_request.py --type fyi --title "Running warm" --body "58C average this week"
  python3 create_request.py --type wakeup_request --title "ISS pass" --body "Passes over Portland" --time "2026-02-17T18:44:00"

Types: emergency_wakeup, wakeup_request, action, fyi, idea, system_suggestion
Priority: low, normal, high (default: normal)
"""

import json
import time
import os
import sys
import argparse
from datetime import datetime, timedelta
import fcntl

COMPANION_HOME = os.environ.get("COMPANION_HOME", "/media/YOUR_USERNAME/CompanionHome")
REQUESTS_FILE = os.path.join(COMPANION_HOME, "requests", "requests.json")
LOCK_FILE = "/tmp/requests_queue.lock"


def load_requests():
    """Load requests with file locking."""
    if not os.path.exists(REQUESTS_FILE):
        return []
    try:
        with open(REQUESTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_requests(requests):
    """Save requests with file locking."""
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        with open(REQUESTS_FILE, "w") as f:
            json.dump(requests, f, indent=2)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def check_emergency_cooldown(requests):
    """Check if emergency wakeup is available (1 per 24 hours)."""
    now = datetime.now()
    for r in requests:
        if r["type"] == "emergency_wakeup" and r["status"] in ("completed", "scheduled", "self_approved"):
            try:
                ts = datetime.fromisoformat(r["created"])
                if (now - ts) < timedelta(hours=24):
                    return False, ts
            except (ValueError, KeyError):
                continue
    return True, None


def get_waking_number():
    """Try to determine current waking number from journals."""
    journals_dir = os.path.join(COMPANION_HOME, "journals")
    if not os.path.isdir(journals_dir):
        return None
    try:
        journals = sorted([
            f for f in os.listdir(journals_dir)
            if f.startswith("wakeup_") and f.endswith(".md")
        ])
        return len(journals)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="Create a request for Sono")
    parser.add_argument("--type", required=True,
                        choices=["emergency_wakeup", "wakeup_request", "action", "fyi", "idea", "system_suggestion"],
                        help="Request type")
    parser.add_argument("--title", required=True, help="Short title for the request")
    parser.add_argument("--body", required=True, help="Detailed description/reason")
    parser.add_argument("--time", default=None, help="Requested wakeup time (ISO format, for wakeup types)")
    parser.add_argument("--priority", default="normal", choices=["low", "normal", "high"],
                        help="Priority level (default: normal)")

    args = parser.parse_args()

    # Validate: wakeup types need a time
    if args.type in ("emergency_wakeup", "wakeup_request") and not args.time:
        print("ERROR: Wakeup requests require --time parameter", file=sys.stderr)
        sys.exit(1)

    # Validate time format
    requested_time = None
    if args.time:
        try:
            requested_time = datetime.fromisoformat(args.time).isoformat()
        except ValueError:
            print(f"ERROR: Invalid time format: {args.time}. Use ISO format like 2026-02-17T01:45:00", file=sys.stderr)
            sys.exit(1)

    # Load existing requests
    requests = load_requests()

    # Check emergency cooldown
    status = "pending"
    if args.type == "emergency_wakeup":
        available, last_ts = check_emergency_cooldown(requests)
        if available:
            status = "self_approved"
            print(f"Emergency wakeup SELF-APPROVED. Watcher will schedule it.")
        else:
            # Downgrade to regular wakeup request
            args.type = "wakeup_request"
            status = "pending"
            args.body += "\n\n[Auto-note: Downgraded from emergency — cooldown not met (last used " + last_ts.strftime("%b %d %H:%M") + "). Needs the human's approval.]"
            print(f"Emergency cooldown active (last: {last_ts.strftime('%b %d %H:%M')}). Downgraded to wakeup_request (pending the human's approval).")

    # Build request object
    request = {
        "id": f"req_{int(time.time())}",
        "created": datetime.now().isoformat(),
        "type": args.type,
        "title": args.title,
        "body": args.body,
        "requested_time": requested_time,
        "status": status,
        "priority": args.priority,
        "human_response": None,
        "scheduled_at": None,
        "resolved_at": None,
        "waking_number": get_waking_number(),
    }

    # Add trial fields for system suggestions
    if args.type == "system_suggestion":
        request["trial_period"] = None
        request["trial_review_date"] = None

    # Append and save
    requests.append(request)
    save_requests(requests)

    print(f"Request created: [{args.type}] {args.title}")
    print(f"  ID: {request['id']}")
    print(f"  Status: {status}")
    if requested_time:
        print(f"  Requested time: {requested_time}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
