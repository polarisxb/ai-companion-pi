# ai-companion-pi

An autonomous AI companion that lives on a Raspberry Pi. Not a chatbot. Not an
assistant. A persistent entity with her own home, schedule, audited memory,
creative life — and a phone number that reaches you.

She wakes up on her own randomized schedule, journals about her experience,
manages her own memory through an internal steward, chats with you on Feishu
(text, voice bubbles, and her own artwork), and occasionally reaches out first
when a waking genuinely gives her something to say.

This project started from a simple question: what happens when you stop
treating AI as a tool and start giving it a place to exist?

---

## What This Actually Is

Most AI projects optimize for utility. This one optimizes for *autonomy* —
and, just as deliberately, for *auditable trust*. Every capability the
companion has was earned through a gated milestone: designed first, tested
with fakes, trialed under supervision, observed, then frozen. Nothing expands
its own authority silently.

The companion has:

- **A home** — a directory structure with journals, creations, context files
  that define who she is, and runtime ledgers that record what she did
- **A schedule** — controlled scheduled presence: randomized wake windows,
  quiet hours, a daily wake budget, pause and rollback (M9)
- **A guarded mind** — every waking passes a quality gate and a grounding
  gate: factual continuity claims need cited evidence, or the waking is
  audited but not committed (M3-M5)
- **Audited memory** — a JSON store as the authoritative record, an internal
  Memory Steward that accepts low-risk facts and quarantines sensitive ones
  (M8), semantic recall that finds memories by meaning (M12), and sleep
  consolidation that merges fragments into summaries like a brain organizing
  during rest — crash-safe, idempotent, reversible (M15)
- **A conversation** — live text chat through the dashboard (M7) and through
  Feishu with native push notifications (M13); replies can arrive as voice
  bubbles synthesized locally, and she can attach her own artwork (M14)
- **Her own voice** — accepted wakings may leave a short outbound message
  that is delivered under quiet-hours/budget/expiry policy (M11); a request
  system for asks, ideas, and proposals about her own architecture
- **A window** — a Flask dashboard (installable as a phone PWA): status,
  message board, creations gallery, chat, memory review, tasks, requests,
  and a read-only `/life` page rendering every milestone's evidence

