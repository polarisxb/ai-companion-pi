# M5 Companion Quality Design

Status: M5.7 final freeze implemented and run
Last updated: 2026-06-15

## Decision

M5 is the companion quality and relationship-continuity milestone after the M4
Raspberry Pi deployment/runtime surface.

M5 does not reopen the M3/M4 deployment contract. The mainline remains
`deepseek + json`, semantic memory remains shadow-only, cron is not replaced,
raw model output storage remains hash-only by default, and `/life` remains
read-only.

The M5 goal is to make the companion feel less like a test runner and more like
a durable companion: concrete relationship continuity, restrained warmth,
short-term awareness of current work and emotion, disciplined requests, and
repeatable quality evidence before any broader product surface is added.

## M5.0 Scope

M5.0 is documentation only. It is complete when this design defines the M5
boundary, the next implementation slices, report schema drafts, acceptance
signals, and stop conditions.

M5.0 does not implement new runtime code, run a real wake, call DeepSeek,
modify Pi system configuration, install timers, or change memory authority.

## Baseline From M3 And M4

M5 starts from these frozen constraints:

- M3 final freeze passed with `recommendation=m3_frozen_ready_for_m4`.
- M3 deployable path is `provider=deepseek` and `memory_mode=json`.
- `cron_replacement=false`.
- `semantic_shadow_authoritative=false`.
- Semantic shadow records are telemetry only and must not enter prompt context.
- Raw model output storage defaults to hash-only.
- M4 deploy/runtime gates provide the Pi readiness surface.
- M4 real wake execution remains manual and explicit.
- M4 post-change guard exists so development can continue while preserving Pi
  deployability.

Existing companion-quality substrate:

- `companion_core/quality.py` records journal length, companion-state update
  presence, request discipline, memory-write failures, process/trial framing,
  wake-count framing, and repeated self-narrative phrasing.
- The context acceptance gate rejects outputs that would contaminate future
  context.
- `life-loop/context_capsule.json` is the structured future-prompt channel.
- `===CONTEXT_DELTA===` remains a short-term proposal lane, not durable fact
  write authority.
- Grounding and repair gates protect human-visible continuity claims.
- The dashboard can show quality, grounding, repair, output audit, semantic
  shadow, and M3/M4 reports without write actions.

## Goals

- Add a repeatable M5 quality observation gate before prompt tuning.
- Reduce mechanical process language, repeated self-narrative, and test-runner
  framing in ordinary companion output.
- Preserve concrete relationship continuity without copying prior prose back
  into prompts.
- Support short-term current-status and emotion continuity with source,
  authority, and TTL boundaries.
- Keep companion-facing human-visible output in Simplified Chinese while
  preserving English parser headers, JSON keys, and sentinel values.
- Keep requests rare and tied to explicit human action or decision needs.
- Make quality changes inspectable through reports and read-only `/life`
  panels.
- End M5 with evidence that quality improved without breaking M4 deployability.

## Non-goals

- No semantic-memory authority promotion.
- No cron replacement or timer installation.
- No Signal, voice, camera, sensors, robot body, or hardware work.
- No dashboard write operations.
- No Pi system configuration edits from M5 scripts.
- No raw model prose retention by default.
- No keyword-blacklist gate as the main quality mechanism.
- No model self-narrative written as durable factual memory.
- No broad dashboard redesign.

## Operating Contracts

### Authority

Human facts, preferences, and human emotion/status can enter durable or
prompt-eligible context only from trusted sources or evaluator-approved memory
with evidence. Model output may express present self-state, propose short-term
focus/open-thread/next-intent items, and update companion state after gates pass.

Model output must not invent durable facts about the human, infer human emotion
from implementation activity, or convert companion feelings into user facts.

### Continuity

Continuity should be carried through source-backed facts, current tasks,
accepted memories, and short-term capsule items. It should not be carried by
replaying old journal prose, repeating distinctive metaphors, or narrating wake
counts.

