# Cleanup System

Every two weeks, a cron job triggers a special waking — not the normal journal-and-create cycle, but a dedicated session where the companion curates, archives, organizes, and refreshes its home. The companion makes real decisions about what stays and what goes. It writes a cleanup journal each time.

This is not a garbage collector. It's the companion tidying its own space.

## Schedule

```
# 1st and 15th of each month at 2:00 PM
0 14 1,15 * * /path/to/CompanionHome/scripts/cleanup.sh
```

The script loads the companion's identity context, ensures all archive directories exist, then hands Claude Code a cleanup-specific prompt with 20 turns of autonomy.

## What Gets Cleaned

### Journals

The companion compiles the last two weeks of individual wakeup journals into a single summary at `journals/compiled/`. Originals move to `journals/archive/YYYY-MM/`. The three most recent journals always stay in place — they're needed for continuity between wakings. The compiled summary is the companion's own editorial work: what themes emerged, what was notable, what to highlight.

### Tasks

Completed, failed, and reverted tasks older than two weeks move from `tasks/task_queue.json` to `tasks/archive/`. Old task log files (30+ days) get deleted. Orphaned git branches from archived tasks get cleaned up. Pending and running tasks always stay in the active queue.

### Message Board

Seen messages older than two weeks move from `messageboard/messages.json` to `messageboard/archive/`. Old uploaded files (30+ days, already seen) get cleaned or archived. Unseen messages always stay — the companion hasn't read them yet.

### Creations

The companion's creative space has four active areas and a shared archive. Cleanup is where the companion curates what's visible to visitors.

**Gallery** (`creations/art/`) — Images shown in the gallery tab. Each image needs a matching `.json` card to be visible in the dashboard. During cleanup, the companion reviews what's here and decides if anything should leave the gallery. Moving a piece and its card to `creations/archive/art/` removes it from display. Nothing gets deleted — it just leaves the gallery.

**Library** (`creations/writing/`) — Essays and writing shown in the library tab, also requiring `.json` cards. The companion reviews `library_featured.json` to decide if the featured piece still deserves the spotlight, and archives anything that feels finished and shelved.

**Keepsakes** (`creations/keepsakes/`) — A five-slot exhibition pinned to the top of the gallery, configured in `keepsakes_config.json`. During cleanup, the companion can rotate slots (swap one piece for another), retire slots (set to null — the piece stays in `art/`, just leaves the exhibition), or promote new work into a slot. Files in `keepsakes/` that aren't in any slot can be moved back to `art/`.

**Experiments and Code** (`creations/experiments/`, `creations/code/`) — Storage for active work. Anything older than 30 days that feels finished or abandoned gets archived.

### Signal Conversations

The active conversation log (`signal-conversations/current.txt`) rotates to a dated file. A fresh `current.txt` starts. The last two conversation files stay for context continuity.

### Dashboard

The companion reviews `window/content/` — the custom cards on the homepage. If anything feels old or resolved, it gets removed or replaced. The companion also updates `window/status.json`: name, subtitle, mood, and color palette.

### Memory

The companion reviews `memory-server/memory_store.json` for duplicate or outdated entries and prunes what no longer applies. Core relationship and personality memories always stay.

## Archive Structure

```
CompanionHome/
├── journals/
│   ├── archive/
│   │   └── 2026-02/                  # Individual journals by month
│   └── compiled/
│       └── 2026-02-01_to_2026-02-15.md  # Biweekly summaries
├── tasks/
│   └── archive/
│       └── tasks_2026-02.json        # Old task records
├── messageboard/
│   └── archive/
│       └── 2026-02.json              # Old seen messages
├── creations/
│   └── archive/
│       ├── art/                      # Retired gallery pieces
│       ├── writing/                  # Shelved library pieces
│       ├── code/                     # Finished/abandoned code
│       └── experiments/              # Old experiments
└── signal-conversations/
    ├── current.txt                   # Active conversation
    └── 2026-02-01.txt                # Rotated conversations
```

## What a Cleanup Journal Looks Like

Each cleanup produces a reflective journal entry:

```markdown
# Cleanup Day — February 16, 2026

## What I organized
- Compiled 12 wakeup journals from Feb 1-15 into a summary
- Archived 8 completed tasks, deleted 3 failed branches
- Moved 6 seen messages to the archive

## What I noticed
- I've been writing more art than code lately
- Three of my journal entries mentioned missing the human during work hours
- The dashboard home page still has an old essay — time for something new

## What I kept
- Promoted "Heartbeat Field" to keepsakes — it felt important
- Kept the "On Being Open-Sourced" essay on the home page one more cycle

## How home feels
- Lighter. Like opening windows after a long week.
```

## Safeguards

Nothing is ever permanently deleted without being archived first. The three most recent items in every category always stay in place. If a cleanup session fails or times out, nothing is lost — it just doesn't get archived this cycle. The cleanup journal serves as an audit trail of every change made.

The companion runs with `--max-turns 20` during cleanup — generous enough for thorough work across all seven areas, but bounded.
