"""M7.4 read-only memory proposal gate for text dialogue artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES
from .paths import CompanionPaths

PROMPT_AUTHORITY_KEYS = ("prompt_eligible", "accepted_for_context", "quality_gate")


@dataclass
class M7MemoryProposalGateResult:
    ok: bool
    report_path: Path
    accepted_memory_count: int
    proposal_memory_count: int
    linked_proposal_count: int
    prompt_authoritative_proposal_count: int
    stop_reasons: list[str] = field(default_factory=list)

    @property
    def recommendation(self) -> str:
        return "m7_memory_proposals_ready" if self.ok else "inspect"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "recommendation": self.recommendation,
            "report_path": str(self.report_path),
            "accepted_memory_count": self.accepted_memory_count,
            "proposal_memory_count": self.proposal_memory_count,
            "linked_proposal_count": self.linked_proposal_count,
            "prompt_authoritative_proposal_count": self.prompt_authoritative_proposal_count,
            "stop_reasons": self.stop_reasons,
        }


def run_m7_memory_proposal_gate(paths: CompanionPaths) -> M7MemoryProposalGateResult:
    """Validate M7 proposal memory artifacts without accepting or promoting them."""

    paths.ensure_runtime_dirs()
    accepted_memories, memory_errors = _read_json_array(paths.memory_store, label="accepted_memory")
    proposals, proposal_errors = _read_jsonl(paths.memory_proposals_file, label="memory_proposal")
    transcripts = _load_transcript_turn_index(paths.conversations_dir)

    stop_reasons = [*memory_errors, *proposal_errors]
    accepted_ids = {memory.get("id") for memory in accepted_memories if isinstance(memory, dict)}
    accepted_contents = {
        str(memory.get("content", "")) for memory in accepted_memories if isinstance(memory, dict)
    }
    linked_count = 0
    prompt_authoritative_count = 0

    for idx, proposal in enumerate(proposals, start=1):
        prefix = f"proposal line {idx}"
        if not isinstance(proposal, dict):
            stop_reasons.append(f"{prefix}: row must be an object")
            continue
        proposal_id = proposal.get("id")
        conversation_id = proposal.get("conversation_id")
        source_turn_id = proposal.get("source_turn_id")
        if not proposal_id:
            stop_reasons.append(f"{prefix}: missing id")
        if not conversation_id:
            stop_reasons.append(f"{prefix}: missing conversation_id")
        if not source_turn_id:
            stop_reasons.append(f"{prefix}: missing source_turn_id")
        if conversation_id and source_turn_id:
            if (conversation_id, source_turn_id) in transcripts:
                linked_count += 1
            else:
                stop_reasons.append(f"{prefix}: source turn not found in transcripts")
        if proposal.get("status") != "proposed":
            stop_reasons.append(f"{prefix}: status must remain proposed")
        if proposal.get("accepted") is not False:
            stop_reasons.append(f"{prefix}: accepted must remain false")
        if proposal.get("accepted_memory_id") in accepted_ids and proposal.get("accepted_memory_id"):
            stop_reasons.append(f"{prefix}: points at an accepted memory id")
        if str(proposal.get("content", "")) in accepted_contents and proposal.get("content"):
            stop_reasons.append(f"{prefix}: duplicates accepted memory content")
        if _is_prompt_authoritative(proposal):
            prompt_authoritative_count += 1
            stop_reasons.append(f"{prefix}: proposal must not be prompt-authoritative")

    ok = not stop_reasons
    report_path = paths.life_loop_dir / "m7_memory_proposal_report.json"
    report = {
        "schema_version": 1,
        "saved_at": datetime.now().isoformat(),
        "ok": ok,
        "recommendation": "m7_memory_proposals_ready" if ok else "inspect",
        "stop_reasons": stop_reasons,
        "counts": {
            "accepted_memory": len(accepted_memories),
            "proposal_memory": len(proposals),
            "linked_proposals": linked_count,
            "prompt_authoritative_proposals": prompt_authoritative_count,
        },
        "source_linkage": {
            "required_fields": ["conversation_id", "source_turn_id"],
            "linked_proposals": linked_count,
            "proposal_records": [
                {
                    "id": proposal.get("id"),
                    "conversation_id": proposal.get("conversation_id"),
                    "source_turn_id": proposal.get("source_turn_id"),
                    "linked": (proposal.get("conversation_id"), proposal.get("source_turn_id")) in transcripts,
                }
                for proposal in proposals if isinstance(proposal, dict)
            ],
        },
        "prompt_authority": {
            "proposal_records_prompt_authoritative": False,
            "semantic_shadow_authority_promoted": False,
            "proposals_are_prompt_authoritative": prompt_authoritative_count > 0,
        },
        "separation": {
            "proposal_file": _relative_to_home(paths, paths.memory_proposals_file),
            "accepted_memory_file": _relative_to_home(paths, paths.memory_store),
            "acceptance_path_added": False,
            "proposal_state_mutated": False,
        },
        "provider_calls": 0,
        "boundaries": dict(DIALOGUE_BOUNDARIES),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return M7MemoryProposalGateResult(
        ok=ok,
        report_path=report_path,
        accepted_memory_count=len(accepted_memories),
        proposal_memory_count=len(proposals),
        linked_proposal_count=linked_count,
        prompt_authoritative_proposal_count=prompt_authoritative_count,
        stop_reasons=stop_reasons,
    )


def _read_json_array(path: Path, *, label: str) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return [], [f"{label}: invalid JSON: {exc.msg}"]
    if not isinstance(payload, list):
        return [], [f"{label}: root must be a list"]
    return payload, []


def _read_jsonl(path: Path, *, label: str) -> tuple[list[dict], list[str]]:
    if not path.exists():
        return [], []
    rows: list[dict] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{label} line {line_number}: invalid JSON: {exc.msg}")
            continue
        rows.append(row)
    return rows, errors


def _load_transcript_turn_index(conversations_dir: Path) -> set[tuple[str, str]]:
    index: set[tuple[str, str]] = set()
    if not conversations_dir.exists():
        return index
    for path in conversations_dir.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            conversation_id = row.get("conversation_id")
            turn_id = row.get("id")
            if conversation_id and turn_id:
                index.add((conversation_id, turn_id))
    return index


def _is_prompt_authoritative(proposal: dict) -> bool:
    if proposal.get("prompt_eligible") is True or proposal.get("accepted_for_context") is True:
        return True
    if proposal.get("quality_gate") == "accepted":
        return True
    return bool(proposal.get("memory_type") == "semantic" and proposal.get("authority") in {"model", "semantic", "user_asserted"})


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
