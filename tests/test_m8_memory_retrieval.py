import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    JsonMemoryStore,
    assemble_dialogue_memory_context,
)


class StaticDialogueLLM:
    def __init__(self, output: str = "我在这里。"):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


def memory_row(
    memory_id: str,
    content: str,
    *,
    prompt_eligible: bool = True,
    accepted_for_context: bool = True,
    authority: str = "user_asserted",
    memory_type: str = "semantic",
    created_at: str = "2026-06-20T09:00:00",
) -> dict:
    return {
        "id": memory_id,
        "content": content,
        "context": [],
        "date": created_at[:10],
        "created_at": created_at,
        "source": "human",
        "memory_type": memory_type,
        "source_type": "user",
        "authority": authority,
        "prompt_eligible": prompt_eligible,
        "accepted_for_context": accepted_for_context,
        "evidence_refs": [{"artifact": "memory_decision", "id": f"memdec_{memory_id}"}],
        "status": "active",
        "schema_refs": [],
    }


def write_dialogue_context(home: Path):
    (home / "context").mkdir(parents=True, exist_ok=True)
    (home / "context" / "who_is_companion.txt").write_text("You are a continuity companion.")
    (home / "context" / "who_is_human.txt").write_text("The human is testing M8 retrieval.")
    (home / "context" / "now.txt").write_text("M8.4 retrieval verification.")
    (home / "life-loop").mkdir(parents=True, exist_ok=True)
    (home / "life-loop" / "m6_final_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m6_frozen_ready_for_scheduler_handoff",
    }))


def write_memory_store(paths: CompanionPaths):
    JsonMemoryStore(paths.memory_store).save([
        memory_row(
            "mem_style",
            "Polaris prefers chat replies to start with the conclusion, then evidence.",
            created_at="2026-06-20T10:00:00",
        ),
        memory_row(
            "mem_project",
            "The project is currently in M7.6 final freeze stage.",
            created_at="2026-06-20T11:00:00",
        ),
        memory_row(
            "mem_quarantine",
            "Quarantined secret-like memory must not enter prompt.",
            prompt_eligible=False,
            accepted_for_context=False,
            authority="model_proposed",
            created_at="2026-06-20T12:00:00",
        ),
    ])


def test_retrieval_filters_to_prompt_eligible_memory_and_hides_project_state_for_casual_chat(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_memory_store(paths)

    result = assemble_dialogue_memory_context(paths, "hello, let's talk casually")

    assert [memory["id"] for memory in result.memories] == ["mem_style"]
    filtered = {item["id"]: item["reason"] for item in result.filtered}
    assert filtered["mem_project"] == "project_state_filtered_without_status_query"
    assert filtered["mem_quarantine"] == "not_prompt_eligible_accepted_memory"
    assert result.retrieved[0].reasons == [
        "prompt_eligible_accepted_memory",
        "style_or_preference_memory",
    ]


def test_retrieval_allows_project_state_when_status_is_requested(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_memory_store(paths)

    result = assemble_dialogue_memory_context(paths, "what is the current M8 status?")

    selected_ids = [memory["id"] for memory in result.memories]
    assert "mem_project" in selected_ids
    assert any("status_query_allows_project_state" in item.reasons for item in result.retrieved)


def test_dialogue_prompt_uses_retrieval_assembler(tmp_path):
    write_dialogue_context(tmp_path)
    paths = CompanionPaths(tmp_path)
    write_memory_store(paths)
    llm = StaticDialogueLLM("收到。")

    DialogueRunner(paths, llm_client=llm, memory_store=JsonMemoryStore(paths.memory_store)).run_turn(
        "hello, just checking in",
        provider="fake",
    )

    prompt = llm.prompts[0]
    assert "Polaris prefers chat replies to start with the conclusion, then evidence." in prompt
    assert "The project is currently in M7.6 final freeze stage." not in prompt
    assert "Quarantined secret-like memory must not enter prompt." not in prompt
    assert not paths.wake_events_file.exists()


def test_m8_retrieval_cli_writes_report_without_runtime_side_effects(tmp_path):
    paths = CompanionPaths(tmp_path)
    write_memory_store(paths)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_memory_retrieval.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--companion-home", str(tmp_path), "--query", "casual chat"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_memory_retrieval_ready"
    assert payload["counts"]["selected"] == 1
    assert (tmp_path / "life-loop" / "m8_memory_retrieval_report.json").exists()
    assert not paths.wake_events_file.exists()
