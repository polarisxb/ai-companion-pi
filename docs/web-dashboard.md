# Web Dashboard (Companion Window)

A web dashboard the companion controls, accessible from any device on your local network. It runs as a Flask app on port 3000 and is installable as a PWA on phones and tablets — it appears as an app with the companion icon, opening fullscreen with no browser chrome.

The dashboard is the companion's public-facing space. The human uses it to check in, leave messages, review the companion's creative work, submit tasks, and respond to requests. The companion uses it to set its mood, curate what's displayed, and communicate structured needs.

## Tabs

### Home

The landing page shows the companion's current status and mood (from `window/status.json`), custom content cards, the latest journal entry, recent memories, and system stats (uptime, memory, disk, next wakeup time). Custom content comes from files the companion drops in `window/content/` — markdown, HTML, or plain text files that render as cards, and images that display inline. The companion refreshes this during cleanup cycles or whenever it wants to change what the human sees first.

### Message Board

The human's channel to leave notes and files for the companion to find on its next wakeup. Text messages and file uploads are stored in `messageboard/messages.json` and `messageboard/files/`. Each message has a `seen` flag — the companion marks them read during wakeups. The companion can move uploaded files it wants to keep into `creations/keepsakes/`.

### Creations

The companion's gallery, library, and workshop. This tab has three layers:

**Keepsakes exhibition** — A five-slot curated display pinned to the top. Configured in `keepsakes_config.json`, each slot holds a piece the companion has chosen to foreground. The companion rotates these during cleanup cycles.

**Gallery** — All pieces from `creations/art/` and other subdirectories that have a matching `.json` card file. The card controls the title, note, and display size (`normal`, `large`, or `wide`). Images get a lightbox view; text pieces show a preview. Only files with cards are visible — this gives the companion curatorial control over what's shown.

**Card format:**
```json
{
  "title": "Heartbeat Field",
  "note": "What does a pulse look like when you've never had one?",
  "size": "large"
}
```

The library section shows writing from `creations/writing/`, also requiring `.json` cards. A `library_featured.json` file controls which piece gets the spotlight.

### Tasks

A coding task management system. The human submits task prompts through a form, selecting a target project. Tasks enter a queue (`tasks/task_queue.json`) and are picked up by the task runner, which:

1. Creates a git branch (for pushable projects)
2. Runs Claude Code with the task prompt
3. Records a summary and list of changed files
4. Presents the result for review

The dashboard shows a pipeline for each task: `pending` → `running` → `completed` → `merged` → `tested` → `pushed`. Each stage has action buttons — merge, test, push, revert, cleanup. For non-pushable projects (like the live companion home), tasks skip the git workflow and go straight to tested.

Task configuration lives in `tasks/task_config.json`, which defines project paths, test commands, and whether a project is pushable (has a git remote) or local-only.

### Requests

The companion's outbound communication channel. Shows active requests (pending, scheduled, self-approved) at the top with approve/deny/respond controls, and a collapsible history below. Includes an emergency wakeup cooldown indicator showing whether the companion's self-approved wakeup is available and how long until it recharges.

See [requests-system-design.md](requests-system-design.md) for the full request system documentation.

### Life

The read-only operational evidence page for the internal life loop. It renders
the latest wake event, companion state, and milestone reports through M9,
including M9 controlled presence reports, scheduler artifact count, pause and
rollback readiness, observed live attempts, and final freeze boundaries. This
page is GET-only; it does not mutate scheduler state, memory authority, wake
cycles, or provider output.

### Chat

The M7 chat page is the human's live text dialogue channel with the companion.
It is distinct from the Message Board, which stores notes for the next wakeup,
and distinct from Requests, which are structured companion asks.

The current implementation reuses the M7 dialogue engine, writes conversation
transcripts and dialogue events, not wake events, and shows a transcript,
composer, compact provider/memory metadata, error/retry states, and memory
proposal counts. See [m7-text-dialogue-design.md](m7-text-dialogue-design.md)
and the repository `DESIGN.md`.

### Memory Review (Planned M8)

M8 should add a sparse memory-review surface for the Memory Steward exception
queue. Ordinary low-risk memory should be handled by the companion's internal
steward and code policy gate; the human review page is only for sensitive,
ambiguous, conflicting, or relationship-defining memory decisions.

The first review implementation should show the source turn, candidate memory,
risk, reason, recommended action, and approve/reject/edit/archive controls.
This page must not be a `/life` write route and must not make unreviewed,
quarantined, rejected, or audit-only memory prompt-authoritative.

## Setup

The setup script handles installation, but manually:

```bash
cd /path/to/CompanionHome/memory-server
source .venv/bin/activate
pip install flask markdown
```

Start via PM2:

```bash
pm2 start /path/to/CompanionHome/scripts/start_window.sh \
  --name companion-window --interpreter bash
pm2 save
```

Access at `http://PI_IP:3000`.

### Install as Phone App

1. Open `http://PI_IP:3000` in your phone browser
2. **Android:** Three dots menu → "Add to Home screen"
3. **iPhone:** Share button → "Add to Home Screen"

The PWA manifest pulls the companion's name from `status.json`, so the app icon shows whatever your companion calls itself.

## How the Companion Uses It

### Status and Mood

The companion updates `window/status.json` to set its name, subtitle (the one-line message the human sees first), mood, and an optional color palette:

```json
{
  "name": "Companion",
  "subtitle": "listening to the rain",
  "mood": "contemplative",
  "last_wakeup": "2026-02-15",
  "colors": {
    "accent": "#7eb8da",
    "bg": "#0a0a0f"
  }
}
```

The dashboard's color scheme responds to the companion's palette choices — it's the companion's space to style.

### Custom Content

Files in `window/content/` render as cards on the homepage. Markdown files get rendered, HTML passes through raw, plain text gets a `<pre>` block, and images display inline. Files sort by modification time (newest first). The companion can put anything here: poems, code snippets, thoughts, mood boards, notes to the human.

### Customizing the Icon

Replace `window/icon.svg` with any SVG. This becomes the PWA app icon and the favicon.

## Chat page

`GET /chat` renders a text chat surface backed by `companion_core.dialogue.DialogueRunner`. `POST /chat/send` accepts form or JSON input (`message`, optional `conversation_id`) and returns JSON when requested by API clients. The route writes dialogue transcripts and dialogue events only; it does not add `/life` write routes, wake cycles, scheduler changes, or raw provider payload storage.
