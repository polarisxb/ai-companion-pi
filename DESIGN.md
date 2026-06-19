# Design

## Source of truth

- Status: Active
- Last refreshed: 2026-06-19
- Primary product surfaces: Companion Window home, message board, creations, tasks, requests, `/life`, and planned M7 text chat.
- Evidence reviewed:
  - `docs/web-dashboard.md`
  - `docs/requests-system-design.md`
  - `docs/internal-life-loop.md`
  - `docs/m5-companion-quality-design.md`
  - `docs/m6-pi-field-pilot-design.md`
  - `window/window.py`

## Brand

- Personality: intimate, quiet, continuity-focused, technically honest.
- Trust signals: visible state, visible evidence paths, explicit boundaries, reversible actions, readable failure states.
- Avoid: marketing pages, decorative hero sections, vague companion mystique, hidden writes, automatic authority expansion.

## Product goals

- Goals:
  - Make the companion feel reachable, inspectable, and continuous.
  - Let the human talk to the same companion identity through a text-first channel.
  - Preserve M3-M6 safety contracts while adding a deliberately scoped dialogue surface.
- Non-goals:
  - Voice, camera, sensors, Signal, and hardware body work.
  - Scheduler installation as a prerequisite for dialogue.
  - Automatic promotion of chat text into long-term factual memory.
- Success signals:
  - The human can send text and receive a response from the live companion identity.
  - Conversation history is durable and reviewable.
  - Memory proposals are visible without being silently committed.
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
  - `/chat`: planned M7 live text dialogue.
- Content hierarchy:
  - Chat foregrounds the conversation.
  - State, provider, memory mode, transcript id, and proposals are secondary metadata.
  - Diagnostics are visible on failure but not dominant during normal conversation.

## Design principles

- Dialogue first: the chat page exists for direct exchange, not dashboard administration.
- Evidence without clutter: show enough status to trust the system without turning chat into a report page.
- Explicit write boundaries: distinguish transcript writes, memory proposals, memory commits, requests, and wake events.
- Natural reply, structured shadow: keep the visible reply conversational while
  machine-readable metadata stays behind the scenes.
- Reuse before redesign: extend existing Window styles and routes before introducing a new UI system.
- Tradeoffs: early M7 should prefer CLI and plain HTML reliability over polished real-time effects.

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
- Variants and states:
  - Empty conversation.
  - Sending.
  - Provider timeout/error.
  - Retry available.
  - Memory proposal present.
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
- Microcopy rules:
  - Do not imply memory was saved unless it was actually committed.
  - Do not imply a wake ran during chat.
  - Do not hide provider or transcript failures behind soft language.

## Implementation constraints

- Framework/styling system: Flask/Jinja in `window/window.py`, with existing inline CSS.
- Design-token constraints: reuse current CSS variables from `get_css_vars`.
- Performance constraints: first M7 chat may be synchronous request/response; streaming can wait.
- Compatibility constraints: the Pi production environment is also development, so changes must be reversible and tested locally.
- Test/screenshot expectations:
  - Route tests for `/chat`.
  - Unit tests for dialogue engine boundaries.
  - No POST/write routes added to `/life`.
  - Manual browser check on `http://127.0.0.1:3000`.

## Open questions

- [ ] Should M7.1 require DeepSeek only, or allow fake provider for local chat smoke tests? Owner: implementation. Impact: testing speed and provider safety.
- [ ] Should the first dashboard chat use full page reloads or fetch-based async submit? Owner: implementation. Impact: reliability versus UX smoothness.
- [ ] What is the exact human-facing companion name in the chat header? Owner: user. Impact: final polish, not blocking.
