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
import shlex
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from flask import Flask, render_template_string, send_file, jsonify, request, redirect, url_for
from werkzeug.utils import secure_filename
import markdown
import sys

# === CONFIGURE THESE PATHS ===
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
COMPANION_HOME = Path(os.environ.get("COMPANION_HOME", REPO_ROOT)).expanduser().resolve()
# =============================

SCRIPTS_DIR = COMPANION_HOME / "scripts"
if SCRIPTS_DIR.exists():
    sys.path.insert(0, str(SCRIPTS_DIR))

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    JsonMemoryStore,
    SemanticFirstMemoryStore,
    approve_memory_review_decision,
    create_llm_client,
    load_local_secrets,
    load_memory_review_queue,
    reject_memory_review_decision,
)
from companion_core.dialogue import _clean_visible_text

app = Flask(__name__)


def register_optional_blueprint(module_name, blueprint_name):
    """Register optional dashboard extensions without breaking core Window import."""
    try:
        module = __import__(module_name, fromlist=[blueprint_name])
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return
        raise
    blueprint = getattr(module, blueprint_name, None)
    if blueprint is not None:
        app.register_blueprint(blueprint)


register_optional_blueprint("substack_window", "substack_bp")
register_optional_blueprint("date_night_window", "date_night_bp")
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload

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
CONVERSATIONS_DIR = COMPANION_HOME / "conversations"
DEFAULT_CHAT_ERROR = "I could not send that chat turn yet. Your text is still here."

