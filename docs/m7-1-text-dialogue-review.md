# M7.1 Text Dialogue Implementation Review

Status: integration review handoff  
Last updated: 2026-06-19  
Source context: `.omx/context/m7-text-dialogue-team-20260619T075455Z.md`

## Scope reviewed

This review covers the parallel M7.1 lanes that were visible in the team
worktrees during the review pass:

- Lane A: dialogue engine and provider/context integration.
- Lane B: transcript event storage, memory boundary, and proposal schema.
- Lane C: CLI one-turn chat.
- Lane D: tests and verification.

The review preserves the M6.7 boundaries: chat must not run wake cycles, mutate
scheduler/cron/timer/service state, promote semantic shadow authority, store raw
provider payloads by default, leak secrets, or expand broad memory authority.

## Integration recommendation

Use the worker-2 implementation as the primary integration base only after the
blocking fixes below are applied. It is the only reviewed lane that includes a
standalone `tests/test_m7_text_dialogue.py`, conversation event reporting, M6.7
final-freeze report awareness, and an `m7_text_dialogue_report.json` writer.

Borrow from worker-1 or worker-4 where useful:

- worker-1 has a chat-specific fake client that returns natural dialogue instead
  of wake-section output.
- worker-4 has a metadata delimiter that is less likely to accidentally parse a
  normal user-visible JSON snippet as internal metadata.

## Blocking review findings before merge

1. **CLI fake dialogue currently exercises wake-output shape in worker-2.**
   `scripts/chat_with_companion.py --fake-llm` routes through the existing wake
   `FakeLLMClient`, and `tests/test_m7_text_dialogue.py` asserts the reply starts
   with `===JOURNAL===`. M7 dialogue must be natural human-visible chat, not
   wake-cycle report sections. Add a chat-specific fake LLM or `--fake-response`
   path and assert the reply does not contain wake section headers.

2. **M6.7 final-freeze evidence is reported, but not enforced before provider
   work.** The worker-2 runner loads `life-loop/m6_final_freeze_report.json` and
   writes `m7_text_dialogue_report.json`, but it still calls the provider before
   stop reasons can block a missing or failed M6.7 freeze. The M7.1 acceptance
   says the final-freeze evidence is loaded before real-provider chat. Convert a
   missing/failed freeze into a pre-provider `provider_required`/`inspect` stop
   condition unless the caller explicitly opts into a fake/local smoke.

3. **Raw provider payload opt-in is present and should remain off by default.**
   The worker-2 transcript writes only an output hash by default and stores raw
   provider payload only when `store_raw_provider_payload=True`. Keep that flag
   inaccessible from the first public CLI unless there is a separate explicit
   replay/audit story. Add a regression asserting the CLI cannot store raw
   provider output by default.

4. **Memory proposal authority must not depend on model claims alone.**
   The worker-2 memory path requires user/source/authority/risk fields plus token
   overlap with the human text before auto-commit. Keep this conservative gate,
   and add tests for Chinese explicit preferences plus relationship-defining,
   medical/legal/financial/security-sensitive content staying proposal-only.

5. **Prompt context must use the filtered capsule renderer.** The worker-2
   prompt renders the raw context capsule JSON. Use the existing context capsule
   rendering/filtering path so non-renderable or legacy capsule items do not
   become prompt-authoritative, and add a regression that a non-renderable
   capsule item is absent from the provider prompt.

6. **Failure events should not imply completed transcript turns.** The reviewed
   worker-2 code writes completed transcript rows after provider success only,
   which is correct. Add a provider-failure regression that writes a failed
   dialogue event/report, preserves the human input for retry through CLI
   stderr/JSON metadata, and does not write an assistant turn marked completed.

7. **Default provider must match the M7 contract.** The design keeps
   `provider=deepseek` by default. Ensure the CLI default is `deepseek` unless
   `COMPANION_LLM_PROVIDER` overrides it, and add a no-env regression to prevent
   drift back to wake-loop defaults.

## Boundary checklist for the integrated patch

Before marking M7.1 ready, verify the integrated branch satisfies all of these:

- `companion_core/dialogue.py` exists and does not import or call
  `LifeLoopRunner` or `scripts/run_wake_cycle.py`.
- `scripts/chat_with_companion.py` defaults to `deepseek` and prints one
  natural companion reply by default.
- Successful turns append a transcript under `conversations/` and append a
  dialogue event under `life-loop/conversation_events.jsonl`.
- `life-loop/wake_events.jsonl` is not created by chat.
- `life-loop/m7_text_dialogue_report.json` records M7.1 status, stop reasons,
  M6.7 freeze evidence, provider, transcript path, and boundary facts.
- Transcript rows include human input and companion reply, hashes, provider, and
  `raw_provider_payload_stored`/equivalent false-by-default audit data.
- Secret-like values, including non-env literal API-key/token/password shapes, are
  redacted from transcripts, reports, CLI JSON, proposal files, and error
  messages.
- Accepted memory comes only from code-owned classification of explicit, low-risk,
  user-stated facts or preferences; inferred, sensitive, relationship-defining,
  ambiguous, spoofed model metadata, or model-originated content remains
  proposal-only.
- State updates happen only from explicit companion metadata and only after a
  successful provider turn.
- Semantic shadow memory remains non-authoritative for dialogue prompt context.
- Scheduler files, cron, timers, services, and `/life` write routes are not
  touched.

## Minimum verification commands

Run these on the integrated branch after applying the blocking fixes:

```bash
python -m compileall -q companion_core scripts tests window
python -m pytest tests/test_m7_text_dialogue.py -q
python -m pytest tests/test_internal_life_loop.py -q -k 'memory_policy or context_capsule or scheduler or final_freeze'
python -m py_compile companion_core/dialogue.py scripts/chat_with_companion.py
```

If `pytest` is unavailable in the environment, install the repository dev
requirements in an isolated environment first:

```bash
python -m pip install -r requirements-dev.txt
```

## Documentation updates required after integration

After the implementation is merged, update `docs/m7-text-dialogue-design.md` with
actual command output examples and the final transcript/event/proposal field
names. Keep this review file as the M7.1 integration checklist until M7.6
hardening freeze supersedes it.
