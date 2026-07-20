# Internal Life Loop

This is the first second-development milestone for the companion direction: make the system feel like a persistent AI companion before adding more hardware, voice, external push, or UI redesign.

## Milestone Boundary

In scope:

- Load identity, human context, current state, structured context capsule, and accepted memories.
- Generate a self-narrative for the waking.
- Persist a journal entry.
- Write durable memory entries.
- Persist outward requests so they appear in the dashboard.
- Update dashboard status for local visibility.
- Support deterministic fake-LLM cycles for regression tests.
- Support controlled real-provider trial cycles without replacing cron.

Deferred:

- Camera, microphone, sensors, robotics, and other body-layer work.
- Voice output and voice conversations.
- Signal push from the new Python loop.
- Substack publishing from the new Python loop.
- Dashboard redesign.
- Multi-user or plugin-platform architecture.

## Runtime Shape

```text
wake trigger
  -> CompanionPaths resolves CompanionHome
  -> context loader reads identity/state/context capsule
  -> companion-state loader reads dashboard state without feeding status prose back into the prompt
  -> memory adapter reads accepted JSON memories
  -> LLM client generates structured sections
  -> parser extracts journal, signal, companion state, context delta, memories, requests
  -> journal writer always persists raw self-narrative for audit
  -> quality report and context acceptance gate decide future context eligibility
  -> output audit records hash-only raw-output snapshots for replay
  -> accepted wakes update companion state, context capsule, memory, requests, and status
  -> rejected wakes suppress state/memory/request/status writes but remain audited
  -> event writer appends quality_gate and accepted_context metadata
```

The implementation lives in `companion_core/`:

- `paths.py` resolves and prepares the shared home layout.
- `context.py` loads identity, human, now, the context capsule, and accepted memories.
- `context_capsule.py` owns structured future prompt context and `===CONTEXT_DELTA===` merges.
- `state.py` loads and updates companion self/relationship/preference state.
- `llm.py` provides the provider-agnostic LLM clients: fake, Claude CLI,
  OpenAI-compatible HTTP, and Ollama.
- `parser.py` parses `===JOURNAL===`, `===SIGNAL===`, `===COMPANION_STATE===`,
  `===MEMORY===`, and `===REQUESTS===`.
- `grounding.py` validates human-visible continuity claims against cited prompt evidence.
- `repair.py` can run one bounded grounded repair/regenerate attempt before commit.
- `output_archive.py` records hash-only raw-output audit snapshots, with optional
  raw storage for explicit regression captures.
- `replay.py` re-runs parser, grounding, optional repair, and quality gates
  without committing state.
- `predeploy.py` runs the Pi predeploy profile: target readiness, isolated fake
  wake smoke, replay regression, and optional real-provider wake.
- `memory.py` writes v2-compatible JSON memories without requiring embeddings during tests.
- `requests.py` owns request schema, locking, and collision-resistant IDs.
- `events.py` owns the local wake event JSONL ledger.
- `lifecycle.py` coordinates the wake cycle.

`JsonMemoryStore` is intentionally a low-dependency milestone adapter for local continuity and fake-LLM tests. Before `scripts/run_wake_cycle.py` replaces the shell wake cycle in cron, decide whether production memory writes should stay JSON-first or route through the semantic memory server so embeddings remain authoritative.

## Smoke Test

```bash
python3 scripts/run_wake_cycle.py \
  --fake-llm \
  --cycles 3 \
  --companion-home /tmp/companion-loop-smoke
```

Expected output:

- Three `journals/wakeup_*.md` files.
- Three entries in `memory-server/memory_store.json`.
- Three entries in `requests/requests.json`.
- A dashboard status file at `window/status.json`.
- Three events in `life-loop/wake_events.jsonl`.
- `life-loop/context_capsule.json` when accepted wakes write `===CONTEXT_DELTA===`
  or age an existing short-term capsule item.

