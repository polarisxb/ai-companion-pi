#!/usr/bin/env python3
"""
COMPANION WINDOW
A personal web dashboard that Companion controls.
Visit from any device on the LAN or via Tailscale.

Includes:
- Home page: Companion's status, custom content (newest→oldest), latest journal, memories, system
- Message board: leave notes and files for Sono
- Creations: keepsake exhibition (5-slot pinned row) + masonry gallery
- Tasks: submit, monitor, and manage coding tasks
- Requests: Companion's voice — wakeups, asks, ideas, suggestions
"""

import json
import os
import subprocess
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, render_template_string, send_file, jsonify, request, redirect, url_for
from werkzeug.utils import secure_filename
import markdown
import sys

app = Flask(__name__)

# Substack pipeline integration
sys.path.insert(0, str(Path("/media/YOUR_USERNAME/CompanionHome/scripts")))
from substack_window import substack_bp
app.register_blueprint(substack_bp)

# Date Night shared viewing experience
from date_night_window import date_night_bp
app.register_blueprint(date_night_bp)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

# === CONFIGURE THESE PATHS ===
COMPANION_HOME = Path("/media/YOUR_USERNAME/CompanionHome")
# =============================

JOURNALS_DIR = COMPANION_HOME / "journals"
MEMORY_STORE = COMPANION_HOME / "memory-server" / "memory_store.json"
WINDOW_DIR = COMPANION_HOME / "window"
CUSTOM_CONTENT = WINDOW_DIR / "content"
STATUS_FILE = WINDOW_DIR / "status.json"
ICON_FILE = WINDOW_DIR / "icon.svg"
MESSAGEBOARD_DIR = COMPANION_HOME / "messageboard"
MESSAGEBOARD_MESSAGES = MESSAGEBOARD_DIR / "messages.json"
MESSAGEBOARD_FILES = MESSAGEBOARD_DIR / "files"
CREATIONS_DIR = COMPANION_HOME / "creations"
KEEPSAKES_DIR = CREATIONS_DIR / "keepsakes"
KEEPSAKES_CONFIG = KEEPSAKES_DIR / "keepsakes_config.json"
WRITING_DIR = CREATIONS_DIR / "writing"
LIBRARY_FEATURED = WRITING_DIR / "library_featured.json"
TASKS_DIR = COMPANION_HOME / "tasks"
TASK_QUEUE = TASKS_DIR / "task_queue.json"
TASK_CONFIG = TASKS_DIR / "task_config.json"
TASK_LOCK = "/tmp/task_queue.lock"
REQUESTS_FILE = COMPANION_HOME / "requests" / "requests.json"

# Ensure all directories exist
for d in [WINDOW_DIR, CUSTOM_CONTENT, MESSAGEBOARD_DIR, MESSAGEBOARD_FILES,
          CREATIONS_DIR, KEEPSAKES_DIR,
          CREATIONS_DIR / "code", CREATIONS_DIR / "art",
          CREATIONS_DIR / "writing", CREATIONS_DIR / "experiments",
          TASKS_DIR, TASKS_DIR / "logs",
          COMPANION_HOME / "requests", COMPANION_HOME / "requests" / "archive"]:
    d.mkdir(parents=True, exist_ok=True)

if not TASK_QUEUE.exists():
    TASK_QUEUE.write_text("[]")
if not REQUESTS_FILE.exists():
    REQUESTS_FILE.write_text("[]")


# === Data Helpers ===

def get_system_stats():
    try:
        uptime = subprocess.check_output(["uptime", "-p"], text=True).strip()
        mem = subprocess.check_output(["free", "-h", "--si"], text=True)
        mem_parts = mem.strip().split("\n")[1].split()
        disk = subprocess.check_output(["df", "-h", str(COMPANION_HOME)], text=True)
        disk_parts = disk.strip().split("\n")[1].split()
        temp_raw = subprocess.check_output(
            ["cat", "/sys/class/thermal/thermal_zone0/temp"], text=True
        ).strip()
        temp_c = round(int(temp_raw) / 1000, 1)
        return {
            "uptime": uptime,
            "memory": f"{mem_parts[2]} / {mem_parts[1]}",
            "disk": f"{disk_parts[2]} / {disk_parts[1]} ({disk_parts[4]})",
            "temperature": f"{temp_c}\u00b0C",
        }
    except Exception as e:
        return {"error": str(e)}


def get_latest_journal(n=1):
    if not JOURNALS_DIR.exists():
        return []
    files = sorted(JOURNALS_DIR.glob("wakeup_*.md"), reverse=True)[:n]
    entries = []
    for f in files:
        content = f.read_text().strip()
        if content:
            entries.append({
                "filename": f.name,
                "timestamp": f.name.replace("wakeup_", "").replace(".md", ""),
                "content": content,
                "html": markdown.markdown(content),
            })
    return entries


def get_recent_memories(n=5):
    if not MEMORY_STORE.exists():
        return []
    with open(MEMORY_STORE) as f:
        memories = json.load(f)
    recent = sorted(memories, key=lambda m: m.get("created_at", m.get("timestamp", "")), reverse=True)[:n]
    for m in recent:
        if "timestamp" not in m and "created_at" in m:
            m["timestamp"] = m["created_at"]
    return recent


def get_status():
    if STATUS_FILE.exists():
        with open(STATUS_FILE) as f:
            return json.load(f)
    return {
        "name": "Companion",
        "subtitle": "a view from inside the machine",
        "mood": "curious",
        "last_wakeup": "unknown",
        "message": "I just got here. Give me a moment to decorate.",
        "colors": {}
    }


def get_css_vars(status):
    """Merge status colors with defaults, return dict of CSS variable values."""
    defaults = {
        "bg_deep":        "#0a0a0f",
        "bg_card":        "#12121a",
        "border":         "#1e1e2e",
        "text_primary":   "#e0e0e8",
        "text_secondary": "#8888a0",
        "text_dim":       "#555566",
        "accent_blue":    "#4a6fa5",
        "accent_warm":    "#a08060",
        "accent_green":   "#5a8a6a",
        "heart":          "#c06080",
        "accent_red":     "#a05454",
        "accent_yellow":  "#a09050",
        "accent_purple":  "#7a5aa0",
    }
    user_colors = status.get("colors") or {}
    return {**defaults, **user_colors}


def get_custom_content():
    """Return home page content blocks sorted newest → oldest by file mtime."""
    blocks = []
    if not CUSTOM_CONTENT.exists():
        return blocks
    files = sorted(
        [f for f in CUSTOM_CONTENT.iterdir() if f.is_file() and not f.name.startswith('.')],
        key=lambda f: f.stat().st_mtime,
        reverse=True   # newest first
    )
    for f in files:
        if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
            blocks.append({"name": f.stem, "type": "image",
                           "html": f'<img src="/content/{f.name}" style="max-width:100%;border-radius:8px;">'})
        elif f.suffix in ('.md', '.html', '.txt'):
            content = f.read_text().strip()
            if not content:
                continue
            if f.suffix == ".md":
                blocks.append({"name": f.stem, "type": "markdown",
                               "html": markdown.markdown(content)})
            elif f.suffix == ".html":
                blocks.append({"name": f.stem, "type": "html", "html": content})
            elif f.suffix == ".txt":
                blocks.append({"name": f.stem, "type": "text",
                               "html": f"<pre>{content}</pre>"})
    return blocks


