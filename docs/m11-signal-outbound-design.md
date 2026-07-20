# M11 Signal Outbound Messaging Design

Status: M11.1-M11.6 code and gates implemented; real-traffic stages await the Pi
Last updated: 2026-07-20

## Why M11

M10 gave the human a way in: inbound Signal messages reach the frozen M7
dialogue identity and get replies. The companion still has no way to reach
out. Every wake cycle already produces a `===SIGNAL===` section, but the
lifecycle drops it and the wake prompt hard-codes `NOSEND`. M9 controlled
presence froze scheduled wakes as `internal_only` output.

M11 completes the loop the project was designed around: the companion, waking
on its own controlled schedule, may send the human a short Signal message.
This is the "messages you via Signal" behavior from the original project
vision, rebuilt inside the milestone gate discipline.

M11 is not a voice, camera, or hardware milestone, and it does not change the
M9 scheduler, the M8 memory authority, or the M7 dialogue engine.

## Baseline

- M10.1 dry-run evidence passing on the machine running gates.
- For real outbound traffic: M10.2-M10.3 evidence (trial + activated listener
  service) plus M7/M8/M9 freeze evidence, because the same bridge service
  delivers outbound messages.

## Confirmed Direction

- Source of outbound messages: the `===SIGNAL===` section of accepted wake
  cycles only. Rejected wakes suppress signal capture exactly like they
  suppress state, memory, request, and status writes.
- Capture and delivery are separated by a durable outbox:
  wake lifecycle appends to `life-loop/signal_outbox.jsonl`; the M10 bridge
  service delivers pending entries under policy. The wake cycle itself never
  touches the network beyond the provider call.
- Delivery is off by default: `outbound_enabled=false` in
  `life-loop/signal_chat_config.json`. When disabled, the bridge performs no
  outbox processing at all, so M10 behavior stays byte-identical.
- One recipient: `outbound_recipient`, defaulting to the first
  `allowed_senders` entry. No broadcast, no groups, no first contact with
  unknown numbers.
- Stale presence messages must not arrive late: entries expire after
  `outbound_max_age_minutes` (default 360) if still undelivered.

## Runtime Shape

```text
wake cycle (unchanged execution path)
  -> parse_wake_output extracts ===SIGNAL=== (NOSEND supported)
  -> accepted wakes append one redacted outbox entry (signal_outbox.jsonl)
  -> wake event records hash-only signal_outbox metadata
bridge run_loop (M10 service, same single-instance lock)
  -> deliver_outbox_once() runs only when outbound_enabled
  -> policy: pause flags, quiet hours, daily budget, length, age, dedupe
  -> retryable skips defer silently; terminal outcomes append ledger records
  -> delivery uses the same transport with one bounded send retry
  -> outbox delivery state lives in signal_chat_state.json
```

## Files

- `life-loop/signal_outbox.jsonl` (gitignored): append-only capture ledger.
  Entries store the companion-authored message text (like journals do),
  secret-redacted, plus `id`, `created_at`, `source_event_id`, `trigger`,
  `content_hash`, `content_length`.
- `life-loop/signal_outbound_pause.flag` (gitignored): pauses outbound
  delivery only. The M10 chat pause flag (`signal_chat_pause.flag`) is the
  master switch and also suppresses outbound.
- `signal_chat_state.json` gains an `outbox` section:
  `{entry_id: {status, attempts, last_error, updated_at}}` for terminal
  states and retry counts.
- Attempt ledger (`signal_chat_attempts.jsonl`) gains outbound records with
  `direction: "outbound"` and decisions `delivered | skipped | failed`.
  Inbound records now carry `direction: "inbound"`; records without a
  `direction` field are treated as inbound for backward compatibility.

## Config additions (`signal_chat_config.json`)

```json
{
  "outbound_enabled": false,
  "outbound_recipient": "+10000000001",
  "daily_outbound_budget": 2,
  "outbound_quiet_hours": ["00:00", "08:00"],
  "outbound_max_length": 900,
  "outbound_max_age_minutes": 360,
  "outbound_max_send_attempts": 3
}
```

Defaults align with the M9 cadence contract (quiet hours `00:00-08:00`,
small daily budget). Outbound rides on wakes that M9 already limits, so the
budget is defense in depth, not the primary limiter.

## Policy

Delivery evaluates each pending entry and produces exactly one of:

- `delivered` after a successful send (terminal, ledger record).
- Silent defer (no ledger record, entry stays pending) for retryable
  conditions: `paused`, `chat_paused`, `quiet_hours`,
  `daily_budget_exhausted`.
- `skipped` terminal ledger record for `expired`, `content_too_long`,
  `recipient_missing`, `duplicate_delivery`.
- `failed` ledger record when the send fails after one bounded retry; the
  entry stays pending with an attempt count and becomes terminal
  (`abandoned` skip record) after `outbound_max_send_attempts`.

Silent defer keeps the ledger meaningful: a quiet-hours night must not write
hundreds of skip rows at 10-second poll intervals.

Grounding note: signal text is not claim-checked line by line; it rides the
same accepted-wake gate that authorizes state, memory, and request writes
(quality + grounding over the whole wake output). Capture applies the same
secret redaction used for dialogue text plus a length cap at delivery time.
Per-sentence grounding of outbound text is explicitly future work, not an
M11 goal.

## Boundaries

```json
{
  "wake_cycle_run": false,
  "scheduler_mutated": false,
  "outbound_gated_by_config": true,
  "recipient_allowlisted": true,
  "first_contact_allowed": false,
  "raw_provider_payload_stored": false,
  "raw_signal_envelope_stored": false,
  "memory_authority_expanded": false,
  "voice_output": false
}
```

- The wake path gains exactly one new write (outbox append on accepted
  wakes) and zero new reads; no network, no scheduler, no memory change.
- The bridge delivers only to the configured recipient, only content that a
  gated wake produced, never model-free text.
- M10 inbound gates ignore outbound records (`direction` filter), and M11
  gates own the outbound contract.

## Stages

### M11.0 Design (this document)

### M11.1 Outbox capture

```text
companion_core/signal_outbox.py
companion_core/lifecycle.py (accepted-wake capture + event metadata)
tests/test_m11_signal_outbox.py
```

Acceptance: accepted wakes with a non-NOSEND signal section append exactly
one redacted outbox entry and hash-only event metadata; rejected wakes and
NOSEND wakes append nothing; the wake prompt asks for a short optional
Simplified Chinese message instead of hard-coded NOSEND.

### M11.2 Outbound delivery in the bridge

```text
companion_core/signal_chat.py (config fields, deliver_outbox_once, run_loop wiring)
tests/test_m11_signal_outbound_delivery.py
```

Acceptance: disabled config is a byte-identical no-op; enabled delivery
honors both pause flags, quiet hours, budget, age, length, dedupe, retry,
and abandonment; terminal outcomes land in the attempt ledger with
`direction=outbound`; state tracks per-entry status.

### M11.3 Outbound dry-run gate

```text
companion_core/m11_outbound_dry_run.py
scripts/run_m11_outbound_dry_run.py
life-loop/m11_signal_outbound_dry_run_report.json
```

Recommendation: `m11_signal_outbound_dry_run_ready` | `inspect`.

### M11.4 Supervised outbound trial (Pi)

```text
companion_core/m11_outbound_trial.py
scripts/run_m11_outbound_trial.py
life-loop/m11_signal_outbound_trial_report.json
```

One supervised real delivery with explicit `--confirm-real-signal-send`,
requiring M11.3 + M10 activation + upstream freezes.

Recommendation: `m11_signal_outbound_trial_ready` | `inspect`.

### M11.5 Outbound observation

```text
companion_core/m11_outbound_observation.py
scripts/run_m11_outbound_observation.py
life-loop/m11_signal_outbound_observation_report.json
```

Read-only ledger analysis: recipient discipline, budget, quiet-hours
compliance, dedupe, hashed evidence, pause drill for the outbound flag.

Recommendation: `m11_signal_outbound_observation_ready` | `inspect`.

### M11.6 Outbound freeze

```text
companion_core/m11_outbound_freeze.py
scripts/run_m11_outbound_freeze.py
life-loop/m11_signal_outbound_freeze_report.json
```

Recommendation: `m11_signal_outbound_frozen` | `inspect`.

## Explicit Non-goals

- No delivery of request notifications, journal excerpts, or dashboard
  events; only the wake `===SIGNAL===` section.
- No companion-initiated conversations with unknown numbers.
- No streaming, media, or attachments outbound.
- No change to M9 scheduler cadence, quiet hours, or budget contracts.
- No per-sentence grounding of outbound text in M11 (documented boundary).
- No voice, camera, sensors, or hardware body work.

## Open Questions

- Should delivered outbound messages be mirrored into the M7 conversation
  transcript so the next inbound chat sees them as context? Default: no for
  M11 (transcripts stay strictly dialogue); revisit after observation.
  Owner: user.
- Should the daily outbound budget count calendar days or rolling 24h?
  Default: calendar day, matching M9. Owner: implementation.