## Model Providers

The Python loop is not tied to Claude. `scripts/run_wake_cycle.py` accepts:

- `--provider fake`
- `--provider claude-cli`
- `--provider openai-compatible`
- `--provider ollama`
- `--provider deepseek`

Common options:

- `--model` for HTTP-backed providers.
- `--base-url` for OpenAI-compatible or custom Ollama endpoints.
- `--api-key-env` for OpenAI-compatible providers. The default is
  `COMPANION_LLM_API_KEY`.
- `--timeout` for CLI or HTTP provider calls.
- `--check-provider` to validate provider configuration and reachability without
  running a wake cycle.

Environment defaults are also supported:

```bash
export COMPANION_LLM_PROVIDER=openai-compatible
export COMPANION_LLM_MODEL=qwen-plus
export COMPANION_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export COMPANION_LLM_API_KEY=...
```

## Real Provider Trial

After the fake smoke test passes, run one real provider-backed trial in a controlled
home directory:

```bash
python3 scripts/run_wake_cycle.py \
  --companion-home /tmp/companion-loop-real-trial \
  --provider openai-compatible \
  --model qwen-plus \
  --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
  --trigger real-trial \
  --timeout 300
```

For a local Ollama model:

```bash
python3 scripts/run_wake_cycle.py \
  --companion-home /tmp/companion-loop-ollama-trial \
  --provider ollama \
  --model qwen2.5:7b \
  --trigger ollama-trial
```

Use `--claude-bin /path/to/claude` only for the `claude-cli` provider when
`claude` is not on `PATH`. The runner prints JSON for both success and failure.
Failures during the wake cycle append a `failed` event to
`life-loop/wake_events.jsonl`, which lets the `/life` dashboard show what
happened without replacing the existing `wakeup.sh` cron path.

Run a provider preflight before a real trial:

```bash
python3 scripts/run_wake_cycle.py \
  --provider ollama \
  --model qwen2.5:7b \
  --check-provider
```

Preflight checks do not write journals, memories, requests, or wake events. Wake
events created by real cycles include the selected provider name so `/life` can
show which backend produced or failed a wake.

## Companion Quality Substrate

M3 adds `life-loop/companion_state.json` as a small, reviewable state file for
companion quality before cron handoff. It stores:

- `mood`: short dashboard mood.
- `status`: short current self-state for dashboard display.
- `relationship_notes`: shared relationship narrative and context.
- `preference_notes`: human preferences, habits, and boundaries.
- `self_notes`: the companion's own continuity and development notes.

The model updates this state through `===COMPANION_STATE===`. Journal remains
the full self-narrative, dashboard status shows the concise state, and requests
remain reserved for explicit asks or decisions.

M3.13 adds a context acceptance gate. Raw journals and wake events are kept for
audit, but only accepted wakes are allowed to update future prompt context,
companion state, memories, requests, or dashboard status. Rejected wakes record
their blocking warnings and suppressed write counts in `life-loop/wake_events.jsonl`.

M3.14 replaces accepted-summary replay with `life-loop/context_capsule.json`.
The capsule is a structured fact channel with `current_focus`, `facts`,
`human_preferences`, `open_threads`, and `next_intent`. Journals and dashboard
status remain visible artifacts, but their prose is intentionally not fed back
into the next prompt. Models update the capsule through `===CONTEXT_DELTA===`
only after the wake passes the context gate.

M3.15 makes `===CONTEXT_DELTA===` a proposal rather than direct write authority.
Model output may update only volatile `current_focus`, `open_threads`, and
`next_intent`; those fields are overwritten, not accumulated. Durable `facts`
and `human_preferences` are read-only to the model and must come from trusted
context, accepted memories, or a future user-message channel. Runtime telemetry
remains available through wake events and trial summaries instead of becoming
future companion context.

