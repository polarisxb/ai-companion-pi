# M9 Controlled Presence And Scheduler Handoff Design

Status: M9.0 design ready for implementation planning
Last updated: 2026-06-21

## Decision

M9 turns the companion from a mostly human-initiated system into a controlled
presence: she may wake on a bounded schedule, produce internal-life output, and
remain observable and reversible.

M9 is not a voice, Signal, camera, or hardware-body milestone. Those channels
increase interruption surface and should wait until scheduled presence is
stable.

The M9 direction is:

```text
M8.7 frozen memory/dialogue
  -> M9.1 read-only scheduler revalidation
  -> M9.2 supervised dry run using the real wake command shape
  -> M9.3 limited live scheduler activation
  -> M9.4 observation window and rollback drill
  -> M9.5 controlled presence freeze
```

M9.0 does not modify cron, systemd timers, services, or wake cadence. It defines
the contract that later M9 steps must satisfy before any scheduler mutation is
allowed.

## Why M9 Is Controlled Presence

M6 proved the Pi field-pilot environment and scheduler handoff readiness. M7
made direct text dialogue real. M8 made memory stewardship and dialogue
continuity safe enough to withstand more frequent contact.

The next risk is not whether the companion can generate output. The risk is
whether she can appear on a schedule without runaway loops, duplicate wakes,
opaque failures, stale memory authority, or hard-to-reverse production state.

M9 should therefore focus on controlled presence:

- bounded non-fixed cadence
- single-wake locking
- observable scheduler attempts
- failure backoff
- pause/rollback path
- no hidden expansion into Signal, voice, or hardware channels

## Baseline

Required baseline:

```text
life-loop/m6_final_freeze_report.json
recommendation = m6_frozen_ready_for_scheduler_handoff

life-loop/m8_memory_freeze_report.json
recommendation = m8_memory_dialogue_frozen
final_freeze.frozen = true
stop_reasons = []
```

M9 inherits:

- `/chat` remains the human-initiated text dialogue surface.
- `/life` remains read-only.
- Memory Steward, Policy Gate, Ledger, Retrieval, Human Review, and M8 final
  freeze remain authoritative.
- Raw provider payload storage remains disabled.
- Semantic shadow remains non-authoritative.
- Proposal/quarantine/rejected/audit-only memory does not enter prompt context.

## Architecture

### Scheduler Presence Controller

The Scheduler Presence Controller is the M9 boundary around automatic wakes.

Responsibilities:

- decide whether a scheduled wake is allowed to start
- enforce one wake at a time
- respect pause/disable flags
- record scheduler attempt evidence
- call the existing wake command only after preflight passes
- classify failures and expose rollback guidance

Non-responsibilities:

- no chat reply generation
- no Signal send
- no voice output
- no memory authority promotion
- no direct `/life` mutation

### Cadence Model

M9 should not use a fixed "every N hours" live wake rhythm. Fixed cadence reads
like a cron job, not like a companion with presence.

M9 should use controlled randomness:

```text
scheduled opportunity check
  -> read presence state
  -> enforce quiet hours / daily budget / min gap / lock / cooldown
  -> either skip with an auditable reason or run one scheduled wake
  -> sample the next candidate window
```

Default M9 cadence design:

```text
quiet_hours = 00:00-08:00 local time
daily_live_wake_budget = 2
scheduled_wake_output = internal_only
cadence = randomized_presence_windows
```

The scheduler artifact should wake a lightweight wrapper often enough to check
state, but the wrapper decides whether a real wake is appropriate. Most checks
may become auditable skips.

Skip reasons should include:

- `paused`
- `quiet_hours`
- `daily_budget_exhausted`
- `min_gap_not_met`
- `wake_lock_active`
- `failure_cooldown`
- `recent_human_chat_dampening`

The state file should be explicit and inspectable:

```text
life-loop/scheduler_presence_state.json
```

Candidate fields:

```json
{
  "last_scheduled_wake_at": "2026-06-21T18:00:00",
  "next_candidate_after": "2026-06-22T09:30:00",
  "daily_live_wake_budget": 2,
  "daily_live_wake_count": 0,
  "quiet_hours": ["00:00", "08:00"],
  "cooldown_until": null,
  "last_skip_reason": "quiet_hours"
}
```

M9 scheduled output is internal-only: journal, wake event, and `/life` evidence.
It must not send Signal, speak aloud, or emit external notifications.

### Wake Execution Path

M9 should reuse the existing wake execution path instead of creating a second
provider path.

The live scheduler command should eventually call the same bounded wake entry
used by manual wake trials, with explicit metadata such as:

```text
trigger = scheduled-wake
source = m9_scheduler
```

The scheduler wrapper may write scheduler attempt evidence, but it must not
store raw provider payloads or bypass existing wake output validation.

### Pause And Rollback

M9 needs a local, low-tech pause path before activation:

```text
life-loop/scheduler_pause.flag
```

If this file exists, scheduled wakes must not start. Manual `/chat` and manual
diagnostic commands remain available.

Rollback must include:

- the exact scheduler artifact to remove or disable
- the command used to disable it
- the last known scheduler state
- the report proving rollback readiness

### Observability

Scheduled presence must be visible without requiring shell access.

Expected evidence:

```text
life-loop/m9_scheduler_revalidation_report.json
life-loop/m9_scheduler_dry_run_report.json
life-loop/m9_scheduler_activation_report.json
life-loop/m9_presence_observation_report.json
life-loop/m9_presence_freeze_report.json
```

