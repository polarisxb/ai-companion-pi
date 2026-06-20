import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    JsonMemoryStore,
    load_memory_decisions,
    run_m8_memory_policy_ledger,
)


def decision_payload(**overrides):
    payload = {
        "schema_version": 1,
        "id": "memdec_20260620_090000_001",
        "conversation_id": "conv-m8",
        "source_turn_ids": ["turn-human-1", "turn-assistant-1"],
        "candidate_content": "Polaris prefers reports to start with conclusion, then evidence.",
        "memory_type": "semantic",
        "decision": "accepted",
        "authority": "memory_steward",
        "prompt_eligible": True,
        "risk": "low",
        "reason": "explicit low-risk user-stated preference",
        "evidence_refs": [
            {"artifact": "conversation", "id": "turn-human-1", "path": "conversations/conv-m8.jsonl"},
        ],
        "created_at": "2026-06-20T09:00:00",
    }
    payload.update(overrides)
    return payload


def write_steward_report(home: Path, decisions: list[dict]):
    path = home / "life-loop" / "m8_memory_steward_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "ok": True,
        "recommendation": "m8_memory_steward_readonly_ready",
        "decisions": decisions,
    }))
    return path


def read_json(path: Path):
    return json.loads(path.read_text())


def test_policy_ledger_accepts_low_risk_decision_and_writes_accepted_memory(tmp_path):
    paths = CompanionPaths(tmp_path)
    result = run_m8_memory_policy_ledger(paths, decisions=[decision_payload()])
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m8_memory_policy_ledger_ready"
    assert report["counts"]["ledger_appended"] == 1
    assert report["counts"]["accepted_memory_written"] == 1
    assert not paths.wake_events_file.exists()

    ledger = load_memory_decisions(paths.memory_decisions_file)
    assert len(ledger) == 1
    assert ledger[0].accepted_memory_id
    assert ledger[0].prompt_eligible is True

    memories = read_json(paths.memory_store)
    assert len(memories) == 1
    memory = memories[0]
    assert memory["content"] == "Polaris prefers reports to start with conclusion, then evidence."
    assert memory["authority"] == "user_asserted"
    assert memory["source_type"] == "user"
    assert memory["prompt_eligible"] is True
    assert memory["accepted_for_context"] is True
    assert memory["memory_decision_id"] == "memdec_20260620_090000_001"
    assert {"artifact": "memory_decision", "id": "memdec_20260620_090000_001"} in memory["evidence_refs"]
    assert JsonMemoryStore(paths.memory_store).recent_for_context(1)[0]["id"] == memory["id"]


def test_policy_ledger_keeps_sensitive_decision_out_of_accepted_memory(tmp_path):
    paths = CompanionPaths(tmp_path)
    sensitive = decision_payload(
        id="memdec_20260620_090000_002",
        candidate_content="my api key is [REDACTED_SECRET]",
        decision="quarantined",
        authority="memory_steward",
        prompt_eligible=False,
        risk="sensitive",
        reason="secret-like content requires quarantine",
    )

    result = run_m8_memory_policy_ledger(paths, decisions=[sensitive])
    report = result.to_dict()

    assert result.ok is True
    assert report["counts"]["ledger_appended"] == 1
    assert report["counts"]["accepted_memory_written"] == 0
    assert not paths.memory_store.exists()
    ledger = load_memory_decisions(paths.memory_decisions_file)
    assert ledger[0].decision == "quarantined"
    assert ledger[0].prompt_eligible is False


def test_policy_ledger_blocks_non_low_risk_auto_accepted_memory(tmp_path):
    paths = CompanionPaths(tmp_path)
    high_risk = decision_payload(
        id="memdec_20260620_090000_003",
        risk="sensitive",
        authority="evaluator_approved",
        reason="trusted authority but still not auto accepted in M8.3",
    )

    result = run_m8_memory_policy_ledger(paths, decisions=[high_risk])
    report = result.to_dict()

    assert result.ok is False
    assert result.recommendation == "inspect"
    assert "memory_policy_gate" in report["stop_reasons"]
    assert "low risk" in " ".join(report["errors"])
    assert not paths.memory_decisions_file.exists()
    assert not paths.memory_store.exists()


def test_policy_ledger_is_idempotent_for_existing_decision_ids(tmp_path):
    paths = CompanionPaths(tmp_path)
    first = run_m8_memory_policy_ledger(paths, decisions=[decision_payload()])
    second = run_m8_memory_policy_ledger(paths, decisions=[decision_payload()])

    assert first.ok is True
    assert second.ok is True
    assert second.to_dict()["counts"]["skipped_existing_decisions"] == 1
    assert second.to_dict()["counts"]["ledger_appended"] == 0
    assert len(load_memory_decisions(paths.memory_decisions_file)) == 1
    assert len(read_json(paths.memory_store)) == 1


def test_policy_ledger_cli_reads_steward_report_and_writes_report(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_steward_report(tmp_path, [decision_payload()])
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_memory_policy_ledger.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--companion-home", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_memory_policy_ledger_ready"
    assert (tmp_path / "life-loop" / "m8_memory_policy_ledger_report.json").exists()
    assert paths.memory_decisions_file.exists()
    assert paths.memory_store.exists()
    assert not paths.wake_events_file.exists()
