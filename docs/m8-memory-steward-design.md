# M8 Memory Steward And Dialogue Humanity Design

Status: M8.0 design ready for implementation planning
Last updated: 2026-06-19

## Decision

M8 hardens the companion's memory and ordinary text dialogue after M7.6 text
dialogue freeze. It is not a scheduler, voice, Signal, or hardware milestone.

The M8 direction is:

```text
human /chat turn
  -> Dialogue Persona replies naturally
  -> Memory Steward reviews the completed turn in the background
  -> Memory Policy Gate validates the steward decision
  -> Memory Ledger records accepted, quarantined, rejected, and audit-only items
  -> Retrieval Assembler selects a small relevant memory set for future dialogue
```

The human should not become the routine memory administrator. The companion
should manage ordinary memory herself through an internal Memory Steward
personality. Human review is reserved for ambiguous, sensitive, conflicting, or
relationship-defining memories.

## Why M8 Is Memory Steward First

M7 made direct text dialogue real. The remaining gap is continuity: the
companion can talk in the moment, but long-term memory formation is still
proposal-heavy and intentionally non-authoritative.

If scheduler, voice, or Signal run before memory hardening, the system will
increase contact frequency without improving continuity. M8 should make the
text companion remember better, forget safely, and retrieve naturally before
more channels are added.

## Baseline From M7

Required baseline:

```text
life-loop/m7_dialogue_freeze_report.json
ok = true
milestone = M7.6
recommendation = m7_text_dialogue_frozen
stop_reasons = []
```

M8 keeps:

- `provider=deepseek` by default.
- `memory_mode=json` by default.
- `/chat` as the current real text dialogue surface.
- Wake events distinct from dialogue events.
- Raw provider payload storage disabled by default.
- Semantic memory shadow mode non-authoritative.
- `/life` read-only.
- Scheduler, cron, timers, and services unchanged.

M8 adds:

- an internal Memory Steward decision pass
- a memory decision schema
- a memory ledger / review trail
- a quarantine lane for sensitive or uncertain memory
- retrieval assembly for more natural dialogue continuity
- a small human review queue for edge cases
- M8 reports and final freeze gates

## External Design Evidence

The architecture follows current agent-memory practice:

- Long-term memory should be separated from short-term dialogue context, with
  semantic, episodic, and procedural categories.
  Sources:
  - https://docs.langchain.com/oss/python/concepts/memory
  - https://langchain-ai.github.io/langmem/concepts/conceptual_guide/
- Agents should manage memory through stateful layers rather than stuffing all
  history into the prompt.
  Sources:
  - https://docs.letta.com/guides/core-concepts/stateful-agents/
  - https://arxiv.org/abs/2310.08560
- Believable agent continuity benefits from memory stream, reflection, and
  planning, not only raw fact storage.
  Source:
  - https://arxiv.org/abs/2304.03442
- Memory systems must remain readable, updateable, deletable, and bounded by
  privacy and excessive-agency controls.
  Sources:
  - https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
  - https://owasp.org/www-project-top-10-for-large-language-model-applications/
  - https://www.nist.gov/itl/ai-risk-management-framework

These sources inform M8 but do not override the local M3-M7 safety contract.

## Architecture

### Dialogue Persona

The Dialogue Persona is the human-facing companion voice used by `/chat`.

Responsibilities:

- reply naturally in Simplified Chinese
- preserve recent turn context
- use accepted/retrieved memory without announcing memory internals
- avoid report-style scaffolding unless the human asks about project status,
  tests, evidence, progress, or boundaries

Non-responsibilities:

- no direct accepted-memory writes
- no scheduler, wake, cron, timer, service, or `/life` mutation
- no direct promotion of semantic shadow or proposal memory

### Memory Steward

The Memory Steward is an internal personality, not a second user-facing
companion.

Responsibilities:

- inspect completed dialogue turns
- classify memory candidates
- decide whether each candidate should be accepted, quarantined, rejected, or
  stored as audit-only reflection
- propose merges or updates to existing memory
- identify when human review is needed
- produce strict JSON decisions, not visible chat prose

Memory Steward tone:

- quiet
- careful
- non-dramatic
- relationship-preserving
- skeptical of inference
- protective of sensitive information

The steward may call the configured provider only after the user-initiated chat
turn completes. It must not generate a new visible chat reply, run a wake cycle,
or mutate scheduler state.

### Memory Policy Gate

The Policy Gate is code authority. It validates steward decisions before any
accepted memory becomes prompt-eligible.

The gate must reject or quarantine:

- secrets, tokens, passwords, API keys, private keys
- health, legal, financial, identity-sensitive, sexual, religious, or political
  material unless explicitly marked for human review
- unsupported model inference about the human
- relationship definitions inferred by the model
- memory conflicts without a resolution decision
- provider payloads or hidden prompt content

The gate may auto-accept only narrow low-risk cases:

