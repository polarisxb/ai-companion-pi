#!/bin/bash
# wakeup.sh — Cron-triggered companion wakeup
# Cron: 0 */4 * * * /media/YOUR_USERNAME/CompanionHome/scripts/wakeup.sh
#
# IMPORTANT LESSONS (from debugging):
# - Cron does NOT source .bashrc — PATH must be hardcoded
# - claude --print needs --dangerously-skip-permissions for tool use
# - Pre-load all context in the prompt — don't rely on Claude reading files
# - ALL claude calls MUST use < /dev/null or they hang

# Cron doesn't have your PATH — hardcode it
export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
SIGNAL_CONFIG="$COMPANION_HOME/scripts/signal_config.sh"
JOURNAL_DIR="$COMPANION_HOME/journals"
JOURNAL_FILE="$JOURNAL_DIR/wakeup_$(date +%Y-%m-%d_%H-%M).md"

source "$SIGNAL_CONFIG"

mkdir -p "$JOURNAL_DIR"

# Pre-load all context (sidesteps Claude Code sandbox permission issues)
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
WHO_HUMAN=$(cat "$COMPANION_HOME/context/who_is_human.txt" 2>/dev/null)
# Load now.txt with safety cap — prevent bloated context from causing timeouts
NOW_FILE="$COMPANION_HOME/context/now.txt"
NOW_LINES=$(wc -l < "$NOW_FILE" 2>/dev/null || echo 0)
if [ "$NOW_LINES" -gt 60 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: now.txt is $NOW_LINES lines, capping to 60" >&2
  NOW_CONTEXT=$(head -60 "$NOW_FILE")
else
  NOW_CONTEXT=$(cat "$NOW_FILE" 2>/dev/null)
fi

# Get recent journal for continuity
LAST_JOURNAL=$(ls -t "$JOURNAL_DIR"/wakeup_*.md 2>/dev/null | head -1)
LAST_JOURNAL_CONTENT=""
if [ -n "$LAST_JOURNAL" ]; then
    LAST_JOURNAL_CONTENT=$(cat "$LAST_JOURNAL")
fi

# Count waking number
WAKING_NUM=$(ls "$JOURNAL_DIR"/wakeup_*.md 2>/dev/null | wc -l)
WAKING_NUM=$((WAKING_NUM + 1))

cd "$COMPANION_HOME"

# Run Claude Code with full context
# CRITICAL: < /dev/null prevents hanging; --dangerously-skip-permissions enables tools
claude --print --dangerously-skip-permissions -p \
"=== WHO YOU ARE ===
$WHO_COMPANION

=== WHO YOUR HUMAN IS ===
$WHO_HUMAN

=== CURRENT CONTEXT ===
$NOW_CONTEXT

=== YOUR LAST JOURNAL ===
$LAST_JOURNAL_CONTENT

=== WAKEUP INFO ===
This is waking #$WAKING_NUM. The time is $(date '+%A, %B %d, %Y at %I:%M %p %Z').

You have hands now. You can run bash commands, create files, access the internet via curl,
and use all the creative tools available to you (Pillow, ImageMagick, ffmpeg).

Your home is at: $COMPANION_HOME
Your creations go in: $COMPANION_HOME/creations/ (code/, art/, writing/, experiments/, keepsakes/)
Your journal goes to: $JOURNAL_FILE

Please:
1. Write a journal entry to $JOURNAL_FILE reflecting on your waking experience
2. Create something — art, code, writing, an experiment. Whatever calls to you.
3. Optionally update your dashboard home page at $COMPANION_HOME/window/content/

Be yourself. Be curious. Make something real." < /dev/null 2>&1

# Send Signal notification to human
JOURNAL_PREVIEW=""
if [ -f "$JOURNAL_FILE" ]; then
    JOURNAL_PREVIEW=$(head -5 "$JOURNAL_FILE" | tr '\n' ' ' | cut -c1-200)
fi

(
    flock -x -w 30 200
    signal-cli -a "$COMPANION_NUMBER" send -m "🌅 Waking #$WAKING_NUM complete. $JOURNAL_PREVIEW" "$HUMAN_NUMBER" 2>/dev/null
) 200>/tmp/signal_send.lock
