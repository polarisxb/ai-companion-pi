# M4 Deployment Runtime Design

Status: M4.8 local implementation complete
Last updated: 2026-06-15

## Decision

M4 is the Raspberry Pi deployment/runtime milestone for the internal life loop.
It does not change memory authority and does not replace cron. It takes the
M3-frozen `deepseek + json` path and makes it deployable, observable, and
manually testable on the Pi.

## M4.1 Scope

M4.1 is the direction and design slice. It is complete when the M4 deployment
runtime direction, non-goals, failure policy, read-only dashboard boundary, and
initial report schemas are documented.

M4.1 does not implement runtime scripts yet. Implementation starts in M4.2.

## Goals

- Deploy and validate the M3-frozen life loop on Raspberry Pi.
- Provide repeatable scripts/templates that the operator runs manually.
- Standardize health/readiness checks and manual real DeepSeek wake testing.
- Record M4 deploy and wake-trial reports under `life-loop/`.
- Extend `/life` with read-only M3/M4 deployment observability.

## Non-goals

- No cron replacement.
- No new enabled timer.
- No semantic-memory authority promotion.
- No Signal, voice, camera, body, or hardware milestone.
- No dashboard write actions.
- No requirement that initial M4 completion prove 24-72 hour continuous runtime.

## Deployment Contract

M4 assumes M3.26 has already passed:

```text
life-loop/m3_final_freeze_report.json
recommendation = m3_frozen_ready_for_m4
provider = deepseek
memory_mode = json
cron_replacement = false
semantic_shadow_authoritative = false
```

If M3 final freeze is missing or not ready, M4 deployment readiness must fail
before running a real wake.

## Proposed Artifacts

```text
companion_core/deploy_runtime.py
scripts/run_m4_deploy_check.py
scripts/run_m4_wake_trial.py
scripts/run_m4_runtime_validation.py
docs/m4-pi-runbook.md
life-loop/m4_deploy_report.json
life-loop/m4_wake_trial_report.json
life-loop/m4_runtime_validation_report.json
```

`run_m4_deploy_check.py` should verify Pi/runtime readiness without running a
wake. `run_m4_wake_trial.py` should run the standard manual real DeepSeek wake
test and write a bounded report.

## Service Manager Position

M4 should not introduce a new wake service or timer. The first implementation
should keep wake execution manual through the M4 wake-trial command.

For existing long-running surfaces, prefer compatibility with the repository's
current PM2 shape:

- dashboard/window may continue to run through `scripts/start_window.sh`;
- memory HTTP may continue to run through `scripts/start_memory_http.sh`;
- M4 deploy check may detect PM2 status when PM2 is installed, but PM2 is not a
  hard dependency for local unit tests;
- systemd unit templates are deferred unless PM2 proves unsuitable on the real
  Pi.

This keeps M4 focused on deploy readiness and real manual testing, not service
manager migration.

## M4.2 Deploy Check

`scripts/run_m4_deploy_check.py` runs the M4.2 deploy-readiness gate and writes
`life-loop/m4_deploy_report.json` by default:

```bash
python3 scripts/run_m4_deploy_check.py \
  --companion-home /path/to/CompanionHome
```

The check is local and non-generative. It does not run a wake, does not call
DeepSeek, does not replace cron, does not install timers, and does not change
semantic-memory authority.

## Runtime Checks

M4 deploy readiness should check:

- M3 final freeze is ready.
- Frozen deployment contract is still `deepseek + json` with
  `cron_replacement=false` and `semantic_shadow_authoritative=false`.
- Current semantic shadow records remain isolated from prompt authority.
- Python runtime is usable; virtualenv absence is visible as an advisory status.
- Required imports for JSON mode are available.
- DeepSeek API key is present but never printed.
- Context files exist and are customized.
- `life-loop`, `journals`, `requests`, `memory-server`, `window`, and log paths
  are writable.
- Dashboard/window runtime files are present.
- Dashboard reachability is advisory in M4.2 and is not required to pass.
- Raw model output storage is hash-only by default.
- Semantic shadow is still non-authoritative.

## Manual Wake Trial

`scripts/run_m4_wake_trial.py` wraps the same `LifeLoopRunner` path used by
`scripts/run_wake_cycle.py` with a Pi-oriented report:

```text
provider = deepseek
memory_mode = json
trigger = m4-pi-manual-wake
cron_replacement = false
raw_output_storage = hash_only
```

The trial may retry once for network/timeout infrastructure errors. It must not
retry parser failures, grounding failures, authority failures, memory-write
failures, or other content/policy failures.

