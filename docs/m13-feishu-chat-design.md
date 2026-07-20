# M13 Feishu Chat Channel Design

Status: M13.1-M13.5 code and gates implemented; real-traffic stages await the Pi
Last updated: 2026-07-20

## Why M13

M10/M11 built the two-way external message channel on Signal. Signal is
blocked in mainland China: registration needs a foreign number and both the
Pi and the phone would need a stable proxy. The human confirmed Feishu (飞书)
as the production chat channel: official bot API, free self-built apps,
direct connectivity in China, native mobile push, and a long-connection mode
that needs no public IP on the Pi.

M10/M11 were deliberately transport-pluggable. M13 adds a Feishu transport
and reuses the entire chat stack unchanged: policy, budgets, dedupe, pause
flags, attempt ledger, outbox delivery, and the M7 dialogue engine. Signal
code stays in the repo as an alternative transport; nothing is removed.

M13 is text-only. Images, voice bubbles, and cards are later milestones.

## Confirmed Direction

- Transport: Feishu self-built app (企业自建应用).
  - Outbound: REST `im/v1/messages` with `receive_id_type=open_id`
    (urllib, no new HTTP dependency), tenant token cached and refreshed.
  - Inbound: official `lark-oapi` SDK long-connection (WebSocket) mode —
    the Pi dials out; no public IP, domain, or tunnel. `lark-oapi` is the
    single new dependency and is imported lazily, so machines without it
    (and all tests) work with injected fakes.
- The push-based long connection adapts to the poll-based bridge through a
  thread-safe queue: the listener thread enqueues parsed messages;
  `transport.receive()` drains the queue each poll.
- Identity model: `account` = app_id; senders/recipients are Feishu
  `open_id`s. The same allowlist/budget/quiet-hours config schema as Signal,
  in its own file `life-loop/feishu_chat_config.json`.
- Secrets: `FEISHU_APP_ID` / `FEISHU_APP_SECRET` live in
  `.secrets/feishu.env` (0600), loaded by the existing secrets loader;
  never in configs, reports, or the ledger.
- Shared runtime substrate with Signal, split where it must be:
  - Shared: chat state file (dedupe/budgets/outbox terminal states — this is
    what makes cross-channel outbox double-delivery impossible), attempt
    ledger (records carry `channel`), signal outbox, M7 transcripts.
  - Per-channel: config file, loop lock (`feishu_chat.lock`) so one Signal
    service and one Feishu service could coexist, systemd unit.
- Records gain an explicit `channel` field (`signal` | `feishu`); records
  without one are treated as `signal` for backward compatibility. M10/M11
  gates scope to the signal channel; M13 gates scope to feishu.
- Conversation ids are channel-prefixed (`feishu_<open_id>`), so transcript
  continuity is per-person per-channel.
- Outbound (M11) delivery through Feishu: the same outbox, enabled by
  `outbound_enabled` in the feishu config. The M11 design rule stands:
  enable outbound on exactly one channel.

## Runtime Shape

```text
lark-oapi ws client (daemon thread, dials out)
  -> P2ImMessageReceiveV1 event
  -> parse_feishu_message_event() -> InboundSignalMessage(sender=open_id,
       timestamp=create_time_ms, body=text, has_attachment, is_group)
  -> thread-safe queue
bridge run_loop (same code as M10, feishu lock file)
  -> transport.receive() drains queue
  -> policy/budget/dedupe/pause (unchanged)
  -> DialogueRunner.run_turn(auto_memory=False) (unchanged)
  -> transport.send(open_id, reply) via REST + tenant token
  -> attempt ledger records with channel=feishu
  -> deliver_outbox_once() when outbound_enabled (M11, unchanged)
```

Non-text inbound messages (image/audio/...) parse to `body="",
has_attachment=True` and hit the existing `attachment_only_unsupported`
skip reason — no new policy needed until the media milestones.

## Files

- `companion_core/feishu_transport.py`: event parsing, REST API client with
  token cache, `FeishuTransport` (queue + listener), `FakeFeishuTransport`.