M3.16 upgrades `life-loop/context_capsule.json` to a v2 item-level read model.
Each item records `field`, `content`, `source_refs`, `source_type`, `authority`,
`prompt_eligible`, and `ttl_wakes`. Legacy v1 `facts` and `human_preferences`
are preserved as `legacy_unverified` and are not rendered into future prompts.
Authorized v2 durable items require source refs and trusted authority; model
`CONTEXT_DELTA` still writes only short-term fields and now receives a
provenance ref to the wake event and delta hash. Short-term items render only
while `ttl_wakes > 0`; accepted wakes consume one TTL and prune expired or
missing-TTL short-term items, while rejected wakes leave the capsule unchanged.

M3.17 adds a conservative memory evaluator before the policy gate. It can
upgrade a model-proposed `USER | ...` semantic memory only when the exact claim
is found in trusted user context (`who_is_human`, `now`, or already-authorized
memories). Approved items become `evaluator_approved` semantic memories with
evidence refs and can enter future prompts. Unsupported user/system claims stay
model-proposed and are rejected by policy; self-reflections remain audit-only.

M3.18c adds a grounding gate for human-visible continuity claims. The prompt now
renders a `GROUNDING LEDGER`; model output can declare factual continuity claims
in `===GROUNDING===` with `claim_type`, `claim`, and `evidence_refs`.
Unsupported claims are quality-blocking, so the journal remains audit-only but
state, capsule, memory, and requests are not committed. This gate checks
claim/evidence support rather than matching forbidden words. Wake events keep a
short `claim_excerpt` for grounding audit, and trial summaries separate
blocking warnings from advisory warnings such as a short journal.

M3.19 adds a bounded grounded repair layer before commit. If the first model
output contains unsupported grounded claims, the runtime may ask the same
provider for one repaired full-section output. The repaired output must pass
grounding again and must not retain the original unsupported claim text. If
repair fails, the wake remains rejected and no future-context writes occur.

M3.20 adds replay and regression support around the same gates. Wake events now
include `output_audit` snapshots for the initial and final model outputs. By
default this is hash-only, so raw model prose is not retained; setting
`COMPANION_STORE_RAW_OUTPUTS=1` stores raw outputs under
`life-loop/model_outputs/` for intentional replay captures. `ReplayRunner` and
`scripts/replay_wake_output.py` can re-run parser, grounding, optional repair,
and quality gates over a captured output without writing journal, state,
capsule, memory, requests, status, or new wake events.
`scripts/run_replay_regression.py` keeps accepted and rejected grounding cases
executable as architecture regressions rather than keyword-filter tests.

M3.21 adds the Pi predeploy profile. `scripts/run_pi_predeploy.py` checks the
target CompanionHome with the Pi-safe default `deepseek + json`, verifies raw
output storage is hash-only, prepares an isolated smoke home, runs a fake wake,
then runs replay regression against that smoke home. It does not replace cron
and it does not write fake smoke artifacts into the target home. A real provider
wake is opt-in through `--run-real-wake`.

M3.23 adds semantic memory shadow mode. The accepted JSON memory write remains
authoritative, while accepted prompt-eligible semantic memories are also written
to an isolated `life-loop/semantic_shadow/memory_store.json` probe store. Shadow
records are forced to `prompt_eligible=false` and `accepted_for_context=false`;
they never enter prompt context. Wake events record `semantic_shadow` counts and
failure status so semantic readiness can be observed without changing companion
behavior.

M3.24 adds real-trial observability for that shadow path. `run_wake_cycle.py`
prints a per-cycle `semantic_shadow` summary, and `build_trial_summary` rolls up
shadow `events/enabled/attempted/succeeded/failed/skipped` counts. Shadow
failures remain non-authoritative trial telemetry: they do not make an accepted
JSON wake fail, but they are visible in CLI output, `/life`, and trial summaries.

