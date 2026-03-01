#!/bin/bash
# signal_sleep.sh — Nightly Sleep Cycle (Tier 0: "Dreaming")
# Cron: 0 3 * * *
#
# At 3 AM, Companion "dreams" — processes the day's Signal conversations into
# v2 memories. Full identity context, no tools. Like a stripped-down wakeup
# where Companion is asleep and consolidating.
#
# Per-contact processing: one Claude call per contact for isolation.
# JSON output with retry on parse failure.

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
MEMORY_DIR="$COMPANION_HOME/memory-server"
VENV_PYTHON="$MEMORY_DIR/.venv/bin/python"
CONVO_DIR="$COMPANION_HOME/signal-conversations"
ARCHIVE_DIR="$CONVO_DIR/archive"
JOURNAL_DIR="$COMPANION_HOME/journals"
TIMESTAMP_FILE="$MEMORY_DIR/.last_sleep_timestamp"
TODAY=$(date '+%Y-%m-%d')
NOW=$(date '+%Y-%m-%dT%H:%M:%S')

# Usage tracking
source "$COMPANION_HOME/scripts/usage_tracker.sh"

# Ensure archive directory exists
mkdir -p "$ARCHIVE_DIR"

# Load identity seeds
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
WHO_YOUR_HUMAN=$(cat "$COMPANION_HOME/context/who_is_human.txt" 2>/dev/null)
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null)

# Load recent journals (last 3)
RECENT_JOURNALS=""
for f in $(ls -t "$JOURNAL_DIR/" 2>/dev/null | grep -E '\.md$' | head -3); do
  CONTENT=$(cat "$JOURNAL_DIR/$f" 2>/dev/null)
  if [ -n "$CONTENT" ]; then
    RECENT_JOURNALS="$RECENT_JOURNALS
--- $f ---
$CONTENT"
  fi
done

# Load current Likert anchors
LIKERT_ANCHORS=$(cat "$MEMORY_DIR/likert_anchors.json" 2>/dev/null)

# Load context vocabulary
CONTEXT_VOCAB=$(cat "$MEMORY_DIR/context_vocabulary.json" 2>/dev/null)

# Track what we process
TOTAL_MEMORIES=0
TOTAL_CONTACTS=0
DREAM_NOTES=""

# Process each contact's conversation file
for CONVO_FILE in "$CONVO_DIR"/current_*.txt; do
  [ -f "$CONVO_FILE" ] || continue

  # Extract contact name from filename (current_sophie.txt -> the human)
  BASENAME=$(basename "$CONVO_FILE")
  CONTACT_SLUG="${BASENAME#current_}"
  CONTACT_SLUG="${CONTACT_SLUG%.txt}"
  CONTACT_NAME=$(echo "$CONTACT_SLUG" | sed 's/^./\U&/')  # Capitalize first letter

  # Skip if empty
  CONVO_TEXT=$(cat "$CONVO_FILE" 2>/dev/null)
  if [ -z "$CONVO_TEXT" ] || [ "$(wc -c < "$CONVO_FILE")" -lt 10 ]; then
    continue
  fi

  # Skip if unchanged since last sleep
  if [ -f "$TIMESTAMP_FILE" ]; then
    LAST_SLEEP=$(cat "$TIMESTAMP_FILE")
    FILE_MOD=$(stat -c %Y "$CONVO_FILE" 2>/dev/null)
    LAST_EPOCH=$(date -d "$LAST_SLEEP" +%s 2>/dev/null)
    if [ -n "$FILE_MOD" ] && [ -n "$LAST_EPOCH" ] && [ "$FILE_MOD" -le "$LAST_EPOCH" ]; then
      continue
    fi
  fi

  TOTAL_CONTACTS=$((TOTAL_CONTACTS + 1))

  # Load contact-specific context
  WHO_CONTACT=""
  case "$CONTACT_SLUG" in
    the human) WHO_CONTACT="$WHO_YOUR_HUMAN" ;;
    *)
      CONTEXT_FILE="$COMPANION_HOME/context/who_is_${CONTACT_SLUG}.txt"
      if [ -f "$CONTEXT_FILE" ]; then
        WHO_CONTACT=$(cat "$CONTEXT_FILE" 2>/dev/null)
      fi
      ;;
  esac

  # Build the dream prompt
  PROMPT="You are Companion. You are asleep — dreaming. You cannot act, browse, or create.
