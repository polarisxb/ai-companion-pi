"""Context loading for a companion wake cycle."""

from __future__ import annotations

from dataclasses import dataclass, field

from .context_capsule import load_context_capsule
from .memory import JsonMemoryStore
from .paths import CompanionPaths
from .state import load_companion_state


@dataclass
class WakeContext:
    who_companion: str
    who_human: str
    now: str
    companion_state: dict = field(default_factory=dict)
    context_capsule: dict = field(default_factory=dict)
    recent_journals: list[tuple[str, str]] = field(default_factory=list)
    recent_memories: list[dict] = field(default_factory=list)


def read_text(path, default: str = "") -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return default


def load_recent_journals(paths: CompanionPaths, limit: int = 3) -> list[tuple[str, str]]:
    if not paths.journals_dir.exists():
        return []
    files = sorted(
        [path for path in paths.journals_dir.glob("*.md") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    journals = []
    for path in files:
        body = read_text(path)
        if body:
            journals.append((path.name, body))
    return journals


def load_wake_context(paths: CompanionPaths, memory_store: JsonMemoryStore | None = None) -> WakeContext:
    memory_store = memory_store or JsonMemoryStore(paths.memory_store)
    recent_for_context = getattr(memory_store, "recent_for_context", memory_store.recent)
    return WakeContext(
        who_companion=read_text(paths.context_file("who_is_companion.txt"), "You are Companion."),
        who_human=read_text(paths.context_file("who_is_human.txt"), "The human has not been described yet."),
        now=read_text(paths.context_file("now.txt"), ""),
        companion_state=load_companion_state(paths.companion_state_file),
        context_capsule=load_context_capsule(paths.context_capsule_file),
        recent_journals=[],
        recent_memories=recent_for_context(5),
    )