# Ensure all directories exist
for d in [WINDOW_DIR, CUSTOM_CONTENT, MESSAGEBOARD_DIR, MESSAGEBOARD_FILES,
          CREATIONS_DIR, KEEPSAKES_DIR,
          CREATIONS_DIR / "code", CREATIONS_DIR / "art",
          CREATIONS_DIR / "writing", CREATIONS_DIR / "experiments",
          TASKS_DIR, TASKS_DIR / "logs", CONVERSATIONS_DIR,
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


def safe_child_path(base_dir, child_path):
    """Return a resolved child path only when it stays inside base_dir."""
    try:
        base = Path(base_dir).resolve()
        candidate = (base / child_path).resolve()
        candidate.relative_to(base)
    except (OSError, ValueError):
        return None
    return candidate


def build_test_commands(project_config):
    commands = project_config.get("test_commands")
    if commands is None:
        command = project_config.get("test_command")
        if not command:
            return []
        if not isinstance(command, str):
            raise ValueError("test_command must be a string")
        if any(token in command for token in ("&&", "||", ";", "|", "`", "$(", "<", ">")):
            raise ValueError("unsafe shell syntax in test_command")
        commands = [shlex.split(command)]

    if not isinstance(commands, list):
        raise ValueError("test_commands must be a list")

    normalized = []
    for command in commands:
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError("test_commands entries must be non-empty string arrays")
        normalized.append(command)
    return normalized


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _load_wake_events():
    events_file = COMPANION_HOME / "life-loop" / "wake_events.jsonl"
    events = []
    try:
        for line in events_file.read_text().splitlines():
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return events


def _latest_wake_event():
    events = _load_wake_events()
    return events[-1] if events else {}


def _report(name):
    return _load_json(COMPANION_HOME / "life-loop" / name, default={}) or {}


class _StaticChatClient:
    def __init__(self, response: str):
        self.response = response

    def generate(self, prompt, context):
        return self.response


def _load_jsonl(path, limit=None):
    rows = []
    try:
        lines = Path(path).read_text().splitlines()
    except (FileNotFoundError, OSError):
        return rows
    selected = lines[-limit:] if limit else lines
    for line in selected:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def get_chat_state(conversation_id=None, limit=40, error=None, preserved_input=""):
    paths = CompanionPaths.from_env(COMPANION_HOME)
    transcripts = sorted(paths.conversations_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if paths.conversations_dir.exists() else []
    transcript_path = None
    if conversation_id:
        candidate = DialogueRunner(paths)._transcript_path(conversation_id)
        if candidate.exists():
            transcript_path = candidate
    elif transcripts:
        transcript_path = transcripts[0]
    transcript = _load_jsonl(transcript_path, limit=limit) if transcript_path else []
    active_conversation_id = conversation_id or (transcript[-1].get("conversation_id") if transcript else "")
    proposals = _load_jsonl(paths.memory_proposals_file)
    proposal_count = sum(
        1
        for proposal in proposals
        if not active_conversation_id or proposal.get("conversation_id") == active_conversation_id
    )
    return {
        "conversation_id": active_conversation_id,
        "transcript_path": str(transcript_path) if transcript_path else "",
        "transcript": transcript,
        "provider": os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"),
        "memory_mode": os.environ.get("COMPANION_MEMORY_MODE", "json"),
        "memory_proposal_count": proposal_count,
        "error": error,
        "preserved_input": preserved_input,
    }


def get_memory_review_state(error=None):
    paths = CompanionPaths.from_env(COMPANION_HOME)
    try:
        queue = load_memory_review_queue(paths)
    except Exception as exc:  # noqa: BLE001 - dashboard keeps review errors visible.
        queue = {
            "pending": [],
            "reviewed": [],
            "actions": [],
            "counts": {
                "decisions": 0,
                "reviewable": 0,
                "pending": 0,
                "reviewed": 0,
                "actions": 0,
            },
        }
        error = error or f"{type(exc).__name__}: {_clean_visible_text(str(exc))}"
    queue["error"] = error
    return queue


def _chat_runner():
    paths = CompanionPaths.from_env(COMPANION_HOME)
    paths.ensure_runtime_dirs()
    load_local_secrets(paths)
    provider = os.environ.get("COMPANION_LLM_PROVIDER", "deepseek")
    memory_mode = os.environ.get("COMPANION_MEMORY_MODE", "json")
    fake_response = os.environ.get("COMPANION_CHAT_FAKE_RESPONSE")
    if fake_response is not None:
        llm_client = _StaticChatClient(fake_response)
        provider = "fake"
    else:
        llm_client = create_llm_client(
            provider,
            claude_bin=os.environ.get("COMPANION_CLAUDE_BIN", "claude"),
            timeout_seconds=int(os.environ.get("COMPANION_CHAT_TIMEOUT", "300")),
            model=os.environ.get("COMPANION_LLM_MODEL"),
            base_url=os.environ.get("COMPANION_LLM_BASE_URL"),
            api_key_env=os.environ.get("COMPANION_LLM_API_KEY_ENV", "COMPANION_LLM_API_KEY"),
        )
    memory_store = SemanticFirstMemoryStore(paths.memory_store) if memory_mode == "dual" else JsonMemoryStore(paths.memory_store)
    return DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store), provider, memory_mode


def _wants_json_response():
    return request.is_json or "application/json" in (request.headers.get("Accept") or "")


def _review_request_value(name, default=""):
    data = request.get_json(silent=True) if request.is_json else {}
    if isinstance(data, dict) and data.get(name) is not None:
        return data.get(name)
    return request.form.get(name, default)


def _kv(key, value):
    return f"{escape(str(key))}={escape(str(value))}"


def _event_life_lines(event):
    if not event:
        return ["No wake events captured."]

    lines = [
        escape(str(event.get("id", ""))),
        escape(str(event.get("status", ""))),
        escape(str(event.get("trigger", ""))),
        f"last provider {escape(str(event.get('provider', '')))}",
    ]

    quality = event.get("quality") if isinstance(event.get("quality"), dict) else {}
    warnings = quality.get("warnings") if isinstance(quality.get("warnings"), list) else []
    gate = event.get("quality_gate") if isinstance(event.get("quality_gate"), dict) else {}
    if warnings or gate:
        decision = gate.get("decision", "unknown")
        context = "context ready" if gate.get("context_eligible") else "context blocked"
        lines.append(f"quality warnings gate={escape(str(decision))} {context}")
        lines.extend(escape(str(warning)) for warning in warnings)

    grounding = event.get("grounding") if isinstance(event.get("grounding"), dict) else {}
    if grounding:
        lines.append(
            "grounding "
            + _kv("supported", grounding.get("supported", 0))
            + " "
            + _kv("unsupported", grounding.get("unsupported", 0))
        )
        lines.extend(escape(str(warning)) for warning in grounding.get("warnings", []) or [])

    repair = event.get("repair") if isinstance(event.get("repair"), dict) else {}
    if repair:
        repair_state = "succeeded" if repair.get("succeeded") else "failed" if repair.get("attempted") else "skipped"
        lines.append(f"repair={repair_state}")

    output_audit = event.get("output_audit") if isinstance(event.get("output_audit"), dict) else {}
    if output_audit:
        lines.append("output_audit=" + escape(str(output_audit.get("raw_output_storage", "unknown"))))
        for key in ("initial", "final"):
            section = output_audit.get(key) if isinstance(output_audit.get(key), dict) else {}
            if section.get("content_hash"):
                lines.append(escape(str(section["content_hash"])))

    memory_policy = event.get("memory_policy") if isinstance(event.get("memory_policy"), dict) else {}
    if memory_policy:
        lines.append("memory_policy " + _kv("accepted", memory_policy.get("accepted", 0)))

    shadow = event.get("semantic_shadow") if isinstance(event.get("semantic_shadow"), dict) else {}
    if shadow:
        attempted = shadow.get("attempted", 0)
        succeeded = shadow.get("succeeded", 0)
        lines.append("semantic shadow")
        lines.append(f"semantic={escape(str(succeeded))}/{escape(str(attempted))}")
        lines.append("semantic_shadow " + _kv("enabled", shadow.get("enabled")))

    return lines


def _report_lines(title, report):
    if not report:
        return [title, "missing"]
    lines = [title]
    for key in ("milestone", "recommendation", "saved_at"):
        if report.get(key) is not None:
            lines.append(escape(str(report[key])))
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    if profile.get("name"):
        lines.append(escape(str(profile["name"])))
    for stage in report.get("stages", []) or []:
        if isinstance(stage, dict) and stage.get("name"):
            lines.append(f"{escape(str(stage['name']))}={escape(str(stage.get('status', '')))}")
    return lines


def _m4_wake_lines(report):
    if not report:
        return []
    lines = ["failure audit"]
    latest = report.get("latest_event") if isinstance(report.get("latest_event"), dict) else {}
    if latest.get("id"):
        lines.append("latest_m4_wake=" + escape(str(latest["id"])))
    audit = report.get("failure_audit") if isinstance(report.get("failure_audit"), dict) else {}
    if audit.get("category"):
        lines.append(escape(str(audit["category"])))
    for attempt in report.get("attempts", []) or []:
        error = attempt.get("error") if isinstance(attempt, dict) and isinstance(attempt.get("error"), dict) else {}
        if error.get("message"):
            lines.append("retry_reason=" + escape(str(error["message"])))
    return lines


def _m5_quality_lines(report):
    lines = ["M5 Quality"]
    if not report:
        lines.append("No M5 quality report captured.")
        return lines
    lines.append(escape(str(report.get("recommendation", ""))))
    sample = report.get("sample") if isinstance(report.get("sample"), dict) else {}
    if sample:
        lines.append("accepted")
        lines.append(_kv("accepted_events", sample.get("accepted_events", 0)))
    profile = report.get("quality_profile") if isinstance(report.get("quality_profile"), dict) else {}
    categories = profile.get("warning_categories") if isinstance(profile.get("warning_categories"), dict) else {}
    lines.append("quality warnings")
    lines.append("repetition=" + escape(str(categories.get("repeated_self_narrative", 0))))
    lines.append("anchor=" + escape(str(categories.get("context_anchor", 0))))
    lines.append("request=" + escape(str(categories.get("request", 0))))
    for warning in profile.get("quality_warnings", []) or []:
        lines.append(escape(str(warning)))
    sources = report.get("source_reports") if isinstance(report.get("source_reports"), dict) else {}
    guard = sources.get("m4_post_change_guard") if isinstance(sources.get("m4_post_change_guard"), dict) else {}
    if guard.get("recommendation"):
        lines.append(escape(str(guard["recommendation"])))
    return lines


def _m6_field_pilot_lines(
    preflight_report,
    manual_wake_report,
    observation_report,
    recovery_report,
    scheduler_report,
    final_freeze_report,
):
    lines = ["M6 Field Pilot"]
    if (
        not preflight_report
        and not manual_wake_report
        and not observation_report
        and not recovery_report
        and not scheduler_report
        and not final_freeze_report
    ):
        lines.append("No M6 field pilot report captured.")
        return lines

    if preflight_report:
        lines.extend(_report_lines("M6 Preflight", preflight_report))
        pi_presence = (
            preflight_report.get("pi_presence")
            if isinstance(preflight_report.get("pi_presence"), dict)
            else {}
        )
        if pi_presence:
            lines.append("pi_detected=" + escape(str(pi_presence.get("detected"))))

    if manual_wake_report:
        lines.extend(_report_lines("M6 Manual Wake", manual_wake_report))
        profile = (
            manual_wake_report.get("profile")
            if isinstance(manual_wake_report.get("profile"), dict)
            else {}
        )
        field_pilot = (
            manual_wake_report.get("field_pilot")
            if isinstance(manual_wake_report.get("field_pilot"), dict)
            else {}
        )
        manual_wake = (
            field_pilot.get("manual_wake")
            if isinstance(field_pilot.get("manual_wake"), dict)
            else {}
        )
        lines.append("real_wake_requested=" + escape(str(profile.get("real_wake_requested"))))
        lines.append("provider_generation_started=" + escape(str(profile.get("provider_generation_started"))))
        if manual_wake:
            lines.append("manual_wake_executed=" + escape(str(manual_wake.get("executed"))))
            lines.append("attempt_count=" + escape(str(manual_wake.get("attempt_count", 0))))
        for reason in manual_wake_report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    if observation_report:
        lines.extend(_report_lines("M6 Observation", observation_report))
        field_pilot = (
            observation_report.get("field_pilot")
            if isinstance(observation_report.get("field_pilot"), dict)
            else {}
        )
        observation = (
            field_pilot.get("observation")
            if isinstance(field_pilot.get("observation"), dict)
            else {}
        )
        if observation:
            lines.append("observed_events=" + escape(str(observation.get("event_count", 0))))
            lines.append("completed_events=" + escape(str(observation.get("completed_count", 0))))

    if recovery_report:
        lines.extend(_report_lines("M6 Recovery", recovery_report))
        backup = (
            recovery_report.get("backup")
            if isinstance(recovery_report.get("backup"), dict)
            else {}
        )
        restore = (
            recovery_report.get("restore_sandbox")
            if isinstance(recovery_report.get("restore_sandbox"), dict)
            else {}
        )
        secret = (
            recovery_report.get("secret_boundary")
            if isinstance(recovery_report.get("secret_boundary"), dict)
            else {}
        )
        profile = (
            recovery_report.get("profile")
            if isinstance(recovery_report.get("profile"), dict)
            else {}
        )
        if backup:
            lines.append("backup_artifacts=" + escape(str(backup.get("artifact_count", 0))))
        if restore:
            lines.append("restore_verified=" + escape(str(restore.get("verified_artifact_count", 0))))
            lines.append("restore_mismatches=" + escape(str(restore.get("checksum_mismatch_count", 0))))
        if secret:
            lines.append("secret_values_copied=" + escape(str(secret.get("secret_values_copied"))))
        lines.append("live_restore_executed=" + escape(str(profile.get("live_restore_executed"))))

    if scheduler_report:
        lines.extend(_report_lines("M6 Scheduler", scheduler_report))
        handoff = (
            scheduler_report.get("handoff")
            if isinstance(scheduler_report.get("handoff"), dict)
            else {}
        )
        rollback = (
            scheduler_report.get("rollback")
            if isinstance(scheduler_report.get("rollback"), dict)
            else {}
        )
        profile = (
            scheduler_report.get("profile")
            if isinstance(scheduler_report.get("profile"), dict)
            else {}
        )
        if handoff:
            lines.append("handoff_ready=" + escape(str(handoff.get("ready"))))
            lines.append("scheduler_mutated=" + escape(str(handoff.get("mutated"))))
            if handoff.get("recommended_trigger"):
                lines.append(escape(str(handoff["recommended_trigger"])))
        if rollback:
            lines.append("rollback_instructions=" + escape(str(rollback.get("instructions_present"))))
            if rollback.get("latest_verified_backup"):
                lines.append(escape(str(rollback["latest_verified_backup"])))
        lines.append("scheduler_mutation_attempted=" + escape(str(profile.get("scheduler_mutation_attempted"))))

    if final_freeze_report:
        lines.extend(_report_lines("M6 Final Freeze", final_freeze_report))
        final_freeze = (
            final_freeze_report.get("final_freeze")
            if isinstance(final_freeze_report.get("final_freeze"), dict)
            else {}
        )
        rollback = (
            final_freeze_report.get("rollback")
            if isinstance(final_freeze_report.get("rollback"), dict)
            else {}
        )
        profile = (
            final_freeze_report.get("profile")
            if isinstance(final_freeze_report.get("profile"), dict)
            else {}
        )
        if final_freeze:
            lines.append("m6_frozen=" + escape(str(final_freeze.get("frozen"))))
            lines.append("readonly=" + escape(str(final_freeze.get("readonly"))))
            lines.append("scheduler_handoff_ready=" + escape(str(final_freeze.get("scheduler_handoff_ready"))))
            lines.append("scheduler_mutated=" + escape(str(final_freeze.get("scheduler_mutated"))))
        if rollback:
            lines.append("rollback_ready=" + escape(str(rollback.get("ready"))))
            if rollback.get("latest_verified_backup"):
                lines.append(escape(str(rollback["latest_verified_backup"])))
        lines.append("provider_generation_requested=" + escape(str(profile.get("provider_generation_requested"))))
        lines.append("live_restore_executed=" + escape(str(profile.get("live_restore_executed"))))

    return lines


def _m7_dialogue_lines(text_report, memory_report, freeze_report):
    lines = ["M7 Text Dialogue"]
    if not text_report and not memory_report and not freeze_report:
        lines.append("No M7 dialogue report captured.")
        return lines
    if text_report:
        lines.extend(_report_lines("M7 Text Dialogue", text_report))
        lines.append("raw_provider_payload_stored=" + escape(str(text_report.get("raw_provider_payload_stored"))))
    if memory_report:
        lines.extend(_report_lines("M7 Memory Proposal Gate", memory_report))
        counts = memory_report.get("counts") if isinstance(memory_report.get("counts"), dict) else {}
        authority = memory_report.get("prompt_authority_status") if isinstance(memory_report.get("prompt_authority_status"), dict) else {}
        lines.append("proposal_memory=" + escape(str(counts.get("proposal_memory", 0))))
        lines.append("proposal_prompt_authoritative=" + escape(str(authority.get("proposal_prompt_authoritative_count", 0))))
    if freeze_report:
        lines.extend(_report_lines("M7 Dialogue Freeze", freeze_report))
        final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
        profile = freeze_report.get("profile") if isinstance(freeze_report.get("profile"), dict) else {}
        lines.append("m7_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("readonly=" + escape(str(final_freeze.get("readonly"))))
        lines.append("provider_generation_requested=" + escape(str(profile.get("provider_generation_requested"))))
        lines.append("scheduler_mutation_allowed=" + escape(str(profile.get("scheduler_mutation_allowed"))))
        lines.append("semantic_shadow_authoritative=" + escape(str(profile.get("semantic_shadow_authoritative"))))
        for reason in freeze_report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))
    return lines


