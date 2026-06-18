"""Grounded repair for wake outputs with unsupported continuity claims."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .context import WakeContext
from .grounding import (
    ConservativeGroundingEvaluator,
    GroundingEvaluation,
    render_grounding_ledger,
)
from .parser import ParsedWakeOutput, parse_wake_output
from .provenance import content_hash

REQUIRED_REPAIR_SECTIONS = ("JOURNAL", "COMPANION_STATE", "GROUNDING")


@dataclass
class RepairAttempt:
    attempt: int
    status: str
    reason: str
    output_hash: str
    grounding: dict
    raw_output: str = ""

    def to_event(self) -> dict:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "reason": self.reason,
            "output_hash": self.output_hash,
            "grounding": self.grounding,
        }


@dataclass
class RepairResult:
    attempted: bool
    succeeded: bool
    reason: str
    parsed: ParsedWakeOutput
    grounding: GroundingEvaluation
    original_grounding: GroundingEvaluation
    attempts: list[RepairAttempt] = field(default_factory=list)

    def to_event(self) -> dict | None:
        if not self.attempted:
            return None
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "reason": self.reason,
            "attempt_count": len(self.attempts),
            "original_grounding": _grounding_brief(self.original_grounding),
            "final_grounding": _grounding_brief(self.grounding),
            "attempts": [attempt.to_event() for attempt in self.attempts],
        }


class GroundedOutputRepairer:
    """Regenerate unsupported factual continuity claims before context commit."""

    def __init__(self, max_attempts: int = 1):
        self.max_attempts = max(0, max_attempts)

    def repair_if_needed(
        self,
        *,
        raw_output: str,
        parsed: ParsedWakeOutput,
        grounding: GroundingEvaluation,
        context: WakeContext,
        trigger: str,
        llm_client,
        grounding_evaluator: ConservativeGroundingEvaluator,
    ) -> RepairResult:
        if grounding.unsupported == 0 or self.max_attempts == 0:
            return RepairResult(
                attempted=False,
                succeeded=False,
                reason="not_needed",
                parsed=parsed,
                grounding=grounding,
                original_grounding=grounding,
            )

        attempts = []
        for attempt_number in range(1, self.max_attempts + 1):
            repair_prompt = render_repair_prompt(
                raw_output=raw_output,
                grounding=grounding,
                context=context,
                trigger=trigger,
            )
            repaired_raw = llm_client.generate(repair_prompt, context)
            repaired = parse_wake_output(repaired_raw)
            repaired_grounding = grounding_evaluator.evaluate(repaired, context=context)
            success, reason = _repair_status(repaired, repaired_grounding, grounding)
            attempts.append(RepairAttempt(
                attempt=attempt_number,
                status="succeeded" if success else "failed",
                reason=reason,
                output_hash=content_hash(repaired_raw),
                grounding=_grounding_brief(repaired_grounding),
                raw_output=repaired_raw,
            ))
            if success:
                return RepairResult(
                    attempted=True,
                    succeeded=True,
                    reason=reason,
                    parsed=repaired,
                    grounding=repaired_grounding,
                    original_grounding=grounding,
                    attempts=attempts,
                )

        return RepairResult(
            attempted=True,
            succeeded=False,
            reason="repair_exhausted",
            parsed=parsed,
            grounding=grounding,
            original_grounding=grounding,
            attempts=attempts,
        )


def render_repair_prompt(
    *,
    raw_output: str,
    grounding: GroundingEvaluation,
    context: WakeContext,
    trigger: str,
) -> str:
    unsupported = "\n".join(
        _unsupported_claim_line(decision)
        for decision in grounding.decisions
        if decision.action == "unsupported"
    )
    return f"""=== REPAIR TASK ===
The previous wake output contained unsupported factual continuity claims.
Rewrite the complete wake output so it can pass the grounding gate.

Trigger: {trigger}

Rules:
- Return the same section format: JOURNAL, SIGNAL, COMPANION_STATE, CONTEXT_DELTA, GROUNDING, MEMORY, REQUESTS.
- Keep warm companion expression, but remove or soften factual claims that are not directly supported by GROUNDING LEDGER.
- Do not add new facts, user preferences, past events, stable relationship facts, memories, or requests.
- Present self-state, uncertainty, current intent, and gentle warmth may remain.
- Human-visible prose and JSON string values must be Simplified Chinese.
- Section headers, JSON keys, and sentinel values must remain English.
- Recompute ===GROUNDING=== for the rewritten output. If no factual continuity claims remain, write NO_GROUNDING_CLAIMS.

=== GROUNDING LEDGER ===
{render_grounding_ledger(context)}

=== UNSUPPORTED CLAIMS TO REMOVE OR SOFTEN ===
{unsupported or "(none)"}

=== ORIGINAL OUTPUT ===
{raw_output}
"""


def summarize_repair_result(repair: RepairResult | None) -> dict | None:
    return repair.to_event() if repair else None


def _repair_status(
    parsed: ParsedWakeOutput,
    grounding: GroundingEvaluation,
    original_grounding: GroundingEvaluation,
) -> tuple[bool, str]:
    missing = [
        section for section in REQUIRED_REPAIR_SECTIONS
        if section not in parsed.raw_sections
    ]
    if missing:
        return False, "missing required repaired sections: " + ", ".join(missing)
    if not parsed.journal.strip():
        return False, "missing repaired journal"
    if grounding.unsupported:
        return False, "repaired output still has unsupported grounded claims"
    retained = _retained_unsupported_claims(parsed, original_grounding)
    if retained:
        return False, "repaired output retained unsupported claim text"
    return True, "grounding_repaired"


def _grounding_brief(grounding: GroundingEvaluation) -> dict:
    return {
        "supported": grounding.supported,
        "unsupported": grounding.unsupported,
        "ignored": grounding.ignored,
        "unsupported_claims": [
            decision.to_event()
            for decision in grounding.decisions
            if decision.action == "unsupported"
        ],
    }


def _unsupported_claim_line(decision) -> str:
    refs = ", ".join(decision.evidence_refs) or "(none)"
    return (
        f"- type={decision.claim_type}; claim={decision.claim}; "
        f"evidence_refs={refs}; reason={decision.reason}"
    )


def _retained_unsupported_claims(
    parsed: ParsedWakeOutput,
    original_grounding: GroundingEvaluation,
) -> list[str]:
    visible_key = _support_key(_visible_output_text(parsed))
    retained = []
    for decision in original_grounding.decisions:
        if decision.action != "unsupported":
            continue
        claim_key = _support_key(decision.claim)
        if claim_key and claim_key in visible_key:
            retained.append(decision.claim)
    return retained


def _visible_output_text(parsed: ParsedWakeOutput) -> str:
    memory_text = "\n".join(memory.content for memory in parsed.memories)
    request_text = "\n".join(
        "\n".join([request.title, request.body])
        for request in parsed.requests
    )
    return "\n".join([
        parsed.journal,
        json.dumps(parsed.companion_state, ensure_ascii=False, sort_keys=True),
        json.dumps(parsed.context_delta, ensure_ascii=False, sort_keys=True),
        memory_text,
        request_text,
    ])


def _support_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())
