"""M7 text dialogue engine.

This module is deliberately separate from the wake lifecycle. Dialogue is a
human-initiated chat surface that may write conversation artifacts, but must not
run wake cycles, append wake events, mutate scheduler state, or store raw
provider payloads by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .context import WakeContext, load_wake_context
from .events import append_wake_event, load_wake_events
from .llm import LLMClient
from .memory import JsonMemoryStore, MemoryEntry
from .memory_policy import evaluate_memory_proposal
from .paths import CompanionPaths
from .state import has_state_update, update_companion_state

DIALOGUE_READY_RECOMMENDATION = "m7_cli_dialogue_ready"
PROVIDER_REQUIRED_RECOMMENDATION = "provider_required"
INSPECT_RECOMMENDATION = "inspect"
SECRET_REDACTION = "[REDACTED]"


@dataclass
class DialogueMemoryProposal:
    """A memory candidate that is not auto-committed."""

    id: str
    content: str
    reason: str
    source: str = "chat"
    status: str = "proposed"
    turn_id: str | None = None
    conversation_id: str | None = None
    created_at: str | None = None

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "reason": self.reason,
            "source": self.source,
            "status": self.status,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
        }


@dataclass
class DialogueTurnResult:
    conversation_id: str
    turn_id: str
    reply: str
    transcript_path: Path
    event: dict
    accepted_memories: list[dict] = field(default_factory=list)
    memory_proposals: list[dict] = field(default_factory=list)
    companion_state: dict | None = None
    report: dict | None = None


class DialogueRunner:
    """One-turn M7 dialogue engine with narrow write authority."""

    def __init__(
        self,
        paths: CompanionPaths,
        llm_client: LLMClient,
        memory_store: JsonMemoryStore | None = None,
        *,
        provider: str | None = None,
        store_raw_provider_payload: bool = False,
    ):
        self.paths = paths
        self.llm_client = llm_client
        self.memory_store = memory_store or JsonMemoryStore(paths.memory_store)
        self.provider = provider
        self.store_raw_provider_payload = store_raw_provider_payload

    def run_turn(self, human_text: str, *, conversation_id: str | None = None) -> DialogueTurnResult:
        human_text = _clean_text(human_text)
        if not human_text:
            raise ValueError("human text is required")

        self.paths.ensure_runtime_dirs()
        _ensure_dialogue_dirs(self.paths)
        started_at = datetime.now()
        monotonic_start = time.monotonic()
        conversation_id = conversation_id or f"conversation_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        turn_id = f"dialogue_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        transcript_path = self.paths.home / "conversations" / f"{conversation_id}.jsonl"

        context = load_wake_context(self.paths, self.memory_store)
        final_freeze = _load_final_freeze_evidence(self.paths)
        prompt = self._render_prompt(context, human_text, final_freeze, transcript_path)
        raw_output = self.llm_client.generate(prompt, context)
        parsed = _parse_dialogue_output(raw_output)
        reply = _redact_secrets(_clean_text(parsed["reply"]))
        if not reply:
            raise ValueError("dialogue provider returned an empty reply")

        accepted_memories, proposals = self._handle_memory_candidates(
            parsed.get("memory_candidates", []),
            human_text=human_text,
            turn_id=turn_id,
            conversation_id=conversation_id,
        )
        companion_state = None
        if has_state_update(parsed.get("state_update")):
            companion_state = update_companion_state(self.paths.companion_state_file, parsed["state_update"])

        output_hash = hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        transcript_rows = [
            {
                "type": "message",
                "role": "human",
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "created_at": started_at.isoformat(),
                "text": _redact_secrets(human_text),
            },
            {
                "type": "message",
                "role": "companion",
                "turn_id": turn_id,
                "conversation_id": conversation_id,
                "created_at": datetime.now().isoformat(),
                "text": reply,
                "provider": self.provider,
                "output_audit": {"sha256": output_hash},
                "memory_ids": [memory.get("id") for memory in accepted_memories],
                "memory_proposal_ids": [proposal["id"] for proposal in proposals],
            },
        ]
        if self.store_raw_provider_payload:
            transcript_rows[-1]["raw_provider_payload"] = _redact_secrets(raw_output)
        _append_jsonl(transcript_path, transcript_rows)

        event = self._build_event(
            turn_id=turn_id,
            conversation_id=conversation_id,
            started_at=started_at,
            monotonic_start=monotonic_start,
            transcript_path=transcript_path,
            status="completed",
            output_hash=output_hash,
            accepted_memories=accepted_memories,
            proposals=proposals,
            companion_state=companion_state,
            final_freeze=final_freeze,
        )
        append_wake_event(self.paths.life_loop_dir / "conversation_events.jsonl", event)
        report = self._write_report(event, final_freeze)
        return DialogueTurnResult(
            conversation_id=conversation_id,
            turn_id=turn_id,
            reply=reply,
            transcript_path=transcript_path,
            event=event,
            accepted_memories=accepted_memories,
            memory_proposals=proposals,
            companion_state=companion_state,
            report=report,
        )

    def _render_prompt(
        self,
        context: WakeContext,
        human_text: str,
        final_freeze: dict,
        transcript_path: Path,
    ) -> str:
        memories = "\n".join(f"- {memory.get('content', '')}" for memory in context.recent_memories)
        state = json.dumps(context.companion_state, ensure_ascii=False, sort_keys=True)
        capsule = json.dumps(context.context_capsule, ensure_ascii=False, sort_keys=True)
        return f"""You are the companion in a user-initiated M7 text dialogue.

