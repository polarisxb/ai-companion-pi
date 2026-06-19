"""M7.4 read-only memory proposal gate."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import CompanionPaths

READY_RECOMMENDATION = "m7_memory_proposals_ready"
INSPECT_RECOMMENDATION = "inspect"


def run_m7_memory_proposal_gate(paths: CompanionPaths) -> dict:
    """Inspect M7 dialogue memory artifacts without accepting proposals.

    The gate is deliberately read-only for memory/proposal inputs: it counts accepted
    JSON memory and proposal JSONL records, verifies proposal linkage back to a
    conversation turn, and confirms proposal records are not prompt-authoritative.
    It writes only the bounded gate report under ``life-loop``.
    """

    accepted_memories = _load_json_list(paths.memory_store)
    proposal_records, proposal_read_errors = _load_jsonl(paths.memory_proposals_file)
    transcript_turns = _load_conversation_turn_index(paths.conversations_dir)

    accepted_ids = {str(memory.get("id")) for memory in accepted_memories if memory.get("id")}
    stop_reasons: list[str] = []
    proposal_summaries = []
    linked = 0
    prompt_authoritative = 0
    separate_from_accepted = 0
    accepted_status = 0

    for proposal in proposal_records:
        proposal_id = str(proposal.get("id", ""))
        conversation_id = proposal.get("conversation_id")
        source_turn_id = proposal.get("source_turn_id")
        has_linkage = bool(conversation_id and source_turn_id)
        turn_key = (str(conversation_id), str(source_turn_id))
        turn_exists = turn_key in transcript_turns if has_linkage else False
        if has_linkage and turn_exists:
            linked += 1
        else:
            stop_reasons.append(f"proposal_missing_source_linkage:{proposal_id or 'unknown'}")

        proposal_is_prompt_authoritative = bool(
            proposal.get("prompt_eligible")
            or proposal.get("prompt_authoritative")
            or proposal.get("authority") in {"user_asserted", "system_config", "evaluator_approved", "derived_summary"}
        )
        if proposal_is_prompt_authoritative:
            prompt_authoritative += 1
            stop_reasons.append(f"proposal_prompt_authoritative:{proposal_id or 'unknown'}")

        if proposal.get("accepted") is True or proposal.get("status") == "accepted":
            accepted_status += 1
            stop_reasons.append(f"proposal_already_accepted:{proposal_id or 'unknown'}")

        accepted_memory_id = proposal.get("accepted_memory_id")
        is_separate = not accepted_memory_id and proposal_id not in accepted_ids
        if is_separate:
            separate_from_accepted += 1
        else:
            stop_reasons.append(f"proposal_not_separate_from_accepted_memory:{proposal_id or 'unknown'}")

        proposal_summaries.append({
            "id": proposal_id,
            "conversation_id": conversation_id,
            "source_turn_id": source_turn_id,
            "source_turn_found": turn_exists,
            "status": proposal.get("status"),
            "accepted": proposal.get("accepted") is True,
            "prompt_authoritative": proposal_is_prompt_authoritative,
            "separate_from_accepted_memory": is_separate,
        })

    for error in proposal_read_errors:
        stop_reasons.append(error)

    proposal_count = len(proposal_records)
    ok = not stop_reasons
    report = {
        "schema_version": 1,
        "saved_at": datetime.now().isoformat(),
        "ok": ok,
        "recommendation": READY_RECOMMENDATION if ok else INSPECT_RECOMMENDATION,
        "stop_reasons": sorted(set(stop_reasons)),
        "accepted_memory_count": len(accepted_memories),
        "proposal_memory_count": proposal_count,
        "proposal_source_link_count": linked,
        "proposal_source_link_missing_count": proposal_count - linked,
        "proposal_separate_from_accepted_count": separate_from_accepted,
        "proposal_prompt_authoritative_count": prompt_authoritative,
        "proposal_accepted_state_count": accepted_status,
        "prompt_authority_status": "proposal_only" if prompt_authoritative == 0 else "inspect",
        "accepted_memory_path": _relative_to_home(paths, paths.memory_store),
        "proposal_memory_path": _relative_to_home(paths, paths.memory_proposals_file),
        "conversation_dir": _relative_to_home(paths, paths.conversations_dir),
        "boundaries": {
            "wake_cycle_run": False,
            "scheduler_mutated": False,
            "life_write_route_added": False,
            "raw_provider_payload_stored": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_acceptance_path_added": False,
        },
        "proposals": proposal_summaries,
    }
    write_m7_memory_proposal_report(paths, report)
    return report


def write_m7_memory_proposal_report(paths: CompanionPaths, report: dict) -> None:
    report_path = paths.life_loop_dir / "m7_memory_proposal_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)


def _load_json_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _load_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    errors: list[str] = []
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"proposal_file_unreadable:{type(exc).__name__}"]
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record: Any = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"proposal_invalid_json_line:{index}")
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            errors.append(f"proposal_non_object_line:{index}")
    return records, errors


def _load_conversation_turn_index(conversations_dir: Path) -> set[tuple[str, str]]:
    index: set[tuple[str, str]] = set()
    if not conversations_dir.exists():
        return index
    for transcript in conversations_dir.glob("*.jsonl"):
        try:
            lines = transcript.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                turn = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(turn, dict) and turn.get("conversation_id") and turn.get("id"):
                index.add((str(turn["conversation_id"]), str(turn["id"])))
    return index


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
