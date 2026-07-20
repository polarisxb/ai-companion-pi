# M12 Semantic Memory Retrieval Design

Status: M12.0 design; implementation follows in M12.1-M12.5
Last updated: 2026-07-20

## Why M12

Memory recall is currently lexical: the M8 retrieval assembler
(`companion_core/memory_retrieval.py`) ranks accepted memories by literal
term overlap plus recency. If the human asks about a memory using different
words than the stored text, the companion cannot find it. The embedding
capability has existed since M3 (memory-server sentence-transformers, M3.23
semantic shadow mode) but was deliberately kept non-authoritative, and
`docs/internal-life-loop.md` has carried the standing decision note: decide
whether production memory routes through semantic search.

M12 makes that decision: **the JSON store stays the authoritative record;
semantic similarity becomes a retrieval ranking layer on top of it.** Nothing
about what the companion is allowed to remember changes; only how well she
finds it.

M12 is not a voice, hardware, scheduler, or Signal milestone.

## Confirmed Direction

- Authority is unchanged: `memory-server/memory_store.json` remains the
  durable record; M8 policy filters (prompt-eligible, authority, type,
  quarantine exclusion) keep running before any ranking.
- The semantic index is derived, rebuildable data keyed by memory id and
  content hash: `life-loop/semantic_index.json`. Deleting it is a complete,
  safe rollback to lexical retrieval.
- Retrieval stays read-only: ranking never writes the index; index updates
  happen only through the explicit M12.3 backfill/sync command.
- Enablement is config-gated and ships off:
  `life-loop/semantic_retrieval_config.json` with `"enabled": false` absent
  or false means byte-identical current behavior.
- Pluggable embedding backends:
  - `hashing`: deterministic character/word n-gram hashing vectors, zero
    dependencies. Used by tests and as a degraded-but-honest fallback.
  - `sentence-transformers`: real semantic vectors on the Pi. Default model
    `paraphrase-multilingual-MiniLM-L12-v2`, because companion memories are
    Simplified Chinese and the legacy memory-server default
    (`all-MiniLM-L6-v2`) is English-focused.
- Deterministic fallback: backend unavailable, index missing, or config
  disabled all degrade to today's lexical scoring, recorded in the retrieval
  result (`semantic.status`), never raising into the dialogue path.
- Semantic shadow mode (M3.23) stays untouched; it remains isolated
  telemetry. M12's index is a separate, cleaner derivation from the
  authoritative store.

## Runtime Shape

```text
assemble_dialogue_memory_context (M8 assembler, dialogue + signal chat)
  -> policy filters (unchanged; quarantine/proposals never pass)
  -> lexical scoring (unchanged base scores)
  -> semantic ranking layer (only when enabled and ready):
       embed(query) once, cosine against indexed vectors,
       score += round(similarity * semantic_scale) when similarity >= min_similarity,
       reasons += "semantic_similarity:<value>"
  -> sort and select top N (unchanged)
result.semantic records backend, status, model, scored count for audit
```

Index sync is explicit:

```text
scripts/run_m12_semantic_backfill.py
  -> load accepted memories from the JSON store
  -> embed missing/stale (content-hash mismatch) prompt-eligible entries
  -> prune index entries whose memory disappeared
  -> atomic write life-loop/semantic_index.json + report
```

## Files

- `companion_core/semantic_retrieval.py`: config, backends, index, ranking.
- `life-loop/semantic_retrieval_config.json` (gitignored, template in
  `templates/semantic_retrieval_config.template.json`):

```json
{
  "enabled": false,
  "backend": "hashing",
  "model": null,
  "min_similarity": 0.15,
  "semantic_scale": 10
}
```

- `life-loop/semantic_index.json` (gitignored, derived):

```json
{
  "schema_version": 1,
  "backend": "hashing",
  "model": "hashing-v1",
  "dims": 256,
  "updated_at": "...",
  "entries": {"mem_id": {"content_hash": "sha256:...", "vector": [0.1]}}
}
```

