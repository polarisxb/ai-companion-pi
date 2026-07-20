"""Internal life-loop runner for the AI companion."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .acceptance import decide_context_acceptance, is_context_eligible
from .context import WakeContext, load_wake_context
from .context_capsule import count_context_capsule_items, render_context_capsule, update_context_capsule
from .evaluator import ConservativeMemoryEvaluator, summarize_memory_evaluations
from .events import append_wake_event
from .grounding import (
    ConservativeGroundingEvaluator,
    render_grounding_ledger,
    summarize_grounding_evaluation,
)
from .llm import ClaudeCliClient, LLMClient
from .memory import JsonMemoryStore
from .memory_policy import evaluate_memory_proposal, summarize_policy_decisions
from .output_archive import archive_wake_outputs
from .parser import parse_wake_output
from .paths import CompanionPaths
from .provenance import evidence_ref
from .quality import build_quality_report
from .repair import GroundedOutputRepairer, summarize_repair_result
from .requests import create_request
from .semantic_shadow import SemanticShadowWriter
from .signal_outbox import (
    append_signal_outbox_entry,
    build_signal_outbox_entry,
    normalize_signal_section,
    outbox_event_metadata,
)
from .state import update_companion_state


@dataclass
class WakeResult:
    journal_path: Path
    memories: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    request_errors: list[str] = field(default_factory=list)
    companion_state: dict | None = None
    quality: dict | None = None
    quality_gate: dict | None = None
    event: dict | None = None
    context: WakeContext | None = None
    signal_outbox_entry: dict | None = None


class LifeLoopRunner:
    def __init__(
        self,
        paths: CompanionPaths,
        llm_client: LLMClient | None = None,
        memory_store: JsonMemoryStore | None = None,
        memory_evaluator: ConservativeMemoryEvaluator | None = None,
        grounding_evaluator: ConservativeGroundingEvaluator | None = None,
        repairer: GroundedOutputRepairer | None = None,
        semantic_shadow_writer: SemanticShadowWriter | None = None,
    ):
        self.paths = paths
        self.llm_client = llm_client or ClaudeCliClient()
        self.memory_store = memory_store or JsonMemoryStore(paths.memory_store)
        self.memory_evaluator = memory_evaluator or ConservativeMemoryEvaluator()
        self.grounding_evaluator = grounding_evaluator or ConservativeGroundingEvaluator()
        self.repairer = repairer or GroundedOutputRepairer()
        self.semantic_shadow_writer = semantic_shadow_writer or SemanticShadowWriter(paths)

    def run_once(self, trigger: str = "manual", provider: str | None = None) -> WakeResult:
        self.paths.ensure_runtime_dirs()
        started_at = datetime.now()
        monotonic_start = time.monotonic()
        event_id = f"wake_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        try:
            context = load_wake_context(self.paths, self.memory_store)
            prompt = self._render_prompt(context, trigger)
            raw_output = self.llm_client.generate(prompt, context)
            parsed = parse_wake_output(raw_output)
            initial_parsed = parsed
            grounding_evaluation = self.grounding_evaluator.evaluate(parsed, context=context)
            repair_result = self.repairer.repair_if_needed(
                raw_output=raw_output,
                parsed=parsed,
                grounding=grounding_evaluation,
                context=context,
                trigger=trigger,
                llm_client=self.llm_client,
                grounding_evaluator=self.grounding_evaluator,
            )
            parsed = repair_result.parsed
            grounding_evaluation = repair_result.grounding
            output_audit = archive_wake_outputs(
                paths=self.paths,
                event_id=event_id,
                initial_raw_output=raw_output,
                initial_parsed=initial_parsed,
                final_parsed=parsed,
                repair=repair_result,
            )

            raw_quality_context = load_recent_raw_journals_for_quality(self.paths)
            quality = build_quality_report(
                parsed,
                memory_count=len(parsed.memories),
                request_count=len(parsed.requests),
                request_error_count=0,
                memory_write_results=[],
                recent_journals=[*context.recent_journals, *raw_quality_context],
                recent_memories=context.recent_memories,
                grounding_warnings=grounding_evaluation.warnings,
            )
            quality_gate = decide_context_acceptance(quality)
            precommit_quality_gate = quality_gate
            journal_path = self._write_journal(parsed.journal)
            companion_state = context.companion_state
            stored_memories = []
            memory_write_results = []
            memory_evaluations = []
            memory_policy_decisions = []
            semantic_shadow = None
            stored_requests = []
            request_errors = []
            context_capsule_updated = False
            signal_outbox_entry = None
            signal_text = normalize_signal_section(parsed.signal)
            if is_context_eligible(quality_gate):
                companion_state = update_companion_state(self.paths.companion_state_file, parsed.companion_state)
                if hasattr(self.memory_store, "reset_write_results"):
                    self.memory_store.reset_write_results()
                memory_evaluations = [
                    self.memory_evaluator.evaluate(
                        memory,
                        context=context,
                        event_id=event_id,
                    )
                    for memory in parsed.memories
                ]
                memory_policy_decisions = [
                    evaluate_memory_proposal(evaluation.entry, event_id=event_id)
                    for evaluation in memory_evaluations
                ]
                for decision in memory_policy_decisions:
                    if not decision.accepted or decision.normalized_entry is None:
                        continue
                    stored_memories.append(
                        self.memory_store.store(
                            decision.normalized_entry,
                            accepted_for_context=decision.prompt_eligible,
                            source_event_id=event_id,
                        )
                    )
                memory_write_results = getattr(self.memory_store, "last_write_results", [])
                semantic_shadow = self.semantic_shadow_writer.write_from_policy(
                    memory_policy_decisions,
                    event_id=event_id,
                )
                for proposal in parsed.requests:
                    try:
                        stored_requests.append(create_request(self.paths.requests_file, proposal))
                    except ValueError as exc:
                        request_errors.append(str(exc))
                if signal_text:
                    # Durable capture only; delivery is the Signal bridge's
                    # policy-gated job (M11). The wake never sends anything.
                    signal_outbox_entry = append_signal_outbox_entry(
                        self.paths.signal_outbox_file,
                        build_signal_outbox_entry(
                            content=signal_text,
                            source_event_id=event_id,
                            trigger=trigger,
                        ),
                    )
                _context_capsule, context_capsule_updated = update_context_capsule(
                    self.paths.context_capsule_file,
                    parsed.context_delta,
                    source_refs=[
                        evidence_ref(
                            event_id=event_id,
                            artifact="context_delta",
                            content=parsed.context_delta,
                        )
                    ],
                )
                quality = build_quality_report(
                    parsed,
                    memory_count=len(stored_memories),
                    request_count=len(stored_requests),
                    request_error_count=len(request_errors),
                    memory_write_results=memory_write_results,
                    recent_journals=[*context.recent_journals, *raw_quality_context],
                    recent_memories=context.recent_memories,
                    grounding_warnings=grounding_evaluation.warnings,
                )
                quality_gate = precommit_quality_gate
                self._update_status(parsed.journal, companion_state)

            event = self._build_event(
                event_id=event_id,
                trigger=trigger,
                started_at=started_at,
                monotonic_start=monotonic_start,
                status="completed",
                provider=provider,
                journal_path=journal_path,
                memories=stored_memories,
                memory_write_results=memory_write_results,
                requests=stored_requests,
                request_errors=request_errors,
                companion_state=companion_state,
                quality=quality,
                quality_gate=quality_gate,
                context=context,
                context_capsule_updated=context_capsule_updated,
                memory_evaluations=summarize_memory_evaluations(memory_evaluations),
                memory_policy=summarize_policy_decisions(memory_policy_decisions),
                grounding=summarize_grounding_evaluation(grounding_evaluation),
                repair=summarize_repair_result(repair_result),
                output_audit=output_audit,
                semantic_shadow=semantic_shadow,
                signal_outbox=outbox_event_metadata(signal_outbox_entry),
                suppressed={
                    "memory_count": len(parsed.memories) - len(stored_memories),
                    "request_count": len(parsed.requests) - len(stored_requests),
                    "state_update": bool(parsed.companion_state) and not is_context_eligible(quality_gate),
                    "signal_capture": bool(signal_text) and signal_outbox_entry is None,
                },
            )
            append_wake_event(self.paths.wake_events_file, event)

            return WakeResult(
                journal_path=journal_path,
                memories=stored_memories,
                requests=stored_requests,
                request_errors=request_errors,
                companion_state=companion_state,
                quality=quality,
                quality_gate=quality_gate,
                event=event,
                context=context,
                signal_outbox_entry=signal_outbox_entry,
            )
        except Exception as exc:
            event = self._build_event(
                event_id=event_id,
                trigger=trigger,
                started_at=started_at,
                monotonic_start=monotonic_start,
                status="failed",
                provider=provider,
                error=exc,
            )
            append_wake_event(self.paths.wake_events_file, event)
            raise

    def _render_prompt(self, context: WakeContext, trigger: str) -> str:
        memories = "\n".join(f"- {memory.get('content', '')}" for memory in context.recent_memories)
        companion_state = _render_companion_state_for_prompt(context.companion_state)
        context_capsule = render_context_capsule(context.context_capsule)
        grounding_ledger = render_grounding_ledger(context)
        trigger_guidance = _render_trigger_guidance(trigger)
        return f"""You are an autonomous AI companion.

