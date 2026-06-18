"""Evidence grounding for model-visible continuity claims."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .context import WakeContext
from .context_capsule import render_context_capsule
from .parser import ParsedWakeOutput
from .provenance import content_hash

REQUIRES_EVIDENCE_TYPES = {
    "current_fact",
    "current_context",
    "current_event",
    "current_focus",
    "current_state",
    "past_event",
    "past",
    "present_context",
    "present_event",
    "memory_reference",
    "remembered_fact",
    "user_preference",
    "human_preference",
    "user_current_state",
    "human_current_state",
    "preference",
    "relationship_fact",
    "relationship_state",
    "stable_fact",
    "user_fact",
    "human_fact",
}

CLAIM_TYPE_ALIASES = {
    "human_preference": "user_preference",
    "human_current_state": "user_current_state",
    "human_fact": "user_fact",
    "current_event": "current_context",
    "current_focus": "current_context",
    "present_context": "current_context",
    "present_event": "current_context",
}

EVIDENCE_REF_ALIASES = {
    "who_human": "context.who_human",
    "human": "context.who_human",
    "now": "context.now",
    "current_context": "context.now",
    "context": "context.now",
    "capsule": "context.capsule",
    "context_capsule": "context.capsule",
}

MIN_SUPPORTED_TEXT_CHARS = 6
MIN_SHARED_CJK_CHARS = 6
CJK_CHAR_OVERLAP_THRESHOLD = 0.62


@dataclass
class GroundingEvidence:
    ref_id: str
    content: str
    source_type: str
    authority: str

    def to_prompt_line(self) -> str:
        return f"- [{self.ref_id}] {self.content}"

    def to_event(self) -> dict:
        return {
            "ref_id": self.ref_id,
            "source_type": self.source_type,
            "authority": self.authority,
            "content_hash": content_hash(self.content),
        }


@dataclass
class GroundingClaimDecision:
    action: str
    reason: str
    claim_type: str
    claim: str
    evidence_refs: list[str]
    matched_refs: list[str]

    def to_event(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "claim_type": self.claim_type,
            "claim_excerpt": _excerpt(self.claim),
            "content_hash": content_hash(self.claim),
            "evidence_refs": self.evidence_refs,
            "matched_refs": self.matched_refs,
        }


@dataclass
class GroundingEvaluation:
    supported: int
    unsupported: int
    ignored: int
    warnings: list[str]
    decisions: list[GroundingClaimDecision]
    evidence: list[GroundingEvidence]

    def to_event(self) -> dict:
        return {
            "supported": self.supported,
            "unsupported": self.unsupported,
            "ignored": self.ignored,
            "warnings": self.warnings,
            "decisions": [decision.to_event() for decision in self.decisions],
            "evidence": [item.to_event() for item in self.evidence],
        }


class ConservativeGroundingEvaluator:
    """Validate model-declared factual continuity claims against prompt evidence."""

    def evaluate(
        self,
        parsed: ParsedWakeOutput,
        *,
        context: WakeContext,
    ) -> GroundingEvaluation:
        evidence = build_grounding_ledger(context)
        evidence_by_ref = {item.ref_id: item for item in evidence}
        decisions = [
            self._evaluate_claim(claim, evidence_by_ref)
            for claim in parsed.grounding_claims
        ]
        warnings = [
            "unsupported grounded claim"
            for decision in decisions
            if decision.action == "unsupported"
        ]
        return GroundingEvaluation(
            supported=sum(1 for decision in decisions if decision.action == "supported"),
            unsupported=sum(1 for decision in decisions if decision.action == "unsupported"),
            ignored=sum(1 for decision in decisions if decision.action == "ignored"),
            warnings=warnings,
            decisions=decisions,
            evidence=evidence,
        )

    def _evaluate_claim(
        self,
        claim: dict,
        evidence_by_ref: dict[str, GroundingEvidence],
    ) -> GroundingClaimDecision:
        content = str(claim.get("claim", "")).strip()
        claim_type = _canonical_claim_type(claim.get("claim_type", "unspecified"))
        evidence_refs = [
            _canonical_ref(ref)
            for ref in claim.get("evidence_refs", [])
            if str(ref).strip()
        ]

        if claim_type not in REQUIRES_EVIDENCE_TYPES:
            return GroundingClaimDecision(
                action="ignored",
                reason="claim type does not require evidence",
                claim_type=claim_type,
                claim=content,
                evidence_refs=evidence_refs,
                matched_refs=[],
            )

        if not evidence_refs:
            return GroundingClaimDecision(
                action="unsupported",
                reason="claim requires evidence refs",
                claim_type=claim_type,
                claim=content,
                evidence_refs=[],
                matched_refs=[],
            )

        matched_refs = [
            ref
            for ref in evidence_refs
            if ref in evidence_by_ref
            if _supports_claim(content, evidence_by_ref[ref].content)
        ]
        if matched_refs:
            return GroundingClaimDecision(
                action="supported",
                reason="claim matched cited prompt evidence",
                claim_type=claim_type,
                claim=content,
                evidence_refs=evidence_refs,
                matched_refs=matched_refs,
            )

        return GroundingClaimDecision(
            action="unsupported",
            reason="cited evidence did not support claim",
            claim_type=claim_type,
            claim=content,
            evidence_refs=evidence_refs,
            matched_refs=[],
        )


def build_grounding_ledger(context: WakeContext) -> list[GroundingEvidence]:
    evidence = [
        GroundingEvidence(
            ref_id="context.who_human",
            content=context.who_human,
            source_type="system",
            authority="system_config",
        ),
        GroundingEvidence(
            ref_id="context.now",
            content=context.now,
            source_type="system",
            authority="system_config",
        ),
    ]
    capsule = render_context_capsule(context.context_capsule)
    if capsule and capsule != "(empty)":
        evidence.append(GroundingEvidence(
            ref_id="context.capsule",
            content=capsule,
            source_type="derived",
            authority="prompt_read_model",
        ))
    for memory in context.recent_memories:
        if memory.get("prompt_eligible") is not True:
            continue
        memory_id = str(memory.get("id") or "unknown")
        evidence.append(GroundingEvidence(
            ref_id=f"memory.{memory_id}",
            content=str(memory.get("content", "")),
            source_type=str(memory.get("source_type", "unknown")),
            authority=str(memory.get("authority", "unknown")),
        ))
    return [
        item for item in evidence
        if item.content and _support_key(item.content)
    ]


def render_grounding_ledger(context: WakeContext) -> str:
    evidence = build_grounding_ledger(context)
    if not evidence:
        return "(none)"
    return "\n".join(item.to_prompt_line() for item in evidence)


def summarize_grounding_evaluation(evaluation: GroundingEvaluation | None) -> dict | None:
    return evaluation.to_event() if evaluation else None


def _canonical_ref(value: str) -> str:
    ref = str(value).strip()
    return EVIDENCE_REF_ALIASES.get(ref, ref)


def _canonical_claim_type(value) -> str:
    claim_type = str(value or "unspecified").strip().lower()
    return CLAIM_TYPE_ALIASES.get(claim_type, claim_type)


def _supports_claim(claim: str, evidence: str) -> bool:
    candidate = _support_key(claim)
    haystack = _support_key(evidence)
    if len(candidate) < MIN_SUPPORTED_TEXT_CHARS:
        return False
    if candidate in haystack:
        return True
    return _has_cjk_overlap(claim, evidence)


def _support_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def _has_cjk_overlap(claim: str, evidence: str) -> bool:
    claim_chars = _cjk_char_set(claim)
    evidence_chars = _cjk_char_set(evidence)
    if len(claim_chars) < MIN_SHARED_CJK_CHARS:
        return False
    shared = len(claim_chars & evidence_chars)
    overlap = shared / len(claim_chars)
    return shared >= MIN_SHARED_CJK_CHARS and overlap >= CJK_CHAR_OVERLAP_THRESHOLD


def _cjk_char_set(value: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]", value or ""))


def _excerpt(value: str, limit: int = 80) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[:limit - 1] + "..."