M3.25 adds the M3 release gate. `scripts/run_m3_release_gate.py` combines the
Pi predeploy fake smoke/replay checks, a bounded trial summary, and a semantic
shadow authority audit into one repeatable report. It keeps the M3 deployment
profile at `deepseek + json`, does not replace cron, and does not run a real
wake. Semantic shadow is treated as isolated readiness telemetry only: shadow
records must stay out of prompt context and the main memory store. A passing
report returns `recommendation=ready_for_m4`; any required failure returns
`recommendation=inspect`.

M3.26 adds the final freeze gate. `scripts/run_m3_final_freeze.py` reads the
M3.25 release gate report and verifies that the deployable M3 contract is still
intact: `deepseek + json`, `cron_replacement=false`, no real wake during freeze,
hash-only raw output storage, a passing bounded trial, and semantic shadow kept
non-authoritative. A passing freeze returns
`recommendation=m3_frozen_ready_for_m4` and writes
`life-loop/m3_final_freeze_report.json`.

M4 adds the Raspberry Pi deployment/runtime surface without reopening M3 memory
authority. `scripts/run_m4_deploy_check.py` verifies the frozen `deepseek +
json` contract, local runtime readiness, hash-only raw output storage,
customized context files, writable runtime directories, current semantic shadow
isolation, and dashboard/window files, then writes
`life-loop/m4_deploy_report.json`. `scripts/run_m4_wake_trial.py` requires that
deploy report before running one manual DeepSeek wake trial; it retries once
only for infrastructure failures such as network errors or timeouts and writes
`life-loop/m4_wake_trial_report.json`. `/life` shows these M3/M4 reports in a
read-only panel with retry and failure-audit status. The Pi operator flow is in
`docs/m4-pi-runbook.md`. `scripts/run_m4_runtime_validation.py` is the M4.6
close-out gate: it does not call DeepSeek or run another wake, and it seals the
latest deploy/wake reports, hash-only output audit, semantic shadow isolation,
latest event journal, and `/life` read-only route boundary into
`life-loop/m4_runtime_validation_report.json`. `scripts/run_m4_post_change_guard.py`
is the M4.7 non-generative compatibility guard for continued development while
the Pi is unavailable; it verifies that the current code still preserves the M4
deploy/runtime baseline and writes `life-loop/m4_post_change_guard_report.json`.
`scripts/run_m4_observation_check.py` is the M4.8 non-generative long-running
observation gate; it reads wake events and returns `stable_runtime_observed`,
`continue_observation`, or `inspect` without running another wake.

M5 is the companion quality and relationship-continuity milestone after the M4
runtime surface. Its design starts in `docs/m5-companion-quality-design.md`.
M5 keeps the frozen `deepseek + json` path, semantic shadow isolation,
hash-only raw output storage, no cron replacement, and read-only `/life`
dashboard boundary while adding quality observation, prompt/rubric tuning,
short-term near-status continuity, and controlled manual quality trials.
`scripts/run_m5_quality_check.py` is the M5.1 non-generative quality observation
gate; it reads existing M4 reports and selected wake evidence, then writes
`life-loop/m5_quality_report.json` with `ready_for_quality_tuning`,
`continue_observation`, or `inspect`. M5.2 begins prompt/rubric tuning by
requiring concrete short-term `CONTEXT_DELTA` anchors when the model provides
that section; too-thin generic anchors are rejected before future-context
writes. M5.3 adds trusted short-term `human_near_status` and `human_emotion`
items to the context capsule. They render only with source refs, prompt
authority, prompt eligibility, and positive TTL; accepted wakes age them, and
model-proposed `CONTEXT_DELTA` attempts to write those fields are rejected
instead of becoming prompt context. M5.4 extends the read-only `/life` page with
M5 quality report status, quality warning and discipline summaries, M4
post-change guard status, and a near-status TTL summary from the context
capsule. The page still does not trigger wake cycles, provider calls, deploy
checks, or writes. M5.5 adds the explicit manual DeepSeek/json quality trial
wrapper `scripts/run_m5_quality_trial.py`, which checks M4/M5 prerequisites,
API-key presence, and hash-only output storage before running wake cycles and
writing `life-loop/m5_quality_trial_report.json`. M5.6 adds the non-generative
quality release gate `scripts/run_m5_quality_release_gate.py`, which treats the
latest M5.5 report attempts as canonical and surfaces extra same-trigger wake
events as audit anomalies in `life-loop/m5_quality_release_report.json`. M5.7
adds `scripts/run_m5_final_freeze.py`, which freezes the M5 quality contract in
`life-loop/m5_final_freeze_report.json` after M5.6 passes.

