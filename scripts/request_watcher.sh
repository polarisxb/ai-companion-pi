#!/bin/bash
# request_watcher.sh — Checks for approved/self-approved wakeup requests and schedules them
# Runs every 5 minutes via cron or as a pm2 polling service
#
# What it does:
#   1. Reads requests/requests.json
#   2. Finds self_approved emergency wakeups → validates cooldown → schedules via `at`
#   3. Finds the human-approved wakeup requests → schedules via `at`
#   4. Expires requests whose time has passed without approval
#   5. Updates request statuses

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
REQUESTS_FILE="$COMPANION_HOME/requests/requests.json"
WAKEUP_SCRIPT="$COMPANION_HOME/scripts/special_wakeup.sh"
LOG_FILE="$COMPANION_HOME/requests/watcher.log"

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

# Ensure requests file exists
if [ ! -f "$REQUESTS_FILE" ]; then
    echo "[]" > "$REQUESTS_FILE"
    log "Created empty requests.json"
    exit 0
fi

# Validate JSON first
python3 -c "import json; json.load(open('$REQUESTS_FILE'))" 2>/dev/null
if [ $? -ne 0 ]; then
    log "ERROR: requests.json is invalid JSON. Skipping this cycle."
    exit 1
fi

# Process requests
python3 << 'PYEOF'
import json
import subprocess
import os
import sys
from datetime import datetime, timedelta

COMPANION_HOME = "/media/YOUR_USERNAME/CompanionHome"
REQUESTS_FILE = os.path.join(COMPANION_HOME, "requests", "requests.json")
WAKEUP_SCRIPT = os.path.join(COMPANION_HOME, "scripts", "special_wakeup.sh")
LOG_FILE = os.path.join(COMPANION_HOME, "requests", "watcher.log")

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

try:
    with open(REQUESTS_FILE, "r") as f:
        requests = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    log("ERROR: Could not read requests.json")
    sys.exit(1)

now = datetime.now()
changed = False

# Find last emergency wakeup timestamp for cooldown check
last_emergency = None
for r in requests:
    if r.get("type") == "emergency_wakeup" and r.get("status") in ("completed", "scheduled"):
        try:
            ts = datetime.fromisoformat(r["created"])
            if last_emergency is None or ts > last_emergency:
                last_emergency = ts
        except (ValueError, KeyError):
            continue

for r in requests:
    # Skip anything not actionable
    if r.get("status") not in ("self_approved", "approved"):
        # Check for expiration of pending wakeup requests
        if r.get("status") == "pending" and r.get("requested_time"):
            try:
                wake_time = datetime.fromisoformat(r["requested_time"])
                if wake_time < now:
                    r["status"] = "expired"
                    log(f"Expired request {r['id']}: {r['title']} (time passed)")
                    changed = True
            except (ValueError, KeyError):
                pass
        continue

    # Only process wakeup-type requests that have a time
    if r.get("requested_time") is None:
        continue

    try:
        wake_time = datetime.fromisoformat(r["requested_time"])
    except (ValueError, KeyError):
        log(f"ERROR: Bad time format in request {r['id']}")
        continue

    # Skip if the time has already passed
    if wake_time < now:
        if r["status"] in ("self_approved", "approved"):
            r["status"] = "expired"
            log(f"Expired request {r['id']}: {r['title']} (time passed before scheduling)")
            changed = True
        continue

    # For self-approved emergency wakeups, enforce 24-hour cooldown
    if r.get("type") == "emergency_wakeup" and r.get("status") == "self_approved":
        if last_emergency and (now - last_emergency) < timedelta(hours=24):
            r["type"] = "wakeup_request"
            r["status"] = "pending"
            r["body"] = r.get("body", "") + f"\n\n[Auto-note: Downgraded from emergency — cooldown not met (last: {last_emergency.strftime('%b %d %H:%M')}). Needs the human's approval.]"
            log(f"Downgraded {r['id']} from emergency to pending wakeup_request (cooldown)")
            changed = True
            continue

    # Schedule the wakeup via `at`
    at_time = wake_time.strftime("%H:%M %m/%d/%Y")
    reason = r.get("title", "Special wakeup").replace("'", "'\\''")  # Escape single quotes
    cmd = f"echo '{WAKEUP_SCRIPT} \"{reason}\"' | at {at_time} 2>/dev/null"

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            r["status"] = "scheduled"
            r["scheduled_at"] = now.isoformat()
            log(f"Scheduled request {r['id']}: {r['title']} for {wake_time.strftime('%b %d %H:%M')}")

            if r.get("type") == "emergency_wakeup":
                last_emergency = now
        else:
            log(f"ERROR: `at` command failed for {r['id']}: {result.stderr.strip()}")
    except Exception as e:
        log(f"ERROR: Failed to schedule {r['id']}: {str(e)}")
        continue

    changed = True

# Save if anything changed
if changed:
    try:
        with open(REQUESTS_FILE, "w") as f:
            json.dump(requests, f, indent=2)
        log("Saved updated requests.json")
    except Exception as e:
        log(f"ERROR: Failed to save requests.json: {str(e)}")

PYEOF
