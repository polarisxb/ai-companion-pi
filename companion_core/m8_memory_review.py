"""M8 human review queue for exceptional memory decisions."""

from __future__ import annotations

import fcntl
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES, SECRET_LIKE_RE
from .m8_memory_schema import MemoryDecision, MemoryDecisionValidationError, load_memory_decisions
from .memory import JsonMemoryStore, MemoryEntry
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_human_review_queue_ready"
REVIEWABLE_DECISIONS = {
    "quarantined",
    "human_review_required",
    "merge_proposed",
    "update_proposed",
}
TERMINAL_REVIEW_ACTIONS = {
    "approve",
    "edit_and_approve",
    "reject",
    "archive",
}


class MemoryReviewError(ValueError):
    """Raised when a human-review action cannot be applied."""


@dataclass
class M8MemoryReviewQueueResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def load_memory_review_actions(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    actions = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            action = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MemoryReviewError(f"memory review action line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(action, dict):
            raise MemoryReviewError(f"memory review action line {line_number}: action must be an object")
        if not action.get("id") or not action.get("decision_id") or not action.get("action"):
            raise MemoryReviewError(f"memory review action line {line_number}: missing id, decision_id, or action")
        actions.append(action)
    return actions


def load_memory_review_queue(paths: CompanionPaths) -> dict:
    decisions = load_memory_decisions(paths.memory_decisions_file)
    actions = load_memory_review_actions(paths.memory_review_actions_file)
    latest_actions = _latest_action_by_decision(actions)
    items = []
    reviewed = []
    for decision in decisions:
        if decision.decision not in REVIEWABLE_DECISIONS:
            continue
        latest_action = latest_actions.get(decision.id)
        item = _review_item(decision, latest_action)
        if latest_action and latest_action.get("action") in TERMINAL_REVIEW_ACTIONS:
            reviewed.append(item)
        else:
            items.append(item)
    return {
        "pending": items,
        "reviewed": reviewed,
        "actions": actions,
        "counts": {
            "decisions": len(decisions),
            "reviewable": len(items) + len(reviewed),
            "pending": len(items),
            "reviewed": len(reviewed),
            "actions": len(actions),
        },
    }


def approve_memory_review_decision(
    paths: CompanionPaths,
    decision_id: str,
    *,
    edited_content: str | None = None,
    reviewer: str = "human",
    note: str | None = None,
) -> dict:
    decision = _find_reviewable_decision(paths, decision_id)
    _ensure_not_terminal(paths, decision_id)
    content = _normalize_review_content(edited_content if edited_content is not None else decision.candidate_content)
    if SECRET_LIKE_RE.search(content):
        raise MemoryReviewError("secret-like content must be edited before approval")

    action_id = _action_id()
    memory_store = JsonMemoryStore(paths.memory_store)
    memory = memory_store.store(
        MemoryEntry(
            content=content,
            source="human_review",
            context=["m8_human_review", decision.conversation_id],
            intensity=2,
            valence=3,
            significance=3,
            memory_type=decision.memory_type if decision.memory_type in {"semantic", "procedural"} else "semantic",
            source_type="user",
            authority="evaluator_approved",
            prompt_eligible=True,
            evidence_refs=_approved_evidence(decision, action_id),
        ),
        accepted_for_context=True,
    )
    action = _review_action(
        action_id=action_id,
        decision_id=decision.id,
        action="edit_and_approve" if edited_content is not None else "approve",
        reviewer=reviewer,
        note=note,
        edited_content=content if edited_content is not None else None,
        accepted_memory_id=memory["id"],
    )
    _append_review_action(paths.memory_review_actions_file, action)
    return {
        "ok": True,
        "action": action,
        "accepted_memory": memory,
    }


def reject_memory_review_decision(
    paths: CompanionPaths,
    decision_id: str,
    *,
    reviewer: str = "human",
    note: str | None = None,
) -> dict:
    decision = _find_reviewable_decision(paths, decision_id)
    _ensure_not_terminal(paths, decision_id)
    action = _review_action(
        action_id=_action_id(),
        decision_id=decision.id,
        action="reject",
        reviewer=reviewer,
        note=note,
    )
    _append_review_action(paths.memory_review_actions_file, action)
    return {"ok": True, "action": action}


def archive_memory_review_decision(
    paths: CompanionPaths,
    decision_id: str,
    *,
    reviewer: str = "human",
    note: str | None = None,
) -> dict:
    decision = _find_reviewable_decision(paths, decision_id)
    _ensure_not_terminal(paths, decision_id)
    action = _review_action(
        action_id=_action_id(),
        decision_id=decision.id,
        action="archive",
        reviewer=reviewer,
        note=note,
    )
    _append_review_action(paths.memory_review_actions_file, action)
    return {"ok": True, "action": action}


def run_m8_memory_review_queue_check(paths: CompanionPaths) -> M8MemoryReviewQueueResult:
    saved_at = datetime.now()
    stages: list[dict] = []
    errors: list[str] = []
    try:
        queue = load_memory_review_queue(paths)
        queue_error = None
    except (MemoryDecisionValidationError, MemoryReviewError) as exc:
        queue = {"pending": [], "reviewed": [], "actions": [], "counts": {"decisions": 0, "reviewable": 0, "pending": 0, "reviewed": 0, "actions": 0}}
        queue_error = str(exc)
        errors.append(queue_error)
    stages.append(_stage(
        "m8_6_review_queue_load",
        queue_error is None,
        (
            f"loaded {queue['counts']['pending']} pending review item(s)"
            if queue_error is None
            else queue_error
        ),
        details=queue["counts"],
    ))

    route_stage = _window_route_stage(paths)
    stages.append(route_stage)
    stages.append(_stage(
        "m8_6_review_boundaries",
        True,
        "human review queue does not call providers, wake, scheduler, semantic shadow, or /life write routes",
        details={
            "provider_calls": 0,
            "wake_events_written": False,
            "scheduler_mutated": False,
            "semantic_shadow_authority_promoted": False,
            "life_write_route_added": False,
        },
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M8.6",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": {
            "human_review_queue": True,
            "ordinary_low_risk_review_required": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "life_write_route_allowed": False,
        },
        "counts": dict(queue["counts"]),
        "pending": list(queue["pending"]),
        "reviewed": list(queue["reviewed"]),
        "source_files": {
            "memory_decisions": _relative(paths, paths.memory_decisions_file),
            "memory_review_actions": _relative(paths, paths.memory_review_actions_file),
            "accepted_memory": _relative(paths, paths.memory_store),
        },
        "boundaries": {
            **DIALOGUE_BOUNDARIES,
            "provider_generation_requested": False,
            "accepted_memory_written_by_check": False,
            "memory_review_actions_written_by_check": False,
            "life_write_route_added": False,
        },
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m8_memory_review": "python3 scripts/run_m8_memory_review.py --companion-home "
            + str(paths.home),
        },
    }
    return M8MemoryReviewQueueResult(
        ok=ok,
        recommendation=recommendation,
        report=report,
        errors=errors,
    )


def write_m8_memory_review_queue_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_human_review_queue_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _find_reviewable_decision(paths: CompanionPaths, decision_id: str) -> MemoryDecision:
    for decision in load_memory_decisions(paths.memory_decisions_file):
        if decision.id == decision_id:
            if decision.decision not in REVIEWABLE_DECISIONS:
                raise MemoryReviewError(f"decision {decision_id} is not reviewable")
            return decision
    raise MemoryReviewError(f"decision {decision_id} not found")


def _ensure_not_terminal(paths: CompanionPaths, decision_id: str) -> None:
    latest = _latest_action_by_decision(load_memory_review_actions(paths.memory_review_actions_file)).get(decision_id)
    if latest and latest.get("action") in TERMINAL_REVIEW_ACTIONS:
        raise MemoryReviewError(f"decision {decision_id} already has terminal review action {latest.get('action')}")


def _normalize_review_content(content: str) -> str:
    cleaned = str(content or "").strip()
    if not cleaned:
        raise MemoryReviewError("approved memory content must not be empty")
    return cleaned[:240]


def _approved_evidence(decision: MemoryDecision, action_id: str) -> list[dict]:
    evidence = [dict(ref) for ref in decision.evidence_refs]
    evidence.append({"artifact": "memory_decision", "id": decision.id})
    evidence.append({"artifact": "memory_review", "id": action_id})
    return evidence


def _review_item(decision: MemoryDecision, latest_action: dict | None) -> dict:
    return {
        "id": decision.id,
        "conversation_id": decision.conversation_id,
        "source_turn_ids": list(decision.source_turn_ids),
        "candidate_content": decision.candidate_content,
        "memory_type": decision.memory_type,
        "decision": decision.decision,
        "risk": decision.risk,
        "reason": decision.reason,
        "evidence_refs": list(decision.evidence_refs),
        "created_at": decision.created_at,
        "review_status": "reviewed" if latest_action and latest_action.get("action") in TERMINAL_REVIEW_ACTIONS else "pending",
        "latest_action": latest_action,
        "recommended_action": _recommended_action(decision),
    }


def _recommended_action(decision: MemoryDecision) -> str:
    if decision.risk == "sensitive":
        return "edit_before_approve_or_reject"
    if decision.risk in {"relationship", "conflict"}:
        return "human_judgment_required"
    if decision.decision in {"merge_proposed", "update_proposed"}:
        return "manual_resolution_required"
    return "review_required"


def _latest_action_by_decision(actions: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for action in actions:
        latest[str(action.get("decision_id"))] = action
    return latest


def _review_action(
    *,
    action_id: str,
    decision_id: str,
    action: str,
    reviewer: str,
    note: str | None,
    edited_content: str | None = None,
    accepted_memory_id: str | None = None,
) -> dict:
    payload = {
        "id": action_id,
        "decision_id": decision_id,
        "action": action,
        "reviewer": reviewer,
        "note": str(note or "").strip(),
        "created_at": datetime.now().isoformat(),
        "accepted_memory_id": accepted_memory_id,
    }
    if edited_content is not None:
        payload["edited_content"] = edited_content
    return payload


def _append_review_action(path: Path, action: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as output:
                output.write(json.dumps(action, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return action


def _action_id() -> str:
    return f"memreview_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"


def _window_route_stage(paths: CompanionPaths) -> dict:
    source = _read_text(paths.window_dir / "window.py")
    required = [
        '@app.route("/memory-review")',
        '@app.route("/memory-review/<decision_id>/approve", methods=["POST"])',
        '@app.route("/memory-review/<decision_id>/reject", methods=["POST"])',
        '@app.route("/memory-review/<decision_id>/edit", methods=["POST"])',
    ]
    problems = [f"missing window route: {needle}" for needle in required if needle not in source]
    if '@app.route("/life", methods=["POST"])' in source or "@app.post(\"/life\")" in source:
        problems.append("/life write route detected")
    return _stage(
        "m8_6_window_review_routes",
        not problems,
        "memory review routes are present and /life remains GET-only" if not problems else "; ".join(problems),
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


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
