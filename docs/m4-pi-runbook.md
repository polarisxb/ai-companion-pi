# M4 Raspberry Pi Runbook

Status: M4.6 runbook complete
Last updated: 2026-06-14

## Boundary

M4 deploys and validates the M3-frozen `deepseek + json` companion life loop on
Raspberry Pi. It does not replace cron, install timers, promote semantic memory,
add dashboard write actions, or require continuous 24-72 hour runtime as the
first completion gate.

Only the M4 wake-trial command runs a real provider wake. All other commands are
local checks, local setup, or read-only inspection.

## Deployment Model

This runbook is intentionally manual. The repository provides repeatable
commands and reports, but it does not silently edit Pi system configuration,
replace cron, install a timer, or expose services to the LAN.

Use one CompanionHome on the Pi:

```bash
export COMPANION_HOME="$HOME/digital_life"
export COMPANION_VENV_DIR="$COMPANION_HOME/.venv"
```

The examples below assume the repository is at `$COMPANION_HOME`.

## 1. Prepare the Pi

On the Raspberry Pi, install the basic runtime tools:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
python3 --version
```

Use Python 3.10 or newer. If the Pi already has these tools, keep the existing
setup and continue.

## 2. Put the Project on the Pi

Use the source path that is authoritative for your deployment.

Git clone path:

```bash
git clone <repo-url> "$COMPANION_HOME"
cd "$COMPANION_HOME"
```

Existing checkout path:

```bash
cd "$COMPANION_HOME"
git pull --ff-only
```

Local sync path from a development machine:

```bash
rsync -a --delete \
  --exclude .git \
  --exclude .venv \
  --exclude __pycache__ \
  /path/to/digital_life/ \
  pi@<pi-host>:/home/pi/digital_life/
```

Do not sync a development `.venv`; build the Pi virtualenv on the Pi.

## 3. Create the Pi Virtualenv

```bash
cd "$COMPANION_HOME"
python3 -m venv "$COMPANION_VENV_DIR"
"$COMPANION_VENV_DIR/bin/python" -m pip install --upgrade pip
"$COMPANION_VENV_DIR/bin/python" -m pip install -r requirements.txt
```

For development-only verification on the Pi, install test dependencies too:

```bash
"$COMPANION_VENV_DIR/bin/python" -m pip install -r requirements-dev.txt
```

If heavyweight optional packages fail on the Pi, stop and inspect the package
failure. Do not work around it by changing M4's `deepseek + json` deployment
contract or promoting semantic memory.

## 4. Configure Secrets and Context

Create a local secret file on the Pi:

```bash
mkdir -p "$COMPANION_HOME/.secrets"
chmod 700 "$COMPANION_HOME/.secrets"
printf 'DEEPSEEK_API_KEY=%s\n' '<your-deepseek-key>' > "$COMPANION_HOME/.secrets/deepseek.env"
chmod 600 "$COMPANION_HOME/.secrets/deepseek.env"
```

The M4 reports only show secret metadata and must never print the key value.

Customize these files for the Pi deployment:

```bash
$EDITOR "$COMPANION_HOME/context/who_is_companion.txt"
$EDITOR "$COMPANION_HOME/context/who_is_human.txt"
$EDITOR "$COMPANION_HOME/context/now.txt"
```

They must not be empty, untouched templates, or placeholder text.

## 5. Preserve M3 Freeze Artifacts

Before running M4 on the Pi, make sure the M3 freeze report exists:

```bash
test -f "$COMPANION_HOME/life-loop/m3_final_freeze_report.json"
```

If it is missing, run the frozen M3 release/final-freeze flow in the Pi
CompanionHome:

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m3_release_gate.py \
  --provider deepseek \
  --memory-mode json

"$COMPANION_VENV_DIR/bin/python" scripts/run_m3_final_freeze.py \
  --expected-provider deepseek \
  --expected-memory-mode json
```

These M3 commands are non-real-wake gates by default.

## 6. Run M4 Deploy Check

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m4_deploy_check.py \
  --companion-home "$COMPANION_HOME"
```

Expected report:

```text
life-loop/m4_deploy_report.json
ok=true
recommendation=ready_for_manual_wake
stop_reasons=[]
```

This check does not call DeepSeek. It verifies the M3 freeze contract, runtime
imports, DeepSeek key metadata, context customization, writable runtime paths,
hash-only raw output storage, semantic shadow isolation, and dashboard/window
runtime files.

If the recommendation is `inspect`, do not run the manual wake trial. Fix the
listed `stop_reasons` and rerun the deploy check.

## 7. Run One Manual Wake Trial

Run this only after the deploy check is ready:

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m4_wake_trial.py \
  --companion-home "$COMPANION_HOME" \
  --timeout 300
```

Expected report:

```text
life-loop/m4_wake_trial_report.json
ok=true
recommendation=continue_runtime_validation
```

The wrapper runs at most two attempts. It retries once only for infrastructure
failures such as network errors or timeouts. It does not retry provider
configuration failures, parser/content failures, grounding failures, authority
gate rejections, memory write failures, or request write failures.

The report stores event ids, gate summaries, retry metadata, failure category,
hash-only output-audit metadata, grounding summary, and semantic-shadow summary.
It must not store raw model prose or secret values.

## 8. Start Read-Only Runtime Surfaces

Start dashboard/window manually:

```bash
cd "$COMPANION_HOME"
COMPANION_HOME="$COMPANION_HOME" \
COMPANION_VENV_DIR="$COMPANION_VENV_DIR" \
scripts/start_window.sh
```

Open `/life` and verify:

- M3 release gate status.
- M3 final-freeze status.
- M4 deploy status.
- Latest M4 wake-trial status.
- Retry count and retry reason.
- Failure audit category.

Start memory HTTP only if that surface is part of your Pi runtime:

```bash
cd "$COMPANION_HOME"
COMPANION_HOME="$COMPANION_HOME" \
COMPANION_VENV_DIR="$COMPANION_VENV_DIR" \
COMPANION_MEMORY_HOST=127.0.0.1 \
scripts/start_memory_http.sh
```

The default memory HTTP host is local-only. Do not set a LAN host unless you are
intentionally exposing it and have reviewed the security boundary.

PM2 can supervise these existing scripts if you already use PM2, but M4 does
not require PM2 and does not install it.

## 9. Seal Runtime Validation

After the deploy check and one manual wake trial have passed, run:

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m4_runtime_validation.py \
  --companion-home "$COMPANION_HOME" \
  --require-raspberry-pi
```

Expected report:

```text
life-loop/m4_runtime_validation_report.json
ok=true
recommendation=m4_runtime_validated
stop_reasons=[]
```

This command is read-only with respect to provider behavior: it does not call
DeepSeek and does not run another wake. It validates the M4 deploy report, M4
wake-trial report, latest event journal path, hash-only output audit,
semantic-shadow isolation, and the `/life` dashboard route boundary.

## 10. Optional Verification

Run the full local test suite on the Pi only if test dependencies are installed:

```bash
"$COMPANION_VENV_DIR/bin/python" -m pytest
```

This is not a replacement for the three M4 reports. The deployment decision
comes from `m4_deploy_report.json`, `m4_wake_trial_report.json`, and
`m4_runtime_validation_report.json`.

## Continuing Development Without the Pi

When the Raspberry Pi is not available, continued development is allowed as
long as M4 remains a frozen deployment baseline. Avoid changing these surfaces
unless the change is explicitly deployment-related:

- `companion_core/lifecycle.py`
- `companion_core/llm.py`
- `companion_core/parser.py`
- `companion_core/memory.py`
- `companion_core/deploy_runtime.py`
- `companion_core/wake_trial.py`
- `companion_core/m4_validation.py`
- `scripts/run_m4_*.py`
- `scripts/start_window.sh`
- `scripts/start_memory_http.sh`
- `window/window.py`
- `requirements.txt`

After any development slice, run the non-generative M4 post-change guard:

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m4_post_change_guard.py \
  --companion-home "$COMPANION_HOME"
```

Expected report:

```text
life-loop/m4_post_change_guard_report.json
ok=true
recommendation=m4_still_deployable
stop_reasons=[]
```

This guard does not call DeepSeek and does not run another wake. It checks the
current deploy-readiness path and reuses the existing successful wake-trial
report through the M4.6 runtime validation seal.

## Long-Running Observation

After the Pi is deployed and manual M4 validation has passed, start an
observation window from the deployment timestamp:

```bash
"$COMPANION_VENV_DIR/bin/python" scripts/run_m4_observation_check.py \
  --companion-home "$COMPANION_HOME" \
  --hours 24 \
  --min-events 2 \
  --since "<deployment-iso-timestamp>"
```

Expected in-progress report:

```text
life-loop/m4_observation_report.json
ok=false
recommendation=continue_observation
```

Expected completed report:

```text
life-loop/m4_observation_report.json
ok=true
recommendation=stable_runtime_observed
stop_reasons=[]
```

This command does not call DeepSeek and does not run a wake. It only reads
`life-loop/wake_events.jsonl`. Use `--hours 72` for a longer stability window.

## Failure Categories

- `infrastructure`: network, timeout, connection, transient HTTP 5xx.
- `provider`: missing key, invalid provider configuration, non-retryable provider
  response.
- `parser`: structured output could not be consumed.
- `grounding`: unsupported factual continuity claim.
- `authority`: context/write gate rejected future-context authority.
- `memory`: authoritative JSON memory write failed.
- `request`: request persistence failed.
- `unknown`: unexpected failure requiring manual inspection.

Only `infrastructure` is retryable, and only once.

## Stop Conditions

Stop and inspect when:

- M4 deploy check is not `ready_for_manual_wake`.
- M4 wake trial is not `continue_runtime_validation`.
- M4 runtime validation is not `m4_runtime_validated`.
- Raw output storage is enabled.
- Semantic shadow becomes prompt-authoritative or writes into the main store as
  shadow records.
- `/life` shows dashboard write controls for M3/M4 operations.
- A real wake fails after its one allowed infrastructure retry.

## Operator Notes

Use the project virtualenv explicitly when the shell `python3` does not have the
dashboard/runtime dependencies.

After M4.6, longer 24-72 hour observation can be defined as a later stability
gate. It is not required for this first M4 deployment-runtime completion.

## M6 Field Pilot Pointer

M6 continues from the M5.7 final freeze as a Pi field pilot. The M6 design is in
`docs/m6-pi-field-pilot-design.md`, and the M6.1 migration package boundary is
in `docs/m6-pi-migration-checklist.md`.

This M4 runbook remains the M4 deployment/runtime reference. It should not be
treated as authorization to replace cron, install timers, edit Pi system
configuration, add Signal/voice/hardware, or run unscheduled real wakes. M6.0
through M6.2 can be developed locally with non-generative checks. M6.3 and later
require the real Raspberry Pi before claiming Pi manual wake, observation,
recovery, scheduler readiness, or final freeze evidence.

The M6.3 guarded manual-wake entry is
`scripts/run_m6_pi_manual_wake_trial.py`. It requires a passing M6.2 preflight
report, explicit `--confirm-real-pi-wake`, and Raspberry Pi identity before it
delegates to the frozen M4 wake-trial wrapper. This entry is not a cron handoff
and does not authorize timer, service, dashboard-write, Signal, voice, or
hardware changes.
