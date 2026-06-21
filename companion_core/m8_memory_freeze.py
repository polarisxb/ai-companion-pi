"""M8 memory and dialogue final freeze gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES
from .m8_memory_schema import MEMORY_DECISION_SCHEMA, MemoryDecisionValidationError, load_memory_decisions
from .m8_memory_review import MemoryReviewError, load_memory_review_actions
from .memory import JsonMemoryStore, PROMPT_AUTHORITIES, PROMPT_MEMORY_TYPES
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_memory_dialogue_frozen"
M7_READY_RECOMMENDATION = "m7_text_dialogue_frozen"
M8_REPORTS = {
    "m8_memory_steward": ("m8_memory_steward_report.json", "m8_memory_steward_readonly_ready"),
    "m8_memory_policy": ("m8_memory_policy_ledger_report.json", "m8_memory_policy_ledger_ready"),
    "m8_memory_retrieval": ("m8_memory_retrieval_report.json", "m8_memory_retrieval_ready"),
    "m8_dialogue_humanity": ("m8_dialogue_humanity_report.json", "m8_dialogue_humanity_ready"),
    "m8_human_review_queue": ("m8_human_review_queue_report.json", "m8_human_review_queue_ready"),
}
PROMPT_BLOCKED_DECISIONS = {
    "quarantined",
    "rejected",
    "audit_only",
    "human_review_required",
    "merge_proposed",
    "update_proposed",
}
SCHEDULER_MUTATION_RE = re.compile(
    r"\b(crontab\s+-|systemctl\s+(?:enable|start|restart|stop|disable)|"
    r"timer_installation\s*[:=]\s*true|scheduler_mutat(?:ed|ion_allowed|ion_attempted)\s*[:=]\s*true)\b",
    re.IGNORECASE,
)
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class M8MemoryFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m8_memory_freeze_check(paths: CompanionPaths) -> M8MemoryFreezeResult:
    """Freeze M8 memory stewardship and dialogue hardening by inspection only."""

    saved_at = datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    schema_stage = _schema_stage()
    stages.append(schema_stage)

    m7_path = paths.life_loop_dir / "m7_dialogue_freeze_report.json"
    m7_report = _load_report(m7_path)
    source_reports["m7_dialogue_freeze"] = _report_snapshot(paths, m7_path, m7_report)
    stages.append(_report_stage(
        "m7_dialogue_freeze",
        m7_report,
        M7_READY_RECOMMENDATION,
        "M7.6 dialogue freeze remains ready",
    ))

    for name, (filename, recommendation) in M8_REPORTS.items():
        path = paths.life_loop_dir / filename
        report = _load_report(path)
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_report_stage(
            name,
            report,
            recommendation,
            f"{name} evidence is ready",
        ))

    memory_stage, memory_evidence = _accepted_memory_stage(paths)
    stages.append(memory_stage)
    decision_stage, decision_evidence = _decision_prompt_boundary_stage(paths, memory_evidence["prompt_decision_refs"])
    stages.append(decision_stage)
    review_stage, review_evidence = _review_audit_stage(paths, memory_evidence["memories_by_id"])
    stages.append(review_stage)
    stages.append(_retrieval_boundary_stage(source_reports.get("m8_memory_retrieval", {}), _load_report(paths.life_loop_dir / "m8_memory_retrieval_report.json")))
    stages.append(_static_boundary_stage(paths))
    stages.append(_readonly_profile_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M8.7",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": _readonly_profile(),
        "source_reports": source_reports,
        "evidence": {
            "m8_1_schema_ready": schema_stage.get("status") == "pass",
            "m7_6_dialogue_freeze_ready": _stage_ok(stages, "m7_dialogue_freeze"),
            "m8_2_steward_ready": _stage_ok(stages, "m8_memory_steward"),
            "m8_3_policy_ready": _stage_ok(stages, "m8_memory_policy"),
            "m8_4_retrieval_ready": _stage_ok(stages, "m8_memory_retrieval"),
            "m8_5_dialogue_humanity_ready": _stage_ok(stages, "m8_dialogue_humanity"),
            "m8_6_human_review_ready": _stage_ok(stages, "m8_human_review_queue"),
            "accepted_memory_authority_ready": memory_stage.get("status") == "pass",
            "nonaccepted_memory_prompt_blocked": decision_stage.get("status") == "pass",
            "human_review_auditable": review_stage.get("status") == "pass",
        },
        "memory": _public_memory_evidence(memory_evidence),
        "memory_decisions": decision_evidence,
        "memory_review": review_evidence,
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "memory_stewardship_ready": ok,
            "dialogue_humanity_ready": ok,
            "next_stage": "post_m8_channel_or_scheduler_planning" if ok else "M8",
        },
        "boundaries": {
            **DIALOGUE_BOUNDARIES,
            "provider_generation_requested": False,
            "scheduler_mutated": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
        },
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m8_memory_freeze": "python3 scripts/run_m8_memory_freeze.py --companion-home "
            + str(paths.home),
        },
    }
    return M8MemoryFreezeResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m8_memory_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_memory_freeze_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _schema_stage() -> dict:
    required = {"id", "conversation_id", "source_turn_ids", "candidate_content", "decision", "risk", "reason", "evidence_refs"}
    schema_required = set(MEMORY_DECISION_SCHEMA.get("required", []))
    ok = required.issubset(schema_required)
    return _stage(
        "m8_memory_decision_schema",
        ok,
        "M8.1 memory decision schema exposes required fields" if ok else "M8.1 schema is missing required fields",
        details={"required": sorted(schema_required)},
    )


def _report_stage(name: str, report: dict | None, recommendation: str, success_message: str) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append(f"{name} report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append(f"{name} ok is not true")
        if report.get("recommendation") != recommendation:
            problems.append(f"{name} recommendation is not {recommendation}")
        if report.get("provider_calls", 0) not in (0, None):
            problems.append(f"{name} reported provider calls")
    return _stage(name, not problems, success_message if not problems else "; ".join(problems))


def _accepted_memory_stage(paths: CompanionPaths) -> tuple[dict, dict]:
    memory_store = JsonMemoryStore(paths.memory_store)
    try:
        memories = memory_store.load()
        load_error = None
    except ValueError as exc:
        memories = []
        load_error = str(exc)
    problems = [load_error] if load_error else []
    prompt_memories = []
    prompt_decision_refs: set[str] = set()
    memories_by_id = {}
    for memory in memories:
        memory_id = str(memory.get("id") or "")
        if memory_id:
            memories_by_id[memory_id] = memory
        if memory.get("prompt_eligible") is not True:
            continue
        prompt_memories.append(memory)
        refs = _memory_refs(memory)
        prompt_decision_refs.update(ref["id"] for ref in refs if ref.get("artifact") == "memory_decision" and ref.get("id"))
        if memory.get("status", "active") != "active":
            problems.append(f"{memory_id}: prompt memory is not active")
        if memory.get("memory_type") not in PROMPT_MEMORY_TYPES:
            problems.append(f"{memory_id}: prompt memory type is not allowed")
        if memory.get("authority") not in PROMPT_AUTHORITIES:
            problems.append(f"{memory_id}: prompt memory authority is not accepted")
        if memory.get("authority") == "model_proposed":
            problems.append(f"{memory_id}: model_proposed memory is prompt eligible")
        if not memory.get("evidence_refs"):
            problems.append(f"{memory_id}: prompt memory has no evidence_refs")
        if memory.get("source") == "human_review" and not any(ref.get("artifact") == "memory_review" for ref in refs):
            problems.append(f"{memory_id}: human-review memory lacks memory_review evidence")
    evidence = {
        "accepted_memory_count": len(memories),
        "prompt_eligible_count": len(prompt_memories),
        "prompt_memory_ids": [memory.get("id") for memory in prompt_memories],
        "prompt_decision_refs": sorted(prompt_decision_refs),
        "memories_by_id": memories_by_id,
    }
    stage = _stage(
        "m8_accepted_memory_authority",
        not problems,
        (
            f"{len(prompt_memories)} prompt-eligible accepted memory row(s) have accepted authority and evidence"
            if not problems
            else "; ".join(str(problem) for problem in problems if problem)
        ),
        details={
            "accepted_memory_count": evidence["accepted_memory_count"],
            "prompt_eligible_count": evidence["prompt_eligible_count"],
            "prompt_memory_ids": evidence["prompt_memory_ids"],
        },
    )
    return stage, evidence


def _decision_prompt_boundary_stage(paths: CompanionPaths, prompt_decision_refs: set[str]) -> tuple[dict, dict]:
    try:
        decisions = load_memory_decisions(paths.memory_decisions_file)
        load_error = None
    except MemoryDecisionValidationError as exc:
        decisions = []
        load_error = str(exc)
    try:
        actions = load_memory_review_actions(paths.memory_review_actions_file)
    except MemoryReviewError:
        actions = []
    approved_review_decision_ids = {
        str(action.get("decision_id"))
        for action in actions
        if action.get("action") in {"approve", "edit_and_approve"}
    }
    problems = [load_error] if load_error else []
    blocked_ids = []
    for decision in decisions:
        if decision.decision in PROMPT_BLOCKED_DECISIONS:
            blocked_ids.append(decision.id)
            if decision.prompt_eligible:
                problems.append(f"{decision.id}: {decision.decision} decision is prompt eligible")
            if decision.id in prompt_decision_refs and decision.id not in approved_review_decision_ids:
                problems.append(f"{decision.id}: {decision.decision} decision is referenced by prompt memory")
        if decision.authority == "model_proposed" and decision.prompt_eligible:
            problems.append(f"{decision.id}: model_proposed decision is prompt eligible")
    evidence = {
        "decision_count": len(decisions),
        "blocked_decision_ids": blocked_ids,
        "approved_review_decision_ids": sorted(approved_review_decision_ids),
        "prompt_decision_refs": sorted(prompt_decision_refs),
    }
    stage = _stage(
        "m8_nonaccepted_memory_prompt_boundary",
        not problems,
        (
            "proposal/quarantine/rejected/audit-only/review decisions stay out of prompt authority"
            if not problems
            else "; ".join(str(problem) for problem in problems if problem)
        ),
        details=evidence,
    )
    return stage, evidence


def _review_audit_stage(paths: CompanionPaths, memories_by_id: dict[str, dict]) -> tuple[dict, dict]:
    try:
        actions = load_memory_review_actions(paths.memory_review_actions_file)
        load_error = None
    except MemoryReviewError as exc:
        actions = []
        load_error = str(exc)
    problems = [load_error] if load_error else []
    approved_actions = []
    for action in actions:
        if not action.get("decision_id"):
            problems.append(f"{action.get('id')}: missing decision_id")
        if action.get("action") in {"approve", "edit_and_approve"}:
            approved_actions.append(action)
            memory_id = action.get("accepted_memory_id")
            memory = memories_by_id.get(str(memory_id))
            if not memory:
                problems.append(f"{action.get('id')}: approved action missing accepted memory {memory_id}")
                continue
            refs = _memory_refs(memory)
            if not any(ref.get("artifact") == "memory_review" and ref.get("id") == action.get("id") for ref in refs):
                problems.append(f"{memory_id}: accepted memory lacks review action evidence")
            if memory.get("authority") not in {"user_asserted", "evaluator_approved"}:
                problems.append(f"{memory_id}: human-reviewed memory authority is not trusted")
    evidence = {
        "review_action_count": len(actions),
        "approved_action_count": len(approved_actions),
        "approved_memory_ids": [action.get("accepted_memory_id") for action in approved_actions],
    }
    stage = _stage(
        "m8_human_review_audit",
        not problems,
        "human review actions are auditable" if not problems else "; ".join(str(problem) for problem in problems if problem),
        details=evidence,
    )
    return stage, evidence


def _retrieval_boundary_stage(snapshot: dict, report: dict | None) -> dict:
    boundaries = report.get("boundaries") if isinstance(report, dict) and isinstance(report.get("boundaries"), dict) else {}
    ok = bool(snapshot.get("ok") is True and boundaries.get("proposal_or_quarantine_prompt_authority") is False)
    return _stage(
        "m8_retrieval_prompt_boundary",
        ok,
        "retrieval report blocks proposal/quarantine prompt authority" if ok else "retrieval report does not prove prompt boundary",
        details={"proposal_or_quarantine_prompt_authority": boundaries.get("proposal_or_quarantine_prompt_authority")},
    )


def _static_boundary_stage(paths: CompanionPaths) -> dict:
    files = [
        REPO_ROOT / "companion_core" / "dialogue.py",
        REPO_ROOT / "companion_core" / "memory_retrieval.py",
        REPO_ROOT / "companion_core" / "m8_memory_policy.py",
        REPO_ROOT / "companion_core" / "m8_memory_review.py",
        REPO_ROOT / "window" / "window.py",
        REPO_ROOT / "scripts" / "run_m8_memory_freeze.py",
    ]
    problems = []
    for path in files:
        text = _read_text(path)
        if SCHEDULER_MUTATION_RE.search(text):
            problems.append(f"scheduler mutation pattern found in {_relative(paths, path)}")
    window_source = _read_text(REPO_ROOT / "window" / "window.py")
    if '@app.route("/life", methods=["POST"])' in window_source or "@app.post(\"/life\")" in window_source:
        problems.append("/life write route detected")
    return _stage(
        "m8_static_runtime_boundaries",
        not problems,
        "M8 sources contain no scheduler mutation or /life write route" if not problems else "; ".join(problems),
    )


def _readonly_profile_stage() -> dict:
    return _stage(
        "m8_freeze_readonly_profile",
        True,
        "freeze gate is read-only; only CLI/report writer emits m8_memory_freeze_report.json",
        details=_readonly_profile(),
    )


def _readonly_profile() -> dict:
    return {
        "name": "M8 memory and dialogue final freeze",
        "readonly_gate": True,
        "writes_report_only": True,
        "wake_cycle_run": False,
        "provider_generation_requested": False,
        "scheduler_mutation_allowed": False,
        "cron_replacement": False,
        "timer_installation": False,
        "service_mutation_allowed": False,
        "life_write_route_allowed": False,
        "semantic_shadow_authoritative": False,
        "raw_provider_payload_storage_allowed": False,
    }


def _memory_refs(memory: dict) -> list[dict]:
    refs = []
    for key in ("evidence_refs", "schema_refs"):
        values = memory.get(key) if isinstance(memory.get(key), list) else []
        refs.extend(ref for ref in values if isinstance(ref, dict))
    if memory.get("memory_decision_id"):
        refs.append({"artifact": "memory_decision", "id": memory.get("memory_decision_id")})
    return refs


def _public_memory_evidence(memory_evidence: dict) -> dict:
    return {
        key: value
        for key, value in memory_evidence.items()
        if key != "memories_by_id"
    }


def _stage_ok(stages: list[dict], name: str) -> bool:
    matches = [stage for stage in stages if stage.get("name") == name]
    return bool(matches) and all(stage.get("status") == "pass" for stage in matches)


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


def _load_report(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    snapshot = {"path": _relative(paths, path), "exists": path.exists(), "ok": False, "recommendation": None}
    if isinstance(report, dict):
        snapshot.update({"ok": report.get("ok") is True, "recommendation": report.get("recommendation"), "saved_at": report.get("saved_at")})
    return snapshot


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