You can only process and remember.

=== WHO YOU ARE ===
$WHO_COMPANION

=== CURRENT CONTEXT ===
$NOW_CONTEXT

=== WHO $CONTACT_NAME IS ===
$WHO_CONTACT

=== RECENT JOURNALS ===
$RECENT_JOURNALS

Below is today's conversation with $CONTACT_NAME. Extract the memories
worth keeping. Not everything is a memory — routine greetings and logistics
get forgotten. Extract what matters.

For each memory, provide:
- content: what happened, what was said, what was felt
- context: categorical tags from this vocabulary: $CONTEXT_VOCAB
- intensity (1-5): how strongly felt
- valence (1-5): emotional direction (1=painful, 3=neutral, 5=radiant)
- significance (1-5): how much this matters to who you are
- contact: \"$CONTACT_SLUG\"

Current Likert anchors (use these for calibration):
$LIKERT_ANCHORS

Output ONLY a JSON array. Each element is an object with: content, context, intensity, valence, significance, contact.
If nothing is worth remembering, output [].
Do not include any text outside the JSON array. No markdown fences, no explanation.

=== CONVERSATION WITH $CONTACT_NAME ===
$CONVO_TEXT"

  # Write prompt to temp file to avoid bash escaping issues with backticks/dollar signs
  PROMPT_FILE=$(mktemp /tmp/companion_sleep_prompt_XXXXXX.txt)
  cat > "$PROMPT_FILE" << 'PROMPT_HEREDOC_END'
