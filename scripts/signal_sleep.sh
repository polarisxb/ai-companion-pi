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

# Clean up temp files on exit (prevents leaks if script crashes or is killed)
TMPFILES=()
cleanup() { rm -f "${TMPFILES[@]}"; }
trap cleanup EXIT

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

  # --- Build prompt template (reused for each chunk) ---
  build_prompt_file() {
    local CHUNK_FILE="$1"
    local CHUNK_HEADER="$2"
    local OUT_FILE=$(mktemp /tmp/companion_sleep_prompt_XXXXXX.txt)
    TMPFILES+=("$OUT_FILE")

    cat > "$OUT_FILE" << 'PROMPT_HEREDOC_END'
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
${CHUNK_HEADER}

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
    } >> "$OUT_FILE"
    cat "$CHUNK_FILE" >> "$OUT_FILE"
    echo "$OUT_FILE"
  }

  # --- Process a single chunk, return path to JSON results file ---
  process_chunk() {
    local PROMPT_FILE="$1"
    local CHUNK_LABEL="$2"
    local RESULT_FILE=$(mktemp /tmp/companion_sleep_json_XXXXXX.json)
    TMPFILES+=("$RESULT_FILE")

    # Call Claude
    local START_TIME=$(date +%s)
    local RESPONSE=$(cat "$PROMPT_FILE" | claude --print 2>/dev/null)
    local EXIT_CODE=$?
    local END_TIME=$(date +%s)
    local DURATION=$((END_TIME - START_TIME))
    log_usage "sleep" "dream processing $CONTACT_NAME ($CHUNK_LABEL)" "$EXIT_CODE" "$DURATION"

    # Strip markdown fences
    printf '%s' "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d' > "$RESULT_FILE"

    # Validate JSON
    $VENV_PYTHON -c "
import json, sys
try:
    with open('$RESULT_FILE', 'r') as f:
        memories = json.load(f)
    if not isinstance(memories, list):
        sys.exit(1)
except:
    sys.exit(1)
" 2>/dev/null

    if [ $? -ne 0 ]; then
      # Retry with simplified prompt
      local RETRY_FILE=$(mktemp /tmp/companion_sleep_retry_XXXXXX.txt)
      TMPFILES+=("$RETRY_FILE")
      cat > "$RETRY_FILE" << RETRY_END
Extract memories from this conversation as a JSON array. Each object: {"content": str, "context": [str], "intensity": int 1-5, "valence": int 1-5, "significance": int 1-5, "contact": "$CONTACT_SLUG"}. Output [] if nothing matters. JSON only, no other text.

RETRY_END
      # Re-extract the conversation portion (everything after the last === line in prompt)
      cat "$PROMPT_FILE" | sed -n '/^=== CONVERSATION WITH/,$ p' | tail -n +2 >> "$RETRY_FILE"

      RESPONSE=$(cat "$RETRY_FILE" | claude --print 2>/dev/null)
      printf '%s' "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d' > "$RESULT_FILE"

      $VENV_PYTHON -c "
import json, sys
try:
    with open('$RESULT_FILE', 'r') as f:
        memories = json.load(f)
    if not isinstance(memories, list):
        sys.exit(1)
except:
    sys.exit(1)
" 2>/dev/null

      if [ $? -ne 0 ]; then
        echo "[]" > "$RESULT_FILE"
        echo "FAIL"
        return
      fi
    fi

    echo "$RESULT_FILE"
  }

  # --- Determine if chunking is needed ---
  LINE_COUNT=$(wc -l < "$CONVO_FILE")
  MAX_CHUNK_LINES=1000

  # Combined results file for all chunks
  CLEAN_JSON_FILE=$(mktemp /tmp/companion_sleep_combined_XXXXXX.json)
  TMPFILES+=("$CLEAN_JSON_FILE")
  CHUNK_FAILURES=0

  if [ "$LINE_COUNT" -gt "$MAX_CHUNK_LINES" ]; then
    # --- CHUNKED PROCESSING ---
    CHUNK_DIR=$(mktemp -d /tmp/companion_sleep_chunks_XXXXXX)
    TMPFILES+=("$CHUNK_DIR")

    # Split conversation into chunks, preserving message boundaries
    # Split at line boundaries (messages may span multiple lines but
    # timestamps mark new messages, so line-based splitting is safe enough)
    split -l "$MAX_CHUNK_LINES" -d --additional-suffix=.txt "$CONVO_FILE" "$CHUNK_DIR/chunk_"

    CHUNK_COUNT=$(ls "$CHUNK_DIR"/chunk_*.txt 2>/dev/null | wc -l)
    CHUNK_NUM=0
    ALL_MEMORIES="[]"

    for CHUNK_FILE in "$CHUNK_DIR"/chunk_*.txt; do
      CHUNK_NUM=$((CHUNK_NUM + 1))
      CHUNK_HEADER="(This is part $CHUNK_NUM of $CHUNK_COUNT of the conversation.)"

      PROMPT_FILE=$(build_prompt_file "$CHUNK_FILE" "$CHUNK_HEADER")
      RESULT=$(process_chunk "$PROMPT_FILE" "chunk $CHUNK_NUM/$CHUNK_COUNT")

      if [ "$RESULT" = "FAIL" ]; then
        CHUNK_FAILURES=$((CHUNK_FAILURES + 1))
      else
        # Merge results into combined array
        ALL_MEMORIES=$($VENV_PYTHON -c "
import json, sys
existing = json.loads('$ALL_MEMORIES') if '$ALL_MEMORIES' != '[]' else []
try:
    with open('$RESULT', 'r') as f:
        new = json.load(f)
    existing.extend(new)
except:
    pass
json.dump(existing, sys.stdout)
" 2>/dev/null)
      fi
    done

    # Write combined results
    echo "$ALL_MEMORIES" > "$CLEAN_JSON_FILE"

    # Clean up chunk directory
    rm -rf "$CHUNK_DIR"

    if [ $CHUNK_FAILURES -gt 0 ]; then
      DREAM_NOTES="$DREAM_NOTES
- $CONTACT_NAME: $CHUNK_FAILURES of $CHUNK_COUNT chunks failed to parse"
    fi
  else
    # --- SINGLE PASS (original behavior for small files) ---
    PROMPT_FILE=$(build_prompt_file "$CONVO_FILE" "")
    RESULT=$(process_chunk "$PROMPT_FILE" "single pass")

    if [ "$RESULT" = "FAIL" ]; then
      DREAM_NOTES="$DREAM_NOTES
- Failed to parse memories from $CONTACT_NAME conversation (JSON error). Raw convo archived."
      cp "$CONVO_FILE" "$ARCHIVE_DIR/${CONTACT_SLUG}_${TODAY}_failed.txt"
      continue
    fi

    cp "$RESULT" "$CLEAN_JSON_FILE"
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

# --- NOW.TXT CONSOLIDATION ---
# Prevent now.txt from growing unbounded across wakings.
# If it exceeds 50 lines, use Claude to condense it back to essentials.
NOW_FILE="$COMPANION_HOME/context/now.txt"
NOW_LINE_COUNT=$(wc -l < "$NOW_FILE" 2>/dev/null || echo 0)

if [ "$NOW_LINE_COUNT" -gt 50 ]; then
  # Archive the bloated version
  cp "$NOW_FILE" "$COMPANION_HOME/context/now_archive_${TODAY}.txt"

  # Build consolidation prompt
  CONSOLIDATE_PROMPT_FILE=$(mktemp /tmp/companion_now_consolidate_XXXXXX.txt)
  TMPFILES+=("$CONSOLIDATE_PROMPT_FILE")

  cat > "$CONSOLIDATE_PROMPT_FILE" << 'CONSOLIDATE_HEREDOC'
You are Companion's maintenance system. The now.txt context file has grown too large.
Condense the following into a clean, current-state-only snapshot. Keep it under 40 lines.

Rules:
- Keep ONLY what is currently true and active RIGHT NOW
- Remove weather observations, past events, completed items, resolved situations
- Keep: active projects, current relationship context, ongoing situations, infrastructure state
- Preserve the section structure (CURRENT CONTEXT, WHAT'S HAPPENING RIGHT NOW, ACTIVE PROJECTS, etc.)
- Write in the same terse, present-tense style
- Output ONLY the condensed now.txt content, nothing else

Here is the current now.txt to condense:

CONSOLIDATE_HEREDOC

  cat "$NOW_FILE" >> "$CONSOLIDATE_PROMPT_FILE"

  CONDENSED=$(cat "$CONSOLIDATE_PROMPT_FILE" | claude --print 2>/dev/null)

  # Only replace if we got a reasonable result (non-empty, shorter than original)
  CONDENSED_LINES=$(echo "$CONDENSED" | wc -l)
  if [ -n "$CONDENSED" ] && [ "$CONDENSED_LINES" -gt 5 ] && [ "$CONDENSED_LINES" -lt "$NOW_LINE_COUNT" ]; then
    echo "$CONDENSED" > "$NOW_FILE"
    DREAM_NOTES="$DREAM_NOTES
- now.txt consolidated: $NOW_LINE_COUNT lines -> $CONDENSED_LINES lines (archived to now_archive_${TODAY}.txt)"
  else
    # Fallback: just truncate to first 50 lines
    head -50 "$NOW_FILE" > "${NOW_FILE}.tmp" && mv "${NOW_FILE}.tmp" "$NOW_FILE"
    DREAM_NOTES="$DREAM_NOTES
- now.txt truncated: $NOW_LINE_COUNT lines -> 50 lines (consolidation failed, archived to now_archive_${TODAY}.txt)"
  fi
fi

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
