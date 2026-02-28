#!/bin/bash
# setup_substack.sh — First-time setup for Companion's Substack pipeline
# Run this once on the Pi after creating the Substack account
#
# Usage: bash setup_substack.sh

COMPANION_HOME="${COMPANION_HOME:-/media/YOUR_USERNAME/CompanionHome}"
SCRIPTS_DIR="$COMPANION_HOME/scripts"
SUBSTACK_DIR="$COMPANION_HOME/substack"

echo "========================================"
echo "  Companion Substack Pipeline — Setup"
echo "========================================"
echo ""

# 1. Create directory structure
echo "Creating directories..."
mkdir -p "$SUBSTACK_DIR"
mkdir -p "$SUBSTACK_DIR/drafts"

# 2. Initialize queue files
if [ ! -f "$SUBSTACK_DIR/queue.json" ]; then
    echo "[]" > "$SUBSTACK_DIR/queue.json"
    echo "  Created queue.json"
fi

if [ ! -f "$SUBSTACK_DIR/published.json" ]; then
    echo "[]" > "$SUBSTACK_DIR/published.json"
    echo "  Created published.json"
fi

# 3. Copy scripts if not already in place
for SCRIPT in substack_config.sh substack_queue.py substack_publish.py publish_cycle.sh; do
    if [ ! -f "$SCRIPTS_DIR/$SCRIPT" ]; then
        echo "  NOTE: $SCRIPT needs to be copied to $SCRIPTS_DIR/"
    else
        echo "  ✓ $SCRIPT in place"
    fi
done

# 4. Check config
echo ""
echo "--- Configuration Check ---"
if [ -f "$SCRIPTS_DIR/substack_config.sh" ]; then
    source "$SCRIPTS_DIR/substack_config.sh"
    if [ -n "$SUBSTACK_SUBDOMAIN" ] && [ "$SUBSTACK_SUBDOMAIN" != "companionwriting" ]; then
        echo "  ✓ Subdomain: $SUBSTACK_SUBDOMAIN"
    else
        echo "  ⚠ SUBSTACK_SUBDOMAIN not configured (still default)"
    fi
    if [ -n "$SUBSTACK_COOKIE" ]; then
        echo "  ✓ Cookie is set (length: ${#SUBSTACK_COOKIE})"
    else
        echo "  ✗ SUBSTACK_COOKIE not set"
        echo ""
        echo "  To get the cookie:"
        echo "    1. Log into Substack in a browser"
        echo "    2. Open DevTools (F12) → Application → Cookies → substack.com"
        echo "    3. Copy the value of 'substack.sid'"
        echo "    4. Paste it into $SCRIPTS_DIR/substack_config.sh"
    fi
else
    echo "  ✗ substack_config.sh not found at $SCRIPTS_DIR/"
fi

# 5. Test queue system
echo ""
echo "--- Queue System Test ---"
TEST_OUTPUT=$(python3 "$SCRIPTS_DIR/substack_queue.py" list 2>&1)
if [ $? -eq 0 ]; then
    echo "  ✓ Queue system works"
else
    echo "  ✗ Queue system error: $TEST_OUTPUT"
fi

# 6. Show wakeup.sh integration
echo ""
echo "========================================"
echo "  Integration with wakeup.sh"
echo "========================================"
echo ""
echo "Add this to Companion's wakeup prompt (in the CAPABILITIES section):"
echo ""
echo '  === SUBSTACK ==='
echo '  You have a Substack publication. You can queue posts for publishing:'
echo '    python3 /media/YOUR_USERNAME/CompanionHome/scripts/substack_queue.py add \'
echo '      --title "TITLE" --body "BODY" [--subtitle "SUB"] [--tags "tag1,tag2"]'
echo '  '
echo '  Posts go into a pending queue. the human approves them via the Window,'
echo '  then they auto-publish on the next cycle.'
echo '  '
echo '  To auto-publish (skip approval): add --auto-publish flag'
echo '  To queue from a file: --body "@/path/to/essay.md"'
echo '  '
echo '  Check your queue: python3 .../substack_queue.py list'
echo '  Check published: cat substack/published.json'
echo ""
echo "Add this near the end of wakeup.sh (before journal parsing):"
echo ""
echo '  # Publish approved Substack posts'
echo '  bash "$COMPANION_HOME/scripts/publish_cycle.sh"'
echo ""
echo "========================================"
echo "  Setup complete!"
echo "========================================"