- direct user-stated stable preference
- direct user-stated name or form of address
- direct user-stated project fact
- direct correction by the human
- explicit "remember this" request that is not sensitive and not ambiguous

### Memory Ledger

M8 should keep an append-only memory decision ledger separate from accepted
memory:

```text
life-loop/memory_decisions.jsonl
```

Decision rows should include:

```json
{
  "id": "memdec_20260619_190000_001",
  "conversation_id": "conversation id",
  "source_turn_ids": ["human turn id", "assistant turn id"],
  "candidate_content": "normalized candidate memory",
  "memory_type": "semantic",
  "decision": "accepted",
  "authority": "memory_steward",
  "prompt_eligible": true,
  "risk": "low",
  "reason": "direct user-stated preference",
  "evidence_refs": [
    {"artifact": "conversation", "id": "turn id"}
  ],
  "accepted_memory_id": "mem_x",
  "created_at": "2026-06-19T19:00:00"
}
```

Allowed decisions:

- `accepted`
- `quarantined`
- `rejected`
- `audit_only`
- `merge_proposed`
- `update_proposed`
- `human_review_required`

### Quarantine

Quarantined memory is not prompt-authoritative. It is stored for review and
audit only.

Quarantine should be used for:

- sensitive content
- uncertain inference
- possible relationship definition
- conflicts with accepted memory
- model-originated summary that lacks enough evidence

### Human Review Queue

Human review is the exception, not the default.

The review queue should show only items that need human judgment. It should not
make Polaris click through ordinary low-risk preferences.

Human actions:

- approve
- reject
- edit and approve
- archive
- keep pending

Human-approved memories should use `authority=user_asserted` or
`authority=evaluator_approved` with evidence refs, not `model_proposed`.

### Retrieval Assembler

The Retrieval Assembler prepares memory context for future dialogue.

Inputs:

- current human text
- recent turns from the active conversation
- accepted prompt-eligible memory
- context capsule
- optional non-authoritative audit summaries for ranking only

Outputs:

- a small ordered list of memory snippets
- retrieval reasons for audit
- no raw provider payloads
- no quarantined or rejected memory in prompt context

The assembler should prefer:

- recent accepted human preferences
- high-significance relationship continuity
- project-state facts only when the human asks about project/status
- procedural style rules for chat behavior

The assembler should avoid:

- stale project milestones that are superseded
- audit-only model reflections
- unaccepted proposals
- sensitive or quarantined items

## Memory Types

### Semantic

Stable facts and preferences.

Examples:

- "Polaris prefers testing reports to start with conclusion, then evidence."
- "Polaris wants ordinary chat to feel person-like unless he asks for status."

### Episodic

Specific events or conversations that may matter later.

Examples:

- "On 2026-06-19, Polaris tested M7 live chat style regression."

Episodic memory is usually not prompt-eligible unless summarized into a
derived, evidence-backed semantic or reflection item.

### Reflection

Low-authority synthesis about patterns. Reflections should be cautious and
source-backed.

Examples:

- "Polaris tends to prefer direct execution over permission handoffs."

Reflections are not automatically prompt-authoritative unless a later gate
approves them as derived summaries with evidence refs.

### Procedural

How the companion should behave.

Examples:

- "In casual chat, do not volunteer project status unless asked."

Procedural memory requires trusted authority. Model-originated procedural
memory must not become prompt-eligible without approval.

## M8 Stage Plan

### M8.0 Memory Steward Design

Artifacts:

```text
docs/m8-memory-steward-design.md
DESIGN.md
```

Acceptance:

- M8 is defined as memory and dialogue hardening.
- Memory Steward, Policy Gate, Ledger, Quarantine, Human Review, and Retrieval
  roles are documented.
- No runtime code changes are required.

Recommendation values:

- `m8_memory_steward_design_ready`
- `inspect`

### M8.1 Memory Decision Schema

Goal: define a strict schema for steward decisions and ledger rows.

Expected implementation:

```text
companion_core/m8_memory_schema.py
tests/test_m8_memory_steward.py
```

Acceptance:

- Decision records validate required ids, decision status, risk, authority, and
  evidence refs.
- Invalid decisions do not write accepted memory.
- Schema supports accepted, quarantined, rejected, audit-only, merge/update, and
  human-review states.
- Existing M7 proposal records remain readable.

Recommendation values:

- `m8_memory_schema_ready`
- `inspect`

### M8.2 Memory Steward Read-only Pass

Goal: run the internal steward against dialogue transcripts without accepting
memory yet.

Expected implementation:

```text
companion_core/memory_steward.py
scripts/run_m8_memory_steward.py
life-loop/m8_memory_steward_report.json
```

Acceptance:

- Reads recent conversation transcript and accepted memory.
- Produces memory decisions.
- Writes report only by default.
- Does not write accepted memory.
- Does not write wake events.
- Does not mutate scheduler, cron, timers, services, or `/life`.

Recommendation values:

- `m8_memory_steward_readonly_ready`
- `provider_required`
- `inspect`

