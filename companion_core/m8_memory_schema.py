"""M8 memory decision schema and append-only ledger helpers."""

from __future__ import annotations

import fcntl
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MEMORY_DECISION_SCHEMA_VERSION = 1

ALLOWED_MEMORY_RISKS = {
    "low",
    "medium",
    "high",
    "sensitive",
    "conflict",
    "relationship",
}
ALLOWED_MEMORY_TYPES = {
    "semantic",
    "episodic",
    "reflection",
    "procedural",
}
ALLOWED_MEMORY_AUTHORITIES = {
    "memory_steward",
    "user_asserted",
    "evaluator_approved",
    "system_config",
    "model_proposed",
}
ALLOWED_MEMORY_DECISIONS = {
    "accepted",
    "quarantined",
    "rejected",
    "audit_only",
    "merge_proposed",
    "update_proposed",
    "human_review_required",
}
TRUSTED_PROMPT_AUTHORITIES = {
    "user_asserted",
    "evaluator_approved",
    "system_config",
}
AUTO_REVIEW_RISKS = {
    "sensitive",
    "conflict",
    "relationship",
}
PROMPT_INELIGIBLE_DECISIONS = ALLOWED_MEMORY_DECISIONS - {"accepted"}

MEMORY_DECISION_SCHEMA = {
    "schema_version": MEMORY_DECISION_SCHEMA_VERSION,
    "required": [
        "id",
        "conversation_id",
        "source_turn_ids",
        "candidate_content",
        "decision",
        "risk",
        "reason",
        "evidence_refs",
    ],
    "allowed": {
        "risk": sorted(ALLOWED_MEMORY_RISKS),
        "memory_type": sorted(ALLOWED_MEMORY_TYPES),
        "authority": sorted(ALLOWED_MEMORY_AUTHORITIES),
        "decision": sorted(ALLOWED_MEMORY_DECISIONS),
    },
}


class MemoryDecisionValidationError(ValueError):
    """Raised when a memory decision cannot enter the M8 decision ledger."""


@dataclass
class MemoryDecision:
    id: str
    conversation_id: str
    source_turn_ids: list[str]
    candidate_content: str
    decision: str
    risk: str
    reason: str
    evidence_refs: list[dict]
    memory_type: str = "semantic"
    authority: str = "memory_steward"
    prompt_eligible: bool = False
    accepted_memory_id: str | None = None
    created_at: str | None = None
    schema_version: int = MEMORY_DECISION_SCHEMA_VERSION

    def to_dict(self) -> dict:
        payload = {
            "schema_version": self.schema_version,
            "id": self.id,
            "conversation_id": self.conversation_id,
            "source_turn_ids": list(self.source_turn_ids),
            "candidate_content": self.candidate_content,
            "memory_type": self.memory_type,
            "decision": self.decision,
            "authority": self.authority,
            "prompt_eligible": self.prompt_eligible,
            "risk": self.risk,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }
        if self.accepted_memory_id:
            payload["accepted_memory_id"] = self.accepted_memory_id
        return payload

    def validate(self) -> "MemoryDecision":
        return validate_memory_decision(self)


def normalize_memory_decision(record: MemoryDecision | dict) -> MemoryDecision:
    """Return a normalized MemoryDecision without writing any memory state."""

    if isinstance(record, MemoryDecision):
        raw = record.to_dict()
    elif isinstance(record, dict):
        raw = dict(record)
    else:
        raise MemoryDecisionValidationError("memory decision must be a dict or MemoryDecision")

    try:
        schema_version = int(raw.get("schema_version", MEMORY_DECISION_SCHEMA_VERSION))
    except (TypeError, ValueError) as exc:
        raise MemoryDecisionValidationError("schema_version must be an integer") from exc

    return MemoryDecision(
        schema_version=schema_version,
        id=_required_text(raw, "id"),
        conversation_id=_required_text(raw, "conversation_id"),
        source_turn_ids=_normalize_text_list(raw.get("source_turn_ids"), "source_turn_ids"),
        candidate_content=_required_text(raw, "candidate_content"),
        memory_type=_normalize_choice(raw.get("memory_type", "semantic")),
        decision=_normalize_choice(raw.get("decision")),
        authority=_normalize_choice(raw.get("authority", "memory_steward")),
        prompt_eligible=_normalize_bool(raw.get("prompt_eligible", False), "prompt_eligible"),
        risk=_normalize_choice(raw.get("risk")),
        reason=_required_text(raw, "reason"),
        evidence_refs=_normalize_evidence_refs(raw.get("evidence_refs")),
        accepted_memory_id=_optional_text(raw.get("accepted_memory_id")),
        created_at=_optional_text(raw.get("created_at")) or _now_iso(),
    )