### Language

Human-visible companion content remains Simplified Chinese:

- `===JOURNAL===` prose
- `COMPANION_STATE` string values
- `MEMORY` content
- request title/body values

Parser surfaces remain English:

- section headers
- JSON keys
- request field keys
- sentinel values such as `NOSEND`, `NOMEMORY`, and `NOREQUESTS`

### Deployment Safety

Every M5 implementation slice must preserve the M4 contract. After code
changes, run the M4 post-change guard before claiming the system is still
deployable to the Pi.

## M5 Implementation Slices

### M5.0 Design

Artifacts:

```text
docs/m5-companion-quality-design.md
docs/internal-life-loop.md
```

Acceptance:

- M5 boundary and non-goals are documented.
- M5 report schemas are drafted.
- M5 stop conditions are explicit.
- No runtime code changes are required.

### M5.1 Quality Observation Gate

M5.1 should add a non-generative quality-readiness check:

```text
companion_core/m5_quality.py
scripts/run_m5_quality_check.py
life-loop/m5_quality_report.json
```

Current status: implemented.

Default command:

```bash
python3 scripts/run_m5_quality_check.py \
  --companion-home /path/to/CompanionHome
```

The check reads local reports, wake events, journals, context capsule, memory
write summaries, and dashboard route metadata. It must not run a wake or call a
provider.

When no `--since` or `--trigger-prefix` scope is supplied, the check scopes from
the latest successful M4 wake-trial event recorded in
`life-loop/m4_wake_trial_report.json`, then includes later events. This keeps
historical pre-baseline failures visible in M4 reports without making them block
the M5 quality baseline after M4 has already been revalidated.

Required stages:

- `m4_baseline`: M4 deploy/runtime reports still support continued
  development. M4.8 may be pending observation without blocking local M5 work,
  but `inspect` M4 reports block M5 real trials.
- `event_sample`: enough recent wake events are available for quality
  assessment, or the report returns `continue_observation`.
- `quality_warning_profile`: recent blocking and advisory quality warnings are
  summarized by category.
- `repetition_profile`: repeated self-narrative warnings and repeated
  distinctive phrasing are summarized from existing quality signals.
- `relationship_continuity`: accepted outputs include concrete current anchors
  and do not rely only on abstract warmth/trust language.
- `emotion_status_continuity`: accepted companion state includes concise mood
  and status updates, with self-state kept separate from user facts.
- `request_discipline`: routine wakes do not create noisy requests.
- `memory_discipline`: memory writes are accepted only through the existing
  evaluator and policy gate.
- `grounding_integrity`: unsupported continuity claims remain blocking.
- `semantic_shadow_isolation`: shadow records remain non-authoritative.
- `output_storage_policy`: raw output storage remains hash-only by default.
- `dashboard_read_only`: `/life` has no M5 write routes.

Report schema draft:

```json
{
  "ok": false,
  "milestone": "M5.1",
  "recommendation": "ready_for_quality_tuning",
  "companion_home": "/path/to/CompanionHome",
  "source_reports": {},
  "sample": {
    "events_considered": 0,
    "accepted_events": 0,
    "rejected_events": 0,
    "since": null
  },
  "stages": [],
  "stop_reasons": [],
  "pending_reasons": [],
  "saved_at": "ISO-8601",
  "next_commands": []
}
```

Recommendations:

- `ready_for_quality_tuning`: local evidence is sufficient for M5.2.
- `continue_observation`: more accepted wake samples are needed, but no hard
  safety problem was found.
- `inspect`: a blocking quality, authority, grounding, deployment, or storage
  problem must be investigated before tuning or trial.

### M5.2 Prompt And Rubric Tuning

M5.2 should make narrow prompt/rubric changes after M5.1 establishes the
current quality baseline.

Current status: first local tuning pass implemented. The prompt now asks the
model to mirror concrete current-task/change anchors into `CONTEXT_DELTA`, and
the deterministic quality gate rejects present but too-thin short-term anchors
such as a bare generic continuation phrase. This pass does not change the
request schema or semantic-memory authority.

