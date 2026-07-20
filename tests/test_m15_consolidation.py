"""M15 sleep consolidation: planning, policy gates, crash-safe apply, rollback,
and anacron-style catch-up scheduling."""

import json
from datetime import datetime, timedelta

import pytest

from companion_core import (
    CompanionPaths,
    ConsolidationConfig,
    ConsolidationConfigError,
    JsonMemoryStore,
    apply_consolidation_plan,
    build_consolidation_prompt,
    consolidation_due,
    evaluate_consolidation_plan,
    load_consolidation_config,
    load_consolidation_ledger,
    load_consolidation_plan,
    load_consolidation_state,
    parse_consolidation_output,
    persist_consolidation_plan,
    rollback_consolidation_plan,
    run_consolidation_once,
    save_consolidation_state,
    select_memories_for_review,
)

NOW = datetime(2026, 7, 20, 22, 0, 0)


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def memory_row(
    memory_id,
    content,
    *,
    eligible=True,
    significance=3,
    created_at="2026-07-01T10:00:00",
):
    return {
        "id": memory_id,
        "content": content,
        "context": [],
        "date": created_at[:10],
        "created_at": created_at,
        "source": "human",
        "memory_type": "semantic",
        "source_type": "user",
        "authority": "user_asserted" if eligible else "model_proposed",
        "prompt_eligible": eligible,
        "accepted_for_context": eligible,
        "evidence_refs": [],
        "status": "active",
        "likert": {"intensity": 3, "valence": 3, "significance": significance},
        "review_history": [],
        "decay_eligible": significance < 4,
        "schema_refs": [],
    }


def seeded_home(tmp_path, extra_rows=None):
    paths = make_paths(tmp_path)
    rows = [
        memory_row("mem_walk1", "周二晚上一起散步聊了猫", created_at="2026-07-01T10:00:00"),
        memory_row("mem_walk2", "周四又散步，还是聊猫的名字", created_at="2026-07-03T10:00:00"),
        memory_row("mem_trivia", "今天喝了冰美式", significance=1, created_at="2026-07-05T10:00:00"),
        memory_row("mem_big", "他说下个月要搬家到海边", significance=5, created_at="2026-07-06T10:00:00"),
        memory_row("mem_quarantine", "未审核的提案内容", eligible=False, created_at="2026-07-07T10:00:00"),
    ]
    rows.extend(extra_rows or [])
    JsonMemoryStore(paths.memory_store).save(rows)
    return paths


def good_proposal():
    return {
        "summaries": [{
            "member_ids": ["mem_walk1", "mem_walk2"],
            "content": "七月初的两次晚间散步都聊到了猫，是最近固定的相处方式。",
            "significance": 3,
        }],
        "archive": [{"id": "mem_trivia", "reason": "low_significance"}],
        "reratings": [{"id": "mem_big", "significance": 5, "reason": "搬家影响后续所有安排"}],
    }


def accepted_plan(paths, proposal=None):
    memories = select_memories_for_review(paths, ConsolidationConfig())
    evaluation = evaluate_consolidation_plan(proposal or good_proposal(), memories, ConsolidationConfig())
    assert evaluation.accepted, evaluation.problems
    return persist_consolidation_plan(paths, evaluation, now=NOW)


class ScriptedLLM:
    def __init__(self, output):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


# --- config ---


def test_config_defaults_when_missing(tmp_path):
    paths = make_paths(tmp_path)
    config = load_consolidation_config(paths)
    assert config.enabled is False
    assert config.interval_days == 7
    assert config.min_new_memories == 20


def test_config_rejects_invalid_values(tmp_path):
    paths = make_paths(tmp_path)
    paths.consolidation_config_file.write_text(json.dumps({"interval_days": 0}))
    with pytest.raises(ConsolidationConfigError):
        load_consolidation_config(paths)
    paths.consolidation_config_file.write_text("not json")
    with pytest.raises(ConsolidationConfigError):
        load_consolidation_config(paths)


# --- anacron-style due logic (the not-always-on Pi) ---


def test_due_disabled_never_fires(tmp_path):
    paths = seeded_home(tmp_path)
    due = consolidation_due(paths, ConsolidationConfig(enabled=False), now=NOW)
    assert due == {"due": False, "reason": "disabled"}


def test_due_first_run_needs_enough_memories(tmp_path):
    paths = seeded_home(tmp_path)
    config = ConsolidationConfig(enabled=True, min_new_memories=3)
    assert consolidation_due(paths, config, now=NOW)["due"] is True
    strict = ConsolidationConfig(enabled=True, min_new_memories=50)
    assert consolidation_due(paths, strict, now=NOW)["due"] is False