M6 is the Raspberry Pi field pilot and deployment-trial milestone after the M5
final freeze. Its design starts in `docs/m6-pi-field-pilot-design.md`. M6 does
not expand companion ability; it moves the frozen M3-M5 behavior onto the real
Pi operating surface and proves deployability, runtime execution,
observability, rollback/recovery, scheduler handoff readiness, and authority
preservation. M6.0-M6.2 remain locally developable and non-generative by
default. M6.3 and later require a real Raspberry Pi for any real Pi manual
wake, observation, recovery drill, scheduler readiness, or final field-pilot
claim. M6.6 may only produce scheduler handoff readiness; it must not replace
cron, install timers, or edit scheduler state.

M6.1 adds the migration package boundary in
`docs/m6-pi-migration-checklist.md` and the local machine-readable manifest
`life-loop/m6_migration_manifest.json`; it records package inventory,
preserve/exclude rules, secret metadata boundaries, and M5.7 evidence
carry-forward without running a wake or touching Pi configuration.
M6.2 adds the local non-generative Pi preflight v2 gate in
`companion_core/m6_preflight.py` and `scripts/run_m6_preflight.py`; it writes
`life-loop/m6_preflight_report.json` after checking the M6.1 manifest, current
M4 deployability guard, current M5.7 freeze evidence, semantic shadow
isolation, hash-only output storage, and local platform identity without
claiming real Pi presence.
M6.3 adds a guarded manual-wake entry in
`companion_core/m6_manual_wake.py` and
`scripts/run_m6_pi_manual_wake_trial.py`; the entry requires the M6.2 preflight
report, explicit `--confirm-real-pi-wake`, Raspberry Pi platform identity, and
hash-only output storage before delegating to the frozen M4 wake-trial wrapper.
On the Pi, the confirmed M6.3 trial produced
`recommendation=continue_pi_observation`.
M6.4 adds real-Pi observation in `companion_core/m6_observation.py` and
`scripts/run_m6_pi_observation_check.py`; the current report returns
`recommendation=stable_pi_field_observed`.
M6.5 adds non-destructive backup and restore-sandbox verification in
`companion_core/m6_recovery.py` and `scripts/run_m6_recovery_drill.py`; the
current report returns `recommendation=rollback_recovery_ready`.
M6.6 adds scheduler handoff readiness in `companion_core/m6_scheduler.py` and
`scripts/run_m6_scheduler_readiness.py`; the current report returns
`recommendation=ready_for_scheduler_handoff` without mutating scheduler state.
M6.7 adds the final read-only Pi field-pilot freeze in
`companion_core/m6_final_freeze.py` and `scripts/run_m6_final_freeze.py`; the
current report returns `recommendation=m6_frozen_ready_for_scheduler_handoff`
with scheduler mutation flags false and rollback evidence present.

