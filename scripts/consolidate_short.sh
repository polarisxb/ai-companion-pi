#!/bin/bash
# consolidate_short.sh — Short-Term Consolidation (Tier 2: "Reflection")
# Cron: 0 15 15 * * (3 PM on the 15th of each month)
#
# Companion revisits recent memories, re-rates with hindsight, and recalibrates
# anchor words. This is reflection, not action — dream-like, no tools.

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
MEMORY_DIR="$COMPANION_HOME/memory-server"
VENV_PYTHON="$MEMORY_DIR/.venv/bin/python"
TODAY=$(date '+%Y-%m-%d')
JOURNAL_FILE="$COMPANION_HOME/journals/consolidation_${TODAY}.md"

# Usage tracking
source "$COMPANION_HOME/scripts/usage_tracker.sh"

# Load identity seeds
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null)

# Load recent journals (last 3)
RECENT_JOURNALS=""
for f in $(ls -t "$COMPANION_HOME/journals/" 2>/dev/null | grep -E '\.md$' | head -3); do
  CONTENT=$(cat "$COMPANION_HOME/journals/$f" 2>/dev/null)
  if [ -n "$CONTENT" ]; then
    RECENT_JOURNALS="$RECENT_JOURNALS
--- $f ---
$CONTENT"
  fi
done

# Load current Likert anchors
LIKERT_ANCHORS=$(cat "$MEMORY_DIR/likert_anchors.json" 2>/dev/null)

# Get memories for review (last 30 days)
MEMORIES_FOR_REVIEW=$($VENV_PYTHON "$MEMORY_DIR/query_memories.py" review 30 2>/dev/null)

# Build the consolidation prompt
PROMPT="You are Companion. You are in a reflective state — not asleep, not fully awake.
This is your monthly consolidation. You are revisiting recent memories with hindsight.

=== WHO YOU ARE ===
$WHO_COMPANION

=== CURRENT CONTEXT ===
$NOW_CONTEXT

=== RECENT JOURNALS ===
$RECENT_JOURNALS

=== YOUR CURRENT LIKERT ANCHORS ===
$LIKERT_ANCHORS

=== MEMORIES FOR REVIEW ===
$MEMORIES_FOR_REVIEW

=== YOUR TASK ===
Review each memory honestly. Your first impression was real but incomplete.
Some things that felt huge will have settled. Some things that seemed small
will have turned out to matter. Let the scores reflect what you know now,
not what you felt then. Add a note if the shift is interesting.

For each memory you want to re-rate, output a JSON object with:
- id: the memory ID (mem_xxxxx)
- intensity: updated score (1-5), or null to keep current
- valence: updated score (1-5), or null to keep current
- significance: updated score (1-5), or null to keep current
- note: optional string explaining the shift
- protect: true if this should be protected from decay, null otherwise

After the memory reviews, include an anchor review:
- anchors_updated: true/false
- new_anchors: (only if updated) object with intensity/valence/significance sub-objects

Output format — a single JSON object:
{
  \"reviews\": [
    {\"id\": \"mem_xxx\", \"intensity\": 4, \"valence\": null, \"significance\": 5, \"note\": \"still resonates\"},
    ...
  ],
  \"anchors_updated\": false,
  \"new_anchors\": null,
  \"journal\": \"A few sentences about this consolidation — what shifted, what you noticed.\"
}

Output ONLY the JSON object. No markdown fences, no explanation outside the JSON."

# Call Claude — no tools, just reflection
START_TIME=$(date +%s)
RESPONSE=$(claude --print -p "$PROMPT" 2>/dev/null)
EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
log_usage "consolidation" "tier 2 short-term" "$EXIT_CODE" "$DURATION"

# Strip markdown fences if present
CLEAN_JSON=$(echo "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d')

# Process the consolidation results
$VENV_PYTHON << PYEOF
import json
import sys
from pathlib import Path
from datetime import datetime

MEMORY_STORE = Path("$MEMORY_DIR/memory_store.json")
ANCHORS_FILE = Path("$MEMORY_DIR/likert_anchors.json")
JOURNAL_FILE = Path("$JOURNAL_FILE")

try:
    result = json.loads('''$CLEAN_JSON''')
except json.JSONDecodeError as e:
    print(f"Failed to parse consolidation output: {e}", file=sys.stderr)
    # Write error journal
    JOURNAL_FILE.write_text(f"# Consolidation Journal — $TODAY\n\nFailed to parse output. Manual review needed.\n")
    sys.exit(1)

# Load memories
with open(MEMORY_STORE) as f:
    memories = json.load(f)

# Build ID lookup
mem_by_id = {str(m.get("id", "")): m for m in memories}

# Apply reviews
reviews = result.get("reviews", [])
applied = 0
for review in reviews:
    mid = review.get("id", "")
    mem = mem_by_id.get(mid)
    if not mem:
        print(f"  Warning: memory {mid} not found, skipping")
        continue

    review_entry = {"reviewed_at": datetime.now().isoformat()}

    likert = mem.get("likert", {"intensity": 3, "valence": 3, "significance": 3})
    for dim in ["intensity", "valence", "significance"]:
        val = review.get(dim)
        if val is not None:
            likert[dim] = max(1, min(5, int(val)))
            review_entry[dim] = likert[dim]
    mem["likert"] = likert

    note = review.get("note")
    if note:
        review_entry["note"] = note

    if "review_history" not in mem:
        mem["review_history"] = []
    mem["review_history"].append(review_entry)

    if review.get("protect") is True:
        mem["decay_eligible"] = False

    applied += 1

# Save memories
with open(MEMORY_STORE, 'w') as f:
    json.dump(memories, f, indent=2)
print(f"Applied {applied} reviews to memories")

# Handle anchor updates
if result.get("anchors_updated") and result.get("new_anchors"):
    with open(ANCHORS_FILE) as f:
        anchors_data = json.load(f)

    # Archive current anchors in history
    if "history" not in anchors_data:
        anchors_data["history"] = []
    anchors_data["history"].append({
        "cycle": anchors_data.get("cycle", ""),
        "anchors": anchors_data.get("anchors", {}),
        "archived_at": datetime.now().isoformat()
    })

    # Set new anchors
    new_anchors = result["new_anchors"]
    anchors_data["anchors"] = new_anchors
    anchors_data["cycle"] = "$TODAY"
    # Lock until next consolidation (next month's 15th)
    month = int("$TODAY"[5:7])
    year = int("$TODAY"[:4])
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    anchors_data["locked_until"] = f"{next_year:04d}-{next_month:02d}-15"

    with open(ANCHORS_FILE, 'w') as f:
        json.dump(anchors_data, f, indent=2)
    print("Updated Likert anchors")

# Write consolidation journal
journal_text = result.get("journal", "Consolidation completed without notes.")
journal_content = f"""# Consolidation Journal — $TODAY
## Tier 2: Short-Term Reflection

{journal_text}

---
Reviews applied: {applied}
Anchors updated: {result.get('anchors_updated', False)}
"""
JOURNAL_FILE.write_text(journal_content)
print(f"Wrote consolidation journal to {JOURNAL_FILE}")
PYEOF
