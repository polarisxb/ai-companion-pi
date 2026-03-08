#!/usr/bin/env python3
"""
usage_check.py — Analyze Claude API usage and determine alert level.

Reads the usage CSV log, counts calls in sliding windows, writes a status
JSON file, and prints an alert message to stdout if thresholds are exceeded.

Usage:
    python3 usage_check.py <usage_log.csv> <usage_status.json>

Exit codes:
    0 = normal (may still print alert to stdout)
    1 = error reading/parsing files
"""

import sys
import json
import csv
from datetime import datetime, timedelta, timezone
from collections import Counter

# --- Thresholds (adjust as needed) ---
WARN_5HR = 80       # Start warning at this many calls per 5hr window
CRITICAL_5HR = 120   # Urgent at this level
WARN_DAILY = 250     # Daily warning
CRITICAL_DAILY = 350  # Daily urgent

# Cooldown: don't send another alert within this many minutes of the last one
ALERT_COOLDOWN_MIN = 30


def parse_timestamp(ts_str):
    """Parse an ISO format timestamp, tolerant of various formats."""
    ts_str = ts_str.strip()
    try:
        # Python 3.7+ fromisoformat handles most ISO strings
        return datetime.fromisoformat(ts_str)
    except ValueError:
        pass
    # Fallback: try common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def load_log(csv_path):
    """Load usage log entries from CSV."""
    entries = []
    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = parse_timestamp(row.get("timestamp", ""))
                if ts is None:
                    continue
                # Strip timezone for consistent comparison
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                entries.append({
                    "timestamp": ts,
                    "type": row.get("type", "unknown"),
                    "description": row.get("description", ""),
                    "exit_code": int(row.get("exit_code", 0)),
                    "duration_sec": int(row.get("duration_sec", 0)),
                })
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error reading log: {e}", file=sys.stderr)
        return []
    return entries


def load_status(status_path):
    """Load previous status JSON, or return empty dict."""
    try:
        with open(status_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_status(status_path, status):
    """Write status JSON."""
    try:
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2, default=str)
    except Exception as e:
        print(f"Error writing status: {e}", file=sys.stderr)


def main():
    if len(sys.argv) < 3:
        print("Usage: usage_check.py <usage_log.csv> <usage_status.json>", file=sys.stderr)
        sys.exit(1)

    csv_path = sys.argv[1]
    status_path = sys.argv[2]

    entries = load_log(csv_path)
    old_status = load_status(status_path)

    now = datetime.now()
    five_hours_ago = now - timedelta(hours=5)
    twenty_four_hours_ago = now - timedelta(hours=24)

    # Filter entries by time window
    last_5hr = [e for e in entries if e["timestamp"] >= five_hours_ago]
    last_24hr = [e for e in entries if e["timestamp"] >= twenty_four_hours_ago]

    # Count by type
    types_5hr = Counter(e["type"] for e in last_5hr)
    types_24hr = Counter(e["type"] for e in last_24hr)

    count_5hr = len(last_5hr)
    count_24hr = len(last_24hr)

    # Exclude rate_limit entries from counts (they're events, not calls)
    actual_5hr = count_5hr - types_5hr.get("rate_limit", 0)
    actual_24hr = count_24hr - types_24hr.get("rate_limit", 0)

    # Determine alert level
    level = "normal"
    if actual_5hr >= CRITICAL_5HR or actual_24hr >= CRITICAL_DAILY:
        level = "critical"
    elif actual_5hr >= WARN_5HR or actual_24hr >= WARN_DAILY:
        level = "warning"

    # Check for recent rate limit events
    rate_limits_24hr = types_24hr.get("rate_limit", 0)

    # Build status object
    status = {
        "checked_at": now.isoformat(),
        "level": level,
        "calls_5hr": actual_5hr,
        "calls_24hr": actual_24hr,
        "types_5hr": dict(types_5hr),
        "types_24hr": dict(types_24hr),
        "rate_limits_24hr": rate_limits_24hr,
        "thresholds": {
            "warn_5hr": WARN_5HR,
            "critical_5hr": CRITICAL_5HR,
            "warn_daily": WARN_DAILY,
            "critical_daily": CRITICAL_DAILY,
        },
    }

    save_status(status_path, status)

    # Check cooldown: don't spam alerts
    last_alert = old_status.get("last_alert_at")
    if last_alert:
        try:
            last_alert_dt = datetime.fromisoformat(last_alert)
            if (now - last_alert_dt) < timedelta(minutes=ALERT_COOLDOWN_MIN):
                # Still in cooldown, don't print alert
                return
        except (ValueError, TypeError):
            pass

    # Print alert message to stdout (bash caller will send via Signal)
    if level == "critical":
        msg = (
            f"🚨 CRITICAL: {actual_5hr} Claude calls in last 5 hours! "
            f"Approaching rate limit. Daily: {actual_24hr}. "
            f"Types: {dict(types_5hr)}"
        )
        print(msg)
        status["last_alert_at"] = now.isoformat()
        save_status(status_path, status)
    elif level == "warning":
        msg = (
            f"⚠️ Usage notice: {actual_5hr} calls in last 5hr. "
            f"Daily: {actual_24hr}. Types: {dict(types_5hr)}"
        )
        print(msg)
        status["last_alert_at"] = now.isoformat()
        save_status(status_path, status)
    # else: normal — print nothing, bash gets empty string


if __name__ == "__main__":
    main()