def test_due_respects_interval(tmp_path):
    paths = seeded_home(tmp_path)
    state = load_consolidation_state(paths)
    state["last_completed_at"] = (NOW - timedelta(days=2)).isoformat()
    state["memories_at_last_run"] = 0
    save_consolidation_state(paths, state)
    config = ConsolidationConfig(enabled=True, interval_days=7, min_new_memories=1)
    due = consolidation_due(paths, config, now=NOW)
    assert due["due"] is False
    assert "2.0d ago" in due["reason"]


def test_due_catches_up_after_long_downtime(tmp_path):
    """The Pi was off for a month: the debt is overdue, not lost."""

    paths = seeded_home(tmp_path)
    state = load_consolidation_state(paths)
    state["last_completed_at"] = (NOW - timedelta(days=30)).isoformat()
    state["memories_at_last_run"] = 1
    save_consolidation_state(paths, state)
    config = ConsolidationConfig(enabled=True, interval_days=7, min_new_memories=3)
    due = consolidation_due(paths, config, now=NOW)
    assert due["due"] is True
    assert due["days_since_last"] == pytest.approx(30, abs=0.1)


def test_due_waits_for_new_memories_even_when_overdue(tmp_path):
    paths = seeded_home(tmp_path)
    state = load_consolidation_state(paths)
    state["last_completed_at"] = (NOW - timedelta(days=30)).isoformat()
    state["memories_at_last_run"] = 4
    save_consolidation_state(paths, state)
    config = ConsolidationConfig(enabled=True, interval_days=7, min_new_memories=3)
    due = consolidation_due(paths, config, now=NOW)
    assert due["due"] is False
    assert "new memories" in due["reason"]


def test_corrupt_state_file_degrades_to_first_run(tmp_path):
    paths = seeded_home(tmp_path)
    paths.consolidation_state_file.write_text("garbage{{{")
    config = ConsolidationConfig(enabled=True, min_new_memories=3)
    assert consolidation_due(paths, config, now=NOW)["due"] is True


# --- candidate selection ---


def test_select_excludes_quarantined_and_prior_summaries(tmp_path):
    paths = seeded_home(tmp_path, extra_rows=[{
        **memory_row("mem_prev_summary", "旧的摘要", created_at="2026-07-08T10:00:00"),
        "consolidation_plan_id": "conplan_old",
    }])
    ids = {m["id"] for m in select_memories_for_review(paths, ConsolidationConfig())}
    assert "mem_quarantine" not in ids
    assert "mem_prev_summary" not in ids
    assert {"mem_walk1", "mem_walk2", "mem_trivia", "mem_big"} <= ids


