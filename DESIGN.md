# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-07-20
- Primary product surfaces: Companion Window home, message board, creations, tasks, requests, `/life`, M7 text chat, M8 memory review/stewardship, M9 controlled scheduler presence (frozen), M10/M11 Signal chat + outbound (alternative transport), M12 semantic memory retrieval, M13 Feishu chat (production channel), M14 Feishu media (voice bubbles + creation images), and M15 sleep consolidation (crash-safe memory review).
- Evidence reviewed:
  - `docs/web-dashboard.md`
  - `docs/requests-system-design.md`
  - `docs/internal-life-loop.md`
  - `docs/m5-companion-quality-design.md`
  - `docs/m6-pi-field-pilot-design.md`
  - `docs/m7-text-dialogue-design.md`
  - `docs/m8-memory-steward-design.md`
  - `docs/m9-controlled-presence-design.md`
  - `docs/m10-signal-chat-design.md`
  - `docs/m11-signal-outbound-design.md`
  - `docs/m12-semantic-retrieval-design.md`
  - `docs/m13-feishu-chat-design.md`
  - `docs/m14-feishu-media-design.md`
  - `docs/m15-sleep-consolidation-design.md`
  - `window/window.py`

## Brand

- Personality: intimate, quiet, continuity-focused, technically honest.
- Trust signals: visible state, visible evidence paths, explicit boundaries, reversible actions, readable failure states.
- Avoid: marketing pages, decorative hero sections, vague companion mystique, hidden writes, automatic authority expansion.

## Product goals

- Goals:
  - Make the companion feel reachable, inspectable, and continuous.
  - Let the human talk to the same companion identity through a text-first channel.
  - Let the companion manage ordinary memory herself through an internal steward while keeping risky memory auditable and reviewable.
  - Let the companion eventually appear on a controlled schedule after memory/dialogue hardening is frozen.
  - Preserve M3-M6 safety contracts while adding a deliberately scoped dialogue surface.
- Non-goals:
  - Voice, camera, sensors, Signal, and hardware body work before controlled scheduler presence is observable and reversible.
  - Scheduler installation as a prerequisite for dialogue or memory stewardship.
  - Human micromanagement of every memory candidate.
  - Automatic promotion of sensitive, inferred, conflicting, or relationship-defining chat text into long-term factual memory.
- Success signals:
  - The human can send text and receive a response from the live companion identity.
  - Conversation history is durable and reviewable.
  - Low-risk memory can be stewarded automatically with evidence and policy gates.
  - Sensitive or ambiguous memory is quarantined or routed to sparse human review.
  - Accepted memories improve future chat naturally without the companion announcing backend memory work.
  - Existing wake, `/life`, request, and recovery flows still pass tests.

## Confirmed M7 direction

- First usable path: CLI dialogue engine first, with frontend integration after
  the core engine and API are stable.
- Frontend: still in scope, but the human may provide the preferred UI design
  before implementation.
- Conversation style: natural companion dialogue first, not report-like output.
- Metadata: record transcript, current state metadata, and memory candidates
  behind the scenes.
- Memory: explicit low-risk user-stated facts/preferences may be remembered
  automatically; inferred, sensitive, relationship-defining, or ambiguous
  content remains proposal-only.
- State: every completed turn writes transcript; current companion state updates
  only when the companion explicitly emits mood/status.

## Confirmed M8 direction

- M8 is frozen as memory hardening and dialogue humanity, not scheduler, voice, Signal, or hardware.
- The human should not become the routine memory administrator.
- A Memory Steward internal personality should review completed dialogue turns and propose memory decisions.
- Code-level policy gates retain final authority over accepted prompt-eligible memory.
- Human review is reserved for sensitive, ambiguous, conflicting, or relationship-defining cases.
- Retrieval should make accepted memory improve ordinary chat while keeping unaccepted proposals, quarantined items, and audit-only reflections out of prompt context.

## Confirmed M9 direction