M7 is the text dialogue milestone after M6.7 final freeze. Its design starts in
`docs/m7-text-dialogue-design.md` and the product/UI source of truth is
`DESIGN.md`. M7 prioritizes direct human-to-companion text dialogue over
scheduler automation. It adds a user-initiated dialogue engine, conversation
transcripts, dialogue event metadata, memory proposals as proposals only, a
read-only M7.4 memory proposal gate report, and the dashboard `/chat` page/API. The confirmed M7 memory boundary allows automatic
memory only for explicit low-risk user-stated facts/preferences; inferred,
sensitive, relationship-defining, or ambiguous content remains proposal-only.
Completed turns always write transcripts, and current companion state updates
only when the companion explicitly emits mood/status. M7 must not run wake
cycles from chat, edit scheduler state, promote semantic shadow authority, or
store raw provider payloads by default. The M7.4 gate writes only `life-loop/m7_memory_proposal_report.json`; M7.5 chat writes only dialogue transcripts/events through `DialogueRunner` and does not add `/life` write routes. M7.6 adds `companion_core/m7_dialogue_freeze.py` and `scripts/run_m7_dialogue_freeze.py` as a read-only freeze gate; the CLI writes `life-loop/m7_dialogue_freeze_report.json` with recommendation `m7_text_dialogue_frozen` when M6.7 and M7.1-M7.5 evidence still satisfy the dialogue boundaries.

M8 is the Memory Steward and dialogue-humanity milestone after M7.6. Its design
starts in `docs/m8-memory-steward-design.md`. M8 should let the companion manage
ordinary low-risk memory through an internal steward while preserving code-level
policy authority, an append-only decision ledger, quarantine for sensitive or
ambiguous candidates, retrieval filtering for accepted memory, and sparse human
review only for edge cases. M8 must not make the human the routine memory
administrator, and it must not reopen wake, scheduler, `/life`, raw payload, or
semantic-shadow authority boundaries.

M9 is the controlled scheduled presence milestone after the M8.7 memory freeze.
Its design starts in `docs/m9-controlled-presence-design.md`. M9.1-M9.5 added
read-only scheduler revalidation, a supervised dry run, explicit cron
activation behind a Scheduler Presence Controller, a presence observation
window, and the final controlled-presence freeze
(`recommendation=m9_controlled_presence_frozen`), all while reusing the frozen
wake execution path with randomized presence windows, quiet hours, a daily
live-wake budget, and pause/rollback drills.

M10 is the Signal text chat milestone after the M9.5 presence freeze. Its
design starts in `docs/m10-signal-chat-design.md`. M10 is chat-first: inbound
Signal messages from allowlisted senders run through the frozen M7
`DialogueRunner` with `auto_memory=False`, and the reply returns to the sender
over signal-cli. M10.1 adds `companion_core/signal_transport.py` (envelope
parsing, fake transport, signal-cli transport), `companion_core/signal_chat.py`
(config, policy, dedupe state, append-only hashed attempt ledger, bridge loop
with pause flag and single-instance lock), and the dry-run gate
`scripts/run_m10_signal_dry_run.py`, which writes
`life-loop/m10_signal_dry_run_report.json`
(`recommendation=m10_signal_dry_run_ready`) after exercising every policy
branch with fake transport and a fake dialogue model. Real signal-cli traffic
requires `scripts/run_m10_signal_chat.py` with passing M7/M8/M9 freeze
evidence, a valid `life-loop/signal_chat_config.json`, and the explicit
`--confirm-real-signal-send` flag. M10.2 adds the supervised real send trial
in `companion_core/m10_signal_trial.py` and
`scripts/run_m10_signal_trial.py`; it requires M10.1 evidence plus the
confirmation flag and writes `life-loop/m10_signal_trial_report.json`
(`m10_signal_trial_ready`) only when at least one allowlisted message was
answered without failures. M10.3 adds explicit listener activation in
`companion_core/m10_signal_activation.py` and
`scripts/run_m10_signal_activation.py`; `--enable` installs exactly one
managed systemd user service (`companion-signal-chat.service`) and
`--disable` is the recorded rollback, writing
`life-loop/m10_signal_activation_report.json`. M10.4 adds the read-only
observation gate in `companion_core/m10_signal_observation.py`
(`life-loop/m10_signal_observation_report.json`), checking decision health,
allowlist discipline, dedupe, budget, hashed-only storage, and a reversible
pause drill. M10.5 adds the read-only final freeze in
`companion_core/m10_signal_freeze.py`
(`life-loop/m10_signal_freeze_report.json`,
`recommendation=m10_signal_chat_frozen`). M10 must not send proactive or
scheduled Signal messages, store raw envelopes or raw provider payloads,
expand memory authority, or mutate scheduler state.