def test_prompt_lists_ids_and_identity(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    prompt = build_consolidation_prompt("你是摇光。", memories)
    assert "你是摇光。" in prompt
    assert "id=mem_walk1" in prompt
    assert "NO_CONSOLIDATION" in prompt


# --- parsing ---


def test_parse_no_consolidation_and_garbage(tmp_path):
    assert parse_consolidation_output("===CONSOLIDATION===\nNO_CONSOLIDATION") is None
    assert parse_consolidation_output("") is None
    assert parse_consolidation_output("===CONSOLIDATION===\n{broken json") is None
    assert parse_consolidation_output("===CONSOLIDATION===\n{\"unrelated\": 1}") is None


def test_parse_valid_section():
    raw = "想了想。\n===CONSOLIDATION===\n" + json.dumps(good_proposal(), ensure_ascii=False)
    parsed = parse_consolidation_output(raw)
    assert parsed is not None
    assert parsed["summaries"][0]["member_ids"] == ["mem_walk1", "mem_walk2"]
    assert parsed["archive"][0]["id"] == "mem_trivia"


# --- policy gates ---


def test_gates_accept_good_plan_and_auto_archive_members(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    evaluation = evaluate_consolidation_plan(good_proposal(), memories, ConsolidationConfig())
    assert evaluation.accepted
    archived_ids = {entry["id"] for entry in evaluation.archive}
    assert archived_ids == {"mem_trivia", "mem_walk1", "mem_walk2"}
    reasons = {entry["id"]: entry["reason"] for entry in evaluation.archive}
    assert reasons["mem_walk1"] == "summarized"


def test_gates_reject_single_member_summary(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    proposal = {"summaries": [{"member_ids": ["mem_walk1"], "content": "只有一条"}]}
    evaluation = evaluate_consolidation_plan(proposal, memories, ConsolidationConfig())
    assert not evaluation.accepted
    assert any("at least two" in problem for problem in evaluation.problems)


def test_gates_reject_unknown_and_quarantined_ids(tmp_path):
    paths = seeded_home(tmp_path)
    memories = JsonMemoryStore(paths.memory_store).load()
    proposal = {"summaries": [{
        "member_ids": ["mem_walk1", "mem_quarantine"],
        "content": "混入了隔离区内容",
    }]}
    evaluation = evaluate_consolidation_plan(proposal, memories, ConsolidationConfig())
    assert not evaluation.accepted
    assert any("non-consolidatable" in problem for problem in evaluation.problems)


def test_gates_reject_secret_like_summary(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    proposal = {"summaries": [{
        "member_ids": ["mem_walk1", "mem_walk2"],
        "content": "他的 api_key: sk-abcdefghijklmnop 记下来了",
    }]}
    evaluation = evaluate_consolidation_plan(proposal, memories, ConsolidationConfig())
    assert not evaluation.accepted
    assert any("secret-like" in problem for problem in evaluation.problems)


def test_gates_reject_archiving_important_unsummarized_memory(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    proposal = {"archive": [{"id": "mem_big", "reason": "low_significance"}]}
    evaluation = evaluate_consolidation_plan(proposal, memories, ConsolidationConfig())
    assert not evaluation.accepted
    assert any("neither summarized" in problem for problem in evaluation.problems)


def test_gates_allow_archiving_decay_eligible_memory(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    proposal = {"archive": [{"id": "mem_trivia", "reason": "low_significance"}]}
    evaluation = evaluate_consolidation_plan(proposal, memories, ConsolidationConfig())
    assert evaluation.accepted


def test_gates_enforce_run_caps(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    config = ConsolidationConfig(max_archive_per_run=1)
    evaluation = evaluate_consolidation_plan(good_proposal(), memories, config)
    assert not evaluation.accepted
    assert any("per-run cap" in problem for problem in evaluation.problems)


def test_gates_reject_empty_plan(tmp_path):
    paths = seeded_home(tmp_path)
    memories = select_memories_for_review(paths, ConsolidationConfig())
    evaluation = evaluate_consolidation_plan({}, memories, ConsolidationConfig())
    assert not evaluation.accepted
    assert any("no changes" in problem for problem in evaluation.problems)


# --- crash-safe apply ---


def active_context_ids(paths):
    return {m["id"] for m in JsonMemoryStore(paths.memory_store).recent_for_context(50)}


def test_apply_adds_summary_and_archives_members(tmp_path):
    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths)
    outcome = apply_consolidation_plan(paths, plan, now=NOW)
    assert outcome["applied"] is True

    memories = {m["id"]: m for m in JsonMemoryStore(paths.memory_store).load()}
    summary = next(m for m in memories.values() if m.get("consolidation_plan_id") == plan["id"])
    assert summary["authority"] == "derived_summary"
    assert summary["prompt_eligible"] is True
    assert {ref["id"] for ref in summary["evidence_refs"]} == {"mem_walk1", "mem_walk2"}
    assert memories["mem_walk1"]["status"] == "archived"
    assert memories["mem_trivia"]["status"] == "archived"
    assert memories["mem_big"]["review_history"][0]["plan_id"] == plan["id"]

    visible = active_context_ids(paths)
    assert "mem_walk1" not in visible and "mem_trivia" not in visible
    assert summary["id"] in visible and "mem_big" in visible

    state = load_consolidation_state(paths)
    assert state["last_plan_id"] == plan["id"]
    assert state["runs_completed"] == 1
    stored_plan = load_consolidation_plan(paths, plan["id"])
    assert stored_plan["applied"] is True
    assert load_consolidation_ledger(paths)[-1]["action"] == "applied"


def test_apply_is_idempotent(tmp_path):
    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths)
    apply_consolidation_plan(paths, plan, now=NOW)
    before = JsonMemoryStore(paths.memory_store).load()
    second = apply_consolidation_plan(paths, plan, now=NOW)
    assert second == {"applied": False, "already_applied": True, "plan_id": plan["id"]}
    assert JsonMemoryStore(paths.memory_store).load() == before


def test_apply_archive_only_plan_is_idempotent(tmp_path):
    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths, {"archive": [{"id": "mem_trivia", "reason": "low_significance"}]})
    assert apply_consolidation_plan(paths, plan, now=NOW)["applied"] is True
    second = apply_consolidation_plan(paths, plan, now=NOW)
    assert second["already_applied"] is True


def test_apply_rerating_only_plan_is_idempotent(tmp_path):
    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths, {"reratings": [{"id": "mem_trivia", "significance": 4, "reason": "其实重要"}]})
    assert apply_consolidation_plan(paths, plan, now=NOW)["applied"] is True
    memories = {m["id"]: m for m in JsonMemoryStore(paths.memory_store).load()}
    assert memories["mem_trivia"]["likert"]["significance"] == 4
    assert memories["mem_trivia"]["decay_eligible"] is False
    second = apply_consolidation_plan(paths, plan, now=NOW)
    assert second["already_applied"] is True
    assert len(memories["mem_trivia"]["review_history"]) == 1


def test_crash_during_apply_leaves_store_untouched_then_retry_succeeds(tmp_path, monkeypatch):
    """Power loss mid-apply: the atomic save never ran, so the old store is
    intact, and the persisted plan can simply be re-applied after boot."""

    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths)
    before = JsonMemoryStore(paths.memory_store).load()

    def power_cut(self, memories):
        raise OSError("simulated power loss")

    monkeypatch.setattr(JsonMemoryStore, "save", power_cut)
    with pytest.raises(OSError):
        apply_consolidation_plan(paths, plan, now=NOW)
    monkeypatch.undo()

    assert JsonMemoryStore(paths.memory_store).load() == before
    assert load_consolidation_ledger(paths) == []
    assert load_consolidation_state(paths)["runs_completed"] == 0

    reloaded = load_consolidation_plan(paths, plan["id"])
    assert reloaded["applied"] is False
    outcome = apply_consolidation_plan(paths, reloaded, now=NOW)
    assert outcome["applied"] is True


def test_crash_between_save_and_bookkeeping_resolves_on_retry(tmp_path):
    """Power loss in the window after the atomic store write but before the
    ledger/state updates: retrying detects the stamped plan id and no-ops,
    so no duplicate summaries can ever be created."""

    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths)
    apply_consolidation_plan(paths, plan, now=NOW)
    # Simulate the crash by erasing all post-save bookkeeping.
    paths.consolidation_state_file.unlink()
    paths.consolidation_ledger_file.unlink()

    summary_count = sum(
        1 for m in JsonMemoryStore(paths.memory_store).load()
        if m.get("consolidation_plan_id") == plan["id"]
    )
    retry = apply_consolidation_plan(paths, plan, now=NOW)
    assert retry["already_applied"] is True
    assert sum(
        1 for m in JsonMemoryStore(paths.memory_store).load()
        if m.get("consolidation_plan_id") == plan["id"]
    ) == summary_count == 1


def test_apply_refuses_stale_plan(tmp_path):
    """If a member changed between planning and applying (e.g. rolled back or
    archived by another path), the whole plan is refused, not half-applied."""

    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths)
    store = JsonMemoryStore(paths.memory_store)
    memories = store.load()
    next(m for m in memories if m["id"] == "mem_walk2")["status"] = "archived"
    store.save(memories)
    before = store.load()
    outcome = apply_consolidation_plan(paths, plan, now=NOW)
    assert outcome["applied"] is False
    assert "no longer active" in outcome["error"]
    assert store.load() == before


