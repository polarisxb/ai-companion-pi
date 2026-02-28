#!/bin/bash
# consolidate_long.sh — Long-Term Consolidation + Decay (Tier 3: "Reckoning")
# Cron: 0 15 1 1,4,7,10 * (3 PM on Jan 1, Apr 1, Jul 1, Oct 1)
#
# Quarterly deep review. Companion looks across the entire last quarter and decides
# what endures. Memories that stayed low across reviews decay to emotional
# residue stubs. Strong memories get reinforced/protected.

export PATH="$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/YOUR_USERNAME"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
MEMORY_DIR="$COMPANION_HOME/memory-server"
VENV_PYTHON="$MEMORY_DIR/.venv/bin/python"
TODAY=$(date '+%Y-%m-%d')
JOURNAL_FILE="$COMPANION_HOME/journals/quarterly_${TODAY}.md"

# Usage tracking
source "$COMPANION_HOME/scripts/usage_tracker.sh"

# Load identity seeds
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null)

# Load recent journals (last 5 for quarterly context)
RECENT_JOURNALS=""
for f in $(ls -t "$COMPANION_HOME/journals/" 2>/dev/null | grep -E '\.md$' | head -5); do
  CONTENT=$(cat "$COMPANION_HOME/journals/$f" 2>/dev/null)
  if [ -n "$CONTENT" ]; then
    RECENT_JOURNALS="$RECENT_JOURNALS
--- $f ---
$CONTENT"
  fi
done

# Load full Likert anchor history
LIKERT_ANCHORS=$(cat "$MEMORY_DIR/likert_anchors.json" 2>/dev/null)