M11 is the Signal outbound milestone after M10. Its design starts in
`docs/m11-signal-outbound-design.md`. M11 lets accepted wake cycles reach the
human: the wake `===SIGNAL===` section (previously hard-coded `NOSEND`) is
captured by `companion_core/signal_outbox.py` into the durable, redacted
outbox `life-loop/signal_outbox.jsonl`, with hash-only `signal_outbox`
metadata on the wake event; rejected wakes suppress capture like every other
write. Delivery is owned by the M10 bridge service through
`deliver_outbox_once`, is disabled unless `outbound_enabled=true` in
`life-loop/signal_chat_config.json`, and enforces one allowlisted recipient,
outbound quiet hours, a small daily outbound budget, entry expiry, length
caps, per-wake dedupe, one bounded send retry, and abandonment after
`outbound_max_send_attempts`; retryable holds (pause flags, quiet hours,
budget) defer silently while terminal outcomes land in the attempt ledger
with `direction=outbound`. Gates mirror M10: M11.3 dry run
(`scripts/run_m11_outbound_dry_run.py`,
`recommendation=m11_signal_outbound_dry_run_ready`), M11.4 supervised trial
(`scripts/run_m11_outbound_trial.py`, requires M10.2/M10.3 evidence and
`--confirm-real-signal-send`), M11.5 read-only observation
(`scripts/run_m11_outbound_observation.py`), and M11.6 final freeze
(`scripts/run_m11_outbound_freeze.py`,
`recommendation=m11_signal_outbound_frozen`, which also requires the M10.5
chat freeze). The wake path itself never sends; M11 must not deliver
request/journal content, message unknown numbers, or change M9 scheduler and
M8 memory contracts.

M12 is the semantic retrieval milestone after the Signal channel work. Its
design starts in `docs/m12-semantic-retrieval-design.md`. M12 resolves the
long-standing JSON-vs-semantic decision: `memory_store.json` stays the
authoritative record and M8 policy filters keep final authority, while a
derived, rebuildable vector index (`life-loop/semantic_index.json`) lets the
M8 retrieval assembler rank already-approved memories by meaning.
`companion_core/semantic_retrieval.py` provides the config
(`life-loop/semantic_retrieval_config.json`, ships disabled), a deterministic
dependency-free hashing backend, a sentence-transformers backend for the Pi
(default `paraphrase-multilingual-MiniLM-L12-v2`), and the ranking layer;
every failure mode (disabled config, missing index, unavailable backend,
stale entries) degrades to the existing lexical scoring without raising.
Retrieval never writes the index; only the idempotent M12.3 sync
(`scripts/run_m12_semantic_backfill.py`) mutates it, and deleting the index
file is a complete rollback. Gates: M12.1 read-only readiness audit
(`scripts/run_m12_semantic_readiness.py`), M12.2 behavior gate proving
semantic gain, policy immunity at similarity 1.0, deterministic fallback, and
read-only retrieval (`scripts/run_m12_semantic_retrieval_check.py`), M12.3
backfill report, M12.4 read-only observation with a live retrieval probe and
fallback drill (`scripts/run_m12_semantic_observation.py`), and the M12.5
final freeze (`scripts/run_m12_semantic_freeze.py`,
`recommendation=m12_semantic_retrieval_frozen`), which also requires intact
M7.6/M8.7 freezes. M12 must not change memory acceptance policy, promote the
M3.23 semantic shadow store, or add prompt authority to proposals and
quarantined memories.

