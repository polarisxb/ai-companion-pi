#!/bin/bash
# publish_cycle.sh — Run the Substack publishing cycle
# Can be called from wakeup.sh, cron, or manually
#
# What it does:
#   1. Checks for approved posts in the queue
#   2. Publishes them to Substack
#   3. Sends the human a Signal notification with the link
#
# Usage:
#   bash publish_cycle.sh              # Publish all approved
#   bash publish_cycle.sh --dry-run    # Preview what would publish

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
SCRIPTS_DIR="$COMPANION_HOME/scripts"
SUBSTACK_DIR="$COMPANION_HOME/substack"
QUEUE_FILE="$SUBSTACK_DIR/queue.json"

# Source Signal config for notifications
source "$SCRIPTS_DIR/signal_config.sh" 2>/dev/null

# Check if there are approved posts
APPROVED_COUNT=$(python3 -c "
import json
try:
    with open('$QUEUE_FILE') as f:
        q = json.load(f)
    print(len([p for p in q if p['status'] == 'approved']))
except:
    print(0)
" 2>/dev/null)

if [ "$APPROVED_COUNT" = "0" ]; then
    # Nothing to publish, exit silently
    exit 0
fi

echo "[$(date)] Publishing $APPROVED_COUNT approved post(s)..."

# Run the publisher
OUTPUT=$(python3 "$SCRIPTS_DIR/substack_publish.py" "$@" 2>&1)
EXIT_CODE=$?

echo "$OUTPUT"

# If we published something, notify the human
if [ $EXIT_CODE -eq 0 ] && [ "$1" != "--dry-run" ]; then
    # Extract published URLs from queue
    PUBLISHED_INFO=$(python3 -c "
import json
try:
    with open('$QUEUE_FILE') as f:
        q = json.load(f)
    recent = [p for p in q if p['status'] == 'published' and p.get('substack_url')]
    # Get ones published in last 5 minutes
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(minutes=5)).isoformat()
    fresh = [p for p in recent if (p.get('published_at', '') or '') > cutoff]
    for p in fresh:
        print(f\"{p['title']} -> {p['substack_url']}\")
except:
    pass
" 2>/dev/null)

    if [ -n "$PUBLISHED_INFO" ]; then
        # Send Signal notification
        if type signal_send_text &>/dev/null; then
            signal_send_text "I just published to Substack! $PUBLISHED_INFO"
        fi
    fi
fi