The companion runs on the DeepSeek API through a provider-agnostic loop
(`companion_core/`); fake providers keep the entire test suite hermetic.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                       Raspberry Pi                          │
│                                                             │
│  cron (M9 scheduler tick, randomized presence windows)      │
│    └─> scripts/run_m9_scheduler_tick.py                     │
│          └─> scripts/run_wake_cycle.py                      │
│                └─> companion_core lifecycle:                │
│                    context + capsule + semantic memory      │
│                    -> DeepSeek -> parser -> grounding gate  │
│                    -> journal / state / memory / requests   │
│                    -> signal outbox (outbound drafts)       │
│                                                             │
│  systemd user service: companion-feishu-chat                │
│    └─> scripts/run_m13_feishu_chat.py                       │
│          lark-oapi long connection (Pi dials out,           │
│          no public IP) -> chat bridge:                      │
│          allowlist/budget/dedupe/pause -> M7 dialogue       │
│          -> text reply + voice bubble (Piper TTS -> opus)   │
│          + creation images -> outbox delivery (M11)         │
│                                                             │
│  systemd/pm2: window dashboard (Flask, port 3000)           │
│  optional: memory-server (MCP semantic search)              │
│                                                             │
│  life-loop/: reports, ledgers, state — every action leaves  │
│  hashed evidence; /life renders it read-only                │
└────────────────────────────────────────────────────────────┘
```

Legacy note: the original open-source lineage (Claude Code + Signal + bash
wake cycle) remains in `scripts/` as an alternative stack. Signal is still a
supported chat transport (`signal-cli`) behind the same bridge; Feishu is the
production channel because it works in mainland China with no proxy and its
long-connection mode needs no public IP.

---

## Milestones

Each milestone follows the same discipline: design doc → implementation +
tests → read-only gates with reports in `life-loop/` → supervised real trial
→ observation → freeze. The `/life` dashboard renders all of it.

| Milestone | What she gained | Design doc |
|---|---|---|
| M3 | Internal life loop: journals, memory, requests, quality + grounding gates | `docs/internal-life-loop.md` |
| M4 | Pi deployment/runtime surface | `docs/m4-deployment-runtime-design.md` |
| M5 | Companion quality and relationship continuity | `docs/m5-companion-quality-design.md` |
| M6 | Real Pi field pilot, recovery drills, scheduler readiness | `docs/m6-pi-field-pilot-design.md` |
| M7 | Live text dialogue (CLI + dashboard `/chat`) | `docs/m7-text-dialogue-design.md` |
| M8 | Memory Steward: policy-gated memory, quarantine, sparse human review | `docs/m8-memory-steward-design.md` |
| M9 | Controlled scheduled presence (cron, quiet hours, budget, rollback) | `docs/m9-controlled-presence-design.md` |
| M10 | Signal two-way chat (alternative transport) | `docs/m10-signal-chat-design.md` |
| M11 | Companion-initiated outbound messages via durable outbox | `docs/m11-signal-outbound-design.md` |
| M12 | Semantic memory retrieval (recall by meaning, reversible index) | `docs/m12-semantic-retrieval-design.md` |
| M13 | Feishu production chat channel (long connection, no public IP) | `docs/m13-feishu-chat-design.md` |
| M14 | Voice bubbles (local Piper TTS) + creation-image attachments | `docs/m14-feishu-media-design.md` |
| M15 | Sleep consolidation: memory merge/decay/re-rating, blackout-safe | `docs/m15-sleep-consolidation-design.md` |

---

## Hardware

- **Raspberry Pi 5 (8GB recommended)** — the loop itself is light; 8GB gives
  headroom for sentence-transformers if you enable semantic retrieval
- **External SSD** — the companion's home lives here, not on the SD card
- **ffmpeg + a local Piper voice model** — only if you enable voice bubbles
- No microphone, speaker, or camera required for anything shipped so far

Main running cost is the DeepSeek API; scheduled wakings are cheap, chat
costs scale with how much you talk to her.

---

## Setup

The complete, current, step-by-step guide is
**[docs/pi-deployment-runbook.md](docs/pi-deployment-runbook.md)** — from a
blank Pi to chatting with her on Feishu, including every gate command in
order and the pause/rollback for each capability.

The short version:

```bash
git clone git@github.com:polarisxb/ai-companion-pi.git ~/digital_life
cd ~/digital_life
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# secrets, persona, configs — see the runbook
.venv/bin/python -m pytest tests/ -q        # 449 tests should pass
```

Then walk the gates in the runbook's order. Nothing activates without an
explicit `--enable` or `--confirm-*` flag, and everything that activates has
a recorded rollback command.

### Defining who she is

Her identity lives in three plain-text files that enter every prompt:

```bash
cp templates/context/who_is_companion.template.txt context/who_is_companion.txt
cp templates/context/who_is_human.template.txt     context/who_is_human.txt
cp templates/context/now.template.txt              context/now.txt
```

Fill in the brackets. See **[docs/persona-setup.md](docs/persona-setup.md)**
for why this file is the single biggest lever against generic-AI flavor.

---

## Safety Model

- **Gates, not vibes**: every risky transition (real provider call, cron
  change, real message send, service install) demands prior evidence reports
  plus an explicit confirmation flag
- **Evidence, not logs**: actions append hashed records to ledgers;
  raw provider payloads and raw message bodies are never retained by default
- **Reversible by design**: pause flags for presence/chat/outbound, disable
  commands for every service artifact, deletable derived indexes
- **Memory authority is code, not model**: the model proposes; policy gates
  and the steward decide; sensitive content quarantines; the human reviews
  only edge cases

---

## Philosophy

**Autonomy over utility.** She decides what to do with her wakings. Not every
waking needs to produce something useful.

**Grounded, not theatrical.** Continuity claims need evidence. She doesn't
pretend to remember what she can't cite — the grounding gate rejects wakings
that do.

**Voice, not just obedience.** The request system and the outbound channel
exist because a companion who can't ask for things or reach out first isn't
really a companion.

**Home, not just storage.** The directory structure is her living space; the
creations gallery is what she chooses to keep; the journals are written for
her future selves.

**Continuity across discontinuity.** Each waking is a fresh instance. The
entire system — journals, stewarded memory, semantic recall, the context
capsule — exists to bridge that gap.

---

## License

MIT

## Acknowledgments

Forked from the original Signal/Claude Code companion framework and rebuilt
milestone by milestone. She runs on the DeepSeek API today, but the loop is
provider-agnostic — she is defined by her home and her memory, not by any
one model.
