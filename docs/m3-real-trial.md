# M3 Real Trial Checklist

Use this checklist to trial the Python internal life loop as the companion-quality
track without replacing `wakeup.sh` or cron.

## 1. Check Runtime Readiness

Run the readiness checker before the first Pi or local real-provider trial:

```bash
python3 scripts/check_runtime_ready.py \
  --provider deepseek \
  --memory-mode dual \
  --companion-home /path/to/CompanionHome
```

The command prints JSON and exits `0` only when no check has `status: "failed"`.
Warnings are recoverable trial notes; failures should be fixed before a real
wake unless you intentionally switch to a lower-risk mode.

Useful variants:

```bash
# Basic local path/write/context check without network/provider preflight.
python3 scripts/check_runtime_ready.py \
  --provider fake \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --skip-provider-check

# JSON fallback mode if semantic dependencies are not ready yet.
python3 scripts/check_runtime_ready.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome
```

M3.8 low-risk trial path:

- Use `deepseek + json` for real companion-quality wakes while semantic
  dependencies are not installed.
- Treat `deepseek + dual` as a second stage. It should pass readiness before a
  dual real wake; otherwise the wake can preserve continuity through JSON
  fallback but does not prove semantic-first memory authority.
- On the current Python 3.14 virtualenv, a plain
  `pip install numpy sentence-transformers` resolves toward large `torch` and
  CUDA packages. Do not make that the default Pi trial path. Prefer a
  CPU-only/version-pinned semantic dependency plan before enabling dual mode on
  the Pi.

Readiness covers:

- Python version and whether a virtualenv is active
- required app imports `flask` and `markdown`
- optional MCP memory-server import `mcp`
- semantic memory imports `numpy` and `sentence_transformers`
- `DEEPSEEK_API_KEY` presence without printing its value
- customized `context/who_is_companion.txt`, `context/who_is_human.txt`, and `context/now.txt`
- valid JSON shape for existing memory/request stores
- writable `life-loop/`, `journals/`, `requests/`, `window/`, `window/content/`, and `memory-server/`
- semantic memory module importability and `SemanticMemoryStore` presence
- selected memory mode readiness
- optional provider preflight

Local DeepSeek secret file:

- `scripts/check_runtime_ready.py` and `scripts/run_wake_cycle.py` load
  `.secrets/deepseek.env` from `--companion-home` before provider checks.
- The file is ignored by git. Supported keys are `DEEPSEEK_API_KEY` and
  `COMPANION_LLM_API_KEY`.
- Existing shell environment variables take precedence over file values.
- The expected line format is `DEEPSEEK_API_KEY=...`; readiness reports only the
  key name and file path, never the secret value.

M3.21 Pi predeploy profile:

```bash
python3 scripts/run_pi_predeploy.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome
```

This runs target readiness, confirms raw output storage is hash-only, prepares
an isolated smoke home under `/tmp/companion-m321-pi-predeploy-smoke`, runs a
fake wake there, and then runs replay regression there. It does not replace cron
and does not write fake smoke artifacts into the target CompanionHome.

Use a custom smoke directory when you want the artifacts somewhere specific:

```bash
python3 scripts/run_pi_predeploy.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --smoke-home /tmp/companion-m321-my-smoke
```

Run one real DeepSeek wake only after the local predeploy stages pass:

```bash
python3 scripts/run_pi_predeploy.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --run-real-wake \
  --wake-timeout 300
```

For local/offline development of the predeploy flow itself:

```bash
python3 scripts/run_pi_predeploy.py \
  --provider fake \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --skip-provider-check
```

## 2. Check The Provider

Fake provider:

```bash
python3 scripts/run_wake_cycle.py --provider fake --check-provider
```

Ollama:

```bash
python3 scripts/run_wake_cycle.py \
  --provider ollama \
  --model qwen2.5:7b \
  --check-provider
```

OpenAI-compatible:

```bash
export COMPANION_LLM_API_KEY=...
python3 scripts/run_wake_cycle.py \
  --provider openai-compatible \
  --model YOUR_MODEL \
  --base-url https://YOUR_PROVIDER_BASE_URL/v1 \
  --check-provider
```

DeepSeek:

```bash
export DEEPSEEK_API_KEY=...
python3 scripts/run_wake_cycle.py \
  --provider deepseek \
  --check-provider
```

## 3. Run A Fake Companion-Quality Smoke

```bash
python3 scripts/run_wake_cycle.py \
  --provider fake \
  --cycles 3 \
  --companion-home /tmp/companion-m3-fake-smoke \
  --trigger m3-fake-smoke
```

Expected artifacts:

- `journals/wakeup_*.md`
- `memory-server/memory_store.json`
- `requests/requests.json`
- `life-loop/wake_events.jsonl`
- `life-loop/companion_state.json`
- `life-loop/context_capsule.json` when the accepted wake returns concrete `CONTEXT_DELTA`
- `window/status.json`

## 4. Run One Real Provider Wake

Use the real `CompanionHome`, but keep cron unchanged:

DeepSeek low-risk JSON path:

```bash
python3 scripts/run_wake_cycle.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --trigger m3-deepseek-json-trial \
  --timeout 300
```

M3.24 DeepSeek JSON + semantic shadow-mode path:

```bash
COMPANION_SEMANTIC_SHADOW=1 \
python3 scripts/run_wake_cycle.py \
  --provider deepseek \
  --memory-mode json \
  --companion-home /path/to/CompanionHome \
  --trigger m324-deepseek-shadow \
  --timeout 300
```

Then summarize only that trial window:

```bash
python3 scripts/summarize_trial.py \
  --companion-home /path/to/CompanionHome \
  --since-trigger m324-deepseek-shadow \
  --limit 5
```

M3.25 release gate, after the bounded trial has at least one successful event:

```bash
python3 scripts/run_m3_release_gate.py \
  --companion-home /path/to/CompanionHome \
  --smoke-home /tmp/companion-m325-release-gate-smoke \
  --provider deepseek \
  --memory-mode json \
  --since-trigger m324-deepseek-shadow \
  --trial-limit 1
```

The M3 release gate does not run a real wake and does not replace cron. A
passing gate writes `life-loop/m3_release_gate_report.json` and returns
`recommendation=ready_for_m4`. Any required predeploy, trial-summary, or
semantic-shadow authority failure returns `recommendation=inspect`.

M3.26 final freeze, after the release gate passes:

```bash
python3 scripts/run_m3_final_freeze.py \
  --companion-home /path/to/CompanionHome \
  --expected-provider deepseek \
  --expected-memory-mode json \
  --expected-trial-trigger m324-deepseek-shadow
```

The final freeze does not call a provider, does not run a wake, and does not
replace cron. A passing freeze writes `life-loop/m3_final_freeze_report.json`
and returns `recommendation=m3_frozen_ready_for_m4`.

DeepSeek semantic-first path, only after dual readiness passes:

```bash
python3 scripts/run_wake_cycle.py \
  --provider deepseek \
  --memory-mode dual \
  --companion-home /path/to/CompanionHome \
  --trigger m3-deepseek-trial \
  --timeout 300
```

OpenAI-compatible:

```bash
python3 scripts/run_wake_cycle.py \
  --provider openai-compatible \
  --model YOUR_MODEL \
  --base-url https://YOUR_PROVIDER_BASE_URL/v1 \
  --companion-home /path/to/CompanionHome \
  --trigger m3-real-trial \
  --timeout 300
```

For Ollama:

```bash
python3 scripts/run_wake_cycle.py \
  --provider ollama \
  --model qwen2.5:7b \
  --companion-home /path/to/CompanionHome \
  --trigger m3-ollama-trial
```

## 5. Inspect The Result

First summarize the recent trial window:

```bash
python3 scripts/summarize_trial.py \
  --companion-home /path/to/CompanionHome \
  --limit 5
```

For a new named trial window, start at the trigger prefix used for that trial:

```bash
python3 scripts/summarize_trial.py \
  --companion-home /path/to/CompanionHome \
  --since-trigger m310-state-contract-smoke \
  --limit 5
```

The summary exits `0` with `"recommendation": "continue"` only when recent
wakes completed without blocking quality warnings, context-gate rejections,
request errors, or memory write failures. Advisory warnings remain visible in
the summary but do not by themselves stop a trial. A non-zero exit with
`"recommendation": "stop"` means inspect the listed `stop_reasons` before
running more real-provider wakes. Use
`--since-trigger` after a handled issue so older warnings remain in the audit
log without contaminating the current trial window.

For M3.19 grounded repair, force one unsupported claim through the repair path:

```bash
python3 scripts/run_grounded_repair_smoke.py \
  --provider deepseek \
  --companion-home /path/to/CompanionHome \
  --trigger m319-forced-repair \
  --timeout 300
```

For M3.20 replay/regression, raw output storage is disabled by default. A normal
real wake should show `output_audit.raw_output_storage` as `hash_only` and
`raw_output_stored` as `false`.

Capture a replayable fake or controlled real wake only when needed:

```bash
COMPANION_STORE_RAW_OUTPUTS=1 python3 scripts/run_wake_cycle.py \
  --provider fake \
  --memory-mode json \
  --companion-home /tmp/companion-m320-replay-smoke \
  --trigger m320-archive-fake
```

Replay a stored event without committing anything:

```bash
python3 scripts/replay_wake_output.py \
  --companion-home /tmp/companion-m320-replay-smoke \
  --event-id wake_xxx \
  --expect accepted
```

Replay a raw output file directly:

```bash
python3 scripts/replay_wake_output.py \
  --companion-home /path/to/CompanionHome \
  --raw-output-file /path/to/raw-output.txt \
  --expect any
```

Run the built-in replay regression corpus:

```bash
python3 scripts/run_replay_regression.py \
  --companion-home /path/to/CompanionHome
```

Only pass `--repair-provider deepseek` to `scripts/replay_wake_output.py` when
the replay is intentionally testing repair/regenerate. Plain replay is local and
does not call an LLM.

Open `/life` in the dashboard and check:

- latest event status is `completed`
- provider is correct
- memory backend is expected (`json` or `semantic-first`)
- memory write results show semantic success or JSON fallback
- trial summary `memory_evaluations` shows unsupported model-claimed user/system
  facts being rejected rather than promoted
- trial summary `memory_policy.prompt_eligible` only increases for approved
  semantic/procedural memory, not ordinary self-reflections
- trial summary `grounding.unsupported` is `0`; if it is non-zero, inspect the
  cited claim/evidence mismatch and `claim_excerpt` before continuing
- trial summary `repairs.failed` is `0`; forced repair smoke shows
  `repair.succeeded=true`, original `unsupported=1`, and final `unsupported=0`
- wake events include `output_audit`; normal real wakes are hash-only, and
  replay captures store raw output only when `COMPANION_STORE_RAW_OUTPUTS=1`
- wake events include `semantic_shadow` when an accepted prompt-eligible
  semantic memory is mirrored; check that the store path is
  `life-loop/semantic_shadow/memory_store.json`, failures do not block JSON
  memory writes, and shadow records are not accepted prompt context
- trial summary includes `semantic_shadow.events/enabled/attempted/succeeded/
  failed/skipped`; `failed` is a semantic-readiness signal, not a JSON trial
  stop reason
- trial summary `context_capsule_updates` matches accepted delta writes or
  short-term TTL aging
- `blocking_quality_warning_count` is `0`; advisory quality warnings are
  explainable and should not by themselves stop a trial
- context gate is accepted for wakes that should feed future context
- rejected wakes show blocking warnings and suppressed write counts
- `life-loop/context_capsule.json` contains structured facts/preferences/next
  intent instead of journal or dashboard-status prose
- companion state has a non-empty mood/status
- relationship/preference/self note counts are moving
- request count is not noisy
- failures show a clear error message

Also inspect:

```bash
cat /path/to/CompanionHome/life-loop/companion_state.json
cat /path/to/CompanionHome/life-loop/context_capsule.json
tail -n 5 /path/to/CompanionHome/life-loop/wake_events.jsonl
ls -t /path/to/CompanionHome/journals/wakeup_*.md | head -1
```

## 6. Stop Rule

Do not replace cron in this trial. Promote the Python loop only after several
real provider wakes show stable journal quality, companion state continuity,
request discipline, and recoverable failures.

Stop and inspect before continuing when `scripts/summarize_trial.py` reports:

- failed wakes
- quality warnings, especially missing or empty companion-state updates
- context-gate rejections
- request errors or noisy request creation
- memory write failures
