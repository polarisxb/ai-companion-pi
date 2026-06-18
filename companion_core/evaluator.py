"""Conservative evaluator for durable memory proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import WakeContext
from .memory import MemoryEntry
from .memory_policy import normalize_memory_entry
from .provenance import content_hash, evidence_ref

MIN_SUPPORTED_TEXT_CHARS = 8
USER_SOURCES = {"user", "human"}
NEGATION_MARKERS = (
    "不要",
    "不能",
    "不应",
    "不应该",
    "禁止",
    "donot",
    "dont",
    "not",
    "never",
)


@dataclass
class MemoryEvaluation:
    action: str
    reason: str
    classification: str
    entry: MemoryEntry
    evidence_refs: list[dict]

    def to_event(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "classification": self.classification,
            "content_hash": content_hash(self.entry.content),
            "memory_type": self.entry.memory_type,
            "source": self.entry.source,
            "source_type": self.entry.source_type,
            "authority": self.entry.authority,
            "evidence_refs": self.evidence_refs,
        }


class ConservativeMemoryEvaluator:
    """Approve only model-proposed user semantic memories with exact evidence."""

    def evaluate(
        self,
        entry: MemoryEntry,
        *,
        context: WakeContext,
        event_id: str | None = None,
    ) -> MemoryEvaluation:
        normalized = normalize_memory_entry(entry, event_id=event_id)
        if not _needs_evaluation(normalized):
            return MemoryEvaluation(
                action="unchanged",
                reason="proposal does not require semantic evaluator",
                classification=normalized.memory_type,
                entry=normalized,
                evidence_refs=[],
            )

        if normalized.source not in USER_SOURCES:
            return MemoryEvaluation(
                action="rejected",
                reason="only user-sourced semantic claims can be evaluator-approved",
                classification="semantic",
                entry=normalized,
                evidence_refs=[],
            )

        evidence = _matching_evidence(normalized.content, context, event_id)
        if not evidence:
            return MemoryEvaluation(
                action="rejected",
                reason="semantic claim is not exactly supported by trusted context",
                classification="semantic",
                entry=normalized,
                evidence_refs=[],
            )

        approved = MemoryEntry(
            content=normalized.content,
            source="human",
            context=normalized.context,
            intensity=normalized.intensity,
            valence=normalized.valence,
            significance=normalized.significance,
            memory_type="semantic",
            source_type="user",
            authority="evaluator_approved",
            prompt_eligible=True,
            evidence_refs=evidence,
        )
        return MemoryEvaluation(
            action="approved",
            reason="semantic claim exactly matched trusted user context",
            classification="semantic",
            entry=approved,
            evidence_refs=evidence,
        )


def summarize_memory_evaluations(evaluations: list[MemoryEvaluation]) -> dict:
    return {
        "approved": sum(1 for evaluation in evaluations if evaluation.action == "approved"),
        "rejected": sum(1 for evaluation in evaluations if evaluation.action == "rejected"),
        "unchanged": sum(1 for evaluation in evaluations if evaluation.action == "unchanged"),
        "decisions": [evaluation.to_event() for evaluation in evaluations],
    }


def _needs_evaluation(entry: MemoryEntry) -> bool:
    return (
        entry.memory_type == "semantic"
        and entry.source_type == "model"
        and entry.authority == "model_proposed"
    )


def _matching_evidence(content: str, context: WakeContext, event_id: str | None) -> list[dict]:
    candidate = _support_key(content)
    if len(candidate) < MIN_SUPPORTED_TEXT_CHARS:
        return []

    matches = []
    for artifact, text in _trusted_context_sources(context):
        if _is_supported_candidate(candidate, text):
            matches.append(evidence_ref(
                event_id=event_id,
                artifact=artifact,
                content=text,
            ))
    return matches


def _trusted_context_sources(context: WakeContext) -> list[tuple[str, str]]:
    sources = [
        ("context.who_human", context.who_human),
        ("context.now", context.now),
    ]
    for memory in context.recent_memories:
        if memory.get("prompt_eligible") is True:
            sources.append((f"memory.{memory.get('id', 'unknown')}", str(memory.get("content", ""))))
    return [(name, text) for name, text in sources if text]


def _support_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def _is_supported_candidate(candidate: str, text: str) -> bool:
    haystack = _support_key(text)
    start = haystack.find(candidate)
    while start >= 0:
        prefix = haystack[max(0, start - 12):start]
        if not any(marker in prefix for marker in NEGATION_MARKERS):
            return True
        start = haystack.find(candidate, start + 1)
    return False
