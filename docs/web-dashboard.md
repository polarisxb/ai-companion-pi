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

### Chat (Planned M7)

The planned M7 chat page is the human's live text dialogue channel with the
companion. It is distinct from the Message Board, which stores notes for the
next wakeup, and distinct from Requests, which are structured companion asks.

The first chat implementation should prove the M7 dialogue engine through CLI
first. The dashboard page should reuse that engine after the API is stable and
may follow a human-provided UI design. It should write conversation
transcripts, not wake events, and show a transcript, composer, compact
companion state/provider metadata, error/retry states, and memory proposals
when present. See [m7-text-dialogue-design.md](m7-text-dialogue-design.md) and
the repository `DESIGN.md`.

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

## Chat surface

`/chat` is a dashboard page for user-initiated Companion text dialogue. It shows the active provider, memory mode, conversation id, transcript rows, and the number of memory proposals linked to the conversation. `POST /chat/send` accepts either form data or JSON and delegates to `DialogueRunner`; it is not a separate provider path.

Failure responses preserve the submitted input (`failed_input` in JSON, preserved text on the rendered page) so the human can retry or edit. `/life` remains read-only; the chat route lives outside `/life` and does not mutate scheduler, cron, timers, services, or wake-cycle state.
