# M6.7 Pi Final Freeze Design

Status: implemented for Pi final-freeze verification
Last updated: 2026-06-19

## Decision

M6.7 freezes the M6 Raspberry Pi field pilot after M6.2, M6.5, and M6.6 have
passed. It is a read-only evidence gate for scheduler handoff readiness.

The default command:

1. Loads `life-loop/m6_preflight_report.json`.
2. Loads `life-loop/m6_recovery_drill_report.json`.
3. Loads `life-loop/m6_scheduler_readiness_report.json`.
4. Loads `life-loop/m6_migration_manifest.json` and verifies the M6.7 script,
   design, and report are in the evidence chain.
5. Re-runs current M4.7 post-change guard and current M5.7 final freeze checks.
6. Audits semantic shadow authority isolation.
7. Writes `life-loop/m6_final_freeze_report.json`.

M6.7 does not run a wake, call DeepSeek, edit cron/crontab, install timers,
enable services, perform a scheduler handoff, run live restore, mutate the
dashboard write surface, or promote semantic memory authority.

## Required Inputs

- M6.2 report:
  `life-loop/m6_preflight_report.json`
- M6.5 report:
  `life-loop/m6_recovery_drill_report.json`
- M6.6 report:
  `life-loop/m6_scheduler_readiness_report.json`
- M6.1 manifest updated with M6.7 evidence:
  `life-loop/m6_migration_manifest.json`
- Current M4.7 post-change guard result.
- Current M5.7 final freeze result.
- Backup package and manifest referenced by M6.5 and M6.6.

The final freeze may run on the Pi only by default. A non-Pi run should return
`recommendation=pi_required` unless explicitly invoked in fixture/test mode.

## Expected CLI Contract

```bash
.venv/bin/python scripts/run_m6_final_freeze.py \
  --companion-home /home/polaris/digital_life
```

Default behavior:

- require real Raspberry Pi identity
- require passing M6.2 preflight
- require passing M6.5 recovery evidence
- require passing M6.6 scheduler handoff readiness
- require current M4.7 deployability
- require current M5.7 final-freeze readiness
- verify scheduler mutation flags are false
- verify rollback/backup evidence exists
- verify semantic shadow remains non-authoritative
- write `life-loop/m6_final_freeze_report.json`
- return non-zero only for `recommendation=inspect`

Expected options:

- `--preflight-report PATH`
- `--recovery-report PATH`
- `--scheduler-report PATH`
- `--manifest PATH`
- `--fixture-allow-non-pi` for tests only
- `--no-write-report`
- `--report-file PATH`

## Expected Report

```json
{
  "ok": true,
  "milestone": "M6.7",
  "recommendation": "m6_frozen_ready_for_scheduler_handoff",
  "profile": {
    "name": "m6-final-freeze",
    "provider": "deepseek",
    "memory_mode": "json",
    "cron_replacement": false,
    "timer_installation": false,
    "service_enablement": false,
    "crontab_edit_allowed": false,
    "scheduler_mutation_allowed": false,
    "scheduler_mutation_attempted": false,
    "scheduler_handoff_performed": false,
    "semantic_shadow_authoritative": false,
    "real_wake_requested": false,
    "provider_generation_requested": false,
    "dashboard_write_allowed": false,
    "live_restore_requested": false,
    "live_restore_executed": false
  },
  "final_freeze": {
    "frozen": true,
    "readonly": true,
    "scheduler_handoff_ready": true,
    "scheduler_mutated": false
  },
  "rollback": {
    "ready": true,
    "latest_verified_backup": "backups/m6/20260619-125958",
    "live_restore_executed": false
  },
  "stages": [],
  "stop_reasons": []
}
```

Recommendation values:

- `m6_frozen_ready_for_scheduler_handoff`
- `pi_required`
- `inspect`

## Stop Conditions

M6.7 must return `inspect` for:

- M6.2 report missing, not `ok=true`, or not
  `recommendation=ready_for_real_pi_manual_wake`.
- M6.5 report missing, not `ok=true`, or not
  `recommendation=rollback_recovery_ready`.
- M6.6 report missing, not `ok=true`, or not
  `recommendation=ready_for_scheduler_handoff`.
- Current M4.7 or M5.7 evidence fails.
- The M6.1 manifest does not list the M6.7 script, design, or report artifact.
- Any scheduler, cron, timer, service, crontab, dashboard write, provider
  generation, live restore, or semantic authority mutation flag is true.
- M6.5 backup path or manifest is missing.
- M6.6 latest verified backup is missing or does not match the M6.5 backup.
- Restore sandbox checksum mismatch or invalid JSON count is nonzero.
- Semantic shadow becomes prompt-authoritative.

M6.7 should return `pi_required` when real Pi identity is required but not
detected.

## Test Plan

- ready path returns `m6_frozen_ready_for_scheduler_handoff` without scheduler
  mutation
- scheduler mutation regression blocks final freeze
- missing backup/rollback evidence blocks final freeze
- non-Pi identity returns `pi_required`
- CLI writes `life-loop/m6_final_freeze_report.json`
- `/life` shows M6 Final Freeze without adding write routes

## M7 Direction

M7 may start only after M6.7 is frozen. The first M7 work should be a scheduled
cadence pilot that installs or edits scheduler state only through an explicit
operator step, with pause/rollback evidence already visible from M6.5-M6.7.