# --- rollback ---


def test_rollback_restores_previous_context(tmp_path):
    paths = seeded_home(tmp_path)
    visible_before = active_context_ids(paths)
    plan = accepted_plan(paths)
    apply_consolidation_plan(paths, plan, now=NOW)
    assert active_context_ids(paths) != visible_before

    outcome = rollback_consolidation_plan(paths, plan["id"], now=NOW)
    assert outcome == {
        "rolled_back": True,
        "plan_id": plan["id"],
        "summaries_retired": 1,
        "memories_restored": 3,
        "reratings_reverted": 1,
    }
    assert active_context_ids(paths) == visible_before
    assert load_consolidation_ledger(paths)[-1]["action"] == "rolled_back"


def test_rollback_reverts_rerating_values(tmp_path):
    paths = seeded_home(tmp_path)
    plan = accepted_plan(paths, {
        "reratings": [{"id": "mem_trivia", "significance": 5, "reason": "以为重要"}],
    })
    apply_consolidation_plan(paths, plan, now=NOW)
    memories = {m["id"]: m for m in JsonMemoryStore(paths.memory_store).load()}
    assert memories["mem_trivia"]["likert"]["significance"] == 5
    assert memories["mem_trivia"]["decay_eligible"] is False

    outcome = rollback_consolidation_plan(paths, plan["id"], now=NOW)
    assert outcome["rolled_back"] is True
    assert outcome["reratings_reverted"] == 1
    memories = {m["id"]: m for m in JsonMemoryStore(paths.memory_store).load()}
    assert memories["mem_trivia"]["likert"]["significance"] == 1
    assert memories["mem_trivia"]["decay_eligible"] is True


