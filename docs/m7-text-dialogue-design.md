# M7 Text Dialogue Design

Status: M7.6 dialogue hardening freeze implemented after M6.7 final freeze
Last updated: 2026-06-19

Related implementation review: `docs/m7-1-text-dialogue-review.md`

## Decision

M7 makes the companion directly reachable through text dialogue. It is not a
scheduler milestone. Scheduler handoff can resume after the human can already
talk to the companion.

M7 introduces a new user-initiated dialogue path:

```text
human text input
  -> load companion identity, human context, current state, context capsule, and accepted memory
  -> call the configured provider
  -> return one companion reply
  -> write a conversation transcript and event metadata
  -> optionally emit memory proposals, not automatic memory commits
```

M7 must preserve M6.7 final-freeze evidence. It may add a dialogue write
surface for transcripts, but it must not silently mutate scheduler state,
replace cron, install timers, run wake cycles, promote semantic shadow
authority, or commit chat text into long-term memory without a later explicit
memory gate.

Confirmed direction from the M7 intake interview:

- M7 must support real back-and-forth text conversation.
- M7 must form memory, but only inside a narrow authority boundary.
- CLI must be implemented first to prove the dialogue engine.
- The frontend chat page is still in scope, but may follow after the CLI and
  API are stable; the human may provide the final UI design before frontend
  implementation.
- Replies should feel like natural companion dialogue, while the system records
  lightweight state, transcript metadata, and memory proposal data behind the
  scenes.

## Why M7 Is Text Dialogue First

M6 proved the Pi field pilot can run, recover, and hand off to a scheduler.
That does not yet let the human talk with the companion in real time.

The next highest-value companion milestone is a direct text channel:

- The human can speak to the companion now.
- Voice and hardware can attach later to the same dialogue engine.
- Scheduler can resume later without blocking conversation.
- Dialogue transcripts create the evidence needed to harden memory behavior.

## M7 Inherits From M6

Required baseline:

```text
life-loop/m6_final_freeze_report.json
ok = true
milestone = M6.7
recommendation = m6_frozen_ready_for_scheduler_handoff
stop_reasons = []
```

M7 keeps:

- `provider=deepseek` by default.
- `memory_mode=json` by default.
- Raw model output storage hash-only by default.
- Semantic memory shadow mode non-authoritative.
- Model self-narrative blocked from automatic durable factual memory.
- Wake events distinct from dialogue events.
- `/life` read-only.

M7 explicitly adds:

- a user-initiated text dialogue engine
- conversation transcripts
- dialogue event metadata
- narrow automatic memory for explicit low-risk user-stated facts/preferences
- memory proposals for inferred, sensitive, relationship-defining, or ambiguous
  content
- a later dashboard chat page

## Non-goals

- No voice, microphone, speech synthesis, camera, or hardware body work.
- No Signal chat integration.
- No automatic scheduler/cron installation.
- No wake cycle triggered by sending a chat message.
- No automatic long-term memory commit from chat text.
- No semantic memory authority promotion.
- No raw provider request/response body retention by default.
- No broad dashboard redesign.

## Storage Boundary

M7 should keep dialogue artifacts separate from wake artifacts:

```text
conversations/
  conversation_<timestamp>.jsonl
life-loop/conversation_events.jsonl
life-loop/m7_text_dialogue_report.json
```

Suggested transcript event shape:

```json
{
  "id": "turn_20260619_153000_001",
  "conversation_id": "conv_20260619_153000",
  "role": "assistant",
  "created_at": "2026-06-19T15:30:00",
  "content": "human-visible reply text",
  "provider": "deepseek",
  "memory_mode": "json",
  "input_hash": "sha256...",
  "output_hash": "sha256...",
  "raw_output_stored": false,
  "memory_proposal_ids": []
}
```

Suggested conversation event shape:

```json
{
  "id": "dialogue_20260619_153000",
  "conversation_id": "conv_20260619_153000",
  "status": "completed",
  "trigger": "human-text-chat",
  "provider": "deepseek",
  "memory_mode": "json",
  "transcript": "conversations/conversation_20260619_153000.jsonl",
  "turn_count": 2,
  "memory_proposal_count": 0,
  "error": null
}
```

Transcripts may store the human input and companion reply because the product
goal is conversation continuity. They must not store secrets, raw provider
payloads, or hidden prompt bundles.

## Memory Boundary

M7 should use three memory levels:

1. Conversation-local continuity: recent turns remain available during the
   active session.
2. Narrow automatic long-term memory: only explicit, stable, low-risk facts or
   preferences directly stated by the human may be committed automatically.
