"""M8 read-only memory steward pass for completed text dialogue."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import (
    DIALOGUE_BOUNDARIES,
    SECRET_LIKE_RE,
    SENSITIVE_RE,
    build_memory_proposals,
)
from .m8_memory_schema import (
    MemoryDecision,
    MemoryDecisionValidationError,
    validate_memory_decision,
)
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_memory_steward_readonly_ready"

RELATIONSHIP_RE = re.compile(
    r"(?i)\b(relationship|partner|girlfriend|boyfriend|wife|husband|romantic|love you)\b|"
    r"(关系|伴侣|女朋友|男朋友|妻子|丈夫|恋人|恋爱|爱你)"
)
CONFLICT_RE = re.compile(
    r"(?i)\b(actually|instead|correction|correct that|not anymore|no longer)\b|"
    r"(更正|纠正|不是|不再|改成)"
)
EXPLICIT_MEMORY_REQUEST_RE = re.compile(
    r"(?i)(?:^|[。！？.!?\n:：,，\s])(?:please\s+)?(?:remember|note)\s+(?:that\s+)?|"
    r"(?:^|[。！？.!?\n:：,，\s])(?:请记住|记住|以后记得)[：:，,\s]*"
)
QUESTION_MARKER_RE = re.compile(
    r"(?i)[?？]|(?:\b(?:what|why|how|when|where|who)\b)|"
    r"(什么|吗|么|是否|是不是|为什么|怎么|怎样|如何|哪里|谁|哪|几)"
)


@dataclass
class M8MemoryStewardResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m8_memory_steward_readonly(
    paths: CompanionPaths,
    *,
    transcript_path: str | Path | None = None,
    transcript_limit: int = 3,
    turn_limit: int = 20,
) -> M8MemoryStewardResult:
    """Inspect recent dialogue turns and return M8 memory decisions.

    This function is read-only: it does not write memory decisions, accepted
    memory, wake events, provider payloads, scheduler state, or /life routes.
    The companion report writer is deliberately separate.
    """

    saved_at = datetime.now()
    stages: list[dict] = []
    errors: list[str] = []
    accepted_memories, accepted_stage = _load_json_array(paths.memory_store, "accepted_memory")
    stages.append(accepted_stage)

    transcripts = _resolve_transcript_paths(
        paths,
        transcript_path=transcript_path,
        transcript_limit=transcript_limit,
    )
    stages.append(_stage(
        "dialogue_transcripts_discovered",
        True,
        f"found {len(transcripts)} transcript(s)" if transcripts else "no dialogue transcripts found; no decisions produced",
        details={"transcripts": [_relative(paths, path) for path in transcripts]},
    ))

    decisions: list[MemoryDecision] = []
    turns_checked = 0
    source_turn_ids: set[str] = set()
    for transcript in transcripts:
        rows, read_errors = _read_transcript(transcript)
        if read_errors:
            errors.extend(read_errors)
            stages.append(_stage(
                "dialogue_transcript_read",
                False,
                f"{_relative(paths, transcript)} read failed: {'; '.join(read_errors)}",
                details={"transcript": _relative(paths, transcript)},
            ))
            continue
        stages.append(_stage(
            "dialogue_transcript_read",
            True,
            f"{_relative(paths, transcript)} read",
            details={"transcript": _relative(paths, transcript), "rows": len(rows)},
        ))
        for human_turn, linked_turn_ids in _iter_human_turns(rows, limit=turn_limit):
            turns_checked += 1
            source_turn_ids.update(linked_turn_ids)
            decisions.extend(_decisions_for_human_turn(
                paths=paths,
                transcript=transcript,
                human_turn=human_turn,
                linked_turn_ids=linked_turn_ids,
                created_at=saved_at,
                start_index=len(decisions) + 1,
            ))

    validation_errors = []
    validated_decisions = []
    for decision in decisions:
        try:
            validated_decisions.append(validate_memory_decision(decision))
        except MemoryDecisionValidationError as exc:
            validation_errors.append(f"{decision.id}: {exc}")
    if validation_errors:
        errors.extend(validation_errors)
    stages.append(_stage(
        "memory_decision_schema",
        not validation_errors,
        (
            f"{len(validated_decisions)} decision(s) validate against M8.1 schema"
            if not validation_errors
            else "; ".join(validation_errors)
        ),
    ))
    stages.append(_stage(
        "readonly_boundaries",
        True,
        "steward pass is report-only and performs no provider or memory writes",
        details={
            "provider_calls": 0,
            "memory_decisions_written": False,
            "accepted_memory_written": False,
            "wake_events_written": False,
            "scheduler_mutated": False,
            "raw_provider_payload_stored": False,
        },
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M8.2",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": {
            "readonly": True,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "writes_memory_decisions": False,
            "writes_accepted_memory": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
        },
        "counts": {
            "accepted_memory": len(accepted_memories),
            "transcripts": len(transcripts),
            "turns_checked": turns_checked,
            "source_turn_ids": len(source_turn_ids),
            "decisions": len(validated_decisions),
            "accepted_shaped_decisions": sum(
                1 for decision in validated_decisions if decision.decision == "accepted"
            ),
            "human_review_decisions": sum(
                1 for decision in validated_decisions if decision.decision == "human_review_required"
            ),
            "quarantined_decisions": sum(
                1 for decision in validated_decisions if decision.decision == "quarantined"
            ),
        },
        "source_files": {
            "accepted_memory": _relative(paths, paths.memory_store),
            "memory_decisions": _relative(paths, paths.memory_decisions_file),
            "transcripts": [_relative(paths, path) for path in transcripts],
        },
        "decisions": [decision.to_dict() for decision in validated_decisions],
        "boundaries": {
            **DIALOGUE_BOUNDARIES,
            "memory_decisions_written": False,
            "accepted_memory_written": False,
            "provider_generation_requested": False,
        },
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m8_memory_steward": "python3 scripts/run_m8_memory_steward.py --companion-home "
            + str(paths.home),
        },
    }
    return M8MemoryStewardResult(
        ok=ok,
        recommendation=recommendation,
        report=report,
        errors=errors,
    )


def write_m8_memory_steward_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_memory_steward_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _decisions_for_human_turn(
    *,
    paths: CompanionPaths,
    transcript: Path,
    human_turn: dict,
    linked_turn_ids: list[str],
    created_at: datetime,
    start_index: int,
) -> list[MemoryDecision]:
    conversation_id = str(human_turn.get("conversation_id") or transcript.stem)
    source_turn_id = str(human_turn.get("id") or "")
    text = str(human_turn.get("content") or "")
    proposals = build_memory_proposals(
        text,
        conversation_id=conversation_id,
        source_turn_id=source_turn_id,
    )
    if _is_question_like(text) and not _is_explicit_memory_request(text):
        proposals = []
    decisions = []
    for offset, proposal in enumerate(proposals):
        risk = _risk_for_text(text, proposal.get("content", ""))
        decision_status = _decision_for_risk_and_status(risk, proposal.get("status"))
        prompt_eligible = decision_status == "accepted" and risk == "low"
        decisions.append(MemoryDecision(
            id=_decision_id(created_at, start_index + offset),
            conversation_id=conversation_id,
            source_turn_ids=linked_turn_ids,
            candidate_content=str(proposal.get("content") or "").strip(),
            memory_type=_memory_type_for_candidate(str(proposal.get("content") or "")),
            decision=decision_status,
            authority="memory_steward",
            prompt_eligible=prompt_eligible,
            risk=risk,
            reason=_reason_for_decision(risk, decision_status, proposal.get("reason")),
            evidence_refs=[{
                "artifact": "conversation",
                "id": source_turn_id,
                "path": _relative(paths, transcript),
            }],
            created_at=created_at.isoformat(),
        ))
    return decisions


def _decision_for_risk_and_status(risk: str, proposal_status: str | None) -> str:
    if risk == "low" and proposal_status == "auto_accepted":
        return "accepted"
    if risk == "sensitive":
        return "quarantined"
    if risk in {"conflict", "relationship"}:
        return "human_review_required"
    return "human_review_required"


def _risk_for_text(raw_text: str, candidate: str) -> str:
    text = f"{raw_text}\n{candidate}"
    if SECRET_LIKE_RE.search(text) or SENSITIVE_RE.search(text):
        return "sensitive"
    if RELATIONSHIP_RE.search(text):
        return "relationship"
    if CONFLICT_RE.search(text):
        return "conflict"
    return "low"


def _memory_type_for_candidate(candidate: str) -> str:
    lowered = candidate.lower()
    if any(word in lowered for word in ("chat", "reply", "respond", "conversation", "dialogue")):
        return "procedural"
    if any(word in candidate for word in ("聊天", "回复", "对话")):
        return "procedural"
    return "semantic"


def _is_question_like(text: str) -> bool:
    return bool(QUESTION_MARKER_RE.search(text))


def _is_explicit_memory_request(text: str) -> bool:
    return bool(EXPLICIT_MEMORY_REQUEST_RE.search(text))


def _reason_for_decision(risk: str, decision: str, proposal_reason: object) -> str:
    if decision == "accepted":
        return "explicit low-risk user-stated fact/preference"
    if risk == "sensitive":
        return "sensitive or secret-like content is report-only and not prompt eligible"
    if risk == "relationship":
        return "relationship-defining memory requires human review"
    if risk == "conflict":
        return "possible memory conflict requires human review"
    reason = str(proposal_reason or "").strip()
    return reason or "memory candidate requires human review"


def _iter_human_turns(rows: list[dict], *, limit: int) -> list[tuple[dict, list[str]]]:
    turns = []
    for index, row in enumerate(rows):
        if row.get("role") != "human" or row.get("status", "completed") != "completed":
            continue
        human_id = str(row.get("id") or "")
        if not human_id:
            continue
        linked_turn_ids = [human_id]
        if index + 1 < len(rows):
            next_row = rows[index + 1]
            if (
                next_row.get("role") == "assistant"
                and next_row.get("status", "completed") == "completed"
                and next_row.get("conversation_id") == row.get("conversation_id")
                and next_row.get("id")
            ):
                linked_turn_ids.append(str(next_row["id"]))
        turns.append((row, linked_turn_ids))
    return turns[-limit:] if limit else turns


def _resolve_transcript_paths(
    paths: CompanionPaths,
    *,
    transcript_path: str | Path | None,
    transcript_limit: int,
) -> list[Path]:
    if transcript_path:
        path = Path(transcript_path).expanduser()
        if not path.is_absolute():
            path = paths.home / path
        return [path]
    if not paths.conversations_dir.exists():
        return []
    transcripts = sorted(
        paths.conversations_dir.glob("*.jsonl"),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    return transcripts[-transcript_limit:] if transcript_limit else transcripts


def _read_transcript(path: Path) -> tuple[list[dict], list[str]]:
    rows = []
    errors = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], [f"missing transcript: {path}"]
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {line_number}: row must be an object")
            continue
        rows.append(row)
    return rows, errors


def _load_json_array(path: Path, label: str) -> tuple[list[dict], dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return [], _stage(label, True, f"{label} file missing; treating as empty")
    except json.JSONDecodeError as exc:
        return [], _stage(label, False, f"{label} invalid JSON: {exc.msg}")
    if not isinstance(payload, list):
        return [], _stage(label, False, f"{label} must be a JSON array")
    return payload, _stage(label, True, f"{label} loaded", details={"count": len(payload)})


def _decision_id(created_at: datetime, sequence: int) -> str:
    return f"memdec_{created_at.strftime('%Y%m%d_%H%M%S')}_{sequence:03d}"


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