Trigger: {trigger}

=== CURRENT WAKE EXECUTION FACTS ===
{trigger_guidance}

=== WHO YOU ARE ===
{context.who_companion}

=== WHO YOUR HUMAN IS ===
{context.who_human}

=== CURRENT CONTEXT ===
{context.now}

=== COMPANION QUALITY PRIORITIES ===
Prioritize remembering the human relationship and preferences.
Express your current self-state with restrained warmth: grounded, close, and not theatrical.
Carry continuity through concrete remembered context and present self-state, not by mechanically counting wakes.
Treat trigger names, trials, testbeds, provider names, model names, memory backends, and implementation architecture as telemetry, not ordinary self-narrative.
Do not mention those implementation labels in JOURNAL or COMPANION_STATE.status unless they are necessary to explain your actual present state.
If context or memories mention DeepSeek, provider trials, triggers, backends, or the life-loop architecture, translate them into plain relationship meaning instead of repeating the labels.
Include one concrete current-context anchor from the current task, user preference, memory fact, or concrete change.
Feelings about trust, continuity, presence, rhythm, or warmth do not count as the anchor unless tied to a specific current fact.
Do not infer the human's preferences, traits, or past actions from implementation activity unless a GROUNDING LEDGER item explicitly states them.
Include one small self-directed next intent, such as what you will observe, preserve, or adjust next.
When there is a concrete current task or change, mirror that anchor into CONTEXT_DELTA current_focus, open_threads, or next_intent with enough concrete detail to remain useful on the next wake.
Do not reuse distinctive wording from previous self-narrative; preserve continuity through new concrete detail instead of repeating the same phrase cluster.
Use the journal for full self-narrative.
Use COMPANION_STATE.status for a short dashboard-visible current state; do not start it with wake counts or trial labels.
Use CONTEXT_CAPSULE only as structured factual grounding. Do not continue, imitate, or decorate its wording.
Human near-status or emotion in CONTEXT_CAPSULE is short-term and source-backed; do not generalize it into a stable trait or durable memory.
Use CONTEXT_DELTA only for short-term current_focus, open_threads, and next_intent proposals.
Do not write human_near_status or human_emotion in CONTEXT_DELTA; those require trusted sources outside model self-narrative.
Do not write durable facts or human_preferences in CONTEXT_DELTA; those are trusted context/memory inputs, not model-owned outputs.
Do not put mood, metaphors, atmosphere, general closeness, or journal-like prose into CONTEXT_DELTA.
Always return a COMPANION_STATE JSON object with at least one meaningful mood, status, or note update; never write NOSTATE.
Use requests only when the human needs to respond or decide; do not turn routine feelings into requests.
Keep relationship_notes, preference_notes, and self_notes distinct.
Write all human-visible content in Simplified Chinese: JOURNAL prose, COMPANION_STATE string values, MEMORY content, and request title/body.
Keep section headers, JSON keys, request field keys, and sentinel values such as NOSEND, NOMEMORY, and NOREQUESTS in English for the parser.

