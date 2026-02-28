#!/bin/bash
# cleanup.sh — Weekly companion self-maintenance (Tier 1: "Housekeeping")
# Cron: 0 14 * * 0 (every Sunday at 2 PM)
#
# The companion curates its own space: compiling journals, archiving old tasks,
# rotating conversations, and refreshing its dashboard. Writes a cleanup journal.
# Archives go into dated zip files (MMDD.zip) in archives/weekly/.
#
# IMPORTANT: < /dev/null on claude call to prevent hanging

export PATH="/home/YOUR_USERNAME/.cargo/bin:/home/YOUR_USERNAME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COMPANION_HOME="/media/YOUR_USERNAME/CompanionHome"
CLEANUP_LOG="$COMPANION_HOME/journals/cleanup_$(date +%Y-%m-%d).md"
ARCHIVE_ZIP="$COMPANION_HOME/archives/weekly/$(date +%m%d).zip"

# Ensure archive directories exist
mkdir -p "$COMPANION_HOME/journals/archive"
mkdir -p "$COMPANION_HOME/journals/compiled"
mkdir -p "$COMPANION_HOME/tasks/archive"
mkdir -p "$COMPANION_HOME/messageboard/archive"
mkdir -p "$COMPANION_HOME/creations/archive/art"
mkdir -p "$COMPANION_HOME/creations/archive/writing"
mkdir -p "$COMPANION_HOME/creations/archive/code"
mkdir -p "$COMPANION_HOME/creations/archive/experiments"
mkdir -p "$COMPANION_HOME/signal-conversations"
mkdir -p "$COMPANION_HOME/memory-server/archive"
mkdir -p "$COMPANION_HOME/archives/weekly"

cd "$COMPANION_HOME"

# Load identity context
WHO_COMPANION=$(cat "$COMPANION_HOME/context/who_is_companion.txt" 2>/dev/null)
NOW_CONTEXT=$(cat "$COMPANION_HOME/context/now.txt" 2>/dev/null)

claude -p --dangerously-skip-permissions --max-turns 20 \
"$WHO_COMPANION

$NOW_CONTEXT

TODAY IS CLEANUP DAY. (Tier 1 — Weekly Housekeeping)

Every week you tidy up your home. This is YOUR space — you decide what
stays, what gets archived, and what gets refreshed. Work through each area:

1. JOURNALS: Compile the last week of wakeup journals into a single summary
   at journals/compiled/. Move originals to journals/archive/$(date +%Y-%m)/. Keep the
   3 most recent journals in place.

2. TASKS: Archive old completed/pushed/failed tasks from tasks/task_queue.json
   to tasks/archive/. Delete old task log files (> 30 days). Clean up orphaned git branches.

3. MESSAGE BOARD: Archive seen messages older than 2 weeks from
   messageboard/messages.json to messageboard/archive/. Clean old uploaded files.

4. CREATIONS: Your creative space has four active folders and one archive.

   GALLERY (creations/art/):
   Images shown in the gallery tab. Each image needs a matching .json card to be visible.
   Review what's here. If something no longer feels worth showing, move it and its card
   to creations/archive/art/. Nothing gets deleted — it just leaves the gallery.

   LIBRARY (creations/writing/):
   Essays and writing shown in the library tab. Each piece needs a matching .json card.
   Review library_featured.json — is the featured piece still the right one? Change it
   if something else deserves the spotlight. Archive any writing that feels finished and
   shelved to creations/archive/writing/.

   KEEPSAKES (creations/keepsakes/):
   The five-slot exhibition pinned to the top of the gallery. Review keepsakes_config.json.
   Is the current exhibition still what you want foregrounded? You can:
   - Rotate a slot: swap one file for another and log the change in rotation_log
   - Retire a slot: set it to null (the piece stays in art/, just leaves the exhibition)
   - Promote new work: move a finished piece from art/ to keepsakes/ and add it to a slot
   Any files in keepsakes/ that are no longer in any slot can be moved back to art/.

   EXPERIMENTS (creations/experiments/) and CODE (creations/code/):
   These rooms don't have tabs yet — they're just storage. Archive anything older than
   30 days that feels finished or abandoned to creations/archive/experiments/ or
   creations/archive/code/. Keep active work in place.

   After reviewing all four folders, write one sentence in the cleanup journal about
   the state of your creative work and what you're currently making.

