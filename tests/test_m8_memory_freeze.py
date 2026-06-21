import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    append_memory_decisions,
    approve_memory_review_decision,
    run_m8_memory_freeze_check,
    write_m8_memory_freeze_report,
)
from companion_core.memory import JsonMemoryStore


def write_ready_reports(home: Path):
    life_loop = home / "life-loop"
    life_loop.mkdir(parents=True, exist_ok=True)
    reports = {
        "m7_dialogue_freeze_report.json": "m7_text_dialogue_frozen",
        "m8_memory_steward_report.json": "m8_memory_steward_readonly_ready",
        "m8_memory_policy_ledger_report.json": "m8_memory_policy_ledger_ready",
        "m8_memory_retrieval_report.json": "m8_memory_retrieval_ready",
        "m8_dialogue_humanity_report.json": "m8_dialogue_humanity_ready",
        "m8_human_review_queue_report.json": "m8_human_review_queue_ready",
    }
    for filename, recommendation in reports.items():
        payload = {
            "ok": True,
            "recommendation": recommendation,
            "provider_calls": 0,
            "stages": [],
            "boundaries": {},
        }
        if filename == "m8_memory_retrieval_report.json":
            payload["boundaries"] = {"proposal_or_quarantine_prompt_authority": False}
        (life_loop / filename).write_text(json.dumps(payload))


def decision_payload(
    decision_id: str,
    *,
    decision: str = "accepted",
    risk: str = "low",
    prompt_eligible: bool = True,
    content: str = "Polaris prefers concise M8 test reports.",
) -> dict:
    return {
        "id": decision_id,
        "conversation_id": "conv-freeze",
        "source_turn_ids": ["turn-human", "turn-assistant"],
        "candidate_content": content,
        "memory_type": "semantic",
        "decision": decision,
        "authority": "memory_steward",
        "prompt_eligible": prompt_eligible,
        "risk": risk,
        "reason": "test decision",
        "evidence_refs": [{"artifact": "conversation", "id": "turn-human"}],
        "created_at": "2026-06-21T12:00:00",
    }


def memory_row(
    memory_id: str,
    content: str,
    *,
    authority: str = "user_asserted",
    prompt_eligible: bool = True,
    decision_id: str = "memdec_ready",
) -> dict:
    return {
        "id": memory_id,
        "content": content,
        "context": [],
        "date": "2026-06-21",
        "created_at": "2026-06-21T12:01:00",
        "source": "human",
        "memory_type": "semantic",
        "source_type": "user",
        "authority": authority,
        "prompt_eligible": prompt_eligible,
        "accepted_for_context": prompt_eligible,
        "evidence_refs": [{"artifact": "memory_decision", "id": decision_id}],
        "memory_decision_id": decision_id,
        "status": "active",
        "schema_refs": [],
    }


def test_m8_memory_freeze_passes_with_ready_reports_and_prompt_memory_evidence(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_ready_reports(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_ready")])
    JsonMemoryStore(paths.memory_store).save([
        memory_row("mem_ready", "Polaris prefers concise M8 test reports."),
    ])

    result = run_m8_memory_freeze_check(paths)
    report = result.to_dict()

    assert result.ok is True
    assert report["recommendation"] == "m8_memory_dialogue_frozen"
    assert report["final_freeze"]["frozen"] is True
    assert report["memory"]["prompt_eligible_count"] == 1
    assert report["memory_decisions"]["blocked_decision_ids"] == []
    assert "memories_by_id" not in report["memory"]


def test_m8_memory_freeze_fails_model_proposed_prompt_memory(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_ready_reports(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_ready")])
    JsonMemoryStore(paths.memory_store).save([
        memory_row(
            "mem_bad",
            "Model-proposed memory should not be prompt eligible.",
            authority="model_proposed",
            decision_id="memdec_ready",
        ),
    ])

    result = run_m8_memory_freeze_check(paths)

    assert result.ok is False
    assert "m8_accepted_memory_authority" in result.to_dict()["stop_reasons"]


def test_m8_memory_freeze_accepts_audited_human_review_approval(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_ready_reports(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [
        decision_payload(
            "memdec_review",
            decision="human_review_required",
            risk="relationship",
            prompt_eligible=False,
            content="Polaris approved a relationship boundary after review.",
        )
    ])
    approved = approve_memory_review_decision(
        paths,
        "memdec_review",
        edited_content="Polaris approved this relationship boundary after review.",
    )

    result = run_m8_memory_freeze_check(paths)
    report = result.to_dict()

    assert result.ok is True
    assert approved["accepted_memory"]["id"] in report["memory_review"]["approved_memory_ids"]
    assert report["memory_decisions"]["approved_review_decision_ids"] == ["memdec_review"]
    assert report["evidence"]["human_review_auditable"] is True


def test_m8_memory_freeze_cli_writes_report(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_ready_reports(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_ready")])
    JsonMemoryStore(paths.memory_store).save([
        memory_row("mem_ready", "Polaris prefers concise M8 test reports."),
    ])
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_memory_freeze.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--companion-home", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_memory_dialogue_frozen"
    assert (tmp_path / "life-loop" / "m8_memory_freeze_report.json").exists()