3. Memory proposals: inferred, sensitive, relationship-defining, ambiguous, or
   model-originated claims are stored as proposals only.

Automatic memory examples:

- "以后叫我 Polaris."
- "我喜欢你用中文跟我说话."
- "这个项目现在叫 digital_life."
- "这件事以后你要记得."

Proposal-only examples:

- inferred emotion or relationship interpretation
- health, legal, financial, identity-sensitive, or security-sensitive content
- model self-narrative
- "I guess the human really means..." style inference
- major relationship definitions not directly stated by the human

## State Update Boundary

Every successful turn writes the transcript. Current companion state may update
only when the companion explicitly emits a current mood/status change.

Allowed:

```json
{
  "mood": "安心，专注",
  "status": "正在和 Polaris 梳理 M7 文字对话能力。",
  "source": "chat"
}
```

Not allowed:

- changing global state after every turn by default
- inferring the human's state as companion state
- updating `window/status.json` from failed turns
- writing state from hidden prompt text instead of the companion's explicit
  emitted state

## Prompt Context Boundary

M7 dialogue should load:

- `context/who_is_companion.txt`
- `context/who_is_human.txt`
- `context/now.txt`
- `life-loop/context_capsule.json`
- accepted JSON memory from `memory-server/memory_store.json`
- current companion state from `life-loop/companion_state.json`
- recent turns from the active conversation transcript

M7 dialogue should not load:

- raw model outputs
- secret files or secret values
- rejected wake content as authority
- semantic shadow memories as prompt-authoritative memory
- unrelated old transcripts unless a later summarization gate selects them

## Output Boundary

The first M7 dialogue response should be natural human-visible Simplified
Chinese text plus optional structured metadata extracted by code. The companion
should not be forced to produce the wake-cycle sections (`===JOURNAL===`,
`===MEMORY===`, `===REQUESTS===`) for normal chat.

The preferred response contract is:

- human-visible reply first
- lightweight internal metadata for mood/status only when explicitly emitted
- memory candidates separated into auto-committable and proposal-only buckets
- no visible report-style scaffolding in normal chat

If memory proposals are supported, they should be separate records:

```json
{
  "id": "memprop_20260619_153000_001",
  "conversation_id": "conv_20260619_153000",
  "source_turn_id": "turn_20260619_153000_001",
  "status": "proposed",
  "content": "candidate memory",
  "reason": "why it may matter",
  "accepted": false
}
```

## Stage Plan

### M7.0 Text Dialogue Design

Artifacts:

```text
DESIGN.md
docs/m7-text-dialogue-design.md
docs/internal-life-loop.md
```

Acceptance:

- M7 is defined as text dialogue, not scheduler handoff.
- Chat, message board, requests, wake cycles, and `/life` have distinct roles.
- Storage, prompt context, memory proposal, and dashboard boundaries are
  documented.
- No runtime code is added.

Recommendation values:

- `m7_dialogue_design_ready`
- `inspect`

### M7.1 CLI One-Turn Chat

Goal: prove the real dialogue engine can answer one human text prompt.

Expected implementation:

```text
companion_core/dialogue.py
scripts/chat_with_companion.py
```

Expected command:

```bash
.venv/bin/python scripts/chat_with_companion.py \
  "你现在在吗？" \
  --companion-home /home/polaris/digital_life \
  --provider deepseek \
  --memory-mode json
```

Acceptance:

- Loads M6.7 final-freeze evidence before real-provider chat.
- Loads companion identity, human context, state, context capsule, and accepted
  memory.
- Sends one human prompt to the provider.
- Prints one companion reply.
- Writes one transcript file and one dialogue event.
- Applies the narrow automatic-memory rule.
- Emits proposal records for non-auto memory candidates.
- Updates current state only when explicitly emitted.
- Leaves a stable engine/API shape that the later dashboard chat can call.
- Does not run a wake cycle.
- Does not edit scheduler state.

Expected report:

```text
life-loop/m7_text_dialogue_report.json
```

Recommendation values:

- `m7_cli_dialogue_ready`
- `provider_required`
- `inspect`

### M7.2 Interactive CLI REPL

Goal: support a continuous local text session.

Implementation:

```bash
.venv/bin/python scripts/chat_with_companion.py \
  --interactive \
  --companion-home /home/polaris/digital_life \
  --provider deepseek \
  --memory-mode json
```

