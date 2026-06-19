# M6.6 Pi Scheduler Handoff Readiness Design

Status: implemented and verified on Pi
Last updated: 2026-06-19

## Decision

M6.6 decides whether the frozen DeepSeek/json wake path is ready to be handed to
the existing scheduler or cron process. It does not perform the handoff.

The default command is read-only:

1. Load M6.3 real Pi manual wake evidence.
2. Load M6.4 stable observation evidence.
3. Load M6.5 backup/recovery evidence.
4. Re-run current M4/M5 no-authority-expansion guards.
5. Verify operator-visible handoff, pause, and rollback instructions.
6. Write `life-loop/m6_scheduler_readiness_report.json`.

M6.6 must not edit cron, install timers, enable services, run a wake, call
DeepSeek, mutate dashboard write surfaces, promote semantic memory, copy secret
values, or execute a live restore.

## Required Inputs

- Passing M6.3 report:
  `life-loop/m6_pi_manual_wake_report.json`
- Passing M6.4 report:
  `life-loop/m6_pi_observation_report.json`
- Passing M6.5 report:
  `life-loop/m6_recovery_drill_report.json`
- Current M4.7 post-change guard result.
- Current M5.7 final freeze result.
- Operator-visible rollback instructions in this document.

The readiness decision may run on the Pi only. A non-Pi run should return
`recommendation=pi_required` unless explicitly invoked in fixture/test mode.

## Handoff Command

M6.6 may report this command as the scheduler target, but must not install it:

```bash
cd /home/polaris/digital_life && \
  .venv/bin/python scripts/run_wake_cycle.py \
    --companion-home /home/polaris/digital_life \
    --provider deepseek \
    --memory-mode json \
    --trigger scheduled-wake
```

Any later cron edit must be an explicit operator action outside M6.6 and only
after `recommendation=ready_for_scheduler_handoff`.

## Pause Instructions

Before any M7 scheduled-cadence pilot, the operator must know how to pause:

```bash
crontab -l
# remove or comment the scheduled-wake entry, then reinstall the edited crontab
```

If a service wrapper is introduced after M6, the equivalent pause command must
be documented before enabling it. M6.6 itself does not create such a service.

## Rollback Instructions

M6.5 already created a verified backup package under `backups/m6/`. For the
current Pi evidence, the latest passing backup package is:

```text
backups/m6/20260619-125958
```

Rollback policy:

- Prefer pause first: stop the scheduler before restoring state.
- Verify the selected backup manifest before any live restore.
- Take a fresh backup of live state before overwriting anything.
- Do not copy `.secrets/` values from reports or backups; restore secret files
  only from the operator-controlled secret source if needed.
- Live restore is still outside M6.6. Any later live restore command must use an
  explicit confirmation flag and must not be bundled with scheduler enablement.

## Expected CLI Contract

The implementation should add:

```bash
.venv/bin/python scripts/run_m6_scheduler_readiness.py \
  --companion-home /home/polaris/digital_life
```

Default behavior:

- require real Raspberry Pi identity
- require passing M6.3, M6.4, and M6.5 reports
- re-run current M4.7 and M5.7 read-only guards
- verify handoff command target exists
- verify pause and rollback instructions are present
- write `life-loop/m6_scheduler_readiness_report.json`
- return non-zero only for `recommendation=inspect`

Expected options:

- `--manual-wake-report PATH`
- `--observation-report PATH`
- `--recovery-report PATH`
- `--rollback-instructions PATH`
- `--fixture-allow-non-pi` for tests only
- `--no-write-report`
- `--report-file PATH`

## Expected Report

```json
{
  "ok": true,
  "milestone": "M6.6",
  "recommendation": "ready_for_scheduler_handoff",
  "profile": {
    "name": "m6-scheduler-handoff-readiness",
    "provider": "deepseek",
    "memory_mode": "json",
    "cron_replacement": false,
    "timer_installation": false,
    "scheduler_mutation_allowed": false,
    "scheduler_mutation_attempted": false,
    "real_wake_requested": false,
    "provider_generation_requested": false,
    "live_restore_requested": false,
    "live_restore_executed": false
  },
  "handoff": {
    "ready": true,
    "mutated": false,
    "target_command": "cd /home/polaris/digital_life && .venv/bin/python scripts/run_wake_cycle.py --companion-home /home/polaris/digital_life --provider deepseek --memory-mode json --trigger scheduled-wake"
  },
  "rollback": {
    "instructions_present": true,
    "latest_verified_backup": "backups/m6/20260619-125958"
  },
  "stages": [],
  "stop_reasons": []
}
```

Recommendation values:

- `ready_for_scheduler_handoff`
- `not_ready_for_scheduler_handoff`
- `pi_required`
- `inspect`

## Stop Conditions

M6.6 must return `inspect` for:

- M6.3 report missing, not `ok=true`, not
  `recommendation=continue_pi_observation`, or not executed on a real Pi.
- M6.4 report missing, not `ok=true`, or not
  `recommendation=stable_pi_field_observed`.
- M6.5 report missing, not `ok=true`, or not
  `recommendation=rollback_recovery_ready`.
- M6.5 backup/restore evidence missing, checksum mismatch count nonzero,
  invalid restored JSON count nonzero, secret values copied, or live restore
  executed.
- Current M4.7 or M5.7 evidence fails.
- Handoff command target is missing.
- Pause or rollback instructions are missing.
- Any scheduler, cron, timer, service, Signal, voice, camera, sensor, hardware,
  dashboard write, provider generation, wake, or live restore mutation is
  attempted by the readiness command.

M6.6 should return `pi_required` when the real Pi identity is required but not
detected.

## Test Plan

- ready path returns `ready_for_scheduler_handoff` without scheduler mutation
- missing M6.5 recovery evidence blocks readiness
- non-Pi identity returns `pi_required`
- missing rollback instructions blocks readiness
- M6.5 report with `secret_values_copied=true` blocks readiness
- CLI writes `life-loop/m6_scheduler_readiness_report.json`
- `/life` shows M6 scheduler readiness without adding write routes

## Next Stage Handoff

M6.7 final freeze may proceed only after M6.6 writes
`life-loop/m6_scheduler_readiness_report.json` with:

```text
ok = true
milestone = M6.6
recommendation = ready_for_scheduler_handoff
stop_reasons = []
```

Current Pi evidence:

- Report: `life-loop/m6_scheduler_readiness_report.json`
- Recommendation: `ready_for_scheduler_handoff`
- Target command:
  `cd /home/polaris/digital_life && .venv/bin/python scripts/run_wake_cycle.py --companion-home /home/polaris/digital_life --provider deepseek --memory-mode json --trigger scheduled-wake`
- Scheduler mutated: false
- Latest verified backup: `backups/m6/20260619-125958`
- Rollback instructions present: true
- Live restore executed: false
