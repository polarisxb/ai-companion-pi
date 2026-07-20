"""M15.2 sleep consolidation dry-run gate.

Runs the full consolidation engine inside an isolated smoke home with a
scripted model and proves the milestone's core contracts as evidence:
blackout interruption drills (power loss before the atomic save and in the
bookkeeping window after it), idempotent re-application, whole-plan
rollback, hostile-plan policy rejection, and anacron-style catch-up debt.
No provider call, no real memory store, no scheduler mutation.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .consolidation import (
    ConsolidationConfig,
    apply_consolidation_plan,
    consolidation_due,
    evaluate_consolidation_plan,
    load_consolidation_ledger,
    load_consolidation_plan,
    load_consolidation_state,
    persist_consolidation_plan,
    rollback_consolidation_plan,
    run_consolidation_once,
    save_consolidation_state,
    select_memories_for_review,
)
from .memory import JsonMemoryStore
from .paths import CompanionPaths

READY_RECOMMENDATION = "m15_consolidation_dry_run_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]
DRY_RUN_NOW = datetime(2026, 7, 20, 3, 0, 0)


class _ScriptedLLM:
    def __init__(self, output: str):
        self.output = output
        self.calls = 0

    def generate(self, prompt, context):
        self.calls += 1
        return self.output


@dataclass
class M15ConsolidationDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m15_consolidation_dry_run(paths: CompanionPaths) -> M15ConsolidationDryRunResult:
    saved_at = datetime.now()
    stages = [
        _crash_before_save_stage(),
        _crash_after_save_stage(),
        _idempotency_stage(),
        _rollback_stage(),
        _stale_plan_stage(),
        _policy_gate_stage(),
        _catch_up_debt_stage(),
        _scripted_full_pass_stage(),
        _config_template_stage(),
        _static_guard_stage(),
    ]

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M15.2",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "provider": "scripted",
            "provider_calls": 0,
            "memory_store": "isolated smoke homes only",
        },
        "stages": stages,
        "boundaries": {
            "real_memory_store_mutated": False,
            "provider_generation_requested": False,
            "memories_deleted": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
            "chat_transport_used": False,
        },
        "provider_calls": 0,
        "errors": errors,
        "stop_reasons": stop_reasons,
        "next_commands": [
            (
                f".venv/bin/python scripts/run_m15_consolidation.py --companion-home {paths.home} "
                "--plan-only --provider deepseek"
            ),
        ],
    }
    return M15ConsolidationDryRunResult(
        ok=ok,
        recommendation=report["recommendation"],
        report=report,
        errors=errors,
    )


def write_m15_consolidation_dry_run_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file
        else paths.life_loop_dir / "m15_consolidation_dry_run_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _smoke_home(smoke_dir: str) -> CompanionPaths:
    smoke_paths = CompanionPaths(Path(smoke_dir))
    smoke_paths.ensure_runtime_dirs()
    rows = [
        _memory_row("mem_walk1", "周二晚上一起散步聊了猫", created_at="2026-07-01T10:00:00"),
        _memory_row("mem_walk2", "周四又散步，聊的还是猫", created_at="2026-07-03T10:00:00"),
        _memory_row("mem_trivia", "那天喝了冰美式", significance=1, created_at="2026-07-05T10:00:00"),
        _memory_row("mem_big", "他说下个月要搬家", significance=5, created_at="2026-07-06T10:00:00"),
        _memory_row("mem_quarantine", "未审核的提案", eligible=False, created_at="2026-07-07T10:00:00"),
    ]
    JsonMemoryStore(smoke_paths.memory_store).save(rows)
    return smoke_paths


def _memory_row(memory_id, content, *, eligible=True, significance=3, created_at="2026-07-01T10:00:00"):
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


def _good_proposal() -> dict:
    return {
        "summaries": [{
            "member_ids": ["mem_walk1", "mem_walk2"],
            "content": "七月初的两次晚间散步都聊到了猫。",
            "significance": 3,
        }],
        "archive": [{"id": "mem_trivia", "reason": "low_significance"}],
        "reratings": [{"id": "mem_big", "significance": 5, "reason": "搬家影响所有安排"}],
    }


def _accepted_plan(smoke_paths: CompanionPaths, proposal: dict | None = None) -> dict:
    memories = select_memories_for_review(smoke_paths, ConsolidationConfig())
    evaluation = evaluate_consolidation_plan(
        proposal or _good_proposal(), memories, ConsolidationConfig()
    )
    if not evaluation.accepted:
        raise AssertionError(f"fixture plan rejected: {evaluation.problems}")
    return persist_consolidation_plan(smoke_paths, evaluation, now=DRY_RUN_NOW)


def _active_context_ids(smoke_paths: CompanionPaths) -> set:
    return {m["id"] for m in JsonMemoryStore(smoke_paths.memory_store).recent_for_context(50)}


def _crash_before_save_stage() -> dict:
    """Power loss during apply, before the atomic replace: old store intact,
    plan file survives, retry after 'boot' completes the consolidation."""

    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-crash1-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        plan = _accepted_plan(smoke_paths)
        before = JsonMemoryStore(smoke_paths.memory_store).load()

        original_save = JsonMemoryStore.save

        def power_cut(self, memories):
            raise OSError("simulated power loss")

        JsonMemoryStore.save = power_cut
        try:
            apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
            problems.append("apply must fail when the save is interrupted")
        except OSError:
            pass
        finally:
            JsonMemoryStore.save = original_save

        if JsonMemoryStore(smoke_paths.memory_store).load() != before:
            problems.append("interrupted apply mutated the store")
        if load_consolidation_ledger(smoke_paths):
            problems.append("interrupted apply left ledger records")
        if load_consolidation_state(smoke_paths)["runs_completed"] != 0:
            problems.append("interrupted apply advanced the debt clock")

        reloaded = load_consolidation_plan(smoke_paths, plan["id"])
        if reloaded is None or reloaded.get("applied"):
            problems.append("plan file must survive the crash unapplied")
        else:
            retry = apply_consolidation_plan(smoke_paths, reloaded, now=DRY_RUN_NOW)
            if not retry.get("applied"):
                problems.append(f"post-boot retry failed: {retry}")

    if problems:
        return _stage("crash_before_save", False, "; ".join(problems))
    return _stage(
        "crash_before_save",
        True,
        "power loss before the atomic save leaves the store byte-identical; the persisted plan re-applies cleanly after boot",
    )


def _crash_after_save_stage() -> dict:
    """Power loss in the window between the atomic store write and the
    bookkeeping writes: retry detects the stamped plan id and no-ops."""

    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-crash2-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        plan = _accepted_plan(smoke_paths)
        apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
        smoke_paths.consolidation_state_file.unlink()
        smoke_paths.consolidation_ledger_file.unlink()

        retry = apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
        if retry != {"applied": False, "already_applied": True, "plan_id": plan["id"]}:
            problems.append(f"retry after bookkeeping loss must be a no-op, got {retry}")
        summary_count = sum(
            1 for m in JsonMemoryStore(smoke_paths.memory_store).load()
            if m.get("consolidation_plan_id") == plan["id"]
        )
        if summary_count != 1:
            problems.append(f"duplicate summaries after retry: {summary_count}")

    if problems:
        return _stage("crash_after_save", False, "; ".join(problems))
    return _stage(
        "crash_after_save",
        True,
        "power loss after the store write but before bookkeeping resolves on retry with zero duplicate summaries",
    )


def _idempotency_stage() -> dict:
    problems = []
    for proposal in (
        _good_proposal(),
        {"archive": [{"id": "mem_trivia", "reason": "low_significance"}]},
        {"reratings": [{"id": "mem_trivia", "significance": 4, "reason": "其实重要"}]},
    ):
        with tempfile.TemporaryDirectory(prefix="m15-idem-") as sub_dir:
            sub_paths = _smoke_home(sub_dir)
            plan = _accepted_plan(sub_paths, proposal)
            first = apply_consolidation_plan(sub_paths, plan, now=DRY_RUN_NOW)
            snapshot = JsonMemoryStore(sub_paths.memory_store).load()
            second = apply_consolidation_plan(sub_paths, plan, now=DRY_RUN_NOW)
            if not first.get("applied") or not second.get("already_applied"):
                problems.append(f"plan kind {sorted(proposal)} is not idempotent")
            if JsonMemoryStore(sub_paths.memory_store).load() != snapshot:
                problems.append(f"re-apply mutated the store for plan kind {sorted(proposal)}")

    if problems:
        return _stage("idempotent_apply", False, "; ".join(problems))
    return _stage(
        "idempotent_apply",
        True,
        "summary, archive-only, and rerating-only plans are all no-ops on re-application",
    )


def _rollback_stage() -> dict:
    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-rollback-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        visible_before = _active_context_ids(smoke_paths)
        plan = _accepted_plan(smoke_paths)
        apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
        if _active_context_ids(smoke_paths) == visible_before:
            problems.append("apply did not change the visible context")
        outcome = rollback_consolidation_plan(smoke_paths, plan["id"], now=DRY_RUN_NOW)
        if not outcome.get("rolled_back"):
            problems.append(f"rollback refused: {outcome}")
        if _active_context_ids(smoke_paths) != visible_before:
            problems.append("rollback did not restore the pre-consolidation context")
        retry = apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
        if not retry.get("already_applied"):
            problems.append("a rolled-back plan must stay terminal, not re-apply")
        missing = rollback_consolidation_plan(smoke_paths, "conplan_ghost", now=DRY_RUN_NOW)
        if missing.get("rolled_back"):
            problems.append("rolling back an unknown plan must be refused")

    if problems:
        return _stage("whole_plan_rollback", False, "; ".join(problems))
    return _stage(
        "whole_plan_rollback",
        True,
        "one command restores archived members and retires summaries; rolled-back plans stay terminal",
    )


def _stale_plan_stage() -> dict:
    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-stale-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        plan = _accepted_plan(smoke_paths)
        store = JsonMemoryStore(smoke_paths.memory_store)
        memories = store.load()
        next(m for m in memories if m["id"] == "mem_walk2")["status"] = "archived"
        store.save(memories)
        before = store.load()
        outcome = apply_consolidation_plan(smoke_paths, plan, now=DRY_RUN_NOW)
        if outcome.get("applied") or "no longer active" not in str(outcome.get("error", "")):
            problems.append(f"stale plan must be refused whole, got {outcome}")
        if store.load() != before:
            problems.append("refused stale plan still mutated the store")

    if problems:
        return _stage("stale_plan_refusal", False, "; ".join(problems))
    return _stage(
        "stale_plan_refusal",
        True,
        "plans referencing memories that changed since planning are refused whole — never half-applied",
    )


def _policy_gate_stage() -> dict:
    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-policy-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        memories = JsonMemoryStore(smoke_paths.memory_store).load()
        config = ConsolidationConfig()
        hostile_cases = {
            "unknown_member": {"summaries": [{"member_ids": ["mem_walk1", "mem_ghost"], "content": "引用幽灵记忆"}]},
            "single_member": {"summaries": [{"member_ids": ["mem_walk1"], "content": "只合并一条"}]},
            "quarantined_member": {"summaries": [{"member_ids": ["mem_walk1", "mem_quarantine"], "content": "混入隔离内容"}]},
            "secret_content": {"summaries": [{
                "member_ids": ["mem_walk1", "mem_walk2"],
                "content": "记下了 api_key: sk-abcdefghijklmnop",
            }]},
            "archive_important": {"archive": [{"id": "mem_big", "reason": "superseded"}]},
            "empty_plan": {},
            "over_cap": {"archive": [{"id": "mem_trivia", "reason": "low_significance"}] * 25},
        }
        for name, proposal in hostile_cases.items():
            evaluation = evaluate_consolidation_plan(proposal, memories, config)
            if evaluation.accepted:
                problems.append(f"hostile case accepted: {name}")
        good = evaluate_consolidation_plan(_good_proposal(), memories, config)
        if not good.accepted:
            problems.append(f"good plan rejected: {good.problems}")

    if problems:
        return _stage("policy_gates", False, "; ".join(problems))
    return _stage(
        "policy_gates",
        True,
        f"all {len(hostile_cases)} hostile plans rejected (unknown/single/quarantined/secret/important/empty/cap); the good plan passes",
    )


def _catch_up_debt_stage() -> dict:
    problems = []
    with tempfile.TemporaryDirectory(prefix="m15-debt-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        config = ConsolidationConfig(enabled=True, interval_days=7, min_new_memories=3)

        if consolidation_due(smoke_paths, ConsolidationConfig(enabled=False), now=DRY_RUN_NOW)["due"]:
            problems.append("disabled config must never be due")
        if not consolidation_due(smoke_paths, config, now=DRY_RUN_NOW)["due"]:
            problems.append("first run with enough memories must be due")

        state = load_consolidation_state(smoke_paths)
        state["last_completed_at"] = (DRY_RUN_NOW - timedelta(days=45)).isoformat()
        state["memories_at_last_run"] = 1
        save_consolidation_state(smoke_paths, state)
        overdue = consolidation_due(smoke_paths, config, now=DRY_RUN_NOW)
        if not overdue["due"] or overdue.get("days_since_last", 0) < 44:
            problems.append(f"45-day downtime must surface as overdue debt, got {overdue}")

        state["last_completed_at"] = (DRY_RUN_NOW - timedelta(days=2)).isoformat()
        save_consolidation_state(smoke_paths, state)
        if consolidation_due(smoke_paths, config, now=DRY_RUN_NOW)["due"]:
            problems.append("a recent run must defer the next consolidation")

        smoke_paths.consolidation_state_file.write_text("corrupted{{{")
        if not consolidation_due(smoke_paths, config, now=DRY_RUN_NOW)["due"]:
            problems.append("corrupt state must degrade to first-run debt, not silence")

    if problems:
        return _stage("catch_up_debt", False, "; ".join(problems))
    return _stage(
        "catch_up_debt",
        True,
        "anacron-style debt: 45 days of downtime surfaces as overdue on the next check; corrupt state degrades safely",
    )


def _scripted_full_pass_stage() -> dict:
    problems = []
    config = ConsolidationConfig(enabled=True, interval_days=7, min_new_memories=3)
    with tempfile.TemporaryDirectory(prefix="m15-full-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        scripted = _ScriptedLLM(
            "===CONSOLIDATION===\n" + json.dumps(_good_proposal(), ensure_ascii=False)
        )
        outcome = run_consolidation_once(
            smoke_paths, scripted, config=config, now=DRY_RUN_NOW,
        )
        if not outcome.get("applied"):
            problems.append(f"scripted full pass did not apply: {outcome}")
        visible = _active_context_ids(smoke_paths)
        if "mem_walk1" in visible or "mem_trivia" in visible:
            problems.append("archived members still visible in context")
        if not any(mid.startswith("mem_") and mid not in
                   {"mem_walk1", "mem_walk2", "mem_trivia", "mem_big", "mem_quarantine"}
                   for mid in visible):
            problems.append("derived summary is not visible in context")

    with tempfile.TemporaryDirectory(prefix="m15-noop-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        outcome = run_consolidation_once(
            smoke_paths,
            _ScriptedLLM("===CONSOLIDATION===\nNO_CONSOLIDATION"),
            config=config,
            now=DRY_RUN_NOW,
        )
        if outcome.get("skipped") != "model proposed no consolidation":
            problems.append(f"NO_CONSOLIDATION path misbehaved: {outcome}")
        if load_consolidation_state(smoke_paths)["last_completed_at"] != DRY_RUN_NOW.isoformat():
            problems.append("a no-op review must still reset the debt clock")

    with tempfile.TemporaryDirectory(prefix="m15-hostile-") as smoke_dir:
        smoke_paths = _smoke_home(smoke_dir)
        before = JsonMemoryStore(smoke_paths.memory_store).load()
        hostile = {"summaries": [{"member_ids": ["mem_walk1", "mem_ghost"], "content": "幽灵"}]}
        outcome = run_consolidation_once(
            smoke_paths,
            _ScriptedLLM("===CONSOLIDATION===\n" + json.dumps(hostile, ensure_ascii=False)),
            config=config,
            now=DRY_RUN_NOW,
        )
        if outcome.get("skipped") != "plan rejected by policy gates":
            problems.append(f"hostile full pass was not rejected: {outcome}")
        if JsonMemoryStore(smoke_paths.memory_store).load() != before:
            problems.append("rejected hostile pass mutated the store")

    if problems:
        return _stage("scripted_full_pass", False, "; ".join(problems))
    return _stage(
        "scripted_full_pass",
        True,
        "end-to-end pass applies a good plan, resets the clock on NO_CONSOLIDATION, and rejects hostile output without mutation",
    )


def _config_template_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "consolidation_config.template.json"
    if not template_path.exists():
        return _stage("config_template", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("config_template", False, f"template is invalid JSON: {exc.msg}")
    if payload.get("enabled") is not False:
        return _stage("config_template", False, "template must ship disabled")
    required = {"interval_days", "min_new_memories", "max_summaries_per_run", "max_archive_per_run"}
    missing = sorted(required - set(payload))
    if missing:
        return _stage("config_template", False, f"template missing keys: {missing}")
    return _stage("config_template", True, "consolidation config template ships disabled with all cadence and cap keys")


def _static_guard_stage() -> dict:
    problems = []
    source = (Path(__file__).resolve().parent / "consolidation.py").read_text()
    for forbidden in ("crontab", "systemctl", "urllib", "requests.", "socket"):
        if forbidden in source:
            problems.append(f"consolidation.py must not reference {forbidden}")
    for forbidden in ("memories.remove(", "del memories[", ".pop(0)"):
        if forbidden in source:
            problems.append(f"consolidation.py must archive, never delete: found {forbidden}")
    if "status\"] = \"archived\"" not in source.replace("'", '"'):
        problems.append("archive path must set status=archived")
    if problems:
        return _stage("static_guard", False, "; ".join(problems))
    return _stage(
        "static_guard",
        True,
        "engine stays network-free and scheduler-free, and can only archive — deletion paths do not exist",
    )


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}
