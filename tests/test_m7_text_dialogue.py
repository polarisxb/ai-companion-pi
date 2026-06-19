import json
import subprocess
import sys
from pathlib import Path

import pytest

from companion_core import CompanionPaths, JsonMemoryStore
from companion_core.dialogue import DialoguePreflightError, DialogueRunner
from companion_core.dialogue_replay import check_dialogue_transcript


class StaticDialogueLLM:
    def __init__(self, output: str):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


class FailingThenStaticDialogueLLM:
    def __init__(self, output: str):
        self.output = output
        self.calls = 0
        self.prompts = []

    def generate(self, prompt, context):
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls == 1:
            raise RuntimeError("provider temporarily unavailable")
        return self.output


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


def test_interactive_cli_keeps_one_conversation_and_appends_turns(tmp_path):
    write_dialogue_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "chat_with_companion.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(tmp_path),
            "--fake-response",
            "收到。\n===DIALOGUE_METADATA===\n{}",
            "--interactive",
            "--json",
        ],
        input="first turn\nsecond turn\nquit\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payloads = [json.loads(line) for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    assert len(payloads) == 2
    assert payloads[0]["conversation_id"] == payloads[1]["conversation_id"]
    transcript = read_jsonl(Path(payloads[0]["transcript"]))
    assert [row["role"] for row in transcript] == ["human", "assistant", "human", "assistant"]
    assert {row["conversation_id"] for row in transcript} == {payloads[0]["conversation_id"]}
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()


def test_interactive_cli_keeps_memory_candidates_as_proposals(tmp_path):
    write_dialogue_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "chat_with_companion.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(tmp_path),
            "--fake-response",
            "收到。",
            "--interactive",
            "--json",
        ],
        input="remember that I like quiet mornings\nquit\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (tmp_path / "memory-server" / "memory_store.json").exists()
    proposals = read_jsonl(tmp_path / "life-loop" / "memory_proposals.jsonl")
    assert proposals[0]["status"] == "proposed"
    assert proposals[0]["accepted"] is False


