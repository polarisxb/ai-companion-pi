# M6 Pi Migration Checklist

Status: M6.1 migration package ready for local preflight
Last updated: 2026-06-15

## Boundary

M6.1 defines what may be migrated to the Raspberry Pi field-pilot surface and
what must stay local, secret, or inactive. It does not run a wake, call
DeepSeek, modify Pi system configuration, replace cron, install timers, change
memory authority, or add Signal/voice/hardware behavior.

The migration package carries the frozen M3-M5 companion loop to the Pi. It is
not permission to expand companion ability.

## Required Source Evidence

M6.1 starts only when these reports are present and passing:

```text
life-loop/m4_post_change_guard_report.json
ok = true
recommendation = m4_still_deployable

life-loop/m5_quality_release_report.json
ok = true
recommendation = m5_quality_ready_for_m6

life-loop/m5_final_freeze_report.json
ok = true
recommendation = m5_frozen_ready_for_m6
```

The migration manifest snapshots these reports by path, milestone,
recommendation, timestamp, and selected frozen-contract fields. It must not
overwrite the source reports.

## Package Inventory

The Pi deployment package should include the repository source required for the
frozen life loop:

- `companion_core/`
- `scripts/run_wake_cycle.py`
- `scripts/run_pi_predeploy.py`
- `scripts/run_m3_release_gate.py`
- `scripts/run_m3_final_freeze.py`
- `scripts/run_m4_deploy_check.py`
- `scripts/run_m4_wake_trial.py`
- `scripts/run_m4_runtime_validation.py`
- `scripts/run_m4_post_change_guard.py`
- `scripts/run_m4_observation_check.py`
- `scripts/run_m5_quality_check.py`
- `scripts/run_m5_quality_trial.py`
- `scripts/run_m5_quality_release_gate.py`
- `scripts/run_m5_final_freeze.py`
- `scripts/run_m6_pi_observation_check.py`
- `scripts/run_m6_recovery_drill.py`
- `scripts/run_m6_scheduler_readiness.py`
- `scripts/run_m6_final_freeze.py`
- `scripts/replay_wake_output.py`
- `scripts/run_replay_regression.py`
- `scripts/start_window.sh`
- `scripts/start_memory_http.sh`
- `window/window.py`
- `memory-server/`
- `requests/`
- `templates/`
- `docs/internal-life-loop.md`
- `docs/m4-deployment-runtime-design.md`
- `docs/m4-pi-runbook.md`
- `docs/m5-companion-quality-design.md`
- `docs/m6-pi-field-pilot-design.md`
- `docs/m6-pi-recovery-drill-design.md`
- `docs/m6-pi-scheduler-readiness-design.md`
- `docs/m6-pi-final-freeze-design.md`
- `docs/m6-pi-migration-checklist.md`
- `requirements.txt`
- `requirements-dev.txt`
- `setup.sh`

The package may include other repository files as inert source, but M6 does not
activate Signal, voice, sensors, camera, hardware, Substack, or broad dashboard
write behavior.

## Runtime State To Preserve

The Pi CompanionHome must preserve these runtime artifacts when they exist:

- `life-loop/m3_release_gate_report.json`
- `life-loop/m3_final_freeze_report.json`
- `life-loop/m4_deploy_report.json`
- `life-loop/m4_wake_trial_report.json`
- `life-loop/m4_runtime_validation_report.json`
- `life-loop/m4_post_change_guard_report.json`
- `life-loop/m4_observation_report.json`
- `life-loop/m5_quality_report.json`
- `life-loop/m5_quality_trial_report.json`
- `life-loop/m5_quality_release_report.json`
- `life-loop/m5_final_freeze_report.json`
- `life-loop/m6_migration_manifest.json`
- `life-loop/m6_preflight_report.json`
- `life-loop/m6_pi_manual_wake_report.json`
- `life-loop/m6_pi_observation_report.json`
- `life-loop/m6_recovery_drill_report.json`
- `life-loop/m6_scheduler_readiness_report.json`
- `life-loop/m6_final_freeze_report.json`
- `life-loop/context_capsule.json`
- `life-loop/companion_state.json`
- `life-loop/wake_events.jsonl`
- `journals/`
- `memory-server/memory_store.json`
- `requests/requests.json`
- `window/status.json`
- `context/who_is_companion.txt`
- `context/who_is_human.txt`
- `context/now.txt`

