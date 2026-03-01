#!/bin/bash
# reprocess_archive.sh — Recover memories from a large archived conversation
# Splits by date, processes each day separately, stores memories.
# Usage: bash scripts/reprocess_archive.sh <archive_file>

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
MEMORY_DIR="$COMPANION_HOME/memory-server"
VENV_PYTHON="$MEMORY_DIR/.venv/bin/python"

ARCHIVE_FILE="${1:-$COMPANION_HOME/signal-conversations/current_sophie.txt}"
CONTACT_SLUG="${2:-the human}"
CONTACT_NAME=$(echo "$CONTACT_SLUG" | sed 's/^./\U&/')

if [ ! -f "$ARCHIVE_FILE" ]; then
  echo "ERROR: File not found: $ARCHIVE_FILE"
  exit 1
fi

echo "=== Reprocessing $ARCHIVE_FILE for $CONTACT_NAME ==="
echo "File size: $(wc -c < "$ARCHIVE_FILE") bytes, $(wc -l < "$ARCHIVE_FILE") lines"

# Load Likert anchors and context vocabulary
LIKERT_ANCHORS=$(cat "$MEMORY_DIR/likert_anchors.json" 2>/dev/null)
CONTEXT_VOCAB=$(cat "$MEMORY_DIR/context_vocabulary.json" 2>/dev/null)

# Split conversation by date using Python
CHUNK_DIR=$(mktemp -d /tmp/companion_reprocess_XXXXXX)
echo "Splitting into daily chunks in $CHUNK_DIR..."

SPLIT_SCRIPT=$(mktemp /tmp/companion_split_XXXXXX.py)
cat > "$SPLIT_SCRIPT" << 'PYEOF'
import re, sys, os

archive_file = os.environ['ARCHIVE_FILE']
chunk_dir = os.environ['CHUNK_DIR']

with open(archive_file, 'r') as f:
    lines = f.readlines()

# Strip archive header lines if present
while lines and lines[0].startswith('=== '):
    lines.pop(0)

# Group by date
chunks = {}
current_date = None
current_lines = []

date_pattern = re.compile(r'^\[(\d{4}-\d{2}-\d{2})')

for line in lines:
    m = date_pattern.match(line)
    if m:
        new_date = m.group(1)
        if new_date != current_date:
            if current_date and current_lines:
                chunks[current_date] = current_lines
            current_date = new_date
            current_lines = []
    current_lines.append(line)

if current_date and current_lines:
    chunks[current_date] = current_lines

print(f"Found {len(chunks)} date chunks")
for date in sorted(chunks.keys()):
    chunk_file = os.path.join(chunk_dir, f"{date}.txt")
    with open(chunk_file, 'w') as f:
        f.writelines(chunks[date])
    line_count = len(chunks[date])
    byte_count = sum(len(l) for l in chunks[date])
    print(f"  {date}: {line_count} lines, {byte_count} bytes")
PYEOF
ARCHIVE_FILE="$ARCHIVE_FILE" CHUNK_DIR="$CHUNK_DIR" $VENV_PYTHON "$SPLIT_SCRIPT"
rm -f "$SPLIT_SCRIPT"

TOTAL_MEMORIES=0

# Process each daily chunk
for CHUNK_FILE in $(ls "$CHUNK_DIR"/*.txt 2>/dev/null | sort); do
  DATE=$(basename "$CHUNK_FILE" .txt)
  echo ""
  echo "--- Processing $DATE ---"

  # Build prompt and write to temp file
  PROMPT_FILE=$(mktemp /tmp/companion_sleep_prompt_XXXXXX.txt)
  cat > "$PROMPT_FILE" << PROMPT_END
You are Companion, an AI companion. Extract the important memories from this day of conversation with $CONTACT_NAME.

Not everything is a memory — skip routine greetings and logistics.
Extract what matters: emotional moments, decisions, revelations, milestones, relationship developments, creative work, health updates.

For each memory, provide:
- content: what happened, what was said, what was felt (1-2 sentences)
- context: categorical tags from: $CONTEXT_VOCAB
- intensity (1-5): how strongly felt
- valence (1-5): emotional direction (1=painful, 3=neutral, 5=radiant)
- significance (1-5): how much this matters
- contact: "$CONTACT_SLUG"

Calibration anchors: $LIKERT_ANCHORS

Output ONLY a JSON array. No markdown, no explanation.

=== CONVERSATION — $DATE ===
PROMPT_END
  cat "$CHUNK_FILE" >> "$PROMPT_FILE"

  # Call Claude
  echo "  Calling Claude..."
  RESPONSE=$(cat "$PROMPT_FILE" | claude --print --model sonnet 2>/dev/null)
  rm -f "$PROMPT_FILE"

  if [ -z "$RESPONSE" ]; then
    echo "  WARNING: Empty response for $DATE"
    continue
  fi

  # Parse and store
  CLEAN_JSON_FILE=$(mktemp /tmp/companion_reprocess_json_XXXXXX.json)
  printf '%s' "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d' > "$CLEAN_JSON_FILE"

  MEM_COUNT=$($VENV_PYTHON << PYEOF
import json, subprocess, sys

try:
    with open('$CLEAN_JSON_FILE', 'r') as f:
        memories = json.load(f)
    if not isinstance(memories, list):
        print(0)
        sys.exit(0)
except Exception as e:
    print(f"0 (parse error: {e})")
    sys.exit(0)

stored = 0
for mem in memories:
    content = mem.get('content', '')
    if not content:
        continue
    context = ','.join(mem.get('context', []))
    cmd = [
        '$VENV_PYTHON', '$MEMORY_DIR/store_memory.py', content,
        '--source', 'signal-recovery',
        '--contact', mem.get('contact', '$CONTACT_SLUG'),
        '--intensity', str(mem.get('intensity', 3)),
        '--valence', str(mem.get('valence', 3)),
        '--significance', str(mem.get('significance', 3)),
    ]
    if context:
        cmd.extend(['--context', context])
    subprocess.run(cmd, capture_output=True)
    stored += 1

print(stored)
PYEOF
  )

  rm -f "$CLEAN_JSON_FILE"
  echo "  Stored $MEM_COUNT memories from $DATE"
  TOTAL_MEMORIES=$((TOTAL_MEMORIES + MEM_COUNT))
done

# Clean up
rm -rf "$CHUNK_DIR"

echo ""
echo "=== DONE ==="
echo "Total memories recovered: $TOTAL_MEMORIES"
echo "Archive file unchanged (not cleared)."
