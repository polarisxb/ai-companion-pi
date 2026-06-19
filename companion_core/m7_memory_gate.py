"""Read-only M7.4 memory proposal gate for text dialogue."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES
from .memory import JsonMemoryStore
from .paths import CompanionPaths


@dataclass
class M7MemoryProposalGateResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m7_memory_proposal_gate(paths: CompanionPaths) -> M7MemoryProposalGateResult:
    """Inspect dialogue memory proposal behavior without accepting proposals.

    This is intentionally read-only with respect to memory semantics: it reads
    accepted JSON memory, proposal JSONL, dialogue transcripts, and dialogue
    events, then emits a report. It never changes proposal status and never
    promotes proposed memory into prompt-authoritative accepted memory.
    """

    accepted_memories = _load_json_array(paths.memory_store, label="accepted_memory")
    proposals = _load_jsonl(paths.memory_proposals_file, label="memory_proposals")
    events = _load_jsonl(paths.conversation_events_file, label="conversation_events")
    transcript_turn_ids = _load_transcript_turn_ids(paths)
    errors: list[str] = []
    stop_reasons: list[str] = []

    accepted_ids = {memory.get("id") for memory in accepted_memories if isinstance(memory, dict)}
    accepted_contents = {
        memory.get("content")
        for memory in accepted_memories
        if isinstance(memory, dict) and memory.get("content")
    }

    prompt_authoritative_proposals = []
    linked_proposals = 0
    for idx, proposal in enumerate(proposals, start=1):
        if not isinstance(proposal, dict):
            errors.append(f"proposal line {idx}: row must be an object")
            continue
        proposal_id = proposal.get("id") or f"line:{idx}"
        conversation_id = proposal.get("conversation_id")
        source_turn_id = proposal.get("source_turn_id")
        if not conversation_id:
            errors.append(f"proposal {proposal_id}: missing conversation_id")
        if not source_turn_id:
            errors.append(f"proposal {proposal_id}: missing source_turn_id")
        if conversation_id and source_turn_id:
            linked_proposals += 1
            if transcript_turn_ids and source_turn_id not in transcript_turn_ids:
                errors.append(f"proposal {proposal_id}: source_turn_id not found in transcripts")
        if proposal.get("status") == "accepted" or proposal.get("accepted") is True:
            errors.append(f"proposal {proposal_id}: proposal gate must not observe accepted proposal state")
        if proposal.get("accepted_memory_id") in accepted_ids:
            errors.append(f"proposal {proposal_id}: linked to accepted memory")
        if proposal.get("content") in accepted_contents:
            errors.append(f"proposal {proposal_id}: duplicates accepted memory content")
        if _is_prompt_authoritative(proposal):
            prompt_authoritative_proposals.append(proposal_id)

    if prompt_authoritative_proposals:
        errors.append(
            "proposal records are prompt-authoritative: "
            + ", ".join(str(item) for item in prompt_authoritative_proposals)
        )

    for idx, memory in enumerate(accepted_memories, start=1):
        if not isinstance(memory, dict):
            errors.append(f"accepted memory row {idx}: row must be an object")
            continue
        if memory.get("source_event_id") and not any(
            event.get("id") == memory.get("source_event_id") for event in events if isinstance(event, dict)
        ):
            stop_reasons.append("accepted_memory_source_event_missing")

    if not proposals:
        stop_reasons.append("no_memory_proposals_observed")
    if errors:
        stop_reasons.append("memory_proposal_gate_failed")

    prompt_authority_status = {
        "accepted_prompt_authoritative_count": sum(1 for memory in accepted_memories if _is_prompt_authoritative(memory)),
        "proposal_prompt_authoritative_count": len(prompt_authoritative_proposals),
        "proposals_prompt_authoritative": False if not prompt_authoritative_proposals else True,
        "proposal_authority_promoted": bool(prompt_authoritative_proposals),
    }
    ok = not errors
    report = {
        "schema_version": 1,
        "saved_at": datetime.now().isoformat(),
        "ok": ok,
        "recommendation": "m7_memory_proposals_ready" if ok else "inspect",
        "stop_reasons": stop_reasons,
        "counts": {
            "accepted_memory": len(accepted_memories),
            "proposal_memory": len(proposals),
            "proposal_source_linked": linked_proposals,
            "conversation_events": len(events),
            "transcript_turns": len(transcript_turn_ids),
        },
        "source_linkage": {
            "required_fields": ["conversation_id", "source_turn_id"],
            "proposal_records_with_required_linkage": linked_proposals,
            "all_proposals_linked": linked_proposals == len(proposals),
        },
        "prompt_authority_status": prompt_authority_status,
        "separation": {
            "proposal_file": _relative_to_home(paths, paths.memory_proposals_file),
            "accepted_memory_file": _relative_to_home(paths, paths.memory_store),
            "proposals_separate_from_accepted_memory": not any(
                isinstance(proposal, dict)
                and (
                    proposal.get("accepted_memory_id") in accepted_ids
                    or proposal.get("content") in accepted_contents
                    or proposal.get("accepted") is True
                    or proposal.get("status") == "accepted"
                )
                for proposal in proposals
            ),
            "acceptance_workflow_present": False,
        },
        "provider_calls": 0,
        "boundaries": dict(DIALOGUE_BOUNDARIES),
        "errors": errors,
    }
    return M7MemoryProposalGateResult(
        ok=ok,
        recommendation=report["recommendation"],
        report=report,
        errors=errors,
    )


def write_m7_memory_proposal_report(paths: CompanionPaths, report: dict) -> Path:
    report_path = paths.life_loop_dir / "m7_memory_proposal_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _load_json_array(path: Path, *, label: str) -> list[dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        return [{"_gate_error": f"{label}: invalid JSON: {exc.msg}"}]
    return payload if isinstance(payload, list) else [{"_gate_error": f"{label}: expected list"}]


def _load_jsonl(path: Path, *, label: str) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return rows
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            rows.append({"_gate_error": f"{label} line {line_number}: invalid JSON: {exc.msg}"})
            continue
        rows.append(row)
    return rows


def _load_transcript_turn_ids(paths: CompanionPaths) -> set[str]:
    turn_ids: set[str] = set()
    if not paths.conversations_dir.exists():
        return turn_ids
    for transcript in paths.conversations_dir.glob("*.jsonl"):
        for row in _load_jsonl(transcript, label=f"transcript {transcript.name}"):
            if isinstance(row, dict) and row.get("id"):
                turn_ids.add(row["id"])
    return turn_ids


def _is_prompt_authoritative(record: dict) -> bool:
    return bool(record.get("prompt_eligible") or record.get("accepted_for_context"))


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