M7 boundary facts:
- This is chat, not a wake cycle.
- Reply naturally to the human. Do not show wake report sections.
- Do not ask to mutate scheduler, cron, timers, services, or /life.
- Memory candidates must be JSON metadata only when useful.

M6.7 final freeze evidence: {final_freeze.get('recommendation', 'missing')}
Transcript path: {transcript_path.relative_to(self.paths.home)}

=== WHO YOU ARE ===
{context.who_companion}

=== WHO YOUR HUMAN IS ===
{context.who_human}

=== CURRENT CONTEXT ===
{context.now}

=== COMPANION STATE ===
{state}

=== CONTEXT CAPSULE ===
{capsule}

=== ACCEPTED MEMORY ===
{memories or '- (none)'}

Human message:
{human_text}

Return either plain text, or JSON with keys:
reply: natural companion reply string
memory_candidates: optional list of objects with content, source, authority, risk
state_update: optional object with mood/status only when explicitly intended
"""

    def _handle_memory_candidates(
        self,
        candidates: list[dict],
        *,
        human_text: str,
        turn_id: str,
        conversation_id: str,
    ) -> tuple[list[dict], list[dict]]:
        accepted: list[dict] = []
        proposals: list[dict] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = _clean_text(candidate.get("content"))
            if not content:
                continue
            low_risk_user_fact = _is_low_risk_user_asserted(candidate, human_text)
            if low_risk_user_fact:
                entry = MemoryEntry(
                    content=content,
                    source="chat",
                    context=[f"conversation:{conversation_id}", f"turn:{turn_id}"],
                    source_type="user",
                    authority="user_asserted",
                    prompt_eligible=True,
                    evidence_refs=[{"event_id": turn_id, "artifact": "human_chat_text"}],
                )
                decision = evaluate_memory_proposal(entry, event_id=turn_id)
                if decision.accepted and decision.normalized_entry is not None:
                    accepted.append(
                        self.memory_store.store(
                            decision.normalized_entry,
                            accepted_for_context=decision.prompt_eligible,
                            source_event_id=turn_id,
                        )
                    )
                    continue
            proposal = DialogueMemoryProposal(
                id=f"memprop_{uuid.uuid4().hex[:12]}",
                content=_redact_secrets(content),
                reason=_proposal_reason(candidate, low_risk_user_fact),
                turn_id=turn_id,
                conversation_id=conversation_id,
                created_at=datetime.now().isoformat(),
            ).to_record()
            proposals.append(proposal)
        if proposals:
            _append_jsonl(self.paths.home / "conversations" / "memory_proposals.jsonl", proposals)
        return accepted, proposals

    def _build_event(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        started_at: datetime,
        monotonic_start: float,
        transcript_path: Path,
        status: str,
        output_hash: str,
        accepted_memories: list[dict],
        proposals: list[dict],
        companion_state: dict | None,
        final_freeze: dict,
    ) -> dict:
        return {
            "id": turn_id,
            "conversation_id": conversation_id,
            "trigger": "human-text-chat",
            "status": status,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_seconds": round(time.monotonic() - monotonic_start, 3),
            "provider": self.provider,
            "transcript": str(transcript_path.relative_to(self.paths.home)),
            "memory_count": len(accepted_memories),
            "memory_ids": [memory.get("id") for memory in accepted_memories],
            "memory_proposal_count": len(proposals),
            "memory_proposal_ids": [proposal["id"] for proposal in proposals],
            "state_updated": companion_state is not None,
            "output_audit": {"sha256": output_hash},
            "m6_final_freeze": {
                "present": final_freeze.get("present") is True,
                "ok": final_freeze.get("ok") is True,
                "recommendation": final_freeze.get("recommendation"),
            },
            "boundaries": {
                "wake_cycle_run": False,
                "wake_events_written": False,
                "scheduler_mutated": False,
                "raw_provider_payload_stored": self.store_raw_provider_payload,
                "semantic_shadow_authority_promoted": False,
            },
        }

    def _write_report(self, event: dict, final_freeze: dict) -> dict:
        stop_reasons = []
        if not final_freeze.get("present"):
            stop_reasons.append("m6_final_freeze_missing")
        elif not final_freeze.get("ok"):
            stop_reasons.append("m6_final_freeze_not_ready")
        report = {
            "ok": not stop_reasons and event["status"] == "completed",
            "milestone": "M7.1",
            "recommendation": DIALOGUE_READY_RECOMMENDATION if not stop_reasons else INSPECT_RECOMMENDATION,
            "companion_home": str(self.paths.home),
            "provider": self.provider,
            "event": event,
            "stop_reasons": stop_reasons,
        }
        self.paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.life_loop_dir / "m7_text_dialogue_report.json").write_text(
            json.dumps(_redact_obj(report), indent=2, sort_keys=True)
        )
        return report


def _ensure_dialogue_dirs(paths: CompanionPaths) -> None:
    (paths.home / "conversations").mkdir(parents=True, exist_ok=True)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fd:
        for row in rows:
            fd.write(json.dumps(_redact_obj(row), sort_keys=True) + "\n")


def _load_final_freeze_evidence(paths: CompanionPaths) -> dict:
    report_path = paths.life_loop_dir / "m6_final_freeze_report.json"
    try:
        report = json.loads(report_path.read_text())
    except FileNotFoundError:
        return {"present": False, "ok": False, "recommendation": None}
    except json.JSONDecodeError:
        return {"present": True, "ok": False, "recommendation": "invalid_report"}
    if not isinstance(report, dict):
        return {"present": True, "ok": False, "recommendation": "invalid_report"}
    return {
        "present": True,
        "ok": report.get("ok") is True,
        "recommendation": report.get("recommendation"),
    }


def _parse_dialogue_output(raw_output: str) -> dict:
    text = raw_output.strip()
    if not text:
        return {"reply": ""}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"reply": text, "memory_candidates": [], "state_update": {}}
    if not isinstance(payload, dict):
        return {"reply": text, "memory_candidates": [], "state_update": {}}
    reply = payload.get("reply") if isinstance(payload.get("reply"), str) else ""
    candidates = payload.get("memory_candidates") if isinstance(payload.get("memory_candidates"), list) else []
    state_update = payload.get("state_update") if isinstance(payload.get("state_update"), dict) else {}
    return {"reply": reply, "memory_candidates": candidates, "state_update": state_update}


def _is_low_risk_user_asserted(candidate: dict, human_text: str) -> bool:
    content = _clean_text(candidate.get("content"))
    source = str(candidate.get("source", "")).lower()
    authority = str(candidate.get("authority", "")).lower()
    risk = str(candidate.get("risk", "")).lower()
    if source not in {"user", "human", "chat"}:
        return False
    if authority not in {"user_asserted", "explicit_user_fact"}:
        return False
    if risk not in {"low", "low_risk", "preference", "fact"}:
        return False
    if _sensitive_text(content):
        return False
    # Require at least a small overlap with the human's message so model-originated
    # claims cannot be laundered into accepted memory.
    return bool(set(_tokens(content)) & set(_tokens(human_text)))


def _proposal_reason(candidate: dict, low_risk_user_fact: bool) -> str:
    if low_risk_user_fact:
        return "candidate did not pass memory policy acceptance"
    risk = str(candidate.get("risk", "")).strip()
    if risk:
        return f"proposal-only memory candidate ({risk})"
    return "inferred, sensitive, relationship-defining, ambiguous, or model-originated candidate"


def _sensitive_text(text: str) -> bool:
    return bool(re.search(r"\b(password|secret|token|api[_ -]?key|ssn|social security|medical|diagnos|bank|credit card|love you|partner|spouse)\b", text, re.I))


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_secrets(value)
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_obj(item) for key, item in value.items()}
    return value


def _redact_secrets(text: str) -> str:
    redacted = text
    for key, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            redacted = redacted.replace(value, SECRET_REDACTION)
    redacted = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s*[:=]\s*\S+", rf"\1={SECRET_REDACTION}", redacted)
    redacted = re.sub(r"(?i)(api[_ -]?key|token|secret|password)\s+is\s+\S+", rf"\1 is {SECRET_REDACTION}", redacted)
    redacted = re.sub(r"\b(?:sk|pk|rk|ghp|gho|xox[baprs])-[-A-Za-z0-9_]{8,}\b", SECRET_REDACTION, redacted)
    return redacted


# Kept as a guardrail import test: dialogue uses its own ledger, never wake_events.
def _dialogue_wake_event_count(paths: CompanionPaths) -> int:
    return len(load_wake_events(paths.wake_events_file))