def test_rollback_unknown_plan_is_refused(tmp_path):
    paths = seeded_home(tmp_path)
    outcome = rollback_consolidation_plan(paths, "conplan_missing", now=NOW)
    assert outcome["rolled_back"] is False


# --- full pass with a scripted model ---


def scripted_output():
    return "===CONSOLIDATION===\n" + json.dumps(good_proposal(), ensure_ascii=False)


def enabled_config(**overrides):
    defaults = dict(enabled=True, interval_days=7, min_new_memories=3)
    defaults.update(overrides)
    return ConsolidationConfig(**defaults)


def test_run_once_skips_when_not_due(tmp_path):
    paths = seeded_home(tmp_path)
    llm = ScriptedLLM(scripted_output())
    outcome = run_consolidation_once(paths, llm, config=ConsolidationConfig(enabled=False))
    assert outcome["skipped"] == "disabled"
    assert llm.prompts == []


def test_run_once_plan_only_never_touches_store(tmp_path):
    paths = seeded_home(tmp_path)
    before = JsonMemoryStore(paths.memory_store).load()
    outcome = run_consolidation_once(
        paths, ScriptedLLM(scripted_output()), config=enabled_config(), apply=False, now=NOW,
    )
    assert outcome["planned"] is True and outcome["applied"] is False
    assert JsonMemoryStore(paths.memory_store).load() == before
    assert load_consolidation_plan(paths, outcome["plan_id"])["applied"] is False


def test_run_once_full_pass_applies(tmp_path):
    paths = seeded_home(tmp_path)
    outcome = run_consolidation_once(
        paths, ScriptedLLM(scripted_output()), config=enabled_config(), now=NOW,
    )
    assert outcome["applied"] is True
    assert outcome["apply_result"]["summaries_added"] == 1
    assert load_consolidation_state(paths)["runs_completed"] == 1


def test_run_once_no_op_still_resets_debt_clock(tmp_path):
    paths = seeded_home(tmp_path)
    outcome = run_consolidation_once(
        paths, ScriptedLLM("===CONSOLIDATION===\nNO_CONSOLIDATION"),
        config=enabled_config(), now=NOW,
    )
    assert outcome["skipped"] == "model proposed no consolidation"
    state = load_consolidation_state(paths)
    assert state["last_completed_at"] == NOW.isoformat()
    assert load_consolidation_ledger(paths)[-1]["action"] == "no_op"
    due = consolidation_due(paths, enabled_config(), now=NOW + timedelta(days=1))
    assert due["due"] is False


def test_run_once_hostile_plan_rejected_without_mutation(tmp_path):
    paths = seeded_home(tmp_path)
    hostile = {
        "summaries": [{"member_ids": ["mem_walk1", "mem_ghost"], "content": "引用了不存在的记忆"}],
        "archive": [{"id": "mem_big", "reason": "superseded"}],
    }
    before = JsonMemoryStore(paths.memory_store).load()
    outcome = run_consolidation_once(
        paths,
        ScriptedLLM("===CONSOLIDATION===\n" + json.dumps(hostile, ensure_ascii=False)),
        config=enabled_config(),
        now=NOW,
    )
    assert outcome["skipped"] == "plan rejected by policy gates"
    assert outcome["policy_problems"]
    assert JsonMemoryStore(paths.memory_store).load() == before
    assert load_consolidation_ledger(paths)[-1]["action"] == "rejected"


def test_run_once_ignore_due_forces_review(tmp_path):
    paths = seeded_home(tmp_path)
    state = load_consolidation_state(paths)
    state["last_completed_at"] = NOW.isoformat()
    state["memories_at_last_run"] = 4
    save_consolidation_state(paths, state)
    outcome = run_consolidation_once(
        paths, ScriptedLLM(scripted_output()),
        config=enabled_config(), ignore_due=True, now=NOW,
    )
    assert outcome["applied"] is True
