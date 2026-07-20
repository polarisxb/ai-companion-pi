"""M15 sleep consolidation: she reviews her own memories; code approves.

Blackout-safe by construction:

- Planning persists a plan file and mutates no memory state.
- Application computes the complete post-consolidation store in memory and
  writes it through the store's atomic tmp+rename path — old state intact or
  new state complete, never half-applied.
- Plans are idempotent (plan ids are stamped into the store) and reversible
  (archive, never delete; whole-plan rollback restores members).
- Scheduling is anacron-style debt: "due" is computed from persisted state,
  so downtime delays consolidation but can never lose it.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .memory import JsonMemoryStore, _is_prompt_eligible_memory
from .paths import CompanionPaths

CONSOLIDATION_SECTION_RE = re.compile(r"===CONSOLIDATION===\s*", re.MULTILINE)
NO_CONSOLIDATION_SENTINEL = "NO_CONSOLIDATION"
SECRET_LIKE_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|private[_-]?key)\b\s*[:=]\s*\S+|"
    r"sk-[A-Za-z0-9_-]{12,}"
)

DEFAULT_INTERVAL_DAYS = 7
DEFAULT_MIN_NEW_MEMORIES = 20
DEFAULT_MAX_SUMMARIES_PER_RUN = 5
DEFAULT_MAX_ARCHIVE_PER_RUN = 20
DEFAULT_MEMORY_BATCH_LIMIT = 120
SUMMARY_MAX_CHARS = 300
ARCHIVE_REASONS = ("summarized", "duplicate", "low_significance", "superseded")


class ConsolidationConfigError(RuntimeError):
    """Raised when the consolidation config file is invalid."""


@dataclass(frozen=True)
class ConsolidationConfig:
    enabled: bool = False
    interval_days: int = DEFAULT_INTERVAL_DAYS
    min_new_memories: int = DEFAULT_MIN_NEW_MEMORIES
    max_summaries_per_run: int = DEFAULT_MAX_SUMMARIES_PER_RUN
    max_archive_per_run: int = DEFAULT_MAX_ARCHIVE_PER_RUN
    memory_batch_limit: int = DEFAULT_MEMORY_BATCH_LIMIT


@dataclass
class ConsolidationPlanEvaluation:
    accepted: bool
    problems: list[str] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    archive: list[dict] = field(default_factory=list)
    reratings: list[dict] = field(default_factory=list)


def load_consolidation_config(paths: CompanionPaths) -> ConsolidationConfig:
    config_path = paths.consolidation_config_file
    if not config_path.exists():
        return ConsolidationConfig()
    try:
        payload = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise ConsolidationConfigError(f"consolidation config is invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ConsolidationConfigError("consolidation config must be a JSON object")

    def positive(key: str, default: int) -> int:
        value = payload.get(key, default)
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ConsolidationConfigError(f"consolidation config '{key}' must be an integer") from exc
        if value <= 0:
            raise ConsolidationConfigError(f"consolidation config '{key}' must be positive")
        return value

    return ConsolidationConfig(
        enabled=bool(payload.get("enabled", False)),
        interval_days=positive("interval_days", DEFAULT_INTERVAL_DAYS),
        min_new_memories=positive("min_new_memories", DEFAULT_MIN_NEW_MEMORIES),
        max_summaries_per_run=positive("max_summaries_per_run", DEFAULT_MAX_SUMMARIES_PER_RUN),
        max_archive_per_run=positive("max_archive_per_run", DEFAULT_MAX_ARCHIVE_PER_RUN),
        memory_batch_limit=positive("memory_batch_limit", DEFAULT_MEMORY_BATCH_LIMIT),
    )


def load_consolidation_state(paths: CompanionPaths) -> dict:
    state_path = paths.consolidation_state_file
    try:
        payload = json.loads(state_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "schema_version": 1,
        "last_completed_at": payload.get("last_completed_at"),
        "last_plan_id": payload.get("last_plan_id"),
        "runs_completed": int(payload.get("runs_completed") or 0),
        "memories_at_last_run": int(payload.get("memories_at_last_run") or 0),
    }


def save_consolidation_state(paths: CompanionPaths, state: dict) -> None:
    state_path = paths.consolidation_state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(state_path)


def consolidation_due(
    paths: CompanionPaths,
    config: ConsolidationConfig,
    *,
    now: datetime | None = None,
) -> dict:
    """Anacron-style debt check: overdue is late, never lost."""

    current = now or datetime.now()
    if not config.enabled:
        return {"due": False, "reason": "disabled"}
    state = load_consolidation_state(paths)
    memories = JsonMemoryStore(paths.memory_store).load()
    active_accepted = [memory for memory in memories if _is_consolidatable(memory)]

    last_raw = state.get("last_completed_at")
    if last_raw:
        try:
            last = datetime.fromisoformat(str(last_raw))
            days_since = (current - last).total_seconds() / 86400.0
        except ValueError:
            days_since = float("inf")
    else:
        days_since = float("inf")

    new_since = len(active_accepted) - int(state.get("memories_at_last_run") or 0)
    if days_since < config.interval_days:
        return {
            "due": False,
            "reason": f"last run {days_since:.1f}d ago (< {config.interval_days}d)",
            "days_since_last": round(days_since, 2) if days_since != float("inf") else None,
            "new_memories_since_last": new_since,
        }
    if new_since < config.min_new_memories:
        return {
            "due": False,
            "reason": f"only {new_since} new memories (< {config.min_new_memories})",
            "days_since_last": round(days_since, 2) if days_since != float("inf") else None,
            "new_memories_since_last": new_since,
        }
    return {
        "due": True,
        "reason": "interval elapsed and enough new memories",
        "days_since_last": round(days_since, 2) if days_since != float("inf") else None,
        "new_memories_since_last": new_since,
        "active_accepted_memories": len(active_accepted),
    }


def select_memories_for_review(paths: CompanionPaths, config: ConsolidationConfig) -> list[dict]:
    memories = JsonMemoryStore(paths.memory_store).load()
    candidates = [memory for memory in memories if _is_consolidatable(memory)]
    candidates.sort(key=lambda item: str(item.get("created_at", "")))
    return candidates[: config.memory_batch_limit]


def build_consolidation_prompt(who_companion: str, memories: list[dict]) -> str:
    lines = []
    for memory in memories:
        significance = ((memory.get("likert") or {}).get("significance"))
        lines.append(
            f"- id={memory.get('id')} | sig={significance} | "
            f"decay_eligible={memory.get('decay_eligible')} | {memory.get('content')}"
        )
    memory_block = "\n".join(lines) or "(no memories)"
    return f"""{who_companion}