def validate_memory_decision(record: MemoryDecision | dict) -> MemoryDecision:
    """Normalize and validate a memory decision for the M8 ledger."""

    decision = normalize_memory_decision(record)
    errors: list[str] = []

    if decision.schema_version != MEMORY_DECISION_SCHEMA_VERSION:
        errors.append(f"schema_version must be {MEMORY_DECISION_SCHEMA_VERSION}")
    if decision.memory_type not in ALLOWED_MEMORY_TYPES:
        errors.append(f"memory_type must be one of {sorted(ALLOWED_MEMORY_TYPES)}")
    if decision.decision not in ALLOWED_MEMORY_DECISIONS:
        errors.append(f"decision must be one of {sorted(ALLOWED_MEMORY_DECISIONS)}")
    if decision.authority not in ALLOWED_MEMORY_AUTHORITIES:
        errors.append(f"authority must be one of {sorted(ALLOWED_MEMORY_AUTHORITIES)}")
    if decision.risk not in ALLOWED_MEMORY_RISKS:
        errors.append(f"risk must be one of {sorted(ALLOWED_MEMORY_RISKS)}")

    if not decision.source_turn_ids:
        errors.append("source_turn_ids must be non-empty")
    if not decision.evidence_refs:
        errors.append("evidence_refs must be non-empty")
    for index, evidence in enumerate(decision.evidence_refs, start=1):
        errors.extend(_validate_evidence_ref(evidence, index))

    if decision.prompt_eligible:
        if decision.decision in PROMPT_INELIGIBLE_DECISIONS:
            errors.append(f"{decision.decision} decisions cannot be prompt_eligible")
        if decision.authority == "model_proposed":
            errors.append("model_proposed decisions cannot be prompt_eligible")
        if decision.decision == "accepted":
            has_low_risk = decision.risk == "low"
            has_trusted_authority = decision.authority in TRUSTED_PROMPT_AUTHORITIES
            if not (has_low_risk or has_trusted_authority):
                errors.append(
                    "accepted prompt_eligible decisions require low risk or trusted authority"
                )
            if decision.risk in AUTO_REVIEW_RISKS and not has_trusted_authority:
                errors.append(
                    f"{decision.risk} risk requires human or system authority before prompt eligibility"
                )

    if errors:
        raise MemoryDecisionValidationError("; ".join(errors))
    return decision


def append_memory_decision(path: Path, record: MemoryDecision | dict) -> MemoryDecision:
    return append_memory_decisions(path, [record])[0]


def append_memory_decisions(
    path: Path,
    records: list[MemoryDecision | dict],
) -> list[MemoryDecision]:
    decisions = [validate_memory_decision(record) for record in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_suffix(path.suffix + ".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(path, "a") as output:
                for decision in decisions:
                    output.write(
                        json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True)
                        + "\n"
                    )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return decisions


def load_memory_decisions(path: Path, limit: int | None = None) -> list[MemoryDecision]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []

    decisions: list[MemoryDecision] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MemoryDecisionValidationError(
                f"memory decision line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        try:
            decisions.append(validate_memory_decision(record))
        except MemoryDecisionValidationError as exc:
            raise MemoryDecisionValidationError(
                f"memory decision line {line_number}: {exc}"
            ) from exc
    return decisions[-limit:] if limit else decisions


def _required_text(raw: dict, key: str) -> str:
    value = _optional_text(raw.get(key))
    if not value:
        raise MemoryDecisionValidationError(f"{key} is required")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = " ".join(value.split())
    return normalized or None


def _normalize_choice(value: Any) -> str:
    text = _optional_text(value)
    if not text:
        return ""
    return text.lower().replace("-", "_").replace(" ", "_")


def _normalize_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise MemoryDecisionValidationError(f"{key} must be a boolean")


def _normalize_text_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MemoryDecisionValidationError(f"{key} must be a list")
    normalized = []
    for item in value:
        text = _optional_text(item)
        if text:
            normalized.append(text)
    return normalized


def _normalize_evidence_refs(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MemoryDecisionValidationError("evidence_refs must be a list")
    refs = []
    for item in value:
        if not isinstance(item, dict):
            raise MemoryDecisionValidationError("evidence_refs entries must be objects")
        refs.append(dict(item))
    return refs


def _validate_evidence_ref(evidence: dict, index: int) -> list[str]:
    errors = []
    artifact = _optional_text(evidence.get("artifact"))
    if not artifact:
        errors.append(f"evidence_refs[{index}] must include artifact")
    evidence_keys = ("id", "event_id", "path", "content_hash")
    if not any(_optional_text(evidence.get(key)) for key in evidence_keys):
        errors.append(
            f"evidence_refs[{index}] must include id, event_id, path, or content_hash"
        )
    return errors


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()
