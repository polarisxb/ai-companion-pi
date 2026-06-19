"""User-initiated text dialogue engine for M7.

Dialogue is intentionally separate from wake cycles: it loads the same identity,
state, context capsule, and accepted memory foundation, but it never runs the
wake lifecycle and never writes wake/scheduler artifacts.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .context import WakeContext, load_wake_context
from .events import append_wake_event
from .llm import LLMClient
from .memory import JsonMemoryStore, MemoryEntry
from .paths import CompanionPaths
from .provenance import evidence_ref
from .state import has_state_update, render_companion_state, update_companion_state

SECRET_TOKEN_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{12,}|[A-Za-z0-9_]*API[_-]?KEY\s*=\s*[^\s]+|Bearer\s+[A-Za-z0-9._-]{12,})"
)
SENSITIVE_RE = re.compile(
    r"(?i)\b(password|api key|secret|token|health|medical|diagnosis|legal|lawyer|bank|ssn|身份证|密码|密钥|令牌|医疗|诊断|法律|银行卡)\b"
)
LOW_RISK_FACT_PATTERNS = (
    re.compile(r"(?i)\b(?:call me|please call me)\s+(.{1,80})$"),
    re.compile(r"(?i)\b(?:my name is)\s+(.{1,80})$"),
    re.compile(r"(?i)\b(?:i prefer|i like)\s+(.{1,140})$"),
    re.compile(r"(?:以后)?(?:请)?叫我\s*([^。.!！?？\n]{1,40})"),
    re.compile(r"我(?:喜欢|偏好)\s*([^。.!！?？\n]{1,80})"),
    re.compile(r"这个项目(?:现在)?叫\s*([^。.!！?？\n]{1,60})"),
)
METADATA_LINE_RE = re.compile(r"(?:^|\n)\s*(?:INTERNAL_METADATA|METADATA)\s*:\s*(\{.*\})\s*$", re.DOTALL)
JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


@dataclass
class DialogueTurnResult:
    conversation_id: str
    transcript_path: Path
    reply: str
    event: dict
    human_turn: dict
    assistant_turn: dict
    stored_memory_ids: list[str] = field(default_factory=list)
    memory_proposal_ids: list[str] = field(default_factory=list)
    companion_state: dict | None = None


class DialogueEngine:
    """One-turn text dialogue runner with transcript and memory boundaries."""

    def __init__(
        self,
        paths: CompanionPaths,
        llm_client: LLMClient,
        *,
        memory_store: JsonMemoryStore | None = None,
        provider: str | None = None,
        memory_mode: str = "json",
    ):
        self.paths = paths
        self.llm_client = llm_client
        self.memory_store = memory_store or JsonMemoryStore(paths.memory_store)
        self.provider = provider
        self.memory_mode = memory_mode

    def run_turn(self, user_text: str, *, conversation_id: str | None = None) -> DialogueTurnResult:
        clean_input = _redact_secrets(user_text).strip()
        if not clean_input:
            raise ValueError("user_text must not be empty")

        self.paths.ensure_runtime_dirs()
        self.paths.conversations_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now()
        conversation_id = conversation_id or f"conv_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
        transcript_path = self.paths.conversation_transcript(conversation_id)
        event_id = f"dialogue_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        human_turn_id = f"turn_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}_human"
        assistant_turn_id = f"turn_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}_assistant"

        try:
            context = load_wake_context(self.paths, self.memory_store)
            recent_turns = load_recent_transcript_turns(transcript_path, limit=8)
            prompt = self._render_prompt(context, clean_input, recent_turns)
            raw_reply = self.llm_client.generate(prompt, context)
            reply, metadata = parse_dialogue_reply(raw_reply)
            reply = _redact_secrets(reply).strip() or "我在这里。你可以再说一遍吗？"

            memory_proposals = build_memory_proposals(
                clean_input,
                conversation_id=conversation_id,
                source_turn_id=human_turn_id,
                created_at=started_at,
            )
            stored_memory_ids = store_auto_memories(
                self.memory_store,
                clean_input,
                event_id=event_id,
                source_turn_id=human_turn_id,
            )
            proposal_records = [proposal for proposal in memory_proposals if not proposal.get("auto_committable")]
            for proposal in proposal_records:
                append_jsonl(self.paths.conversation_memory_proposals_file, proposal)

            companion_state = None
            state_update = metadata.get("companion_state") if isinstance(metadata, dict) else None
            if has_state_update(state_update):
                companion_state = update_companion_state(self.paths.companion_state_file, state_update)

            human_turn = transcript_turn(
                turn_id=human_turn_id,
                conversation_id=conversation_id,
                role="human",
                content=clean_input,
                created_at=started_at,
                provider=self.provider,
                memory_mode=self.memory_mode,
                input_hash=hash_text(clean_input),
                output_hash=None,
                memory_proposal_ids=[proposal["id"] for proposal in proposal_records],
            )
            assistant_turn = transcript_turn(
                turn_id=assistant_turn_id,
                conversation_id=conversation_id,
                role="assistant",
                content=reply,
                created_at=datetime.now(),
                provider=self.provider,
                memory_mode=self.memory_mode,
                input_hash=hash_text(clean_input),
                output_hash=hash_text(reply),
                memory_proposal_ids=[proposal["id"] for proposal in proposal_records],
            )
            append_jsonl(transcript_path, human_turn)
            append_jsonl(transcript_path, assistant_turn)

            event = self._build_event(
                event_id=event_id,
                conversation_id=conversation_id,
                status="completed",
                started_at=started_at,
                transcript_path=transcript_path,
                turn_count=2,
                memory_proposal_count=len(proposal_records),
                stored_memory_count=len(stored_memory_ids),
                companion_state_updated=companion_state is not None,
                error=None,
            )
            append_wake_event(self.paths.conversation_events_file, event)
            return DialogueTurnResult(
                conversation_id=conversation_id,
                transcript_path=transcript_path,
                reply=reply,
                event=event,
                human_turn=human_turn,
                assistant_turn=assistant_turn,
                stored_memory_ids=stored_memory_ids,
                memory_proposal_ids=[proposal["id"] for proposal in proposal_records],
                companion_state=companion_state,
            )
        except Exception as exc:
            event = self._build_event(
                event_id=event_id,
                conversation_id=conversation_id,
                status="failed",
                started_at=started_at,
                transcript_path=transcript_path,
                turn_count=0,
                memory_proposal_count=0,
                stored_memory_count=0,
                companion_state_updated=False,
                error={"type": type(exc).__name__, "message": _redact_secrets(str(exc))},
            )
            append_wake_event(self.paths.conversation_events_file, event)
            raise

    def _render_prompt(self, context: WakeContext, user_text: str, recent_turns: list[dict]) -> str:
        memories = "\n".join(f"- {memory.get('content', '')}" for memory in context.recent_memories) or "- (none accepted yet)"
        turns = render_recent_turns(recent_turns)
        capsule = json.dumps(context.context_capsule, ensure_ascii=False, sort_keys=True)
        state = render_companion_state(context.companion_state)
        return f"""You are having a direct text conversation with your human.