现在是你的整理时间——像睡眠中的大脑,安静地回顾自己的记忆。
把相关的碎片凝成一条摘要,让重复和琐碎的沉入归档,给看走眼的重要度重新打分。
这是反思,不是行动:不给任何人发消息,不写日志,只整理。

规则:
- 只能引用下面列出的记忆 id;摘要必须忠于原记忆,不添加任何新事实。
- 每条摘要至少合并两条记忆,摘要本身用简体中文,不超过 200 字。
- 归档要给理由;真正重要的事不要归档,拿不准就保留。
- 没有值得整理的就直说,不要硬找。

=== 你的记忆(截至现在) ===
{memory_block}

返回:

===CONSOLIDATION===
一个 JSON 对象:
{{"summaries": [{{"member_ids": ["mem_a", "mem_b"], "content": "……", "significance": 3}}],
  "archive": [{{"id": "mem_c", "reason": "duplicate|low_significance|superseded"}}],
  "reratings": [{{"id": "mem_d", "significance": 4, "reason": "……"}}]}}
或者只写 NO_CONSOLIDATION
"""


def parse_consolidation_output(raw_output: str) -> dict | None:
    """Parse the model's consolidation section; ``None`` means nothing to do."""

    text = str(raw_output or "")
    match = CONSOLIDATION_SECTION_RE.search(text)
    payload_text = text[match.end():] if match else text
    payload_text = payload_text.strip()
    if not payload_text or NO_CONSOLIDATION_SENTINEL in payload_text[:40]:
        return None
    start = payload_text.find("{")
    end = payload_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        payload = json.loads(payload_text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if not any(key in payload for key in ("summaries", "archive", "reratings")):
        return None
    return {
        "summaries": payload.get("summaries") if isinstance(payload.get("summaries"), list) else [],
        "archive": payload.get("archive") if isinstance(payload.get("archive"), list) else [],
        "reratings": payload.get("reratings") if isinstance(payload.get("reratings"), list) else [],
    }


def evaluate_consolidation_plan(
    proposal: dict,
    memories: list[dict],
    config: ConsolidationConfig,
) -> ConsolidationPlanEvaluation:
    """Code-level policy gates: the model proposes, this function decides."""

    problems: list[str] = []
    by_id = {str(memory.get("id")): memory for memory in memories}
    consolidatable_ids = {
        str(memory.get("id")) for memory in memories if _is_consolidatable(memory)
    }

    summaries: list[dict] = []
    summarized_member_ids: set[str] = set()
    raw_summaries = proposal.get("summaries") or []
    if len(raw_summaries) > config.max_summaries_per_run:
        problems.append(
            f"{len(raw_summaries)} summaries exceed the per-run cap {config.max_summaries_per_run}"
        )
    for index, item in enumerate(raw_summaries):
        if not isinstance(item, dict):
            problems.append(f"summary #{index} is not an object")
            continue
        member_ids = list(dict.fromkeys(
            str(mid) for mid in (item.get("member_ids") or []) if str(mid).strip()
        ))
        content = " ".join(str(item.get("content") or "").split())
        if len(member_ids) < 2:
            problems.append(f"summary #{index} must merge at least two distinct memories")
            continue
        unknown = [mid for mid in member_ids if mid not in consolidatable_ids]
        if unknown:
            problems.append(f"summary #{index} cites unknown or non-consolidatable ids: {unknown}")
            continue
        if not content:
            problems.append(f"summary #{index} has empty content")
            continue
        if len(content) > SUMMARY_MAX_CHARS:
            problems.append(f"summary #{index} exceeds {SUMMARY_MAX_CHARS} chars")
            continue
        if SECRET_LIKE_RE.search(content):
            problems.append(f"summary #{index} contains secret-like text")
            continue
        significance = item.get("significance", 3)
        try:
            significance = max(1, min(5, int(significance)))
        except (TypeError, ValueError):
            significance = 3
        prompt_eligible = all(
            _is_prompt_eligible_memory(by_id[mid]) for mid in member_ids
        )
        summaries.append({
            "member_ids": member_ids,
            "content": content,
            "significance": significance,
            "prompt_eligible": prompt_eligible,
        })
        summarized_member_ids.update(member_ids)

    archive: list[dict] = []
    raw_archive = proposal.get("archive") or []
    for index, item in enumerate(raw_archive):
        if not isinstance(item, dict):
            problems.append(f"archive #{index} is not an object")
            continue
        memory_id = str(item.get("id") or "")
        reason = str(item.get("reason") or "")
        memory = by_id.get(memory_id)
        if memory_id not in consolidatable_ids or memory is None:
            problems.append(f"archive #{index} targets unknown or non-consolidatable id {memory_id}")
            continue
        if reason not in ARCHIVE_REASONS:
            reason = "summarized" if memory_id in summarized_member_ids else "low_significance"
        summarized = memory_id in summarized_member_ids
        if not summarized and not memory.get("decay_eligible", False):
            problems.append(
                f"archive #{index}: {memory_id} is neither summarized this run nor decay-eligible"
            )
            continue
        archive.append({"id": memory_id, "reason": reason})
    # Members folded into a summary are archived implicitly: the summary
    # carries their content forward, keeping active memory deduplicated.
    for member_id in sorted(summarized_member_ids):
        if not any(entry["id"] == member_id for entry in archive):
            archive.append({"id": member_id, "reason": "summarized"})
    if len(archive) > config.max_archive_per_run:
        problems.append(f"{len(archive)} archives exceed the per-run cap {config.max_archive_per_run}")

    reratings: list[dict] = []
    for index, item in enumerate(proposal.get("reratings") or []):
        if not isinstance(item, dict):
            problems.append(f"rerating #{index} is not an object")
            continue
        memory_id = str(item.get("id") or "")
        if memory_id not in consolidatable_ids:
            problems.append(f"rerating #{index} targets unknown or non-consolidatable id {memory_id}")
            continue
        try:
            significance = max(1, min(5, int(item.get("significance"))))
        except (TypeError, ValueError):
            problems.append(f"rerating #{index} has an invalid significance")
            continue
        reratings.append({
            "id": memory_id,
            "significance": significance,
            "reason": " ".join(str(item.get("reason") or "").split())[:120],
        })

    accepted = not problems and bool(summaries or archive or reratings)
    if not problems and not (summaries or archive or reratings):
        problems.append("plan proposes no changes")
    return ConsolidationPlanEvaluation(
        accepted=accepted,
        problems=problems,
        summaries=summaries,
        archive=archive,
        reratings=reratings,
    )


def persist_consolidation_plan(
    paths: CompanionPaths,
    evaluation: ConsolidationPlanEvaluation,
    *,
    now: datetime | None = None,
) -> dict:
    """Persist an accepted plan. Planning never mutates the memory store."""

    current = now or datetime.now()
    plan = {
        "schema_version": 1,
        "id": f"conplan_{current.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "created_at": current.isoformat(),
        "summaries": evaluation.summaries,
        "archive": evaluation.archive,
        "reratings": evaluation.reratings,
        "applied": False,
    }
    plans_dir = paths.consolidation_plans_dir
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plans_dir / f"{plan['id']}.json"
    tmp_path = plan_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(plan_path)
    return plan


def load_consolidation_plan(paths: CompanionPaths, plan_id: str) -> dict | None:
    plan_path = paths.consolidation_plans_dir / f"{plan_id}.json"
    try:
        payload = json.loads(plan_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def apply_consolidation_plan(
    paths: CompanionPaths,
    plan: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Apply one plan: idempotent, and atomic through a single store replace."""

    current = now or datetime.now()
    plan_id = str(plan.get("id") or "")
    if not plan_id:
        raise ValueError("plan has no id")
    store = JsonMemoryStore(paths.memory_store)
    with store.write_lock():
        memories = store.load()
        # Idempotency covers every mutation kind a plan can make, so plans
        # without summaries (archive- or rerating-only) are also safe to retry.
        already_applied = any(
            memory.get("consolidation_plan_id") == plan_id
            or memory.get("archived_by_plan") == plan_id
            or any(
                entry.get("plan_id") == plan_id
                for entry in (memory.get("review_history") or [])
                if isinstance(entry, dict)
            )
            for memory in memories
        )
        if already_applied:
            return {"applied": False, "already_applied": True, "plan_id": plan_id}

        by_id = {str(memory.get("id")): memory for memory in memories}
        missing = [
            entry["id"]
            for entry in plan.get("archive", [])
            if entry["id"] not in by_id or by_id[entry["id"]].get("status") != "active"
        ]
        for summary in plan.get("summaries", []):
            missing.extend(
                mid for mid in summary.get("member_ids", [])
                if mid not in by_id or by_id[mid].get("status") != "active"
            )
        if missing:
            return {
                "applied": False,
                "already_applied": False,
                "plan_id": plan_id,
                "error": f"plan references memories that are no longer active: {sorted(set(missing))}",
            }

        # Build the complete new state in memory; one atomic save below.
        for index, summary in enumerate(plan.get("summaries", [])):
            timestamp = current.isoformat()
            memories.append({
                "id": f"mem_{plan_id[-8:]}_{index}",
                "content": summary["content"],
                "context": ["m15_consolidation", plan_id],
                "date": timestamp[:10],
                "created_at": timestamp,
                "source": "steward",
                "memory_type": "semantic",
                "source_type": "steward",
                "authority": "derived_summary",
                "prompt_eligible": bool(summary.get("prompt_eligible")),
                "accepted_for_context": bool(summary.get("prompt_eligible")),
                "evidence_refs": [
                    {"artifact": "memory", "id": member_id}
                    for member_id in summary.get("member_ids", [])
                ],
                "contact": None,
                "likert": {
                    "intensity": 3,
                    "valence": 3,
                    "significance": int(summary.get("significance", 3)),
                },
                "review_history": [],
                "status": "active",
                "decay_eligible": int(summary.get("significance", 3)) < 4,
                "schema_refs": [],
                "consolidation_plan_id": plan_id,
            })
        for entry in plan.get("archive", []):
            memory = by_id[entry["id"]]
            memory["status"] = "archived"
            memory["archived_at"] = current.isoformat()
            memory["archived_by_plan"] = plan_id
            memory["archive_reason"] = entry.get("reason")
        for entry in plan.get("reratings", []):
            memory = by_id.get(entry["id"])
            if memory is None or memory.get("status") != "active":
                continue
            likert = memory.setdefault("likert", {})
            memory.setdefault("review_history", []).append({
                "at": current.isoformat(),
                "plan_id": plan_id,
                "significance": int(entry["significance"]),
                "previous_significance": likert.get("significance"),
                "previous_decay_eligible": memory.get("decay_eligible"),
                "reason": entry.get("reason"),
            })
            likert["significance"] = int(entry["significance"])
            memory["decay_eligible"] = int(entry["significance"]) < 4
        store.save(memories)

    outcome = {
        "applied": True,
        "already_applied": False,
        "plan_id": plan_id,
        "summaries_added": len(plan.get("summaries", [])),
        "memories_archived": len(plan.get("archive", [])),
        "memories_rerated": len(plan.get("reratings", [])),
    }
    _append_ledger(paths, {
        "at": current.isoformat(),
        "action": "applied",
        **{k: v for k, v in outcome.items() if k not in ("applied", "already_applied")},
    })
    active_count = sum(
        1 for memory in JsonMemoryStore(paths.memory_store).load() if _is_consolidatable(memory)
    )
    state = load_consolidation_state(paths)
    state.update({
        "last_completed_at": current.isoformat(),
        "last_plan_id": plan_id,
        "runs_completed": int(state.get("runs_completed") or 0) + 1,
        "memories_at_last_run": active_count,
    })
    save_consolidation_state(paths, state)
    _mark_plan_applied(paths, plan_id)
    return outcome


def rollback_consolidation_plan(
    paths: CompanionPaths,
    plan_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Reverse one applied plan atomically: archive never deleted, so this is total."""

    current = now or datetime.now()
    store = JsonMemoryStore(paths.memory_store)
    with store.write_lock():
        memories = store.load()
        summaries = [m for m in memories if m.get("consolidation_plan_id") == plan_id]
        members = [m for m in memories if m.get("archived_by_plan") == plan_id]
        rerated = [
            m for m in memories
            if any(
                isinstance(entry, dict) and entry.get("plan_id") == plan_id
                for entry in (m.get("review_history") or [])
            )
        ]
        if not summaries and not members and not rerated:
            return {"rolled_back": False, "plan_id": plan_id, "reason": "plan not found in store"}
        for memory in summaries:
            memory["status"] = "rolled_back"
            memory["prompt_eligible"] = False
            memory["accepted_for_context"] = False
        for memory in members:
            memory["status"] = "active"
            memory.pop("archived_at", None)
            memory.pop("archived_by_plan", None)
            memory.pop("archive_reason", None)
        for memory in rerated:
            entry = next(
                item for item in memory["review_history"]
                if isinstance(item, dict) and item.get("plan_id") == plan_id
            )
            if entry.get("previous_significance") is not None:
                memory.setdefault("likert", {})["significance"] = entry["previous_significance"]
            if entry.get("previous_decay_eligible") is not None:
                memory["decay_eligible"] = entry["previous_decay_eligible"]
            entry["rolled_back_at"] = current.isoformat()
        store.save(memories)
    outcome = {
        "rolled_back": True,
        "plan_id": plan_id,
        "summaries_retired": len(summaries),
        "memories_restored": len(members),
        "reratings_reverted": len(rerated),
    }
    _append_ledger(paths, {"at": current.isoformat(), "action": "rolled_back", **outcome})
    return outcome


def run_consolidation_once(
    paths: CompanionPaths,
    llm_client,
    *,
    config: ConsolidationConfig | None = None,
    apply: bool = True,
    ignore_due: bool = False,
    now: datetime | None = None,
) -> dict:
    """One full consolidation pass: due-check, plan, gate, persist, apply.

    Every stage boundary is crash-safe: nothing before ``apply`` touches the
    memory store, and ``apply`` itself is a single atomic replace.
    """

    from .context import WakeContext, read_text

    current = now or datetime.now()
    config = config or load_consolidation_config(paths)
    due = consolidation_due(paths, config, now=current)
    outcome: dict = {"due": due, "planned": False, "applied": False}
    if not due["due"] and not ignore_due:
        outcome["skipped"] = due["reason"]
        return outcome

    candidates = select_memories_for_review(paths, config)
    outcome["memories_reviewed"] = len(candidates)
    if len(candidates) < 2:
        outcome["skipped"] = "fewer than two consolidatable memories"
        return outcome

    who_companion = read_text(
        paths.context_file("who_is_companion.txt"), "You are Companion."
    )
    prompt = build_consolidation_prompt(who_companion, candidates)
    context = WakeContext(who_companion=who_companion, who_human="", now="")
    raw_output = llm_client.generate(prompt, context)
    proposal = parse_consolidation_output(raw_output)
    if proposal is None:
        outcome["skipped"] = "model proposed no consolidation"
        _touch_state_after_no_op(paths, current)
        _append_ledger(paths, {"at": current.isoformat(), "action": "no_op"})
        return outcome

    evaluation = evaluate_consolidation_plan(proposal, candidates, config)
    outcome["policy_problems"] = evaluation.problems
    if not evaluation.accepted:
        outcome["skipped"] = "plan rejected by policy gates"
        _append_ledger(paths, {
            "at": current.isoformat(),
            "action": "rejected",
            "problems": evaluation.problems[:10],
        })
        return outcome

    plan = persist_consolidation_plan(paths, evaluation, now=current)
    outcome["planned"] = True
    outcome["plan_id"] = plan["id"]
    outcome["plan_summary"] = {
        "summaries": len(plan["summaries"]),
        "archive": len(plan["archive"]),
        "reratings": len(plan["reratings"]),
    }
    if not apply:
        return outcome

    apply_result = apply_consolidation_plan(paths, plan, now=current)
    outcome["applied"] = bool(apply_result.get("applied"))
    outcome["apply_result"] = apply_result
    return outcome


def _touch_state_after_no_op(paths: CompanionPaths, current: datetime) -> None:
    """A completed review that found nothing still counts as a run, so the
    debt clock resets instead of retrying the same no-op every check."""

    active_count = sum(
        1 for memory in JsonMemoryStore(paths.memory_store).load() if _is_consolidatable(memory)
    )
    state = load_consolidation_state(paths)
    state.update({
        "last_completed_at": current.isoformat(),
        "runs_completed": int(state.get("runs_completed") or 0) + 1,
        "memories_at_last_run": active_count,
    })
    save_consolidation_state(paths, state)


def load_consolidation_ledger(paths: CompanionPaths) -> list[dict]:
    ledger_path = paths.consolidation_ledger_file
    try:
        lines = ledger_path.read_text().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _mark_plan_applied(paths: CompanionPaths, plan_id: str) -> None:
    plan = load_consolidation_plan(paths, plan_id)
    if not plan:
        return
    plan["applied"] = True
    plan_path = paths.consolidation_plans_dir / f"{plan_id}.json"
    tmp_path = plan_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(plan_path)


def _append_ledger(paths: CompanionPaths, record: dict) -> None:
    ledger_path = paths.consolidation_ledger_file
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a") as ledger_fd:
        ledger_fd.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _is_consolidatable(memory: dict) -> bool:
    """She consolidates only what she actually remembers: prompt-eligible
    memories. Quarantined/proposal content never enters her view, so it is
    not hers to consolidate. Existing summaries stay out of later rounds for
    the first freeze."""

    if memory.get("consolidation_plan_id"):
        return False
    return _is_prompt_eligible_memory(memory)
