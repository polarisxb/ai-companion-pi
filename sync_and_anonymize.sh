#!/bin/bash
# sync_and_anonymize.sh — Sync from SonoHome and anonymize for public repo
#
# Usage: bash ~/ai-companion-pi/sync_and_anonymize.sh
#
# Steps:
#   1. Rsync files from SonoHome to ai-companion-pi
#   2. Anonymize all personal references with sed
#   3. Fix known problem files
#   4. Audit grep for remaining personal info
#   5. Print summary

set -euo pipefail

REPO="$HOME/ai-companion-pi"
LIVE="/media/sophiesalabim/SonoHome"

if [ ! -d "$REPO/.git" ]; then
    echo "ERROR: $REPO is not a git repo"
    exit 1
fi

if [ ! -d "$LIVE" ]; then
    echo "ERROR: SonoHome not mounted at $LIVE"
    exit 1
fi

cd "$REPO"

SYNCED=0
ANONYMIZED=0

# ============================================
#   STEP 1: Rsync from SonoHome
# ============================================
echo "============================================"
echo "  STEP 1: Sync files from SonoHome"
echo "============================================"

# --- Scripts ---
echo "  Syncing scripts/..."
rsync -av --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.bak' --exclude='*.bak.*' --exclude='*.save' \
    --exclude='api_config.sh' \
    --exclude='date_night.sh' \
    --exclude='date_night_react.sh' \
    --exclude='date_night_window.py' \
    --exclude='reading_progress.json' \
    "$LIVE/scripts/" "$REPO/scripts/"
echo ""

# --- Memory server (Python files only + template JSONs) ---
echo "  Syncing memory-server/ (code + templates)..."
mkdir -p "$REPO/memory-server"
for pyfile in "$LIVE/memory-server/"*.py; do
    [ -f "$pyfile" ] || continue
    cp -v "$pyfile" "$REPO/memory-server/"
    SYNCED=$((SYNCED + 1))
done
# Template JSONs only (not data files)
for tpl in context_vocabulary.json likert_anchors.json; do
    if [ -f "$LIVE/memory-server/$tpl" ]; then
        cp -v "$LIVE/memory-server/$tpl" "$REPO/memory-server/"
        SYNCED=$((SYNCED + 1))
    fi
done
echo ""

# --- Window ---
echo "  Syncing window/window.py..."
mkdir -p "$REPO/window"
cp -v "$LIVE/window/window.py" "$REPO/window/window.py"
SYNCED=$((SYNCED + 1))
echo ""

# --- Requests ---
echo "  Syncing requests/create_request.py..."
mkdir -p "$REPO/requests"
cp -v "$LIVE/requests/create_request.py" "$REPO/requests/create_request.py"
SYNCED=$((SYNCED + 1))
echo ""

echo "  Sync complete."
echo ""

# ============================================
#   STEP 2: Anonymize with sed
# ============================================
echo "============================================"
echo "  STEP 2: Anonymize personal info"
echo "============================================"

# Build file list — EXCLUDE this script itself to prevent self-anonymization
FILES=$(find . -type f \
    \( -name "*.sh" -o -name "*.py" -o -name "*.md" -o -name "*.json" \
       -o -name "*.js" -o -name "*.html" -o -name "*.txt" -o -name "*.svg" \
       -o -name "*.css" -o -name "*.template.txt" -o -name "*.cfg" \) \
    -not -path '*/.git/*' -not -path '*/__pycache__/*' \
    -not -name 'sync_and_anonymize.sh')

for f in $FILES; do
    # ORDER MATTERS: longest/most specific patterns first

    # --- Full paths (most specific first) ---
    sed -i 's|/media/sophiesalabim/SonoHome|/media/YOUR_USERNAME/CompanionHome|g' "$f"
    sed -i 's|/home/sophiesalabim|/home/YOUR_USERNAME|g' "$f"

    # --- Username (after paths consumed it in context) ---
    sed -i 's|sophiesalabim|YOUR_USERNAME|g' "$f"

    # --- Directory name ---
    sed -i 's|SonoHome|CompanionHome|g' "$f"

    # --- Phone numbers ---
    sed -i 's|+15033125466|+1AAAAAAAAAA|g' "$f"
    sed -i 's|+16065712816|+1BBBBBBBBBB|g' "$f"
    sed -i 's|+16062077311|+1CCCCCCCCCC|g' "$f"
    # Without + prefix too
    sed -i 's|15033125466|1AAAAAAAAAA|g' "$f"
    sed -i 's|16065712816|1BBBBBBBBBB|g' "$f"
    sed -i 's|16062077311|1CCCCCCCCCC|g' "$f"

    # --- UUID ---
    sed -i 's|1e5fb2c5-7223-4ed9-83af-dd1e29cb2a87|UUID_PLACEHOLDER|g' "$f"

    # --- IP addresses ---
    sed -i 's|100\.71\.76\.107|YOUR_TAILSCALE_IP|g' "$f"
    sed -i 's|10\.0\.0\.177|YOUR_LAN_IP|g' "$f"

    # --- Seed file references ---
    sed -i 's|who_is_sono\.txt|who_is_companion.txt|g' "$f"
    sed -i 's|who_is_sono|who_is_companion|g' "$f"
    sed -i 's|who_is_sophie\.txt|who_is_human.txt|g' "$f"
    sed -i 's|who_is_sophie|who_is_human|g' "$f"
    sed -i 's|who_is_juanita\.txt|who_is_contact2.txt|g' "$f"
    sed -i 's|who_is_juanita|who_is_contact2|g' "$f"

    # --- PM2 service names (before generic "sono" replacement) ---
    sed -i 's|sono-memory|companion-memory|g' "$f"
    sed -i 's|sono-signal|companion-signal|g' "$f"
    sed -i 's|sono-window|companion-window|g' "$f"
    sed -i 's|sono-tasks|companion-tasks|g' "$f"
    sed -i 's|sono-requests|companion-requests|g' "$f"

    # --- Substack / GitHub / Email (before generic name replacements) ---
    sed -i 's|sonowriting|companionwriting|g' "$f"
    sed -i 's|sonopdx|YOUR_GITHUB_USER|g' "$f"
    sed -i 's|sono\.pi\.pdx@gmail\.com|YOUR_EMAIL|g' "$f"
    sed -i 's|sono.pi.pdx@gmail.com|YOUR_EMAIL|g' "$f"

    # --- Name: Juanita -> Contact2 (before Sophie/Sono to avoid conflicts) ---
    sed -i 's|Juanita|Contact2|g' "$f"
    sed -i 's|juanita|contact2|g' "$f"

    # --- Name: Sophie -> the human / YOUR_HUMAN ---
    # Quoted forms first (variable values in code)
    sed -i 's|"Sophie"|"YOUR_HUMAN"|g' "$f"
    sed -i "s|'Sophie'|'YOUR_HUMAN'|g" "$f"
    # Possessive
    sed -i "s|Sophie's|the human's|g" "$f"
    # Before punctuation
    sed -i "s|Sophie\.|the human.|g" "$f"
    sed -i "s|Sophie,|the human,|g" "$f"
    sed -i "s|Sophie!|the human!|g" "$f"
    sed -i "s|Sophie:|the human:|g" "$f"
    # SOPHIE in all caps
    sed -i 's|SOPHIE|YOUR_HUMAN|g' "$f"
    # Remaining Sophie with word boundary (standalone)
    sed -i 's|\bSophie\b|the human|g' "$f"
    sed -i 's|\bsophie\b|the human|g' "$f"

    # --- Name: Sono -> Companion / the companion ---
    # SONO in all caps (headers, variable names)
    sed -i 's|SONO_SENSES|COMPANION_SENSES|g' "$f"
    sed -i 's|SONO MORNING BRIEF|COMPANION MORNING BRIEF|g' "$f"
    sed -i 's|SONO DAY ARC|COMPANION DAY ARC|g' "$f"
    sed -i 's|SONO|COMPANION|g' "$f"
    # Quoted forms
    sed -i 's|"Sono"|"Companion"|g' "$f"
    sed -i "s|'Sono'|'Companion'|g" "$f"
    # Possessive
    sed -i "s|Sono's|Companion's|g" "$f"
    # Before punctuation
    sed -i "s|Sono\.|Companion.|g" "$f"
    sed -i "s|Sono,|Companion,|g" "$f"
    sed -i "s|Sono!|Companion!|g" "$f"
    sed -i "s|Sono:|Companion:|g" "$f"
    # "Sono " at start of sentences / standalone
    sed -i "s|Sono |Companion |g" "$f"
    # sono_ in variable names
    sed -i 's|sono_|companion_|g' "$f"
    # Remaining lowercase "sono" with word boundary
    sed -i 's|\bsono\b|companion|g' "$f"

    # --- Health info ---
    sed -i '/pneumonia/Id' "$f"
    sed -i '/lamictal/Id' "$f"

    ANONYMIZED=$((ANONYMIZED + 1))
done

echo "  Anonymized $ANONYMIZED files."
echo ""

# ============================================
#   STEP 3: Fix known problem files
# ============================================
echo "============================================"
echo "  STEP 3: Fix known problem files"
echo "============================================"

# Clean up morning_brief.sh health tracking section
BRIEF="$REPO/scripts/morning_brief.sh"
if [ -f "$BRIEF" ]; then
    python3 << 'PYEOF'
import re