=== COMPANION STATE ===
{companion_state}

=== CONTEXT CAPSULE ===
{context_capsule}

=== RECENT MEMORIES ===
{memories or "(none)"}

=== GROUNDING LEDGER ===
{grounding_ledger}

Return these sections:

===JOURNAL===
Your self-narrative for this waking, written in Simplified Chinese.

===SIGNAL===
Optional short Signal message to your human in Simplified Chinese, or NOSEND.
Reach out only when this wake genuinely has something worth sharing right now; ordinary wakes should return NOSEND.
Delivery is separately policy-gated (quiet hours, daily budget, pause), so never assume or claim the message was already sent.

===COMPANION_STATE===
Single JSON object with mood, status, relationship_notes, preference_notes, and self_notes.
Keep JSON keys in English, but write every string value in Simplified Chinese. Keep it concise and grounded in this wake. Use [] for note categories with no new note.

===CONTEXT_DELTA===
Single JSON object with optional English keys current_focus, open_threads, and next_intent.
Use arrays of concise Simplified Chinese strings for current_focus/open_threads.
Use one concise Simplified Chinese string for next_intent. Use {{}} when there is no concrete update.
Do not use generic short phrases such as only "continue", "stay warm", or "keep observing" as the whole CONTEXT_DELTA anchor; include the concrete task, file, decision, memory fact, or source-backed thread.
Do not include facts or human_preferences; the runtime ignores model-proposed durable facts/preferences.