def _m8_memory_lines(
    steward_report,
    policy_report,
    retrieval_report,
    humanity_report,
    review_report,
    freeze_report,
):
    lines = ["M8 Memory Steward"]
    if not any((steward_report, policy_report, retrieval_report, humanity_report, review_report, freeze_report)):
        lines.append("No M8 memory report captured.")
        return lines
    for title, report in (
        ("M8 Steward", steward_report),
        ("M8 Policy", policy_report),
        ("M8 Retrieval", retrieval_report),
        ("M8 Dialogue Humanity", humanity_report),
        ("M8 Human Review", review_report),
        ("M8 Final Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))
    if review_report:
        counts = review_report.get("counts") if isinstance(review_report.get("counts"), dict) else {}
        lines.append("review_pending=" + escape(str(counts.get("pending", 0))))
        lines.append("ordinary_low_risk_review_required=" + escape(str(
            (review_report.get("profile") or {}).get("ordinary_low_risk_review_required")
            if isinstance(review_report.get("profile"), dict)
            else None
        )))
    if freeze_report:
        final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
        profile = freeze_report.get("profile") if isinstance(freeze_report.get("profile"), dict) else {}
        lines.append("m8_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("readonly=" + escape(str(final_freeze.get("readonly"))))
        lines.append("provider_generation_requested=" + escape(str(profile.get("provider_generation_requested"))))
        lines.append("scheduler_mutation_allowed=" + escape(str(profile.get("scheduler_mutation_allowed"))))
        lines.append("semantic_shadow_authoritative=" + escape(str(profile.get("semantic_shadow_authoritative"))))
        for reason in freeze_report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))
    return lines


def _m9_controlled_presence_lines(
    revalidation_report,
    dry_run_report,
    activation_report,
    observation_report,
    freeze_report,
):
    lines = ["M9 Controlled Presence"]
    if not any((revalidation_report, dry_run_report, activation_report, observation_report, freeze_report)):
        lines.append("No M9 controlled presence report captured.")
        return lines

    for title, report in (
        ("M9 Revalidation", revalidation_report),
        ("M9 Dry Run", dry_run_report),
        ("M9 Activation", activation_report),
        ("M9 Observation", observation_report),
        ("M9 Final Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    cadence_source = activation_report or revalidation_report
    cadence = cadence_source.get("cadence") if isinstance(cadence_source.get("cadence"), dict) else {}
    if cadence:
        lines.append("cadence_model=" + escape(str(cadence.get("model"))))
        lines.append("quiet_hours=" + escape(str(cadence.get("quiet_hours"))))
        lines.append("daily_live_wake_budget=" + escape(str(cadence.get("daily_live_wake_budget"))))
        lines.append("scheduled_wake_output=" + escape(str(cadence.get("scheduled_wake_output"))))

    scheduler = {}
    for report in (freeze_report, observation_report, activation_report):
        candidate = report.get("scheduler") if isinstance(report.get("scheduler"), dict) else {}
        if candidate:
            scheduler = candidate
            break
    if scheduler:
        lines.append("scheduler_mechanism=" + escape(str(scheduler.get("mechanism"))))
        lines.append("scheduler_enabled=" + escape(str(scheduler.get("enabled"))))
        lines.append("scheduler_artifact_count=" + escape(str(scheduler.get("artifact_count"))))
        if scheduler.get("pause_flag_path"):
            lines.append("pause_flag_path=" + escape(str(scheduler["pause_flag_path"])))
        if scheduler.get("presence_state_path"):
            lines.append("presence_state_path=" + escape(str(scheduler["presence_state_path"])))
        if scheduler.get("rollback_command"):
            lines.append("rollback_command=" + escape(str(scheduler["rollback_command"])))

    evidence = freeze_report.get("evidence") if isinstance(freeze_report.get("evidence"), dict) else {}
    if evidence:
        lines.append("live_attempts_observed=" + escape(str(evidence.get("live_attempts_observed"))))
        lines.append("scheduled_wake_events_observed=" + escape(str(evidence.get("scheduled_wake_events_observed"))))
        lines.append("pause_drill_ready=" + escape(str(evidence.get("pause_drill_ready"))))
        lines.append("rollback_drill_ready=" + escape(str(evidence.get("rollback_drill_ready"))))
        lines.append("provider_calls_by_freeze=" + escape(str(evidence.get("provider_calls_by_freeze"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m9_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("readonly=" + escape(str(final_freeze.get("readonly"))))
        lines.append("controlled_presence_ready=" + escape(str(final_freeze.get("controlled_presence_ready"))))
        lines.append("scheduler_reversible=" + escape(str(final_freeze.get("scheduler_reversible"))))

    boundaries = freeze_report.get("boundaries") if isinstance(freeze_report.get("boundaries"), dict) else {}
    if boundaries:
        for key in (
            "scheduler_mutated_by_freeze",
            "wake_cycle_run_by_freeze",
            "provider_generation_requested_by_freeze",
            "raw_provider_payload_stored",
            "semantic_shadow_authority_promoted",
            "proposal_or_quarantine_prompt_authority",
            "voice_signal_hardware_activation_allowed",
        ):
            lines.append(f"{escape(str(key))}={escape(str(boundaries.get(key)))}")

    for report in (revalidation_report, dry_run_report, activation_report, observation_report, freeze_report):
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _m10_signal_chat_lines(
    dry_run_report,
    trial_report,
    activation_report,
    observation_report,
    freeze_report,
):
    lines = ["M10 Signal Chat"]
    if not any((dry_run_report, trial_report, activation_report, observation_report, freeze_report)):
        lines.append("No M10 signal chat report captured.")
        return lines

    for title, report in (
        ("M10 Dry Run", dry_run_report),
        ("M10 Send Trial", trial_report),
        ("M10 Activation", activation_report),
        ("M10 Observation", observation_report),
        ("M10 Final Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    dry_run = dry_run_report.get("dry_run") if isinstance(dry_run_report.get("dry_run"), dict) else {}
    if dry_run:
        lines.append("dry_run_attempts=" + escape(str(dry_run.get("attempt_count"))))
        decisions = dry_run.get("decision_counts") if isinstance(dry_run.get("decision_counts"), dict) else {}
        for decision in ("replied", "skipped", "failed"):
            lines.append(f"decision_{decision}={escape(str(decisions.get(decision, 0)))}")
        missing = dry_run.get("skip_reasons_missing")
        lines.append("skip_reasons_missing=" + escape(str(missing if missing else "none")))

    transport = dry_run_report.get("transport") if isinstance(dry_run_report.get("transport"), dict) else {}
    if transport:
        lines.append("fake_transport_only=" + escape(str(transport.get("fake_transport_only"))))
        lines.append("signal_cli_invoked=" + escape(str(transport.get("signal_cli_invoked"))))
        lines.append("proactive_outbound_sent=" + escape(str(transport.get("proactive_outbound_sent"))))

    signal_chat = dry_run_report.get("signal_chat") if isinstance(dry_run_report.get("signal_chat"), dict) else {}
    if signal_chat:
        if signal_chat.get("attempts_file"):
            lines.append("signal_attempts_file=" + escape(str(signal_chat["attempts_file"])))
        if signal_chat.get("pause_flag_path"):
            lines.append("signal_pause_flag_path=" + escape(str(signal_chat["pause_flag_path"])))
        lines.append("signal_config_present=" + escape(str(signal_chat.get("config_present"))))

    service = {}
    for report in (freeze_report, activation_report):
        candidate = report.get("service") if isinstance(report.get("service"), dict) else {}
        if candidate:
            service = candidate
            break
    if service:
        lines.append("signal_service_mechanism=" + escape(str(service.get("mechanism"))))
        lines.append("signal_service_enabled=" + escape(str(service.get("enabled"))))
        lines.append("signal_service_artifact_count=" + escape(str(service.get("artifact_count"))))
        if service.get("unit_name"):
            lines.append("signal_service_unit=" + escape(str(service["unit_name"])))
        if service.get("rollback_command"):
            lines.append("signal_rollback_command=" + escape(str(service["rollback_command"])))

    observation = observation_report.get("observation") if isinstance(observation_report.get("observation"), dict) else {}
    if observation:
        lines.append("signal_observed_attempts=" + escape(str(observation.get("observed_attempts"))))
        observed_decisions = observation.get("decision_counts") if isinstance(observation.get("decision_counts"), dict) else {}
        for decision in ("replied", "skipped", "failed"):
            lines.append(f"signal_observed_{decision}={escape(str(observed_decisions.get(decision, 0)))}")

    evidence = freeze_report.get("evidence") if isinstance(freeze_report.get("evidence"), dict) else {}
    if evidence:
        lines.append("signal_live_attempts_observed=" + escape(str(evidence.get("live_attempts_observed"))))
        lines.append("signal_pause_drill_ready=" + escape(str(evidence.get("pause_drill_ready"))))
        lines.append("signal_rollback_documented=" + escape(str(evidence.get("rollback_documented"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m10_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("signal_chat_ready=" + escape(str(final_freeze.get("signal_chat_ready"))))
        lines.append("signal_service_reversible=" + escape(str(final_freeze.get("service_reversible"))))

    for report in (dry_run_report, trial_report, activation_report, observation_report, freeze_report):
        if not report:
            continue
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _m11_signal_outbound_lines(
    dry_run_report,
    trial_report,
    observation_report,
    freeze_report,
):
    lines = ["M11 Signal Outbound"]
    if not any((dry_run_report, trial_report, observation_report, freeze_report)):
        lines.append("No M11 signal outbound report captured.")
        return lines

    for title, report in (
        ("M11 Outbound Dry Run", dry_run_report),
        ("M11 Outbound Trial", trial_report),
        ("M11 Outbound Observation", observation_report),
        ("M11 Outbound Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    dry_run = dry_run_report.get("dry_run") if isinstance(dry_run_report.get("dry_run"), dict) else {}
    if dry_run:
        decisions = dry_run.get("decision_counts") if isinstance(dry_run.get("decision_counts"), dict) else {}
        for decision in ("delivered", "skipped", "failed"):
            lines.append(f"outbound_dry_{decision}={escape(str(decisions.get(decision, 0)))}")
        missing = dry_run.get("skip_reasons_missing")
        lines.append("outbound_skip_reasons_missing=" + escape(str(missing if missing else "none")))
        lines.append("outbound_disabled_noop=" + escape(str(dry_run.get("disabled_noop_confirmed"))))

    observation = observation_report.get("observation") if isinstance(observation_report.get("observation"), dict) else {}
    if observation:
        lines.append("outbound_observed_records=" + escape(str(observation.get("observed_records"))))
        observed_decisions = observation.get("decision_counts") if isinstance(observation.get("decision_counts"), dict) else {}
        for decision in ("delivered", "skipped", "failed"):
            lines.append(f"outbound_observed_{decision}={escape(str(observed_decisions.get(decision, 0)))}")

    evidence = freeze_report.get("evidence") if isinstance(freeze_report.get("evidence"), dict) else {}
    if evidence:
        lines.append("outbound_records_observed=" + escape(str(evidence.get("outbound_records_observed"))))
        lines.append("outbound_delivered_observed=" + escape(str(evidence.get("delivered_observed"))))
        lines.append("outbound_pause_drill_ready=" + escape(str(evidence.get("pause_drill_ready"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m11_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("outbound_ready=" + escape(str(final_freeze.get("outbound_ready"))))
        lines.append("outbound_reversible=" + escape(str(final_freeze.get("outbound_reversible"))))

    for report in (dry_run_report, trial_report, observation_report, freeze_report):
        if not report:
            continue
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _m12_semantic_retrieval_lines(
    readiness_report,
    retrieval_report,
    backfill_report,
    observation_report,
    freeze_report,
):
    lines = ["M12 Semantic Retrieval"]
    if not any((readiness_report, retrieval_report, backfill_report, observation_report, freeze_report)):
        lines.append("No M12 semantic retrieval report captured.")
        return lines

    for title, report in (
        ("M12 Readiness", readiness_report),
        ("M12 Retrieval Check", retrieval_report),
        ("M12 Backfill", backfill_report),
        ("M12 Observation", observation_report),
        ("M12 Final Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    backend_probe = {}
    for report in (observation_report, readiness_report):
        candidate = report.get("backend_probe") if isinstance(report.get("backend_probe"), dict) else {}
        if candidate:
            backend_probe = candidate
            break
    if backend_probe:
        lines.append("semantic_backend=" + escape(str(backend_probe.get("backend"))))
        lines.append("semantic_model=" + escape(str(backend_probe.get("model"))))

    coverage = {}
    for report in (observation_report, readiness_report):
        candidate = report.get("index_coverage") if isinstance(report.get("index_coverage"), dict) else {}
        if not candidate and isinstance(report.get("semantic_index"), dict):
            candidate = report["semantic_index"]
        if candidate and "coverage_ratio" in candidate:
            coverage = candidate
            break
    if coverage:
        lines.append("semantic_index_entries=" + escape(str(coverage.get("entries"))))
        lines.append("semantic_coverage_ratio=" + escape(str(coverage.get("coverage_ratio"))))
        lines.append("semantic_index_stale=" + escape(str(coverage.get("stale"))))

    counts = backfill_report.get("counts") if isinstance(backfill_report.get("counts"), dict) else {}
    if counts:
        lines.append("semantic_backfill_new=" + escape(str(counts.get("embedded_new"))))
        lines.append("semantic_backfill_refreshed=" + escape(str(counts.get("refreshed_stale"))))
        lines.append("semantic_backfill_pruned=" + escape(str(counts.get("pruned"))))

    live_probe = observation_report.get("live_probe") if isinstance(observation_report.get("live_probe"), dict) else {}
    if isinstance(live_probe.get("semantic"), dict):
        lines.append("semantic_live_status=" + escape(str(live_probe["semantic"].get("status"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m12_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("json_store_authoritative=" + escape(str(final_freeze.get("json_store_authoritative"))))
        lines.append("semantic_index_reversible=" + escape(str(final_freeze.get("index_reversible"))))

    for report in (readiness_report, retrieval_report, backfill_report, observation_report, freeze_report):
        if not report:
            continue
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _m13_feishu_chat_lines(
    dry_run_report,
    trial_report,
    activation_report,
    observation_report,
    freeze_report,
):
    lines = ["M13 Feishu Chat"]
    if not any((dry_run_report, trial_report, activation_report, observation_report, freeze_report)):
        lines.append("No M13 feishu chat report captured.")
        return lines

    for title, report in (
        ("M13 Dry Run", dry_run_report),
        ("M13 Reply Trial", trial_report),
        ("M13 Activation", activation_report),
        ("M13 Observation", observation_report),
        ("M13 Final Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    dry_run = dry_run_report.get("dry_run") if isinstance(dry_run_report.get("dry_run"), dict) else {}
    if dry_run:
        decisions = dry_run.get("decision_counts") if isinstance(dry_run.get("decision_counts"), dict) else {}
        for decision in ("replied", "skipped", "failed"):
            lines.append(f"feishu_dry_{decision}={escape(str(decisions.get(decision, 0)))}")
        lines.append("feishu_conversation_prefix=" + escape(str(dry_run.get("conversation_prefix_confirmed"))))

    transport = dry_run_report.get("transport") if isinstance(dry_run_report.get("transport"), dict) else {}
    if transport:
        lines.append("feishu_api_invoked=" + escape(str(transport.get("feishu_api_invoked"))))
        lines.append("feishu_fake_transport_only=" + escape(str(transport.get("fake_transport_only"))))

    service = {}
    for report in (freeze_report, activation_report):
        candidate = report.get("service") if isinstance(report.get("service"), dict) else {}
        if candidate:
            service = candidate
            break
    if service:
        lines.append("feishu_service_enabled=" + escape(str(service.get("enabled"))))
        if service.get("unit_name"):
            lines.append("feishu_service_unit=" + escape(str(service["unit_name"])))
        if service.get("rollback_command"):
            lines.append("feishu_rollback_command=" + escape(str(service["rollback_command"])))

    observation = observation_report.get("observation") if isinstance(observation_report.get("observation"), dict) else {}
    if observation:
        lines.append("feishu_observed_attempts=" + escape(str(observation.get("observed_attempts"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m13_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("feishu_chat_ready=" + escape(str(final_freeze.get("feishu_chat_ready"))))

    for report in (dry_run_report, trial_report, activation_report, observation_report, freeze_report):
        if not report:
            continue
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _m14_feishu_media_lines(
    dry_run_report,
    trial_report,
    observation_report,
    freeze_report,
):
    lines = ["M14 Feishu Media"]
    if not any((dry_run_report, trial_report, observation_report, freeze_report)):
        lines.append("No M14 feishu media report captured.")
        return lines

    for title, report in (
        ("M14 Media Dry Run", dry_run_report),
        ("M14 Media Trial", trial_report),
        ("M14 Media Observation", observation_report),
        ("M14 Media Freeze", freeze_report),
    ):
        if report:
            lines.extend(_report_lines(title, report))

    dry_run = dry_run_report.get("dry_run") if isinstance(dry_run_report.get("dry_run"), dict) else {}
    if dry_run:
        lines.append("media_text_priority=" + escape(str(dry_run.get("replied_despite_media_failures"))))
        voice = dry_run.get("voice_outcomes") if isinstance(dry_run.get("voice_outcomes"), dict) else {}
        if voice:
            lines.append("media_dry_voice_sent=" + escape(str(voice.get("sent"))))

    observation = observation_report.get("observation") if isinstance(observation_report.get("observation"), dict) else {}
    if observation:
        lines.append("media_events_observed=" + escape(str(observation.get("media_events"))))
        lines.append("media_voice_sent=" + escape(str(observation.get("voice_sent"))))
        lines.append("media_images_sent=" + escape(str(observation.get("images_sent"))))
        lines.append("media_voice_errors=" + escape(str(observation.get("voice_errors"))))

    final_freeze = freeze_report.get("final_freeze") if isinstance(freeze_report.get("final_freeze"), dict) else {}
    if final_freeze:
        lines.append("m14_frozen=" + escape(str(final_freeze.get("frozen"))))
        lines.append("feishu_media_ready=" + escape(str(final_freeze.get("feishu_media_ready"))))

    for report in (dry_run_report, trial_report, observation_report, freeze_report):
        if not report:
            continue
        for reason in report.get("stop_reasons", []) or []:
            lines.append("stop_reason=" + escape(str(reason)))

    return lines


def _near_status_lines():
    lines = ["Near-status TTL"]
    capsule = _load_json(COMPANION_HOME / "life-loop" / "context_capsule.json", default={}) or {}
    items = capsule.get("items") if isinstance(capsule.get("items"), list) else []
    near_items = [
        item for item in items
        if isinstance(item, dict) and item.get("field") in ("human_near_status", "human_emotion")
    ]
    if not near_items:
        lines.append("No context capsule captured.")
        return lines
    for item in near_items:
        ttl = item.get("ttl_wakes")
        readiness = "prompt ready" if item.get("prompt_eligible") and isinstance(ttl, int) and ttl > 0 else "prompt blocked"
        lines.append(readiness)
        lines.append(f"{escape(str(item.get('field')))} ttl={escape(str(ttl))}")
        lines.append(escape(str(item.get("content", ""))))
    return lines


def render_life_dashboard():
    latest = _latest_wake_event()
    state = _load_json(COMPANION_HOME / "life-loop" / "companion_state.json", default={}) or {}
    predeploy = _report("predeploy_report.json")
    m3_release = _report("m3_release_gate_report.json")
    m3_freeze = _report("m3_final_freeze_report.json")
    m4_deploy = _report("m4_deploy_report.json")
    m4_wake = _report("m4_wake_trial_report.json")
    m5_quality = _report("m5_quality_report.json")
    m6_preflight = _report("m6_preflight_report.json")
    m6_manual_wake = _report("m6_pi_manual_wake_report.json")
    m6_observation = _report("m6_pi_observation_report.json")
    m6_recovery = _report("m6_recovery_drill_report.json")
    m6_scheduler = _report("m6_scheduler_readiness_report.json")
    m6_final_freeze = _report("m6_final_freeze_report.json")
    m7_text_dialogue = _report("m7_text_dialogue_report.json")
    m7_memory_proposal = _report("m7_memory_proposal_report.json")
    m7_dialogue_freeze = _report("m7_dialogue_freeze_report.json")
    m8_memory_steward = _report("m8_memory_steward_report.json")
    m8_memory_policy = _report("m8_memory_policy_ledger_report.json")
    m8_memory_retrieval = _report("m8_memory_retrieval_report.json")
    m8_dialogue_humanity = _report("m8_dialogue_humanity_report.json")
    m8_human_review = _report("m8_human_review_queue_report.json")
    m8_memory_freeze = _report("m8_memory_freeze_report.json")
    m9_scheduler_revalidation = _report("m9_scheduler_revalidation_report.json")
    m9_scheduler_dry_run = _report("m9_scheduler_dry_run_report.json")
    m9_scheduler_activation = _report("m9_scheduler_activation_report.json")
    m9_presence_observation = _report("m9_presence_observation_report.json")
    m9_presence_freeze = _report("m9_presence_freeze_report.json")
    m10_signal_dry_run = _report("m10_signal_dry_run_report.json")
    m10_signal_trial = _report("m10_signal_trial_report.json")
    m10_signal_activation = _report("m10_signal_activation_report.json")
    m10_signal_observation = _report("m10_signal_observation_report.json")
    m10_signal_freeze = _report("m10_signal_freeze_report.json")
    m11_outbound_dry_run = _report("m11_signal_outbound_dry_run_report.json")
    m11_outbound_trial = _report("m11_signal_outbound_trial_report.json")
    m11_outbound_observation = _report("m11_signal_outbound_observation_report.json")
    m11_outbound_freeze = _report("m11_signal_outbound_freeze_report.json")
    m12_semantic_readiness = _report("m12_semantic_readiness_report.json")
    m12_semantic_retrieval = _report("m12_semantic_retrieval_report.json")
    m12_semantic_backfill = _report("m12_semantic_backfill_report.json")
    m12_semantic_observation = _report("m12_semantic_observation_report.json")
    m12_semantic_freeze = _report("m12_semantic_freeze_report.json")
    m13_feishu_dry_run = _report("m13_feishu_dry_run_report.json")
    m13_feishu_trial = _report("m13_feishu_trial_report.json")
    m13_feishu_activation = _report("m13_feishu_activation_report.json")
    m13_feishu_observation = _report("m13_feishu_observation_report.json")
    m13_feishu_freeze = _report("m13_feishu_freeze_report.json")
    m14_media_dry_run = _report("m14_feishu_media_dry_run_report.json")
    m14_media_trial = _report("m14_feishu_media_trial_report.json")
    m14_media_observation = _report("m14_feishu_media_observation_report.json")
    m14_media_freeze = _report("m14_feishu_media_freeze_report.json")

    sections = [
        ("Internal Life Loop", _event_life_lines(latest)),
        ("Companion State", [state.get("mood", ""), state.get("status", "")]),
        ("Safety Gates", []),
        ("Pi Predeploy", _report_lines("Pi Predeploy", predeploy)),
        ("M3/M4 Gates", (
            _report_lines("M3 Release", m3_release)
            + _report_lines("M3 Freeze", m3_freeze)
            + _report_lines("M4 Deploy", m4_deploy)
            + _m4_wake_lines(m4_wake)
        )),
        ("M5 Quality", _m5_quality_lines(m5_quality)),
        ("M6 Field Pilot", _m6_field_pilot_lines(
            m6_preflight,
            m6_manual_wake,
            m6_observation,
            m6_recovery,
            m6_scheduler,
            m6_final_freeze,
        )),
        ("M7 Text Dialogue", _m7_dialogue_lines(
            m7_text_dialogue,
            m7_memory_proposal,
            m7_dialogue_freeze,
        )),
        ("M8 Memory Steward", _m8_memory_lines(
            m8_memory_steward,
            m8_memory_policy,
            m8_memory_retrieval,
            m8_dialogue_humanity,
            m8_human_review,
            m8_memory_freeze,
        )),
        ("M9 Controlled Presence", _m9_controlled_presence_lines(
            m9_scheduler_revalidation,
            m9_scheduler_dry_run,
            m9_scheduler_activation,
            m9_presence_observation,
            m9_presence_freeze,
        )),
        ("M10 Signal Chat", _m10_signal_chat_lines(
            m10_signal_dry_run,
            m10_signal_trial,
            m10_signal_activation,
            m10_signal_observation,
            m10_signal_freeze,
        )),
        ("M11 Signal Outbound", _m11_signal_outbound_lines(
            m11_outbound_dry_run,
            m11_outbound_trial,
            m11_outbound_observation,
            m11_outbound_freeze,
        )),
        ("M12 Semantic Retrieval", _m12_semantic_retrieval_lines(
            m12_semantic_readiness,
            m12_semantic_retrieval,
            m12_semantic_backfill,
            m12_semantic_observation,
            m12_semantic_freeze,
        )),
        ("M13 Feishu Chat", _m13_feishu_chat_lines(
            m13_feishu_dry_run,
            m13_feishu_trial,
            m13_feishu_activation,
            m13_feishu_observation,
            m13_feishu_freeze,
        )),
        ("M14 Feishu Media", _m14_feishu_media_lines(
            m14_media_dry_run,
            m14_media_trial,
            m14_media_observation,
            m14_media_freeze,
        )),
        ("Near-status TTL", _near_status_lines()),
    ]
    body = ["<!doctype html><html><body>"]
    for title, lines in sections:
        body.append(f"<section><h1>{escape(title)}</h1>")
        for line in lines:
            if line is not None and str(line) != "":
                body.append(f"<p>{line}</p>")
        body.append("</section>")
    body.append("</body></html>")
    return "\n".join(body)


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

        /* ── Chat ── */
        .chat-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .chat-pill { background: var(--bg-deep); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
                     font-family: 'IBM Plex Mono', monospace; font-size: 0.72em; color: var(--text-dim); overflow-wrap: anywhere; }
        .chat-pill span { display: block; color: var(--text-secondary); margin-top: 3px; }
        .chat-transcript { display: flex; flex-direction: column; gap: 12px; margin-bottom: 18px; }
        .chat-row { max-width: 86%; padding: 14px 16px; border-radius: 12px; border: 1px solid var(--border); }
        .chat-row.human { align-self: flex-end; background: rgba(74,111,165,0.14); border-color: rgba(74,111,165,0.35); }
        .chat-row.assistant { align-self: flex-start; background: var(--bg-deep); }
        .chat-row.failed { border-color: var(--accent-red); opacity: 0.85; }
        .chat-role { font-family: 'IBM Plex Mono', monospace; font-size: 0.68em; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.12em; margin-bottom: 6px; }
        .chat-content { color: var(--text-secondary); white-space: pre-wrap; overflow-wrap: anywhere; }
        .chat-time { color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 0.65em; margin-top: 8px; }
        .chat-error { border-left: 3px solid var(--accent-red); background: rgba(160,84,84,0.12); padding: 12px 14px; border-radius: 8px; color: var(--text-secondary); margin-bottom: 16px; }
        .chat-form textarea { width: 100%; min-height: 115px; padding: 15px; background: var(--bg-deep); border: 1px solid var(--border);
                              border-radius: 8px; color: var(--text-primary); font-family: 'DM Sans', sans-serif; font-size: 0.95em; resize: vertical; }
        .chat-form textarea:focus { outline: none; border-color: var(--accent-blue); }
        .chat-form .chat-options { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
        .chat-form input, .chat-form select { background: var(--bg-deep); border: 1px solid var(--border); border-radius: 6px; color: var(--text-secondary); padding: 8px 10px; }
        .chatx-topbar { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
        .chatx-topbar .card-title { margin-bottom: 0; }
        .chatx-new-btn { font-size: 0.78em; color: var(--text-secondary); border: 1px solid var(--border); border-radius: 999px;
            padding: 6px 14px; text-decoration: none; transition: all 0.2s; }
        .chatx-new-btn:hover { color: var(--text-primary); border-color: var(--accent-blue); }
        .chatx-meta-inline { display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.7em; color: var(--text-dim); margin-bottom: 10px; }
        .chatx-shell { display: flex; flex-direction: column; height: calc(100vh - 330px); min-height: 460px;
            background: var(--bg-deep); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
        .chatx-messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; padding: 20px 18px 14px;
            scroll-behavior: smooth; }
        .chatx-messages::-webkit-scrollbar { width: 5px; }
        .chatx-messages::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
        .chatx-day { align-self: center; font-size: 0.68em; color: var(--text-dim); background: var(--bg-card);
            border: 1px solid var(--border); border-radius: 999px; padding: 3px 12px; margin: 4px 0; }
        .chatx-row { display: flex; gap: 9px; max-width: 80%; animation: chatxin 0.22s ease-out; }
        @keyframes chatxin { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        .chatx-row.human { align-self: flex-end; flex-direction: row-reverse; }
        .chatx-row.assistant { align-self: flex-start; }
        .chatx-avatar { width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center;
            justify-content: center; font-size: 0.82em; color: #f2f3f8; letter-spacing: 0; margin-top: 2px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.35); }
        .chatx-row.assistant .chatx-avatar { background: linear-gradient(135deg, var(--accent-purple), var(--heart)); }
        .chatx-row.human .chatx-avatar { background: linear-gradient(135deg, var(--accent-blue), #3a8a8a); }
        .chatx-body { display: flex; flex-direction: column; min-width: 0; }
        .chatx-row.human .chatx-body { align-items: flex-end; }
        .chatx-row.assistant .chatx-body { align-items: flex-start; }
        .chatx-bubble { padding: 11px 16px; border-radius: 18px; line-height: 1.65; white-space: pre-wrap;
            overflow-wrap: anywhere; font-size: 0.95em; box-shadow: 0 2px 10px rgba(0,0,0,0.22); }
        .chatx-row.human .chatx-bubble { background: linear-gradient(135deg, var(--accent-blue), #56548f);
            color: #f0f2f8; border-top-right-radius: 6px; }
        .chatx-row.assistant .chatx-bubble { background: var(--bg-card); border: 1px solid var(--border);
            color: var(--text-primary); border-top-left-radius: 6px; }
        .chatx-row.failed .chatx-bubble { box-shadow: inset 0 0 0 1px var(--accent-red); opacity: 0.8; }
        .chatx-time { font-size: 0.66em; color: var(--text-dim); margin: 4px 6px 0; }
        .chatx-time .chatx-fail { color: var(--accent-red); }
        .chatx-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
            gap: 14px; color: var(--text-secondary); text-align: center; line-height: 1.9; padding: 30px 20px; }
        .chatx-empty .chatx-empty-avatar { width: 58px; height: 58px; border-radius: 50%; display: flex; align-items: center;
            justify-content: center; font-size: 1.35em; color: #f2f3f8;
            background: linear-gradient(135deg, var(--accent-purple), var(--heart)); box-shadow: 0 4px 18px rgba(122,90,160,0.4); }
        .chatx-empty .chatx-empty-hint { font-size: 0.8em; color: var(--text-dim); }
        .chatx-typing-row { display: none; gap: 9px; align-self: flex-start; max-width: 80%; }
        .chatx-typing-row.on { display: flex; }
        .chatx-typing-bubble { padding: 14px 18px; border-radius: 18px; border-top-left-radius: 6px;
            background: var(--bg-card); border: 1px solid var(--border); display: flex; gap: 5px; align-items: center; }
        .chatx-typing-bubble .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text-secondary);
            animation: chatxblink 1.2s infinite; }
        .chatx-typing-bubble .dot:nth-child(2) { animation-delay: 0.2s; }
        .chatx-typing-bubble .dot:nth-child(3) { animation-delay: 0.4s; }
        @keyframes chatxblink { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }
        .chatx-composer { display: flex; gap: 10px; align-items: flex-end; padding: 12px 14px;
            background: var(--bg-card); border-top: 1px solid var(--border); }
        .chatx-composer textarea { flex: 1; resize: none; min-height: 44px; max-height: 150px; padding: 12px 18px;
            border-radius: 22px; background: var(--bg-deep); border: 1px solid var(--border); color: var(--text-primary);
            font-family: inherit; font-size: 0.95em; line-height: 1.5; transition: border-color 0.2s; }
        .chatx-composer textarea:focus { outline: none; border-color: var(--accent-blue); }
        .chatx-send-btn { height: 44px; padding: 0 22px; border: none; border-radius: 22px; cursor: pointer;
            background: linear-gradient(135deg, var(--accent-blue), #56548f); color: #f0f2f8; font-family: inherit;
            font-size: 0.9em; letter-spacing: 0.05em; transition: filter 0.2s, transform 0.1s; }
        .chatx-send-btn:hover { filter: brightness(1.15); }
        .chatx-send-btn:active { transform: scale(0.97); }
        .chatx-send-btn:disabled { opacity: 0.45; cursor: default; filter: none; }
        .chatx-transcript-path { font-size: 0.66em; color: var(--text-dim); margin-top: 10px; overflow-wrap: anywhere; }

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
            .chat-layout { grid-template-columns: 1fr; }
            .nav a { padding: 6px 10px; font-size: 0.7em; }
            .chat-meta { flex-direction: column; }
            .chat-pill { width: 100%; overflow-wrap: anywhere; }
            .task-form .form-row { flex-direction: column; align-items: stretch; }
            .req-meta { flex-wrap: wrap; gap: 8px; }
            .req-actions { flex-direction: column; }
            .reply-form { flex-direction: column; }
            .chat-row { max-width: 100%; }
            .chat-form .chat-options { flex-direction: column; align-items: stretch; }
            .chatx-row { max-width: 94%; }
            .chatx-avatar { width: 30px; height: 30px; font-size: 0.75em; }
            .chatx-shell { height: calc(100vh - 300px); min-height: 380px; border-radius: 12px; }
            .chatx-messages { padding: 14px 10px 10px; gap: 12px; }
            .chatx-send-btn { padding: 0 16px; }
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
            <a href="/life" class="{{ 'active' if page == 'life' }}">life</a>
            <a href="/chat" class="{{ 'active' if page == 'chat' }}">chat</a>
            <a href="/memory-review" class="{{ 'active' if page == 'memory_review' }}">memory review</a>
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

        {% elif page == 'chat' %}
            {% set companion_name = status.name | default('Companion') %}
            {% set companion_initial = companion_name[:1] %}
            <div class="card">
                <div class="chatx-topbar">
                    <div class="card-title">{{ companion_name }}</div>
                    <a class="chatx-new-btn" href="/chat?conversation_id=new">+ 新对话</a>
                </div>
                <div class="chatx-meta-inline">
                    <span>provider: {{ chat.provider }}</span>
                    <span>memory: {{ chat.memory_mode }}</span>
                    <span>conversation: {{ chat.conversation_id or 'new' }}</span>
                    <span id="chatx-proposals">proposals: {{ chat.memory_proposal_count }}</span>
                </div>
                {% if chat.error %}
                <div class="chat-error" id="chatx-error">{{ chat.error }}</div>
                {% else %}
                <div class="chat-error" id="chatx-error" style="display:none"></div>
                {% endif %}
                <div class="chatx-shell">
                    <div class="chatx-messages" id="chatx-messages">
                        {% if chat.transcript %}
                        {% set day_state = namespace(current='') %}
                        {% for row in chat.transcript %}
                        {% set row_day = (row.created_at or '')[:10] %}
                        {% if row_day and row_day != day_state.current %}
                        {% set day_state.current = row_day %}
                        <div class="chatx-day">{{ row_day }}</div>
                        {% endif %}
                        <div class="chatx-row {{ row.role }} {{ row.status }}">
                            <div class="chatx-avatar">{{ companion_initial if row.role == 'assistant' else '你' }}</div>
                            <div class="chatx-body">
                                <div class="chatx-bubble">{{ row.content }}</div>
                                <div class="chatx-time">{{ (row.created_at or '')[11:16] }}{% if row.status == 'failed' %} · <span class="chatx-fail">未送达</span>{% endif %}</div>
                            </div>
                        </div>
                        {% endfor %}
                        {% else %}
                        <div class="chatx-empty" id="chatx-empty">
                            <div class="chatx-empty-avatar">{{ companion_initial }}</div>
                            <div>这里是和 {{ companion_name }} 的实时对话,<br>她现在就会回你。</div>
                            <div class="chatx-empty-hint">留言板上的话,她要到下次醒来才会看到;这里不一样。</div>
                        </div>
                        {% endif %}
                        <div class="chatx-typing-row" id="chatx-typing">
                            <div class="chatx-avatar">{{ companion_initial }}</div>
                            <div class="chatx-typing-bubble">
                                <span class="dot"></span><span class="dot"></span><span class="dot"></span>
                            </div>
                        </div>
                    </div>
                    <form class="chatx-composer" id="chatx-form" action="/chat/send" method="POST">
                        <input type="hidden" name="conversation_id" id="chatx-conversation" value="{{ chat.conversation_id }}">
                        <textarea name="message" id="chatx-input" placeholder="说点什么..." rows="1"
                            autocomplete="off">{{ chat.preserved_input }}</textarea>
                        <button type="submit" class="chatx-send-btn" id="chatx-send">发送</button>
                    </form>
                </div>
                {% if chat.transcript_path %}
                <div class="chatx-transcript-path">transcript · {{ chat.transcript_path }}</div>
                {% endif %}
            </div>
            <script>
            (function () {
                var form = document.getElementById('chatx-form');
                var input = document.getElementById('chatx-input');
                var sendBtn = document.getElementById('chatx-send');
                var messages = document.getElementById('chatx-messages');
                var typing = document.getElementById('chatx-typing');
                var errorBox = document.getElementById('chatx-error');
                var conversationField = document.getElementById('chatx-conversation');
                var companionInitial = {{ (status.name | default('Companion'))[:1] | tojson }};
                if (!form || !input) return;

                function scrollToBottom() { messages.scrollTop = messages.scrollHeight; }
                function autogrow() {
                    input.style.height = 'auto';
                    input.style.height = Math.min(input.scrollHeight, 150) + 'px';
                }
                function pad(n) { return (n < 10 ? '0' : '') + n; }
                function ensureDayChip() {
                    var d = new Date();
                    var today = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
                    var chips = messages.querySelectorAll('.chatx-day');
                    var last = chips.length ? chips[chips.length - 1].textContent : '';
                    if (last !== today) {
                        var chip = document.createElement('div');
                        chip.className = 'chatx-day';
                        chip.textContent = today;
                        messages.insertBefore(chip, typing);
                    }
                }
                function appendBubble(role, text) {
                    var empty = document.getElementById('chatx-empty');
                    if (empty) empty.remove();
                    ensureDayChip();
                    var d = new Date();
                    var row = document.createElement('div');
                    row.className = 'chatx-row ' + role + ' completed';
                    var avatar = document.createElement('div');
                    avatar.className = 'chatx-avatar';
                    avatar.textContent = role === 'assistant' ? companionInitial : '你';
                    var body = document.createElement('div');
                    body.className = 'chatx-body';
                    var bubble = document.createElement('div');
                    bubble.className = 'chatx-bubble';
                    bubble.textContent = text;
                    var time = document.createElement('div');
                    time.className = 'chatx-time';
                    time.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes());
                    body.appendChild(bubble); body.appendChild(time);
                    row.appendChild(avatar); row.appendChild(body);
                    messages.insertBefore(row, typing);
                    scrollToBottom();
                }
                function setBusy(busy) {
                    sendBtn.disabled = busy;
                    typing.classList.toggle('on', busy);
                    if (busy) scrollToBottom();
                }
                function showError(text) {
                    errorBox.textContent = text;
                    errorBox.style.display = text ? '' : 'none';
                }

                input.addEventListener('input', autogrow);
                input.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        form.dispatchEvent(new Event('submit', { cancelable: true }));
                    }
                });

                form.addEventListener('submit', function (event) {
                    event.preventDefault();
                    var text = input.value.trim();
                    if (!text || sendBtn.disabled) return;
                    showError('');
                    appendBubble('human', text);
                    input.value = '';
                    autogrow();
                    setBusy(true);
                    var payload = { message: text };
                    if (conversationField.value && conversationField.value !== 'new') {
                        payload.conversation_id = conversationField.value;
                    }
                    fetch('/chat/send', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                        body: JSON.stringify(payload)
                    }).then(function (response) {
                        return response.json().then(function (data) { return { ok: response.ok, data: data }; });
                    }).then(function (result) {
                        setBusy(false);
                        if (result.ok && result.data.ok) {
                            appendBubble('assistant', result.data.reply);
                            if (result.data.conversation_id) {
                                conversationField.value = result.data.conversation_id;
                                var url = new URL(window.location.href);
                                url.searchParams.set('conversation_id', result.data.conversation_id);
                                window.history.replaceState({}, '', url);
                            }
                        } else {
                            input.value = (result.data && result.data.input) || text;
                            autogrow();
                            showError((result.data && result.data.error) || 'send failed; your text is preserved above.');
                        }
                    }).catch(function () {
                        setBusy(false);
                        input.value = text;
                        autogrow();
                        showError('network error; your text is preserved in the composer.');
                    });
                    input.focus();
                });

                autogrow();
                scrollToBottom();
                input.focus();
            })();
            </script>

        {% elif page == 'memory_review' %}
            <div class="card">
                <div class="card-title">Memory Review</div>
                <div class="chat-meta">
                    <span class="chat-pill">pending: {{ memory_review.counts.pending }}</span>
                    <span class="chat-pill">reviewed: {{ memory_review.counts.reviewed }}</span>
                    <span class="chat-pill">actions: {{ memory_review.counts.actions }}</span>
                </div>
                {% if memory_review.error %}
                <div class="chat-error">{{ memory_review.error }}</div>
                {% endif %}
            </div>

            {% if memory_review.pending %}
                {% for item in memory_review.pending %}
                <div class="card">
                    <div class="card-title">{{ item.risk }} · {{ item.decision }} · {{ item.recommended_action }}</div>
                    <div class="memory-item">
                        <div class="memory-text">{{ item.candidate_content }}</div>
                        <div class="memory-time">{{ item.id }} · {{ item.conversation_id }}</div>
                    </div>
                    <div class="journal-content">{{ item.reason }}</div>
                    <div class="task-card">
                        <div class="task-actions">
                            <form action="/memory-review/{{ item.id }}/approve" method="POST">
                                <button type="submit" class="btn btn-green btn-small">Approve</button>
                            </form>
                            <form action="/memory-review/{{ item.id }}/reject" method="POST">
                                <button type="submit" class="btn btn-red btn-small">Reject</button>
                            </form>
                        </div>
                        <form class="message-form" action="/memory-review/{{ item.id }}/edit" method="POST">
                            <textarea name="content">{{ item.candidate_content }}</textarea>
                            <button type="submit" class="btn btn-purple btn-small">Edit and approve</button>
                        </form>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="card">
                    <div class="board-empty">no pending memory review.</div>
                </div>
            {% endif %}

            {% if memory_review.reviewed %}
            <div class="card">
                <div class="card-title">Reviewed</div>
                {% for item in memory_review.reviewed %}
                <div class="memory-item">
                    <div class="memory-text">{{ item.candidate_content }}</div>
                    <div class="memory-time">{{ item.id }} · {{ item.latest_action.action }}</div>
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
                try {
                    const resp = await fetch('/api/substack/queue');
                    ssQueue = await resp.json();
                    ssRender();
                } catch(e) {
                    document.getElementById('substack-content').innerHTML =
                        '<div class="ss-empty">Failed to load queue: ' + e.message + '</div>';
                }
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

                const created = new Date(post.created || post.created_at).toLocaleDateString('en-US', {
                    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
                });

                let actions = '';
                if (post.status === 'pending') {
                    actions = '<button class="ss-btn ss-btn-approve" onclick="ssApprove(\\'' + post.id + '\\')">Approve</button>' +
                              '<button class="ss-btn ss-btn-reject" onclick="ssReject(\\'' + post.id + '\\')">Reject</button>';
                } else if (post.status === 'published' && post.substack_url) {
                    actions = '<a class="ss-link" href="' + post.substack_url + '" target="_blank">View on Substack &rarr;</a>';
                }

                const expandBtn = !expanded
                    ? '<button class="ss-btn-expand" onclick="ssToggle(\\'' + post.id + '\\')">Read full post</button>'
                    : '<button class="ss-btn-expand" onclick="ssToggle(\\'' + post.id + '\\')">Collapse</button>';

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
    full = safe_child_path(CREATIONS_DIR, filepath)
    if full and full.exists() and full.is_file():
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
        chat=get_chat_state(),
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


@app.route("/life")
def life_dashboard():
    return render_life_dashboard()


@app.route("/chat")
def chat_page():
    ctx = _base_context()
    ctx.update(
        page="chat",
        chat=get_chat_state(request.args.get("conversation_id") or None),
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/chat/send", methods=["POST"])
def chat_send():
    data = request.get_json(silent=True) if request.is_json else {}
    human_text = (data.get("message") if isinstance(data, dict) else None) or request.form.get("message", "")
    conversation_id = (data.get("conversation_id") if isinstance(data, dict) else None) or request.form.get("conversation_id") or None
    if conversation_id == "new":
        # "new" is the fresh-conversation marker from the chat page; the
        # dialogue runner generates a real id when none is supplied.
        conversation_id = None
    preserved_input = human_text
    if not str(human_text or "").strip():
        error = "message must not be empty"
        if _wants_json_response():
            return jsonify({"ok": False, "error": error, "input": preserved_input}), 400
        ctx = _base_context()
        ctx.update(page="chat", chat=get_chat_state(conversation_id, error=error, preserved_input=preserved_input))
        return render_template_string(TEMPLATE, **ctx), 400
    try:
        runner, provider, memory_mode = _chat_runner()
        result = runner.run_turn(
            human_text,
            conversation_id=conversation_id,
            provider=provider,
            memory_mode=memory_mode,
            auto_memory=False,
        )
    except Exception as exc:  # noqa: BLE001 - dashboard preserves failed input and redacts secrets.
        error = f"{DEFAULT_CHAT_ERROR} ({type(exc).__name__}: {_clean_visible_text(str(exc))})"
        if _wants_json_response():
            return jsonify({"ok": False, "error": error, "input": preserved_input, "conversation_id": conversation_id}), 500
        ctx = _base_context()
        ctx.update(page="chat", chat=get_chat_state(conversation_id, error=error, preserved_input=preserved_input))
        return render_template_string(TEMPLATE, **ctx), 500

    payload = {
        "ok": True,
        "conversation_id": result.conversation_id,
        "reply": result.reply,
        "transcript": str(result.transcript_path),
        "event": result.event["id"],
        "memory_ids": [memory["id"] for memory in result.stored_memories],
        "memory_proposal_ids": [proposal["id"] for proposal in result.memory_proposals],
        "memory_proposal_count": len(result.memory_proposals),
    }
    if _wants_json_response():
        return jsonify(payload)
    return redirect(url_for("chat_page", conversation_id=result.conversation_id))


@app.route("/memory-review")
def memory_review_page():
    ctx = _base_context()
    ctx.update(
        page="memory_review",
        memory_review=get_memory_review_state(),
    )
    return render_template_string(TEMPLATE, **ctx)


@app.route("/memory-review/<decision_id>/approve", methods=["POST"])
def memory_review_approve(decision_id):
    note = _review_request_value("note", "")
    try:
        result = approve_memory_review_decision(
            CompanionPaths.from_env(COMPANION_HOME),
            decision_id,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - dashboard returns compact user-visible error.
        error = f"{type(exc).__name__}: {_clean_visible_text(str(exc))}"
        if _wants_json_response():
            return jsonify({"ok": False, "error": error, "decision_id": decision_id}), 400
        ctx = _base_context()
        ctx.update(page="memory_review", memory_review=get_memory_review_state(error=error))
        return render_template_string(TEMPLATE, **ctx), 400
    if _wants_json_response():
        return jsonify(result)
    return redirect(url_for("memory_review_page"))


@app.route("/memory-review/<decision_id>/reject", methods=["POST"])
def memory_review_reject(decision_id):
    note = _review_request_value("note", "")
    try:
        result = reject_memory_review_decision(
            CompanionPaths.from_env(COMPANION_HOME),
            decision_id,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {_clean_visible_text(str(exc))}"
        if _wants_json_response():
            return jsonify({"ok": False, "error": error, "decision_id": decision_id}), 400
        ctx = _base_context()
        ctx.update(page="memory_review", memory_review=get_memory_review_state(error=error))
        return render_template_string(TEMPLATE, **ctx), 400
    if _wants_json_response():
        return jsonify(result)
    return redirect(url_for("memory_review_page"))


@app.route("/memory-review/<decision_id>/edit", methods=["POST"])
def memory_review_edit(decision_id):
    content = _review_request_value("content", "")
    note = _review_request_value("note", "")
    try:
        result = approve_memory_review_decision(
            CompanionPaths.from_env(COMPANION_HOME),
            decision_id,
            edited_content=content,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {_clean_visible_text(str(exc))}"
        if _wants_json_response():
            return jsonify({"ok": False, "error": error, "decision_id": decision_id}), 400
        ctx = _base_context()
        ctx.update(page="memory_review", memory_review=get_memory_review_state(error=error))
        return render_template_string(TEMPLATE, **ctx), 400
    if _wants_json_response():
        return jsonify(result)
    return redirect(url_for("memory_review_page"))


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
    try:
        test_commands = build_test_commands(proj_config)
    except ValueError as exc:
        task["status"] = "test_failed"
        task["test_result"] = f"FAIL: {exc}"
        save_task_queue(queue)
        return redirect(url_for("tasks"))
    test_timeout = config.get("defaults", {}).get("test_timeout_seconds", 30)
    if not test_commands:
        task["status"] = "tested"
        task["tested"] = datetime.now().isoformat()
        task["test_result"] = "PASS: no test command configured, skipped"
        save_task_queue(queue)
        return redirect(url_for("tasks"))
    try:
        outputs = []
        failed = None
        for test_cmd in test_commands:
            result = subprocess.run(
                test_cmd, cwd=task["project_path"],
                capture_output=True, text=True, timeout=test_timeout
            )
            outputs.append(result.stdout or result.stderr)
            if result.returncode != 0:
                failed = result
                break
        if failed is None:
            task["status"] = "tested"
            task["tested"] = datetime.now().isoformat()
            task["test_result"] = f"PASS: {' '.join(outputs)[:300]}"
        else:
            task["status"] = "test_failed"
            task["test_result"] = f"FAIL (exit {failed.returncode}): {failed.stderr[:300]}"
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