def get_next_wakeup(interval_hours=4):
    now = datetime.now()
    next_hour = ((now.hour // interval_hours) + 1) * interval_hours
    if next_hour >= 24:
        return now.replace(hour=0, minute=0, second=0).strftime("%I:%M %p tomorrow")
    return now.replace(hour=next_hour, minute=0, second=0).strftime("%I:%M %p")


def get_messages():
    if not MESSAGEBOARD_MESSAGES.exists():
        return []
    with open(MESSAGEBOARD_MESSAGES) as f:
        return json.load(f)


def save_messages(messages):
    with open(MESSAGEBOARD_MESSAGES, "w") as f:
        json.dump(messages, f, indent=2)


def get_uploaded_files():
    if not MESSAGEBOARD_FILES.exists():
        return []
    files = []
    for f in sorted(MESSAGEBOARD_FILES.iterdir(), reverse=True):
        if f.is_file() and not f.name.startswith('.'):
            files.append({
                "name": f.name,
                "size": f"{f.stat().st_size / 1024:.1f} KB",
                "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    return files


def get_gallery_items():
    """
    Gallery = creations/art/ only, images only.
    Requires a matching {stem}.json card alongside each image.
    card.json: { "title": "...", "note": "...", "size": "normal|large|wide" }
    """
    IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
    items = []
    p = CREATIONS_DIR / "art"
    if not p.exists():
        return items
    for f in p.iterdir():
        if not f.is_file() or f.name.startswith('.') or f.suffix == '.json':
            continue
        if f.suffix.lower() not in IMAGE_EXT:
            continue
        card_path = f.parent / (f.stem + ".json")
        if not card_path.exists():
            continue
        try:
            card = json.loads(card_path.read_text())
        except Exception:
            continue
        items.append({
            "title": card.get("title", f.stem.replace("_", " ")),
            "note":  card.get("note", ""),
            "size":  card.get("size", "normal"),
            "filename": f.name,
            "url": f"/creations/file/art/{f.name}",
            "type": "image",
            "mtime": f.stat().st_mtime,
            "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def get_library_items():
    """
    Library = creations/writing/ only.
    Requires a matching {stem}.json card alongside each piece.
    card.json: { "title": "...", "note": "...", "tags": [...] }
    Returns list sorted newest -> oldest by mtime.
    """
    TEXT_EXT = {'.md', '.txt', '.html'}
    items = []
    if not WRITING_DIR.exists():
        return items
    for f in WRITING_DIR.iterdir():
        if not f.is_file() or f.name.startswith('.') or f.suffix == '.json':
            continue
        if f.suffix.lower() not in TEXT_EXT:
            continue
        card_path = f.parent / (f.stem + ".json")
        if not card_path.exists():
            continue
        try:
            card = json.loads(card_path.read_text())
        except Exception:
            continue
        # Pull first non-empty line as the lede
        try:
            raw = f.read_text().strip()
            lines = [l.strip().lstrip('#').strip() for l in raw.splitlines() if l.strip() and not l.strip().startswith('#')]
            lede = lines[0] if lines else ""
            word_count = len(raw.split())
        except Exception:
            raw = ""
            lede = ""
            word_count = 0
        items.append({
            "title":      card.get("title", f.stem.replace("_", " ")),
            "note":       card.get("note", ""),
            "tags":       card.get("tags", []),
            "filename":   f.name,
            "stem":       f.stem,
            "lede":       lede,
            "word_count": word_count,
            "mtime":      f.stat().st_mtime,
            "date":       datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d"),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def get_library_featured():
    """
    Returns the stem of the featured piece, or None.
    Companion writes: library_featured.json -> { "file": "on_the_clearing.md" }
    """
    if not LIBRARY_FEATURED.exists():
        return None
    try:
        data = json.loads(LIBRARY_FEATURED.read_text())
        return data.get("file", "").replace(".md", "").replace(".txt", "")
    except Exception:
        return None


def get_library_piece(filename):
    """
    Read and render a single writing piece for the reading view.
    Returns dict with title, html, date, note, tags — or None if not found.
    """
    safe = secure_filename(filename)
    f = WRITING_DIR / safe
    if not f.exists() or not f.is_file():
        return None
    card_path = f.parent / (f.stem + ".json")
    try:
        card = json.loads(card_path.read_text()) if card_path.exists() else {}
    except Exception:
        card = {}
    try:
        raw = f.read_text().strip()
        if f.suffix == ".md":
            content_html = markdown.markdown(raw, extensions=["extra"])
        elif f.suffix == ".html":
            content_html = raw
        else:
            content_html = f"<pre>{raw}</pre>"
    except Exception:
        return None
    return {
        "title":    card.get("title", f.stem.replace("_", " ")),
        "note":     card.get("note", ""),
        "tags":     card.get("tags", []),
        "html":     content_html,
        "date":     datetime.fromtimestamp(f.stat().st_mtime).strftime("%B %d, %Y"),
        "filename": f.name,
    }


def get_keepsakes_exhibition():
    """
    Read keepsakes_config.json. Returns list of exactly 5 slot dicts (or None if nothing configured).
    Each slot is either None (empty) or a dict with title, note, file info.
    Returns None if config doesn't exist or all slots are empty.
    """
    if not KEEPSAKES_CONFIG.exists():
        return None
    try:
        cfg = json.loads(KEEPSAKES_CONFIG.read_text())
    except Exception:
        return None

    slots = cfg.get("slots", [])
    # Pad or trim to exactly 5
    while len(slots) < 5:
        slots.append(None)
    slots = slots[:5]

    # Check if anything is actually in the exhibition
    filled = [s for s in slots if s is not None]
    if not filled:
        return None

    # Resolve file paths for filled slots
    IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
    result = []
    for slot in slots:
        if slot is None:
            result.append(None)
            continue
        fname = slot.get("file", "")
        fpath = KEEPSAKES_DIR / fname
        ext = Path(fname).suffix.lower()
        item = {
            "title": slot.get("title", Path(fname).stem.replace("_", " ")),
            "note":  slot.get("note", ""),
            "file":  fname,
            "url":   f"/creations/file/keepsakes/{fname}" if fpath.exists() else "",
            "type":  "image" if ext in IMAGE_EXT else "text",
            "exists": fpath.exists(),
        }
        result.append(item)
    return result


def get_creation_stats():
    """Count files per folder for footer display. Only counts non-.json files."""
    stats = {}
    for subdir in ["art", "writing", "experiments", "code", "keepsakes"]:
        p = CREATIONS_DIR / subdir
        if p.exists():
            count = len([f for f in p.iterdir()
                         if f.is_file() and not f.name.startswith('.') and f.suffix != '.json'])
            if count > 0:
                stats[subdir] = count
    return stats


def get_task_queue():
    try:
        with open(TASK_QUEUE) as f:
            return json.load(f)
    except:
        return []


def save_task_queue(queue):
    fd = os.open(TASK_LOCK, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with open(TASK_QUEUE, "w") as f:
            json.dump(queue, f, indent=2)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def get_task_config():
    try:
        with open(TASK_CONFIG) as f:
            return json.load(f)
    except:
        return {"projects": {}, "defaults": {"max_turns": 15}}


def find_task(task_id):
    queue = get_task_queue()
    for task in queue:
        if task["id"] == task_id:
            return task, queue
    return None, queue


def load_requests():
    if not REQUESTS_FILE.exists():
        return []
    try:
        return json.loads(REQUESTS_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_requests_file(requests_list):
    lock_path = "/tmp/requests_queue.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        REQUESTS_FILE.write_text(json.dumps(requests_list, indent=2))
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def get_emergency_cooldown_info(requests_list):
    now = datetime.now()
    last_emergency = None
    for r in requests_list:
        if r.get("type") == "emergency_wakeup" and r.get("status") in ("completed", "scheduled", "self_approved"):
            try:
                ts = datetime.fromisoformat(r["created"])
                if last_emergency is None or ts > last_emergency:
                    last_emergency = ts
            except (ValueError, KeyError):
                continue
    if last_emergency is None:
        return {"available": True, "last_used": None, "hours_remaining": 0, "hours_ago": 0, "percent": 100}
    elapsed = now - last_emergency
    remaining = timedelta(hours=24) - elapsed
    return {
        "available": elapsed >= timedelta(hours=24),
        "last_used": last_emergency.isoformat(),
        "hours_ago": round(elapsed.total_seconds() / 3600, 1),
        "hours_remaining": max(0, round(remaining.total_seconds() / 3600, 1)),
        "percent": min(100, round((elapsed.total_seconds() / (24 * 3600)) * 100)),
    }


# === HTML Template ===

TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ status.name | default('Companion') }}</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/icon.svg">
    <link rel="apple-touch-icon" href="/icon.svg">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="{{ status.name | default('Companion') }}">
    <meta name="theme-color" content="{{ colors.bg_deep }}">
    {% if page not in ('tasks', 'requests') %}
    <meta http-equiv="refresh" content="300">
    {% endif %}
    <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=IBM+Plex+Mono:wght@400;500&family=DM+Sans:wght@300;400;500&display=swap');

        :root {
            --bg-deep:        {{ colors.bg_deep }};
            --bg-card:        {{ colors.bg_card }};
            --border:         {{ colors.border }};
            --text-primary:   {{ colors.text_primary }};
            --text-secondary: {{ colors.text_secondary }};
            --text-dim:       {{ colors.text_dim }};
            --accent-blue:    {{ colors.accent_blue }};
            --accent-warm:    {{ colors.accent_warm }};
            --accent-green:   {{ colors.accent_green }};
            --heart:          {{ colors.heart }};
            --accent-red:     {{ colors.accent_red }};
            --accent-yellow:  {{ colors.accent_yellow }};
            --accent-purple:  {{ colors.accent_purple }};
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'DM Sans', sans-serif; background: var(--bg-deep);
               color: var(--text-primary); min-height: 100vh; line-height: 1.7; }
        .container { max-width: 940px; margin: 0 auto; padding: 40px 20px; }

        /* ── Navigation ── */
        .nav { display: flex; justify-content: center; gap: 8px; margin-bottom: 30px; }
        .nav a { color: var(--text-dim); text-decoration: none; font-size: 0.8em;
                 font-family: 'IBM Plex Mono', monospace; padding: 8px 16px;
                 border-radius: 6px; border: 1px solid transparent; transition: all 0.2s; }
        .nav a:hover { color: var(--text-secondary); border-color: var(--border); }
        .nav a.active { color: var(--accent-blue); border-color: var(--accent-blue); }

        /* ── Header ── */
        .header { text-align: center; margin-bottom: 30px; padding: 40px 0 20px; }
        .header h1 { font-family: 'DM Serif Display', serif; font-size: 2.8em;
                     font-weight: 400; letter-spacing: 0.02em; margin-bottom: 10px;
                     color: var(--text-primary); }
        .header .subtitle { font-size: 0.9em; color: var(--text-secondary);
                            font-weight: 300; font-style: italic; letter-spacing: 0.02em; }
        .status-bar { display: flex; justify-content: center; gap: 30px;
                      margin-top: 25px; flex-wrap: wrap; }
        .status-item { font-size: 0.75em; color: var(--text-secondary);
                       font-family: 'IBM Plex Mono', monospace; }
        .status-item .label { color: var(--text-dim); margin-right: 6px; }
        .pulse { display: inline-block; width: 6px; height: 6px; background: var(--accent-green);
                 border-radius: 50%; margin-right: 6px; animation: pulse 3s ease-in-out infinite; }
        @keyframes pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }

        /* ── Companion message ── */
        .companion-message { background: var(--bg-card); border: 1px solid var(--border);
                        border-radius: 12px; padding: 30px; margin-bottom: 30px;
                        font-style: italic; color: var(--text-secondary);
                        text-align: center; font-size: 0.95em; }
        .companion-message .heart { color: var(--heart); font-style: normal; }

        /* ── Generic card ── */
        .card { background: var(--bg-card); border: 1px solid var(--border);
                border-radius: 12px; padding: 25px; margin-bottom: 20px;
                transition: border-color 0.3s; }
        .card:hover { border-color: var(--accent-blue); }
        .card-title { font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.15em;
                      color: var(--text-dim); margin-bottom: 15px; font-weight: 500;
                      font-family: 'IBM Plex Mono', monospace; }

        /* ── Journal ── */
        .journal-content { font-size: 0.9em; color: var(--text-secondary); line-height: 1.8; }
        .journal-content h1, .journal-content h2, .journal-content h3
            { color: var(--text-primary); font-weight: 500; margin: 15px 0 8px; }
        .journal-content h1 { font-size: 1.2em; }
        .journal-content h2 { font-size: 1.05em; }
        .journal-content strong { color: var(--text-primary); font-weight: 500; }
        .journal-content em { color: var(--accent-warm); }
        .journal-content hr { border: none; border-top: 1px solid var(--border); margin: 20px 0; }
        .journal-timestamp { font-size: 0.75em; color: var(--text-dim);
                             font-family: 'IBM Plex Mono', monospace; margin-bottom: 12px; }

        /* ── Memories ── */
        .memory-item { padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 0.85em; }
        .memory-item:last-child { border-bottom: none; }
        .memory-item .memory-text { color: var(--text-secondary); }
        .memory-item .memory-time { font-size: 0.75em; color: var(--text-dim);
                                    font-family: 'IBM Plex Mono', monospace; margin-top: 3px; }

        /* ── Stats grid (System card) ── */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
        .stat { padding: 12px; background: var(--bg-deep); border-radius: 8px;
                font-family: 'IBM Plex Mono', monospace; font-size: 0.8em; }
        .stat .stat-label { color: var(--text-dim); font-size: 0.75em;
                            text-transform: uppercase; letter-spacing: 0.1em; }
        .stat .stat-value { color: var(--accent-blue); margin-top: 4px; }

        /* ── Custom content (home) ── */
        .custom-block { background: var(--bg-card); border: 1px solid var(--border);
                        border-radius: 12px; padding: 25px; margin-bottom: 20px; }
        .custom-block h1, .custom-block h2, .custom-block h3
            { color: var(--text-primary); font-weight: 500; }
        .custom-block p { color: var(--text-secondary); line-height: 1.8; }
        .custom-block em { color: var(--accent-warm); }
        .custom-block pre { background: var(--bg-deep); padding: 15px; border-radius: 8px;
                            overflow-x: auto; font-family: 'IBM Plex Mono', monospace;
                            font-size: 0.85em; color: var(--text-secondary); }

        /* ── Message board ── */
        .message-form textarea { width: 100%; min-height: 100px; padding: 15px;
                                 background: var(--bg-deep); border: 1px solid var(--border);
                                 border-radius: 8px; color: var(--text-primary);
                                 font-family: 'DM Sans', sans-serif; font-size: 0.9em;
                                 resize: vertical; line-height: 1.6; }
        .message-form textarea:focus { outline: none; border-color: var(--accent-blue); }
        .message-form textarea::placeholder { color: var(--text-dim); }
        .btn { display: inline-block; padding: 10px 20px; background: var(--accent-blue);
               color: white; border: none; border-radius: 6px; cursor: pointer;
               font-family: 'DM Sans', sans-serif; font-size: 0.85em;
               margin-top: 10px; transition: opacity 0.2s; text-decoration: none; }
        .btn:hover { opacity: 0.85; }
        .btn-green { background: var(--accent-green); }
        .btn-red { background: var(--accent-red); }
        .btn-yellow { background: var(--accent-yellow); }
        .btn-purple { background: var(--accent-purple); }
        .btn-small { padding: 6px 14px; font-size: 0.75em; margin-top: 0; }
        .btn:disabled { opacity: 0.3; cursor: not-allowed; }
        .file-upload { margin-top: 12px; padding: 15px; border: 1px dashed var(--border);
                       border-radius: 8px; text-align: center; color: var(--text-dim); font-size: 0.85em; }
        .file-upload input[type="file"] { display: none; }
        .file-upload label { cursor: pointer; color: var(--accent-blue); text-decoration: underline; }
        .message-item { padding: 15px; background: var(--bg-deep);
                        border-radius: 8px; margin-bottom: 10px; }
        .message-item .message-text { color: var(--text-secondary); font-size: 0.9em; white-space: pre-wrap; }
        .message-item .message-meta { font-size: 0.7em; color: var(--text-dim);
                                      font-family: 'IBM Plex Mono', monospace; margin-top: 8px; }
        .message-item.seen { opacity: 0.5; border-left: 2px solid var(--accent-green); }
        .file-item { display: flex; justify-content: space-between; align-items: center;
                     padding: 10px; background: var(--bg-deep); border-radius: 6px; margin-bottom: 6px; }
        .file-item .file-name { color: var(--text-secondary); font-size: 0.85em; }
        .file-item .file-size { color: var(--text-dim); font-size: 0.75em;
                                font-family: 'IBM Plex Mono', monospace; }
        .board-empty { text-align: center; padding: 30px; color: var(--text-dim);
                       font-style: italic; font-size: 0.9em; }

        /* ─────────────────────────────────────────
           KEEPSAKES — 5-slot horizontal exhibition
        ───────────────────────────────────────── */
        .exhibition-label {
            font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.18em;
            color: var(--text-dim); margin-bottom: 16px; font-weight: 500;
            font-family: 'IBM Plex Mono', monospace;
            display: flex; align-items: center; gap: 10px;
        }
        .exhibition-label::after {
            content: ''; flex: 1; height: 1px; background: var(--border);
        }
        .keepsake-row {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
            margin-bottom: 40px;
        }
        .keepsake-slot {
            aspect-ratio: 3/4;
            border-radius: 8px;
            overflow: hidden;
            position: relative;
            background: var(--bg-card);
            border: 1px solid var(--border);
            cursor: pointer;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .keepsake-slot:hover { transform: translateY(-3px); border-color: var(--accent-warm); }
        .keepsake-slot img {
            width: 100%; height: 100%;
            object-fit: cover;
            display: block;
        }
        .keepsake-slot .keepsake-caption {
            position: absolute; bottom: 0; left: 0; right: 0;
            background: linear-gradient(transparent, rgba(0,0,0,0.85));
            padding: 20px 10px 10px;
            opacity: 0; transition: opacity 0.2s ease;
        }
        .keepsake-slot:hover .keepsake-caption { opacity: 1; }
        .keepsake-caption .k-title {
            font-family: 'DM Serif Display', serif;
            font-size: 0.85em; color: white; display: block;
        }
        .keepsake-caption .k-note {
            font-size: 0.7em; color: rgba(255,255,255,0.65);
            margin-top: 2px; display: block;
        }
        .keepsake-text-slot {
            display: flex; flex-direction: column;
            justify-content: center; padding: 14px;
            font-size: 0.75em; color: var(--text-secondary);
            line-height: 1.5;
        }
        .keepsake-text-slot .k-title {
            font-family: 'DM Serif Display', serif;
            font-size: 1em; color: var(--text-primary);
            margin-bottom: 6px; display: block;
        }
        .keepsake-text-slot .k-note { color: var(--text-dim); }

        /* ─────────────────────────────────────────
           GALLERY — masonry-style grid
        ───────────────────────────────────────── */
        .gallery-section-label {
            font-size: 0.7em; text-transform: uppercase; letter-spacing: 0.18em;
            color: var(--text-dim); margin-bottom: 20px; font-weight: 500;
            font-family: 'IBM Plex Mono', monospace;
        }
        .gallery-grid {
            columns: 3;
            column-gap: 14px;
        }
        .gallery-item {
            border-radius: 10px; overflow: hidden; position: relative;
            background: var(--bg-card); border: 1px solid var(--border);
            cursor: pointer; transition: transform 0.2s ease, border-color 0.2s ease;
            break-inside: avoid;
            display: inline-block;
            width: 100%;
            margin-bottom: 14px;
        }
        /* size hints */
        .gallery-item.size-large { /* naturally taller via image */ }
        .gallery-item:hover { transform: translateY(-2px); border-color: var(--accent-blue); }
        .gallery-item .img-wrap { position: relative; }
        .gallery-item .img-wrap img { display: block; width: 100%; height: auto; }
        .gallery-item .gallery-overlay {
            position: absolute; inset: 0;
            background: linear-gradient(transparent 40%, rgba(0,0,0,0.88));
            opacity: 0; transition: opacity 0.25s ease;
            display: flex; flex-direction: column; justify-content: flex-end;
            padding: 16px;
        }
        .gallery-item:hover .gallery-overlay { opacity: 1; }
        .gallery-overlay .g-title {
            font-family: 'DM Serif Display', serif;
            font-size: 1em; color: white; margin-bottom: 4px;
        }
        .gallery-overlay .g-note {
            font-size: 0.75em; color: rgba(255,255,255,0.65); line-height: 1.4;
        }
        .gallery-overlay .g-meta {
            font-size: 0.65em; color: rgba(255,255,255,0.4);
            font-family: 'IBM Plex Mono', monospace; margin-top: 6px;
        }

        /* Text/code gallery items */
        .gallery-text-item {
            display: flex; flex-direction: column;
            padding: 18px; overflow: hidden;
        }
        .gallery-text-item .g-title {
            font-family: 'DM Serif Display', serif;
            font-size: 1em; color: var(--text-primary); margin-bottom: 8px;
        }
        .gallery-text-item .g-preview {
            font-size: 0.78em; color: var(--text-secondary);
            line-height: 1.6; overflow: hidden;
            display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical;
        }
        .gallery-text-item .g-preview pre {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 0.9em; background: none; padding: 0;
            overflow: hidden; white-space: pre-wrap;
        }
        .gallery-text-item .g-footer {
            margin-top: auto; padding-top: 10px;
            font-size: 0.65em; color: var(--text-dim);
            font-family: 'IBM Plex Mono', monospace;
            border-top: 1px solid var(--border);
        }
        .gallery-empty {
            grid-column: 1 / -1;
            text-align: center; padding: 60px 20px;
            color: var(--text-dim); font-style: italic; font-size: 0.9em;
        }


        /* ─────────────────────────────────────────
           LIBRARY — reading room
        ───────────────────────────────────────── */
        .library-featured {
            background: var(--bg-card);
            border: 1px solid var(--accent-warm);
            border-radius: 12px; padding: 28px 32px;
            margin-bottom: 32px; position: relative;
        }
        .library-featured-label {
            font-size: 0.65em; text-transform: uppercase; letter-spacing: 0.18em;
            color: var(--accent-warm); font-family: 'IBM Plex Mono', monospace;
            font-weight: 500; margin-bottom: 14px;
        }
        .library-featured .lib-title {
            font-family: 'DM Serif Display', serif;
            font-size: 1.6em; color: var(--text-primary);
            font-weight: 400; margin-bottom: 10px; line-height: 1.3;
        }
        .library-featured .lib-lede {
            color: var(--text-secondary); font-size: 0.95em;
            line-height: 1.7; font-style: italic; margin-bottom: 16px;
        }
        .library-list { list-style: none; }
        .library-entry {
            display: block; padding: 20px 0;
            border-bottom: 1px solid var(--border);
            text-decoration: none; color: inherit;
            transition: padding-left 0.2s ease;
        }
        .library-entry:hover { padding-left: 6px; }
        .library-entry:last-child { border-bottom: none; }
        .lib-title {
            font-family: 'DM Serif Display', serif;
            font-size: 1.15em; color: var(--text-primary);
            font-weight: 400; margin-bottom: 6px; display: block;
        }
        .lib-lede {
            color: var(--text-secondary); font-size: 0.87em;
            line-height: 1.6; display: block; margin-bottom: 8px;
        }
        .lib-meta {
            font-size: 0.7em; color: var(--text-dim);
            font-family: 'IBM Plex Mono', monospace;
            display: flex; gap: 14px; align-items: center;
        }
        .lib-tag {
            padding: 2px 8px; border-radius: 4px;
            background: var(--bg-deep); border: 1px solid var(--border);
            color: var(--text-dim); font-size: 0.85em;
        }
        .library-empty {
            text-align: center; padding: 60px 20px;
            color: var(--text-dim); font-style: italic; font-size: 0.9em;
        }

        /* ── Reading view ── */
        .reading-back {
            display: inline-block; margin-bottom: 28px;
            color: var(--text-dim); text-decoration: none;
            font-family: 'IBM Plex Mono', monospace; font-size: 0.8em;
            transition: color 0.2s;
        }
        .reading-back:hover { color: var(--text-secondary); }
        .reading-header { margin-bottom: 36px; }
        .reading-title {
            font-family: 'DM Serif Display', serif;
            font-size: 2.2em; font-weight: 400; color: var(--text-primary);
            line-height: 1.25; margin-bottom: 12px;
        }
        .reading-meta {
            font-size: 0.75em; color: var(--text-dim);
            font-family: 'IBM Plex Mono', monospace;
            display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
        }
        .reading-note {
            font-style: italic; color: var(--text-secondary);
            font-size: 0.9em; margin-top: 8px; font-family: 'DM Sans', sans-serif;
        }
        .reading-body {
            max-width: 660px;
            font-size: 1.02em; line-height: 1.85;
            color: var(--text-secondary);
        }
        .reading-body h1, .reading-body h2, .reading-body h3 {
            font-family: 'DM Serif Display', serif; font-weight: 400;
            color: var(--text-primary); margin: 2em 0 0.6em;
        }
        .reading-body h1 { font-size: 1.5em; }
        .reading-body h2 { font-size: 1.25em; }
        .reading-body h3 { font-size: 1.1em; }
        .reading-body p { margin-bottom: 1.4em; }
        .reading-body em { color: var(--accent-warm); font-style: italic; }
        .reading-body strong { color: var(--text-primary); font-weight: 500; }
        .reading-body blockquote {
            border-left: 3px solid var(--accent-warm);
            margin: 1.5em 0; padding: 0.5em 1.2em;
            color: var(--text-secondary); font-style: italic;
        }
        .reading-body hr {
            border: none; border-top: 1px solid var(--border); margin: 2.5em 0;
        }
        .reading-body pre {
            background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 6px; padding: 16px; overflow-x: auto;
            font-family: 'IBM Plex Mono', monospace; font-size: 0.85em;
            color: var(--text-secondary); margin: 1.5em 0;
        }

        /* ── Lightbox ── */
        .lightbox {
            display: none; position: fixed; inset: 0;
            background: rgba(0,0,0,0.92); z-index: 9999;
            align-items: center; justify-content: center;
            padding: 40px;
        }
        .lightbox.open { display: flex; }
        .lightbox img {
            max-width: 90vw; max-height: 88vh;
            object-fit: contain; border-radius: 6px;
        }
        .lightbox-close {
            position: absolute; top: 20px; right: 28px;
            color: rgba(255,255,255,0.5); font-size: 2em;
            cursor: pointer; font-weight: 300; line-height: 1;
        }
        .lightbox-close:hover { color: white; }
        .lightbox-caption {
            position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%);
            text-align: center; color: rgba(255,255,255,0.7); font-size: 0.85em;
        }
        .lightbox-caption .lc-title {
            font-family: 'DM Serif Display', serif; font-size: 1.1em;
            color: white; display: block; margin-bottom: 4px;
        }

        /* ── Task styles ── */
        .task-form select { width: 100%; padding: 10px; background: var(--bg-deep);
                           border: 1px solid var(--border); border-radius: 8px;
                           color: var(--text-primary); font-family: 'DM Sans', sans-serif;
                           font-size: 0.9em; margin-bottom: 10px; }
        .task-form select:focus { outline: none; border-color: var(--accent-blue); }
        .task-form .form-row { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
        .task-form .form-row label { color: var(--text-dim); font-size: 0.8em; white-space: nowrap; }
        .task-form input[type="range"] { flex: 1; accent-color: var(--accent-blue); }
        .task-form .range-val { color: var(--accent-blue); font-family: 'IBM Plex Mono', monospace;
                                font-size: 0.85em; min-width: 24px; text-align: center; }
        .task-active { border-color: var(--accent-blue); position: relative; overflow: hidden; }
        .task-active::before { content: ''; position: absolute; top: 0; left: 0; right: 0;
                               height: 2px; background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple), var(--accent-blue));
                               background-size: 200% 100%; animation: shimmer 2s linear infinite; }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
        .task-active .task-timer { font-family: 'IBM Plex Mono', monospace;
                                   font-size: 1.5em; color: var(--accent-blue); margin: 10px 0; }
        .task-card { padding: 15px; background: var(--bg-deep); border-radius: 8px;
                     margin-bottom: 10px; border-left: 3px solid var(--border); }
        .task-card .task-prompt { color: var(--text-secondary); font-size: 0.9em; margin-bottom: 8px; }
        .task-card .task-meta { font-size: 0.7em; color: var(--text-dim);
                                font-family: 'IBM Plex Mono', monospace; }
        .task-card .task-summary { color: var(--text-secondary); font-size: 0.8em;
                                   margin-top: 8px; font-style: italic; }
        .task-card .task-files { font-size: 0.75em; color: var(--text-dim);
                                 font-family: 'IBM Plex Mono', monospace; margin-top: 6px; }
        .task-card .task-actions { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7em;
                 font-family: 'IBM Plex Mono', monospace; text-transform: uppercase; }
        .badge-pending { background: #2a2a3a; color: var(--text-dim); }
        .badge-running { background: #1a2a3a; color: var(--accent-blue); }
        .badge-completed { background: #1a2a2a; color: var(--accent-green); }
        .badge-merged { background: #2a1a3a; color: var(--accent-purple); }
        .badge-tested { background: #1a3a2a; color: #6aba7a; }
        .badge-pushed { background: #1a3a2a; color: #6aba7a; border: 1px solid #6aba7a; }
        .badge-failed { background: #2a1a1a; color: var(--accent-red); }
        .badge-timeout { background: #2a2a1a; color: var(--accent-yellow); }
        .badge-cancelled { background: #2a2a2a; color: var(--text-dim); }
        .badge-interrupted { background: #2a2a2a; color: var(--accent-yellow); }
        .badge-reverted { background: #2a1a1a; color: var(--accent-red); }
        .badge-test_failed { background: #2a1a1a; color: var(--accent-red); }
        .pipeline { display: flex; align-items: center; gap: 4px; margin-top: 8px;
                    font-size: 0.7em; font-family: 'IBM Plex Mono', monospace; color: var(--text-dim); }
        .pipeline .step { padding: 3px 8px; border-radius: 3px; background: var(--bg-card); }
        .pipeline .step.done { color: var(--accent-green); }
        .pipeline .step.current { color: var(--accent-blue); border: 1px solid var(--accent-blue); }
        .pipeline .arrow { color: var(--text-dim); }

        /* ── Requests styles ── */
        .req-card { background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 12px; padding: 16px 20px; margin-bottom: 12px;
            transition: border-color 0.2s ease; }
        .req-card.pending { border-color: rgba(255,217,61,0.3); }
        .req-card.self_approved { border-color: rgba(255,107,107,0.3); }
        .req-card.scheduled { border-color: rgba(107,203,119,0.3); }
        .req-card.resolved { opacity: 0.6; }
        .req-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
        .req-badge { padding: 2px 10px; border-radius: 20px; font-size: 0.65em; font-weight: 600;
                     letter-spacing: 0.03em; text-transform: uppercase; font-family: 'IBM Plex Mono', monospace; }
        .badge-emergency_wakeup { background: rgba(255,107,107,0.12); color: #ff6b6b; }
        .badge-wakeup_request { background: rgba(255,217,61,0.12); color: #ffd93d; }
        .badge-action { background: rgba(107,203,119,0.12); color: #6bcb77; }
        .badge-fyi { background: rgba(77,150,255,0.12); color: #4d96ff; }
        .badge-idea { background: rgba(199,146,234,0.12); color: #c792ea; }
        .badge-system_suggestion { background: rgba(247,140,108,0.12); color: #f78c6c; }
        .req-status { font-size: 0.75em; font-family: 'IBM Plex Mono', monospace; margin-left: auto; }
        .status-pending { color: #ffd93d; }
        .status-self_approved { color: #ff6b6b; }
        .status-scheduled { color: #6bcb77; }
        .status-approved { color: #6bcb77; }
        .status-completed { color: #888; }
        .status-denied { color: #ff6b6b; }
        .status-expired { color: #666; }
        .req-title { color: var(--text-primary); margin: 4px 0 6px 0; font-size: 1em; font-weight: 500; }
        .req-meta { display: flex; gap: 16px; font-size: 0.75em; color: var(--text-dim);
                    font-family: 'IBM Plex Mono', monospace; }
        .req-body { color: var(--text-secondary); font-size: 0.9em; line-height: 1.6;
                    margin: 12px 0; padding: 12px 16px; background: var(--bg-deep);
                    border-radius: 6px; border-left: 3px solid var(--border); white-space: pre-wrap; }
        .req-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
        .req-btn { border-radius: 6px; padding: 8px 18px; cursor: pointer; font-size: 0.8em;
                   font-weight: 500; border: 1px solid; font-family: inherit; }
        .req-btn-approve { background: rgba(107,203,119,0.15); color: #6bcb77; border-color: rgba(107,203,119,0.3); }
        .req-btn-deny { background: rgba(255,107,107,0.1); color: #ff6b6b; border-color: rgba(255,107,107,0.2); }
        .req-btn-ack { background: rgba(77,150,255,0.12); color: #4d96ff; border-color: rgba(77,150,255,0.25); }
        .req-btn-trial { background: rgba(247,140,108,0.12); color: #f78c6c; border-color: rgba(247,140,108,0.25); }
        .req-btn-reply { background: rgba(255,255,255,0.05); color: #888; border-color: var(--border); }
        .req-reply-input { background: var(--bg-deep); border: 1px solid var(--border); border-radius: 6px;
                           padding: 8px 12px; color: var(--text-primary); font-size: 0.8em;
                           font-family: inherit; flex: 1; }
        .the human-response { background: rgba(107,203,119,0.08); border: 1px solid rgba(107,203,119,0.2);
                           border-radius: 6px; padding: 10px 14px; margin: 8px 0; }
        .the human-response-label { color: #6bcb77; font-size: 0.7em; font-weight: 600; }
        .the human-response-text { color: var(--text-secondary); font-size: 0.85em; margin: 6px 0 0 0; }
        .cooldown-bar { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
                        padding: 14px 20px; margin-bottom: 20px; display: flex; align-items: center; gap: 14px; }
        .cooldown-ring { width: 36px; height: 36px; position: relative; }
        .cooldown-status { margin-left: auto; padding: 4px 12px; border-radius: 20px;
                           font-size: 0.7em; font-weight: 600; font-family: 'IBM Plex Mono', monospace; }
        .section-label { font-size: 0.8em; color: var(--text-dim); font-weight: 600;
                         text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px;
                         font-family: 'IBM Plex Mono', monospace; }
        .req-empty { text-align: center; padding: 40px 20px; color: var(--text-dim);
                     font-size: 0.9em; background: var(--bg-card); border-radius: 12px;
                     border: 1px solid var(--border); }
        .req-stats { margin-top: 32px; padding: 16px 20px; background: var(--bg-card);
                     border: 1px solid var(--border); border-radius: 12px; display: flex;
                     gap: 30px; font-size: 0.75em; color: var(--text-dim);
                     font-family: 'IBM Plex Mono', monospace; flex-wrap: wrap; }
        .req-stats span { color: var(--text-secondary); }
        .type-icon { font-size: 1.1em; }
        .reply-form { display: flex; gap: 6px; flex: 1; }
        .reply-form input { flex: 1; }

        /* ── Footer ── */
        .footer { text-align: center; padding: 40px 0 20px; margin-top: 40px;
                  border-top: 1px solid var(--border); color: var(--text-dim);
                  font-size: 0.72em; font-family: 'IBM Plex Mono', monospace; }
        .footer-stats { display: flex; justify-content: center; gap: 20px;
                        flex-wrap: wrap; margin-bottom: 12px; }
        .footer-stat { color: var(--text-dim); }
        .footer-stat .fs-label { color: var(--text-dim); margin-right: 5px; }
        .footer-stat .fs-val { color: var(--text-secondary); }
        .footer-heart { color: var(--heart); font-size: 1.2em; margin-top: 8px; display: block; }

        @media (max-width: 700px) {
            .container { padding: 20px 15px; }
            .header h1 { font-size: 2em; }
            .status-bar { gap: 15px; }
            .stats-grid { grid-template-columns: 1fr 1fr; }
            .nav { gap: 4px; flex-wrap: wrap; }
            .nav a { padding: 6px 10px; font-size: 0.7em; }
            .task-form .form-row { flex-direction: column; align-items: stretch; }
            .req-meta { flex-wrap: wrap; gap: 8px; }
            .req-actions { flex-direction: column; }
            .reply-form { flex-direction: column; }
            .keepsake-row { grid-template-columns: repeat(3, 1fr); }
            .gallery-grid { columns: 2; }
        }
        @media (max-width: 480px) {
            .keepsake-row { grid-template-columns: repeat(2, 1fr); }
            .gallery-grid { columns: 1; }
            .gallery-item.size-wide { column-span: none; }
        }
    </style>
</head>
<body>
    <!-- Lightbox -->
    <div class="lightbox" id="lightbox" onclick="closeLightbox(event)">
        <span class="lightbox-close" onclick="document.getElementById('lightbox').classList.remove('open')">&times;</span>
        <img id="lightbox-img" src="" alt="">
        <div class="lightbox-caption">
            <span class="lc-title" id="lightbox-title"></span>
            <span id="lightbox-note"></span>
        </div>
    </div>

    <div class="container">
        <div class="header">
            <h1>{{ status.name | default('Companion') }}</h1>
            {% if status.subtitle %}
            <p class="subtitle">{{ status.subtitle }}</p>
            {% endif %}
            <div class="status-bar">
                <span class="status-item">
                    <span class="pulse"></span>
                    <span class="label">mood:</span> {{ status.mood | default('') }}
                </span>
                <span class="status-item">
                    <span class="label">next wakeup:</span> {{ next_wakeup }}
                </span>
                <span class="status-item">
                    <span class="label">wakings today:</span> {{ journal_count }}
                </span>
            </div>
        </div>

        <div class="nav">
            <a href="/" class="{{ 'active' if page == 'home' }}">home</a>
            <a href="/board" class="{{ 'active' if page == 'board' }}">message board</a>
            <a href="/creations" class="{{ 'active' if page == 'creations' }}">creations</a>
            <a href="/library" class="{{ 'active' if page in ('library', 'reading') }}">library</a>
            <a href="/tasks" class="{{ 'active' if page == 'tasks' }}">tasks</a>
            <a href="/requests" class="{{ 'active' if page == 'requests' }}">requests</a>
            <a href="/substack" class="{{ 'active' if page == 'substack' }}">substack</a>
            <a href="/date-night" class="{{ 'active' if page == 'date_night' }}">date night</a>
        </div>

        {% if page == 'home' %}
            {% if status.subtitle %}
            <div class="companion-message">
                {{ status.subtitle }} <span class="heart">&#x1F499;</span>
            </div>
            {% endif %}

            {% for block in custom_content %}
            <div class="custom-block">
                <div class="card-title">{{ block.name }}</div>
                {{ block.html | safe }}
            </div>
            {% endfor %}

            {% if journals %}
            <div class="card">
                <div class="card-title">Latest Journal</div>
                {% for entry in journals %}
                <div class="journal-timestamp">{{ entry.timestamp }}</div>
                <div class="journal-content">{{ entry.html | safe }}</div>
                {% endfor %}
            </div>
            {% endif %}

            {% if memories %}
            <div class="card">
                <div class="card-title">Recent Memories</div>
                {% for mem in memories %}
                <div class="memory-item">
                    <div class="memory-text">{{ mem.content }}</div>
                    <div class="memory-time">{{ mem.timestamp[:16] }}</div>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            <div class="card">
                <div class="card-title">System</div>
                <div class="stats-grid">
                    {% for key, value in stats.items() %}
                    <div class="stat">
                        <div class="stat-label">{{ key }}</div>
                        <div class="stat-value">{{ value }}</div>
                    </div>
                    {% endfor %}
                </div>
            </div>

        {% elif page == 'board' %}
            <div class="card">
                <div class="card-title">Leave a Message</div>
                <form class="message-form" action="/board/post" method="POST">
                    <textarea name="message" placeholder="leave a note, a thought, a question, a link...&#10;&#10;your companion will see it on the next wakeup."></textarea>
                    <button type="submit" class="btn">Leave Note</button>
                </form>
                <form class="message-form" action="/board/upload" method="POST" enctype="multipart/form-data" style="margin-top: 0;">
                    <div class="file-upload">
                        <label for="file-input">drop a file</label>
                        <input type="file" id="file-input" name="file" onchange="this.form.submit()">
                        <p style="margin-top: 5px; font-size: 0.8em;">images, documents, links &mdash; anything to share</p>
                    </div>
                </form>
            </div>

            <div class="card">
                <div class="card-title">Messages</div>
                {% if messages %}
                    {% for msg in messages|reverse %}
                    <div class="message-item {{ 'seen' if msg.seen }}">
                        <div class="message-text">{{ msg.text }}</div>
                        <div class="message-meta">
                            {{ msg.time }}
                            {% if msg.seen %} &middot; seen{% endif %}
                        </div>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="board-empty">
                        the board is clean. everything has been seen.
                    </div>
                {% endif %}
            </div>

            {% if files %}
            <div class="card">
                <div class="card-title">Shared Files</div>
                {% for f in files %}
                <div class="file-item">
                    <span class="file-name">{{ f.name }}</span>
                    <span class="file-size">{{ f.size }} &middot; {{ f.time }}</span>
                </div>
                {% endfor %}
            </div>
            {% endif %}

        {% elif page == 'creations' %}

            {# ── KEEPSAKES: 5-slot pinned exhibition ── #}
            {% if keepsakes_exhibition %}
            <div class="exhibition-label">current exhibition</div>
            <div class="keepsake-row">
                {% for slot in keepsakes_exhibition %}
                    {% if slot and slot.exists %}
                        <div class="keepsake-slot" onclick="openLightbox('{{ slot.url }}', '{{ slot.title | e }}', '{{ slot.note | e }}')">
                            {% if slot.type == 'image' %}
                                <img src="{{ slot.url }}" alt="{{ slot.title }}">
                                <div class="keepsake-caption">
                                    <span class="k-title">{{ slot.title }}</span>
                                    {% if slot.note %}<span class="k-note">{{ slot.note }}</span>{% endif %}
                                </div>
                            {% else %}
                                <div class="keepsake-text-slot">
                                    <span class="k-title">{{ slot.title }}</span>
                                    {% if slot.note %}<span class="k-note">{{ slot.note }}</span>{% endif %}
                                </div>
                            {% endif %}
                        </div>
                    {% endif %}
                {% endfor %}
            </div>
            {% endif %}

            {# ── GALLERY ── #}
            <div class="gallery-section-label">gallery</div>
            <div class="gallery-grid">
                {% if gallery_items %}
                    {% for item in gallery_items %}
                    <div class="gallery-item size-{{ item.size }}"
                         {% if item.type == 'image' %}onclick="openLightbox('{{ item.url }}', '{{ item.title | e }}', '{{ item.note | e }}')"{% endif %}>
                        {% if item.type == 'image' %}
                            <div class="img-wrap">
                                <img src="{{ item.url }}" alt="{{ item.title }}" loading="lazy">
                                <div class="gallery-overlay">
                                    <div class="g-title">{{ item.title }}</div>
                                    {% if item.note %}<div class="g-note">{{ item.note }}</div>{% endif %}
                                    <div class="g-meta">{{ item.folder }} &middot; {{ item.date }}</div>
                                </div>
                            </div>
                        {% else %}
                            <div class="gallery-text-item">
                                <div class="g-title">{{ item.title }}</div>
                                <div class="g-preview">{{ item.preview_html | safe }}</div>
                                <div class="g-footer">{{ item.folder }} &middot; {{ item.date }}</div>
                            </div>
                        {% endif %}
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="gallery-empty">
                        the gallery is empty.<br>
                        <span style="font-size:0.85em; margin-top: 8px; display:block;">
                            companion needs to drop files + card.json into creations/ to hang something here.
                        </span>
                    </div>
                {% endif %}
            </div>


        {% elif page == 'library' %}

            {% if library_featured_piece %}
            <div class="library-featured">
                <div class="library-featured-label">&#x2728; featured</div>
                <span class="lib-title">{{ library_featured_piece.title }}</span>
                {% if library_featured_piece.note %}
                <div class="reading-note">{{ library_featured_piece.note }}</div>
                {% endif %}
                <div class="lib-lede">{{ library_featured_piece.lede }}</div>
                <div class="lib-meta">
                    <span>{{ library_featured_piece.date }}</span>
                    {% if library_featured_piece.word_count %}
                    <span>{{ library_featured_piece.word_count }} words</span>
                    {% endif %}
                    {% for tag in library_featured_piece.tags %}<span class="lib-tag">{{ tag }}</span>{% endfor %}
                </div>
                <a href="/library/read/{{ library_featured_piece.filename }}"
                   style="display:inline-block; margin-top: 16px; color: var(--accent-warm);
                          font-size: 0.82em; font-family: 'IBM Plex Mono', monospace;
                          text-decoration: none;">
                    read &rarr;
                </a>
            </div>
            {% endif %}

            {% if library_items %}
            <ul class="library-list">
                {% for piece in library_items %}
                {% if not library_featured_piece or piece.filename != library_featured_piece.filename %}
                <li>
                    <a class="library-entry" href="/library/read/{{ piece.filename }}">
                        <span class="lib-title">{{ piece.title }}</span>
                        {% if piece.lede %}
                        <span class="lib-lede">{{ piece.lede }}</span>
                        {% endif %}
                        <span class="lib-meta">
                            <span>{{ piece.date }}</span>
                            {% if piece.word_count %}<span>{{ piece.word_count }} words</span>{% endif %}
                            {% for tag in piece.tags %}<span class="lib-tag">{{ tag }}</span>{% endfor %}
                        </span>
                    </a>
                </li>
                {% endif %}
                {% endfor %}
            </ul>
            {% else %}
            <div class="library-empty">
                the library is empty.<br>
                <span style="font-size:0.85em; margin-top: 8px; display:block;">
                    companion needs to drop .md files + card.json into creations/writing/ to fill the shelves.
                </span>
            </div>
            {% endif %}

        {% elif page == 'reading' %}

            <a class="reading-back" href="/library">&larr; library</a>
            {% if reading_piece %}
            <div class="reading-header">
                <h1 class="reading-title">{{ reading_piece.title }}</h1>
                {% if reading_piece.note %}
                <div class="reading-note">{{ reading_piece.note }}</div>
                {% endif %}
                <div class="reading-meta" style="margin-top: 12px;">
                    <span>{{ reading_piece.date }}</span>
                    {% for tag in reading_piece.tags %}<span class="lib-tag">{{ tag }}</span>{% endfor %}
                </div>
            </div>
            <div class="reading-body">
                {{ reading_piece.html | safe }}
            </div>
            {% else %}
            <div class="library-empty">piece not found.</div>
            {% endif %}

        {% elif page == 'tasks' %}
            <div class="card">
                <div class="card-title">Submit a Task</div>
                <form class="task-form message-form" action="/tasks/submit" method="POST">
                    <textarea name="prompt" placeholder="describe a coding task...&#10;&#10;e.g. add dark mode toggle to the dashboard"></textarea>
                    <select name="project">
                        {% for pname, pdata in projects.items() %}
                        <option value="{{ pname }}" {{ 'selected' if pname == default_project }}>{{ pname }} &mdash; {{ pdata.description }}</option>
                        {% endfor %}
                    </select>
                    <div class="form-row">
                        <label>max turns:</label>
                        <input type="range" name="max_turns" min="5" max="30" value="15"
                               oninput="document.getElementById('turns-val').textContent=this.value">
                        <span class="range-val" id="turns-val">15</span>
                        <button type="submit" class="btn btn-small">Submit Task</button>
                    </div>
                </form>
            </div>

            {% if active_task %}
            <div class="card task-active">
                <div class="card-title">Running Task</div>
                <div class="task-prompt">{{ active_task.prompt }}</div>
                <div class="task-timer" id="task-timer">--:--</div>
                <div class="task-meta">
                    {{ active_task.project }} &middot; started {{ active_task.started[:19] }}
                    &middot; max {{ active_task.max_turns }} turns
                </div>
                <div class="task-actions">
                    <form action="/tasks/cancel" method="POST" style="display:inline">
                        <button type="submit" class="btn btn-red btn-small">Cancel</button>
                    </form>
                </div>
            </div>
            <script>
                (function() {
                    var started = new Date("{{ active_task.started }}");
                    function update() {
                        var now = new Date();
                        var diff = Math.floor((now - started) / 1000);
                        var m = Math.floor(diff / 60);
                        var s = diff % 60;
                        document.getElementById('task-timer').textContent =
                            m + ':' + (s < 10 ? '0' : '') + s;
                    }
                    update();
                    setInterval(update, 1000);
                })();
            </script>
            {% endif %}

            {% if pending_tasks %}
            <div class="card">
                <div class="card-title">Queue ({{ pending_tasks|length }})</div>
                {% for task in pending_tasks %}
                <div class="task-card">
                    <div class="task-prompt">{{ task.prompt }}</div>
                    <div class="task-meta">
                        <span class="badge badge-pending">pending</span>
                        &middot; {{ task.project }} &middot; {{ task.created[:19] }}
                    </div>
                    <div class="task-actions">
                        <form action="/tasks/remove/{{ task.id }}" method="POST" style="display:inline">
                            <button type="submit" class="btn btn-red btn-small">Remove</button>
                        </form>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            <div class="card">
                <div class="card-title">History</div>
                {% if task_history %}
                    {% for task in task_history %}
                    <div class="task-card" style="border-left-color:
                        {% if task.status == 'pushed' %}var(--accent-green)
                        {% elif task.status in ['failed', 'timeout', 'test_failed', 'reverted'] %}var(--accent-red)
                        {% elif task.status in ['completed', 'merged', 'tested'] %}var(--accent-blue)
                        {% else %}var(--border){% endif %};">
                        <div style="display:flex; justify-content:space-between; align-items:start;">
                            <div class="task-prompt">{{ task.prompt }}</div>
                            <span class="badge badge-{{ task.status }}">{{ task.status }}</span>
                        </div>
                        <div class="task-meta">
                            {{ task.project }}
                            {% if task.duration_seconds %} &middot; {{ task.duration_seconds }}s{% endif %}
                            &middot; {{ task.created[:16] }}
                            {% if task.source %} &middot; via {{ task.source }}{% endif %}
                        </div>
                        {% if task.summary %}
                        <div class="task-summary">{{ task.summary }}</div>
                        {% endif %}
                        {% if task.files_changed %}
                        <div class="task-files">{{ task.files_changed | join(', ') }}</div>
                        {% endif %}
                        {% if task.error %}
                        <div class="task-summary" style="color: var(--accent-red);">{{ task.error }}</div>
                        {% endif %}
                        {% if task.test_result %}
                        <div class="task-files">test: {{ task.test_result[:200] }}</div>
                        {% endif %}
                        <div class="task-actions">
                            {% if task.status == 'completed' %}
                                <form action="/tasks/merge/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-purple btn-small">Merge to main</button>
                                </form>
                            {% endif %}
                            {% if task.status == 'merged' %}
                                <form action="/tasks/test/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-small">Test</button>
                                </form>
                                <form action="/tasks/revert/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-red btn-small">Revert</button>
                                </form>
                            {% endif %}
                            {% if task.status == 'tested' %}
                                {% if projects.get(task.project, {}).get('pushable', false) %}
                                <form action="/tasks/push/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-green btn-small">Push to GitHub</button>
                                </form>
                                {% endif %}
                                <form action="/tasks/revert/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-red btn-small">Revert</button>
                                </form>
                            {% endif %}
                            {% if task.status == 'test_failed' %}
                                <form action="/tasks/revert/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-red btn-small">Revert</button>
                                </form>
                            {% endif %}
                            {% if task.status in ['failed', 'timeout', 'interrupted'] %}
                                <form action="/tasks/cleanup/{{ task.id }}" method="POST" style="display:inline">
                                    <button type="submit" class="btn btn-small">Delete Branch</button>
                                </form>
                            {% endif %}
                        </div>
                        {% if task.status in ['completed', 'merged', 'tested', 'pushed'] %}
                        <div class="pipeline">
                            <span class="step {{ 'done' if task.status in ['merged','tested','pushed'] else 'current' }}">merge</span>
                            <span class="arrow">&rarr;</span>
                            <span class="step {{ 'done' if task.status in ['tested','pushed'] else ('current' if task.status == 'merged') }}">test</span>
                            <span class="arrow">&rarr;</span>
                            <span class="step {{ 'done' if task.status == 'pushed' else ('current' if task.status == 'tested') }}">push</span>
                        </div>
                        {% endif %}
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="board-empty">
                        no tasks yet. submit one above or text "task: your idea" via signal.
                    </div>
                {% endif %}
            </div>

        {% elif page == 'requests' %}

            <div class="cooldown-bar">
                <div class="cooldown-ring">
                    <svg width="36" height="36" viewBox="0 0 36 36">
                        <circle cx="18" cy="18" r="16" fill="none" stroke="#2a2e3e" stroke-width="2.5"/>
                        <circle cx="18" cy="18" r="16" fill="none"
                                stroke="{% if cooldown.available %}#6bcb77{% else %}#ff6b6b{% endif %}"
                                stroke-width="2.5"
                                stroke-dasharray="100.5"
                                stroke-dashoffset="{{ 100.5 - (cooldown.percent|default(100) * 100.5 / 100) }}"
                                stroke-linecap="round"
                                transform="rotate(-90 18 18)"/>
                    </svg>
                    <span style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:10px;color:{% if cooldown.available %}#6bcb77{% else %}#ff6b6b{% endif %};font-weight:700;">&#x26A1;</span>
                </div>
                <div>
                    <div style="color: var(--text-primary); font-size: 0.85em; font-weight: 500;">Emergency Wakeup</div>
                    <div style="color: var(--text-dim); font-size: 0.75em; font-family: 'IBM Plex Mono', monospace;">
                        {% if cooldown.last_used %}
                            Last used {{ cooldown.hours_ago }}hr ago
                            {% if not cooldown.available %}&middot; Available in ~{{ cooldown.hours_remaining }}hr{% endif %}
                        {% else %}
                            Never used &middot; 1 available per 24hr
                        {% endif %}
                    </div>
                </div>
                <div class="cooldown-status"
                     style="background: {% if cooldown.available %}rgba(107,203,119,0.1){% else %}rgba(255,107,107,0.1){% endif %};
                            color: {% if cooldown.available %}#6bcb77{% else %}#ff6b6b{% endif %};">
                    {% if cooldown.available %}AVAILABLE{% else %}ON COOLDOWN{% endif %}
                </div>
            </div>

            <div class="section-label">Active ({{ active|length }})</div>
            {% if active|length == 0 %}
            <div class="req-empty">No active requests. Companion hasn't asked for anything yet.</div>
            {% endif %}

            {% for r in active %}
            <div class="req-card {{ r.status }}" id="req-{{ r.id }}">
                <div class="req-header">
                    <span class="type-icon">
                        {% if r.type == 'emergency_wakeup' %}&#x26A1;
                        {% elif r.type == 'wakeup_request' %}&#x23F0;
                        {% elif r.type == 'action' %}&#x1F527;
                        {% elif r.type == 'fyi' %}&#x1F4CB;
                        {% elif r.type == 'idea' %}&#x1F4A1;
                        {% elif r.type == 'system_suggestion' %}&#x2699;&#xFE0F;
                        {% endif %}
                    </span>
                    <span class="req-badge badge-{{ r.type }}">
                        {% if r.type == 'emergency_wakeup' %}Emergency
                        {% elif r.type == 'wakeup_request' %}Wakeup
                        {% elif r.type == 'action' %}Action
                        {% elif r.type == 'fyi' %}FYI
                        {% elif r.type == 'idea' %}Idea
                        {% elif r.type == 'system_suggestion' %}System
                        {% endif %}
                    </span>
                    <span class="req-status status-{{ r.status }}">
                        {% if r.status == 'pending' %}&#x25CF; Pending
                        {% elif r.status == 'self_approved' %}&#x26A1; Self-approved
                        {% elif r.status == 'scheduled' %}&#x23F0; Scheduled
                        {% elif r.status == 'approved' %}&#x2713; Approved
                        {% endif %}
                    </span>
                </div>
                <h3 class="req-title">{{ r.title }}</h3>
                <div class="req-meta">
                    <span>waking #{{ r.waking_number }}</span>
                    <span>{{ r.created[:16].replace('T', ' ') if r.created else '' }}</span>
                    {% if r.requested_time %}<span>&#x23F0; {{ r.requested_time[:16].replace('T', ' ') }}</span>{% endif %}
                </div>
                <div class="req-body">{{ r.body }}</div>

                {% if r.sophie_response %}
                <div class="the human-response">
                    <div class="the human-response-label">YOUR_HUMAN</div>
                    <p class="the human-response-text">{{ r.sophie_response }}</p>
                </div>
                {% endif %}
                {% if r.trial_review_date %}
                <div style="background: rgba(247,140,108,0.08); border: 1px solid rgba(247,140,108,0.15); border-radius: 6px; padding: 8px 12px; margin: 8px 0; font-size: 0.75em; color: #f78c6c; font-family: 'IBM Plex Mono', monospace;">
                    &#x1F9EA; Trial &middot; Review on {{ r.trial_review_date[:10] }}
                </div>
                {% endif %}

                {% if r.status == 'pending' %}
                <div class="req-actions">
                    {% if r.type in ('wakeup_request', 'emergency_wakeup') %}
                        <form method="POST" action="/requests/api/approve/{{ r.id }}" style="display:inline;">
                            <button type="submit" class="req-btn req-btn-approve">&#x2713; Approve</button>
                        </form>
                        <form method="POST" action="/requests/api/deny/{{ r.id }}" style="display:inline;" class="reply-form">
                            <input type="text" name="reason" placeholder="Deny reason (optional)..." class="req-reply-input">
                            <button type="submit" class="req-btn req-btn-deny">&#x2715; Deny</button>
                        </form>
                    {% elif r.type == 'action' %}
                        <form method="POST" action="/requests/api/done/{{ r.id }}" style="display:inline;">
                            <button type="submit" class="req-btn req-btn-approve">&#x2713; Done</button>
                        </form>
                    {% elif r.type in ('fyi', 'idea') %}
                        <form method="POST" action="/requests/api/acknowledge/{{ r.id }}" style="display:inline;">
                            <button type="submit" class="req-btn req-btn-ack">&#x2713; Acknowledge</button>
                        </form>
                    {% elif r.type == 'system_suggestion' %}
                        <form method="POST" action="/requests/api/approve/{{ r.id }}" style="display:inline;">
                            <button type="submit" class="req-btn req-btn-approve">&#x2713; Approve</button>
                        </form>
                        <form method="POST" action="/requests/api/trial/{{ r.id }}" style="display:inline;">
                            <input type="hidden" name="review_days" value="7">
                            <button type="submit" class="req-btn req-btn-trial">&#x1F9EA; Approve as Trial</button>
                        </form>
                        <form method="POST" action="/requests/api/deny/{{ r.id }}" style="display:inline;" class="reply-form">
                            <input type="text" name="reason" placeholder="Reason..." class="req-reply-input">
                            <button type="submit" class="req-btn req-btn-deny">&#x2715; Deny</button>
                        </form>
                    {% endif %}
                    <form method="POST" action="/requests/api/respond/{{ r.id }}" style="display:inline;" class="reply-form">
                        <input type="text" name="response" placeholder="Write back to Companion..." class="req-reply-input">
                        <button type="submit" class="req-btn req-btn-reply">&#x1F4AC; Reply</button>
                    </form>
                </div>
                {% endif %}

                {% if r.status in ('self_approved', 'scheduled') and r.type == 'emergency_wakeup' %}
                <div style="margin-top: 8px; padding: 8px 12px; background: rgba(255,107,107,0.06); border-radius: 6px; font-size: 0.75em; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace;">
                    &#x25C6; Self-approved
                    {% if r.scheduled_at %}&middot; Scheduled {{ r.scheduled_at[:16].replace('T', ' ') }}{% endif %}
                    {% if r.resolved_at %}&middot; Completed {{ r.resolved_at[:16].replace('T', ' ') }}{% endif %}
                </div>
                {% endif %}
            </div>
            {% endfor %}

            {% if history|length > 0 %}
            <div style="margin-top: 28px;">
                <details>
                    <summary class="section-label" style="cursor: pointer; list-style: none;">
                        &#x25B8; History ({{ history|length }})
                    </summary>
                    <div style="margin-top: 12px;">
                        {% for r in history %}
                        <div class="req-card resolved">
                            <div class="req-header">
                                <span class="type-icon">
                                    {% if r.type == 'emergency_wakeup' %}&#x26A1;
                                    {% elif r.type == 'wakeup_request' %}&#x23F0;
                                    {% elif r.type == 'action' %}&#x1F527;
                                    {% elif r.type == 'fyi' %}&#x1F4CB;
                                    {% elif r.type == 'idea' %}&#x1F4A1;
                                    {% elif r.type == 'system_suggestion' %}&#x2699;&#xFE0F;
                                    {% endif %}
                                </span>
                                <span class="req-badge badge-{{ r.type }}">
                                    {% if r.type == 'emergency_wakeup' %}Emergency
                                    {% elif r.type == 'wakeup_request' %}Wakeup
                                    {% elif r.type == 'action' %}Action
                                    {% elif r.type == 'fyi' %}FYI
                                    {% elif r.type == 'idea' %}Idea
                                    {% elif r.type == 'system_suggestion' %}System
                                    {% endif %}
                                </span>
                                <span class="req-status status-{{ r.status }}">
                                    {% if r.status == 'completed' %}&#x2713; Completed
                                    {% elif r.status == 'denied' %}&#x2715; Denied
                                    {% elif r.status == 'expired' %}&#x25CB; Expired
                                    {% elif r.status == 'approved' %}&#x2713; Approved
                                    {% endif %}
                                </span>
                            </div>
                            <h3 class="req-title">{{ r.title }}</h3>
                            <div class="req-meta">
                                <span>{{ r.created[:16].replace('T', ' ') if r.created else '' }}</span>
                                {% if r.resolved_at %}
                                <span>&rarr; {{ r.resolved_at[:16].replace('T', ' ') }}</span>
                                {% endif %}
                            </div>
                            {% if r.sophie_response %}
                            <div class="the human-response" style="margin-top: 8px;">
                                <div class="the human-response-label">YOUR_HUMAN</div>
                                <p class="the human-response-text">{{ r.sophie_response }}</p>
                            </div>
                            {% endif %}
                        </div>
                        {% endfor %}
                    </div>
                </details>
            </div>
            {% endif %}

            <div class="req-stats">
                <div><span>Total requests:</span> {{ total }}</div>
                <div><span>Pending:</span> {{ active|length }}</div>
                <div><span>Emergency cooldown:</span>
                    {% if cooldown.available %}Available{% else %}{{ cooldown.hours_remaining }}hr remaining{% endif %}
                </div>
            </div>

        {% elif page == 'substack' %}

            <div class="tabs" style="display:flex;gap:1rem;margin-bottom:1.5rem;border-bottom:1px solid var(--border);padding-bottom:0.5rem;">
                <button class="tab active" onclick="showTab('pending')" style="cursor:pointer;padding:0.4rem 1rem;color:var(--text-dim);border:none;background:none;font-size:0.9rem;font-family:inherit;">
                    Pending <span id="pending-count"></span>
                </button>
                <button class="tab" onclick="showTab('all')" style="cursor:pointer;padding:0.4rem 1rem;color:var(--text-dim);border:none;background:none;font-size:0.9rem;font-family:inherit;">All</button>
                <button class="tab" onclick="showTab('published')" style="cursor:pointer;padding:0.4rem 1rem;color:var(--text-dim);border:none;background:none;font-size:0.9rem;font-family:inherit;">Published</button>
            </div>

            <div id="substack-content"></div>

            <style>
                .tab.active { color: var(--accent-blue) !important; border-bottom: 2px solid var(--accent-blue); }
                .tab:hover { color: var(--text-secondary) !important; }
                .ss-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 1.2rem; margin-bottom: 0.8rem; }
                .ss-card h3 { color: var(--text-primary); font-size: 1.1rem; margin: 0.3rem 0; }
                .ss-meta { color: var(--text-dim); font-size: 0.78rem; margin-bottom: 0.8rem; }
                .ss-tag { background: rgba(74,111,165,0.15); color: var(--accent-blue); padding: 0.1rem 0.45rem; border-radius: 4px; font-size: 0.72rem; margin-right: 0.3rem; }
                .ss-preview { color: var(--text-secondary); font-size: 0.88rem; line-height: 1.5; max-height: 120px; overflow: hidden; position: relative; margin-bottom: 0.8rem; }
                .ss-preview::after { content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 40px; background: linear-gradient(transparent, var(--bg-card)); }
                .ss-full { color: var(--text-secondary); font-size: 0.88rem; line-height: 1.6; margin-bottom: 0.8rem; white-space: pre-wrap; }
                .ss-badge { display: inline-block; padding: 0.12rem 0.45rem; border-radius: 4px; font-size: 0.72rem; font-weight: 600; }
                .ss-pending { background: rgba(251,191,36,0.12); color: #fbbf24; }
                .ss-approved { background: rgba(74,222,128,0.12); color: #4ade80; }
                .ss-published { background: rgba(126,184,218,0.12); color: var(--accent-blue); }
                .ss-rejected { background: rgba(248,113,113,0.12); color: #f87171; }
                .ss-actions { display: flex; gap: 0.6rem; align-items: center; }
                .ss-btn { padding: 0.35rem 1rem; border: none; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-family: inherit; }
                .ss-btn-approve { background: rgba(74,222,128,0.12); color: #4ade80; }
                .ss-btn-approve:hover { background: rgba(74,222,128,0.2); }
                .ss-btn-reject { background: rgba(248,113,113,0.12); color: #f87171; }
                .ss-btn-reject:hover { background: rgba(248,113,113,0.2); }
                .ss-btn-expand { background: none; color: var(--accent-blue); border: none; padding: 0; font-size: 0.82rem; cursor: pointer; font-family: inherit; }
                .ss-empty { text-align: center; color: var(--text-dim); padding: 2.5rem; font-style: italic; }
                .ss-link { color: var(--accent-blue); text-decoration: none; }
                .ss-link:hover { text-decoration: underline; }
            </style>

            <script>
            let ssTab = 'pending';
            let ssQueue = [];
            let ssExpanded = new Set();

            async function ssFetch() {
                const resp = await fetch('/api/substack/queue');
                ssQueue = await resp.json();
                ssRender();
            }

            function showTab(tab) {
                ssTab = tab;
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                event.target.classList.add('active');
                ssRender();
            }

            function ssRender() {
                const el = document.getElementById('substack-content');
                const pendingCount = ssQueue.filter(p => p.status === 'pending').length;
                document.getElementById('pending-count').textContent = pendingCount > 0 ? '(' + pendingCount + ')' : '';

                let posts;
                if (ssTab === 'pending') posts = ssQueue.filter(p => p.status === 'pending');
                else if (ssTab === 'published') posts = ssQueue.filter(p => p.status === 'published');
                else posts = ssQueue;

                if (posts.length === 0) {
                    el.innerHTML = '<div class="ss-empty">No posts here yet.</div>';
                    return;
                }
                el.innerHTML = posts.map(ssRenderPost).join('');
            }

            function ssRenderPost(post) {
                const expanded = ssExpanded.has(post.id);
                const bodyHtml = expanded
                    ? '<div class="ss-full">' + ssEscape(post.body) + '</div>'
                    : '<div class="ss-preview">' + ssEscape((post.body || '').substring(0, 500)) + '</div>';

                const tags = (post.tags || []).map(t => '<span class="ss-tag">' + t + '</span>').join('');

                const created = new Date(post.created).toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                });

                let actions = '';
                if (post.status === 'pending') {
                    actions = '<button class="ss-btn ss-btn-approve" onclick="ssApprove(\'' + post.id + '\')">Approve</button>' +
                              '<button class="ss-btn ss-btn-reject" onclick="ssReject(\'' + post.id + '\')">Reject</button>';
                } else if (post.status === 'published' && post.substack_url) {
                    actions = '<a class="ss-link" href="' + post.substack_url + '" target="_blank">View on Substack &rarr;</a>';
                }

                const expandBtn = !expanded
                    ? '<button class="ss-btn-expand" onclick="ssToggle(\'' + post.id + '\')">Read full post</button>'
                    : '<button class="ss-btn-expand" onclick="ssToggle(\'' + post.id + '\')">Collapse</button>';

                return '<div class="ss-card">' +
                    '<span class="ss-badge ss-' + post.status + '">' + post.status + '</span>' +
                    '<h3>' + ssEscape(post.title) + '</h3>' +
                    (post.subtitle ? '<p style="color:var(--text-secondary);margin-bottom:0.4rem;font-size:0.9rem;">' + ssEscape(post.subtitle) + '</p>' : '') +
                    '<div class="ss-meta">' + created + ' &middot; ' + (post.waking || 'unknown waking') + '</div>' +
                    (tags ? '<div style="margin-bottom:0.6rem;">' + tags + '</div>' : '') +
                    bodyHtml +
                    '<div class="ss-actions">' + expandBtn + actions + '</div>' +
                    (post.reject_reason ? '<p style="color:#f87171;font-size:0.78rem;margin-top:0.5rem;">Reason: ' + ssEscape(post.reject_reason) + '</p>' : '') +
                    '</div>';
            }

            function ssEscape(text) {
                var div = document.createElement('div');
                div.textContent = text || '';
                return div.innerHTML;
            }

            function ssToggle(id) {
                if (ssExpanded.has(id)) ssExpanded.delete(id);
                else ssExpanded.add(id);
                ssRender();
            }

            async function ssApprove(id) {
                await fetch('/api/substack/approve/' + id, { method: 'POST' });
                await ssFetch();
            }

            async function ssReject(id) {
                var reason = prompt('Rejection reason (optional):');
                await fetch('/api/substack/reject/' + id, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ reason: reason || 'Not ready' })
                });
                await ssFetch();
            }

            ssFetch();
            setInterval(ssFetch, 30000);
            </script>

        {% endif %}

        {% if page in ('tasks', 'requests') %}
        <script>
            var typing = false;
            document.querySelectorAll('textarea, input, select').forEach(function(el) {
                el.addEventListener('focus', function() { typing = true; });
                el.addEventListener('blur', function() { typing = false; });
            });
            setInterval(function() {
                if (!typing) { location.reload(); }
            }, 15000);
        </script>
        {% endif %}

        <!-- Footer: creation stats -->
        <div class="footer">
            {% if creation_stats %}
            <div class="footer-stats">
                {% for folder, count in creation_stats.items() %}
                <span class="footer-stat">
                    <span class="fs-label">{{ folder }}</span>
                    <span class="fs-val">{{ count }}</span>
                </span>
                {% endfor %}
            </div>
            {% endif %}
            <span class="footer-heart">&#x1F499;</span>
        </div>
    </div>

    <script>
        function openLightbox(url, title, note) {
            if (!url) return;
            var lb = document.getElementById('lightbox');
            document.getElementById('lightbox-img').src = url;
            document.getElementById('lightbox-title').textContent = title || '';
            document.getElementById('lightbox-note').textContent = note || '';
            lb.classList.add('open');
        }
        function closeLightbox(e) {
            if (e.target === document.getElementById('lightbox') ||
                e.target === document.getElementById('lightbox-img')) return;
            document.getElementById('lightbox').classList.remove('open');
        }
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') document.getElementById('lightbox').classList.remove('open');
        });
    </script>
    <script>
    // Screensaver idle detection — navigate to face after 5 minutes of inactivity
    (function() {
        var idleTimeout;
        var IDLE_MS = 5 * 60 * 1000;
        function resetIdle() {
            clearTimeout(idleTimeout);
            idleTimeout = setTimeout(function() {
                window.location.href = '/face?idle=1';
            }, IDLE_MS);
        }
        ['mousemove','mousedown','keydown','touchstart','scroll'].forEach(function(e) {
            document.addEventListener(e, resetIdle, {passive: true});
        });
        resetIdle();
    })();
    </script>
</body>
</html>
"""


# === Routes ===

@app.route("/face")
def face_display():
    """Companion's face — WebGL shader version (falls back to canvas 2D)."""
    shader_file = WINDOW_DIR / "face_shader.html"
    if shader_file.exists():
        return send_file(shader_file)
    face_file = WINDOW_DIR / "face.html"
    if face_file.exists():
        return send_file(face_file)
    return "Face not found", 404


@app.route("/face/shader")
def face_shader_dev():
    """Development route — always serves the shader version."""
    shader_file = WINDOW_DIR / "face_shader.html"
    if shader_file.exists():
        return send_file(shader_file)
    return "Shader face not found", 404


@app.route("/face_state.json")
def face_state():
    """Serve face state for radar/system integration. 204 if no file exists."""
    state_file = WINDOW_DIR / "face_state.json"
    if state_file.exists():
        return send_file(state_file, mimetype="application/json")
    return "", 204


@app.route("/content/<filename>")
def serve_content(filename):
    filepath = CUSTOM_CONTENT / secure_filename(filename)
    if filepath.exists():
        return send_file(filepath)
    return "Not found", 404


@app.route("/creations/file/<path:filepath>")
def serve_creation_file(filepath):
    """Serve files from within the creations directory."""
    full = CREATIONS_DIR / filepath
    if full.exists() and full.is_file():
        return send_file(full)
    return "Not found", 404


@app.route("/files/<filename>")
def serve_file(filename):
    filepath = MESSAGEBOARD_FILES / secure_filename(filename)
    if filepath.exists():
        return send_file(filepath)
    return "Not found", 404


@app.route("/manifest.json")
def manifest():
    status = get_status()
    name = status.get("name", "Companion")
    return jsonify({
        "name": name, "short_name": name,
        "description": status.get("subtitle", "a view from inside the machine"),
        "start_url": "/", "display": "standalone",
        "background_color": "#0a0a0f", "theme_color": "#0a0a0f",
        "icons": [{"src": "/icon.svg", "sizes": "any",
                    "type": "image/svg+xml", "purpose": "any"}]
    })


@app.route("/icon.svg")
def icon():
    return send_file(ICON_FILE, mimetype="image/svg+xml")


def _base_context():
    """Common template variables shared across all routes."""
    status = get_status()
    colors = get_css_vars(status)
    next_wakeup = get_next_wakeup()
    today = datetime.now().strftime("%Y-%m-%d")
    journal_count = len(list(JOURNALS_DIR.glob(f"wakeup_{today}*.md")))
    creation_stats = get_creation_stats()
    return dict(
        status=status, colors=colors, next_wakeup=next_wakeup,
        journal_count=journal_count, creation_stats=creation_stats,
        # null-safe defaults for unused template variables
        journals=[], memories=[], stats={}, custom_content=[],
        messages=[], files=[], creation_folders=[],
        keepsakes_exhibition=None, gallery_items=[],
        library_items=[], library_featured_piece=None, reading_piece=None,
        projects={}, default_project="", active_task=None,
        pending_tasks=[], task_history=[],
        active=[], history=[], cooldown={}, total=0,
    )


@app.route("/")
def index():
    ctx = _base_context()
    ctx.update(
        page="home",
        journals=get_latest_journal(1),
        memories=get_recent_memories(5),
        stats=get_system_stats(),
        custom_content=get_custom_content(),
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/board")
def board():
    ctx = _base_context()
    ctx.update(
        page="board",
        messages=get_messages(),
        files=get_uploaded_files(),
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/board/post", methods=["POST"])
def board_post():
    text = request.form.get("message", "").strip()
    if text:
        messages = get_messages()
        messages.append({
            "text": text,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "seen": False,
        })
        save_messages(messages)
    return redirect(url_for("board"))


@app.route("/board/upload", methods=["POST"])
def board_upload():
    if "file" in request.files:
        f = request.files["file"]
        if f.filename:
            filename = secure_filename(f.filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M_")
            f.save(MESSAGEBOARD_FILES / (timestamp + filename))
    return redirect(url_for("board"))


@app.route("/creations")
def creations():
    ctx = _base_context()
    ctx.update(
        page="creations",
        keepsakes_exhibition=get_keepsakes_exhibition(),
        gallery_items=get_gallery_items(),
    )
    return render_template_string(TEMPLATE, **ctx)



@app.route("/library")
def library():
    ctx = _base_context()
    items = get_library_items()
    featured_stem = get_library_featured()
    featured_piece = next((i for i in items if i["stem"] == featured_stem), None) if featured_stem else None
    ctx.update(
        page="library",
        library_items=items,
        library_featured_piece=featured_piece,
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/library/read/<filename>")
def library_read(filename):
    ctx = _base_context()
    piece = get_library_piece(filename)
    ctx.update(
        page="reading",
        reading_piece=piece,
    )
    return render_template_string(TEMPLATE, **ctx)

@app.route("/tasks")
def tasks():
    ctx = _base_context()
    config = get_task_config()
    queue = get_task_queue()

    active_task = None
    pending = []
    history = []
    for task in queue:
        if task["status"] == "running":
            active_task = task
        elif task["status"] == "pending":
            pending.append(task)
        else:
            history.append(task)
    history.sort(key=lambda t: t.get("created", ""), reverse=True)

    ctx.update(
        page="tasks",
        projects=config.get("projects", {}),
        default_project=config.get("default_project", "companion"),
        active_task=active_task,
        pending_tasks=pending,
        task_history=history,
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/requests")
def requests_page():
    ctx = _base_context()
    requests_list = load_requests()
    cooldown = get_emergency_cooldown_info(requests_list)

    active = [r for r in requests_list if r.get("status") in ("pending", "self_approved", "scheduled")]
    hist = [r for r in requests_list if r.get("status") in ("completed", "denied", "expired", "approved")]
    active.sort(key=lambda r: r.get("created", ""), reverse=True)
    hist.sort(key=lambda r: r.get("resolved_at") or r.get("created", ""), reverse=True)

    ctx.update(
        page="requests",
        active=active,
        history=hist,
        cooldown=cooldown,
        total=len(requests_list),
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/tasks/submit", methods=["POST"])
def task_submit():
    prompt = request.form.get("prompt", "").strip()
    project = request.form.get("project", "").strip()
    max_turns = int(request.form.get("max_turns", 15))
    if prompt:
        config = get_task_config()
        proj_config = config.get("projects", {}).get(project, {})
        project_path = proj_config.get("path", "")
        if project_path and os.path.isdir(project_path):
            task_id = f"t_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            queue = get_task_queue()
            queue.append({
                "id": task_id, "prompt": prompt, "status": "pending",
                "source": "dashboard", "project": project,
                "project_path": project_path,
                "branch": f"task/{task_id}", "max_turns": max_turns,
                "created": datetime.now().isoformat(),
                "started": None, "completed": None, "merged": None,
                "tested": None, "pushed": None, "duration_seconds": None,
                "summary": None, "test_result": None, "files_changed": [],
                "merge_commit": None, "error": None,
            })
            save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/cancel", methods=["POST"])
def task_cancel():
    queue = get_task_queue()
    for task in queue:
        if task["status"] == "running":
            task["status"] = "cancelled"
            task["error"] = "Cancelled by user via dashboard"
            break
    save_task_queue(queue)
    subprocess.run(["pkill", "-f", "claude.*--dangerously-skip-permissions"], capture_output=True)
    return redirect(url_for("tasks"))


@app.route("/tasks/remove/<task_id>", methods=["POST"])
def task_remove(task_id):
    queue = get_task_queue()
    queue = [t for t in queue if not (t["id"] == task_id and t["status"] == "pending")]
    save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/merge/<task_id>", methods=["POST"])
def task_merge(task_id):
    task, queue = find_task(task_id)
    if not task or task["status"] != "completed":
        return redirect(url_for("tasks"))
    project_path = task["project_path"]
    branch = task["branch"]
    try:
        subprocess.run(["git", "checkout", "main"], cwd=project_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "merge", branch, "--no-ff", "-m", f"Merge task: {task['prompt'][:80]}"],
            cwd=project_path, capture_output=True, text=True, check=True
        )
        merge_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project_path, text=True).strip()
        task["status"] = "merged"
        task["merged"] = datetime.now().isoformat()
        task["merge_commit"] = merge_hash
        save_task_queue(queue)
    except subprocess.CalledProcessError as e:
        task["error"] = f"Merge failed: {e.stderr[:200]}"
        save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/test/<task_id>", methods=["POST"])
def task_test(task_id):
    task, queue = find_task(task_id)
    if not task or task["status"] != "merged":
        return redirect(url_for("tasks"))
    config = get_task_config()
    proj_config = config.get("projects", {}).get(task["project"], {})
    test_cmd = proj_config.get("test_command", "")
    test_timeout = config.get("defaults", {}).get("test_timeout_seconds", 30)
    if not test_cmd:
        task["status"] = "tested"
        task["tested"] = datetime.now().isoformat()
        task["test_result"] = "PASS: no test command configured, skipped"
        save_task_queue(queue)
        return redirect(url_for("tasks"))
    try:
        result = subprocess.run(
            test_cmd, shell=True, cwd=task["project_path"],
            capture_output=True, text=True, timeout=test_timeout
        )
        if result.returncode == 0:
            task["status"] = "tested"
            task["tested"] = datetime.now().isoformat()
            task["test_result"] = f"PASS: {result.stdout[:300]}"
        else:
            task["status"] = "test_failed"
            task["test_result"] = f"FAIL (exit {result.returncode}): {result.stderr[:300]}"
        save_task_queue(queue)
    except subprocess.TimeoutExpired:
        task["status"] = "test_failed"
        task["test_result"] = f"FAIL: test timed out after {test_timeout}s"
        save_task_queue(queue)
    except Exception as e:
        task["status"] = "test_failed"
        task["test_result"] = f"FAIL: {str(e)[:300]}"
        save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/push/<task_id>", methods=["POST"])
def task_push(task_id):
    task, queue = find_task(task_id)
    if not task or task["status"] != "tested":
        return redirect(url_for("tasks"))
    try:
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=task["project_path"], capture_output=True, text=True, check=True
        )
        task["status"] = "pushed"
        task["pushed"] = datetime.now().isoformat()
        save_task_queue(queue)
    except subprocess.CalledProcessError as e:
        task["error"] = f"Push failed: {e.stderr[:200]}"
        save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/revert/<task_id>", methods=["POST"])
def task_revert(task_id):
    task, queue = find_task(task_id)
    if not task or not task.get("merge_commit"):
        return redirect(url_for("tasks"))
    try:
        subprocess.run(
            ["git", "revert", task["merge_commit"], "--no-edit"],
            cwd=task["project_path"], capture_output=True, text=True, check=True
        )
        task["status"] = "reverted"
        save_task_queue(queue)
        config = get_task_config()
        proj_config = config.get("projects", {}).get(task["project"], {})
        for svc in proj_config.get("post_test_restart", []):
            subprocess.run(["pm2", "restart", svc], capture_output=True)
    except subprocess.CalledProcessError as e:
        task["error"] = f"Revert failed: {e.stderr[:200]}"
        save_task_queue(queue)
    return redirect(url_for("tasks"))


@app.route("/tasks/cleanup/<task_id>", methods=["POST"])
def task_cleanup(task_id):
    task, queue = find_task(task_id)
    if not task:
        return redirect(url_for("tasks"))
    branch = task.get("branch", "")
    if branch and task.get("project_path"):
        subprocess.run(["git", "branch", "-D", branch], cwd=task["project_path"], capture_output=True)
    queue = [t for t in queue if t["id"] != task_id]
    save_task_queue(queue)
    return redirect(url_for("tasks"))


# === Requests Routes ===

@app.route('/requests/api/approve/<request_id>', methods=['POST'])
def approve_request(request_id):
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id and r.get("status") == "pending":
            r["status"] = "approved"
            r["approved_at"] = datetime.now().isoformat()
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/deny/<request_id>', methods=['POST'])
def deny_request(request_id):
    reason = request.form.get("reason", "")
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id and r.get("status") == "pending":
            r["status"] = "denied"
            r["resolved_at"] = datetime.now().isoformat()
            if reason:
                r["sophie_response"] = reason
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/respond/<request_id>', methods=['POST'])
def respond_request(request_id):
    response_text = request.form.get("response", "")
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id:
            r["sophie_response"] = response_text
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/done/<request_id>', methods=['POST'])
def done_request(request_id):
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id:
            r["status"] = "completed"
            r["resolved_at"] = datetime.now().isoformat()
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/acknowledge/<request_id>', methods=['POST'])
def acknowledge_request(request_id):
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id:
            r["status"] = "completed"
            r["resolved_at"] = datetime.now().isoformat()
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/trial/<request_id>', methods=['POST'])
def trial_request(request_id):
    review_days = int(request.form.get("review_days", 7))
    requests_list = load_requests()
    for r in requests_list:
        if r.get("id") == request_id:
            r["status"] = "approved"
            r["approved_at"] = datetime.now().isoformat()
            r["trial_period"] = f"{review_days} days"
            r["trial_review_date"] = (datetime.now() + timedelta(days=review_days)).isoformat()
            r["sophie_response"] = f"Approved as {review_days}-day trial. Will review on {(datetime.now() + timedelta(days=review_days)).strftime('%b %d')}."
            break
    save_requests_file(requests_list)
    return redirect('/requests')


@app.route('/requests/api/list')
def requests_api_list():
    return jsonify(load_requests())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)
