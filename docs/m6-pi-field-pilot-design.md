# M6 Pi Field Pilot Design

Status: M6.3 guarded manual-wake entry implemented; real Pi trial pending
Last updated: 2026-06-18

## Decision

M6 is the Raspberry Pi field pilot and deployment-trial milestone for the
internal companion life loop. It moves the already frozen M3-M5 behavior onto
the real Pi operating surface and proves that the system is deployable,
runnable, observable, recoverable, rollback-ready, and still inside authority
boundaries.

M6 is not a companion-capability expansion milestone. It does not make semantic
memory authoritative, replace cron, add a timer, add Signal/voice/hardware, add
dashboard write actions, retain raw model output by default, or promote model
self-narrative into durable factual memory.

## M6.0 Scope

M6.0 is documentation only. It is complete when this design defines the M6
field-pilot boundary, local-vs-Pi development boundary, report schema drafts,
stage recommendations, stop conditions, and testing strategy.

M6.0 does not implement runtime code, run a real wake, call DeepSeek, modify Pi
system configuration, install timers, replace cron, or alter memory authority.

## Baseline From M3, M4, And M5

M6 inherits the frozen contracts below:

- M3 main path remains `deepseek + json`.
- Semantic memory remains shadow-mode telemetry only and must not enter the
  authoritative prompt path.
- Cron is not replaced.
- No timer is installed.
- Real wake execution remains explicit and manual.
- Raw model output storage remains hash-only by default.
- Dashboard/window surfaces remain read-only for M3/M4/M5/M6 operations.
- Model self-narrative must not be written as durable factual memory.
- Final human-visible companion output remains Simplified Chinese.
- M4 deployment/runtime reports remain the deployability baseline.
- M5 quality/release/freeze reports remain the relationship-continuity and
  quality baseline.

M6 must not be selected unless the M5 final freeze report exists and passes:

```text
life-loop/m5_final_freeze_report.json
ok = true
milestone = M5.7
recommendation = m5_frozen_ready_for_m6
stop_reasons = []
```

The M6 final freeze must retain this M5.7 evidence. It may reference the report
by path and snapshot its `ok`, `milestone`, `recommendation`, `saved_at`, and
quality-contract fields, but it must not overwrite or discard the source
evidence.

## Development Boundary

M6.0-M6.2 can be developed locally while the Raspberry Pi is not present:

- M6.0: design documentation.
- M6.1: deployment package, migration checklist, and secret-boundary
  documentation.
- M6.2: Pi preflight v2 gate that is local and non-generative by default.

M6.3 and later require a real Raspberry Pi for any claim that depends on Pi
presence:

- M6.3: controlled real Pi manual wake trial.
- M6.4: Pi observation gate over real Pi journal, memory, request, quality, and
  report evidence.
- M6.5: backup, rollback, and recovery drill on Pi-managed artifacts.
- M6.6: scheduler handoff readiness decision.
- M6.7: M6 final freeze.

When the Pi is unavailable, development may continue only on local scripts,
documentation, fixtures, and non-generative checks. Reports may say
`pi_required` or `continue_local_preflight`, but they must not claim a real Pi
trial, Pi observation window, rollback drill, or scheduler handoff readiness has
completed.

## Non-goals

- No semantic-memory authority promotion.
- No cron replacement.
- No timer installation.
- No Signal, voice, camera, sensors, robot body, or hardware expansion.
- No dashboard write operations.
- No Pi system configuration edits unless a later, explicit, manually approved
  deployment step is in scope.
- No raw model prose retention by default.
- No model self-narrative written as durable human/system fact memory.
- No broader companion-style expansion beyond preserving M5 quality on the Pi.

## M6 Stage Plan

### M6.0 Pi Field Pilot Design

Artifacts:

```text
docs/m6-pi-field-pilot-design.md
docs/internal-life-loop.md
docs/m4-pi-runbook.md
```

Acceptance:

- M6 is documented as Pi field pilot / deployment trial.
- M3/M4/M5 frozen contracts are inherited explicitly.
- Local development boundary and Pi-required boundary are explicit.
- Report schema drafts, recommendations, and stop conditions are documented.
- No runtime code, provider call, wake, cron, timer, or system config change is
  introduced.

Recommendation values:

- `m6_design_ready_for_m61`
- `inspect`

### M6.1 Deployment Package And Secret Boundary

M6.1 should define the artifact transfer and migration plan without mutating a
real Pi by default.

Expected artifacts:

```text
docs/m6-pi-field-pilot-design.md
docs/m6-pi-migration-checklist.md
life-loop/m6_migration_manifest.json
```

Current status: implemented locally. `docs/m6-pi-migration-checklist.md`
defines the migration package, preserve/exclude lists, secret metadata boundary,
local network assumptions, scheduler boundary, owner/permission expectations,
and M6.1 stop conditions. `life-loop/m6_migration_manifest.json` records the
same package as a machine-readable local manifest with M4/M5 source-report
snapshots and `recommendation=migration_manifest_ready`.

The deployment package should enumerate:

- Repository files required on the Pi.
- Runtime directories and files that must be preserved.
- Files that must not be copied from development machines, including virtualenvs
  and machine-local caches.
- Secret locations and redaction requirements.
- M5.7 evidence that must be carried forward.
- Expected owner, permissions, and local-only network assumptions.

Recommendation values:

- `migration_manifest_ready`
- `inspect`

### M6.2 Pi Preflight v2 Gate

M6.2 should add a local, non-generative preflight gate that proves the migration
package and current working tree are still deployable before a real Pi trial.

It must not run a real wake by default. It must not call DeepSeek, install a
timer, replace cron, edit system configuration, or promote semantic memory.

Current status: implemented locally in `companion_core/m6_preflight.py` and
`scripts/run_m6_preflight.py`. The gate reads the M6.1 migration manifest,
checks package inventory and preserve/exclude policy, verifies secret/network/
scheduler/optional-surface boundaries, re-runs current no-write M4 post-change
guard and M5.7 final-freeze checks, audits semantic shadow authority, confirms
hash-only raw output storage, and records local platform identity. It does not
run a wake, call DeepSeek, mutate scheduler/system configuration, or claim real
Pi presence.

Expected report:

```text
life-loop/m6_preflight_report.json
```

Recommendation values:

- `ready_for_real_pi_manual_wake`
- `continue_local_preflight`
- `inspect`

### M6.3 Real Pi Manual Wake Trial

M6.3 begins only when the real Raspberry Pi is present and the operator has
explicitly selected the manual trial command. It runs the frozen DeepSeek/json
path once or for a bounded, documented count.

Current status: the guarded entry is implemented in
`companion_core/m6_manual_wake.py` and
`scripts/run_m6_pi_manual_wake_trial.py`. The entry reads the M6.2 preflight
report, requires `--confirm-real-pi-wake`, requires Raspberry Pi platform
identity, confirms hash-only raw output storage, and only then delegates to the
existing M4 wake-trial wrapper. A local run without those gates returns an M6.3
guard report and must not call DeepSeek or create a wake event. The actual M6.3
success report remains pending until it is run on the real Pi.

The trial must preserve the M4/M5 retry discipline:

- Retry at most once for infrastructure failures.
- Do not retry provider configuration, parser, grounding, authority, memory,
  request, dashboard, or storage-policy failures.
- Store hashes, ids, counts, categories, and report paths, not raw model prose
  or secrets.

Expected report:

```text
life-loop/m6_pi_manual_wake_report.json
```

Recommendation values:

- `continue_pi_observation`
- `pi_required`
- `inspect`

### M6.4 Pi Observation Gate

M6.4 observes real Pi artifacts after M6.3: wake events, journals, JSON memory,
requests, context capsule, companion state, M5 quality signals, M6 reports, and
read-only dashboard state.

It does not run a wake and does not call DeepSeek. It reads evidence from the
real Pi CompanionHome and decides whether the pilot remains stable.

Expected report:

```text
life-loop/m6_pi_observation_report.json
```

Recommendation values:

- `stable_pi_field_observed`
- `continue_pi_observation`
- `pi_required`
- `inspect`

### M6.5 Backup, Rollback, And Recovery Drill

M6.5 proves recoverability for Pi-managed artifacts. The drill should operate
on an explicit backup target and a documented restore scope. It must not delete
or overwrite live Pi state without a manually approved drill command and a
recoverable backup already present.

The drill should cover at least:

- `life-loop/`
- `journals/`
- `memory-server/memory_store.json`
- `requests/requests.json`
- `window/status.json`
- `.secrets/` metadata only, never secret values

Expected report:

```text
life-loop/m6_recovery_drill_report.json
```

Recommendation values:

- `rollback_recovery_ready`
- `pi_required`
- `inspect`

### M6.6 Scheduler Handoff Readiness

M6.6 only decides whether the frozen wake path is ready to be handed to the
existing scheduler/cron process. It must not replace cron, install a new timer,
enable a service, or edit crontab.

Readiness should require:

- M6.3 real Pi manual wake success.
- M6.4 stable observation evidence.
- M6.5 rollback/recovery readiness.
- M5.7 final freeze evidence still passing.
- M4/M5 no-authority-expansion contracts still intact.
- Operator-visible rollback instructions.

Expected report:

```text
life-loop/m6_scheduler_readiness_report.json
```

Recommendation values:

- `ready_for_scheduler_handoff`
- `not_ready_for_scheduler_handoff`
- `pi_required`
- `inspect`

### M6.7 Final Freeze

M6.7 freezes the Pi field pilot after the required M6 reports pass. It reads
reports and evidence only; it does not run a wake, call DeepSeek, replace cron,
install timers, edit system configuration, mutate the dashboard, or change
memory authority.

Expected report:

```text
life-loop/m6_final_freeze_report.json
```

Recommendation values:

- `m6_frozen_ready_for_scheduler_handoff`
- `inspect`

## Stage Outcome Semantics

Each M6 stage should make the `ok`, `recommendation`, and `stop_reasons`
contract explicit:

- M6.0:
  - `ok=true` when design, boundaries, schema draft, stop conditions, and test
    strategy are documented without runtime or system changes.
  - Recommendations: `m6_design_ready_for_m61`, `inspect`.
  - `stop_reasons`: missing frozen-contract inheritance, missing Pi boundary,
    missing schema semantics, or documentation inconsistency.
- M6.1:
  - `ok=true` when the migration/package manifest and secret boundary are
    complete, redacted, and preserve M5.7 evidence.
  - Recommendations: `migration_manifest_ready`, `inspect`.
  - `stop_reasons`: missing required artifact, unsafe copy rule, secret
    exposure, absent M5.7 snapshot, or unclear rollback ownership.
- M6.2:
  - `ok=true` when local non-generative preflight passes and no real wake,
    provider call, or system mutation was requested.
  - Recommendations: `ready_for_real_pi_manual_wake`,
    `continue_local_preflight`, `inspect`.
  - `stop_reasons`: M5.7/M4 baseline failure, raw-output policy failure,
    semantic-shadow authority failure, dashboard write route, missing migration
    evidence, or accidental provider call.
- M6.3:
  - `ok=true` when a real Pi is present and the explicit manual DeepSeek/json
    wake trial completes inside retry and authority boundaries.
  - Recommendations: `continue_pi_observation`, `pi_required`, `inspect`.
  - `stop_reasons`: Pi absent, preflight not ready, manual wake failure after
    allowed retry, unsupported grounding, rejected context, memory/request
    failure, raw output retention, or secret exposure.
- M6.4:
  - `ok=true` when real Pi observation evidence meets the configured
    window/count and shows no hard event, quality, authority, storage, or
    dashboard failure.
  - Recommendations: `stable_pi_field_observed`, `continue_pi_observation`,
    `pi_required`, `inspect`.
  - `stop_reasons`: Pi absent, observation window incomplete with failures,
    failed/rejected wake, unsupported grounding, write-boundary regression, or
    missing evidence.
- M6.5:
  - `ok=true` when backup, rollback, and recovery drill evidence proves the
    scoped artifacts can be restored without exposing secrets or losing
    M5.7/M6 evidence.
  - Recommendations: `rollback_recovery_ready`, `pi_required`, `inspect`.
  - `stop_reasons`: Pi absent, missing backup, unsafe overwrite/delete,
    unrecoverable artifact, secret copied into report, or restore evidence
    missing.