## Boundaries

```json
{
  "json_store_remains_authoritative": true,
  "retrieval_writes_index": false,
  "policy_filters_before_ranking": true,
  "proposal_or_quarantine_prompt_authority": false,
  "semantic_shadow_authority_promoted": false,
  "provider_generation_requested": false,
  "wake_cycle_run": false,
  "scheduler_mutated": false
}
```

- Ranking can only reorder memories that already passed the M8 policy gate;
  a similarity of 1.0 on a quarantined memory still retrieves nothing.
- The index stores vectors and hashes; memory text lives only in the
  authoritative store.
- Embedding runs locally (hashing) or via the local sentence-transformers
  model; no network provider is involved.

## Stages

### M12.1 Semantic readiness audit (read-only)

```text
companion_core/m12_semantic_readiness.py
scripts/run_m12_semantic_readiness.py
life-loop/m12_semantic_readiness_report.json
```

Checks: authoritative store integrity, prompt-eligible census, config
validity, backend probe (embed a fixture without writing), index
existence/coverage/staleness, shadow telemetry summary from wake events.
Recommendation: `m12_semantic_readiness_ready` | `inspect`.

### M12.2 Retrieval upgrade + behavior gate

Code: semantic ranking layer in `memory_retrieval.py` (surgical, additive).

```text
companion_core/m12_semantic_retrieval_check.py
scripts/run_m12_semantic_retrieval_check.py
life-loop/m12_semantic_retrieval_report.json
```

The gate proves, in an isolated smoke home with the hashing backend:
semantic ranking surfaces a related memory that lexical scoring misses;
policy filters still exclude quarantined/proposal memories at any
similarity; disabled config and missing index fall back deterministically;
retrieval writes nothing. Recommendation: `m12_semantic_retrieval_ready` |
`inspect`.

### M12.3 Index backfill/sync

```text
companion_core/m12_semantic_backfill.py
scripts/run_m12_semantic_backfill.py
life-loop/m12_semantic_backfill_report.json
```

Idempotent incremental sync: embed missing/stale, prune orphans, atomic
replace, counts in the report. Re-running with no changes is a no-op.
Rollback = delete the index file. Recommendation:
`m12_semantic_backfill_ready` | `inspect`.

### M12.4 Observation (read-only)

```text
companion_core/m12_semantic_observation.py
scripts/run_m12_semantic_observation.py
life-loop/m12_semantic_observation_report.json
```

Coverage ratio over prompt-eligible memories, staleness count, backend
probe, live retrieval probe (read-only assembler call recording which
backend actually served), fallback drill (config disabled probe).
Recommendation: `m12_semantic_observation_ready` | `inspect`.

### M12.5 Freeze (read-only)

```text
companion_core/m12_semantic_freeze.py
scripts/run_m12_semantic_freeze.py
life-loop/m12_semantic_freeze_report.json
```

Requires M12.1-M12.4 evidence plus intact M7.6/M8.7 freezes (memory-adjacent
milestones), boundary verification, and template default-off. Recommendation:
`m12_semantic_retrieval_frozen` | `inspect`.

## Explicit Non-goals

- No change to memory acceptance, policy, steward, review, or quarantine.
- No promotion of the M3.23 semantic shadow store.
- No memory consolidation/summarization (future milestone).
- No new prompt sections; the memory block shape stays the same.
- No network calls; embedding is local.
- No automatic index writes from retrieval, dialogue, or wake paths.

## Open Questions

- Should the wake cycle context (recency-based `recent_for_context`) also use
  semantic retrieval against the context capsule focus? Default: no for M12;
  wakes have no query. Owner: implementation, revisit after observation.
- When should the Pi run the backfill sync: manual, or appended to an
  existing maintenance cycle? Default: manual/operator. Owner: user.
