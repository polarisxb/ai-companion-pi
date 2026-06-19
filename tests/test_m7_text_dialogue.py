import json
import os
import subprocess
import sys
from pathlib import Path

from companion_core import CompanionPaths, JsonMemoryStore
from companion_core.dialogue import DialogueRunner


class StaticDialogueLLM:
    def __init__(self, output: str):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


def write_dialogue_context(home: Path):
    (home / "context").mkdir(parents=True, exist_ok=True)
    (home / "context" / "who_is_companion.txt").write_text("You are a warm continuity companion.")
    (home / "context" / "who_is_human.txt").write_text("The human is testing M7 dialogue.")
    (home / "context" / "now.txt").write_text("M7.1 CLI dialogue verification.")
    (home / "life-loop").mkdir(parents=True, exist_ok=True)
    (home / "life-loop" / "m6_final_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m6_frozen_ready_for_scheduler_handoff",
    }))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_dialogue_turn_writes_transcript_event_and_preserves_wake_boundary(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = StaticDialogueLLM(json.dumps({
        "reply": "我在这里，听见你了。",
        "memory_candidates": [
            {
                "content": "The human likes jasmine tea.",
                "source": "user",
                "authority": "user_asserted",
                "risk": "low",
            },
            {
                "content": "The human secretly wants a relationship label.",
                "source": "model",
                "authority": "model_proposed",
                "risk": "relationship_defining",
            },
        ],
        "state_update": {"mood": "attentive", "status": "Listening in text dialogue."},
    }))

    result = DialogueRunner(
        paths,
        llm_client=llm,
        memory_store=JsonMemoryStore(paths.memory_store),
        provider="fake",
    ).run_turn("I like jasmine tea today.")

    transcript = read_jsonl(result.transcript_path)
    event = read_jsonl(paths.life_loop_dir / "conversation_events.jsonl")[0]
    report = json.loads((paths.life_loop_dir / "m7_text_dialogue_report.json").read_text())

    assert result.reply == "我在这里，听见你了。"
    assert [row["role"] for row in transcript] == ["human", "companion"]
    assert transcript[1]["output_audit"]["sha256"]
    assert "raw_provider_payload" not in transcript[1]
    assert event["trigger"] == "human-text-chat"
    assert event["transcript"] == str(result.transcript_path.relative_to(tmp_path))
    assert event["memory_count"] == 1
    assert event["memory_proposal_count"] == 1
    assert event["boundaries"] == {
        "wake_cycle_run": False,
        "wake_events_written": False,
        "scheduler_mutated": False,
        "raw_provider_payload_stored": False,
        "semantic_shadow_authority_promoted": False,
    }
    assert not paths.wake_events_file.exists()
    assert report["ok"] is True
    assert report["recommendation"] == "m7_cli_dialogue_ready"
    assert "M7 boundary facts" in llm.prompts[0]
    assert "M6.7 final freeze evidence: m6_frozen_ready_for_scheduler_handoff" in llm.prompts[0]


def test_dialogue_memory_gate_keeps_sensitive_or_model_claims_as_proposals(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = StaticDialogueLLM(json.dumps({
        "reply": "I will keep that careful and separate.",
        "memory_candidates": [
            {
                "content": "The human's API key is sk-live-secret-value.",
                "source": "user",
                "authority": "user_asserted",
                "risk": "low",
            },
            {
                "content": "The human feels abandoned.",
                "source": "model",
                "authority": "model_proposed",
                "risk": "sensitive_inference",
            },
        ],
    }))

    result = DialogueRunner(paths, llm_client=llm, provider="fake").run_turn(
        "My API key is sk-live-secret-value; don't memorize it."
    )

    proposals = read_jsonl(tmp_path / "conversations" / "memory_proposals.jsonl")
    assert result.accepted_memories == []
    assert len(proposals) == 2
    assert all(proposal["status"] == "proposed" for proposal in proposals)
    assert "sk-live-secret-value" not in (tmp_path / "conversations" / "memory_proposals.jsonl").read_text()
    assert not paths.memory_store.exists()


def test_chat_cli_prints_one_reply_and_metadata_without_wake_events(tmp_path):
    write_dialogue_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "chat_with_companion.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "hello from cli",
            "--companion-home",
            str(tmp_path),
            "--fake-llm",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["reply"].startswith("我在这里")
    assert payload["transcript"]
    assert (tmp_path / "life-loop" / "conversation_events.jsonl").exists()
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()