The REPL allocates one `conversation_id` for the local session, appends every
successful human/assistant pair to that transcript, and exits on `exit` or
`quit`. If a provider turn fails, the human input is recorded as a failed
human transcript row with no assistant row, then kept pending in the REPL so
the operator can press Enter to retry or type replacement text.
Unlike the M7.1 one-turn command, the interactive command keeps memory
candidates as proposals instead of auto-accepting low-risk facts during the
live session.

Acceptance:

- Keeps recent turns in process-local short context.
- Appends each turn to the active transcript.
- Handles `exit`/`quit` cleanly.
- Preserves failed human input for retry.
- Does not automatically commit memory.
- Does not run wake cycles or mutate scheduler state.

Recommendation values:

- `m7_interactive_dialogue_ready`
- `inspect`

### M7.3 Transcript And Replay Check

Goal: make dialogue inspectable and replayable enough to debug.

Implementation:

```bash
.venv/bin/python scripts/dialogue_replay_check.py \
  conversations/<conversation_id>.jsonl \
  --companion-home /home/polaris/digital_life \
  --json
```

The check is read-only. It parses transcript JSONL and the linked
`life-loop/conversation_events.jsonl` ledger, validates turn/event linkage,
verifies transcript content hashes, rejects raw provider payload fields, and
confirms failed provider turns have no assistant turn. It does not construct an
LLM client, call a provider, run wake logic, or write scheduler/runtime state.

Acceptance:

- Transcript JSONL validates.
- Dialogue events are append-only.
- Hash-only output audit is present.
- A replay/check command can parse transcripts without calling the provider.
- Failed provider turns do not create assistant turns marked completed.
- Raw provider payload fields in transcript rows or dialogue events fail the
  check.

Recommendation values:

- `m7_dialogue_transcript_ready`
- `inspect`

### M7.4 Memory Proposal Gate

Status: implemented as a read-only report gate in `companion_core/m7_memory_gate.py` and `scripts/run_m7_memory_proposal_gate.py`.

Goal: let dialogue form memory without silently changing broad authority.

Acceptance:

- Explicit low-risk user-stated facts/preferences may become accepted JSON
  memory automatically.
- Memory proposals are stored separately from accepted memory.
- Proposals include source conversation and turn ids.
- No proposal enters prompt context until an explicit later accept path exists.
- `/life` or a report can show proposal counts.
- The gate writes `life-loop/m7_memory_proposal_report.json` with accepted-memory counts, proposal counts, source conversation/turn linkage, proposal-only prompt-authority status, stop reasons, and boundary flags.
- The gate does not approve, accept, or promote proposals; proposed memories remain outside prompt authority until a later explicit acceptance workflow exists.

Recommendation values:

- `m7_memory_proposals_ready`
- `inspect`

### M7.5 Dashboard Chat Page

Status: implemented in Companion Window with `GET /chat` and `POST /chat/send` on top of `DialogueRunner`.

Goal: add the text dialogue surface to Companion Window.

Expected route:

```text
GET /chat
POST /chat/send
```

UI shape:

- main transcript column
- bottom composer
- compact state strip showing mood/status, provider, memory mode, transcript id
- optional memory proposal panel
- clear loading/error/retry states

Acceptance:

- Uses the same dialogue engine as CLI.
- Adds a `chat` nav item.
- Can be implemented after the human provides a preferred UI design.
- Does not add write routes to `/life`.
- Keeps message board and requests separate from chat.
- Works on desktop and phone PWA widths.
- `POST /chat/send` supports JSON API callers and rendered form submissions, returns structured JSON when requested, and preserves failed input in error responses for retry.
- The route displays transcript rows, composer, provider, memory mode, conversation id, companion status, and proposal count without adding write routes under `/life`.

Recommendation values:

- `m7_dashboard_chat_ready`
- `inspect`

### M7.6 Dialogue Hardening Freeze

Status: implemented as a read-only freeze gate in `companion_core/m7_dialogue_freeze.py` and `scripts/run_m7_dialogue_freeze.py`.

Goal: freeze the text dialogue path before voice, Signal, or scheduler work
continues.

Acceptance:

- M7.1-M7.5 evidence passes.
- Provider failures are handled without corrupting transcripts.
- Secret strings do not appear in reports, transcripts, or dashboard HTML.
- Chat does not run wake cycles.
- Chat does not edit scheduler state.
- Chat commits only narrow explicit low-risk human-stated memory automatically.
- All inferred, sensitive, relationship-defining, or ambiguous memory remains
  proposal-only.
- Existing M6.7 final-freeze evidence remains valid.

Expected command:

```bash
python3 scripts/run_m7_dialogue_freeze.py \
  --companion-home /home/polaris/digital_life
```

