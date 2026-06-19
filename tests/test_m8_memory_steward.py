import json

import pytest

from companion_core import (
    CompanionPaths,
    MemoryDecision,
    MemoryDecisionValidationError,
    append_memory_decision,
    load_memory_decisions,
    validate_memory_decision,
)


def base_decision(**overrides):
    payload = {
        "id": "memdec_20260619_190000_001",
        "conversation_id": "conv-1",
        "source_turn_ids": ["turn-human-1", "turn-assistant-1"],
        "candidate_content": "Polaris prefers ordinary chat to avoid status reports unless asked.",
        "memory_type": "semantic",
        "decision": "accepted",
        "authority": "memory_steward",
        "prompt_eligible": True,
        "risk": "low",
        "reason": "direct user-stated chat preference",
        "evidence_refs": [
            {"artifact": "conversation", "id": "turn-human-1"},
        ],
        "created_at": "2026-06-19T19:00:00",
    }
    payload.update(overrides)
    return payload


def test_valid_accepted_low_risk_decision_can_be_prompt_eligible():
    decision = validate_memory_decision(base_decision())

    assert isinstance(decision, MemoryDecision)
    assert decision.decision == "accepted"
    assert decision.risk == "low"
    assert decision.authority == "memory_steward"
    assert decision.prompt_eligible is True
    assert decision.to_dict()["candidate_content"] == (
        "Polaris prefers ordinary chat to avoid status reports unless asked."
    )


@pytest.mark.parametrize(
    "decision",
    ["quarantined", "rejected", "audit_only", "human_review_required"],
)
def test_non_accepted_decisions_cannot_be_prompt_eligible(decision):
    with pytest.raises(MemoryDecisionValidationError, match="cannot be prompt_eligible"):
        validate_memory_decision(base_decision(decision=decision, prompt_eligible=True))


def test_model_proposed_decision_cannot_be_prompt_eligible():
    with pytest.raises(MemoryDecisionValidationError, match="model_proposed"):
        validate_memory_decision(base_decision(authority="model_proposed", prompt_eligible=True))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"source_turn_ids": []}, "source_turn_ids must be non-empty"),
        ({"evidence_refs": []}, "evidence_refs must be non-empty"),
    ],
)
def test_missing_evidence_or_source_ids_fail_validation(overrides, message):
    with pytest.raises(MemoryDecisionValidationError, match=message):
        validate_memory_decision(base_decision(**overrides))


def test_append_and_load_memory_decisions_jsonl(tmp_path):
    paths = CompanionPaths(tmp_path)
    first = append_memory_decision(paths.memory_decisions_file, base_decision())
    second = append_memory_decision(
        paths.memory_decisions_file,
        base_decision(
            id="memdec_20260619_190100_002",
            candidate_content="Potentially sensitive material should require review.",
            decision="human_review_required",
            authority="model_proposed",
            prompt_eligible=False,
            risk="sensitive",
            reason="sensitive content requires review",
        ),
    )

    assert first.id == "memdec_20260619_190000_001"
    assert second.id == "memdec_20260619_190100_002"
    assert paths.memory_decisions_file == tmp_path / "life-loop" / "memory_decisions.jsonl"
    assert not paths.memory_store.exists()

    raw_rows = [
        json.loads(line)
        for line in paths.memory_decisions_file.read_text().splitlines()
        if line.strip()
    ]
    loaded = load_memory_decisions(paths.memory_decisions_file)

    assert [row["id"] for row in raw_rows] == [
        "memdec_20260619_190000_001",
        "memdec_20260619_190100_002",
    ]
    assert [decision.id for decision in loaded] == [
        "memdec_20260619_190000_001",
        "memdec_20260619_190100_002",
    ]
    assert loaded[0].prompt_eligible is True
    assert loaded[1].prompt_eligible is False
