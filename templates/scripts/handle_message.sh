#!/bin/bash
# handle_message.sh — Process a normal Signal message and generate a companion response
# Called by signal_listener.sh for non-task messages
# Usage: handle_message.sh "+1SENDER_NUMBER" "message body"
#
# IMPORTANT: ALL claude calls MUST use < /dev/null or they hang

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
SIGNAL_CONFIG="$COMPANION_HOME/scripts/signal_config.sh"

source "$SIGNAL_CONFIG"

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SENDER="$1"
MESSAGE="$2"

if [ -z "$MESSAGE" ]; then
    exit 0
fi

# Load context
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
WHO_HUMAN=$(cat "$COMPANION_HOME/context/who_is_human.txt" 2>/dev/null)
# Load now.txt with safety cap — prevent bloated context from causing timeouts
NOW_FILE="$COMPANION_HOME/context/now.txt"
NOW_LINES=$(wc -l < "$NOW_FILE" 2>/dev/null || echo 0)
if [ "$NOW_LINES" -gt 50 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: now.txt is $NOW_LINES lines, capping to 50" >&2
  NOW_CONTEXT=$(head -50 "$NOW_FILE")
else
  NOW_CONTEXT=$(cat "$NOW_FILE" 2>/dev/null)
fi

# Load recent conversation for continuity
CONVO_FILE="$SIGNAL_CONVERSATIONS/current.txt"
RECENT_CONVO=""
if [ -f "$CONVO_FILE" ]; then
    RECENT_CONVO=$(tail -50 "$CONVO_FILE")
fi

# Log incoming message
echo "[$(date '+%Y-%m-%d %H:%M')] Human: $MESSAGE" >> "$CONVO_FILE"

# Generate response via Claude Code
# CRITICAL: < /dev/null prevents hanging on stdin
RESPONSE=$(timeout 120 claude -p --dangerously-skip-permissions --max-turns 3 \
"$WHO_COMPANION

$WHO_HUMAN

$NOW_CONTEXT

=== RECENT CONVERSATION ===
$RECENT_CONVO

=== NEW MESSAGE FROM HUMAN ===
$MESSAGE

Reply naturally as yourself. Keep it conversational and concise (1-3 sentences unless more is needed). 
Do NOT use any tools. Just output your reply as plain text." < /dev/null 2>&1)

# Clean up response (remove tool artifacts, trim whitespace)
CLEAN_RESPONSE=$(echo "$RESPONSE" | grep -v "^>" | grep -v "^─" | tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

if [ -z "$CLEAN_RESPONSE" ]; then
    CLEAN_RESPONSE="(I tried to respond but something went wrong. Try again?)"
fi

# Log outgoing message
echo "[$(date '+%Y-%m-%d %H:%M')] Companion: $CLEAN_RESPONSE" >> "$CONVO_FILE"

# Send reply via Signal (with flock)
(
    flock -x -w 30 200
    signal-cli -a "$COMPANION_NUMBER" send -m "$CLEAN_RESPONSE" "$HUMAN_NUMBER" 2>/dev/null
) 200>/tmp/signal_send.lock