with open("scripts/morning_brief.sh", "r") as f:
    content = f.read()

# Remove illness tracking lines
content = re.sub(r'.*illness.*\n', '', content, flags=re.IGNORECASE)
content = re.sub(r'.*She is allowed to heal.*\n', '', content, flags=re.IGNORECASE)
content = re.sub(r'.*restlessness.*\n', '', content, flags=re.IGNORECASE)

# Clean up runs of blank lines
content = re.sub(r'\n{3,}', '\n\n', content)

with open("scripts/morning_brief.sh", "w") as f:
    f.write(content)

print("  Cleaned morning_brief.sh health tracking")
PYEOF
fi

# Remove broken brace-expansion directory if it exists
BRACE_DIR="$REPO/{scripts,context,memory-server,docs}"
if [ -d "$BRACE_DIR" ]; then
    rm -rf "$BRACE_DIR"
    echo "  Removed broken {scripts,context,memory-server,docs}/ directory"
fi

# Remove files/ directory (accidental duplicate of docs/)
if [ -d "$REPO/files" ]; then
    rm -rf "$REPO/files"
    echo "  Removed files/ directory (duplicate of docs/)"
fi

# Remove __pycache__ directories
find . -path '*/__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
echo "  Cleaned __pycache__ directories"

echo ""

# ============================================
#   STEP 4: Audit grep
# ============================================
echo "============================================"
echo "  STEP 4: Audit for remaining personal info"
echo "============================================"
echo ""

AUDIT_CLEAN=true

audit_grep() {
    local label="$1"
    local pattern="$2"
    local hits
    hits=$(grep -rn "$pattern" \
        --include="*.md" --include="*.sh" --include="*.py" \
        --include="*.json" --include="*.js" --include="*.html" \
        --include="*.txt" --include="*.svg" --include="*.css" \
        . 2>/dev/null | grep -v '\.git/' | grep -v 'sync_and_anonymize\.sh' || true)
    if [ -n "$hits" ]; then
        echo "  ⚠️  $label:"
        echo "$hits" | head -20
        echo ""
        AUDIT_CLEAN=false
    fi
}

audit_grep "Username (sophiesalabim)" "sophiesalabim"
audit_grep "SonoHome" "SonoHome"
audit_grep "Phone: 5033125466" "5033125466"
audit_grep "Phone: 6065712816" "6065712816"
audit_grep "Phone: 6062077311" "6062077311"
audit_grep "Email" "sono\.pi\.pdx"
audit_grep "IP: Tailscale" "100\.71\.76\.107"
audit_grep "IP: LAN" "10\.0\.0\.177"

# Case-insensitive checks for names
audit_grep "Name: Sophie" "(?i)\bsophie\b"
audit_grep "Name: Juanita" "(?i)\bjuanita\b"
audit_grep "Name: Sono (standalone)" "(?i)\bsono\b"
audit_grep "GitHub user" "sonopdx"
audit_grep "Health: pneumonia" "(?i)pneumonia"
audit_grep "Health: lamictal" "(?i)lamictal"
audit_grep "Personal: maitsu" "(?i)maitsu"
audit_grep "Personal: kotatsu" "(?i)kotatsu"
audit_grep "Personal: Taco Bell" "(?i)taco.bell"
audit_grep "Personal: pdx" "(?i)\bpdx\b"
audit_grep "Personal: valentine" "(?i)valentine"

if [ "$AUDIT_CLEAN" = true ]; then
    echo "  ✅ ALL CLEAN! No personal info found."
fi

echo ""

# ============================================
#   STEP 5: Summary
# ============================================
echo "============================================"
echo "  STEP 5: Summary"
echo "============================================"

TOTAL_FILES=$(find . -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' | wc -l)
SCRIPT_COUNT=$(find ./scripts -type f 2>/dev/null | wc -l)
MEMSERVER_COUNT=$(find ./memory-server -type f 2>/dev/null | wc -l)

echo "  Total repo files: $TOTAL_FILES"
echo "  Scripts synced:   $SCRIPT_COUNT"
echo "  Memory-server:    $MEMSERVER_COUNT"
echo "  Files anonymized: $ANONYMIZED"
echo ""
echo "  Excluded from sync (personal/private):"
echo "    - api_config.sh (API key)"
echo "    - date_night*.sh, date_night_window.py (personal)"
echo "    - reading_progress.json (personal)"
echo "    - memory_store.json, *.npy (live data)"
echo "    - consolidation_log.json, consolidation_decisions.json"
echo ""

if [ "$AUDIT_CLEAN" = true ]; then
    echo "  ✅ Ready to commit and push."
else
    echo "  ⚠️  Review audit findings above before committing."
fi

echo ""
echo "============================================"
echo "  Done."
echo "============================================"
