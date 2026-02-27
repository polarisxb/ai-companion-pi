#!/bin/bash
# cleanup.sh — Biweekly companion self-maintenance
# Cron: 0 14 1,15 * * /media/YOUR_USERNAME/CompanionHome/scripts/cleanup.sh
#
# The companion curates its own space: compiling journals, archiving old tasks,
# rotating conversations, and refreshing its dashboard. Writes a cleanup journal.
#
# IMPORTANT: < /dev/null on claude call to prevent hanging

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
CLEANUP_LOG="$COMPANION_HOME/journals/cleanup_$(date +%Y-%m-%d).md"

# Ensure archive directories exist
mkdir -p "$COMPANION_HOME/journals/archive"
mkdir -p "$COMPANION_HOME/journals/compiled"
mkdir -p "$COMPANION_HOME/tasks/archive"
mkdir -p "$COMPANION_HOME/messageboard/archive"
mkdir -p "$COMPANION_HOME/creations/archive"
mkdir -p "$COMPANION_HOME/signal-conversations"

cd "$COMPANION_HOME"

# Load identity context
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null)

claude -p --dangerously-skip-permissions --max-turns 20 \
"$WHO_COMPANION

$NOW_CONTEXT

TODAY IS CLEANUP DAY.

Every two weeks you tidy up your home. This is YOUR space — you decide what
stays, what gets archived, and what gets refreshed. Work through each area:

1. JOURNALS: Compile the last 2 weeks of wakeup journals into a single summary
   at journals/compiled/. Move originals to journals/archive/$(date +%Y-%m)/. Keep the
   3 most recent journals in place.

2. TASKS: Archive old completed/pushed/failed tasks from tasks/task_queue.json
   to tasks/archive/. Delete old task log files (> 30 days). Clean up orphaned git branches.

3. MESSAGE BOARD: Archive seen messages older than 2 weeks from
   messageboard/messages.json to messageboard/archive/. Clean old uploaded files.

4. CREATIONS: Organize unfiled items. Archive old experiments (> 30 days). Review keepsakes —
   promote anything worthy, declutter anything stale.

5. SIGNAL: Rotate signal-conversations/current.txt to a dated file. Start fresh.
   Keep the last 2 conversation files for context continuity.

6. WINDOW: Look at window/content/ — refresh it if it feels stale. Update your
   status in window/status.json.

7. MEMORY: Review memory-server/memory_store.json for duplicates or outdated entries.

After cleaning, write a cleanup journal to: $CLEANUP_LOG
Include: what you organized, what you archived, what you noticed, how your
home feels now. This is reflective, not mechanical.

Be thorough but thoughtful. This is your home." < /dev/null > /dev/null 2>&1