Do not run wake-cycle sections. Do not trigger schedulers, cron, timers, services, or /life writes.
Reply naturally in the user's language, usually concise Simplified Chinese when appropriate.
You may optionally end with a single INTERNAL_METADATA JSON object containing only:
{{"companion_state": {{"mood": "...", "status": "..."}}}}
Only emit companion_state when you explicitly choose a current mood/status. Do not infer the human's state.

=== WHO YOU ARE ===
{context.who_companion}

=== WHO YOUR HUMAN IS ===
{context.who_human}

=== CURRENT CONTEXT ===
{context.now}

=== CURRENT COMPANION STATE ===
{state}

=== CONTEXT CAPSULE ===
{capsule}

=== ACCEPTED MEMORY ONLY ===
{memories}

=== RECENT CONVERSATION TURNS ===
{turns}

Human says:
{user_text}
"""

    def _build_event(
        self,
        *,
        event_id: str,
        conversation_id: str,
        status: str,
        started_at: datetime,
        transcript_path: Path,
        turn_count: int,
        memory_proposal_count: int,
        stored_memory_count: int,
        companion_state_updated: bool,
        error: dict | None,
    ) -> dict:
        return {
            "id": event_id,
            "conversation_id": conversation_id,
            "created_at": started_at.isoformat(),
            "status": status,
            "trigger": "human-text-chat",
            "provider": self.provider,
            "memory_mode": self.memory_mode,
            "transcript": str(transcript_path.relative_to(self.paths.home)),
            "turn_count": turn_count,
            "memory_proposal_count": memory_proposal_count,
            "stored_memory_count": stored_memory_count,
            "companion_state_updated": companion_state_updated,
            "raw_provider_payload_stored": False,
            "wake_cycle_triggered": False,
            "scheduler_mutated": False,
            "error": error,
        }


class FakeDialogueClient:
    """Deterministic text-chat fake for local M7 smoke tests."""

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, context: WakeContext) -> str:
        self.calls += 1
        return (
            f"我在这里，收到你的第 {self.calls} 条消息。"
            "我们可以先把这件事讲清楚，再决定下一步。"
            "\nINTERNAL_METADATA: {\"companion_state\": {\"mood\": \"专注\", \"status\": \"正在进行 M7 文字对话。\"}}"
        )


def parse_dialogue_reply(raw_reply: str) -> tuple[str, dict]:
    text = _redact_secrets(raw_reply or "")
    metadata: dict[str, Any] = {}
    match = METADATA_LINE_RE.search(text)
    if match:
        metadata = _safe_json_object(match.group(1))
        text = text[: match.start()].strip()
    else:
        fence_match = JSON_FENCE_RE.search(text)
        if fence_match:
            candidate = _safe_json_object(fence_match.group(1))
            if "companion_state" in candidate:
                metadata = candidate
                text = (text[: fence_match.start()] + text[fence_match.end():]).strip()
    return text.strip(), metadata


def load_recent_transcript_turns(transcript_path: Path, limit: int = 8) -> list[dict]:
    try:
        lines = transcript_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    turns = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("role") in {"human", "assistant"}:
            turns.append(item)
    return turns[-limit:]


def render_recent_turns(turns: list[dict]) -> str:
    if not turns:
        return "(no previous turns in this conversation)"
    rendered = []
    for turn in turns[-8:]:
        content = _redact_secrets(str(turn.get("content", "")))[:800]
        role = turn.get("role", "unknown")
        rendered.append(f"{role}: {content}")
    return "\n".join(rendered)


def transcript_turn(
    *,
    turn_id: str,
    conversation_id: str,
    role: str,
    content: str,
    created_at: datetime,
    provider: str | None,
    memory_mode: str,
    input_hash: str | None,
    output_hash: str | None,
    memory_proposal_ids: list[str],
) -> dict:
    return {
        "id": turn_id,
        "conversation_id": conversation_id,
        "role": role,
        "created_at": created_at.isoformat(),
        "content": _redact_secrets(content),
        "provider": provider,
        "memory_mode": memory_mode,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "raw_output_stored": False,
        "memory_proposal_ids": memory_proposal_ids,
    }


def build_memory_proposals(user_text: str, *, conversation_id: str, source_turn_id: str, created_at: datetime) -> list[dict]:
    explicit_fact = extract_low_risk_user_fact(user_text)
    proposals = []
    if explicit_fact:
        proposals.append(_memory_proposal(
            conversation_id=conversation_id,
            source_turn_id=source_turn_id,
            created_at=created_at,
            content=explicit_fact,
            reason="explicit low-risk user-stated fact/preference",
            auto_committable=True,
        ))
    elif should_propose_memory(user_text):
        proposals.append(_memory_proposal(
            conversation_id=conversation_id,
            source_turn_id=source_turn_id,
            created_at=created_at,
            content=_redact_secrets(user_text)[:500],
            reason="possibly important but sensitive, inferred, ambiguous, or not low-risk enough for automatic memory",
            auto_committable=False,
        ))
    return proposals


def store_auto_memories(
    memory_store: JsonMemoryStore,
    user_text: str,
    *,
    event_id: str,
    source_turn_id: str,
) -> list[str]:
    fact = extract_low_risk_user_fact(user_text)
    if not fact:
        return []
    entry = MemoryEntry(
        content=fact,
        source="human",
        context=["m7_text_dialogue"],
        intensity=2,
        valence=3,
        significance=3,
        memory_type="semantic",
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=[
            evidence_ref(event_id=event_id, artifact="conversation_turn", content=source_turn_id),
        ],
    )
    memory = memory_store.store(entry, accepted_for_context=True, source_event_id=event_id)
    return [memory["id"]]


def extract_low_risk_user_fact(user_text: str) -> str | None:
    text = " ".join(_redact_secrets(user_text).split())
    if not text or SENSITIVE_RE.search(text):
        return None
    for pattern in LOW_RISK_FACT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(1).strip(" 。.!！?？\"'")
        if value and not SENSITIVE_RE.search(value):
            return f"Human stated: {value}"
    if ("记得" in text or "remember" in text.lower()) and len(text) <= 160 and not SENSITIVE_RE.search(text):
        return f"Human stated: {text}"
    return None


def should_propose_memory(user_text: str) -> bool:
    text = user_text.strip()
    if not text:
        return False
    return bool(SENSITIVE_RE.search(text) or "我觉得" in text or "maybe" in text.lower() or len(text) >= 160)


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as out:
                out.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def hash_text(text: str | None) -> str | None:
    if text is None:
        return None
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _memory_proposal(
    *,
    conversation_id: str,
    source_turn_id: str,
    created_at: datetime,
    content: str,
    reason: str,
    auto_committable: bool,
) -> dict:
    return {
        "id": f"memprop_{created_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}",
        "conversation_id": conversation_id,
        "source_turn_id": source_turn_id,
        "created_at": created_at.isoformat(),
        "status": "auto_committable" if auto_committable else "proposed",
        "content": _redact_secrets(content),
        "reason": reason,
        "accepted": bool(auto_committable),
        "auto_committable": bool(auto_committable),
    }


def _safe_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _redact_secrets(text: str) -> str:
    redacted = SECRET_TOKEN_RE.sub("[REDACTED]", text or "")
    for env_name in ("DEEPSEEK_API_KEY", "COMPANION_LLM_API_KEY"):
        value = os.environ.get(env_name)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted
