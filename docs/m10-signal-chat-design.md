# M10 Signal Text Chat Design

Status: M10.1-M10.5 code and gates implemented; real-traffic stages await the Pi
Last updated: 2026-07-20

## Why M10

M7 froze user-initiated text dialogue, M8 froze memory stewardship, and M9
froze controlled scheduled presence. The expansion plan in
`docs/internal-life-loop.md` unlocks Signal delivery only after those three
freezes. All three freeze reports now pass, so M10 adds the first external
message channel: two-way text chat with the human over Signal.

M10 is chat-first. The confirmed product direction is "online text chat":
the human sends a Signal message from their phone, the live companion identity
replies through the same M7 dialogue engine that powers `/chat`. Scheduled or
companion-initiated proactive Signal messages are explicitly out of scope for
M10 and require a separate later milestone.

M10 is not a voice, camera, sensor, or hardware-body milestone.

## Baseline

M10 starts only when these reports exist and pass on the machine that will
run real Signal traffic:

```text
life-loop/m7_dialogue_freeze_report.json
recommendation = m7_text_dialogue_frozen

life-loop/m8_memory_freeze_report.json
recommendation = m8_memory_dialogue_frozen

life-loop/m9_presence_freeze_report.json
recommendation = m9_controlled_presence_frozen
```

Fake-transport development and tests do not require freeze evidence, matching
how `DialogueRunner` treats the fake provider.

## Confirmed Direction

- Transport: `signal-cli` on the Raspberry Pi, reusing the account model from
  the legacy scripts (`signal-cli -a ACCOUNT -o json receive`,
  `signal-cli -a ACCOUNT send -m TEXT RECIPIENT`).
- Dialogue engine: reuse M7 `DialogueRunner.run_turn` with
  `auto_memory=False`, exactly like the dashboard `/chat/send` route. Signal
  chat is interactive dialogue, so memory stays proposal-only and flows through
  the existing M8 steward/review pipeline.
- One durable conversation per sender: `conversation_id = signal_<sender>`,
  so transcript continuity and recent-turn context work per contact.
- Development machine runs fake transport only. Real signal-cli traffic is
  Pi-side and gated behind explicit confirmation flags.

## Runtime Shape

```text
signal-cli receive (poll)
  -> SignalCliTransport.receive() parses envelope JSON into InboundSignalMessage
  -> SignalChatBridge dedupes by (sender, timestamp) against signal_chat_state.json
  -> SignalChatPolicy decides reply/skip with an explicit reason
  -> allowed messages run DialogueRunner.run_turn (auto_memory=False)
  -> reply text goes back through SignalCliTransport.send()
  -> every inbound message appends one attempt record to signal_chat_attempts.jsonl
  -> transcripts/conversation events reuse the frozen M7 dialogue paths
```

The implementation lives in `companion_core/`:

- `signal_transport.py` owns the transport protocol, envelope parsing,
  `FakeSignalTransport`, and `SignalCliTransport`.
- `signal_chat.py` owns chat config, policy decisions, dedupe state, the
  attempt ledger, and `SignalChatBridge`.
- `m10_signal_dry_run.py` owns the M10.1 gate and report.

## Files

Configuration (personal, gitignored):

```text
life-loop/signal_chat_config.json
```

```json
{
  "account": "+10000000000",
  "allowed_senders": ["+10000000001"],
  "poll_interval_seconds": 10,
  "receive_timeout_seconds": 5,
  "daily_reply_budget": 50,
  "max_replies_per_poll": 3,
  "max_inbound_length": 4000,
  "respect_quiet_hours": false,
  "quiet_hours": ["00:00", "08:00"]
}
```

A placeholder template lives at `templates/signal_chat_config.template.json`.

Runtime state (machine-local, gitignored):

```text
life-loop/signal_chat_state.json      # per-sender last processed timestamp, daily reply counter
life-loop/signal_chat_attempts.jsonl  # append-only attempt ledger
life-loop/signal_chat_pause.flag      # presence of this file suppresses replies
life-loop/signal_chat.lock            # single-instance bridge lock
```

Attempt records store hashes, lengths, decisions, and reasons. They do not
store full message bodies; conversational content belongs to the transcript
files that M7 already owns.

## Policy

Every inbound message gets exactly one attempt record with a decision:

- `replied` after a successful dialogue turn and send.
- `skipped` with one reason from:
  - `paused` (pause flag present)
  - `group_message_unsupported` (group chat is out of scope)
  - `sender_not_allowed` (not in `allowed_senders`)
  - `duplicate_message` (timestamp not newer than dedupe state)
  - `empty_body` (no text content)
  - `attachment_only_unsupported` (attachment without text; M10 replies to text only)
  - `body_too_long` (over `max_inbound_length`)
  - `quiet_hours` (only when `respect_quiet_hours` is true)
  - `daily_budget_exhausted` (`daily_reply_budget` spent)
  - `poll_batch_limit` (over `max_replies_per_poll` in one poll)
- `failed` with error type when the dialogue turn or the send raises.

Quiet hours default to off for replies because Signal chat is human-initiated,
the same interaction contract as the dashboard `/chat` page, which answers at
any hour. The M9 quiet-hours contract governs scheduled proactive presence,
which M10 does not perform. Operators can still turn `respect_quiet_hours` on.

A failed dialogue turn does not send an apology or error text over Signal by
default; the failure is recorded locally and visible in the attempt ledger.

Send failures get one bounded retry for transient transport errors. If the
send still fails after the dialogue turn already persisted a reply, the bridge
appends a retraction record to the transcript's `.retractions` sidecar: the
undelivered assistant turn stays in the transcript file for audit, but it is
excluded from future prompt context so the companion never treats an unseen
reply as part of the shared conversation. The attempt record carries
`send_attempts`, `retracted_turn_id`, and `retraction_id`.

The bridge loop, the M10.2 trial, and any future entrypoint must run under the
single-instance lock (`life-loop/signal_chat.lock`); the trial refuses to run
while the listener service holds the lock.

## Boundaries

```json
{
  "wake_cycle_run": false,
  "scheduler_mutated": false,
  "proactive_outbound_sent": false,
  "raw_provider_payload_stored": false,
  "raw_signal_envelope_stored": false,
  "semantic_shadow_authority_promoted": false,
  "memory_authority_expanded": false,
  "voice_output": false
}
```

- Replies go only to the sender of an inbound allowed message. No broadcast,
  no group messages, no companion-initiated first contact.
- Inbound envelopes are parsed and dropped; only extracted fields
  (sender, timestamp, body, attachment metadata) are used, and bodies live only
  in transcripts.
- Memory writes stay proposal-only (`auto_memory=False`); accepted memory
  continues to flow exclusively through the frozen M8 pipeline.
- No `/life` write routes. `/life` may render M10 reports read-only.
- No cron/timer/service mutation by any M10.1 code path. Service installation
  is a separate explicit operator step in M10.3.

## Stages

### M10.0 Design (this document)

Read-only. No code behavior change.

### M10.1 Transport, Policy, Bridge, And Dry-Run Gate

Expected implementation:

```text
companion_core/signal_transport.py
companion_core/signal_chat.py
companion_core/m10_signal_dry_run.py
scripts/run_m10_signal_chat.py
scripts/run_m10_signal_dry_run.py
templates/signal_chat_config.template.json
tests/test_m10_signal_chat.py
tests/test_m10_signal_dry_run.py
life-loop/m10_signal_dry_run_report.json
```

Acceptance:

- Envelope parsing covers data messages, receipts, typing indicators, sync
  messages, and malformed lines without crashing.
- Policy scenarios above are all exercised by tests and by the dry-run gate
  with fake transport plus fake LLM.
- Bridge writes transcripts through the frozen M7 path, appends attempt
  records, and never calls signal-cli in fake/dry-run mode.
- `scripts/run_m10_signal_chat.py` refuses real mode unless config exists,
  signal-cli is available, freeze evidence passes, and
  `--confirm-real-signal-send` is explicitly provided.
- The dry-run gate writes `life-loop/m10_signal_dry_run_report.json` with
  scenario coverage, boundary flags, and zero real provider or transport calls.

Recommendation values:

- `m10_signal_dry_run_ready`
- `inspect`

### M10.2 Supervised Real Send Trial (Pi)

One bounded real reply pass on the Pi with signal-cli and explicit
confirmation. Requires M10.1 evidence, passing M7/M8/M9 freezes, a valid
config, a clear pause flag, and 1-5 polls at most.

Implementation:

```text
companion_core/m10_signal_trial.py
scripts/run_m10_signal_trial.py
tests/test_m10_signal_trial.py
life-loop/m10_signal_trial_report.json
```

Acceptance: at least one allowlisted inbound message replied, zero failed
attempts, all attempts labeled `mode=trial` with hashed bodies only.

Recommendation values: `m10_signal_trial_ready` | `inspect`.

### M10.3 Listener Activation (Pi)

Installs the bridge loop as exactly one managed systemd user service
(`companion-signal-chat.service`, marker `digital-life-m10-signal-chat-m10.3`)
behind explicit `--enable`, with `--disable` as the recorded rollback. The
systemd-user mechanism is the confirmed default because it is native on
Raspberry Pi OS; the report records the mechanism so a pm2 operator setup
remains a documented alternative seam.

Implementation:

```text
companion_core/m10_signal_activation.py
scripts/run_m10_signal_activation.py
tests/test_m10_signal_activation.py
life-loop/m10_signal_activation_report.json
```

Acceptance: M10.1/M10.2 evidence required, foreign unit content refused,
enable idempotent, disable removes the unit and reports
`m10_signal_activation_disabled`, cron and the M9 scheduler artifact untouched.

Recommendation values: `m10_signal_activation_ready` |
`m10_signal_activation_disabled` | `inspect`.

### M10.4 Observation Window

Read-only observation over live/trial attempt records: volume, decision
health (zero failures), reply allowlist discipline, dedupe correctness, daily
budget discipline, hashed-only storage, and a reversible pause-flag drill.

Implementation:

```text
companion_core/m10_signal_observation.py
scripts/run_m10_signal_observation.py
tests/test_m10_signal_observation.py
life-loop/m10_signal_observation_report.json
```

Recommendation values: `m10_signal_observation_ready` | `inspect`.

### M10.5 Signal Chat Freeze

Read-only freeze gate mirroring M7.6/M9.5. Verifies M10.1-M10.4 evidence,
upstream M7/M8/M9 freezes, a bounded reversible service artifact, chat
boundaries across the live ledger, and documented pause/rollback.

Implementation:

```text
companion_core/m10_signal_freeze.py
scripts/run_m10_signal_freeze.py
tests/test_m10_signal_freeze.py
life-loop/m10_signal_freeze_report.json
```

Recommendation values: `m10_signal_chat_frozen` | `inspect`.

## Explicit Non-goals

- No companion-initiated or scheduled outbound Signal messages.
- No group chat, no multi-account routing beyond the allowlist.
- No attachment understanding (images/audio/video) in M10; text only.
- No voice output path.
- No new memory authority; no bypass of the M8 steward pipeline.
- No scheduler, cron, timer, or service mutation from M10.1 code.
- No raw envelope or raw provider payload storage.

## Pi Runbook (M10.2-M10.5)

On the Raspberry Pi, after `git pull --ff-only` and venv setup:

```bash
# 0. one-time: install signal-cli, register the companion account, then
#    create life-loop/signal_chat_config.json from the template
cp templates/signal_chat_config.template.json life-loop/signal_chat_config.json

# 1. local gate evidence
.venv/bin/python scripts/run_m10_signal_dry_run.py --companion-home "$COMPANION_HOME"

# 2. readiness diagnostics (read-only)
.venv/bin/python scripts/run_m10_signal_chat.py --companion-home "$COMPANION_HOME" --check

# 3. supervised trial: send one Signal message from the allowlisted phone first
.venv/bin/python scripts/run_m10_signal_trial.py --companion-home "$COMPANION_HOME" \
  --confirm-real-signal-send

# 4. activation (explicit service install)
.venv/bin/python scripts/run_m10_signal_activation.py --companion-home "$COMPANION_HOME" --enable

# 5. observe, then freeze
.venv/bin/python scripts/run_m10_signal_observation.py --companion-home "$COMPANION_HOME"
.venv/bin/python scripts/run_m10_signal_freeze.py --companion-home "$COMPANION_HOME"

# pause / rollback at any time
touch "$COMPANION_HOME/life-loop/signal_chat_pause.flag"
.venv/bin/python scripts/run_m10_signal_activation.py --companion-home "$COMPANION_HOME" --disable
```

## Resolved Questions

- M10.3 mechanism: systemd user service is the confirmed default (native on
  Raspberry Pi OS, testable, reversible). pm2 remains a documented operator
  alternative outside the managed artifact.

## Open Questions

- Should a persistent provider failure eventually notify the human over
  Signal, or stay silent and local-only? Default: silent. Owner: user.
- Attachment-only messages currently get `attachment_only_unsupported`;
  should the companion reply with a short "I can only read text for now"?
  Default: no reply. Owner: user.