- M9 is controlled scheduled presence after M8.7 memory/dialogue freeze.
- M9.0 and M9.1 must not mutate cron, timers, services, or scheduler state.
- M9 should reuse the existing wake execution path behind a Scheduler Presence Controller rather than creating a second provider path.
- Scheduled presence uses non-fixed randomized presence windows, with default quiet hours `00:00-08:00`, `daily_live_wake_budget=2`, and internal-only output.
- Live activation requires read-only revalidation, supervised dry-run evidence, pause/rollback design, and an observation window.
- Voice, Signal, camera, sensors, and hardware body work remain out of scope until scheduled presence is frozen.

## Confirmed M10 direction

- M9.5 controlled presence is frozen, which unlocks Signal as the first external channel.
- M10 is chat-first: the human texts the companion over Signal and the same M7 dialogue identity replies. It is online text chat, not a broadcast or notification channel.
- Proactive or scheduled companion-initiated Signal messages are out of scope for M10 and need a separate milestone.
- The development machine uses fake transport only; real signal-cli traffic runs on the Raspberry Pi behind explicit confirmation flags and freeze evidence.
- Replies are allowlist-only, budgeted, deduped, pause-able, and recorded in an append-only attempt ledger without raw envelope storage.
- Signal chat memory stays proposal-only through the frozen M8 steward pipeline; no new memory authority.
- Voice and hardware remain out of scope; the human has no voice hardware yet.

## Confirmed M11 direction

- M11 is the companion's outbound voice on the existing Signal channel: accepted wake cycles may produce a short `===SIGNAL===` message that reaches the human.
- Capture and delivery are separated: the wake writes a durable redacted outbox entry and never touches the network; the M10 bridge service delivers under policy.
- Delivery ships disabled (`outbound_enabled=false`) and only ever targets one configured allowlisted recipient; no first contact, groups, or broadcast.
- Outbound respects its own quiet hours and small daily budget (aligned with the M9 cadence), an outbound-only pause flag plus the master chat pause flag, entry expiry so stale presence never arrives late, and per-wake dedupe.
- Ledger evidence is hash-only with `direction=outbound`; the outbox keeps the message text like journals do.
- Delivered outbound messages are not mirrored into chat transcripts in M11 (open question for later).
- Voice, camera, sensors, and hardware body work remain out of scope.

## Confirmed M12 direction

- M12 upgrades memory recall from lexical matching to meaning-based ranking while changing nothing about what may be remembered.
- The JSON memory store remains the authoritative record; the semantic index is derived, rebuildable, and deletable as a complete rollback.
- M8 policy filters run before ranking; a quarantined or proposal memory retrieves nothing at any similarity.
- Enablement is config-gated and ships off; disabled config, missing index, or an unavailable embedding backend all degrade deterministically to today's lexical retrieval.
- Two backends: a dependency-free deterministic hashing backend (tests, degraded mode) and sentence-transformers on the Pi with a multilingual model suited to Simplified Chinese memories.
- Retrieval is read-only; only the explicit idempotent backfill command writes the index.
- The M3.23 semantic shadow store stays isolated telemetry and is not promoted.

## Confirmed M13 direction

- Signal is blocked in mainland China; Feishu self-built-app bots are the confirmed production chat channel. Signal stays in the repo as an alternative transport.
- Inbound uses the official lark-oapi long-connection mode: the Pi dials out over WebSocket, no public IP, domain, tunnel, or exposed port.
- The entire M10/M11 chat stack (policy, budgets, dedupe, pause, ledger, outbox, M7 dialogue identity) is reused unchanged behind the pluggable transport.
- Ledger records carry an explicit channel field; per-channel configs and loop locks, shared state/ledger/outbox so cross-channel dedupe and outbox delivery stay safe.
- Credentials live only in `.secrets/feishu.env`; never in configs, reports, or the ledger.
- M13 is text-only. Feishu supports images, opus voice bubbles, and interactive cards; those are separate later milestones (voice bubbles need no voice hardware — TTS on the Pi suffices).
- Only one channel should have outbound (M11) enabled at a time.

## Confirmed M14 direction