- M6.6:
  - `ok=true` when M6.3-M6.5 pass and the system is judged ready for scheduler
    handoff without mutating cron, timers, or services.
  - Recommendations: `ready_for_scheduler_handoff`,
    `not_ready_for_scheduler_handoff`, `pi_required`, `inspect`.
  - `stop_reasons`: Pi absent, manual wake/observation/recovery not ready,
    scheduler mutation attempted, rollback instructions missing, or
    frozen-contract regression.
- M6.7:
  - `ok=true` when required M6 reports pass, M5.7 evidence is preserved, and
    the final freeze performs only read-only evidence validation.
  - Recommendations: `m6_frozen_ready_for_scheduler_handoff`, `inspect`.
  - `stop_reasons`: required M6 report missing/not ready, M5.7 evidence
    missing or regressed, blocking audit anomaly, cron/timer/system mutation,
    provider call, or authority expansion.

## Report Schema Draft

All M6 reports should use the same top-level shape unless a stage has a narrow
reason to add extra fields:

```json
{
  "ok": false,
  "milestone": "M6.x",
  "recommendation": "inspect",
  "companion_home": "/path/to/CompanionHome",
  "pi_presence": {
    "required": false,
    "detected": false,
    "evidence": [],
    "claim": "local_only"
  },
  "profile": {
    "name": "m6-stage-name",
    "provider": "deepseek",
    "memory_mode": "json",
    "cron_replacement": false,
    "timer_installation": false,
    "scheduler_mutation_allowed": false,
    "semantic_shadow_authoritative": false,
    "real_wake_requested": false,
    "provider_generation_requested": false,
    "raw_output_storage_required": "hash_only",
    "dashboard_write_allowed": false,
    "system_config_mutation_allowed": false
  },
  "source_reports": {
    "m4_post_change_guard": {},
    "m5_quality_release": {},
    "m5_final_freeze": {}
  },
  "field_pilot": {
    "deployment_package": {},
    "manual_wake": {},
    "observation": {},
    "recovery": {},
    "scheduler_readiness": {}
  },
  "stages": [
    {
      "name": "stage_name",
      "status": "passed",
      "ok": true,
      "required": true,
      "message": "short evidence-backed result",
      "details": {}
    }
  ],
  "stop_reasons": [],
  "pending_reasons": [],
  "saved_at": "ISO-8601",
  "next_commands": {}
}
```

Schema rules:

- `ok=true` means all required stages passed and `stop_reasons=[]`.
- `ok=false` with `pending_reasons` and no `stop_reasons` means the next safe
  step is waiting for more evidence, usually real Pi presence or a longer
  observation window.
- `recommendation=inspect` means at least one required safety, authority,
  storage, deployment, quality, rollback, or report-contract stage failed.
- `pi_presence.detected=true` must be backed by concrete evidence from the Pi
  environment or an explicitly captured Pi report. Local development machines
  must not set this to true.
- Reports must never include secret values or raw model prose.
- Report snapshots should store paths, hashes, counts, stage names,
  recommendations, and timestamps.

## Stop Conditions

Stop M6 and inspect when any condition below appears:

- M5.7 final freeze is missing, not `ok=true`, or not
  `recommendation=m5_frozen_ready_for_m6`.
- M5.7 source evidence is overwritten, deleted, or cannot be read before M6
  final freeze.
- M4 post-change guard or M4 deploy/runtime contract regresses.
- Provider or memory mode is no longer `deepseek + json`.
- Semantic shadow becomes prompt-authoritative or writes shadow records into
  authoritative prompt context.
- Cron is replaced or a timer/service is installed by an M6 command.
- M6.6 attempts to edit scheduler state instead of only producing a readiness
  report.
- A real Pi result is claimed while the Pi is unavailable.
- A real wake is run outside the explicit M6.3 manual trial path.
- DeepSeek is called by M6.0-M6.2 or by any non-generative gate.
- Raw model output is stored by default.
- Dashboard/window gains write actions for wake, memory, request, deploy,
  quality, recovery, scheduler, or M6 reports.
- Pi system configuration is modified without an explicit, later manual
  deployment step.
- Secret values appear in reports, logs, dashboard HTML, or test output.
- Rejected wakes update companion state, context capsule, memory, requests, or
  dashboard status.
- Model self-narrative is written as durable factual memory.
- Human-visible companion output stops being Simplified Chinese.
- Backup/rollback drill cannot prove the target artifacts are recoverable.