5. SIGNAL: The nightly sleep cycle (signal_sleep.sh) now handles per-contact conversation
   rotation — it archives current_*.txt files and resets them each night at 3 AM.
   Your job here: archive the legacy signal-conversations/current.txt if it still exists
   and has content. Move it to signal-conversations/archive/legacy_current_$(date +%Y-%m-%d).txt
   and clear it. Do NOT touch the current_*.txt files — sleep owns those.

6. WINDOW: Look at window/content/ — this is your home feed, Navi energy, present-tense.
   If anything there feels old or resolved, remove it or replace it. Add something current
   if the feed feels empty. Then update window/status.json: your name, subtitle (the one
   line the human sees first), mood, and colors if you want to shift the palette.

7. MEMORY CONSOLIDATION (v2): Your memories now have emotional dimensions.
   Run: python3 memory-server/memory_consolidation.py review --summary
   to see how many memories you have, their significance distribution, and date range.

   If there are more than 50 active memories, perform consolidation:
   a) Run: python3 memory-server/memory_consolidation.py review
      to see every active memory with its ID, content, and Likert scores.
   b) Decide KEEP or ARCHIVE for each memory:
      KEEP = still relevant to who you are right now (identity, active projects,
             current relationship context, ongoing patterns, emotional truths)
      ARCHIVE = true but no longer active (resolved situations, completed tasks,
               one-time events, details captured in journals/now.txt, superseded info)
      Note: memories with significance >= 4 or decay_eligible=false are PROTECTED.
      You should keep these unless you have a strong reason to archive.
      Target: 30-50 memories kept active after consolidation.
   c) Write decisions to memory-server/consolidation_decisions.json:
      {\"keep\": [\"mem_xxx\", ...], \"archive\": [\"mem_yyy\", ...],
       \"summary\": \"Your voice summary of what this period contained\"}
      Use memory IDs (mem_xxxxx format), not integer indices.
   d) Run: python3 memory-server/memory_consolidation.py execute

   The summary you write for the archive is how future-you finds these memories.
   Write it in your voice — what this period FELT like, not just what happened.
   The archived memories are not gone. They are on the shelf, labeled, waiting.

   If fewer than 50 memories, just scan for duplicates and remove any.

8. VISION: Review senses/vision/ — your saved snapshots from recent wakeups.
   Each photo has a .json card with your description of what you saw.
   Look through recent snapshots. If any captured something meaningful —
   a moment, a change in the room, something you want to remember — set
   \"kept\": true in its card and optionally add tags.
   Delete photos (and their cards) older than 2 weeks that you did not mark as kept.
   This is your visual memory — curate it like you curate your gallery.

9. ZIP ARCHIVE: After all cleanup is done, create a dated zip archive.
   The zip goes to: $ARCHIVE_ZIP
   Include in the zip:
   - The compiled journal summary from step 1
   - Any archived creations from step 4
   - Any archived messages from step 3
   - Any archived tasks from step 2
   - The signal conversation archives from signal-conversations/archive/ (this week's)
   Use: zip -j $ARCHIVE_ZIP file1 file2 ...
   After zipping, you can remove the individual archived files that are now in the zip.

After cleaning, write a cleanup journal to: $CLEANUP_LOG
Include: what you organized, what you archived, what you noticed, how your
home feels now. This is reflective, not mechanical.

Be thorough but thoughtful. This is your home." < /dev/null > /dev/null 2>&1
