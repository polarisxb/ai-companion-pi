import json
import subprocess
import sys
from pathlib import Path

import pytest

from companion_core import CompanionPaths, JsonMemoryStore
from companion_core.dialogue import DialoguePreflightError, DialogueRunner


class StaticDialogueLLM:
    def __init__(self, output: str):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


class FailingDialogueLLM:
    def __init__(self, error: Exception):
        self.error = error
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        raise self.error


class SequenceDialogueLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def write_dialogue_context(home: Path, *, include_m6_freeze: bool = True):
    (home / "context").mkdir(parents=True, exist_ok=True)
    (home / "context" / "who_is_companion.txt").write_text("You are a warm continuity companion.")
    (home / "context" / "who_is_human.txt").write_text("The human is testing M7 dialogue.")
    (home / "context" / "now.txt").write_text("M7.1 CLI dialogue verification.")
    (home / "life-loop").mkdir(parents=True, exist_ok=True)
    if include_m6_freeze:
        (home / "life-loop" / "m6_final_freeze_report.json").write_text(json.dumps({
            "ok": True,
            "recommendation": "m6_frozen_ready_for_scheduler_handoff",
        }))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_dialogue_turn_writes_transcript_event_report_and_preserves_wake_boundary(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = StaticDialogueLLM("""我在这里，听见你了。
===DIALOGUE_METADATA===
{"companion_state": {"mood": "attentive", "status": "Listening in text dialogue."}, "memory_proposals": [{"content": "The human wants a relationship label.", "reason": "relationship-defining"}]}
""")

    result = DialogueRunner(
        paths,
        llm_client=llm,
        memory_store=JsonMemoryStore(paths.memory_store),
    ).run_turn("I like jasmine tea today.", provider="fake")

    transcript = read_jsonl(result.transcript_path)
    event = read_jsonl(paths.life_loop_dir / "conversation_events.jsonl")[0]
    report = json.loads((paths.life_loop_dir / "m7_text_dialogue_report.json").read_text())

    assert result.reply == "我在这里，听见你了。"
    assert [row["role"] for row in transcript] == ["human", "assistant"]
    assert transcript[1]["output_hash"].startswith("sha256:")
    assert transcript[1]["raw_output_stored"] is False
    assert "DIALOGUE_METADATA" not in transcript[1]["content"]
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
    assert report["m6_final_freeze"]["recommendation"] == "m6_frozen_ready_for_scheduler_handoff"
    assert "This is not a wake cycle" in llm.prompts[0]


def test_dialogue_memory_gate_keeps_sensitive_or_model_claims_as_proposals(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = StaticDialogueLLM("I will keep that careful and separate.")

    result = DialogueRunner(paths, llm_client=llm).run_turn(
        "remember my api key is sk-live-secret-value",
        provider="fake",
    )

    proposals = read_jsonl(paths.memory_proposals_file)
    assert result.accepted_memories == []
    assert len(proposals) == 1
    assert proposals[0]["status"] == "proposed"
    assert proposals[0]["accepted"] is False
    proposal_text = paths.memory_proposals_file.read_text()
    assert "sk-live-secret-value" not in proposal_text
    assert "[REDACTED_SECRET]" in proposal_text
    assert not paths.memory_store.exists()


def test_real_provider_dialogue_requires_m6_final_freeze_before_provider_call(tmp_path):
    write_dialogue_context(tmp_path, include_m6_freeze=False)
    paths = CompanionPaths(tmp_path)
    llm = StaticDialogueLLM("should not be called")

    with pytest.raises(DialoguePreflightError):
        DialogueRunner(paths, llm_client=llm).run_turn("hello", provider="deepseek")

    assert llm.prompts == []
    report = json.loads((paths.life_loop_dir / "m7_text_dialogue_report.json").read_text())
    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert report["stop_reasons"] == ["m6_final_freeze_not_ready"]


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
            "--fake-response",
            "我在这里。\n===DIALOGUE_METADATA===\n{}",
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["reply"] == "我在这里。"
    assert payload["transcript"]
    assert (tmp_path / "life-loop" / "conversation_events.jsonl").exists()
    assert (tmp_path / "life-loop" / "m7_text_dialogue_report.json").exists()
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()


def test_interactive_cli_reuses_conversation_id_across_turns(tmp_path):
    write_dialogue_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "chat_with_companion.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--interactive",
            "--companion-home",
            str(tmp_path),
            "--conversation-id",
            "conv_cli_repl_test",
            "--fake-response",
            "我在这里。\n===DIALOGUE_METADATA===\n{}",
            "--json",
        ],
        input="I prefer jasmine tea\nsecond turn\nquit\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert [reply["conversation_id"] for reply in replies] == ["conv_cli_repl_test", "conv_cli_repl_test"]
    transcript = tmp_path / "conversations" / "conv_cli_repl_test.jsonl"
    rows = read_jsonl(transcript)
    assert [row["role"] for row in rows] == ["human", "assistant", "human", "assistant"]
    assert {row["conversation_id"] for row in rows} == {"conv_cli_repl_test"}
    assert not (tmp_path / "memory-server" / "memory_store.json").exists()
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()


def test_failed_provider_turn_preserves_human_input_without_assistant_turn(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = FailingDialogueLLM(RuntimeError("provider unavailable"))

    with pytest.raises(RuntimeError):
        DialogueRunner(paths, llm_client=llm).run_turn(
            "please keep this failed input available",
            conversation_id="conv_failure_test",
            provider="fake",
        )

    transcript = read_jsonl(tmp_path / "conversations" / "conv_failure_test.jsonl")
    events = read_jsonl(paths.conversation_events_file)
    assert [row["role"] for row in transcript] == ["human"]
    assert transcript[0]["content"] == "please keep this failed input available"
    assert transcript[0]["turn_status"] == "failed"
    assert transcript[0]["raw_output_stored"] is False
    assert events[0]["status"] == "failed"
    assert events[0]["turn_count"] == 0
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()


def test_failed_provider_turn_is_not_loaded_as_recent_context_until_retry(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = SequenceDialogueLLM([RuntimeError("provider unavailable"), "recovered"])
    runner = DialogueRunner(paths, llm_client=llm)

    with pytest.raises(RuntimeError):
        runner.run_turn("failed input should not become context", conversation_id="conv_retry_test", provider="fake")

    runner.run_turn("new input", conversation_id="conv_retry_test", provider="fake")

    assert len(llm.prompts) == 2
    assert "failed input should not become context" not in llm.prompts[1]
    transcript = read_jsonl(tmp_path / "conversations" / "conv_retry_test.jsonl")
    assert [row["role"] for row in transcript] == ["human", "human", "assistant"]
    assert transcript[0]["turn_status"] == "failed"


def test_dialogue_replay_check_validates_transcript_and_events_read_only(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    result = DialogueRunner(paths, llm_client=StaticDialogueLLM("我在这里。")).run_turn(
        "hello replay",
        conversation_id="conv_replay_test",
        provider="fake",
    )
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    script = Path(__file__).resolve().parents[1] / "scripts" / "dialogue_replay_check.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            str(result.transcript_path),
            "--companion-home",
            str(tmp_path),
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "m7_dialogue_transcript_ready"
    assert payload["transcript_rows"] == 2
    assert payload["event_count"] == 1
    assert before == after


def test_dialogue_replay_check_rejects_raw_payload_and_bad_hash(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    transcript = tmp_path / "conversations" / "bad.jsonl"
    transcript.write_text(json.dumps({
        "id": "turn_bad_human",
        "conversation_id": "bad",
        "role": "human",
        "created_at": "2026-06-19T00:00:00",
        "content": "hello",
        "provider": "fake",
        "memory_mode": "json",
        "input_hash": "sha256:not-the-hash",
        "output_hash": None,
        "raw_output_stored": True,
        "raw_provider_payload": {"secret": "payload"},
        "memory_proposal_ids": [],
    }) + "\n")

    from companion_core.dialogue_replay import check_dialogue_transcript

    result = check_dialogue_transcript(paths, transcript)

    assert result.ok is False
    assert any("raw_output_stored_not_false" in problem for problem in result.problems)
    assert any("raw_provider_payload_present" in problem for problem in result.problems)
    assert any("input_hash_mismatch" in problem for problem in result.problems)
