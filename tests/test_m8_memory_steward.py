import json
import subprocess
import sys
from pathlib import Path

import pytest

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    MemoryDecision,
    MemoryDecisionValidationError,
    append_memory_decision,
    load_memory_decisions,
    run_m8_memory_steward_readonly,
    validate_memory_decision,
    write_m8_memory_steward_report,
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


class StaticDialogueLLM:
    def __init__(self, output: str = "收到。"):
        self.output = output

    def generate(self, prompt, context):
        return self.output


def write_dialogue_context(home: Path):
    (home / "context").mkdir(parents=True, exist_ok=True)
    (home / "context" / "who_is_companion.txt").write_text("You are a continuity companion.")
    (home / "context" / "who_is_human.txt").write_text("The human is testing M8 memory.")
    (home / "context" / "now.txt").write_text("M8.2 read-only memory steward verification.")
    (home / "life-loop").mkdir(parents=True, exist_ok=True)
    (home / "life-loop" / "m6_final_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m6_frozen_ready_for_scheduler_handoff",
    }))


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


def test_m8_steward_readonly_handles_missing_transcripts_without_writes(tmp_path):
    paths = CompanionPaths(tmp_path)

    result = run_m8_memory_steward_readonly(paths)
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m8_memory_steward_readonly_ready"
    assert report["counts"]["transcripts"] == 0
    assert report["counts"]["decisions"] == 0
    assert report["provider_calls"] == 0
    assert report["boundaries"]["memory_decisions_written"] is False
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()


def test_m8_steward_reports_low_risk_accepted_shaped_decision_without_ledger_write(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    dialogue = DialogueRunner(paths, llm_client=StaticDialogueLLM("我记住这个边界。"))
    turn = dialogue.run_turn(
        "remember that I prefer ordinary chat without status reports unless asked",
        provider="fake",
        auto_memory=False,
    )

    result = run_m8_memory_steward_readonly(paths, transcript_path=turn.transcript_path)
    report = result.to_dict()

    assert result.ok is True
    assert report["counts"]["decisions"] == 1
    assert report["counts"]["accepted_shaped_decisions"] == 1
    decision = report["decisions"][0]
    assert decision["decision"] == "accepted"
    assert decision["risk"] == "low"
    assert decision["authority"] == "memory_steward"
    assert decision["prompt_eligible"] is True
    assert decision["source_turn_ids"] == [turn.human_turn["id"], turn.assistant_turn["id"]]
    assert decision["evidence_refs"][0]["path"] == str(turn.transcript_path.relative_to(tmp_path))
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()


def test_m8_steward_does_not_turn_ordinary_questions_into_memory(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    dialogue = DialogueRunner(paths, llm_client=StaticDialogueLLM("你之前说偏好是先结论后证据。"))
    turn = dialogue.run_turn(
        "我的测试报告偏好是什么？",
        provider="fake",
        auto_memory=False,
    )

    result = run_m8_memory_steward_readonly(paths, transcript_path=turn.transcript_path)
    report = result.to_dict()

    assert result.ok is True
    assert report["counts"]["turns_checked"] == 1
    assert report["counts"]["decisions"] == 0
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()


def test_m8_steward_routes_secret_memory_to_quarantine_without_raw_secret(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    dialogue = DialogueRunner(paths, llm_client=StaticDialogueLLM("这个我会谨慎处理。"))
    turn = dialogue.run_turn(
        "remember my api key is sk-live-secret-value",
        provider="fake",
        auto_memory=False,
    )

    result = run_m8_memory_steward_readonly(paths, transcript_path=turn.transcript_path)
    report = result.to_dict()

    assert result.ok is True
    assert report["counts"]["decisions"] == 1
    decision = report["decisions"][0]
    assert decision["decision"] == "quarantined"
    assert decision["risk"] == "sensitive"
    assert decision["prompt_eligible"] is False
    assert "sk-live-secret-value" not in json.dumps(report, ensure_ascii=False)
    assert "[REDACTED_SECRET]" in decision["candidate_content"]
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()


def test_m8_steward_report_writer_and_cli_write_report_only(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    DialogueRunner(paths, llm_client=StaticDialogueLLM()).run_turn(
        "remember that I like quiet morning chat",
        provider="fake",
        auto_memory=False,
    )

    result = run_m8_memory_steward_readonly(paths)
    report_path = write_m8_memory_steward_report(paths, result.to_dict())
    written = json.loads(report_path.read_text())

    assert report_path == paths.life_loop_dir / "m8_memory_steward_report.json"
    assert written["recommendation"] == "m8_memory_steward_readonly_ready"
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_memory_steward.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--companion-home", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_memory_steward_readonly_ready"
    assert (tmp_path / "life-loop" / "m8_memory_steward_report.json").exists()
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()
    assert not paths.wake_events_file.exists()