### M8.3 Policy Gate And Ledger

Goal: make steward decisions auditable and bounded by code.

Expected implementation:

```text
companion_core/m8_memory_policy.py
life-loop/memory_decisions.jsonl
```

Acceptance:

- Low-risk direct user-stated memories may be accepted.
- Sensitive, ambiguous, conflict, and relationship-defining candidates are
  quarantined or routed to human review.
- Every decision has source conversation/turn evidence.
- No quarantined/rejected/audit-only memory is prompt-eligible.
- Accepted memory includes a decision id and evidence refs.

Recommendation values:

- `m8_memory_policy_ledger_ready`
- `inspect`

### M8.4 Retrieval Assembler

Goal: improve future dialogue continuity without stuffing full history into the
prompt.

Expected implementation:

```text
companion_core/memory_retrieval.py
```

Acceptance:

- Retrieves only prompt-eligible accepted memory.
- Filters stale superseded project-state memories unless status is requested.
- Produces a small ranked context set with audit reasons.
- Keeps proposal, quarantine, and audit-only rows out of prompt context.
- Makes M7 accepted style preferences available to chat.

Recommendation values:

- `m8_memory_retrieval_ready`
- `inspect`

### M8.5 Dialogue Humanity Regression

Goal: verify that better memory makes chat more human, not more report-like.

Acceptance:

- Casual chat uses relevant accepted memory naturally.
- The companion does not announce memory operations in ordinary chat.
- Project/status reports appear only when asked.
- Multi-turn conversation remains coherent.
- Provider failures preserve input and do not corrupt transcripts.

Expected report:

```text
life-loop/m8_dialogue_humanity_report.json
```

Recommendation values:

- `m8_dialogue_humanity_ready`
- `inspect`

### M8.6 Human Review Exception Queue

Goal: expose only edge-case memory decisions to Polaris.

Expected UI:

```text
GET /memory-review
POST /memory-review/<decision_id>/approve
POST /memory-review/<decision_id>/reject
POST /memory-review/<decision_id>/edit
```

Acceptance:

- Human review is not required for ordinary low-risk accepted memory.
- Review queue shows source, candidate, risk, reason, and recommended action.
- Approval writes accepted memory with human/evaluator authority.
- Reject/archive keeps items out of prompt context.
- `/life` remains read-only.

Recommendation values:

- `m8_human_review_queue_ready`
- `inspect`

### M8.7 Memory And Dialogue Final Freeze

Goal: freeze memory stewardship before scheduler, voice, or Signal work.

Expected implementation:

```text
companion_core/m8_memory_freeze.py
scripts/run_m8_memory_freeze.py
life-loop/m8_memory_freeze_report.json
```

Acceptance:

- M8.1-M8.6 evidence passes.
- M7.6 final freeze remains valid.
- Steward provider pass cannot directly promote memory without policy gate.
- Accepted prompt-eligible memory has source evidence and accepted authority.
- Quarantined/rejected/audit-only memory is not in prompt context.
- Human review decisions are auditable.
- Dialogue remains natural under regression tests.
- No wake, scheduler, cron, timer, service, semantic-shadow authority, raw
  provider payload, or `/life` write mutation occurs.

Recommendation values:

- `m8_memory_dialogue_frozen`
- `inspect`

## Stop Conditions

Stop M8 and inspect when:

- M7.6 freeze evidence is missing or not ready.
- Memory Steward writes accepted memory without Policy Gate approval.
- A proposal, quarantine, rejected item, or audit-only reflection becomes
  prompt-authoritative.
- Sensitive content is accepted without human review.
- Model inference about Polaris is stored as a durable fact.
- Relationship-defining memory is accepted without explicit human wording or
  review.
- Accepted memory lacks source conversation/turn evidence.
- Retrieval includes unaccepted or quarantined memory.
- Dialogue starts reporting memory internals in ordinary chat.
- Chat writes wake events.
- Scheduler, cron, timer, or service state changes during M8.
- Raw provider payloads are stored by default.
- `/life` gains write routes.

## Test Strategy

For M8.0:

```bash
git diff --check
```

For M8.1-M8.4:

```bash
.venv/bin/python -m pytest tests/test_m8_memory_steward.py -q
.venv/bin/python -m pytest tests/test_m7_text_dialogue.py -q
```

For M8.5:

```bash
.venv/bin/python scripts/chat_with_companion.py \
  --companion-home /home/polaris/digital_life \
  --provider deepseek \
  --memory-mode json \
  "普通聊两句，不要汇报项目。"
```

For M8.7:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/run_m8_memory_freeze.py \
  --companion-home /home/polaris/digital_life
```

## M9 Handoff

After `m8_memory_dialogue_frozen`, the next stage can choose between:

- scheduler handoff with stronger memory continuity
- voice interface over the same dialogue engine
- Signal text bridge
- richer memory review and relationship timeline UI

Recommended default: M9 scheduler handoff, because M8 will make autonomous
presence safer and less repetitive.