def test_failed_turn_preserves_human_input_without_assistant_and_can_retry(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = FailingThenStaticDialogueLLM("重试后我在这里。")
    runner = DialogueRunner(paths, llm_client=llm)

    with pytest.raises(RuntimeError):
        runner.run_turn("please keep this available for retry", conversation_id="retry-check", provider="fake")
    result = runner.run_turn("please keep this available for retry", conversation_id="retry-check", provider="fake")

    transcript = read_jsonl(result.transcript_path)
    assert [row["role"] for row in transcript] == ["human", "human", "assistant"]
    assert transcript[0]["status"] == "failed"
    assert transcript[0]["content"] == "please keep this available for retry"
    assert transcript[1]["status"] == "completed"
    assert transcript[2]["role"] == "assistant"


def test_failed_turn_is_not_loaded_into_replacement_prompt_context(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    llm = FailingThenStaticDialogueLLM("replacement accepted")
    runner = DialogueRunner(paths, llm_client=llm)

    with pytest.raises(RuntimeError):
        runner.run_turn("failed text should stay out of prompt context", conversation_id="replacement", provider="fake")
    runner.run_turn("replacement text", conversation_id="replacement", provider="fake")

    assert "failed text should stay out of prompt context" not in llm.prompts[-1]
    assert "replacement text" in llm.prompts[-1]


def test_dialogue_replay_check_validates_transcript_without_provider_calls(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    result = DialogueRunner(paths, llm_client=StaticDialogueLLM("我在这里。")).run_turn(
        "hello transcript check",
        provider="fake",
    )

    check = check_dialogue_transcript(paths, result.transcript_path)

    assert check.ok is True
    payload = check.to_dict()
    assert payload["recommendation"] == "m7_dialogue_transcript_ready"
    assert payload["provider_calls"] == 0
    assert payload["rows_checked"] == 2


def test_dialogue_replay_check_rejects_raw_payload_and_failed_assistant(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    transcript = paths.conversations_dir / "bad.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        "\n".join([
            json.dumps({
                "id": "h1",
                "conversation_id": "bad",
                "role": "human",
                "status": "failed",
                "created_at": "2026-06-19T00:00:00",
                "content": "hello",
                "input_hash": "sha256:bad",
                "output_hash": None,
                "raw_output_stored": False,
            }),
            json.dumps({
                "id": "a1",
                "conversation_id": "bad",
                "role": "assistant",
                "status": "completed",
                "created_at": "2026-06-19T00:00:01",
                "content": "should not exist",
                "input_hash": "sha256:bad",
                "output_hash": "sha256:bad",
                "raw_output_stored": False,
                "raw_provider_payload": {"secret": "payload"},
            }),
        ])
        + "\n"
    )
    (paths.life_loop_dir / "conversation_events.jsonl").write_text(json.dumps({
        "id": "e1",
        "conversation_id": "bad",
        "status": "failed",
        "transcript": str(transcript.relative_to(tmp_path)),
        "turn_count": 1,
        "raw_output_stored": False,
        "boundaries": {
            "wake_cycle_run": False,
            "wake_events_written": False,
            "scheduler_mutated": False,
            "raw_provider_payload_stored": False,
            "semantic_shadow_authority_promoted": False,
        },
    }) + "\n")

    check = check_dialogue_transcript(paths, transcript)

    assert check.ok is False
    assert any("assistant turn follows failed human input" in error for error in check.errors)
    assert any("raw provider payload field is not allowed" in error for error in check.errors)


def test_m7_memory_proposal_gate_reports_proposal_boundaries(tmp_path):
    from companion_core import run_m7_memory_proposal_gate

    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    runner = DialogueRunner(paths, llm_client=StaticDialogueLLM("我会把敏感内容留在提案里。"))
    result = runner.run_turn("remember my api key is sk-live-secret-value", provider="fake")

    report = run_m7_memory_proposal_gate(paths)
    saved = json.loads((paths.life_loop_dir / "m7_memory_proposal_report.json").read_text())

    assert report["ok"] is True
    assert saved["recommendation"] == "m7_memory_proposals_ready"
    assert report["accepted_memory_count"] == 0
    assert report["proposal_memory_count"] == 1
    assert report["proposal_source_link_count"] == 1
    assert report["proposal_prompt_authoritative_count"] == 0
    assert report["proposal_separate_from_accepted_count"] == 1
    assert report["prompt_authority_status"] == "proposal_only"
    assert report["proposals"][0]["conversation_id"] == result.conversation_id
    assert report["proposals"][0]["source_turn_id"] == result.human_turn["id"]
    assert report["boundaries"]["proposal_acceptance_path_added"] is False
    assert not paths.wake_events_file.exists()


def test_m7_memory_proposal_gate_rejects_prompt_authoritative_proposal(tmp_path):
    from companion_core import run_m7_memory_proposal_gate

    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    transcript = paths.conversations_dir / "gate.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({
        "id": "turn_human",
        "conversation_id": "gate",
        "role": "human",
        "status": "completed",
        "content": "remember this",
    }) + "\n")
    paths.memory_proposals_file.parent.mkdir(parents=True, exist_ok=True)
    paths.memory_proposals_file.write_text(json.dumps({
        "id": "memprop_bad",
        "conversation_id": "gate",
        "source_turn_id": "turn_human",
        "status": "proposed",
        "accepted": False,
        "prompt_eligible": True,
    }) + "\n")

    report = run_m7_memory_proposal_gate(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert "proposal_prompt_authoritative:memprop_bad" in report["stop_reasons"]


def test_dashboard_chat_get_and_post_json_reuse_dialogue_runner(tmp_path, monkeypatch):
    write_dialogue_context(tmp_path)
    monkeypatch.setenv("COMPANION_HOME", str(tmp_path))
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m7_chat_test", module_path)
    window = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(window)
    monkeypatch.setattr(window, "create_llm_client", lambda *args, **kwargs: StaticDialogueLLM("我在窗口里。"))
    client = window.app.test_client()

    get_response = client.get("/chat")
    post_response = client.post("/chat/send", json={"message": "hello window", "provider": "fake"})

    assert get_response.status_code == 200
    assert b"/chat/send" in get_response.data
    assert post_response.status_code == 200
    payload = post_response.get_json()
    assert payload["ok"] is True
    assert payload["reply"] == "我在窗口里。"
    assert payload["conversation_id"]
    chat_response = client.get(f"/chat?conversation_id={payload['conversation_id']}")
    html = chat_response.data.decode()
    assert "hello window" in html
    assert "我在窗口里。" in html
    assert "fake" in html
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()


def test_dashboard_chat_error_preserves_failed_input(tmp_path, monkeypatch):
    write_dialogue_context(tmp_path)
    monkeypatch.setenv("COMPANION_HOME", str(tmp_path))
    import importlib.util

    module_path = Path(__file__).resolve().parents[1] / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m7_chat_error_test", module_path)
    window = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(window)

    class BrokenLLM:
        def generate(self, prompt, context):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(window, "create_llm_client", lambda *args, **kwargs: BrokenLLM())
    response = window.app.test_client().post(
        "/chat/send",
        data={"message": "please preserve this", "provider": "fake"},
    )

    assert response.status_code == 500
    html = response.data.decode()
    assert "please preserve this" in html
    assert "provider failed" in html