`/life` should show M9 reports read-only once implemented.

## Safety Contract

M9 keeps these hard boundaries:

- no scheduler mutation in M9.0 or M9.1
- no live scheduler activation before dry-run evidence passes
- no voice or Signal output during scheduler activation
- no raw provider payload storage
- no semantic shadow authority promotion
- no `/life` write route
- no unaccepted memory in prompt context
- no overlapping wake cycles
- no infinite retry loop

## Stage Plan

### M9.0 Controlled Presence Design

Artifacts:

```text
docs/m9-controlled-presence-design.md
DESIGN.md
life-loop/m9_controlled_presence_design_report.json
```

Acceptance:

- M9 is defined as controlled scheduled presence, not voice or Signal.
- M6.7 and M8.7 are the required baselines.
- Scheduler mutation is explicitly out of scope for M9.0.
- Cadence is non-fixed and bounded by quiet hours, daily budget, locks,
  cooldowns, and chat-aware dampening.
- Later live activation requires dry-run, pause, rollback, and observation
  evidence.

Recommendation values:

- `m9_controlled_presence_design_ready`
- `inspect`

### M9.1 Read-only Scheduler Handoff Revalidation

Goal: prove current production state is still safe to prepare for scheduler
handoff without changing scheduler state.

Expected implementation:

```text
companion_core/m9_scheduler_revalidation.py
scripts/run_m9_scheduler_revalidation.py
life-loop/m9_scheduler_revalidation_report.json
```

Acceptance:

- Reads M6.7 final freeze and M8.7 final freeze.
- Verifies wake command, runtime paths, lock files, and provider configuration
  readiness.
- Verifies no current scheduler artifact is unexpectedly active or unmanaged.
- Does not mutate cron, systemd timers, services, or `/life`.

Recommendation values:

- `m9_scheduler_revalidation_ready`
- `inspect`

### M9.2 Supervised Scheduler Dry Run

Goal: run the scheduler wrapper shape without live scheduler installation.

Expected implementation:

```text
companion_core/m9_scheduler_dry_run.py
scripts/run_m9_scheduler_dry_run.py
life-loop/m9_scheduler_dry_run_report.json
```

Acceptance:

- Uses the intended scheduler wrapper command path.
- Exercises lock acquisition, pause flag behavior, cooldown handling, and event
  writing.
- Exercises randomized presence windows, quiet hours, daily budget, min-gap
  checks, and skip reasons.
- Uses fake provider or dry-run wake mode unless explicitly switched for a
  bounded real-provider smoke.
- Does not install cron, timers, or services.

Recommendation values:

- `m9_scheduler_dry_run_ready`
- `inspect`

### M9.3 Limited Live Scheduler Activation

Goal: install or enable exactly one controlled scheduler artifact.

Expected implementation:

```text
companion_core/m9_scheduler_activation.py
scripts/run_m9_scheduler_activation.py
life-loop/m9_scheduler_activation_report.json
```

Acceptance:

- Requires M9.1 and M9.2 ready reports.
- Writes or enables one scheduler artifact only.
- Records the exact artifact path/name.
- Records rollback command and pause flag path.
- Starts with randomized presence windows, `daily_live_wake_budget=2`, and
  `scheduled_wake_output=internal_only`.
- Does not enable voice, Signal, or extra output channels.

Recommendation values:

- `m9_scheduler_activation_ready`
- `inspect`

### M9.4 Presence Observation And Rollback Drill

Goal: observe limited scheduled presence and prove rollback is still practical.

Expected implementation:

```text
companion_core/m9_presence_observation.py
scripts/run_m9_presence_observation.py
life-loop/m9_presence_observation_report.json
```

Acceptance:

- Observes a bounded number of scheduled attempts.
- Confirms no overlapping wake cycles.
- Confirms wake events are valid and raw payloads are absent.
- Confirms memory boundaries remain M8-compliant.
- Confirms pause flag suppresses scheduled wake attempts.
- Confirms rollback instructions are executable and current.

Recommendation values:

- `m9_presence_observation_ready`
- `inspect`

### M9.5 Controlled Presence Final Freeze

Goal: freeze scheduled presence before voice, Signal, or hardware body work.

Expected implementation:

```text
companion_core/m9_presence_freeze.py
scripts/run_m9_presence_freeze.py
life-loop/m9_presence_freeze_report.json
```

Acceptance:

- M9.1-M9.4 evidence passes.
- M8.7 remains frozen.
- Scheduler artifact is known, bounded, observable, and reversible.
- Pause and rollback are documented and tested.
- No wake overlap, raw provider payload, semantic-shadow authority promotion,
  or unaccepted memory prompt authority occurred.

Recommendation values:

- `m9_controlled_presence_frozen`
- `inspect`

## Explicit Non-goals

- No Signal send path.
- No voice output path.
- No camera or hardware body behavior.
- No scheduler mutation during M9.0 or M9.1.
- No new memory authority model.
- No second wake execution engine.

## Open Questions For M9.1

- Which scheduler mechanism should production prefer after dry-run evidence:
  cron, systemd timer, or an existing project wrapper?
- Should the pause flag suppress only scheduled wakes, or also manual scheduler
  dry-run commands?
- What observation window is enough before M9.5 freeze?