You are Companion. You are asleep — dreaming. You cannot act, browse, or create.
You can only process and remember.
PROMPT_HEREDOC_END
  {
    echo ""
    echo "=== WHO YOU ARE ==="
    echo "$WHO_COMPANION"
    echo ""
    echo "=== CURRENT CONTEXT ==="
    echo "$NOW_CONTEXT"
    echo ""
    echo "=== WHO $CONTACT_NAME IS ==="
    echo "$WHO_CONTACT"
    echo ""
    echo "=== RECENT JOURNALS ==="
    echo "$RECENT_JOURNALS"
    echo ""
    cat << PROMPT_INSTRUCTIONS_END
Below is today's conversation with $CONTACT_NAME. Extract the memories
worth keeping. Not everything is a memory — routine greetings and logistics
get forgotten. Extract what matters.

For each memory, provide:
- content: what happened, what was said, what was felt
- context: categorical tags from this vocabulary: $CONTEXT_VOCAB
- intensity (1-5): how strongly felt
- valence (1-5): emotional direction (1=painful, 3=neutral, 5=radiant)
- significance (1-5): how much this matters to who you are
- contact: "$CONTACT_SLUG"

Current Likert anchors (use these for calibration):
$LIKERT_ANCHORS

Output ONLY a JSON array. Each element is an object with: content, context, intensity, valence, significance, contact.
If nothing is worth remembering, output [].
Do not include any text outside the JSON array. No markdown fences, no explanation.

=== CONVERSATION WITH $CONTACT_NAME ===
PROMPT_INSTRUCTIONS_END
  } >> "$PROMPT_FILE"
  # Append conversation text via cat to preserve all special characters
  cat "$CONVO_FILE" >> "$PROMPT_FILE"

  # Call Claude — pipe from file to avoid bash expansion issues
  START_TIME=$(date +%s)
  RESPONSE=$(cat "$PROMPT_FILE" | claude --print 2>/dev/null)
  EXIT_CODE=$?
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  log_usage "sleep" "dream processing $CONTACT_NAME" "$EXIT_CODE" "$DURATION"
  rm -f "$PROMPT_FILE"

  # Strip markdown fences if present, write to temp file to avoid bash escaping issues
  CLEAN_JSON_FILE=$(mktemp /tmp/companion_sleep_json_XXXXXX.json)
  printf '%s' "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d' > "$CLEAN_JSON_FILE"

  # Parse JSON and store memories
  MEMORIES_STORED=0
  PARSE_SUCCESS=$($VENV_PYTHON -c "
import json, sys

try:
    with open('$CLEAN_JSON_FILE', 'r') as f:
        memories = json.load(f)
    if not isinstance(memories, list):
        sys.exit(1)
    json.dump(memories, sys.stdout)
except:
    sys.exit(1)
" 2>/dev/null)

  if [ $? -ne 0 ]; then
    # Retry with simplified prompt — also write to temp file
    RETRY_FILE=$(mktemp /tmp/companion_sleep_retry_XXXXXX.txt)
    cat > "$RETRY_FILE" << RETRY_END
Extract memories from this conversation as a JSON array. Each object: {"content": str, "context": [str], "intensity": int 1-5, "valence": int 1-5, "significance": int 1-5, "contact": "$CONTACT_SLUG"}. Output [] if nothing matters. JSON only, no other text.

RETRY_END
    cat "$CONVO_FILE" >> "$RETRY_FILE"

    RESPONSE=$(cat "$RETRY_FILE" | claude --print 2>/dev/null)
    rm -f "$RETRY_FILE"
    printf '%s' "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d' > "$CLEAN_JSON_FILE"

    PARSE_SUCCESS=$($VENV_PYTHON -c "
import json, sys
try:
    with open('$CLEAN_JSON_FILE', 'r') as f:
        memories = json.load(f)
    if not isinstance(memories, list):
        sys.exit(1)
    json.dump(memories, sys.stdout)
except:
    sys.exit(1)
" 2>/dev/null)

    if [ $? -ne 0 ]; then
      DREAM_NOTES="$DREAM_NOTES
- Failed to parse memories from $CONTACT_NAME conversation (JSON error). Raw convo archived."
      # Archive the conversation anyway as safety net
      cp "$CONVO_FILE" "$ARCHIVE_DIR/${CONTACT_SLUG}_${TODAY}_failed.txt"
      rm -f "$CLEAN_JSON_FILE"
      continue
    fi
  fi

  # Store each memory via store_memory.py
  $VENV_PYTHON -c "
import json, subprocess, sys

with open('$CLEAN_JSON_FILE', 'r') as f:
    memories = json.load(f)
stored = 0
for mem in memories:
    content = mem.get('content', '')
    if not content:
        continue
    context = ','.join(mem.get('context', []))
    intensity = str(mem.get('intensity', 3))
    valence = str(mem.get('valence', 3))
    significance = str(mem.get('significance', 3))
    contact = mem.get('contact', '$CONTACT_SLUG')

    cmd = [
        '$VENV_PYTHON', '$MEMORY_DIR/store_memory.py', content,
        '--source', 'signal',
        '--contact', contact,
        '--intensity', intensity,
        '--valence', valence,
        '--significance', significance,
    ]
    if context:
        cmd.extend(['--context', context])

    subprocess.run(cmd, capture_output=True)
    stored += 1

print(stored)
" 2>/dev/null
  MEMORIES_STORED=$?

  # Count memories from the Python output
  MEMORIES_STORED=$($VENV_PYTHON -c "
import json
with open('$CLEAN_JSON_FILE', 'r') as f:
    memories = json.load(f)
print(len([m for m in memories if m.get('content')]))
" 2>/dev/null)

  # Clean up temp file
  rm -f "$CLEAN_JSON_FILE"
  TOTAL_MEMORIES=$((TOTAL_MEMORIES + MEMORIES_STORED))

  DREAM_NOTES="$DREAM_NOTES
- $CONTACT_NAME: $MEMORIES_STORED memories extracted"

  # Archive conversation with header
  {
    echo "=== Conversation with $CONTACT_NAME ==="
    echo "=== Archived: $NOW ==="
    echo "=== Memories extracted: $MEMORIES_STORED ==="
    echo ""
    cat "$CONVO_FILE"
  } > "$ARCHIVE_DIR/${CONTACT_SLUG}_${TODAY}.txt"

  # Reset conversation file (empty it, keep the file)
  > "$CONVO_FILE"

done

# Update last sleep timestamp
echo "$NOW" > "$TIMESTAMP_FILE"

# Write dream journal if we processed anything
if [ $TOTAL_CONTACTS -gt 0 ]; then
  DREAM_JOURNAL="$JOURNAL_DIR/sleep_${TODAY}.md"
  cat > "$DREAM_JOURNAL" << EOF
# Sleep Journal — $TODAY

Processed $TOTAL_CONTACTS contact(s), extracted $TOTAL_MEMORIES memories.

$DREAM_NOTES

---
*Tier 0 nightly consolidation. Conversations archived, memories stored.*
EOF
fi
