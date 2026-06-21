import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from companion_core import (
    CompanionPaths,
    MemoryReviewError,
    append_memory_decisions,
    approve_memory_review_decision,
    load_memory_review_actions,
    load_memory_review_queue,
    reject_memory_review_decision,
    run_m8_memory_review_queue_check,
    write_m8_memory_review_queue_report,
)


def decision_payload(
    decision_id: str,
    *,
    decision: str = "human_review_required",
    risk: str = "relationship",
    prompt_eligible: bool = False,
    content: str = "Polaris used a relationship-defining phrase.",
) -> dict:
    return {
        "id": decision_id,
        "conversation_id": "conv-review",
        "source_turn_ids": ["turn-human", "turn-assistant"],
        "candidate_content": content,
        "memory_type": "semantic",
        "decision": decision,
        "authority": "memory_steward",
        "prompt_eligible": prompt_eligible,
        "risk": risk,
        "reason": "edge-case memory requires human review",
        "evidence_refs": [{"artifact": "conversation", "id": "turn-human"}],
        "created_at": "2026-06-21T10:00:00",
    }


def write_window_route_fixture(home: Path):
    window = home / "window" / "window.py"
    window.parent.mkdir(parents=True, exist_ok=True)
    window.write_text(
        "\n".join([
            'from flask import Flask',
            'app = Flask(__name__)',
            '@app.route("/life")',
            'def life(): return "life"',
            '@app.route("/memory-review")',
            'def memory_review(): return "review"',
            '@app.route("/memory-review/<decision_id>/approve", methods=["POST"])',
            'def approve(decision_id): return decision_id',
            '@app.route("/memory-review/<decision_id>/reject", methods=["POST"])',
            'def reject(decision_id): return decision_id',
            '@app.route("/memory-review/<decision_id>/edit", methods=["POST"])',
            'def edit(decision_id): return decision_id',
        ])
    )


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    module_path = Path(__file__).resolve().parents[1] / "window" / "window.py"
    spec = importlib.util.spec_from_file_location(f"window_m8_review_test_{uuid.uuid4().hex}", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_queue_shows_only_exception_memory_decisions(tmp_path):
    paths = CompanionPaths(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [
        decision_payload(
            "memdec_low",
            decision="accepted",
            risk="low",
            prompt_eligible=True,
            content="Polaris prefers concise replies.",
        ),
        decision_payload("memdec_review"),
        decision_payload(
            "memdec_rejected",
            decision="rejected",
            risk="medium",
            content="Unsupported model inference.",
        ),
    ])

    queue = load_memory_review_queue(paths)

    assert queue["counts"]["decisions"] == 3
    assert queue["counts"]["reviewable"] == 1
    assert queue["pending"][0]["id"] == "memdec_review"
    assert queue["pending"][0]["recommended_action"] == "human_judgment_required"


def test_approve_review_decision_writes_evaluator_approved_memory_and_action(tmp_path):
    paths = CompanionPaths(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_review")])

    result = approve_memory_review_decision(
        paths,
        "memdec_review",
        edited_content="Polaris wants this reviewed relationship note kept as an approved boundary.",
        note="approved after edit",
    )

    memory = result["accepted_memory"]
    assert memory["authority"] == "evaluator_approved"
    assert memory["prompt_eligible"] is True
    assert memory["accepted_for_context"] is True
    assert any(ref["artifact"] == "memory_review" for ref in memory["evidence_refs"])
    actions = load_memory_review_actions(paths.memory_review_actions_file)
    assert actions[0]["action"] == "edit_and_approve"
    assert actions[0]["accepted_memory_id"] == memory["id"]
    queue = load_memory_review_queue(paths)
    assert queue["counts"]["pending"] == 0
    assert queue["counts"]["reviewed"] == 1


def test_review_reject_keeps_decision_out_of_accepted_memory(tmp_path):
    paths = CompanionPaths(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_review")])

    result = reject_memory_review_decision(paths, "memdec_review", note="not useful")

    assert result["action"]["action"] == "reject"
    assert not paths.memory_store.exists()
    queue = load_memory_review_queue(paths)
    assert queue["counts"]["pending"] == 0
    assert queue["reviewed"][0]["latest_action"]["action"] == "reject"


def test_review_approval_requires_secret_like_content_to_be_edited(tmp_path):
    paths = CompanionPaths(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [
        decision_payload(
            "memdec_secret",
            decision="quarantined",
            risk="sensitive",
            content="my api key is sk-live-secret-value",
        )
    ])

    with pytest.raises(MemoryReviewError, match="secret-like content"):
        approve_memory_review_decision(paths, "memdec_secret")

    result = approve_memory_review_decision(paths, "memdec_secret", edited_content="Polaris keeps API keys out of memory.")
    assert result["accepted_memory"]["content"] == "Polaris keeps API keys out of memory."


def test_m8_memory_review_check_and_cli_write_report(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_window_route_fixture(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_review")])

    result = run_m8_memory_review_queue_check(paths)
    report_path = write_m8_memory_review_queue_report(paths, result.to_dict())

    assert result.ok is True
    assert result.recommendation == "m8_human_review_queue_ready"
    assert json.loads(report_path.read_text())["counts"]["pending"] == 1
    assert not paths.wake_events_file.exists()

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_memory_review.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--companion-home", str(tmp_path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_human_review_queue_ready"
    assert (tmp_path / "life-loop" / "m8_human_review_queue_report.json").exists()


def test_window_memory_review_routes_show_and_apply_review_actions(tmp_path, monkeypatch):
    paths = CompanionPaths(tmp_path)
    append_memory_decisions(paths.memory_decisions_file, [decision_payload("memdec_review")])
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()

    page = client.get("/memory-review")
    assert page.status_code == 200
    assert b"Memory Review" in page.data
    assert b"memdec_review" in page.data

    response = client.post(
        "/memory-review/memdec_review/edit",
        json={"content": "Polaris approved this relationship boundary after review."},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    memory = json.loads(paths.memory_store.read_text())[0]
    assert memory["authority"] == "evaluator_approved"
    assert memory["prompt_eligible"] is True
    assert not paths.wake_events_file.exists()