- `life-loop/feishu_chat_config.json` (gitignored; template in
  `templates/feishu_chat_config.template.json`): same schema as signal chat
  config (account = app_id, allowed_senders = open_ids, budgets, quiet
  hours, outbound fields).
- `.secrets/feishu.env`: `FEISHU_APP_ID=...`, `FEISHU_APP_SECRET=...`.
- `life-loop/feishu_chat.lock`: per-channel single-instance loop lock.

## Boundaries

```json
{
  "reuses_frozen_chat_policy": true,
  "secrets_in_reports_or_ledger": false,
  "raw_event_payload_stored": false,
  "group_or_unknown_senders_replied": false,
  "memory_authority_expanded": false,
  "scheduler_mutated": false,
  "wake_cycle_run": false,
  "voice_or_media_sent": false
}
```

- Replies only to allowlisted `open_id`s from p2p chats; group messages hit
  the existing `group_message_unsupported` skip.
- Raw Feishu event payloads are parsed and dropped; the ledger keeps hashes
  only, exactly like M10.
- The wake path is untouched; outbox capture stays M11's.

## Stages

### M13.1 Transport + dry-run gate

```text
companion_core/feishu_transport.py
companion_core/m13_feishu_dry_run.py
scripts/run_m13_feishu_chat.py
scripts/run_m13_feishu_dry_run.py
templates/feishu_chat_config.template.json
tests/test_m13_feishu_transport.py
tests/test_m13_feishu_dry_run.py
life-loop/m13_feishu_dry_run_report.json
```

Acceptance: event fixtures (text p2p, group, image-only, empty, malformed,
non-message events) parse safely; bridge end-to-end with fake transport +
fake dialogue covers replied/skips/failure with `channel=feishu` records;
token/send layer exercised against a stubbed HTTP layer including 401
token-refresh retry; secrets never appear in configs, reports, or the
ledger; runner refuses real mode without config, secrets, SDK, freeze
evidence, and `--confirm-real-feishu-send`.
Recommendation: `m13_feishu_dry_run_ready` | `inspect`.

### M13.2 Supervised real trial (Pi)

One bounded real reply pass with the real app credentials, mirroring M10.2:
requires M13.1 evidence, M7/M8/M9 freeze evidence, explicit confirm flag,
pause flags clear, and the bridge loop lock. Writes
`life-loop/m13_feishu_trial_report.json`
(`m13_feishu_trial_ready` | `inspect`).

### M13.3 Listener activation (Pi)

Exactly one managed systemd user unit `companion-feishu-chat.service`
(marker `digital-life-m13-feishu-chat-m13.3`), `--enable`/`--disable`,
mirroring M10.3. Writes `life-loop/m13_feishu_activation_report.json`
(`m13_feishu_activation_ready` | `m13_feishu_activation_disabled` |
`inspect`).

### M13.4 Observation window

Read-only ledger analysis scoped to `channel=feishu` live/trial records:
volume, decision health, allowlist discipline, dedupe, budget, hashed-only
storage, pause drill. Writes `life-loop/m13_feishu_observation_report.json`
(`m13_feishu_observation_ready` | `inspect`).

### M13.5 Freeze

Read-only freeze mirroring M10.5, requiring M13.1-M13.4 plus intact
M7/M8/M9 freezes. Writes `life-loop/m13_feishu_freeze_report.json`
(`m13_feishu_chat_frozen` | `inspect`).

## Explicit Non-goals

- No images, voice bubbles, cards, files, or media in M13 (text only).
- No group chat participation.
- No webhook mode (long connection only; the Pi never exposes a port).
- No change to policy, budgets, memory, scheduler, or wake contracts.
- No removal of the Signal transport.

## Open Questions

- Should Feishu interactive cards eventually replace `/requests` approvals
  in chat? Later milestone. Owner: user.
- Media milestones ordering after M13 freeze: images first or voice bubbles
  first? Owner: user.
