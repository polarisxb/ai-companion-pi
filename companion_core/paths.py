"""Path resolution for local and deployed companion homes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompanionPaths:
    home: Path

    @classmethod
    def from_env(cls, companion_home: str | Path | None = None) -> "CompanionPaths":
        raw_home = companion_home or os.environ.get("COMPANION_HOME")
        if raw_home is None:
            raw_home = Path(__file__).resolve().parents[1]
        return cls(Path(raw_home).expanduser().resolve())

    @property
    def context_dir(self) -> Path:
        return self.home / "context"

    @property
    def journals_dir(self) -> Path:
        return self.home / "journals"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory-server"

    @property
    def memory_store(self) -> Path:
        return self.memory_dir / "memory_store.json"

    @property
    def requests_dir(self) -> Path:
        return self.home / "requests"

    @property
    def requests_file(self) -> Path:
        return self.requests_dir / "requests.json"

    @property
    def life_loop_dir(self) -> Path:
        return self.home / "life-loop"

    @property
    def wake_events_file(self) -> Path:
        return self.life_loop_dir / "wake_events.jsonl"

    @property
    def conversations_dir(self) -> Path:
        return self.home / "conversations"

    @property
    def conversation_events_file(self) -> Path:
        return self.life_loop_dir / "conversation_events.jsonl"

    @property
    def memory_proposals_file(self) -> Path:
        return self.life_loop_dir / "memory_proposals.jsonl"

    @property
    def memory_decisions_file(self) -> Path:
        return self.life_loop_dir / "memory_decisions.jsonl"

    @property
    def memory_review_actions_file(self) -> Path:
        return self.life_loop_dir / "memory_review_actions.jsonl"

    @property
    def scheduler_pause_flag(self) -> Path:
        return self.life_loop_dir / "scheduler_pause.flag"

    @property
    def scheduler_presence_state_file(self) -> Path:
        return self.life_loop_dir / "scheduler_presence_state.json"

    @property
    def scheduler_attempts_file(self) -> Path:
        return self.life_loop_dir / "scheduler_attempts.jsonl"

    @property
    def scheduler_wake_lock_file(self) -> Path:
        return self.life_loop_dir / "scheduler_wake.lock"

    @property
    def signal_chat_config_file(self) -> Path:
        return self.life_loop_dir / "signal_chat_config.json"

    @property
    def signal_chat_state_file(self) -> Path:
        return self.life_loop_dir / "signal_chat_state.json"

    @property
    def signal_chat_attempts_file(self) -> Path:
        return self.life_loop_dir / "signal_chat_attempts.jsonl"

    @property
    def signal_chat_pause_flag(self) -> Path:
        return self.life_loop_dir / "signal_chat_pause.flag"

    @property
    def signal_chat_lock_file(self) -> Path:
        return self.life_loop_dir / "signal_chat.lock"

    @property
    def signal_outbox_file(self) -> Path:
        return self.life_loop_dir / "signal_outbox.jsonl"

    @property
    def signal_outbound_pause_flag(self) -> Path:
        return self.life_loop_dir / "signal_outbound_pause.flag"

    @property
    def semantic_retrieval_config_file(self) -> Path:
        return self.life_loop_dir / "semantic_retrieval_config.json"

    @property
    def semantic_index_file(self) -> Path:
        return self.life_loop_dir / "semantic_index.json"

    @property
    def feishu_chat_config_file(self) -> Path:
        return self.life_loop_dir / "feishu_chat_config.json"

    @property
    def feishu_chat_lock_file(self) -> Path:
        return self.life_loop_dir / "feishu_chat.lock"

    @property
    def model_outputs_dir(self) -> Path:
        return self.life_loop_dir / "model_outputs"

    @property
    def semantic_shadow_dir(self) -> Path:
        return self.life_loop_dir / "semantic_shadow"

    @property
    def semantic_shadow_store(self) -> Path:
        return self.semantic_shadow_dir / "memory_store.json"

    @property
    def companion_state_file(self) -> Path:
        return self.life_loop_dir / "companion_state.json"

    @property
    def context_capsule_file(self) -> Path:
        return self.life_loop_dir / "context_capsule.json"

    @property
    def window_dir(self) -> Path:
        return self.home / "window"

    @property
    def status_file(self) -> Path:
        return self.window_dir / "status.json"

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.context_dir,
            self.journals_dir,
            self.memory_dir,
            self.requests_dir,
            self.life_loop_dir,
            self.conversations_dir,
            self.window_dir,
            self.window_dir / "content",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def context_file(self, name: str) -> Path:
        return self.context_dir / name

    def conversation_transcript(self, conversation_id: str) -> Path:
        safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in conversation_id)
        return self.conversations_dir / f"conversation_{safe_id}.jsonl"