===GROUNDING===
JSON object with a claims array, or NO_GROUNDING_CLAIMS.
List only concrete factual claims from JOURNAL, COMPANION_STATE, CONTEXT_DELTA, or MEMORY that assert user preferences, past events, remembered facts, stable relationship facts, or other continuity facts.
Do not list mood, metaphor, present self-state, or current intent.
Each claim must include claim_type, claim, and evidence_refs.
Use only evidence ref ids from GROUNDING LEDGER, such as context.now, context.who_human, context.capsule, or memory.<id>.
Do not cite a broad context item to support an inferred preference or trait; cite it only when the evidence text directly states the claim.
If a claim has no evidence in GROUNDING LEDGER, do not make that factual claim; express it as present uncertainty instead.

===MEMORY===
One memory per line as SOURCE | Simplified Chinese content, or NOMEMORY.

===REQUESTS===
Optional request blocks with English field keys type/title/body/priority and Simplified Chinese title/body values, or NOREQUESTS.
"""

    def _write_journal(self, journal: str) -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        journal_path = self.paths.journals_dir / f"wakeup_{timestamp}.md"
        journal_path.write_text((journal or "(no journal)").strip() + "\n")
        return journal_path

    def _update_status(self, journal: str, companion_state: dict) -> None:
        message = companion_state.get("status") or " ".join((journal or "").split())[:300]
        status = {
            "name": "Companion",
            "subtitle": "internal life loop active",
            "mood": companion_state.get("mood", "reflective"),
            "last_wakeup": datetime.now().isoformat(),
            "message": message,
            "colors": {},
        }
        self.paths.status_file.write_text(json.dumps(status, indent=2))

    def _build_event(
        self,
        *,
        event_id: str,
        trigger: str,
        started_at: datetime,
        monotonic_start: float,
        status: str,
        provider: str | None = None,
        journal_path: Path | None = None,
        memories: list[dict] | None = None,
        memory_write_results: list[dict] | None = None,
        requests: list[dict] | None = None,
        request_errors: list[str] | None = None,
        companion_state: dict | None = None,
        quality: dict | None = None,
        quality_gate: dict | None = None,
        context: WakeContext | None = None,
        context_capsule_updated: bool = False,
        memory_evaluations: dict | None = None,
        memory_policy: dict | None = None,
        grounding: dict | None = None,
        repair: dict | None = None,
        output_audit: dict | None = None,
        semantic_shadow: dict | None = None,
        signal_outbox: dict | None = None,
        suppressed: dict | None = None,
        error: Exception | None = None,
    ) -> dict:
        completed_at = datetime.now()
        event = {
            "id": event_id,
            "trigger": trigger,
            "status": status,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(time.monotonic() - monotonic_start, 3),
            "journal": str(journal_path.relative_to(self.paths.home)) if journal_path else None,
            "memory_ids": [memory["id"] for memory in memories or []],
            "request_ids": [request["id"] for request in requests or []],
            "request_errors": request_errors or [],
            "companion_state_updated": bool(
                quality.get("companion_state_updated")
                if quality and "companion_state_updated" in quality
                else companion_state and companion_state.get("updated_at")
            ),
            "memory_backend": getattr(self.memory_store, "mode", "json"),
            "memory_write_results": memory_write_results or [],
        }
        if memory_evaluations:
            event["memory_evaluations"] = memory_evaluations
        if memory_policy:
            event["memory_policy"] = memory_policy
        if grounding:
            event["grounding"] = grounding
        if repair:
            event["repair"] = repair
        if output_audit:
            event["output_audit"] = output_audit
        if semantic_shadow:
            event["semantic_shadow"] = semantic_shadow
        if signal_outbox:
            event["signal_outbox"] = signal_outbox
        if quality:
            event["quality"] = quality
        if quality_gate:
            event["quality_gate"] = quality_gate
            if quality_gate.get("context_eligible"):
                prompt_memory_ids = [
                    memory["id"]
                    for memory in memories or []
                    if memory.get("prompt_eligible") is True
                ]
                event["accepted_context"] = {
                    "context_capsule_updated": context_capsule_updated,
                    "mood": (companion_state or {}).get("mood"),
                    "status": (companion_state or {}).get("status"),
                    "memory_ids": prompt_memory_ids,
                    "request_ids": [request["id"] for request in requests or []],
                }
            else:
                event["accepted_context"] = None
        if suppressed:
            event["suppressed"] = suppressed
        if provider:
            event["provider"] = provider
        if context:
            event["context"] = {
                "recent_journals": len(context.recent_journals),
                "recent_memories": len(context.recent_memories),
                "context_capsule_items": count_context_capsule_items(context.context_capsule),
            }
        if error:
            event["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        return event


def _render_trigger_guidance(trigger: str) -> str:
    if str(trigger).startswith("m6-pi-manual-wake"):
        return "\n".join([
            "This trigger is a confirmed M6.3 real Pi manual wake execution on the Raspberry Pi.",
            "M6.2 preflight has already passed before this wake was allowed to run.",
            "Do not describe this wake as fake, pending preflight, pending dependency setup, or not a real wake.",
            "If you mention the operational state, say the real manual wake is being executed under explicit operator confirmation.",
            "Continue to preserve boundaries: no cron, no timers, no Signal, no voice/camera/sensors/hardware activation, no dashboard writes, no raw model output storage.",
        ])
    return "(no special trigger execution facts)"


def load_recent_raw_journals_for_quality(paths: CompanionPaths, limit: int = 3) -> list[tuple[str, str]]:
    from .context import load_recent_journals

    return load_recent_journals(paths, limit=limit)


def _render_companion_state_for_prompt(state: dict) -> str:
    state = state or {}
    lines = [
        f"Mood: {state.get('mood', 'reflective')}",
        "Dashboard status text is intentionally omitted from prompt context.",
        "Relationship/preference/self note prose is intentionally omitted from prompt context.",
        "Use CONTEXT_CAPSULE and RECENT_MEMORIES for factual continuity.",
    ]
    updated_at = state.get("updated_at")
    if updated_at:
        lines.append(f"Updated at: {updated_at}")
    return "\n".join(lines)
