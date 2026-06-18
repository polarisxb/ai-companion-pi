"""Replay raw wake outputs through parser, grounding, repair, and quality gates."""

from __future__ import annotations

from dataclasses import dataclass

from .acceptance import decide_context_acceptance
from .context import load_wake_context
from .grounding import ConservativeGroundingEvaluator, summarize_grounding_evaluation
from .memory import JsonMemoryStore
from .parser import parse_wake_output
from .paths import CompanionPaths
from .provenance import content_hash
from .quality import build_quality_report
from .repair import GroundedOutputRepairer, summarize_repair_result


@dataclass
class ReplayResult:
    raw_output_hash: str
    trigger: str
    provider: str
    repair_enabled: bool
    grounding: dict
    repair: dict | None
    quality: dict
    quality_gate: dict
    parsed: dict

    def to_dict(self) -> dict:
        return {
            "ok": self.quality_gate.get("context_eligible") is True,
            "raw_output_hash": self.raw_output_hash,
            "trigger": self.trigger,
            "provider": self.provider,
            "repair_enabled": self.repair_enabled,
            "grounding": self.grounding,
            "repair": self.repair,
            "quality": self.quality,
            "quality_gate": self.quality_gate,
            "parsed": self.parsed,
            "committed": False,
        }


class ReplayRunner:
    """Run wake output gates without committing any runtime state."""

    def __init__(
        self,
        paths: CompanionPaths,
        *,
        memory_store: JsonMemoryStore | None = None,
        grounding_evaluator: ConservativeGroundingEvaluator | None = None,
        repairer: GroundedOutputRepairer | None = None,
    ):
        self.paths = paths
        self.memory_store = memory_store or JsonMemoryStore(paths.memory_store)
        self.grounding_evaluator = grounding_evaluator or ConservativeGroundingEvaluator()
        self.repairer = repairer or GroundedOutputRepairer()

    def replay_raw_output(
        self,
        raw_output: str,
        *,
        trigger: str = "replay",
        provider: str = "replay",
        repair_llm_client=None,
    ) -> ReplayResult:
        context = load_wake_context(self.paths, self.memory_store)
        parsed = parse_wake_output(raw_output)
        grounding = self.grounding_evaluator.evaluate(parsed, context=context)
        repair_result = None
        if repair_llm_client is not None and grounding.unsupported:
            repair_result = self.repairer.repair_if_needed(
                raw_output=raw_output,
                parsed=parsed,
                grounding=grounding,
                context=context,
                trigger=trigger,
                llm_client=repair_llm_client,
                grounding_evaluator=self.grounding_evaluator,
            )
            parsed = repair_result.parsed
            grounding = repair_result.grounding
        quality = build_quality_report(
            parsed,
            memory_count=len(parsed.memories),
            request_count=len(parsed.requests),
            request_error_count=0,
            memory_write_results=[],
            recent_journals=[],
            recent_memories=context.recent_memories,
            grounding_warnings=grounding.warnings,
        )
        quality_gate = decide_context_acceptance(quality)
        return ReplayResult(
            raw_output_hash=content_hash(raw_output),
            trigger=trigger,
            provider=provider,
            repair_enabled=repair_llm_client is not None,
            grounding=summarize_grounding_evaluation(grounding),
            repair=summarize_repair_result(repair_result),
            quality=quality,
            quality_gate=quality_gate,
            parsed={
                "sections": sorted(parsed.raw_sections.keys()),
                "journal_chars": len(parsed.journal.strip()),
                "memory_count": len(parsed.memories),
                "request_count": len(parsed.requests),
                "grounding_claim_count": len(parsed.grounding_claims),
            },
        )
