"""Structured authority gate for companion memory writes."""

from __future__ import annotations

from dataclasses import dataclass

from .memory import MemoryEntry
from .provenance import content_hash, evidence_ref

PROMPT_ELIGIBLE_AUTHORITIES = {
    "user_asserted",
    "system_config",
    "evaluator_approved",
    "derived_summary",
}
DURABLE_SEMANTIC_AUTHORITIES = {
    "user_asserted",
    "system_config",
    "evaluator_approved",
    "derived_summary",
}
MODEL_AUTHORITIES = {"model_proposed"}


@dataclass
class PolicyDecision:
    accepted: bool
    prompt_eligible: bool
    target: str
    reason: str
    normalized_entry: MemoryEntry | None = None

    def to_event(self) -> dict:
        entry = self.normalized_entry
        payload = {
            "accepted": self.accepted,
            "prompt_eligible": self.prompt_eligible,
            "target": self.target,
            "reason": self.reason,
        }
        if entry:
            payload.update({
                "content_hash": content_hash(entry.content),
                "memory_type": entry.memory_type,
                "source_type": entry.source_type,
                "authority": entry.authority,
            })
        return payload


def evaluate_memory_proposal(
    entry: MemoryEntry,
    *,
    event_id: str | None = None,
) -> PolicyDecision:
    normalized = normalize_memory_entry(entry, event_id=event_id)

    if not normalized.content.strip():
        return PolicyDecision(
            accepted=False,
            prompt_eligible=False,
            target="memory.rejected",
            reason="empty memory content",
            normalized_entry=normalized,
        )

    if _is_model_reflection(normalized):
        normalized.prompt_eligible = False
        return PolicyDecision(
            accepted=True,
            prompt_eligible=False,
            target="memory.audit",
            reason="model reflection stored for audit only",
            normalized_entry=normalized,
        )

    if _is_model_proposed_semantic(normalized):
        return PolicyDecision(
            accepted=False,
            prompt_eligible=False,
            target="memory.rejected",
            reason="model-proposed semantic memory requires evidence and evaluator approval",
            normalized_entry=normalized,
        )

    if normalized.authority == "derived_summary" and not normalized.evidence_refs:
        return PolicyDecision(
            accepted=False,
            prompt_eligible=False,
            target="memory.rejected",
            reason="derived summary requires evidence refs",
            normalized_entry=normalized,
        )

    if normalized.memory_type == "semantic":
        prompt_eligible = normalized.authority in DURABLE_SEMANTIC_AUTHORITIES
        normalized.prompt_eligible = prompt_eligible
        return PolicyDecision(
            accepted=prompt_eligible,
            prompt_eligible=prompt_eligible,
            target="memory.semantic" if prompt_eligible else "memory.rejected",
            reason=(
                "durable semantic memory accepted"
                if prompt_eligible
                else "semantic memory authority is not durable"
            ),
            normalized_entry=normalized,
        )

    if normalized.memory_type == "procedural":
        prompt_eligible = normalized.authority == "system_config"
        normalized.prompt_eligible = prompt_eligible
        return PolicyDecision(
            accepted=prompt_eligible,
            prompt_eligible=prompt_eligible,
            target="memory.procedural" if prompt_eligible else "memory.rejected",
            reason=(
                "procedural memory accepted"
                if prompt_eligible
                else "procedural memory requires system_config authority"
            ),
            normalized_entry=normalized,
        )

    normalized.prompt_eligible = False
    return PolicyDecision(
        accepted=True,
        prompt_eligible=False,
        target="memory.audit",
        reason=f"{normalized.memory_type} memory stored for audit only",
        normalized_entry=normalized,
    )


def summarize_policy_decisions(decisions: list[PolicyDecision]) -> dict:
    return {
        "accepted": sum(1 for decision in decisions if decision.accepted),
        "rejected": sum(1 for decision in decisions if not decision.accepted),
        "prompt_eligible": sum(1 for decision in decisions if decision.prompt_eligible),
        "decisions": [decision.to_event() for decision in decisions],
    }


def normalize_memory_entry(entry: MemoryEntry, *, event_id: str | None = None) -> MemoryEntry:
    normalized = MemoryEntry(
        content=" ".join(entry.content.split()),
        source=(entry.source or "self").lower(),
        context=entry.context or [],
        intensity=entry.intensity,
        valence=entry.valence,
        significance=entry.significance,
        memory_type=(entry.memory_type or "semantic").lower(),
        source_type=(entry.source_type or "model").lower(),
        authority=(entry.authority or "model_proposed").lower(),
        prompt_eligible=bool(entry.prompt_eligible),
        evidence_refs=list(entry.evidence_refs or []),
    )
    if event_id and not normalized.evidence_refs and normalized.source_type in {"model", "runtime"}:
        normalized.evidence_refs.append({
            **evidence_ref(
                event_id=event_id,
                artifact="model_output",
                content=normalized.content,
            )
        })
    return normalized


def _is_model_reflection(entry: MemoryEntry) -> bool:
    return (
        entry.source_type == "model"
        and entry.authority in MODEL_AUTHORITIES
        and entry.memory_type in {"reflection", "episodic"}
    )


def _is_model_proposed_semantic(entry: MemoryEntry) -> bool:
    return (
        entry.source_type == "model"
        and entry.authority in MODEL_AUTHORITIES
        and entry.memory_type in {"semantic", "procedural"}
    )