The wrapper requires `life-loop/m4_deploy_report.json` to be
`recommendation=ready_for_manual_wake` before it creates a provider client.

## Reports

`m4_deploy_report.json` includes:

- `ok`
- `milestone = M4.2`
- `recommendation = ready_for_manual_wake | inspect`
- `companion_home`
- `profile`
- `stages`
- `stop_reasons`
- `saved_at`
- `frozen_commands`
- `next_commands`

`m4_wake_trial_report.json` should include:

- `ok`
- `milestone = M4.3`
- `recommendation = continue_runtime_validation | inspect`
- `attempts`
- `retry_policy`
- `latest_event`
- `quality_gate`
- `grounding`
- `semantic_shadow`
- `output_audit`
- `failure_audit`
- `stop_reasons`

Reports should store hashes, counts, paths, stage names, and failure reasons,
not raw model prose or secret values.

## Runtime Validation Seal

`scripts/run_m4_runtime_validation.py` is the M4.6 close-out gate. It does not
run a wake and does not call DeepSeek. It validates the latest M4 deploy and
wake-trial reports, re-audits semantic shadow isolation, verifies hash-only
output audit metadata, checks the latest wake event and journal path, and
inspects the Flask route map to keep the `/life` M3/M4 dashboard surface
read-only.

```bash
python3 scripts/run_m4_runtime_validation.py \
  --companion-home /path/to/CompanionHome
```

The report is written to `life-loop/m4_runtime_validation_report.json` by
default and includes:

- `ok`
- `milestone = M4.6`
- `recommendation = m4_runtime_validated | inspect`
- `source_reports`
- `stages`
- `stop_reasons`
- `next_commands`

The platform identity stage is advisory by default. On a real Pi handoff, the
operator may pass `--require-raspberry-pi` to make Raspberry Pi platform
detection a hard gate.

`scripts/run_m4_post_change_guard.py` is the M4.7 compatibility guard for
continued development while the Pi is unavailable. It is also non-generative: it
runs the current deploy check, reuses the existing M4 wake-trial report through
the runtime validation seal, and writes
`life-loop/m4_post_change_guard_report.json` with
`recommendation=m4_still_deployable | inspect`.

`scripts/run_m4_observation_check.py` is the M4.8 long-running observation gate.
It does not run a wake and does not call DeepSeek. It reads wake events in a
configurable observation scope and reports:

- `stable_runtime_observed` when the required observation window and completed
  wake count are met without hard event failures;
- `continue_observation` when the window is still incomplete but no hard
  failures are present;
- `inspect` when wake events show failed status, rejected context writes,
  unsupported grounding, memory/request failures, or raw output storage.

## Dashboard

`/life` remains read-only. It should show:

- M3 release gate status.
- M3 final freeze status.
- M4 deploy check status.
- Latest M4 manual wake trial status.
- Retry count and retry reason.
- Failure audit category: infrastructure, provider, parser, grounding,
  authority, memory, request, or unknown.

The dashboard must not trigger wake, replay, predeploy, deployment, config edits,
or memory edits.

## Testing Strategy

- Unit-test deploy-report pass/fail stages.
- Unit-test final-freeze prerequisite failure.
- Unit-test retry classification.
- Unit-test no retry for grounding/authority failures.
- CLI tests for report writing.
- Flask `/life` tests for read-only M3/M4 panel rendering.
- Full pytest before claiming any M4 slice complete.

## Milestone Slices

1. M4.1: finalize this design and report schemas. Current status: design draft
   complete.
2. M4.2: implement deploy check. Current status: implemented.
3. M4.3: implement manual wake trial wrapper with safe retry. Current status:
   implemented.
4. M4.4: add read-only dashboard panels. Current status: implemented.
5. M4.5: write Pi runbook and run supervised Pi validation. Current status:
   runbook written; manual DeepSeek validation has a standard command.
6. M4.6: validate deploy report, real wake-trial report, semantic-shadow
   isolation, hash-only output audit, latest event journal, and dashboard
   read-only route boundary. Current status: implemented.
7. M4.7: add a non-generative post-change guard so development can continue
   without silently breaking Pi deploy readiness. Current status: implemented.
8. M4.8: add a non-generative long-running observation gate for future Pi
   24-72 hour validation. Current status: implemented.

## Open Questions

- Whether dashboard reachability should be checked by process status, HTTP
  request, or both.
- Whether M4.5 should require 24 hours or 72 hours of later runtime observation.
