# M15 Sleep Consolidation Design

Status: M15.0 design; core engine follows in M15.1
Last updated: 2026-07-20

## Why M15

Human brains consolidate during sleep: episodic fragments merge into
narratives, trivia fades, important things get re-weighted with hindsight.
The companion's memory today only accumulates. The legacy stack had a poetic
version of this ("this is reflection, not action — dream-like") in
`consolidate_short.sh` and `memory-server/memory_consolidation.py`, but it
predates the gate discipline and sits unused.

The current schema was built for this day: `derived_summary` is already an
allowed prompt authority and every memory carries `decay_eligible`. M15
fills the reserved slot: **the companion herself (the LLM) periodically
reviews her own memories and proposes consolidation; code-level policy gates
approve; application is crash-safe, idempotent, and reversible.**

## The Blackout Problem (first-class requirement)

The Pi may lose power mid-run and is not guaranteed to be always-on. M15
treats both as normal operating conditions, not edge cases.

### Crash safety: plan/apply separation + single atomic write

1. **Planning writes no memory state.** The LLM's proposed plan is persisted
   to `life-loop/consolidation_plans/<plan_id>.json` first. A crash during
   planning loses only a thought, never a memory.
2. **Application is one atomic replace.** The complete post-consolidation
   store (summaries added, members marked archived, re-ratings applied) is
   computed in memory and written through the store's existing
   tmp-file + `rename` path. The filesystem guarantees old-state-intact or
   new-state-complete; a half-applied store cannot exist.
3. **Archive, never delete.** Consolidated originals get
   `status="archived"` plus `archived_by_plan`; archived memories drop out
   of retrieval and prompt context automatically (eligibility already
   requires `status == "active"`), but remain in the store for audit and
   rollback.
4. **Idempotent application.** Every plan has a unique id stamped onto the
   summaries it creates and the members it archives. Re-applying an applied
   plan is a no-op — so the crash window between "store saved" and "ledger
   appended" resolves safely on retry.
5. **Whole-plan rollback.** `rollback` reverses one plan atomically:
   summaries retire, members return to `active`. Her memories are never more
   than one command away from their pre-consolidation state.

### Not-always-on: anacron-style debt, not alarm-clock cron

There is no "every Sunday 03:00". Instead, a cheap periodic check (and every
boot) asks: **is consolidation due?**

```text
due = enabled
      AND days since last successful run >= interval_days (default 7)
      AND new accepted memories since last run >= min_new_memories (default 20)
```

Downtime just makes the debt more overdue; the next check after power-on
pays it. All trigger state lives in `life-loop/consolidation_state.json`
(atomic writes). A missed window is a late consolidation, never a lost one.

## Runtime Shape

```text
consolidation check (cron/tick or manual; boots included)
  -> due? no: exit silently
  -> planning: load bounded batch of active accepted memories
       -> render consolidation prompt (her identity + memory list with ids)
       -> LLM returns JSON: summaries / archive / reratings, or NO_CONSOLIDATION
       -> parse defensively; persist plan file (no store mutation)
  -> policy gates (pure code, final authority):
       summaries: >=2 existing active member ids, content length cap,
         no secret-like text, prompt_eligible only if every member was,
         summary count cap per run
       archive: only plan members or decay_eligible memories; caps per run;
         user-asserted memories archivable only when summarized this run
       reratings: significance 1..5 on existing active memories only
       any violation -> plan rejected, nothing applied, evidence kept
  -> apply: single atomic store replace + ledger append + state update
  -> semantic index resync (M12 backfill) picks up the new shape
```

Boundaries: consolidation is not a wake cycle (no journal, no signal, no
requests, no wake events); it does not touch scheduler, chat, or provider
config; the provider call is its sole purpose and is recorded.

## Files

- `companion_core/consolidation.py` — engine (due-check, prompt, parser,
  policy gates, plan persistence, idempotent apply, rollback, state).
- `life-loop/consolidation_config.json` (gitignored; template provided):

```json
{
  "enabled": false,
  "interval_days": 7,
  "min_new_memories": 20,
  "max_summaries_per_run": 5,
  "max_archive_per_run": 20,
  "memory_batch_limit": 120
}
```

- `life-loop/consolidation_state.json` — last success, run counters.
- `life-loop/consolidation_plans/` — one JSON per plan (audit trail).
- `life-loop/consolidation_ledger.jsonl` — append-only apply/rollback log.

## Memory semantics

- Summaries are stored as normal memories with `authority="derived_summary"`,
  `source_type="steward"`, `evidence_refs` pointing at every member id, and
  `consolidation_plan_id`. They are prompt-eligible only when all members
  were, so consolidation can never smuggle quarantined content into prompts.
- Archived members keep their full content; retrieval, wake context, and the
  M12 index ignore them by the existing `status == "active"` rule.
- Re-ratings adjust `likert.significance` and `decay_eligible` only.

## Stages

- **M15.1** Core engine + tests (crash simulation, idempotency, rollback,
  catch-up debt) + runner CLI (`--check` / `--plan-only` / `--apply-plan` /
  `--rollback` / full run behind `--confirm-consolidation`). (Done.)
- **M15.2** Dry-run gate (`scripts/run_m15_consolidation_dry_run.py`):
  blackout drills (power loss before the atomic save; power loss in the
  bookkeeping window after it), idempotency for all three plan kinds,
  whole-plan rollback incl. rerating reversion, stale-plan refusal, hostile
  plan coverage across every policy gate, anacron catch-up debt after 45
  simulated offline days, and static guards (no network, no scheduler, no
  deletion paths). Report `life-loop/m15_consolidation_dry_run_report.json`,
  `recommendation=m15_consolidation_dry_run_ready`. (Done.)
- **M15.3** Supervised real trial on accumulated real memories
  (`m15_consolidation_trial_report.json`).
- **M15.4** Activation: anacron-style check wired into cron beside the M9
  tick (`m15_consolidation_activation_report.json`).
- **M15.5** Observation + freeze
  (`m15_consolidation_freeze_report.json`, `m15_sleep_consolidation_frozen`).

## Explicit Non-goals

- No deletion of memories, ever; archive only.
- No consolidation of quarantined/proposal/audit-only items.
- No new facts in summaries (summaries derive only from cited members).
- No change to M8 acceptance policy or M12 retrieval authority.
- No consolidation during a wake cycle or chat turn; it is its own quiet run.

## Open Questions

- Should she journal a short "dream note" about what she consolidated?
  Charming, cheap, and auditable — default yes in M15.3 unless it feels
  mechanical. Owner: user taste.
- Multi-level summaries (summaries of summaries) — allowed naturally later;
  capped out of scope for the first freeze.