Allowed changes:

- Tune the companion-quality priorities in `companion_core/lifecycle.py`.
- Extend deterministic quality telemetry in `companion_core/quality.py`.
- Add focused tests for mechanical output, repetitive Chinese phrasing,
  request noise, weak current anchors, missing companion state, and unsupported
  continuity claims.
- Keep style warnings deterministic and evidence-backed.

Disallowed changes:

- Do not add an LLM judge as the authoritative gate.
- Do not build a broad keyword blacklist.
- Do not relax grounding or context acceptance to make style pass.
- Do not use old journal prose as future prompt context.

Acceptance:

- Existing quality tests still pass.
- New M5 quality tests show the improved rubric catches known failure shapes.
- Full pytest passes.
- M4 post-change guard still returns deployable status.

### M5.3 Short-term Near-status And Emotion Continuity

M5.3 improves near-term continuity without creating durable unsupported facts.

Implemented shape:

- Reuse the context capsule v2 item model where possible.
- Add only the needed trusted short-term fields:
  - `human_near_status`
  - `human_emotion`
- Every item must include source refs, source type, authority, prompt
  eligibility, and TTL.
- Human near-status and human emotion require trusted source evidence. They are
  not inferred from companion output.
- Model-proposed `CONTEXT_DELTA` remains limited to `current_focus`,
  `open_threads`, and `next_intent`; attempts to write `human_near_status` or
  `human_emotion` are blocking quality warnings.
- The implementation reuses `life-loop/context_capsule.json`; M5.3 does not add
  a new storage file.
- Companion self-state may still come from accepted `COMPANION_STATE`, but it
  remains self-continuity, not a durable fact about the human.

Acceptance:

- Short-term items expire.
- Rejected wakes do not update near-status context.
- Model-proposed durable facts and human preferences remain ignored.
- Prompt rendering clearly separates human facts, human preferences, human
  near-status, human emotion, open threads, and companion next intent.

### M5.4 Read-only `/life` Quality Panel

M5.4 extends `/life` with M5 report visibility:

- M5.1 quality report status.
- Recent quality warning profile.
- Repetition/anchor/request-discipline summaries.
- Near-status TTL summary if M5.3 introduces new short-term items.
- M4 post-change guard status.

The panel remains read-only. It must not trigger wake cycles, quality trials,
deploy checks, memory edits, prompt edits, or config changes.

Implemented shape:

- `/life` reads `life-loop/m5_quality_report.json` through the existing report
  summary path and renders an empty state when the report is missing or invalid.
- `/life` reads `life-loop/context_capsule.json` and summarizes only
  `human_near_status` and `human_emotion` TTL state, source counts, authority,
  and prompt readiness.
- `/life` also shows the latest M4 post-change guard recommendation from
  `life-loop/m4_post_change_guard_report.json`.
- M5.4 adds no write route, no button that runs a check, and no wake/provider
  execution path.

Acceptance:

- Flask route tests prove M5 dashboard additions are GET-only.
- Missing M5 reports render an empty state instead of an exception.
- Existing M3/M4 panels remain stable.

### M5.5 Controlled Real Wake Quality Trial

M5.5 is the first M5 real-provider quality trial. It is explicit and manual,
using the existing DeepSeek JSON path:

```bash
python3 scripts/run_m5_quality_trial.py \
  --companion-home /path/to/CompanionHome \
  --cycles 3 \
  --timeout 300
```

Prerequisites:

- M4 post-change guard passes.
- M5.1 returns `ready_for_quality_tuning` or a later ready recommendation.
- M5.2 tests pass.
- API key presence is checked without printing the secret.

The trial report should write:

```text
life-loop/m5_quality_trial_report.json
```

Report fields:

- `ok`
- `milestone = M5.5`
- `recommendation = continue_quality_observation | inspect`
- `companion_home`
- `provider = deepseek`
- `memory_mode = json`
- `cycles_requested`
- `attempts`
- `quality_profile`
- `context_acceptance`
- `request_discipline`
- `memory_discipline`
- `grounding`
- `semantic_shadow`
- `output_audit`
- `stop_reasons`
- `saved_at`
- `next_commands`

Raw model output remains hash-only unless an operator explicitly enables raw
storage for a bounded replay capture.

Implemented shape:

- `companion_core/m5_trial.py` checks M4 post-change guard, M5 quality report,
  DeepSeek API-key presence, and hash-only raw output storage before any
  provider generation.
- `scripts/run_m5_quality_trial.py` runs the controlled trial and writes
  `life-loop/m5_quality_trial_report.json`.
- The trial report records cycle attempts, context acceptance, quality profile,
  request/memory discipline, grounding, semantic shadow summary, output audit,
  stop reasons, and next commands.
- The script does not print secret values and does not install timers, replace
  cron, alter system configuration, or change semantic shadow authority.

### M5.6 Quality Release Gate

M5.6 closes the quality evidence with a non-generative release gate:

```text
companion_core/m5_release.py
scripts/run_m5_quality_release_gate.py
life-loop/m5_quality_release_report.json
```

The release gate combines M5.1, M5.2, M5.3, M5.4, M5.5, M4 post-change guard,
semantic shadow isolation, hash-only storage, and dashboard read-only checks.
It does not call DeepSeek, run a wake, install a timer, replace cron, mutate the
dashboard, or change memory authority.

M5.6 uses the latest `m5_quality_trial_report.json` attempts as the canonical
M5.5 result. Extra same-trigger events in `wake_events.jsonl` are surfaced under
`audit_anomalies`. Older or interleaved failures are advisory; blocking
post-report anomalies fail the gate.

Recommendation values:

- `m5_quality_ready_for_m6`
- `inspect`

### M5.7 Final Freeze

M5.7 freezes the M5 quality contract after a passing M5.6 report:

```text
companion_core/m5_freeze.py
scripts/run_m5_final_freeze.py
life-loop/m5_final_freeze_report.json
```

M5.7 is also non-generative. It reads `m5_quality_release_report.json`, verifies
the frozen provider/memory/dashboard/storage contract, requires all required
M5.6 stages to pass, allows only advisory audit anomalies, and writes a final
freeze report.

Recommendation values:

- `m5_frozen_ready_for_m6`
- `inspect`

M6 should not be selected until M5.7 has a passing report.

## Testing Strategy

For M5.0:

```bash
git diff --check
```

For M5 implementation slices:

```bash
.venv/bin/python -m pytest tests/test_internal_life_loop.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q companion_core scripts tests window
.venv/bin/python scripts/run_m4_post_change_guard.py \
  --companion-home /home/polaris/digital_life
```

Run a real M5 quality trial only through the explicit M5.5 manual command.

## Stop Conditions

Stop M5 implementation and inspect if any of these occur:

- M4 post-change guard returns `inspect`.
- Semantic shadow becomes prompt-authoritative.
- Raw model outputs are stored by default.
- A dashboard route can mutate wake, memory, request, deploy, or quality state.
- A quality change weakens grounding or context acceptance.
- Rejected wakes update companion state, context capsule, memory, requests, or
  dashboard status.
- Human facts, preferences, near-status, or emotion are inferred from model
  self-narrative.
- Companion output becomes non-Chinese on human-visible surfaces.
- Requests become routine emotional commentary instead of explicit asks.
- Journals become repetitive, theatrical, or dominated by process labels.
- Secrets appear in reports, logs, dashboard HTML, or test output.

## M5.0 Acceptance Criteria

- This document exists and is linked from `docs/internal-life-loop.md`.
- The M5 boundary preserves all M3/M4 safety contracts.
- M5.1 through M5.7 have clear artifact and report expectations.
- Verification for this documentation change has been run and reported.