- M14 adds voice bubbles and creation-image attachments to Feishu chat replies, both hardware-free.
- Media is an enhancement, never a dependency: text is delivered first and every media failure downgrades silently to it, recorded in the ledger.
- Voice is synthesized locally (command-driven engine, default Piper reusing the legacy voice investment) and converted to opus on the Pi; no cloud TTS.
- Voice modes: off (default) / always / companion_choice — in the last mode she decides per reply, and the prompt hint exists only when the mode is active.
- Image attachments come exclusively from her `creations/` directory with strict path, extension, size, and count validation; traversal is impossible.
- Ledger media payloads carry outcomes only — never audio or image bytes; synthesized audio lives in temp dirs and is deleted after send.
- Inbound media understanding (your photos, your voice) and outbox media are explicit non-goals for M14.

## Confirmed M15 direction

- M15 gives the companion sleep consolidation: she periodically reviews her own memories and proposes merges (derived summaries), archival of trivia, and significance re-ratings; code-level policy gates hold final authority.
- Blackout safety is a first-class requirement, not an edge case: planning writes no memory state, application is one atomic store replace, archives are reversible (never deletes), and plans are idempotent so any crash window resolves safely on retry.
- Scheduling is anacron-style debt (interval elapsed + enough new memories, checked from persisted state) so a Pi that is off for weeks pays the debt on the next boot — consolidation can be late, never lost.
- Summaries carry `authority=derived_summary` with evidence refs to every member and are prompt-eligible only when all members were; quarantined content can never be smuggled into prompts through consolidation.
- Whole-plan rollback restores archived members and retires the summaries in one atomic step.
- Non-goals: deleting memories, consolidating quarantined items, adding new facts in summaries, and consolidation inside wake cycles or chat turns.

## Personas and jobs

- Primary personas:
  - The human operator who wants to speak with the companion directly.
  - The companion process, which needs a reliable text ingress distinct from wake cycles.
- User jobs:
  - Start a conversation quickly from the Pi or LAN.
  - Understand whether the companion is responding, failed, or waiting.
  - Review what was said and what, if anything, was proposed for memory.
- Key contexts of use:
  - Desktop browser on the Pi/LAN.
  - Phone PWA on the LAN.
  - Terminal CLI during early M7 hardening.

## Information architecture

- Primary navigation: keep the existing simple top navigation and add `chat` only when the route is implemented.
- Core routes/screens:
  - `/`: public companion home.
  - `/board`: asynchronous human-to-companion notes for later wake cycles.
  - `/requests`: companion-to-human structured requests.
  - `/life`: read-only runtime evidence.
  - `/chat`: M7 live text dialogue.
  - `/memory-review`: M8 exception queue for ambiguous or sensitive memory decisions.
  - M9 uses `/life` for read-only scheduler presence evidence before any new dashboard controls are considered.
- Content hierarchy:
  - Chat foregrounds the conversation.
  - State, provider, memory mode, transcript id, and proposals are secondary metadata.
  - Memory review foregrounds only decisions that require human judgment; ordinary low-risk stewarded memory should not demand routine clicks.
  - Diagnostics are visible on failure but not dominant during normal conversation.

## Design principles

- Dialogue first: the chat page exists for direct exchange, not dashboard administration.
- Evidence without clutter: show enough status to trust the system without turning chat into a report page.
- Explicit write boundaries: distinguish transcript writes, memory proposals, memory commits, requests, and wake events.
- Natural reply, structured shadow: keep the visible reply conversational while
  machine-readable metadata stays behind the scenes.
- Stewarded memory, sparse review: ordinary memory management belongs to the companion's internal steward; human review is an exception path.
- Controlled presence before new channels: prove non-fixed cadence, quiet hours, daily budget, pause, rollback, and observation before adding voice or Signal.
- Reuse before redesign: extend existing Window styles and routes before introducing a new UI system.
- Tradeoffs: early M7 should prefer CLI and plain HTML reliability over polished real-time effects.
  M8 should prefer auditable memory correctness over invisible personalization magic.

## Visual language

- Color: use existing `window/window.py` CSS variables and companion palette from `window/status.json`.
- Typography: keep the existing dashboard type scale; chat message text should be readable and not oversized.
- Spacing/layout rhythm: dense but calm, with clear message grouping and stable composer placement.
- Shape/radius/elevation: reuse existing card and panel treatment; avoid nested cards.
- Motion: minimal. Loading states can pulse subtly, but no decorative animation is required.
- Imagery/iconography: no new imagery required for M7 chat. Use text labels until an icon set is introduced.

## Components

