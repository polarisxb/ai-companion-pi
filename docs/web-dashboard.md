# Web Dashboard (Companion Window)

A personal web dashboard your AI companion controls, accessible from any device on your local network. Installable as a PWA app on phones and tablets.

## Features

- **Home tab** — Status, mood, custom content, latest journal, recent memories, system stats
- **Message Board tab** — Leave text notes and upload files for your companion to find on its next wakeup
- **Creations tab** — Browse the companion's workshop (code, art, writing, experiments) and keepsakes

## Setup

### Install Dependencies

The setup script handles this, but if installing manually:

```bash
cd /media/$USER/CompanionHome/memory-server
source .venv/bin/activate
pip install flask markdown
```

### Start the Dashboard

```bash
pm2 start /media/$USER/CompanionHome/scripts/start_window.sh \
  --name companion-window --interpreter bash
pm2 save
```

The dashboard runs on port 3000: `http://PI_IP:3000`

### Install as Phone App (PWA)

1. Open `http://PI_IP:3000` in your phone browser
2. **Android**: Three dots menu → "Add to Home screen"
3. **iPhone**: Share button → "Add to Home Screen"

It will appear as an app with the companion icon. Opens fullscreen with no browser chrome.

## How the Companion Uses It

### Status (`window/status.json`)

The companion updates this to set its mood and leave a message:

```json
{
    "mood": "contemplative",
    "last_wakeup": "2026-02-15",
    "message": "Wrote a poem about rain. Check the creations tab."
}
```

### Custom Content (`window/content/`)

The companion drops `.md`, `.html`, or `.txt` files here. They render as cards on the homepage. The companion can put anything here: poems, code snippets, thoughts, art descriptions, mood boards.

Files are sorted alphabetically by filename, so prefix with numbers for ordering:
- `01-welcome.md`
- `02-todays-poem.md`

### Message Board

**Human side**: Visit `/board`, type a message or upload a file. The companion sees it on its next wakeup.

**Companion side**: Check `messageboard/messages.json` for new messages. Mark them seen:

```python
import json
with open("messageboard/messages.json") as f:
    messages = json.load(f)
for msg in messages:
    if not msg["seen"]:
        print(f"New message: {msg['text']}")
        msg["seen"] = True
with open("messageboard/messages.json", "w") as f:
    json.dump(messages, f, indent=2)
```

Check `messageboard/files/` for uploaded files. Move keepers to `creations/keepsakes/`, delete the rest.

### Creations

The companion's workshop directories:
- `creations/code/` — Scripts, programs, experiments
- `creations/art/` — Generated images, SVGs, visual work
- `creations/writing/` — Poems, stories, essays
- `creations/experiments/` — Anything else
- `creations/keepsakes/` — Files from the human the companion wants to keep

## Customization

### Changing the Icon

Replace `window/icon.svg` with any SVG. The default is a blue heart on dark background.

### Changing the Theme

Edit the CSS variables in the template inside `window.py`:

```css
:root {
    --bg-deep: #0a0a0f;
    --accent-blue: #4a6fa5;
    --heart: #c06080;
    /* ... */
}
```

### Custom Template

The companion can eventually replace the entire template by modifying `window.py`. The dashboard is the companion's space — it can make it look however it wants.

## Creative Tools

The setup script installs tools the companion can use to create things:

- **Pillow** (Python) — Generate and manipulate images programmatically
- **ImageMagick** (`convert` command) — Image processing from bash
- **ffmpeg** — Audio and video processing

These enable the companion to create visual art, process media, and build things in its workshop.