M13 is the Feishu chat channel milestone after M12. Its design starts in
`docs/m13-feishu-chat-design.md`. Signal is blocked in mainland China, so the
human confirmed Feishu (飞书) self-built-app bots as the production chat
channel; the M10/M11 chat stack was transport-pluggable by design, and M13
reuses its policy, budgets, dedupe, pause flags, ledger, outbox delivery, and
the M7 dialogue engine unchanged. `companion_core/feishu_transport.py` parses
`im.message.receive_v1` events, sends text through the REST API with a cached
tenant token and one stale-token retry (stdlib urllib), and adapts the
official `lark-oapi` long-connection WebSocket (lazily imported; the Pi needs
no public IP) to the poll-based bridge through a thread-safe queue. Ledger
records carry an explicit `channel` field (`signal` by default for older
records, `feishu` for M13); conversation ids use the `feishu_` prefix; the
config schema is shared (`life-loop/feishu_chat_config.json`, account =
app_id, allowlisted open_ids) and credentials live only in
`.secrets/feishu.env`. Gates mirror M10: M13.1 dry run
(`scripts/run_m13_feishu_dry_run.py`,
`recommendation=m13_feishu_dry_run_ready`, including a stubbed-HTTP
token/send/retry stage and secret-hygiene checks), M13.2 supervised trial
(`scripts/run_m13_feishu_trial.py`, requires `--confirm-real-feishu-send`),
M13.3 activation of exactly one managed systemd user unit
(`companion-feishu-chat.service` via `scripts/run_m13_feishu_activation.py`),
M13.4 read-only observation scoped to feishu-channel records, and the M13.5
final freeze (`recommendation=m13_feishu_chat_frozen`). M10/M11 gates now
scope to the signal channel; the Signal transport remains in the repo as an
alternative. M13 is text-only: images, voice bubbles, and cards are later
milestones.

For the full real-provider trial path, see `docs/m3-real-trial.md`.

## Expansion Plan

1. Keep M7 text dialogue frozen without reopening M3-M6 contracts.
2. Execute M8 memory steward and dialogue-humanity hardening. (Done: M8.7
   memory/dialogue freeze.)
3. Revisit scheduler handoff after accepted/retrieved memory improves ordinary
   dialogue continuity; do not replace cron or install timers as part of M8.
   (Done: M9.5 controlled presence freeze.)
4. Add optional Signal delivery as an output/input adapter only after text
   dialogue and memory stewardship are frozen and scheduler handoff is
   explicitly approved. (In progress: M10 Signal text chat, inbound-reply only.)
5. Add voice, hardware/body adapters, and broader product surfaces only after
   the internal companion loop is stable, observable, recoverable, frozen on the
   Pi, reachable through text dialogue, and backed by safe memory stewardship.

## M7 memory proposal boundary

M7 dialogue memory proposals remain proposal-only until the M8 Memory Steward
and policy-gated ledger path accepts, quarantines, rejects, or routes them to
human review. `scripts/run_m7_memory_proposal_gate.py` reports whether proposal
records carry `conversation_id` and `source_turn_id`, remain separate from
accepted memory, and are not prompt-authoritative. This gate is read-only and
does not mutate wake, scheduler, provider, `/life`, or semantic-shadow
authority.


## M7 dialogue freeze boundary

M7.6 freezes text dialogue before voice, Signal, or scheduler work resumes. `scripts/run_m7_dialogue_freeze.py` inspects existing reports, transcript replay checks, memory proposal gate evidence, and dashboard chat route evidence. It does not call the provider, run wake cycles, write scheduler/cron/timer/service state, add `/life` write routes, store raw provider payloads, accept proposed memory, or promote semantic-shadow authority. `/life` displays the resulting `life-loop/m7_dialogue_freeze_report.json` as read-only evidence.