Do not replace a Pi runtime artifact with an older development-machine artifact
unless the operator is intentionally restoring from a documented backup.

## Exclude From Transfer

Do not copy development-machine local state into the Pi package:

- `.git/` when using rsync-style transfer instead of clone/pull
- `.venv/`
- `__pycache__/`
- `.pytest_cache/`
- `.omx/`
- `.codex/`
- `.agents/`
- `.secrets/`
- `.env`
- `.env.*`
- `scripts/substack_config.local.sh`
- `node_modules/`
- `*.pid`
- `core.*`
- raw files under `life-loop/model_outputs/` unless a bounded replay capture is
  explicitly approved

The Pi may keep its own runtime state in ignored paths. The rule is to avoid
copying local development state over Pi-owned state by accident.

## Secret Boundary

Secret values are never stored in reports, manifests, dashboard HTML, logs, or
migration checklist output.

Allowed secret metadata:

- expected secret file path, such as `.secrets/deepseek.env`
- expected environment variable name, such as `DEEPSEEK_API_KEY`
- presence/absence status
- permission expectation, such as directory `0700` and file `0600`

Disallowed secret data:

- API key values
- token prefixes or suffixes
- copied `.env` contents
- shell history containing exported secret values
- raw provider request/response bodies

The Pi operator should create or preserve secrets on the Pi. M6.1 does not copy
secrets from the development machine.

## Local Network Boundary

M6 keeps existing read-only local surfaces local by default:

- Dashboard/window remains read-only.
- Memory HTTP remains local-only unless separately reviewed.
- No new LAN exposure is introduced by M6.1.
- M6.1 does not open firewall ports or edit router/system network config.

## Scheduler Boundary

M6.1 does not edit scheduler state. It does not replace cron, install timers,
enable services, or modify crontab.

Scheduler handoff is only a readiness decision in M6.6. Any later cron change
requires an explicit operator step outside M6.1.

## Ownership And Permission Expectations

Expected Pi ownership:

- Repository and runtime files are owned by the Pi deployment user.
- `.secrets/` is readable only by that user.
- Runtime directories are writable by that user.
- No M6.1 artifact requires `sudo` to run locally.

Expected runtime directories:

- `life-loop/`
- `journals/`
- `memory-server/`
- `requests/`
- `window/`
- `tasks/logs/`

## M6.1 Manifest

The canonical M6.1 manifest is:

```text
life-loop/m6_migration_manifest.json
```

It records:

- package inventory
- runtime artifacts to preserve
- excluded paths
- secret metadata boundary
- network and scheduler boundary
- source report snapshots
- M5.7 evidence carry-forward
- M6.7 final-freeze script and report carry-forward
- stage results and stop reasons

Expected result:

```text
ok = true
milestone = M6.1
recommendation = migration_manifest_ready
stop_reasons = []
```

M6.2 consumes this manifest through:

```bash
.venv/bin/python scripts/run_m6_preflight.py \
  --companion-home /home/polaris/digital_life
```

The M6.2 command writes `life-loop/m6_preflight_report.json` and remains local
and non-generative.

## Stop Conditions

Stop M6.1 and inspect when:

- M5.7 final freeze is missing or not `m5_frozen_ready_for_m6`.
- M4 post-change guard is missing or not `m4_still_deployable`.
- The migration manifest includes secret values.
- The package plan copies `.venv`, caches, `.env`, `.secrets`, or raw model
  outputs by default.
- The checklist instructs the operator to replace cron, install timers, or edit
  scheduler state.
- Signal, voice, camera, sensor, hardware, or dashboard write behavior is
  activated as part of migration.
- A local M6.1 step claims real Pi validation while the Pi is absent.
- A local M6.1 step calls DeepSeek or runs a wake.

## Local Verification

M6.1 verification is local and non-generative:

```bash
python3 -m json.tool life-loop/m6_migration_manifest.json >/dev/null
git diff --check
.venv/bin/python -m compileall -q companion_core scripts tests window
.venv/bin/python scripts/run_m5_final_freeze.py \
  --companion-home /home/polaris/digital_life \
  --no-write-report
```

These checks do not run a real wake, do not call DeepSeek, and do not modify Pi
system configuration.