- Existing components to reuse:
  - Navigation links.
  - Status/mood display.
  - Message form textarea styling.
  - Request/status pills and empty-state treatment.
- New/changed components:
  - Chat transcript list.
  - Human and companion message bubbles or rows.
  - Composer with send button and disabled/loading states.
  - Conversation status strip.
  - Memory proposal panel.
  - Memory review queue rows.
  - Memory decision detail view with source turn, candidate, risk, reason, and action controls.
  - Scheduler presence status rows in `/life` once M9 reports exist.
- Variants and states:
  - Empty conversation.
  - Sending.
  - Provider timeout/error.
  - Retry available.
  - Memory proposal present.
  - Memory decision accepted, quarantined, rejected, audit-only, and human-review-required.
  - Transcript saved.
- Token/component ownership: keep tokens in `window/window.py` until the dashboard is split into static assets.

## Accessibility

- Target standard: practical WCAG 2.1 AA for the dashboard.
- Keyboard/focus behavior: chat composer must be keyboard usable; send button and retry controls need visible focus.
- Contrast/readability: preserve sufficient contrast under custom companion palettes.
- Screen-reader semantics: message list should have ordered message structure, author labels, and status text.
- Reduced motion and sensory considerations: do not rely on animation to indicate response state.

## Responsive behavior

- Supported breakpoints/devices: desktop browser, tablet, and phone PWA.
- Layout adaptations:
  - Desktop may use a side metadata panel.
  - Mobile should put metadata below or in collapsible sections.
  - Composer remains reachable without covering the latest response.
- Touch/hover differences: controls must be touch-sized on mobile; hover-only affordances are not sufficient.

## Interaction states

- Loading: disable send, show "sending" or "thinking" state, keep typed text recoverable.
- Empty: explain that this is live chat, distinct from the message board.
- Error: show provider/config/runtime failure plainly and preserve the unsent or failed prompt.
- Success: append assistant response and transcript metadata.
- Disabled: disable send when provider config is missing, another send is in progress, or the input is empty.
- Offline/slow network: preserve local typed input and show timeout/retry status.

## Content voice

- Tone: direct, warm, and specific.
- Terminology:
  - Use "chat" for live dialogue.
  - Use "message board" for asynchronous notes.
  - Use "request" for structured companion-to-human asks.
  - Use "memory proposal" when text is not yet committed to durable memory.
  - Use "Memory Steward" only in implementation/docs; avoid surfacing it as a second companion persona in ordinary chat.
  - Use "memory review" for the sparse human exception queue.
  - Use "controlled presence" for M9 scheduled wake behavior; avoid implying voice or Signal availability.
- Microcopy rules:
  - Do not imply memory was saved unless it was actually committed.
  - Do not imply a wake ran during chat.
  - Do not hide provider or transcript failures behind soft language.
  - Do not make ordinary chat narrate background memory operations.

## Implementation constraints

- Framework/styling system: Flask/Jinja in `window/window.py`, with existing inline CSS.
- Design-token constraints: reuse current CSS variables from `get_css_vars`.
- Performance constraints: first M7 chat may be synchronous request/response; streaming can wait.
- Compatibility constraints: the Pi production environment is also development, so changes must be reversible and tested locally.
- Test/screenshot expectations:
  - Route tests for `/chat`.
  - Route tests for `/memory-review` once implemented.
  - Unit tests for dialogue engine boundaries.
  - Unit tests for Memory Steward decision schema, policy gate, quarantine, and retrieval filtering.
  - No POST/write routes added to `/life`.
  - No scheduler mutation during M9.0 or M9.1.
  - M9 route/report tests must prove pause, rollback, and single-wake behavior before live activation.
  - Manual browser check on `http://127.0.0.1:3000`.

## Open questions

- [ ] What is the exact human-facing companion name in the chat header? Owner: user. Impact: final polish, not blocking.
- [ ] Which scheduler mechanism should M9 prefer after dry-run evidence: cron, systemd timer, or an existing project wrapper? Owner: implementation/user. Impact: deployment and rollback shape.
- [ ] Should the M9 pause flag suppress only scheduled wakes, or also manual scheduler dry-run commands? Owner: implementation/user. Impact: operator control semantics.
