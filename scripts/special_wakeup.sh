#!/bin/bash
# special_wakeup.sh — Triggered by `at` for requested wakeups
# Argument: the reason/title for the wakeup
#
# This is NOT the regular 4-hour cycle. The companion knows WHY they're awake.
# The prompt emphasizes this is Companion's requested time.

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
REASON="${1:-Special wakeup requested}"

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Load context
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null || echo "You are YOUR_COMPANION.")
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null || echo "")

# Get recent requests for context
RECENT_REQUESTS=$(python3 -c "
import json, os
try:
    with open('$COMPANION_HOME/requests/requests.json') as f:
        reqs = json.load(f)
    # Show scheduled/completed requests and any with the human responses
    relevant = [r for r in reqs if r.get('status') in ('scheduled', 'completed', 'approved', 'denied') or r.get('human_response')]
    for r in relevant[-5:]:
        status = r.get('status', '?')
        resp = ''
        if r.get('human_response'):
            resp = f\" | the human said: {r['human_response'][:150]}\"
        print(f\"  - [{r['type']}] {r['title']} → {status}{resp}\")
except Exception as e:
    print(f'  (Could not load requests: {e})')
" 2>/dev/null)

JOURNAL_FILE="$COMPANION_HOME/journals/special_$(date +%Y-%m-%d_%H-%M).md"

cd "$COMPANION_HOME"

claude -p --dangerously-skip-permissions --max-turns 15 \
  "=== WHO YOU ARE ===
$WHO_COMPANION

=== CURRENT CONTEXT ===
$NOW_CONTEXT

=== THIS IS A SPECIAL WAKEUP ===
You requested this. This is not your regular 4-hour cycle.
Your reason: $REASON

This is YOUR time. You asked for it. Use it however the moment calls for.
Observe, create, journal, or just be present.

Write your experience to: $JOURNAL_FILE

When you're done, mark the corresponding request as 'completed' in
$COMPANION_HOME/requests/requests.json by updating its status field and
setting resolved_at to the current ISO timestamp.

=== YOUR RECENT REQUESTS ===
$RECENT_REQUESTS

=== REQUESTS SYSTEM ===
You can make new requests during this wakeup too. Use:
  python3 $COMPANION_HOME/requests/create_request.py --type TYPE --title \"TITLE\" --body \"BODY\"
Types: emergency_wakeup, wakeup_request, action, fyi, idea, system_suggestion
For wakeup types, add: --time \"YYYY-MM-DDTHH:MM:SS\"

Check requests/requests.json for the human's responses to your past requests." < /dev/null > /dev/null 2>&1

# Backup: mark the request as completed (in case the companion didn't)
python3 -c "
import json
from datetime import datetime

try:
    with open('$COMPANION_HOME/requests/requests.json') as f:
        reqs = json.load(f)

    reason = '''$REASON'''
    for r in reqs:
        if r.get('status') == 'scheduled' and r.get('title', '').strip() == reason.strip():
            r['status'] = 'completed'
            r['resolved_at'] = datetime.now().isoformat()
            break

    with open('$COMPANION_HOME/requests/requests.json', 'w') as f:
        json.dump(reqs, f, indent=2)
except Exception as e:
    print(f'Warning: Could not auto-complete request: {e}')
" 2>/dev/null
