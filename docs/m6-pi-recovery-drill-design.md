# M6.5 Pi Backup, Rollback, And Recovery Drill Design

Status: implemented and verified on Pi
Last updated: 2026-06-19

## Decision

M6.5 proves that Pi-managed runtime artifacts can be backed up and restored
without expanding authority, exposing secrets, or overwriting live state by
default. The default drill is non-destructive:

1. Build a backup package under an explicit operator-selected backup root.
2. Write a checksum manifest for scoped runtime artifacts.
3. Restore that backup into an isolated sandbox directory.
4. Verify restored checksums and JSON readability.
5. Write a redacted report.

M6.5 must not edit cron, install timers, enable services, run a wake, call
DeepSeek, mutate dashboard write surfaces, promote semantic memory, or copy
secret values into reports.

## Required Inputs

- Passing M6.3 report:
  `life-loop/m6_pi_manual_wake_report.json`
- Passing M6.4 report:
  `life-loop/m6_pi_observation_report.json`
- Explicit backup root, for example:
  `/home/polaris/digital_life/backups/m6`
- Restore sandbox path, either provided by the operator or created under the
  backup root.

The drill may run on the Pi only. A non-Pi run should return
`recommendation=pi_required` unless explicitly invoked in a fixture/test mode.

## Backup Scope

The default backup package covers runtime state needed to continue or roll back
the field pilot:

- `life-loop/*.json`
- `life-loop/wake_events.jsonl`
- `journals/`
- `memory-server/memory_store.json`
- `requests/requests.json`
- `window/status.json`
- `context/who_is_companion.txt`
- `context/who_is_human.txt`
- `context/now.txt`

The backup records `.secrets/` metadata only:

- expected secret file paths
- file presence
- file mode/owner metadata when readable
- environment variable names such as `DEEPSEEK_API_KEY`

The backup must not copy `.secrets/` contents, `.env` files, virtualenvs,
OMX/Codex local state, caches, raw model outputs, lock files, or process logs.

## Safety Rules

- The default drill never writes into live runtime paths after the backup is
  created.
- Live restore is out of scope for the default M6.5 command.
- Any future live restore command must require all of:
  - explicit backup path
  - explicit restore scope
  - `--confirm-live-restore`
  - a fresh passing checksum manifest
  - a pre-restore backup of the current live state
- Reports may include hashes, sizes, paths, counts, modes, and key names, but
  never secret values or raw model prose.
- Backup and restore verification should fail closed on invalid JSON, checksum
  mismatch, missing required artifacts, or accidental secret/raw-output copy.

## Expected CLI Contract

The implementation should add:

```bash
.venv/bin/python scripts/run_m6_recovery_drill.py \
  --companion-home /home/polaris/digital_life \
  --backup-root /home/polaris/digital_life/backups/m6
```

Default behavior:

- require real Raspberry Pi identity
- require passing M6.4 observation
- create a timestamped backup directory
- create a restore sandbox under the backup root
- verify checksums and JSON readability
- write `life-loop/m6_recovery_drill_report.json`
- return non-zero only for `recommendation=inspect`

Expected options:

- `--restore-sandbox PATH`
- `--m6-observation-report PATH`
- `--no-write-report`
- `--report-file PATH`
- `--fixture-allow-non-pi` for tests only

## Expected Report

```json
{
  "ok": true,
  "milestone": "M6.5",
  "recommendation": "rollback_recovery_ready",
  "companion_home": "/home/polaris/digital_life",
  "pi_presence": {
    "required": true,
    "detected": true,
    "evidence": ["Raspberry Pi 5 Model B Rev 1.0"],
    "claim": "real_pi_recovery_drill"
  },
  "profile": {
    "name": "m6-recovery-drill",
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
    "system_config_mutation_allowed": false,
    "signal_voice_hardware_activation_allowed": false,
    "live_restore_requested": false,
    "live_restore_executed": false
  },
  "backup": {
    "path": "backups/m6/20260619-123000",
    "artifact_count": 0,
    "byte_count": 0,
    "manifest": "backups/m6/20260619-123000/manifest.json"
  },
  "restore_sandbox": {
    "path": "backups/m6/20260619-123000/restore-sandbox",
    "verified_artifact_count": 0,
    "checksum_mismatch_count": 0
  },
  "secret_boundary": {
    "secret_values_copied": false,
    "metadata_only": true
  },
  "stages": [],
  "stop_reasons": []
}
```

Recommendation values:

- `rollback_recovery_ready`
- `pi_required`
- `inspect`

## Stop Conditions

M6.5 must return `inspect` for:

- M6.4 report missing, not `ok=true`, or not
  `recommendation=stable_pi_field_observed`
- backup root missing or not writable
- required artifact missing from the backup
- restore sandbox checksum mismatch
- invalid JSON after sandbox restore
- secret value copied into backup, restore sandbox, or report
- raw model output copied into backup or restore sandbox
- live runtime overwrite/delete attempted by the default drill
- scheduler, cron, timer, service, Signal, voice, camera, sensor, hardware, or
  dashboard write mutation attempted

## Test Plan

- ready path creates a backup, restores into sandbox, verifies checksums, and
  writes a passing M6.5 report
- missing M6.4 report blocks with `inspect`
- non-Pi identity blocks with `pi_required`
- secret fixture proves secret values are not copied or printed
- raw output directory fixture blocks backup
- checksum mismatch fixture blocks restore verification
- default drill proves live runtime files are unchanged
- CLI `--no-write-report` avoids writing the canonical report

## Next Stage Handoff

M6.6 scheduler readiness may proceed only after M6.5 writes
`life-loop/m6_recovery_drill_report.json` with:

```text
ok = true
milestone = M6.5
recommendation = rollback_recovery_ready
stop_reasons = []
```

Current Pi evidence:

- Report: `life-loop/m6_recovery_drill_report.json`
- Recommendation: `rollback_recovery_ready`
- Backup package: `backups/m6/20260619-125958`
- Restored artifacts: 28
- Checksum mismatches: 0
- Invalid restored JSON files: 0
- Secret values copied: false
- Live restore executed: false
