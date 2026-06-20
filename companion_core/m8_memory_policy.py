"""M8 policy gate and append-only memory decision ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .dialogue import DIALOGUE_BOUNDARIES
from .m8_memory_schema import (
    MemoryDecision,
    MemoryDecisionValidationError,
    append_memory_decisions,
    load_memory_decisions,
    validate_memory_decision,
)
from .memory import JsonMemoryStore, MemoryEntry
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_memory_policy_ledger_ready"
STEWARD_READY_RECOMMENDATION = "m8_memory_steward_readonly_ready"
PROMPT_MEMORY_TYPES = {"semantic", "procedural"}


@dataclass
class M8MemoryPolicyLedgerResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m8_memory_policy_ledger(
    paths: CompanionPaths,
    *,
    decisions: Iterable[MemoryDecision | dict] | None = None,
    steward_report_path: str | Path | None = None,
    write_ledger: bool = True,
    write_accepted_memory: bool = True,
) -> M8MemoryPolicyLedgerResult:
    """Validate steward decisions, append the ledger, and accept low-risk memory."""

    saved_at = datetime.now()
    stages: list[dict] = []
    errors: list[str] = []
    raw_decisions, source_stage, source_path = _load_source_decisions(
        paths,
        decisions=decisions,
        steward_report_path=steward_report_path,
    )
    stages.append(source_stage)

    existing_decisions, ledger_stage = _load_existing_ledger(paths)
    stages.append(ledger_stage)
    existing_decision_ids = {decision.id for decision in existing_decisions}

    memory_store = JsonMemoryStore(paths.memory_store)
    accepted_memories, accepted_memory_stage = _load_accepted_memory(memory_store)
    stages.append(accepted_memory_stage)

    validation_errors: list[str] = []
    policy_errors: list[str] = []
    duplicate_actions: list[dict] = []
    gated_decisions: list[MemoryDecision] = []
    for raw_decision in raw_decisions:
        try:
            decision = validate_memory_decision(raw_decision)
        except MemoryDecisionValidationError as exc:
            validation_errors.append(str(exc))
            continue
        if decision.id in existing_decision_ids:
            duplicate_actions.append({
                "id": decision.id,
                "action": "skipped_existing_ledger_decision",
                "decision": decision.decision,
                "risk": decision.risk,
            })
            continue
        policy_error = _policy_error(decision)
        if policy_error:
            policy_errors.append(f"{decision.id}: {policy_error}")
            continue
        gated_decisions.append(decision)

    errors.extend(validation_errors)
    errors.extend(policy_errors)
    gate_ok = (
        source_stage["status"] == "pass"
        and ledger_stage["status"] == "pass"
        and accepted_memory_stage["status"] == "pass"
        and not validation_errors
        and not policy_errors
    )
    stages.append(_stage(
        "memory_policy_gate",
        gate_ok,
        (
            f"{len(gated_decisions)} new decision(s) passed policy"
            if gate_ok
            else "; ".join(errors)
        ),
        details={
            "validated_new_decisions": len(gated_decisions),
            "skipped_existing_decisions": len(duplicate_actions),
            "validation_errors": len(validation_errors),
            "policy_errors": len(policy_errors),
        },
    ))

    ledger_records: list[MemoryDecision] = []
    actions = list(duplicate_actions)
    accepted_memory_writes = 0
    accepted_memory_existing = 0
    if gate_ok:
        for decision in gated_decisions:
            accepted_memory_id = None
            action = "ledger_only"
            if _should_write_accepted_memory(decision):
                existing_memory = _find_memory_for_decision(accepted_memories, decision)
                if existing_memory:
                    accepted_memory_id = str(existing_memory.get("id"))
                    accepted_memory_existing += 1
                    action = "accepted_memory_existing"
                elif write_accepted_memory:
                    memory = _store_accepted_memory(memory_store, decision)
                    accepted_memories.append(memory)
                    accepted_memory_id = memory["id"]
                    accepted_memory_writes += 1
                    action = "accepted_memory_written"
                else:
                    action = "accepted_memory_dry_run"
            ledger_decision = (
                replace(decision, accepted_memory_id=accepted_memory_id)
                if accepted_memory_id
                else decision
            )
            ledger_records.append(ledger_decision)
            actions.append({
                "id": decision.id,
                "action": action,
                "decision": decision.decision,
                "risk": decision.risk,
                "prompt_eligible": decision.prompt_eligible,
                "accepted_memory_id": accepted_memory_id,
            })

    ledger_appended = 0
    if gate_ok and write_ledger and ledger_records:
        append_memory_decisions(paths.memory_decisions_file, ledger_records)
        ledger_appended = len(ledger_records)
    stages.append(_stage(
        "memory_decision_ledger",
        gate_ok,
        (
            f"appended {ledger_appended} decision(s) to memory_decisions.jsonl"
            if write_ledger
            else "dry run: memory_decisions.jsonl not written"
        ),
        details={
            "ledger_records_ready": len(ledger_records),
            "ledger_appended": ledger_appended,
            "write_ledger": write_ledger,
        },
    ))
    stages.append(_stage(
        "accepted_memory_write",
        gate_ok,
        f"wrote {accepted_memory_writes} accepted memory row(s)",
        details={
            "accepted_memory_writes": accepted_memory_writes,
            "accepted_memory_existing": accepted_memory_existing,
            "write_accepted_memory": write_accepted_memory,
        },
    ))
    stages.append(_stage(
        "policy_boundaries",
        True,
        "policy gate does not call providers, wake, scheduler, /life routes, or semantic shadow",
        details={
            "provider_calls": 0,
            "wake_events_written": False,
            "scheduler_mutated": False,
            "raw_provider_payload_stored": False,
            "semantic_shadow_authority_promoted": False,
        },
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M8.3",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": {
            "policy_gate": True,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "writes_memory_decisions": bool(write_ledger and ledger_appended),
            "writes_accepted_memory": bool(write_accepted_memory and accepted_memory_writes),
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
        },
        "counts": {
            "source_decisions": len(raw_decisions),
            "existing_ledger_decisions": len(existing_decisions),
            "new_ledger_decisions": len(ledger_records),
            "ledger_appended": ledger_appended,
            "accepted_memory_written": accepted_memory_writes,
            "accepted_memory_existing": accepted_memory_existing,
            "skipped_existing_decisions": len(duplicate_actions),
            "quarantined_or_review_only": sum(
                1 for decision in ledger_records
                if decision.decision in {"quarantined", "human_review_required", "audit_only", "rejected"}
            ),
        },
        "source_files": {
            "steward_report": _relative(paths, source_path) if source_path else None,
            "memory_decisions": _relative(paths, paths.memory_decisions_file),
            "accepted_memory": _relative(paths, paths.memory_store),
        },
        "actions": actions,
        "ledger_records": [decision.to_dict() for decision in ledger_records],
        "boundaries": {
            **DIALOGUE_BOUNDARIES,
            "provider_generation_requested": False,
            "memory_decisions_written": bool(write_ledger and ledger_appended),
            "accepted_memory_written": bool(write_accepted_memory and accepted_memory_writes),
        },
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m8_memory_policy": "python3 scripts/run_m8_memory_policy_ledger.py --companion-home "
            + str(paths.home),
        },
    }
    return M8MemoryPolicyLedgerResult(
        ok=ok,
        recommendation=recommendation,
        report=report,
        errors=errors,
    )


def write_m8_memory_policy_ledger_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_memory_policy_ledger_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _load_source_decisions(
    paths: CompanionPaths,
    *,
    decisions: Iterable[MemoryDecision | dict] | None,
    steward_report_path: str | Path | None,
) -> tuple[list[MemoryDecision | dict], dict, Path | None]:
    if decisions is not None:
        records = list(decisions)
        return records, _stage(
            "source_decisions",
            True,
            f"loaded {len(records)} provided decision(s)",
        ), None

    path = Path(steward_report_path).expanduser() if steward_report_path else paths.life_loop_dir / "m8_memory_steward_report.json"
    if not path.is_absolute():
        path = paths.home / path
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return [], _stage("source_decisions", False, f"missing steward report: {_relative(paths, path)}"), path
    except json.JSONDecodeError as exc:
        return [], _stage("source_decisions", False, f"invalid steward report JSON: {exc.msg}"), path
    decisions_payload = payload.get("decisions") if isinstance(payload, dict) else None
    ok = (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("recommendation") == STEWARD_READY_RECOMMENDATION
        and isinstance(decisions_payload, list)
    )
    return list(decisions_payload or []), _stage(
        "source_decisions",
        ok,
        (
            f"loaded {len(decisions_payload or [])} steward decision(s)"
            if ok
            else "steward report is missing ready status or decisions list"
        ),
        details={"steward_report": _relative(paths, path)},
    ), path


def _load_existing_ledger(paths: CompanionPaths) -> tuple[list[MemoryDecision], dict]:
    try:
        decisions = load_memory_decisions(paths.memory_decisions_file)
    except MemoryDecisionValidationError as exc:
        return [], _stage("existing_ledger", False, str(exc))
    return decisions, _stage(
        "existing_ledger",
        True,
        f"loaded {len(decisions)} existing ledger decision(s)",
    )


def _load_accepted_memory(memory_store: JsonMemoryStore) -> tuple[list[dict], dict]:
    try:
        memories = memory_store.load()
    except ValueError as exc:
        return [], _stage("accepted_memory", False, str(exc))
    return memories, _stage(
        "accepted_memory",
        True,
        f"loaded {len(memories)} accepted memory row(s)",
    )


def _policy_error(decision: MemoryDecision) -> str | None:
    if decision.decision == "accepted" and decision.prompt_eligible:
        if decision.risk != "low":
            return "M8.3 auto-accepted prompt memory must be low risk"
        if decision.authority == "model_proposed":
            return "model_proposed memory cannot be accepted into prompt context"
        if decision.memory_type not in PROMPT_MEMORY_TYPES:
            return "accepted prompt memory must be semantic or procedural"
    if decision.risk in {"sensitive", "conflict", "relationship"} and decision.prompt_eligible:
        return f"{decision.risk} memory cannot be prompt eligible"
    return None


def _should_write_accepted_memory(decision: MemoryDecision) -> bool:
    return (
        decision.decision == "accepted"
        and decision.prompt_eligible
        and decision.risk == "low"
        and decision.memory_type in PROMPT_MEMORY_TYPES
    )


def _store_accepted_memory(memory_store: JsonMemoryStore, decision: MemoryDecision) -> dict:
    entry = MemoryEntry(
        content=decision.candidate_content,
        source="human",
        context=["m8_memory_policy", decision.conversation_id],
        intensity=2,
        valence=3,
        significance=3,
        memory_type=decision.memory_type,
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=_accepted_memory_evidence(decision),
    )
    memory = memory_store.store(entry, accepted_for_context=True)
    return _annotate_memory_with_decision(memory_store, memory["id"], decision.id) or memory


def _accepted_memory_evidence(decision: MemoryDecision) -> list[dict]:
    evidence = [dict(ref) for ref in decision.evidence_refs]
    evidence.append({"artifact": "memory_decision", "id": decision.id})
    return evidence


def _annotate_memory_with_decision(
    memory_store: JsonMemoryStore,
    memory_id: str,
    decision_id: str,
) -> dict | None:
    with memory_store.write_lock():
        memories = memory_store.load()
        updated = None
        for memory in memories:
            if memory.get("id") != memory_id:
                continue
            memory["memory_decision_id"] = decision_id
            schema_refs = list(memory.get("schema_refs") or [])
            decision_ref = {"artifact": "memory_decision", "id": decision_id}
            if decision_ref not in schema_refs:
                schema_refs.append(decision_ref)
            memory["schema_refs"] = schema_refs
            updated = dict(memory)
            break
        memory_store.save(memories)
    return updated


def _find_memory_for_decision(memories: list[dict], decision: MemoryDecision) -> dict | None:
    for memory in memories:
        if memory.get("memory_decision_id") == decision.id:
            return memory
        refs = list(memory.get("evidence_refs") or []) + list(memory.get("schema_refs") or [])
        for ref in refs:
            if isinstance(ref, dict) and ref.get("artifact") == "memory_decision" and ref.get("id") == decision.id:
                return memory
    return None


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {
        "name": name,
        "status": "pass" if ok else "fail",
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