## Testing Strategy

For M6.0:

```bash
git diff --check
```

For M6.1-M6.3 guarded local implementation:

```bash
.venv/bin/python -m pytest tests/test_internal_life_loop.py -q \
  -k 'm6_preflight or m6_pi_manual_wake'
.venv/bin/python -m compileall -q companion_core scripts tests window
.venv/bin/python scripts/run_m6_preflight.py \
  --companion-home /home/polaris/digital_life
.venv/bin/python scripts/run_m6_pi_manual_wake_trial.py \
  --companion-home /home/polaris/digital_life \
  --no-write-report
.venv/bin/python scripts/run_m4_post_change_guard.py \
  --companion-home /home/polaris/digital_life
.venv/bin/python scripts/run_m5_final_freeze.py \
  --companion-home /home/polaris/digital_life \
  --no-write-report
```

M6.0-M6.2 tests must not run a real wake or call DeepSeek. M6.3 local tests
must exercise only the guard path or use fake wake runners/report fixtures. A
local M6.3 command without `--confirm-real-pi-wake` is expected to fail closed
and must not create a provider client.

For M6.3-M6.7, validation must be run against the real Pi CompanionHome when a
claim depends on Pi presence. The reports should preserve the same
non-generative gate discipline after the manual wake trial: observation,
recovery readiness, scheduler readiness, and final freeze read evidence rather
than generating new companion output.

## M6.0 Acceptance Criteria

- This document exists and is linked from `docs/internal-life-loop.md`.
- `docs/m4-pi-runbook.md` points operators to M6 as a later field-pilot
  sequence without adding new real deployment steps.
- M6.0-M6.2 local development and M6.3+ Pi-required boundaries are explicit.
- M6 report schema drafts include `ok`, `recommendation`, and `stop_reasons`
  semantics.
- M6 final freeze requirements preserve M5.7 passing evidence.
- Verification for this documentation change has been run and reported.

## M6.1 Acceptance Criteria

- `docs/m6-pi-migration-checklist.md` exists.
- `life-loop/m6_migration_manifest.json` exists and is valid JSON.
- The manifest keeps `provider=deepseek`, `memory_mode=json`,
  `cron_replacement=false`, `semantic_shadow_authoritative=false`,
  `raw_output_storage_required=hash_only`, and
  `dashboard_write_allowed=false`.
- The manifest snapshots M4.7, M5.6, and M5.7 passing evidence without
  overwriting the source reports.
- Secret values, local `.env` files, `.secrets/`, `.venv/`, caches, and raw
  model outputs are excluded from default transfer.
- M6.1 verification is local and non-generative.

## M6.2 Acceptance Criteria

- `companion_core/m6_preflight.py` and `scripts/run_m6_preflight.py` exist.
- `life-loop/m6_preflight_report.json` exists after the local preflight command
  runs.
- The report returns `milestone=M6.2`,
  `recommendation=ready_for_real_pi_manual_wake`, and `stop_reasons=[]` when
  M6.1, M4.7, and M5.7 evidence are intact.
- The report keeps `real_wake_requested=false`,
  `provider_generation_requested=false`, `scheduler_mutation_allowed=false`,
  `system_config_mutation_allowed=false`, and `dashboard_write_allowed=false`.
- `pi_presence.required=false`; a local run may record
  `pi_presence.detected=false` without blocking M6.2.
- Tests cover ready, manifest-boundary failure, and CLI secret-redaction paths.

## M6.3 Guarded Entry Acceptance Criteria

- `companion_core/m6_manual_wake.py` and
  `scripts/run_m6_pi_manual_wake_trial.py` exist.
- Without `--confirm-real-pi-wake`, the M6.3 entry returns `ok=false` and does
  not delegate to the wake runner.
- With confirmation but without Raspberry Pi identity, the entry returns
  `recommendation=pi_required` and does not delegate to the wake runner.
- With M6.2 ready, explicit confirmation, Raspberry Pi identity, and a passing
  delegate wake report, the entry returns `milestone=M6.3` and
  `recommendation=continue_pi_observation`.
- The guarded local tests use fake wake runners or blocked CLI paths; they do
  not call DeepSeek, run a real wake, edit scheduler/system configuration, or
  create a canonical real-Pi success claim.
