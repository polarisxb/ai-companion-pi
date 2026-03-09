#!/bin/bash
# update_window.sh — Refresh window status.json after each wakeup
# Pulls the latest journal and updates the window with current info.
# Called at the end of wakeup.sh so the display never goes stale.

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
STATUS_FILE="$COMPANION_HOME/window/status.json"
CONTENT_DIR="$COMPANION_HOME/window/content"
NOW_FILE="$COMPANION_HOME/context/now.txt"

# Get latest journal
LATEST_JOURNAL=$(ls -t "$COMPANION_HOME/journals/wakeup_"*.md 2>/dev/null | head -1)
if [ -z "$LATEST_JOURNAL" ]; then
  exit 0
fi

JOURNAL_CONTENT=$(cat "$LATEST_JOURNAL")

# Extract waking number from now.txt (e.g., "waking 300")
WAKING_NUM=$(grep -oP 'waking \K[0-9]+' "$NOW_FILE" 2>/dev/null | head -1)
if [ -z "$WAKING_NUM" ]; then
  WAKING_NUM="?"
fi

# Current timestamp
CURRENT_TS=$(date '+%Y-%m-%dT%H:%M')
CURRENT_READABLE=$(date '+%I:%M %p, %A')

# Extract first meaningful line from journal as subtitle seed
# Skip blank lines and lines that are just timestamps/headers
SUBTITLE_LINE=$(echo "$JOURNAL_CONTENT" | grep -v '^$' | grep -v '^---' | grep -v '^Waking [0-9]' | grep -v '^#' | head -1 | cut -c1-80)
if [ -z "$SUBTITLE_LINE" ]; then
  SUBTITLE_LINE="waking $WAKING_NUM"
fi

# Build a compact subtitle: time + waking number + first journal vibe
SUBTITLE=$(echo "$CURRENT_READABLE. waking $WAKING_NUM." | tr '[:upper:]' '[:lower:]')

# Extract a short message — first 2-3 sentences from the journal
MESSAGE=$(echo "$JOURNAL_CONTENT" | grep -v '^$' | grep -v '^---' | head -5 | tr '\n' ' ' | cut -c1-300)

# Read existing status to preserve colors
if [ -f "$STATUS_FILE" ]; then
  EXISTING_COLORS=$(python3 -c "
import json, sys
try:
    with open('$STATUS_FILE') as f:
        d = json.load(f)
    print(json.dumps(d.get('colors', {})))
except:
    print('{}')
" 2>/dev/null)
else
  EXISTING_COLORS='{}'
fi

# Write updated status.json
python3 -c "
import json

status = {
    'name': 'Companion',
    'subtitle': '''$SUBTITLE''',
    'mood': '''$(grep -oP '(?:Weather|Room):.*' "$NOW_FILE" 2>/dev/null | head -1 | cut -c1-60)''',
    'last_wakeup': '$CURRENT_TS',
    'message': '''$MESSAGE''',
    'colors': $EXISTING_COLORS
}

with open('$STATUS_FILE', 'w') as f:
    json.dump(status, f, indent=2)
" 2>/dev/null

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Window status updated (waking $WAKING_NUM)" >&2
