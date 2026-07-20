"""M14 chat reply media: voice bubbles and creation-image attachments.

Media is an enhancement layered after the text reply has already been
delivered. Every failure here is recorded in the attempt ledger and silently
downgrades to the text that was already sent — a media problem can never
fail a reply. Only transports that declare ``supports_media`` participate.
"""

from __future__ import annotations

import os
import re
import stat as stat_module
import tempfile
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import SignalChatConfig
from .tts import TTSError, create_tts_backend

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CREATIONS_DIR_NAME = "creations"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def media_enabled(config: SignalChatConfig, transport) -> bool:
    if not getattr(transport, "supports_media", False):
        return False
    return config.voice_replies != "off" or config.image_attachments_enabled


def media_prompt_hints(config: SignalChatConfig, transport) -> str | None:
    """Metadata guidance injected into the dialogue prompt only when active."""

    if not getattr(transport, "supports_media", False):
        return None
    hints = []
    if config.voice_replies == "companion_choice":
        hints.append(
            '- If you genuinely want this reply heard in your voice instead of read, '
            'add "voice": true to the metadata JSON. Use it sparingly, for moments that deserve it.'
        )
    if config.image_attachments_enabled:
        hints.append(
            '- You may attach one of your own creations to this reply by adding '
            '"attachments": [{"type": "image", "path": "creations/art/<file>"}] to the metadata JSON. '
            "Only attach files that exist under your creations directory."
        )
    if not hints:
        return None
    return "\n".join(hints)


def voice_decision(config: SignalChatConfig, metadata: dict, reply_text: str) -> tuple[bool, str | None]:
    """Return (requested, skip_reason). ``requested`` means synthesis should run."""

    mode = config.voice_replies
    if mode == "off":
        return False, None
    if mode == "companion_choice" and not (isinstance(metadata, dict) and metadata.get("voice") is True):
        return False, None
    if len(str(reply_text or "")) > config.voice_max_chars:
        return False, "reply_too_long_for_voice"
    return True, None


def validate_image_attachments(
    paths: CompanionPaths,
    config: SignalChatConfig,
    metadata: dict,
) -> tuple[list[dict], list[dict]]:
    """Return (validated snapshots, rejected audit records).

    Each valid entry is ``{"relative": str, "filename": str, "data": bytes}``.
    The bytes are read at validation time through an ``O_NOFOLLOW`` file
    descriptor whose ``fstat`` must show a regular, non-hardlinked file within
    the size cap — so what gets uploaded later is exactly what was validated,
    and neither symlink swaps nor hardlink aliases can smuggle bytes from
    outside ``creations/``.
    """

    if not config.image_attachments_enabled:
        return [], []
    raw_attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
    if not isinstance(raw_attachments, list):
        return [], []
    creations_root = (paths.home / CREATIONS_DIR_NAME).resolve()
    valid: list[dict] = []
    rejected: list[dict] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            rejected.append({"path": None, "reason": "malformed_attachment"})
            continue
        raw_path = str(item.get("path") or "")[:200]
        record = {"path": raw_path, "reason": None}
        if item.get("type") != "image":
            record["reason"] = "unsupported_attachment_type"
            rejected.append(record)
            continue
        if not raw_path:
            record["reason"] = "missing_path"
            rejected.append(record)
            continue
        try:
            resolved = (paths.home / raw_path).resolve()
        except OSError:
            record["reason"] = "unresolvable_path"
            rejected.append(record)
            continue
        if creations_root != resolved and creations_root not in resolved.parents:
            record["reason"] = "path_outside_creations"
            rejected.append(record)
            continue
        if resolved.suffix.lower() not in IMAGE_EXTENSIONS:
            record["reason"] = "unsupported_extension"
            rejected.append(record)
            continue
        if not resolved.is_file():
            record["reason"] = "file_not_found"
            rejected.append(record)
            continue
        snapshot_reason, data = _snapshot_regular_file(resolved, config.image_max_bytes)
        if snapshot_reason is not None:
            record["reason"] = snapshot_reason
            rejected.append(record)
            continue
        if len(valid) >= config.max_images_per_reply:
            record["reason"] = "over_max_images_per_reply"
            rejected.append(record)
            continue
        valid.append({
            "relative": _relative_to_home(paths, resolved),
            "filename": _safe_filename(resolved.name),
            "data": data,
        })
    return valid, rejected


def _snapshot_regular_file(resolved: Path, max_bytes: int) -> tuple[str | None, bytes | None]:
    """Read file bytes through an O_NOFOLLOW fd with fstat-backed checks."""

    try:
        fd = os.open(str(resolved), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        if exc.errno == getattr(os, "ELOOP", 40):
            return "symlink_rejected", None
        return "file_not_found", None
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            return "not_regular_file", None
        if info.st_nlink > 1:
            return "hardlinked_file_rejected", None
        if info.st_size > max_bytes:
            return "file_too_large", None
        data = b""
        remaining = info.st_size
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            data += chunk
            remaining -= len(chunk)
        return None, data
    except OSError:
        return "unreadable_file", None
    finally:
        os.close(fd)


def _safe_filename(name: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", str(name or ""))[:80]
    return cleaned or "image.png"


def deliver_reply_media(
    paths: CompanionPaths,
    config: SignalChatConfig,
    transport,
    recipient: str,
    reply_text: str,
    metadata: dict,
    *,
    tts_backend=None,
) -> dict | None:
    """Send voice/image media for an already-delivered reply.

    Returns a hashed-safe media outcome payload for the attempt ledger, or
    ``None`` when media is not in play at all.
    """

    if not media_enabled(config, transport):
        return None
    payload: dict = {}

    requested, skip_reason = voice_decision(config, metadata, reply_text)
    if config.voice_replies != "off":
        voice_outcome: dict = {"requested": requested, "sent": False}
        if skip_reason:
            voice_outcome["skip_reason"] = skip_reason
        if requested:
            try:
                backend = tts_backend or create_tts_backend(config)
                if backend is None:
                    raise TTSError("voice replies are enabled but tts_command is not configured")
                with tempfile.TemporaryDirectory(prefix="companion-voice-") as voice_dir:
                    synthesized = backend.synthesize_opus(reply_text, Path(voice_dir))
                    transport.send_voice(recipient, synthesized.opus_path, synthesized.duration_ms)
                    voice_outcome["sent"] = True
                    voice_outcome["duration_ms"] = synthesized.duration_ms
            except Exception as exc:  # noqa: BLE001 - media failures downgrade to the sent text.
                voice_outcome["error"] = {
                    "type": type(exc).__name__,
                    "message": " ".join(str(exc).split())[:200],
                }
        payload["voice"] = voice_outcome

    if config.image_attachments_enabled:
        valid, rejected = validate_image_attachments(paths, config, metadata)
        if valid or rejected:
            images_outcome: dict = {"sent": 0, "sent_paths": [], "rejected": rejected, "errors": []}
            for snapshot in valid:
                try:
                    # The validated byte snapshot is what gets uploaded, so a
                    # file swapped on disk after validation changes nothing.
                    transport.send_image(recipient, snapshot["filename"], snapshot["data"])
                    images_outcome["sent"] += 1
                    images_outcome["sent_paths"].append(snapshot["relative"])
                except Exception as exc:  # noqa: BLE001 - media failures downgrade to the sent text.
                    images_outcome["errors"].append({
                        "path": snapshot["relative"],
                        "type": type(exc).__name__,
                        "message": " ".join(str(exc).split())[:200],
                    })
            payload["images"] = images_outcome

    return payload or None


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