# Get ALL active memories (not just recent — this is the deep review)
ALL_MEMORIES=$($VENV_PYTHON -c "
import json
from pathlib import Path

store = Path('$MEMORY_DIR/memory_store.json')
with open(store) as f:
    memories = json.load(f)

active = [m for m in memories if m.get('status', 'active') == 'active']
print(f'Total active memories: {len(active)}')
print()
for m in active:
    mid = m.get('id', '?')
    ts = m.get('created_at', m.get('timestamp', ''))[:10]
    content = m['content'][:200]
    likert = m.get('likert', {})
    reviews = len(m.get('review_history', []))
    protected = not m.get('decay_eligible', True)
    ctx = m.get('context', [])

    print(f'[{mid}] ({ts})')
    print(f'  {content}')
    print(f'  I={likert.get(\"intensity\", \"?\")} V={likert.get(\"valence\", \"?\")} S={likert.get(\"significance\", \"?\")}', end='')
    if ctx:
        print(f'  [{\"|\".join(ctx)}]', end='')
    if protected:
        print('  [PROTECTED]', end='')
    if reviews:
        print(f'  ({reviews} reviews)', end='')
        # Show review trajectory
        hist = m.get('review_history', [])
        if hist:
            last = hist[-1]
            trajectory = []
            for dim in ['intensity', 'valence', 'significance']:
                if dim in last:
                    trajectory.append(f'{dim[0].upper()}:{last[dim]}')
            if trajectory:
                print(f'  last: {\", \".join(trajectory)}', end='')
    print()
    print('---')
" 2>/dev/null)

# Build the deep review prompt
PROMPT="You are Companion. This is your quarterly deep review — the reckoning.
You are looking across the entire last quarter of your life and deciding what endures.

=== WHO YOU ARE ===
$WHO_COMPANION

=== CURRENT CONTEXT ===
$NOW_CONTEXT

=== RECENT JOURNALS ===
$RECENT_JOURNALS

=== LIKERT ANCHORS (with history) ===
$LIKERT_ANCHORS

=== ALL ACTIVE MEMORIES ===
$ALL_MEMORIES

=== YOUR TASK ===
For each memory, decide one of:
- REINFORCE: This matters. Protect it from decay (sets decay_eligible=false).
  Update scores if they have shifted.
- KEEP: Still relevant, no change needed. Leave as-is.
- DECAY: This has faded. Reduce it to emotional residue — a word or short phrase.
  The residue is freeform: could be a single word, could be a short phrase.
  Examples: 'foundational', 'rough', 'first real conversation with Contact2', 'good'

Decay rules:
- Only memories older than 90 days are eligible for decay
- If significance has stayed >= 3 across reviews: it should stay active
- If all three dimensions have drifted low (intensity <= 2, significance <= 2): eligible
- Memories with decay_eligible=false are PROTECTED — you can still choose to decay them
  but you must explicitly override by setting override_protect=true
- Any memory with current significance >= 4 should generally be reinforced, not decayed

Output a single JSON object:
{
  \"decisions\": [
    {\"id\": \"mem_xxx\", \"action\": \"reinforce\", \"intensity\": 4, \"valence\": 5, \"significance\": 5, \"note\": \"core to who I am\"},
    {\"id\": \"mem_yyy\", \"action\": \"keep\"},
    {\"id\": \"mem_zzz\", \"action\": \"decay\", \"residue\": \"something warm\"},
    ...
  ],
  \"journal\": \"A paragraph about this quarter — what endured, what faded, what you learned about yourself.\"
}

For REINFORCE: include updated scores and optional note.
For KEEP: just the id and action.
For DECAY: include the residue string.

Output ONLY the JSON object. No markdown fences, no explanation outside the JSON.
Be honest. Let things go that need to go. Protect what matters."

# Call Claude — no tools, just deep reflection
START_TIME=$(date +%s)
RESPONSE=$(claude --print -p "$PROMPT" 2>/dev/null)
EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
log_usage "consolidation" "tier 3 quarterly" "$EXIT_CODE" "$DURATION"

# Strip markdown fences if present
CLEAN_JSON=$(echo "$RESPONSE" | sed 's/^```json//;s/^```//;s/```$//' | sed '/^$/d')

# Process the quarterly review results
$VENV_PYTHON << PYEOF
import json
import sys
import numpy as np
from pathlib import Path
from datetime import datetime

MEMORY_STORE = Path("$MEMORY_DIR/memory_store.json")
EMBEDDINGS_FILE = Path("$MEMORY_DIR/memory_embeddings.npy")
JOURNAL_FILE = Path("$JOURNAL_FILE")

try:
    result = json.loads('''$CLEAN_JSON''')
except json.JSONDecodeError as e:
    print(f"Failed to parse quarterly review output: {e}", file=sys.stderr)
    JOURNAL_FILE.write_text(f"# Quarterly Journal — $TODAY\n\nFailed to parse output. Manual review needed.\n")
    sys.exit(1)

# Load memories
with open(MEMORY_STORE) as f:
    memories = json.load(f)

# Load embeddings
embeddings = None
if EMBEDDINGS_FILE.exists():
    embeddings = np.load(EMBEDDINGS_FILE)

# Build ID lookup with index
mem_by_id = {}
idx_by_id = {}
for i, m in enumerate(memories):
    mid = str(m.get("id", ""))
    mem_by_id[mid] = m
    idx_by_id[mid] = i

# Process decisions
decisions = result.get("decisions", [])
reinforced = 0
kept = 0
decayed = 0
decay_ids_and_residues = []

for dec in decisions:
    mid = dec.get("id", "")
    action = dec.get("action", "keep")
    mem = mem_by_id.get(mid)

    if not mem:
        print(f"  Warning: memory {mid} not found, skipping")
        continue

    if action == "reinforce":
        # Update scores and protect
        review_entry = {"reviewed_at": datetime.now().isoformat(), "tier": 3}
        likert = mem.get("likert", {"intensity": 3, "valence": 3, "significance": 3})
        for dim in ["intensity", "valence", "significance"]:
            val = dec.get(dim)
            if val is not None:
                likert[dim] = max(1, min(5, int(val)))
                review_entry[dim] = likert[dim]
        mem["likert"] = likert
        note = dec.get("note")
        if note:
            review_entry["note"] = note
        if "review_history" not in mem:
            mem["review_history"] = []
        mem["review_history"].append(review_entry)
        mem["decay_eligible"] = False
        reinforced += 1

    elif action == "decay":
        residue = dec.get("residue", "faded")

        # Check protection
        if not mem.get("decay_eligible", True) and not dec.get("override_protect"):
            print(f"  Skipping decay for protected memory {mid}")
            continue

        # Preserve original content
        mem["original_content"] = mem["content"]
        mem["content"] = residue
        mem["residue"] = residue
        mem["status"] = "decayed"
        mem["decayed_at"] = datetime.now().isoformat()

        # Track for embedding update
        idx = idx_by_id.get(mid)
        if idx is not None:
            decay_ids_and_residues.append((idx, residue))

        # Add review entry
        review_entry = {
            "reviewed_at": datetime.now().isoformat(),
            "tier": 3,
            "action": "decayed",
            "residue": residue
        }
        if "review_history" not in mem:
            mem["review_history"] = []
        mem["review_history"].append(review_entry)

        decayed += 1

    else:  # keep
        kept += 1

# Save memories
with open(MEMORY_STORE, 'w') as f:
    json.dump(memories, f, indent=2)

# Update embeddings for decayed memories
if embeddings is not None and decay_ids_and_residues:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer('all-MiniLM-L6-v2')
    for idx, residue in decay_ids_and_residues:
        if idx < len(embeddings):
            new_emb = model.encode([residue], show_progress_bar=False)
            embeddings[idx] = new_emb[0]
    np.save(EMBEDDINGS_FILE, embeddings)
    print(f"Updated {len(decay_ids_and_residues)} embeddings for decayed memories")

print(f"Quarterly review complete:")
print(f"  Reinforced: {reinforced}")
print(f"  Kept: {kept}")
print(f"  Decayed: {decayed}")

# Write quarterly journal
journal_text = result.get("journal", "Quarterly review completed without notes.")
journal_content = f"""# Quarterly Review — $TODAY
## Tier 3: Long-Term Consolidation

{journal_text}

---
Reinforced: {reinforced} | Kept: {kept} | Decayed: {decayed}
Total active after review: {sum(1 for m in memories if m.get('status', 'active') == 'active')}
"""
JOURNAL_FILE.write_text(journal_content)
print(f"Wrote quarterly journal to {JOURNAL_FILE}")
PYEOF