Expected report:

```text
life-loop/m7_dialogue_freeze_report.json
```

The gate only reads M6.7 and M7 reports, conversation transcripts/events, memory proposal evidence, and dashboard route source. It does not create a provider client, does not run wake logic, does not mutate scheduler/cron/timer/service state, does not add `/life` write routes, and does not accept or promote proposed memory.

Recommendation values:

- `m7_text_dialogue_frozen`
- `inspect`

## Chat Page Design Contract

The dashboard chat page should follow `DESIGN.md`.

Required first version:

- A single conversation view.
- Plain transcript rows with author labels.
- A textarea composer and send button.
- Visible provider/memory mode state.
- Visible transcript id or path after the first turn.
- Error panel that preserves the failed input.

Deferred:

- Streaming responses.
- Markdown-rich rendering.
- Multiple conversation search.
- Voice controls.
- Memory approval UI.
- Mobile push notifications.

## Stop Conditions

Stop M7 and inspect when:

- M6.7 final freeze is missing or not ready.
- Provider config is missing for a real-provider chat.
- Dialogue writes to wake_events as if a wake ran.
- Dialogue commits long-term memory without an explicit accept gate.
- Semantic shadow becomes prompt-authoritative.
- Raw provider payloads are stored by default.
- Secret values appear in transcripts, reports, logs, or dashboard HTML.
- `/life` gains write routes.
- Scheduler, cron, timer, or service state changes during M7.0-M7.6.
- The dashboard chat route cannot preserve failed input after an error.

## Test Strategy

For M7.0:

```bash
git diff --check
```

For M7.1-M7.4:

```bash
.venv/bin/python -m pytest tests/test_internal_life_loop.py -q \
  -k 'dialogue or m7'
.venv/bin/python -m compileall -q companion_core scripts tests window
```

For M7.5:

```bash
.venv/bin/python -m pytest tests/test_internal_life_loop.py -q \
  -k 'chat or dialogue or life_dashboard'
curl -fsS http://127.0.0.1:3000/chat
```

For M7.6:

```bash
.venv/bin/python -m pytest
.venv/bin/python scripts/run_m7_dialogue_freeze.py \
  --companion-home /home/polaris/digital_life \
  --no-write-report
```

## M8 Handoff

After `m7_text_dialogue_frozen`, the next milestone can choose between:

- scheduler handoff for automatic wake cadence
- voice input/output on top of the M7 dialogue engine
- Signal chat adapter
- memory proposal acceptance UI

The recommended path is scheduler handoff after text dialogue is stable, then
voice/hardware adapters.

## M7.4 Memory Proposal Gate

`companion_core.m7_memory_gate.run_m7_memory_proposal_gate` is a read-only report gate for dialogue memory behavior. It inspects `life-loop/memory_proposals.jsonl`, accepted JSON memory, conversation transcripts, and dialogue events, then writes `life-loop/m7_memory_proposal_report.json` via `scripts/run_m7_memory_proposal_gate.py`.

The gate does not accept proposals, does not change proposal state, does not call a provider, and does not make proposed memories prompt-authoritative. A passing report recommends `m7_memory_proposals_ready` and records proposal/accepted counts, conversation/turn linkage, prompt-authority status, stop reasons, and dialogue boundaries.

## M7.5 Dashboard Chat

The Window dashboard exposes `/chat` and `/chat/send` for user-initiated text dialogue. The route reuses `DialogueRunner`; it is not a separate provider path. The page shows transcript rows, composer, provider, memory mode, conversation id/transcript path, and memory proposal count. Failed form submissions preserve the submitted input in the response; JSON clients receive an `ok: false` error contract with the original input.


## M7.6 Dialogue Freeze Gate

`companion_core.m7_dialogue_freeze.run_m7_dialogue_freeze_check` is the final read-only M7 text-dialogue hardening gate. It inspects the current M6.7 freeze report, the M7 text dialogue report, replay-valid conversation transcripts, the current M7 memory proposal gate result, dashboard `/chat` source evidence, and dialogue evidence for secret/raw-provider-payload leaks.

The CLI wrapper writes `life-loop/m7_dialogue_freeze_report.json`; the gate itself performs no runtime mutations and reports `provider_calls=0`. A passing report recommends `m7_text_dialogue_frozen` and records boundary flags for no wake cycle, no scheduler mutation, no raw provider payload storage, and no semantic-shadow authority promotion. `/life` now displays M7 text dialogue, memory proposal, and dialogue freeze evidence while preserving the GET-only dashboard contract.
