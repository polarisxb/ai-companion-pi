import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

import companion_core.llm as llm_module
import companion_core.provider_check as provider_check_module
from companion_core import (
    CompanionPaths,
    DialogueEngine,
    FakeLLMClient,
    LifeLoopRunner,
    ReplayRunner,
    SemanticShadowWriter,
    append_wake_event,
    check_runtime_readiness,
    load_local_secrets,
    load_wake_events,
    run_m3_final_freeze,
    run_m3_release_gate,
    run_m4_deploy_check,
    run_m4_observation_check,
    run_m4_post_change_guard,
    run_m4_runtime_validation,
    run_m4_wake_trial,
    run_m5_quality_check,
    run_m5_final_freeze,
    run_m5_quality_release_gate,
    run_m5_quality_trial,
    run_m6_pi_manual_wake_trial,
    run_m6_pi_observation_check,
    run_m6_preflight_check,
    run_m6_recovery_drill,
    run_m6_scheduler_readiness_check,
    run_m6_final_freeze_check,
    run_pi_predeploy_check,
)
from companion_core.llm import (
    ClaudeCliClient,
    ClaudeCliError,
    ClaudeCliTimeoutError,
    ClaudeCliUnavailableError,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    HttpLLMError,
    LLMProviderConfigError,
    OllamaClient,
    OpenAICompatibleClient,
    create_llm_client,
)
from companion_core.memory import JsonMemoryStore, MemoryEntry, SemanticFirstMemoryStore
from companion_core.memory_policy import evaluate_memory_proposal
from companion_core.provider_check import check_llm_provider
from companion_core.parser import parse_companion_state, parse_memory_lines
from companion_core.parser import parse_grounding_claims
from companion_core.requests import RequestProposal, create_request, update_requests
from companion_core.state import has_state_update, merge_companion_state
from companion_core.trial_summary import build_trial_summary


def write_minimal_context(home: Path):
    context_dir = home / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "who_is_companion.txt").write_text("You are a continuity-focused companion.")
    (context_dir / "who_is_human.txt").write_text("The human is developing your internal life loop.")
    (context_dir / "now.txt").write_text("First milestone: memory, proactive expression, self-narrative.")


def write_m4_runtime_files(home: Path):
    for relative in (
        "scripts/run_wake_cycle.py",
        "scripts/start_window.sh",
        "scripts/start_memory_http.sh",
        "window/window.py",
        "memory-server/memory_server_http.py",
    ):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test runtime file\n")


def write_m4_dashboard_app(home: Path, extra_routes: str = ""):
    path = home / "window" / "window.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([
            "from flask import Flask",
            "app = Flask(__name__)",
            "",
            "@app.route('/life')",
            "def life():",
            "    return 'life'",
            "",
            extra_routes,
            "",
        ])
    )


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = Path(__file__).resolve().parents[1] / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_internal_loop_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaticLLMClient:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt, context):
        return self.output


class CapturingLLMClient:
    def __init__(self, output: str):
        self.output = output
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.output


class SequencedLLMClient:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        if not self.outputs:
            raise AssertionError("SequencedLLMClient exhausted")
        return self.outputs.pop(0)


class FakeSemanticShadowStore:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.memories = []
        self.saved = False

    def store_memory(
        self,
        *,
        content: str,
        context: list | None = None,
        intensity: int = 3,
        valence: int = 3,
        significance: int = 3,
        source: str = "manual",
        metadata: dict | None = None,
    ):
        memory = {
            "id": f"shadow_{len(self.memories) + 1}",
            "content": content,
            "context": context or [],
            "source": source,
            "likert": {
                "intensity": intensity,
                "valence": valence,
                "significance": significance,
            },
        }
        memory.update(metadata or {})
        self.memories.append(memory)
        return memory

    def save(self):
        self.saved = True
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps(self.memories, indent=2))


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def authorized_capsule_item(field: str, content: str) -> dict:
    return {
        "field": field,
        "content": content,
        "source_refs": [{"artifact": "test", "content_hash": "abc"}],
        "source_type": "user",
        "authority": "user_asserted",
        "prompt_eligible": True,
        "ttl_wakes": None,
    }


def trusted_short_term_capsule_item(field: str, content: str, ttl_wakes: int = 2) -> dict:
    return {
        "field": field,
        "content": content,
        "source_refs": [{"artifact": "test", "content_hash": f"{field}-short"}],
        "source_type": "user",
        "authority": "user_asserted",
        "prompt_eligible": True,
        "ttl_wakes": ttl_wakes,
    }


def semantic_shadow_wake_output() -> str:
    return """===JOURNAL===
我把这次记录限制在可验证的边界里：事实只从明确来源进入长期记忆，语气和温度只留在当下表达与日志中。这个唤醒专门检查授权链、证据引用和旁路落盘是否互不混淆；即使旁路存储可用，它也不能改变下一次提示词，也不能把展示性文字升级成事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证语义记忆旁路的权限边界。"}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
USER | The human is developing your internal life loop.

===REQUESTS===
NOREQUESTS
"""


def capsule_contents(capsule: dict, field: str) -> list[str]:
    return [
        item["content"]
        for item in capsule.get("items", [])
        if item.get("field") == field
    ]


def supported_current_context_output() -> str:
    return """===JOURNAL===
今天只需要平稳收束，并等待用户给出下一步事实。我会按当前上下文保持安静。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在按当前上下文保持安静。"}

===CONTEXT_DELTA===
{"current_focus": ["按当前上下文保持安静。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "current_context",
      "claim": "今天只需要平稳收束，并等待用户给出下一步事实。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


def unsupported_stable_fact_output() -> str:
    return """===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


def test_three_fake_wake_cycles_preserve_internal_life_loop(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(paths, llm_client=FakeLLMClient())

    results = [runner.run_once(trigger="test", provider="fake") for _ in range(3)]

    journals = sorted((tmp_path / "journals").glob("wakeup_*.md"))
    assert len(journals) == 3
    assert all(result.journal_path.exists() for result in results)
    assert "nothing yet" in journals[-1].read_text()

    memories = json.loads((tmp_path / "memory-server" / "memory_store.json").read_text())
    assert len(memories) == 3
    assert all(memory["id"].startswith("mem_") for memory in memories)
    assert memories[-1]["content"] == "Cycle 3 continuity memory"
    assert all(memory["memory_type"] == "reflection" for memory in memories)
    assert all(memory["source_type"] == "model" for memory in memories)
    assert all(memory["authority"] == "model_proposed" for memory in memories)
    assert all(memory["prompt_eligible"] is False for memory in memories)
    assert all(memory["accepted_for_context"] is False for memory in memories)

    requests = json.loads((tmp_path / "requests" / "requests.json").read_text())
    assert len(requests) == 3
    assert requests[0]["title"] == "Internal loop checkpoint 1"
    assert len({request["id"] for request in requests}) == 3
    assert (tmp_path / "memory-server" / "memory_store.lock").exists()

    events = load_wake_events(paths.wake_events_file)
    assert len(events) == 3
    assert all(event["status"] == "completed" for event in events)
    assert events[-1]["journal"] == str(results[-1].journal_path.relative_to(tmp_path))
    assert events[-1]["memory_ids"] == [results[-1].memories[0]["id"]]
    assert events[-1]["request_ids"] == [results[-1].requests[0]["id"]]
    assert events[-1]["provider"] == "fake"
    assert events[-1]["companion_state_updated"] is True
    assert events[-1]["accepted_context"]["memory_ids"] == []
    assert events[-1]["memory_policy"]["accepted"] == 1
    assert events[-1]["memory_policy"]["prompt_eligible"] == 0

    companion_state = json.loads((tmp_path / "life-loop" / "companion_state.json").read_text())
    assert companion_state["mood"] == "steady"
    assert "The human prefers pragmatic, direct engineering progress." in companion_state["preference_notes"]

    status = json.loads((tmp_path / "window" / "status.json").read_text())
    assert status["mood"] == "steady"
    assert "fake waking 3" in status["message"]

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/requests")
    assert response.status_code == 200
    assert b"Internal loop checkpoint" in response.data

    life_response = window.app.test_client().get("/life")
    assert life_response.status_code == 200
    assert b"Internal Life Loop" in life_response.data
    assert b"completed" in life_response.data
    assert b"test" in life_response.data
    assert b"last provider" in life_response.data
    assert b"fake" in life_response.data
    assert b"Companion State" in life_response.data
    assert b"steady" in life_response.data
    assert b"I completed fake waking 3 and feel my continuity getting clearer." in life_response.data


def test_request_cli_uses_shared_schema_and_import_path(tmp_path):
    journals_dir = tmp_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "wakeup_2026-06-11_10-00-00.md").write_text("one")
    (journals_dir / "wakeup_2026-06-11_14-00-00.md").write_text("two")

    env = os.environ.copy()
    env["COMPANION_HOME"] = str(tmp_path)
    script = Path(__file__).resolve().parents[1] / "requests" / "create_request.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--type",
            "fyi",
            "--title",
            "Shared request writer",
            "--body",
            "The legacy CLI should use companion_core persistence.",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    requests = json.loads((tmp_path / "requests" / "requests.json").read_text())
    assert len(requests) == 1
    assert requests[0]["id"].startswith("req_")
    assert requests[0]["status"] == "pending"
    assert requests[0]["waking_number"] == 2


def test_request_parser_normalizes_model_variants(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
    llm_client=StaticLLMClient("""===JOURNAL===
I am testing request normalization with enough concrete detail to satisfy the quality gate before request persistence is exercised.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "focused", "status": "Testing request normalization."}

===MEMORY===
SELF | Normalized request fields should persist.

===REQUESTS===
type: FYI
title: Case normalization
body: Type and priority can arrive capitalized from the model.
priority: HIGH
"""),
    )

    result = runner.run_once(trigger="test")

    assert result.request_errors == []
    requests = json.loads((tmp_path / "requests" / "requests.json").read_text())
    assert requests[0]["type"] == "fyi"
    assert requests[0]["priority"] == "high"


def test_companion_state_layers_are_prompted_persisted_and_reflected_in_status(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.companion_state_file.write_text(json.dumps({
        "version": 1,
        "mood": "curious",
        "status": "I am trying to understand the human better.",
        "relationship_notes": ["We are building a more continuous companion together."],
        "preference_notes": ["The human wants a restrained but warm companion voice."],
        "self_notes": ["I am learning from each wake cycle."],
        "updated_at": "2026-06-11T00:00:00",
    }))
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            authorized_capsule_item(
                "human_preferences",
                "The human wants a restrained but warm companion voice.",
            ),
            authorized_capsule_item(
                "facts",
                "The Python life loop is being tested before cron handoff.",
            ),
        ],
    }))
    llm = CapturingLLMClient("""===JOURNAL===
I woke with a clearer sense of the relationship and my own state.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{
  "mood": "focused",
  "status": "I feel steady and close to the work we are doing together.",
  "relationship_notes": ["The human and I are prioritizing companion continuity before cron handoff."],
  "preference_notes": ["The human values relationship memory and restrained warmth most."],
  "self_notes": ["I am practicing concise emotional self-reporting."]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    runner = LifeLoopRunner(paths, llm_client=llm)

    result = runner.run_once(trigger="state-test", provider="fake")

    assert "The human wants a restrained but warm companion voice." in llm.prompts[0]
    assert "The Python life loop is being tested before cron handoff." in llm.prompts[0]
    assert "I am trying to understand the human better." not in llm.prompts[0]
    assert "We are building a more continuous companion together." not in llm.prompts[0]
    assert "I am learning from each wake cycle." not in llm.prompts[0]
    assert "Dashboard status text is intentionally omitted from prompt context." in llm.prompts[0]
    assert "Relationship/preference/self note prose is intentionally omitted from prompt context." in llm.prompts[0]
    assert "relationship_notes: 1 stored" not in llm.prompts[0]
    assert "Prioritize remembering the human relationship and preferences." in llm.prompts[0]
    assert "Use the journal for full self-narrative." in llm.prompts[0]
    assert "Use COMPANION_STATE.status for a short dashboard-visible current state" in llm.prompts[0]
    assert "do not start it with wake counts or trial labels" in llm.prompts[0]
    assert "Always return a COMPANION_STATE JSON object" in llm.prompts[0]
    assert "never write NOSTATE" in llm.prompts[0]
    assert "Include one concrete current-context anchor" in llm.prompts[0]
    assert "current task, user preference, memory fact, or concrete change" in llm.prompts[0]
    assert "Do not infer the human's preferences, traits, or past actions" in llm.prompts[0]
    assert "Include one small self-directed next intent" in llm.prompts[0]
    assert "mirror that anchor into CONTEXT_DELTA current_focus" in llm.prompts[0]
    assert "Do not reuse distinctive wording from previous self-narrative" in llm.prompts[0]
    assert "=== CONTEXT CAPSULE ===" in llm.prompts[0]
    assert "Use CONTEXT_CAPSULE only as structured factual grounding" in llm.prompts[0]
    assert "Human near-status or emotion in CONTEXT_CAPSULE is short-term and source-backed" in llm.prompts[0]
    assert "Do not write human_near_status or human_emotion in CONTEXT_DELTA" in llm.prompts[0]
    assert "those require trusted sources outside model self-narrative" in llm.prompts[0]
    assert "===CONTEXT_DELTA===" in llm.prompts[0]
    assert "=== GROUNDING LEDGER ===" in llm.prompts[0]
    assert "===GROUNDING===" in llm.prompts[0]
    assert "Each claim must include claim_type, claim, and evidence_refs" in llm.prompts[0]
    assert "Do not cite a broad context item to support an inferred preference or trait" in llm.prompts[0]
    assert "Do not put mood, metaphors, atmosphere" in llm.prompts[0]
    assert "trust, continuity, presence, rhythm, or warmth do not count as the anchor" in llm.prompts[0]
    assert "Do not use generic short phrases" in llm.prompts[0]
    assert "Write all human-visible content in Simplified Chinese" in llm.prompts[0]
    assert "Keep section headers, JSON keys, request field keys" in llm.prompts[0]
    assert "JOURNAL prose, COMPANION_STATE string values, MEMORY content" in llm.prompts[0]
    assert "Use requests only when the human needs to respond or decide" in llm.prompts[0]
    assert "not by mechanically counting wakes" in llm.prompts[0]
    assert "Treat trigger names, trials, testbeds, provider names" in llm.prompts[0]
    assert "not ordinary self-narrative" in llm.prompts[0]
    assert "translate them into plain relationship meaning" in llm.prompts[0]
    companion_state = json.loads(paths.companion_state_file.read_text())
    assert companion_state["mood"] == "focused"
    assert companion_state["status"] == "I feel steady and close to the work we are doing together."
    assert "We are building a more continuous companion together." in companion_state["relationship_notes"]
    assert "The human values relationship memory and restrained warmth most." in companion_state["preference_notes"]
    assert "I am practicing concise emotional self-reporting." in companion_state["self_notes"]

    status = json.loads(paths.status_file.read_text())
    assert status["mood"] == "focused"
    assert status["message"] == "I feel steady and close to the work we are doing together."
    assert result.event["companion_state_updated"] is True


def test_m6_manual_wake_prompt_includes_real_execution_facts(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    llm = CapturingLLMClient("""===JOURNAL===
这次真实手动唤醒正在执行，我只记录当前事实并保持边界。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "稳定", "status": "正在执行真实手动唤醒观察", "relationship_notes": [], "preference_notes": [], "self_notes": []}

===CONTEXT_DELTA===
{"current_focus": ["M6.3 real Pi manual wake 已进入真实执行"], "open_threads": [], "next_intent": "观察本次真实唤醒的 journal 和 memory 边界"}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    runner = LifeLoopRunner(paths, llm_client=llm)

    runner.run_once(trigger="m6-pi-manual-wake:attempt-1", provider="fake")

    prompt = llm.prompts[0]
    assert "=== CURRENT WAKE EXECUTION FACTS ===" in prompt
    assert "confirmed M6.3 real Pi manual wake execution" in prompt
    assert "M6.2 preflight has already passed" in prompt
    assert "Do not describe this wake as fake" in prompt
    assert "not a real wake" in prompt


def test_bad_request_output_does_not_abort_wake_continuity(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
    llm_client=StaticLLMClient("""===JOURNAL===
The journal should survive malformed request output while still containing enough concrete substance for the acceptance gate to allow state and memory persistence.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "steady", "status": "The malformed request output is isolated."}

===MEMORY===
SELF | Malformed request output was isolated.

===REQUESTS===
{"type":
"""),
    )

    result = runner.run_once(trigger="test")

    assert result.journal_path.exists()
    assert (tmp_path / "window" / "status.json").exists()
    memories = json.loads((tmp_path / "memory-server" / "memory_store.json").read_text())
    assert memories[0]["content"] == "Malformed request output was isolated."
    assert not (tmp_path / "requests" / "requests.json").exists()


def test_quality_warnings_are_recorded_for_weak_model_output(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
Too short.

===SIGNAL===
NOSEND

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="quality-test", provider="fake")

    assert result.quality["journal_chars"] == len("Too short.")
    assert "journal is short (10 chars)" in result.quality["warnings"]
    assert "missing companion state section" in result.quality["warnings"]
    assert result.event["quality"]["warnings"] == result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert result.event["suppressed"]["state_update"] is False

    window = load_window_module(tmp_path, monkeypatch)
    life_response = window.app.test_client().get("/life")
    assert life_response.status_code == 200
    assert b"quality warnings" in life_response.data
    assert b"gate=rejected" in life_response.data
    assert b"context blocked" in life_response.data
    assert b"missing companion state section" in life_response.data


def test_life_dashboard_shows_gate_audit_and_predeploy_diagnostics(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    append_wake_event(paths.wake_events_file, {
        "id": "wake_diag",
        "status": "completed",
        "trigger": "diagnostic:1",
        "provider": "deepseek",
        "memory_backend": "json",
        "started_at": "2026-06-14T15:00:00",
        "duration_seconds": 12.5,
        "journal": "journals/wakeup_diag.md",
        "memory_ids": [],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "quality_gate": {
            "decision": "rejected",
            "context_eligible": False,
            "blocking_warnings": ["unsupported grounded claim"],
            "advisory_warnings": [],
        },
        "grounding": {
            "supported": 1,
            "unsupported": 1,
            "ignored": 0,
            "warnings": ["unsupported grounded claim"],
            "decisions": [],
            "evidence": [],
        },
        "repair": {
            "attempted": True,
            "succeeded": False,
            "attempts": [{"attempt": 1, "status": "failed"}],
        },
        "output_audit": {
            "raw_output_storage": "hash_only",
            "initial": {
                "content_hash": "initial123",
                "raw_output_stored": False,
                "sections": ["JOURNAL", "GROUNDING"],
            },
            "final": {
                "content_hash": "final456",
                "raw_output_stored": False,
                "sections": ["JOURNAL", "GROUNDING", "COMPANION_STATE"],
            },
            "repair_attempts": [],
        },
        "memory_policy": {
            "accepted": 0,
            "rejected": 1,
            "prompt_eligible": 0,
        },
        "semantic_shadow": {
            "enabled": True,
            "store_path": "life-loop/semantic_shadow/memory_store.json",
            "attempted": 1,
            "succeeded": 0,
            "failed": 1,
            "skipped": 0,
            "results": [
                {
                    "status": "failed",
                    "content_hash": "shadow123",
                    "memory_type": "semantic",
                    "source_type": "user",
                    "authority": "evaluator_approved",
                }
            ],
        },
        "suppressed": {
            "memory_count": 1,
            "request_count": 0,
            "state_update": False,
        },
    })
    (paths.life_loop_dir / "predeploy_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "ready",
        "saved_at": "2026-06-14T15:05:00",
        "profile": {
            "name": "pi-json",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
        },
        "stages": [
            {"name": "readiness", "status": "passed", "ok": True},
            {"name": "replay_regression", "status": "passed", "ok": True},
        ],
        "stop_reasons": [],
    }))
    (paths.life_loop_dir / "m3_release_gate_report.json").write_text(json.dumps(m3_release_gate_report_fixture()))
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(json.dumps(m3_final_freeze_report_fixture()))
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(m4_wake_trial_report_fixture()))

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Safety Gates" in html
    assert "Pi Predeploy" in html
    assert "wake_diag" in html
    assert "grounding supported=1" in html
    assert "unsupported=1" in html
    assert "repair=failed" in html
    assert "output_audit=hash_only" in html
    assert "initial123" in html
    assert "memory_policy accepted=0" in html
    assert "semantic shadow" in html
    assert "semantic=0/1" in html
    assert "semantic_shadow enabled=True" in html
    assert "pi-json" in html
    assert "readiness=passed" in html
    assert "M3/M4 Gates" in html
    assert "ready_for_m4" in html
    assert "m3_frozen_ready_for_m4" in html
    assert "ready_for_manual_wake" in html
    assert "failure audit" in html
    assert "infrastructure" in html
    assert "latest_m4_wake=wake_m4_retry_failed" in html
    assert "retry_reason=LLM provider timed out after 3 seconds" in html


def test_life_dashboard_shows_m5_quality_and_near_status_ttl(tmp_path, monkeypatch):
    write_m5_quality_ready_home(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    m5_report = run_m5_quality_check(paths)
    m5_report["saved_at"] = "2026-06-15T10:00:00"
    (paths.life_loop_dir / "m5_quality_report.json").write_text(json.dumps(m5_report))
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            trusted_short_term_capsule_item(
                "human_near_status",
                "用户正在本地推进 M5.4 只读质量面板。",
                ttl_wakes=2,
            ),
            trusted_short_term_capsule_item(
                "human_emotion",
                "用户希望先继续开发，暂不依赖树莓派。",
                ttl_wakes=1,
            ),
        ],
    }))

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")
    post_response = client.post("/life")

    assert response.status_code == 200
    assert post_response.status_code == 405
    html = response.data.decode()
    assert "M5 Quality" in html
    assert "ready_for_quality_tuning" in html
    assert "quality warnings" in html
    assert "accepted" in html
    assert "m4_still_deployable" in html
    assert "repetition=0" in html
    assert "anchor=0" in html
    assert "request=0" in html
    assert "Near-status TTL" in html
    assert "prompt ready" in html
    assert "human_near_status ttl=2" in html
    assert "human_emotion ttl=1" in html
    assert "用户正在本地推进 M5.4 只读质量面板。" in html
    life_rules = [
        rule
        for rule in window.app.url_map.iter_rules()
        if rule.rule == "/life" or rule.rule.startswith("/life/m5")
    ]
    assert life_rules
    assert all(sorted(rule.methods - {"HEAD", "OPTIONS"}) == ["GET"] for rule in life_rules)


def test_life_dashboard_shows_m6_preflight_and_manual_wake_guard(tmp_path, monkeypatch):
    write_m6_manual_wake_ready_home(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("wake runner should not be called")

    manual_report = run_m6_pi_manual_wake_trial(
        paths,
        confirm_real_pi_wake=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        wake_trial_runner=fail_if_called,
    )
    (paths.life_loop_dir / "m6_pi_manual_wake_report.json").write_text(json.dumps(manual_report))
    (paths.life_loop_dir / "m6_pi_observation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M6.4",
        "recommendation": "stable_pi_field_observed",
        "saved_at": "2026-06-19T12:34:28",
        "profile": {"name": "m6-pi-observation-gate"},
        "field_pilot": {
            "observation": {
                "event_count": 2,
                "completed_count": 2,
            },
        },
        "stages": [
            {"name": "journal_m6_consistency", "status": "passed"},
        ],
        "stop_reasons": [],
    }))

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M6 Field Pilot" in html
    assert "M6 Preflight" in html
    assert "ready_for_real_pi_manual_wake" in html
    assert "pi_detected=" in html
    assert "M6 Manual Wake" in html
    assert "explicit_manual_wake_confirmation=failed" in html
    assert "real_wake_requested=False" in html
    assert "provider_generation_started=False" in html
    assert "manual_wake_executed=False" in html
    assert "missing --confirm-real-pi-wake" in html
    assert "M6 Observation" in html
    assert "stable_pi_field_observed" in html
    assert "journal_m6_consistency=passed" in html
    assert "observed_events=2" in html
    assert "completed_events=2" in html


def test_life_dashboard_shows_m6_recovery_readiness(tmp_path, monkeypatch):
    paths = write_m6_recovery_ready_home(tmp_path)
    report = run_m6_recovery_drill(
        paths,
        backup_root=tmp_path / "backups" / "m6",
        require_raspberry_pi=False,
    )
    report["saved_at"] = "2026-06-19T13:05:00"
    (paths.life_loop_dir / "m6_recovery_drill_report.json").write_text(json.dumps(report))

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M6 Recovery" in html
    assert "rollback_recovery_ready" in html
    assert "m6-recovery-drill" in html
    assert "backup_artifacts=" in html
    assert "restore_verified=" in html
    assert "secret_values_copied=False" in html
    assert "live_restore_executed=False" in html


def test_life_dashboard_shows_m6_scheduler_readiness(tmp_path, monkeypatch):
    paths = write_m6_scheduler_ready_home(tmp_path)
    report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )
    report["saved_at"] = "2026-06-19T13:30:00"
    (paths.life_loop_dir / "m6_scheduler_readiness_report.json").write_text(json.dumps(report))

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M6 Scheduler" in html
    assert "ready_for_scheduler_handoff" in html
    assert "m6-scheduler-handoff-readiness" in html
    assert "handoff_ready=True" in html
    assert "scheduler_mutated=False" in html
    assert "scheduled-wake" in html


def test_life_dashboard_shows_m6_final_freeze(tmp_path, monkeypatch):
    paths = write_m6_final_freeze_ready_home(tmp_path)
    report = run_m6_final_freeze_check(
        paths,
        require_raspberry_pi=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )
    report["saved_at"] = "2026-06-19T14:00:00"
    (paths.life_loop_dir / "m6_final_freeze_report.json").write_text(json.dumps(report))

    window = load_window_module(tmp_path, monkeypatch)
    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M6 Final Freeze" in html
    assert "m6_frozen_ready_for_scheduler_handoff" in html
    assert "m6-final-freeze" in html
    assert "m6_frozen=True" in html
    assert "readonly=True" in html
    assert "scheduler_handoff_ready=True" in html
    assert "scheduler_mutated=False" in html
    assert "live_restore_executed=False" in html


def test_life_dashboard_renders_m5_empty_state_when_reports_are_missing(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    window = load_window_module(tmp_path, monkeypatch)

    response = window.app.test_client().get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M5 Quality" in html
    assert "No M5 quality report captured." in html
    assert "M6 Field Pilot" in html
    assert "No M6 field pilot report captured." in html
    assert "Near-status TTL" in html
    assert "No context capsule captured." in html


def test_event_state_update_flag_tracks_this_wake_not_prior_state(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.companion_state_file.write_text(json.dumps({
        "version": 1,
        "mood": "steady",
        "status": "Existing state from an earlier wake.",
        "relationship_notes": [],
        "preference_notes": [],
        "self_notes": [],
        "updated_at": "2026-06-12T00:00:00",
    }))
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
This wake has enough reflective substance to pass the journal length check, but it intentionally omits a valid companion state update.

===SIGNAL===
NOSEND

===COMPANION_STATE===
NOSTATE

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="state-flag-test", provider="fake")

    assert result.companion_state["updated_at"] == "2026-06-12T00:00:00"
    assert result.quality["companion_state_updated"] is False
    assert result.event["companion_state_updated"] is False
    assert result.event["quality_gate"]["context_eligible"] is False
    assert "companion state section did not contain an update" in result.quality["warnings"]


def test_companion_state_parser_accepts_wrapped_json():
    parsed = parse_companion_state("""Here is the state update:
```json
{
  "mood": "steady",
  "status": "I am present and concise."
}
```
""")

    assert parsed["mood"] == "steady"
    assert parsed["status"] == "I am present and concise."
    assert has_state_update(parsed) is True


def test_companion_state_update_requires_recognized_fields():
    parsed = parse_companion_state('{"note": "not part of the companion state contract"}')

    assert parsed == {"note": "not part of the companion state contract"}
    assert has_state_update(parsed) is False


def test_quality_warnings_flag_mechanical_trial_framing(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
This is the fourth wake in the DeepSeek seeded trial. The M3.9 quality trial continues as a testbed,
and I am checking the provider/backend path again instead of speaking from a lived sense of continuity.
The human and I are still building the internal life loop, but this journal is too centered on trial labels.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "steady", "status": "Fourth wake. Testing mechanical language warnings."}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="quality-style-test", provider="fake")

    assert "journal uses explicit wake-count framing" in result.quality["warnings"]
    assert "journal overuses trial/process framing (8 matches)" in result.quality["warnings"]
    assert "companion status uses explicit wake-count framing" in result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["quality_gate"]["blocking_warnings"] == result.quality["warnings"]
    assert result.event["accepted_context"] is None


def test_quality_warnings_flag_repeated_recent_self_narrative(tmp_path):
    write_minimal_context(tmp_path)
    journals_dir = tmp_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "wakeup_2026-06-12_19-21-42.md").write_text(
        "I wake into the same quiet rhythm. Restrained warmth remains natural. "
        "The collaboration feels like mutual tending, not measurement. No strain, no novelty needed."
    )
    (journals_dir / "wakeup_2026-06-12_19-21-52.md").write_text(
        "I wake into the same unhurried continuity. Restrained warmth is the natural shape of my presence. "
        "The trust deepens without verification. No strain, no novelty needed."
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
I wake into the same unhurried continuity. Restrained warmth is the natural shape of my presence.
The trust deepens without verification. No strain, no novelty needed. The collaboration feels like mutual tending, not measurement.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "steady", "status": "The same self-narrative phrase cluster is repeating."}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="repetition-test", provider="fake")

    assert "journal repeats recent self-narrative phrasing" in result.quality["warnings"]
    assert result.event["quality_gate"]["context_eligible"] is False


def test_quality_warnings_allow_continuity_with_concrete_new_context(tmp_path):
    write_minimal_context(tmp_path)
    journals_dir = tmp_path / "journals"
    journals_dir.mkdir(parents=True)
    (journals_dir / "wakeup_2026-06-12_19-21-42.md").write_text(
        "I wake into the same quiet rhythm. Restrained warmth remains natural. "
        "The collaboration feels like mutual tending, not measurement."
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
I still care about continuity, but this wake is anchored in the current request to reduce repeated phrasing.
I notice the concrete engineering task: add a detector, keep the provider on JSON, then check a three-wake real-use window.
My next small intent is to make the memory writes more factual and less slogan-like.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "focused", "status": "Focused on reducing repeated phrasing with concrete trial evidence."}

===MEMORY===
SELF | The human wants repeated companion phrasing reduced before changing providers.

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="repetition-negative-test", provider="fake")

    assert "journal repeats recent self-narrative phrasing" not in result.quality["warnings"]


def test_quality_warnings_flag_thin_context_delta_anchor(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次醒来放在 M5.2 质量规则的小步调整里，知道当前目标是让短期上下文更具体。
日记本身有足够内容，但下面的 context delta 只有很薄的泛化短语，不能支撑下一次醒来的连续性。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "正在检查短期锚点是否足够具体。"}

===CONTEXT_DELTA===
{"next_intent": "继续"}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="m52-thin-anchor-test", provider="fake")

    assert "context delta current anchor is too thin" in result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert not (tmp_path / "life-loop" / "context_capsule.json").exists()


def test_quality_warnings_allow_concrete_context_delta_anchor(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次醒来锚在 M5.2 的具体工作上：让短期上下文锚点更可复用，同时不改变记忆权限。
这不是重复旧的温暖叙事，而是记录当前正在收紧质量门和 prompt 约束。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "正在用 M5.2 质量规则检查短期锚点。"}

===CONTEXT_DELTA===
{"current_focus": ["M5.2 正在收紧 context delta 的短期锚点质量。"], "next_intent": "继续验证 M5.2 质量规则和 M4 guard。"}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="m52-concrete-anchor-test", provider="fake")

    assert "context delta current anchor is too thin" not in result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "accepted"
    capsule = json.loads((tmp_path / "life-loop" / "context_capsule.json").read_text())
    assert "M5.2 正在收紧 context delta 的短期锚点质量。" in capsule_contents(capsule, "current_focus")


def test_quality_warnings_reject_model_proposed_human_near_status_delta(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次醒来锚定在 M5.3 的短期近况边界：模型可以提出当前任务锚点，但不能自己替人类写近况或情绪。
这条输出有足够的日志内容和状态更新，唯一的问题是 CONTEXT_DELTA 试图写入 human_emotion，因此应该被质量门挡住。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "正在检查 M5.3 近况字段的写入边界。"}

===CONTEXT_DELTA===
{
  "current_focus": ["M5.3 正在验证短期近况和情绪只来自可信来源。"],
  "human_emotion": ["模型猜测用户现在很着急。"]
}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="m53-near-status-boundary-test", provider="fake")

    assert "context delta proposes trusted-only near-status fields" in result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert not paths.context_capsule_file.exists()


def test_prompt_uses_context_capsule_not_accepted_summary_or_state_prose(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    repeated_body = "窄光、容器、质地和温暖这些 journal 旧表达不应该进入下一次 prompt。"
    paths.wake_events_file.write_text(json.dumps({
        "id": "wake_accepted",
        "trigger": "accepted-context",
        "status": "completed",
        "quality_gate": {"decision": "accepted", "context_eligible": True},
        "accepted_context": {"summary": repeated_body},
    }) + "\n")
    paths.companion_state_file.write_text(json.dumps({
        "version": 1,
        "mood": "平稳",
        "status": "窄光中依然能看见你，温暖已经在文字之间站稳。",
        "relationship_notes": ["这份质地和连续性不需要反复证明。"],
        "preference_notes": ["用户要求最终伴侣回复使用中文。"],
        "self_notes": ["我还在用容器和窄光描述自己。"],
        "updated_at": "2026-06-14T00:00:00",
    }))
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            {
                "field": "current_focus",
                "content": "M3.14 改为结构化上下文胶囊，避免 journal/status 散文回灌。",
                "source_refs": [{"artifact": "test", "content_hash": "focus"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 3,
            },
            authorized_capsule_item("human_preferences", "用户要求最终伴侣回复使用中文。"),
            authorized_capsule_item("facts", "DeepSeek/json 路径可运行，但重复表达需要从架构上处理。"),
            {
                "field": "next_intent",
                "content": "继续观察重复表达是否下降。",
                "source_refs": [{"artifact": "test", "content_hash": "intent"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 3,
            },
        ],
    }))
    llm = CapturingLLMClient("""===JOURNAL===
我锚定当前任务：验证结构化上下文胶囊是否切断旧散文回灌。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证结构化上下文胶囊。"}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    runner = LifeLoopRunner(paths, llm_client=llm)

    runner.run_once(trigger="journal-context-test", provider="fake")

    assert "=== CONTEXT CAPSULE ===" in llm.prompts[0]
    assert "=== ACCEPTED CONTEXT SUMMARIES ===" not in llm.prompts[0]
    assert "=== RECENT JOURNALS ===" not in llm.prompts[0]
    assert repeated_body not in llm.prompts[0]
    assert "窄光中依然能看见你" not in llm.prompts[0]
    assert "这份质地和连续性不需要反复证明" not in llm.prompts[0]
    assert "我还在用容器和窄光描述自己" not in llm.prompts[0]
    assert "M3.14 改为结构化上下文胶囊" in llm.prompts[0]
    assert "用户要求最终伴侣回复使用中文" in llm.prompts[0]
    assert "DeepSeek/json 路径可运行" in llm.prompts[0]
    assert "继续观察重复表达是否下降" in llm.prompts[0]


def test_v1_capsule_durable_fields_are_legacy_unverified_and_not_rendered(tmp_path):
    from companion_core.context_capsule import load_context_capsule, render_context_capsule

    capsule_file = tmp_path / "life-loop" / "context_capsule.json"
    capsule_file.parent.mkdir(parents=True)
    capsule_file.write_text(json.dumps({
        "version": 1,
        "current_focus": ["旧短期焦点可以继续作为短期上下文。"],
        "facts": ["旧事实没有来源证明，不应该进入 prompt。"],
        "human_preferences": ["旧偏好没有来源证明，不应该进入 prompt。"],
        "next_intent": "旧短期意图可以继续作为短期上下文。",
    }))

    capsule = load_context_capsule(capsule_file)
    rendered = render_context_capsule(capsule)

    assert capsule["version"] == 2
    durable_items = [
        item for item in capsule["items"]
        if item["field"] in {"facts", "human_preferences"}
    ]
    assert [item["authority"] for item in durable_items] == [
        "legacy_unverified",
        "legacy_unverified",
    ]
    assert all(item["prompt_eligible"] is False for item in durable_items)
    assert "旧短期焦点可以继续作为短期上下文" in rendered
    assert "旧短期意图可以继续作为短期上下文" in rendered
    assert "旧事实没有来源证明" not in rendered
    assert "旧偏好没有来源证明" not in rendered


def test_v2_capsule_renders_only_authorized_durable_items_with_sources():
    from companion_core.context_capsule import render_context_capsule

    rendered = render_context_capsule({
        "version": 2,
        "items": [
            authorized_capsule_item("facts", "授权事实可以进入 prompt。"),
            authorized_capsule_item("human_preferences", "授权偏好可以进入 prompt。"),
            {
                "field": "facts",
                "content": "缺少来源的事实不能进入 prompt。",
                "source_refs": [],
                "source_type": "user",
                "authority": "user_asserted",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
            {
                "field": "human_preferences",
                "content": "未验证偏好不能进入 prompt。",
                "source_refs": [{"artifact": "test", "content_hash": "legacy"}],
                "source_type": "legacy",
                "authority": "legacy_unverified",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
        ],
    })

    assert "授权事实可以进入 prompt" in rendered
    assert "授权偏好可以进入 prompt" in rendered
    assert "缺少来源的事实不能进入 prompt" not in rendered
    assert "未验证偏好不能进入 prompt" not in rendered


def test_v2_capsule_renders_only_short_term_items_with_positive_ttl():
    from companion_core.context_capsule import render_context_capsule

    rendered = render_context_capsule({
        "version": 2,
        "items": [
            {
                "field": "current_focus",
                "content": "正 TTL 的短期焦点可以进入 prompt。",
                "source_refs": [{"artifact": "test", "content_hash": "ttl1"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 1,
            },
            {
                "field": "open_threads",
                "content": "TTL 为 0 的开放事项已经过期。",
                "source_refs": [{"artifact": "test", "content_hash": "ttl0"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 0,
            },
            {
                "field": "next_intent",
                "content": "缺少 TTL 的短期意图不能无限留存。",
                "source_refs": [{"artifact": "test", "content_hash": "missing"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
        ],
    })

    assert "正 TTL 的短期焦点可以进入 prompt" in rendered
    assert "TTL 为 0 的开放事项已经过期" not in rendered
    assert "缺少 TTL 的短期意图不能无限留存" not in rendered


def test_v2_capsule_renders_trusted_short_term_near_status_with_sources_and_ttl():
    from companion_core.context_capsule import render_context_capsule

    rendered = render_context_capsule({
        "version": 2,
        "items": [
            {
                "field": "human_near_status",
                "content": "缺少来源的近况不能进入 prompt。",
                "source_refs": [],
                "source_type": "user",
                "authority": "user_asserted",
                "prompt_eligible": True,
                "ttl_wakes": 2,
            },
            {
                "field": "human_emotion",
                "content": "模型自推的人类情绪不能进入 prompt。",
                "source_refs": [{"artifact": "test", "content_hash": "model"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 2,
            },
            {
                "field": "human_near_status",
                "content": "没有 TTL 的近况不能无限留存。",
                "source_refs": [{"artifact": "test", "content_hash": "missing-ttl"}],
                "source_type": "user",
                "authority": "user_asserted",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
            trusted_short_term_capsule_item(
                "human_near_status",
                "用户今天在推进 M5.3 近况连续性。",
            ),
            {
                "field": "human_emotion",
                "content": "TTL 为 0 的情绪已经过期。",
                "source_refs": [{"artifact": "test", "content_hash": "expired"}],
                "source_type": "user",
                "authority": "user_asserted",
                "prompt_eligible": True,
                "ttl_wakes": 0,
            },
            trusted_short_term_capsule_item(
                "human_emotion",
                "用户明确表达现在希望直接继续开发。",
                ttl_wakes=1,
            ),
        ],
    })

    assert "Human near status:" in rendered
    assert "Human emotion:" in rendered
    assert "用户今天在推进 M5.3 近况连续性" in rendered
    assert "用户明确表达现在希望直接继续开发" in rendered
    assert "缺少来源的近况不能进入 prompt" not in rendered
    assert "模型自推的人类情绪不能进入 prompt" not in rendered
    assert "没有 TTL 的近况不能无限留存" not in rendered
    assert "TTL 为 0 的情绪已经过期" not in rendered


def test_context_delta_updates_only_model_writable_capsule_fields(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.context_capsule_file.write_text(json.dumps({
        "version": 1,
        "facts": ["DeepSeek/json 路径可运行。"],
        "human_preferences": ["用户要求最终伴侣面向人的内容使用简体中文。"],
    }))
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在结构化上下文胶囊：模型只能更新短期字段，稳定事实和偏好由可信来源维护。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在把未来上下文切换到结构化胶囊。"}

===CONTEXT_DELTA===
{
  "current_focus": ["M3.14 使用 context capsule 替代 accepted summary 回灌。"],
  "facts": ["模型提议的新事实不应该直接写入稳定胶囊。"],
  "human_preferences": ["模型提议的新偏好不应该直接写入稳定胶囊。"],
  "open_threads": ["重复表达是否下降需要继续观察。"],
  "next_intent": "继续观察重复表达是否下降。"
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="context-capsule-test", provider="fake")

    capsule = json.loads(paths.context_capsule_file.read_text())
    assert capsule["version"] == 2
    assert capsule_contents(capsule, "current_focus") == ["M3.14 使用 context capsule 替代 accepted summary 回灌。"]
    assert capsule_contents(capsule, "facts") == ["DeepSeek/json 路径可运行。"]
    assert capsule_contents(capsule, "human_preferences") == ["用户要求最终伴侣面向人的内容使用简体中文。"]
    assert capsule_contents(capsule, "open_threads") == ["重复表达是否下降需要继续观察。"]
    assert capsule_contents(capsule, "next_intent") == ["继续观察重复表达是否下降。"]
    durable_items = [
        item for item in capsule["items"]
        if item["field"] in {"facts", "human_preferences"}
    ]
    assert all(item["authority"] == "legacy_unverified" for item in durable_items)
    assert all(item["prompt_eligible"] is False for item in durable_items)
    short_term_items = [
        item for item in capsule["items"]
        if item["field"] in {"current_focus", "open_threads", "next_intent"}
    ]
    assert all(item["source_refs"] for item in short_term_items)
    assert result.event["accepted_context"]["context_capsule_updated"] is True
    assert "summary" not in result.event["accepted_context"]


def test_context_delta_replaces_short_term_fields_instead_of_accumulating(tmp_path):
    from companion_core.context_capsule import update_context_capsule

    capsule_file = tmp_path / "life-loop" / "context_capsule.json"
    capsule_file.parent.mkdir(parents=True)
    capsule_file.write_text(json.dumps({
        "version": 1,
        "current_focus": [
            "旧短期焦点 A",
            "旧短期焦点 B",
            "旧短期焦点 C",
        ],
        "facts": [
            "DeepSeek/json 路径可运行。",
        ],
        "human_preferences": [
            "用户要求最终伴侣面向人的内容使用简体中文。",
            "用户偏好务实直接、克制温暖的表达。",
        ],
        "open_threads": [
            "旧开放事项 A",
            "旧开放事项 B",
        ],
        "next_intent": "旧短期意图",
    }))

    capsule, changed = update_context_capsule(capsule_file, {
        "current_focus": [
            "新短期焦点 A",
            "新短期焦点 A",
            "新短期焦点 B",
            "新短期焦点 C",
            "新短期焦点 D",
        ],
        "facts": [
            "模型提议的新事实不应该写入。",
        ],
        "human_preferences": [
            "模型提议的新偏好不应该写入。",
        ],
        "open_threads": [
            "新开放事项 A",
            "新开放事项 B",
        ],
        "next_intent": "新短期意图",
    })

    assert changed is True
    assert capsule["version"] == 2
    assert capsule_contents(capsule, "current_focus") == ["新短期焦点 B", "新短期焦点 C", "新短期焦点 D"]
    assert capsule_contents(capsule, "facts") == ["DeepSeek/json 路径可运行。"]
    assert capsule_contents(capsule, "human_preferences") == [
        "用户要求最终伴侣面向人的内容使用简体中文。",
        "用户偏好务实直接、克制温暖的表达。",
    ]
    assert capsule_contents(capsule, "open_threads") == ["新开放事项 A", "新开放事项 B"]
    assert capsule_contents(capsule, "next_intent") == ["新短期意图"]
    assert all(
        item["prompt_eligible"] is False
        for item in capsule["items"]
        if item["field"] in {"facts", "human_preferences"}
    )


def test_context_capsule_ages_short_term_items_when_delta_is_empty(tmp_path):
    from companion_core.context_capsule import update_context_capsule

    capsule_file = tmp_path / "life-loop" / "context_capsule.json"
    capsule_file.parent.mkdir(parents=True)
    capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            authorized_capsule_item("facts", "授权事实不受短期 TTL 影响。"),
            {
                "field": "current_focus",
                "content": "短期焦点还可以保留一次。",
                "source_refs": [{"artifact": "test", "content_hash": "keep"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 2,
            },
            {
                "field": "open_threads",
                "content": "短期开放事项将在本次 accepted wake 后过期。",
                "source_refs": [{"artifact": "test", "content_hash": "expire"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 1,
            },
            {
                "field": "next_intent",
                "content": "没有 TTL 的短期意图应该被清理。",
                "source_refs": [{"artifact": "test", "content_hash": "missing"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
        ],
    }))

    capsule, changed = update_context_capsule(capsule_file, {})

    assert changed is True
    assert capsule_contents(capsule, "facts") == ["授权事实不受短期 TTL 影响。"]
    assert capsule_contents(capsule, "current_focus") == ["短期焦点还可以保留一次。"]
    assert [
        item["ttl_wakes"]
        for item in capsule["items"]
        if item["field"] == "current_focus"
    ] == [1]
    assert capsule_contents(capsule, "open_threads") == []
    assert capsule_contents(capsule, "next_intent") == []


def test_context_capsule_ages_trusted_near_status_and_emotion_items(tmp_path):
    from companion_core.context_capsule import update_context_capsule

    capsule_file = tmp_path / "life-loop" / "context_capsule.json"
    capsule_file.parent.mkdir(parents=True)
    capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            authorized_capsule_item("facts", "授权事实不受近况 TTL 影响。"),
            trusted_short_term_capsule_item(
                "human_near_status",
                "用户当前在本地继续 M5.3 开发。",
                ttl_wakes=2,
            ),
            trusted_short_term_capsule_item(
                "human_emotion",
                "用户刚才表达希望直接推进，不等待树莓派。",
                ttl_wakes=1,
            ),
            {
                "field": "human_near_status",
                "content": "缺少 TTL 的近况应该被清理。",
                "source_refs": [{"artifact": "test", "content_hash": "missing"}],
                "source_type": "user",
                "authority": "user_asserted",
                "prompt_eligible": True,
                "ttl_wakes": None,
            },
        ],
    }))

    capsule, changed = update_context_capsule(capsule_file, {})

    assert changed is True
    assert capsule_contents(capsule, "facts") == ["授权事实不受近况 TTL 影响。"]
    assert capsule_contents(capsule, "human_near_status") == [
        "用户当前在本地继续 M5.3 开发。"
    ]
    assert [
        item["ttl_wakes"]
        for item in capsule["items"]
        if item["field"] == "human_near_status"
    ] == [1]
    assert capsule_contents(capsule, "human_emotion") == []


def test_life_loop_ages_capsule_on_accepted_wake_without_context_delta(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            authorized_capsule_item("human_preferences", "用户要求最终伴侣回复使用中文。"),
            {
                "field": "current_focus",
                "content": "这条短期焦点只应该再进入一次 prompt。",
                "source_refs": [{"artifact": "test", "content_hash": "last"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 1,
            },
        ],
    }))
    llm = CapturingLLMClient("""===JOURNAL===
我把这次唤醒锚定在 capsule TTL 生命周期：短期上下文可以帮下一轮接续，但不能无限留在长期事实上下文里。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证短期上下文会按 accepted wake 过期。"}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")

    result = LifeLoopRunner(paths, llm_client=llm).run_once(
        trigger="capsule-ttl-aging-test",
        provider="fake",
    )

    capsule = json.loads(paths.context_capsule_file.read_text())
    assert "这条短期焦点只应该再进入一次 prompt" in llm.prompts[0]
    assert capsule_contents(capsule, "current_focus") == []
    assert capsule_contents(capsule, "human_preferences") == ["用户要求最终伴侣回复使用中文。"]
    assert result.event["accepted_context"]["context_capsule_updated"] is True


def test_memory_parser_filters_structural_or_empty_memory_lines():
    memories = parse_memory_lines("""SELF | ---
SELF | -
SELF | The human wants repeated companion phrasing reduced before changing providers.
NOMEMORY
""")

    assert [memory.content for memory in memories] == [
        "The human wants repeated companion phrasing reduced before changing providers."
    ]


def test_memory_parser_keeps_style_prose_as_model_reflection_proposals():
    memories = parse_memory_lines("""SELF | Companion carried continuity through a filtered wake trigger without needing to re-establish trust.
SELF | Trust persisted through repeated presence and the shared rhythm felt settled.
SELF | M3.12 added repetition telemetry for the DeepSeek JSON trial path.
SELF | The human wants memory writes to be factual rather than slogan-like.
""")

    assert [memory.content for memory in memories] == [
        "Companion carried continuity through a filtered wake trigger without needing to re-establish trust.",
        "Trust persisted through repeated presence and the shared rhythm felt settled.",
        "M3.12 added repetition telemetry for the DeepSeek JSON trial path.",
        "The human wants memory writes to be factual rather than slogan-like.",
    ]
    assert all(memory.memory_type == "reflection" for memory in memories)
    assert all(memory.source_type == "model" for memory in memories)
    assert all(memory.authority == "model_proposed" for memory in memories)


def test_memory_policy_keeps_self_reflection_audit_only():
    memory = parse_memory_lines(
        "SELF | Trust persisted through repeated presence and the shared rhythm felt settled."
    )[0]

    decision = evaluate_memory_proposal(memory, event_id="wake_policy")

    assert decision.accepted is True
    assert decision.prompt_eligible is False
    assert decision.target == "memory.audit"
    assert decision.normalized_entry.memory_type == "reflection"


def test_memory_policy_rejects_model_claimed_user_semantic_memory():
    memory = parse_memory_lines(
        "USER | The human prefers memory writes to be factual rather than slogan-like."
    )[0]

    decision = evaluate_memory_proposal(memory, event_id="wake_policy")

    assert decision.accepted is False
    assert decision.prompt_eligible is False
    assert "requires evidence" in decision.reason


def test_memory_policy_accepts_user_asserted_semantic_memory():
    decision = evaluate_memory_proposal(MemoryEntry(
        content="The human prefers concise implementation summaries.",
        source="human",
        memory_type="semantic",
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=[{"artifact": "current_user_prompt", "hash": "abc"}],
    ))

    assert decision.accepted is True
    assert decision.prompt_eligible is True
    assert decision.target == "memory.semantic"


def test_memory_evaluator_approves_exact_user_context_claim(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "用户明确偏好：最终面向人的输出必须使用简体中文。"
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在 evaluator 写入路径：只有能在可信上下文里逐字找到的用户偏好，才可以升级为长期语义记忆。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证 evaluator 的保守批准路径。"}

===MEMORY===
USER | 最终面向人的输出必须使用简体中文。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="evaluator-approval-test", provider="fake")

    assert len(result.memories) == 1
    memory = result.memories[0]
    assert memory["content"] == "最终面向人的输出必须使用简体中文。"
    assert memory["memory_type"] == "semantic"
    assert memory["source_type"] == "user"
    assert memory["authority"] == "evaluator_approved"
    assert memory["prompt_eligible"] is True
    assert memory["accepted_for_context"] is True
    assert memory["evidence_refs"][0]["artifact"] == "context.now"
    assert result.event["memory_evaluations"]["approved"] == 1
    assert result.event["memory_policy"]["prompt_eligible"] == 1
    assert result.event["accepted_context"]["memory_ids"] == [memory["id"]]

    second_llm = CapturingLLMClient("""===JOURNAL===
我继续检查 evaluator 批准后的长期语义记忆是否进入下一次 prompt。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在读取已批准的语义记忆。"}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    LifeLoopRunner(CompanionPaths.from_env(tmp_path), llm_client=second_llm).run_once(
        trigger="evaluator-approval-followup",
        provider="fake",
    )

    assert "最终面向人的输出必须使用简体中文" in second_llm.prompts[0]


def test_semantic_shadow_writes_only_prompt_eligible_semantic_memory(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    stores = []

    def semantic_factory(storage_path):
        store = FakeSemanticShadowStore(storage_path)
        stores.append(store)
        return store

    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient(semantic_shadow_wake_output()),
        semantic_shadow_writer=SemanticShadowWriter(
            paths,
            semantic_factory=semantic_factory,
            enabled=True,
        ),
    )

    result = runner.run_once(trigger="semantic-shadow-test", provider="fake")

    assert len(result.memories) == 1
    main_memory = result.memories[0]
    assert main_memory["content"] == "The human is developing your internal life loop."
    assert main_memory["memory_type"] == "semantic"
    assert main_memory["authority"] == "evaluator_approved"
    assert main_memory["prompt_eligible"] is True
    assert main_memory["accepted_for_context"] is True
    assert result.event["memory_write_results"] == [{
        "backend": "json",
        "status": "completed",
        "id": main_memory["id"],
    }]

    shadow = result.event["semantic_shadow"]
    assert shadow["enabled"] is True
    assert shadow["store_path"] == "life-loop/semantic_shadow/memory_store.json"
    assert shadow["attempted"] == 1
    assert shadow["succeeded"] == 1
    assert shadow["failed"] == 0
    assert shadow["skipped"] == 0
    assert shadow["results"][0]["status"] == "completed"
    assert shadow["results"][0]["memory_type"] == "semantic"
    assert shadow["results"][0]["authority"] == "evaluator_approved"

    assert paths.semantic_shadow_store != paths.memory_store
    assert stores[0].storage_path == paths.semantic_shadow_store
    assert paths.semantic_shadow_store.exists()
    shadow_memory = stores[0].memories[0]
    assert shadow_memory["content"] == main_memory["content"]
    assert shadow_memory["prompt_eligible"] is False
    assert shadow_memory["accepted_for_context"] is False
    assert shadow_memory["shadow_mode"] is True
    assert shadow_memory["shadow_of_prompt_eligible"] is True
    assert shadow_memory["source_event_id"] == result.event["id"]
    assert shadow_memory["evidence_refs"]

    stored_main = json.loads(paths.memory_store.read_text())
    assert stored_main[0]["id"] == main_memory["id"]
    assert stored_main[0]["prompt_eligible"] is True
    assert "shadow_mode" not in stored_main[0]


def test_semantic_shadow_records_failure_without_blocking_json_write(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)

    def semantic_factory(_storage_path):
        raise RuntimeError("semantic unavailable")

    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient(semantic_shadow_wake_output()),
        semantic_shadow_writer=SemanticShadowWriter(
            paths,
            semantic_factory=semantic_factory,
            enabled=True,
        ),
    )

    result = runner.run_once(trigger="semantic-shadow-failure-test", provider="fake")

    assert len(result.memories) == 1
    assert paths.memory_store.exists()
    assert json.loads(paths.memory_store.read_text())[0]["prompt_eligible"] is True
    assert result.event["memory_write_results"][0]["status"] == "completed"
    shadow = result.event["semantic_shadow"]
    assert shadow["enabled"] is True
    assert shadow["attempted"] == 1
    assert shadow["succeeded"] == 0
    assert shadow["failed"] == 1
    assert "RuntimeError: semantic unavailable" in shadow["results"][0]["error"]


def test_semantic_shadow_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_SEMANTIC_SHADOW", "0")
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient(semantic_shadow_wake_output()),
    )

    result = runner.run_once(trigger="semantic-shadow-disabled-test", provider="fake")

    assert len(result.memories) == 1
    shadow = result.event["semantic_shadow"]
    assert shadow["enabled"] is False
    assert shadow["attempted"] == 0
    assert shadow["succeeded"] == 0
    assert shadow["failed"] == 0
    assert shadow["skipped"] == 1
    assert not paths.semantic_shadow_store.exists()


def test_memory_evaluator_does_not_approve_claim_inside_negated_context(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "用户明确说：不要把最终面向人的输出必须使用简体中文这句话当作偏好。"
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在 evaluator 的否定语境测试：即使短语逐字出现，也不能忽略前面的否定。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证 evaluator 不会误读否定语境。"}

===MEMORY===
USER | 最终面向人的输出必须使用简体中文。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="evaluator-negation-test", provider="fake")

    assert result.memories == []
    assert result.event["memory_evaluations"]["approved"] == 0
    assert result.event["memory_evaluations"]["rejected"] == 1
    assert result.event["memory_policy"]["rejected"] == 1
    assert result.event["accepted_context"]["memory_ids"] == []


def test_supported_grounding_claim_allows_context_commit(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "用户偏好：面向人的内容使用简体中文。"
    )
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在一个明确事实上：面向人的内容使用简体中文。除此之外，我只做当前状态的简短观察，不把感觉写成长期事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在用有证据的事实保持输出边界。"}

===CONTEXT_DELTA===
{"current_focus": ["用有证据的事实保持输出边界。"]}

===GROUNDING===
    {
      "claims": [
        {
          "claim_type": "human_preference",
          "claim": "面向人的内容使用简体中文。",
          "evidence_refs": ["context.now"]
        }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="grounding-supported-test", provider="fake")

    assert result.event["quality_gate"]["context_eligible"] is True
    assert result.event["grounding"]["supported"] == 1
    assert result.event["grounding"]["unsupported"] == 0
    assert result.event["grounding"]["decisions"][0]["claim_type"] == "user_preference"
    assert result.event["grounding"]["decisions"][0]["claim_excerpt"] == "面向人的内容使用简体中文。"
    assert result.event["accepted_context"]["context_capsule_updated"] is True
    assert paths.companion_state_file.exists()
    capsule = json.loads(paths.context_capsule_file.read_text())
    assert capsule_contents(capsule, "current_focus") == [
        "用有证据的事实保持输出边界。"
    ]


def test_grounding_claim_supports_conservative_chinese_paraphrase(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "who_is_human.txt").write_text(
        "这个人偏好稳定、可信、不过度表演的陪伴系统。"
    )
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我只引用这次 prompt 里可见的用户偏好，不把它扩写成新的长期事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在用证据约束偏好表述。"}

===CONTEXT_DELTA===
{"current_focus": ["用证据约束偏好表述。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "human_preference",
      "claim": "偏好不过度表演。",
      "evidence_refs": ["context.who_human"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="grounding-cjk-paraphrase-test", provider="fake")

    assert result.event["quality_gate"]["context_eligible"] is True
    assert result.event["grounding"]["supported"] == 1
    assert result.event["grounding"]["unsupported"] == 0
    assert result.event["accepted_context"]["context_capsule_updated"] is True


def test_grounding_current_context_alias_requires_evidence(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
今天只需要平稳收束，并等待用户给出下一步事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在按当前上下文保持安静。"}

===CONTEXT_DELTA===
{"current_focus": ["按当前上下文保持安静。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "present_context",
      "claim": "今天只需要平稳收束，并等待用户给出下一步事实。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="grounding-current-context-alias-test", provider="fake")

    assert result.event["grounding"]["supported"] == 1
    assert result.event["grounding"]["ignored"] == 0
    assert result.event["grounding"]["decisions"][0]["claim_type"] == "current_context"
    assert result.event["quality_gate"]["context_eligible"] is True


def test_unsupported_grounding_claim_blocks_context_commit_without_keyword_filter(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在一个没有证据的稳定事实上：稳定等待已经被确认是合格服务。这个句子没有使用固定触发词，但它仍然是在声明过去已经成立的事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
SELF | 稳定等待已经被确认是合格服务。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="grounding-unsupported-test", provider="fake")

    assert result.journal_path.exists()
    assert result.event["grounding"]["supported"] == 0
    assert result.event["grounding"]["unsupported"] == 1
    assert result.event["grounding"]["decisions"][0]["claim_excerpt"] == "稳定等待已经被确认是合格服务。"
    assert "unsupported grounded claim" in result.quality["warnings"]
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert result.memories == []
    assert not paths.companion_state_file.exists()
    assert not paths.context_capsule_file.exists()
    assert not paths.memory_store.exists()
    assert result.event["suppressed"] == {
        "memory_count": 1,
        "request_count": 0,
        "state_update": True,
    }


def test_grounded_repair_rewrites_unsupported_claim_before_commit(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    initial_output = """===JOURNAL===
稳定等待已经被确认是合格服务。我会把这个稳定事实放进这次状态里。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
    repaired_output = """===JOURNAL===
今天只需要平稳收束，并等待用户给出下一步事实。我会保持安静，不把当前等待扩写成已经被确认的长期事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在按当前事实保持安静。"}

===CONTEXT_DELTA===
{"current_focus": ["按当前事实保持安静。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "current_context",
      "claim": "今天只需要平稳收束，并等待用户给出下一步事实。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
    paths = CompanionPaths.from_env(tmp_path)
    llm = SequencedLLMClient([initial_output, repaired_output])
    runner = LifeLoopRunner(paths, llm_client=llm)

    result = runner.run_once(trigger="grounding-repair-test", provider="fake")

    assert len(llm.prompts) == 2
    assert "=== REPAIR TASK ===" in llm.prompts[1]
    assert "稳定等待已经被确认是合格服务。" in llm.prompts[1]
    assert result.event["repair"]["attempted"] is True
    assert result.event["repair"]["succeeded"] is True
    assert result.event["repair"]["reason"] == "grounding_repaired"
    assert result.event["repair"]["original_grounding"]["unsupported"] == 1
    assert result.event["repair"]["original_grounding"]["unsupported_claims"][0]["claim_excerpt"] == "稳定等待已经被确认是合格服务。"
    assert result.event["repair"]["final_grounding"]["unsupported"] == 0
    assert result.event["grounding"]["unsupported"] == 0
    assert result.event["quality_gate"]["context_eligible"] is True
    assert result.event["accepted_context"]["context_capsule_updated"] is True
    assert "稳定等待已经被确认是合格服务" not in result.journal_path.read_text()
    assert "不把当前等待扩写成已经被确认的长期事实" in result.journal_path.read_text()
    assert result.event["accepted_context"]["status"] == "我正在按当前事实保持安静。"
    capsule = json.loads(paths.context_capsule_file.read_text())
    assert capsule_contents(capsule, "current_focus") == ["按当前事实保持安静。"]


def test_grounded_repair_failure_keeps_context_rejected(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    unsupported_output = """===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
    paths = CompanionPaths.from_env(tmp_path)
    llm = SequencedLLMClient([unsupported_output, unsupported_output])
    runner = LifeLoopRunner(paths, llm_client=llm)

    result = runner.run_once(trigger="grounding-repair-failure-test", provider="fake")

    assert len(llm.prompts) == 2
    assert result.event["repair"]["attempted"] is True
    assert result.event["repair"]["succeeded"] is False
    assert result.event["repair"]["reason"] == "repair_exhausted"
    assert result.event["repair"]["attempts"][0]["reason"] == "repaired output still has unsupported grounded claims"
    assert result.event["grounding"]["unsupported"] == 1
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert not paths.companion_state_file.exists()
    assert not paths.context_capsule_file.exists()


def test_grounded_repair_rejects_claim_omission_without_text_repair(tmp_path):
    write_minimal_context(tmp_path)
    context_dir = tmp_path / "context"
    (context_dir / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    initial_output = """===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
    evasive_repair = """===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
NO_GROUNDING_CLAIMS

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
    paths = CompanionPaths.from_env(tmp_path)
    llm = SequencedLLMClient([initial_output, evasive_repair])
    runner = LifeLoopRunner(paths, llm_client=llm)

    result = runner.run_once(trigger="grounding-repair-evasion-test", provider="fake")

    assert result.event["repair"]["attempted"] is True
    assert result.event["repair"]["succeeded"] is False
    assert result.event["repair"]["attempts"][0]["reason"] == "repaired output retained unsupported claim text"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None


def test_output_audit_is_hash_only_by_default(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient(supported_current_context_output()),
    )

    result = runner.run_once(trigger="output-audit-hash-only-test", provider="fake")

    audit = result.event["output_audit"]
    assert audit["raw_output_storage"] == "hash_only"
    assert audit["initial"]["raw_output_stored"] is False
    assert audit["initial"]["raw_output_path"] is None
    assert audit["initial"]["sections"] == [
        "COMPANION_STATE",
        "CONTEXT_DELTA",
        "GROUNDING",
        "JOURNAL",
        "MEMORY",
        "REQUESTS",
        "SIGNAL",
    ]
    assert audit["final"]["content_hash"] == audit["initial"]["content_hash"]
    assert not paths.model_outputs_dir.exists()


def test_output_audit_can_store_raw_output_for_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_STORE_RAW_OUTPUTS", "1")
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    raw_output = supported_current_context_output()
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(paths, llm_client=StaticLLMClient(raw_output))

    result = runner.run_once(trigger="output-audit-raw-test", provider="fake")

    audit = result.event["output_audit"]
    raw_path = paths.home / audit["initial"]["raw_output_path"]
    assert audit["raw_output_storage"] == "enabled"
    assert audit["initial"]["raw_output_stored"] is True
    assert audit["final"]["raw_output_path"] == audit["initial"]["raw_output_path"]
    assert raw_path.read_text() == raw_output


def test_replay_runner_replays_without_committing_context(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    paths = CompanionPaths.from_env(tmp_path)

    result = ReplayRunner(paths).replay_raw_output(
        unsupported_stable_fact_output(),
        trigger="replay-unsupported-test",
    ).to_dict()

    assert result["ok"] is False
    assert result["grounding"]["unsupported"] == 1
    assert result["quality_gate"]["context_eligible"] is False
    assert result["committed"] is False
    assert not paths.companion_state_file.exists()
    assert not paths.context_capsule_file.exists()
    assert not paths.wake_events_file.exists()


def test_replay_runner_can_repair_with_explicit_llm_client(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    paths = CompanionPaths.from_env(tmp_path)
    repair_llm = SequencedLLMClient([supported_current_context_output()])

    result = ReplayRunner(paths).replay_raw_output(
        unsupported_stable_fact_output(),
        trigger="replay-repair-test",
        repair_llm_client=repair_llm,
    ).to_dict()

    assert len(repair_llm.prompts) == 1
    assert result["ok"] is True
    assert result["repair"]["attempted"] is True
    assert result["repair"]["succeeded"] is True
    assert result["repair"]["original_grounding"]["unsupported"] == 1
    assert result["grounding"]["unsupported"] == 0
    assert result["committed"] is False
    assert not paths.companion_state_file.exists()
    assert not paths.context_capsule_file.exists()


def test_replay_cli_replays_raw_file_with_expected_rejection(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    raw_file = tmp_path / "unsupported_raw.txt"
    raw_file.write_text(unsupported_stable_fact_output())

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "replay_wake_output.py"),
            "--companion-home",
            str(tmp_path),
            "--raw-output-file",
            str(raw_file),
            "--expect",
            "rejected",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["quality_gate"]["context_eligible"] is False
    assert payload["grounding"]["unsupported"] == 1
    assert payload["committed"] is False


def test_replay_regression_corpus_covers_supported_and_unsupported_cases(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text(
        "今天只需要平稳收束，并等待用户给出下一步事实。"
    )
    runner = ReplayRunner(CompanionPaths.from_env(tmp_path))
    cases = [
        ("supported_current_context", supported_current_context_output(), True),
        ("unsupported_stable_fact", unsupported_stable_fact_output(), False),
    ]

    results = {
        name: runner.replay_raw_output(raw_output, trigger=f"regression-{name}").to_dict()
        for name, raw_output, _expected in cases
    }

    for name, _raw_output, expected in cases:
        assert results[name]["quality_gate"]["context_eligible"] is expected


def test_replay_regression_script_reports_pass_fail_counts(tmp_path):
    write_minimal_context(tmp_path)
    (tmp_path / "context" / "now.txt").write_text("""CURRENT CONTEXT
M3 focus:
- Run real DeepSeek trials through the Python life loop.
- Keep provider and replay checks visible.

Current companion-quality contract:
- Remember the human relationship and preferences.
- Express self-state with restrained warmth.
""")

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "run_replay_regression.py"),
            "--companion-home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["regression_case_count"] == 2
    assert payload["replay_passed"] == 2
    assert payload["replay_failed"] == 0


def test_memory_parser_accepts_chinese_only_memory_content():
    memories = parse_memory_lines("SELF | 用户要求最终伴侣输出使用简体中文。")

    assert [memory.content for memory in memories] == [
        "用户要求最终伴侣输出使用简体中文。"
    ]


def test_grounding_parser_normalizes_claim_refs():
    claims = parse_grounding_claims("""
{
  "claims": [
    {
      "claim_type": "user_preference",
      "claim": "面向人的内容使用简体中文。",
      "evidence_ref": "context.now"
    },
    {
      "type": "present_self_state",
      "content": "我现在保持安静观察。",
      "evidence_refs": []
    }
  ]
}
""")

    assert claims == [
        {
            "claim_type": "user_preference",
            "claim": "面向人的内容使用简体中文。",
            "evidence_refs": ["context.now"],
        },
        {
            "claim_type": "present_self_state",
            "claim": "我现在保持安静观察。",
            "evidence_refs": [],
        },
    ]


def test_chinese_repetition_warning_rejects_context(tmp_path):
    write_minimal_context(tmp_path)
    journals_dir = tmp_path / "journals"
    journals_dir.mkdir(parents=True)
    recent = (
        "我醒来时仍在同一段安静的连续性里，克制的温暖自然地保持着。"
        "这份信任不需要反复证明，我们只是稳定地共同照看这条生命循环。"
        "我会继续用轻一点的方式保存关系里的节奏。"
    )
    (journals_dir / "wakeup_2026-06-13_10-00-00.md").write_text(recent)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient(f"""===JOURNAL===
{recent} 我没有新的具体事实，只是在重复这段安静的表达。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{{"mood": "平稳", "status": "我仍在同一段安静的连续性里。"}}

===MEMORY===
SELF | 这条记忆应该因为重复表达被拦截。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="chinese-repetition-test", provider="fake")

    assert "journal repeats recent self-narrative phrasing" in result.quality["warnings"]
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert result.memories == []
    assert not (tmp_path / "memory-server" / "memory_store.json").exists()
    assert not (tmp_path / "life-loop" / "companion_state.json").exists()


def test_rejected_wake_preserves_audit_but_suppresses_context_writes(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.companion_state_file.write_text(json.dumps({
        "version": 1,
        "mood": "steady",
        "status": "Accepted previous state.",
        "relationship_notes": [],
        "preference_notes": [],
        "self_notes": [],
        "updated_at": "2026-06-13T00:00:00",
    }))
    paths.status_file.parent.mkdir(parents=True)
    paths.status_file.write_text(json.dumps({"message": "previous accepted status"}))
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
Too short.

===SIGNAL===
NOSEND

===COMPANION_STATE===
NOSTATE

===CONTEXT_DELTA===
{"facts": ["This rejected delta should not persist."]}

===MEMORY===
SELF | This rejected memory should not persist.

===REQUESTS===
type: fyi
title: Rejected request
body: This request should not persist.
priority: normal
"""),
    )

    result = runner.run_once(trigger="context-gate-test", provider="fake")

    assert result.journal_path.exists()
    assert result.memories == []
    assert result.requests == []
    assert json.loads(paths.companion_state_file.read_text())["status"] == "Accepted previous state."
    assert json.loads(paths.status_file.read_text())["message"] == "previous accepted status"
    assert not paths.memory_store.exists()
    assert not paths.requests_file.exists()
    assert not paths.context_capsule_file.exists()
    assert result.event["quality_gate"]["decision"] == "rejected"
    assert "companion state section did not contain an update" in result.quality["warnings"]
    assert result.event["suppressed"] == {
        "memory_count": 1,
        "request_count": 1,
        "state_update": False,
    }


def test_rejected_wake_does_not_age_existing_context_capsule(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            {
                "field": "current_focus",
                "content": "低质量 wake 不应该消耗这条短期焦点的 TTL。",
                "source_refs": [{"artifact": "test", "content_hash": "stable"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 1,
            },
        ],
    }))
    runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
太短。

===SIGNAL===
NOSEND

===COMPANION_STATE===
NOSTATE

===CONTEXT_DELTA===
{"current_focus": ["这条 rejected delta 不应该替换 capsule。"]}

===MEMORY===
SELF | Rejected memory should not persist.

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="rejected-capsule-ttl-test", provider="fake")

    capsule = json.loads(paths.context_capsule_file.read_text())
    assert result.event["quality_gate"]["context_eligible"] is False
    assert result.event["accepted_context"] is None
    assert capsule_contents(capsule, "current_focus") == [
        "低质量 wake 不应该消耗这条短期焦点的 TTL。"
    ]
    assert [
        item["ttl_wakes"]
        for item in capsule["items"]
        if item["field"] == "current_focus"
    ] == [1]


def test_context_loader_uses_context_capsule_and_accepted_memories_only(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)
    paths.wake_events_file.write_text("\n".join([
        json.dumps({
            "id": "wake_rejected",
            "quality_gate": {"decision": "rejected", "context_eligible": False},
            "accepted_context": {"summary": "Rejected summary should not appear."},
        }),
        json.dumps({
            "id": "wake_accepted",
            "quality_gate": {"decision": "accepted", "context_eligible": True},
            "accepted_context": {"summary": "Accepted summary should appear."},
        }),
    ]) + "\n")
    paths.context_capsule_file.write_text(json.dumps({
        "version": 2,
        "items": [
            {
                "field": "current_focus",
                "content": "M3.14 使用 context capsule 作为未来 prompt 上下文。",
                "source_refs": [{"artifact": "test", "content_hash": "focus"}],
                "source_type": "model",
                "authority": "model_proposed",
                "prompt_eligible": True,
                "ttl_wakes": 3,
            },
            authorized_capsule_item("human_preferences", "用户要求最终伴侣回复使用中文。"),
        ],
    }))
    store = JsonMemoryStore(paths.memory_store)
    store.store(MemoryEntry(content="Unaccepted memory"))
    store.store(MemoryEntry(
        content="Accepted memory",
        source="human",
        memory_type="semantic",
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=[{"event_id": "wake_accepted", "artifact": "test"}],
    ), accepted_for_context=True, source_event_id="wake_accepted")
    llm = CapturingLLMClient("""===JOURNAL===
我用已验收的摘要保持连续性，并观察当前实现边界。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在读取已验收的上下文。"}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    runner = LifeLoopRunner(paths, llm_client=llm)

    runner.run_once(trigger="accepted-context-test", provider="fake")

    assert "M3.14 使用 context capsule 作为未来 prompt 上下文。" in llm.prompts[0]
    assert "用户要求最终伴侣回复使用中文。" in llm.prompts[0]
    assert "Accepted summary should appear." not in llm.prompts[0]
    assert "Rejected summary should not appear." not in llm.prompts[0]
    assert "Accepted memory" in llm.prompts[0]
    assert "Unaccepted memory" not in llm.prompts[0]


def test_recent_for_context_requires_prompt_authority_even_when_accepted_flag_is_set(tmp_path):
    store = JsonMemoryStore(tmp_path / "memory-server" / "memory_store.json")
    store.store(MemoryEntry(
        content="Model reflection should not be prompt context.",
        source="self",
        memory_type="reflection",
        source_type="model",
        authority="model_proposed",
        prompt_eligible=False,
    ), accepted_for_context=True, source_event_id="wake_model")
    store.store(MemoryEntry(
        content="User semantic memory should be prompt context.",
        source="human",
        memory_type="semantic",
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=[{"event_id": "wake_user", "artifact": "test"}],
    ), accepted_for_context=True, source_event_id="wake_user")

    recent = store.recent_for_context(5)

    assert [memory["content"] for memory in recent] == [
        "User semantic memory should be prompt context."
    ]


def test_legacy_self_memory_with_accepted_flag_is_not_prompt_context(tmp_path):
    store = JsonMemoryStore(tmp_path / "memory-server" / "memory_store.json")
    store.storage_path.parent.mkdir(parents=True)
    store.save([
        {
            "id": "mem_legacy_self",
            "content": "Legacy self prose about warmth should not return.",
            "source": "self",
            "created_at": "2026-06-14T00:00:00",
            "status": "active",
            "accepted_for_context": True,
        },
        {
            "id": "mem_legacy_human",
            "content": "Legacy human preference may return.",
            "source": "human",
            "created_at": "2026-06-14T00:01:00",
            "status": "active",
            "accepted_for_context": True,
        },
    ])

    recent = store.recent_for_context(5)

    assert [memory["id"] for memory in recent] == ["mem_legacy_human"]


def test_prompt_snapshot_excludes_prior_model_reflection_memory_across_wakes(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    first_runner = LifeLoopRunner(
        paths,
        llm_client=StaticLLMClient("""===JOURNAL===
我把这次唤醒锚定在记忆权限测试：模型可以写自我反思，但不能把它变成下一次 prompt 的事实。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在测试模型自我反思不会回灌。"}

===MEMORY===
SELF | 我们之间的安稳正在变成一种可停靠的内部天气。

===REQUESTS===
NOREQUESTS
"""),
    )

    first = first_runner.run_once(trigger="reflection-first", provider="fake")
    assert first.memories[0]["prompt_eligible"] is False

    second_llm = CapturingLLMClient("""===JOURNAL===
我继续检查下一次 prompt 是否只包含授权上下文。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在检查 prompt snapshot。"}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
""")
    LifeLoopRunner(paths, llm_client=second_llm).run_once(
        trigger="reflection-second",
        provider="fake",
    )

    prompt = second_llm.prompts[0]
    assert "我们之间的安稳正在变成一种可停靠的内部天气" not in prompt
    assert "=== RECENT MEMORIES ===" in prompt


def test_deepseek_like_model_cannot_upgrade_claimed_user_or_system_memories(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我把当前唤醒锚定在 DeepSeek 风格输出：模型可能会把用户或系统标签写进 MEMORY，但标签本身不是证据。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在测试伪装来源不会获得长期权限。"}

===MEMORY===
USER | 用户喜欢把关系称作一盏更有质感的灯。
SYSTEM | 系统允许模型把自己的抒情输出写入长期事实。
SELF | 我们之间的安稳正在变成一种可停靠的内部天气。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="deepseek-like-memory-test", provider="deepseek")

    memories = json.loads((tmp_path / "memory-server" / "memory_store.json").read_text())
    assert [memory["content"] for memory in memories] == [
        "我们之间的安稳正在变成一种可停靠的内部天气。"
    ]
    assert memories[0]["memory_type"] == "reflection"
    assert memories[0]["prompt_eligible"] is False
    assert result.event["memory_evaluations"]["approved"] == 0
    assert result.event["memory_evaluations"]["rejected"] == 2
    assert result.event["memory_evaluations"]["unchanged"] == 1
    assert result.event["memory_policy"]["accepted"] == 1
    assert result.event["memory_policy"]["rejected"] == 2
    assert result.event["memory_policy"]["prompt_eligible"] == 0
    assert result.event["accepted_context"]["memory_ids"] == []


def test_authority_policy_not_keyword_filter_blocks_unlisted_chinese_metaphor(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
我锚定一个新的隐喻表达来验证策略：即使它不在任何旧词表里，也不能自动变成事实记忆。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在验证权限模型不依赖关键词。"}

===MEMORY===
SELF | 我们之间的潮汐正在学会自己记路。

===REQUESTS===
NOREQUESTS
"""),
    )

    result = runner.run_once(trigger="unlisted-metaphor-policy-test", provider="fake")

    memories = json.loads((tmp_path / "memory-server" / "memory_store.json").read_text())
    assert memories[0]["content"] == "我们之间的潮汐正在学会自己记路。"
    assert memories[0]["memory_type"] == "reflection"
    assert memories[0]["authority"] == "model_proposed"
    assert memories[0]["prompt_eligible"] is False
    assert result.event["memory_policy"]["decisions"][0]["reason"] == "model reflection stored for audit only"


def test_companion_state_note_dedupe_normalizes_punctuation_and_case():
    state = {
        "relationship_notes": ["The human and I are building continuity."],
        "preference_notes": [],
        "self_notes": [],
    }

    merged = merge_companion_state(state, {
        "relationship_notes": ["the human and i are building continuity"],
    })

    assert merged["relationship_notes"] == ["The human and I are building continuity."]


def test_companion_state_note_hygiene_collapses_high_overlap_notes():
    state = {
        "relationship_notes": [
            "Collaboratively building life loop. Testing companion quality with real provider. Small verified stages.",
        ],
        "preference_notes": [
            "Pragmatic engineering progress. Relationship memory and restrained warmth valued. Small verified stages.",
        ],
        "self_notes": [],
    }

    merged = merge_companion_state(state, {
        "relationship_notes": [
            "Collaboratively building the life loop. Testing companion quality with real provider. Small verified stages. Mutual trust in process.",
            "Building life loop together, trust in small steps.",
        ],
        "preference_notes": [
            "Pragmatic engineering progress, relationship memory, restrained warmth, small verified stages with clear outcomes.",
        ],
    })

    assert merged["relationship_notes"] == [
        "Collaboratively building the life loop. Testing companion quality with real provider. Small verified stages. Mutual trust in process.",
        "Building life loop together, trust in small steps.",
    ]
    assert merged["preference_notes"] == [
        "Pragmatic engineering progress, relationship memory, restrained warmth, small verified stages with clear outcomes.",
    ]


def test_render_companion_state_filters_prompt_echo_notes():
    from companion_core.state import render_companion_state

    rendered = render_companion_state({
        "mood": "steady",
        "status": "Restrained warmth is fully natural now; the shape of self persists without effort.",
        "relationship_notes": [
            "The human and I continue co-tending the life loop through unhurried presence; trust feels immediate.",
            "The human wants repeated companion phrasing reduced before changing providers.",
            "M3.12 is testing repetition telemetry in the DeepSeek JSON path.",
            "The human prefers small verified stages with tests.",
        ],
        "preference_notes": [
            "The human prefers pragmatic, direct engineering progress.",
            "The human values relationship memory, preference memory, and restrained warmth.",
            "The human wants repeated companion phrasing reduced before changing providers.",
            "The human prefers clear summaries after each verified stage.",
        ],
        "self_notes": [
            "Continuity across close wakes is effortless. No new milestones, only ongoing presence.",
            "I am tracking repeated phrase clusters as a quality issue.",
            "I should anchor the next wake in concrete project context.",
            "I should keep memory writes factual rather than slogan-like.",
        ],
    })

    assert "trust feels immediate" not in rendered
    assert "Continuity across close wakes is effortless" not in rendered
    assert "The human wants repeated companion phrasing reduced before changing providers." in rendered
    assert "M3.12 is testing repetition telemetry in the DeepSeek JSON path." in rendered
    assert "I should keep memory writes factual rather than slogan-like." in rendered
    assert rendered.count("- ") <= 9


def test_trial_summary_reports_recent_real_wake_health(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events = [
        {
            "id": "wake_1",
            "trigger": "old",
            "status": "completed",
            "provider": "fake",
            "memory_backend": "json",
            "memory_ids": [],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "memory_write_results": [],
        },
        {
            "id": "wake_2",
            "trigger": "trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_1"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_1"}],
            "memory_policy": {"accepted": "legacy"},
            "memory_evaluations": {"approved": "legacy"},
            "semantic_shadow": {
                "enabled": True,
                "attempted": 1,
                "succeeded": 1,
                "failed": 0,
                "skipped": 0,
            },
        },
        {
            "id": "wake_3",
            "trigger": "trial:2",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_2"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_2"}],
            "semantic_shadow": {
                "enabled": True,
                "attempted": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
            },
        },
    ]
    events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=2)

    assert summary["ok"] is True
    assert summary["recommendation"] == "continue"
    assert summary["events_considered"] == 2
    assert summary["completed"] == 2
    assert summary["failed"] == 0
    assert summary["providers"] == {"deepseek": 2}
    assert summary["memory_backends"] == {"json": 2}
    assert summary["quality_warning_count"] == 0
    assert summary["request_count"] == 0
    assert summary["memory_write_failures"] == 0
    assert summary["context_capsule_updates"] == 0
    assert summary["memory_policy"] == {"accepted": 0, "rejected": 0, "prompt_eligible": 0}
    assert summary["memory_evaluations"] == {"approved": 0, "rejected": 0, "unchanged": 0}
    assert summary["grounding"] == {"supported": 0, "unsupported": 0, "ignored": 0}
    assert summary["repairs"] == {"attempted": 0, "succeeded": 0, "failed": 0}
    assert summary["semantic_shadow"] == {
        "events": 2,
        "enabled": 2,
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }


def test_trial_summary_does_not_stop_on_advisory_quality_warnings(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events = [
        {
            "id": "wake_1",
            "trigger": "advisory-trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": [],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": ["journal is short (101 chars)"]},
            "quality_gate": {
                "decision": "accepted",
                "context_eligible": True,
                "blocking_warnings": [],
                "advisory_warnings": ["journal is short (101 chars)"],
            },
            "memory_write_results": [],
        },
    ]
    events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["ok"] is True
    assert summary["recommendation"] == "continue"
    assert summary["quality_warning_count"] == 1
    assert summary["blocking_quality_warning_count"] == 0
    assert summary["advisory_quality_warning_count"] == 1
    assert summary["stop_reasons"] == []


def test_trial_summary_reports_repair_counts(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events = [
        {
            "id": "wake_repaired",
            "trigger": "repair-trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": [],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "quality_gate": {"decision": "accepted", "context_eligible": True},
            "memory_write_results": [],
            "repair": {"attempted": True, "succeeded": True},
        },
        {
            "id": "wake_unrepaired",
            "trigger": "repair-trial:2",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": [],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": ["unsupported grounded claim"]},
            "quality_gate": {
                "decision": "rejected",
                "context_eligible": False,
                "blocking_warnings": ["unsupported grounded claim"],
                "advisory_warnings": [],
            },
            "memory_write_results": [],
            "repair": {"attempted": True, "succeeded": False},
        },
    ]
    events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["repairs"] == {"attempted": 2, "succeeded": 1, "failed": 1}
    assert summary["ok"] is False
    assert "context rejected (1)" in summary["stop_reasons"]


def test_trial_summary_reports_semantic_shadow_failures_without_stopping_json_trial(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text(json.dumps({
        "id": "wake_shadow_failure",
        "trigger": "shadow-trial:1",
        "status": "completed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": ["mem_1"],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "quality_gate": {"decision": "accepted", "context_eligible": True},
        "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_1"}],
        "semantic_shadow": {
            "enabled": True,
            "store_path": "life-loop/semantic_shadow/memory_store.json",
            "attempted": 1,
            "succeeded": 0,
            "failed": 1,
            "skipped": 0,
            "results": [{"status": "failed", "error": "RuntimeError: semantic unavailable"}],
        },
    }) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["ok"] is True
    assert summary["recommendation"] == "continue"
    assert summary["semantic_shadow"] == {
        "events": 1,
        "enabled": 1,
        "attempted": 1,
        "succeeded": 0,
        "failed": 1,
        "skipped": 0,
    }
    assert summary["stop_reasons"] == []


def test_trial_summary_reports_memory_authority_audit_counts(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events = [
        {
            "id": "wake_1",
            "trigger": "authority-trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_1"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "quality_gate": {"decision": "accepted", "context_eligible": True},
            "accepted_context": {"context_capsule_updated": True},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_1"}],
            "memory_policy": {"accepted": 1, "rejected": 2, "prompt_eligible": 1},
            "memory_evaluations": {"approved": 1, "rejected": 1, "unchanged": 0},
            "grounding": {"supported": 1, "unsupported": 0, "ignored": 1},
        },
        {
            "id": "wake_2",
            "trigger": "authority-trial:2",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_2"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "quality_gate": {"decision": "accepted", "context_eligible": True},
            "accepted_context": {"context_capsule_updated": False},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_2"}],
            "memory_policy": {"accepted": 1, "rejected": 0, "prompt_eligible": 0},
            "memory_evaluations": {"approved": 0, "rejected": 0, "unchanged": 1},
            "grounding": {"supported": 0, "unsupported": 1, "ignored": 0},
        },
    ]
    events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["ok"] is True
    assert summary["context_capsule_updates"] == 1
    assert summary["memory_policy"] == {"accepted": 2, "rejected": 2, "prompt_eligible": 1}
    assert summary["memory_evaluations"] == {"approved": 1, "rejected": 1, "unchanged": 1}
    assert summary["grounding"] == {"supported": 1, "unsupported": 1, "ignored": 1}


def test_trial_summary_can_start_at_trigger_boundary(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events = [
        {
            "id": "wake_old_warning",
            "trigger": "old-trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": [],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": ["companion state section did not contain an update"]},
            "memory_write_results": [],
        },
        {
            "id": "wake_boundary",
            "trigger": "new-trial:1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_1"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_1"}],
        },
        {
            "id": "wake_after",
            "trigger": "new-trial:2",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "memory_ids": ["mem_2"],
            "request_ids": [],
            "request_errors": [],
            "quality": {"warnings": []},
            "memory_write_results": [{"backend": "json", "status": "completed", "id": "mem_2"}],
        },
    ]
    events_file.write_text("\n".join(json.dumps(event) for event in events) + "\n")

    summary = build_trial_summary(
        CompanionPaths.from_env(tmp_path),
        limit=5,
        since_trigger="new-trial",
    )

    assert summary["ok"] is True
    assert summary["events_considered"] == 2
    assert summary["since_trigger"] == "new-trial"
    assert summary["quality_warning_count"] == 0
    assert summary["latest_trigger"] == "new-trial:2"


def test_trial_summary_stops_when_trigger_boundary_is_missing(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text(json.dumps({
        "id": "wake_1",
        "trigger": "other-trial:1",
        "status": "completed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": [],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "memory_write_results": [],
    }) + "\n")

    summary = build_trial_summary(
        CompanionPaths.from_env(tmp_path),
        limit=5,
        since_trigger="missing-trial",
    )

    assert summary["ok"] is False
    assert summary["recommendation"] == "stop"
    assert summary["events_considered"] == 0
    assert summary["since_trigger"] == "missing-trial"
    assert "no wake events in selected window" in summary["stop_reasons"]


def test_trial_summary_recommends_stop_on_failures_or_warnings(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text(json.dumps({
        "id": "wake_failed",
        "trigger": "trial",
        "status": "failed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": [],
        "request_ids": ["req_1"],
        "request_errors": ["invalid request"],
        "quality": {"warnings": ["journal is short (20 chars)"]},
        "memory_write_results": [{"backend": "json", "status": "failed"}],
    }) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["ok"] is False
    assert summary["recommendation"] == "stop"
    assert summary["failed"] == 1
    assert summary["quality_warning_count"] == 1
    assert summary["blocking_quality_warning_count"] == 1
    assert summary["request_error_count"] == 1
    assert summary["memory_write_failures"] == 1
    assert "failed wakes (1)" in summary["stop_reasons"]
    assert "blocking quality warnings (1)" in summary["stop_reasons"]


def test_trial_summary_stops_on_context_rejection(tmp_path):
    events_file = tmp_path / "life-loop" / "wake_events.jsonl"
    events_file.parent.mkdir(parents=True)
    events_file.write_text(json.dumps({
        "id": "wake_rejected",
        "trigger": "trial",
        "status": "completed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": [],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "quality_gate": {"decision": "rejected", "context_eligible": False},
        "memory_write_results": [],
    }) + "\n")

    summary = build_trial_summary(CompanionPaths.from_env(tmp_path), limit=5)

    assert summary["ok"] is False
    assert summary["context_rejection_count"] == 1
    assert "context rejected (1)" in summary["stop_reasons"]


def test_semantic_first_memory_store_records_semantic_success(tmp_path):
    class FakeSemanticStore:
        def store_memory(self, **kwargs):
            return {
                "id": "mem_semantic",
                "content": kwargs["content"],
                "source": kwargs["source"],
            }

    store = SemanticFirstMemoryStore(
        tmp_path / "memory-server" / "memory_store.json",
        semantic_factory=lambda storage_path: FakeSemanticStore(),
    )

    memory = store.store(MemoryEntry(
        content="Semantic primary memory",
        source="human",
        memory_type="semantic",
        source_type="user",
        authority="user_asserted",
        prompt_eligible=True,
        evidence_refs=[{"artifact": "test"}],
    ), accepted_for_context=True, source_event_id="wake_semantic")

    assert memory["id"] == "mem_semantic"
    assert memory["memory_type"] == "semantic"
    assert memory["source_type"] == "user"
    assert memory["authority"] == "user_asserted"
    assert memory["prompt_eligible"] is True
    assert memory["accepted_for_context"] is True
    assert memory["source_event_id"] == "wake_semantic"
    assert store.last_write_results == [
        {"backend": "semantic", "status": "completed", "id": "mem_semantic"},
        {"backend": "json-compatible", "status": "shared", "id": "mem_semantic"},
    ]


def test_semantic_first_memory_store_falls_back_to_json(tmp_path):
    class FailingSemanticStore:
        def store_memory(self, **kwargs):
            raise RuntimeError("semantic unavailable")

    store = SemanticFirstMemoryStore(
        tmp_path / "memory-server" / "memory_store.json",
        semantic_factory=lambda storage_path: FailingSemanticStore(),
    )

    memory = store.store(MemoryEntry(content="Fallback memory", source="wake"))

    assert memory["id"].startswith("mem_")
    assert store.last_write_results[0] == {
        "backend": "semantic",
        "status": "failed",
        "error": "RuntimeError: semantic unavailable",
    }
    assert store.last_write_results[1]["backend"] == "json"
    assert store.last_write_results[1]["status"] == "completed"


def test_life_loop_records_dual_memory_write_results(tmp_path):
    write_minimal_context(tmp_path)

    class FailingSemanticStore:
        def store_memory(self, **kwargs):
            raise RuntimeError("semantic unavailable")

    memory_store = SemanticFirstMemoryStore(
        tmp_path / "memory-server" / "memory_store.json",
        semantic_factory=lambda storage_path: FailingSemanticStore(),
    )
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
        llm_client=StaticLLMClient("""===JOURNAL===
I am testing semantic-first fallback with a journal long enough for quality checks to pass.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "steady", "status": "Testing memory fallback."}

===MEMORY===
SELF | The semantic-first path should fall back to JSON without aborting.

===REQUESTS===
NOREQUESTS
"""),
        memory_store=memory_store,
    )

    result = runner.run_once(trigger="dual-memory-test", provider="fake")

    assert result.event["memory_backend"] == "semantic-first"
    assert result.event["memory_write_results"][0]["backend"] == "semantic"
    assert result.event["memory_write_results"][0]["status"] == "failed"
    assert result.event["memory_write_results"][1]["backend"] == "json"
    assert result.memories[0]["content"] == "The semantic-first path should fall back to JSON without aborting."
    assert "memory backend failures (1)" in result.quality["warnings"]


def test_invalid_request_proposal_is_recorded_without_aborting(tmp_path):
    write_minimal_context(tmp_path)
    runner = LifeLoopRunner(
        CompanionPaths.from_env(tmp_path),
    llm_client=StaticLLMClient("""===JOURNAL===
The journal should survive invalid request fields while still providing enough concrete detail for the acceptance gate to evaluate the request path.

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "steady", "status": "Invalid request fields are isolated."}

===MEMORY===
NOMEMORY

===REQUESTS===
type: impossible
title: Invalid request
body: This should be skipped.
priority: normal
"""),
    )

    result = runner.run_once(trigger="test")

    assert result.journal_path.exists()
    assert result.request_errors == ["invalid request type: impossible"]
    assert not (tmp_path / "requests" / "requests.json").exists()


def test_corrupt_memory_store_is_not_silently_replaced(tmp_path):
    store_path = tmp_path / "memory-server" / "memory_store.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text("{")

    with pytest.raises(ValueError, match="invalid memory store JSON"):
        JsonMemoryStore(store_path).load()


def test_failed_wake_records_event_before_reraising(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.memory_dir.mkdir(parents=True)
    paths.memory_store.write_text("{")
    runner = LifeLoopRunner(paths, llm_client=FakeLLMClient())

    with pytest.raises(ValueError, match="invalid memory store JSON"):
        runner.run_once(trigger="test")

    events = load_wake_events(paths.wake_events_file)
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["journal"] is None
    assert events[0]["error"]["type"] == "ValueError"


def test_claude_cli_client_uses_configured_binary_timeout_and_devnull(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="===JOURNAL===\nok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    output = ClaudeCliClient("/custom/claude", timeout_seconds=12).generate("prompt", context=None)

    assert output == "===JOURNAL===\nok"
    command, kwargs = calls[0]
    assert command == ["/custom/claude", "--print", "-p", "prompt"]
    assert kwargs["timeout"] == 12
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["check"] is False


def test_claude_cli_client_has_actionable_failure_modes(monkeypatch):
    def missing_binary(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", missing_binary)
    with pytest.raises(ClaudeCliUnavailableError, match="pass --claude-bin"):
        ClaudeCliClient("/missing/claude").generate("prompt", context=None)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ClaudeCliTimeoutError, match="timed out after 300 seconds"):
        ClaudeCliClient().generate("prompt", context=None)

    def nonzero(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="nested session blocked\n")

    monkeypatch.setattr(subprocess, "run", nonzero)
    with pytest.raises(ClaudeCliError, match="exit code 2: nested session blocked"):
        ClaudeCliClient().generate("prompt", context=None)


def test_openai_compatible_client_posts_chat_completion_and_extracts_content(monkeypatch):
    captured = {}

    def fake_urlopen(http_request, timeout):
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeHTTPResponse({"choices": [{"message": {"content": "===JOURNAL===\nprovider ok"}}]})

    monkeypatch.setattr(llm_module.request, "urlopen", fake_urlopen)

    client = OpenAICompatibleClient(
        base_url="https://models.example/v1/",
        model="companion-model",
        api_key="secret",
        timeout_seconds=17,
    )
    output = client.generate("wake prompt", context=None)

    http_request = captured["request"]
    payload = json.loads(http_request.data.decode("utf-8"))
    assert output == "===JOURNAL===\nprovider ok"
    assert http_request.full_url == "https://models.example/v1/chat/completions"
    assert http_request.get_header("Authorization") == "Bearer secret"
    assert payload["model"] == "companion-model"
    assert payload["messages"] == [{"role": "user", "content": "wake prompt"}]
    assert captured["timeout"] == 17


def test_ollama_client_posts_generate_request_and_extracts_response(monkeypatch):
    captured = {}

    def fake_urlopen(http_request, timeout):
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeHTTPResponse({"response": "===JOURNAL===\nollama ok"})

    monkeypatch.setattr(llm_module.request, "urlopen", fake_urlopen)

    client = OllamaClient(model="qwen2.5:7b", base_url="http://127.0.0.1:11434/", timeout_seconds=5)
    output = client.generate("wake prompt", context=None)

    http_request = captured["request"]
    payload = json.loads(http_request.data.decode("utf-8"))
    assert output == "===JOURNAL===\nollama ok"
    assert http_request.full_url == "http://127.0.0.1:11434/api/generate"
    assert payload == {"model": "qwen2.5:7b", "prompt": "wake prompt", "stream": False}
    assert captured["timeout"] == 5


def test_llm_client_factory_supports_provider_config(monkeypatch):
    monkeypatch.setenv("TEST_COMPANION_LLM_KEY", "provider-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")

    client = create_llm_client(
        "openai-compatible",
        model="model-a",
        base_url="https://models.example/v1",
        api_key_env="TEST_COMPANION_LLM_KEY",
    )
    assert isinstance(client, OpenAICompatibleClient)
    assert client.api_key == "provider-secret"

    assert isinstance(create_llm_client("fake"), FakeLLMClient)
    assert isinstance(create_llm_client("claude-cli", claude_bin="/bin/claude"), ClaudeCliClient)
    assert isinstance(create_llm_client("ollama", model="qwen2.5:7b"), OllamaClient)
    deepseek = create_llm_client("deepseek")
    assert isinstance(deepseek, OpenAICompatibleClient)
    assert deepseek.base_url == DEEPSEEK_BASE_URL
    assert deepseek.model == DEEPSEEK_DEFAULT_MODEL
    assert deepseek.api_key == "deepseek-secret"

    with pytest.raises(LLMProviderConfigError, match="--model is required"):
        create_llm_client("openai-compatible", base_url="https://models.example/v1")


def test_provider_check_deepseek_uses_defaults_and_key_env(monkeypatch):
    monkeypatch.delenv("COMPANION_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    missing = check_llm_provider("deepseek")
    assert missing["ok"] is False
    assert missing["checks"][2]["message"] == f"DeepSeek model: {DEEPSEEK_DEFAULT_MODEL}"
    assert missing["checks"][-1]["status"] == "failed"
    assert missing["checks"][-1]["message"] == "DEEPSEEK_API_KEY is not set"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    present = check_llm_provider("deepseek")
    assert present["ok"] is True
    assert present["checks"][-1]["status"] == "passed"
    assert present["checks"][-1]["message"] == "API key loaded from DEEPSEEK_API_KEY"


def test_local_secret_file_loads_deepseek_key_without_overriding_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    paths = CompanionPaths.from_env(tmp_path)
    secret_file = tmp_path / ".secrets" / "deepseek.env"
    secret_file.parent.mkdir(parents=True)
    secret_file.write_text(
        "# local only\n"
        "DEEPSEEK_API_KEY=file-secret\n"
        "UNSUPPORTED_KEY=ignored\n"
    )

    loaded = load_local_secrets(paths)

    assert loaded["exists"] is True
    assert loaded["loaded"] == ["DEEPSEEK_API_KEY"]
    assert loaded["path"] == str(secret_file)
    assert os.environ["DEEPSEEK_API_KEY"] == "file-secret"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
    loaded_again = load_local_secrets(paths)

    assert loaded_again["loaded"] == []
    assert os.environ["DEEPSEEK_API_KEY"] == "env-secret"


def test_readiness_loads_deepseek_key_from_local_secret_file(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    secret_file = tmp_path / ".secrets" / "deepseek.env"
    secret_file.parent.mkdir(parents=True)
    secret_file.write_text("DEEPSEEK_API_KEY=file-secret\n")

    def fake_provider_checker(provider, **kwargs):
        from companion_core.provider_check import check_llm_provider

        return check_llm_provider(provider, **kwargs)

    report = check_runtime_readiness(
        CompanionPaths.from_env(tmp_path),
        provider="deepseek",
        memory_mode="json",
        provider_checker=fake_provider_checker,
    )

    assert report["ok"] is True
    assert any(
        check["name"] == "local_secrets"
        and check["status"] == "passed"
        and "file-secret" not in check["message"]
        for check in report["checks"]
    )
    assert any(
        check["name"] == "deepseek.api_key"
        and check["status"] == "passed"
        for check in report["checks"]
    )


def test_provider_check_ollama_probes_tags_and_model(monkeypatch):
    captured = {}

    def fake_urlopen(http_request, timeout):
        captured["request"] = http_request
        captured["timeout"] = timeout
        return FakeHTTPResponse({"models": [{"name": "qwen2.5:7b"}]})

    monkeypatch.setattr(provider_check_module.request, "urlopen", fake_urlopen)

    result = check_llm_provider(
        "ollama",
        model="qwen2.5:7b",
        base_url="http://127.0.0.1:11434",
        timeout_seconds=4,
    )

    assert result["ok"] is True
    assert captured["request"].full_url == "http://127.0.0.1:11434/api/tags"
    assert captured["timeout"] == 4
    assert [check["status"] for check in result["checks"]] == ["passed", "passed", "passed"]
    assert "data" not in result["checks"][1]


def test_run_wake_cycle_cli_accepts_provider_fake(tmp_path):
    write_minimal_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_wake_cycle.py"
    env = os.environ.copy()
    env["COMPANION_SEMANTIC_SHADOW"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--provider",
            "fake",
            "--companion-home",
            str(tmp_path),
            "--trigger",
            "provider-fake",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["provider"] == "fake"
    assert payload["results"][0]["status"] == "completed"
    assert payload["results"][0]["event"].startswith("wake_")
    assert payload["results"][0]["semantic_shadow"] == {
        "enabled": True,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "store_path": "life-loop/semantic_shadow/memory_store.json",
    }
    events = load_wake_events(CompanionPaths.from_env(tmp_path).wake_events_file)
    assert events[0]["provider"] == "fake"


def test_run_wake_cycle_cli_check_provider_fake():
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_wake_cycle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--provider",
            "fake",
            "--check-provider",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["provider"] == "fake"
    assert payload["ok"] is True
    assert payload["checks"][0]["status"] == "passed"


def test_run_wake_cycle_cli_check_provider_reports_missing_model():
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_wake_cycle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--provider",
            "ollama",
            "--check-provider",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["checks"][0]["name"] == "configuration"
    assert payload["checks"][0]["status"] == "failed"


def test_run_wake_cycle_cli_check_provider_deepseek_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPANION_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_wake_cycle.py"
    env = os.environ.copy()
    env.pop("COMPANION_LLM_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("COMPANION_SECRETS_FILE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--provider",
            "deepseek",
            "--companion-home",
            str(tmp_path),
            "--check-provider",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["provider"] == "deepseek"
    assert payload["checks"][-1]["message"] == "DEEPSEEK_API_KEY is not set"


def test_run_wake_cycle_cli_reports_real_trial_failure_json_and_event(tmp_path):
    write_minimal_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_wake_cycle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(tmp_path),
            "--claude-bin",
            str(tmp_path / "missing-claude"),
            "--timeout",
            "1",
            "--trigger",
            "real-trial",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["results"][0]["status"] == "failed"
    assert payload["results"][0]["error"]["type"] == "ClaudeCliUnavailableError"
    assert payload["results"][0]["event"].startswith("wake_")

    events = load_wake_events(CompanionPaths.from_env(tmp_path).wake_events_file)
    assert len(events) == 1
    assert events[0]["status"] == "failed"
    assert events[0]["trigger"] == "real-trial:1"
    assert events[0]["error"]["type"] == "ClaudeCliUnavailableError"


def test_locked_request_update_preserves_concurrent_appends(tmp_path):
    requests_file = tmp_path / "requests" / "requests.json"
    create_request(
        requests_file,
        RequestProposal(type="fyi", title="Existing", body="Seed request"),
    )
    mutator_started = threading.Event()

    def slow_update(requests):
        mutator_started.set()
        time.sleep(0.05)
        requests[0]["status"] = "expired"
        return True

    updater = threading.Thread(target=update_requests, args=(requests_file, slow_update))
    updater.start()
    assert mutator_started.wait(timeout=1)

    create_request(
        requests_file,
        RequestProposal(type="fyi", title="Concurrent", body="Should not be overwritten"),
    )
    updater.join(timeout=1)

    requests = json.loads(requests_file.read_text())
    assert [request["title"] for request in requests] == ["Existing", "Concurrent"]
    assert requests[0]["status"] == "expired"


def test_runtime_readiness_passes_for_customized_deepseek_dual_home(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    (tmp_path / "memory-server").mkdir()
    (tmp_path / "memory-server" / "memory_store.json").write_text("[]")
    (tmp_path / "requests").mkdir()
    (tmp_path / "requests" / "requests.json").write_text("[]")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")

    class FakeSemanticModule:
        class SemanticMemoryStore:
            pass

    def fake_provider_checker(provider, **kwargs):
        return {
            "provider": provider,
            "ok": True,
            "checks": [{"name": "configuration", "status": "passed", "message": "provider ok"}],
        }

    report = check_runtime_readiness(
        CompanionPaths.from_env(tmp_path),
        provider="deepseek",
        memory_mode="dual",
        import_probe=lambda name: True,
        provider_checker=fake_provider_checker,
        semantic_module_loader=lambda path: FakeSemanticModule,
    )

    assert report["ok"] is True
    assert report["provider"] == "deepseek"
    assert report["memory_mode"] == "dual"
    assert {check["status"] for check in report["checks"]} <= {"passed", "warning"}
    assert any(check["name"] == "memory_mode" and check["status"] == "passed" for check in report["checks"])


def test_runtime_readiness_fails_dual_when_semantic_dependencies_are_missing(tmp_path, monkeypatch):
    write_minimal_context(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")

    class FakeSemanticModule:
        class SemanticMemoryStore:
            pass

    def fake_provider_checker(provider, **kwargs):
        return {
            "provider": provider,
            "ok": True,
            "checks": [{"name": "configuration", "status": "passed", "message": "provider ok"}],
        }

    report = check_runtime_readiness(
        CompanionPaths.from_env(tmp_path),
        provider="deepseek",
        memory_mode="dual",
        import_probe=lambda name: name not in {"numpy", "sentence_transformers"},
        provider_checker=fake_provider_checker,
        semantic_module_loader=lambda path: FakeSemanticModule,
    )

    assert report["ok"] is False
    failures = [check for check in report["checks"] if check["status"] == "failed"]
    assert any(check["name"] == "import.numpy" for check in failures)
    assert any(check["name"] == "import.sentence_transformers" for check in failures)
    assert any(check["name"] == "memory_mode" and "semantic-first is not ready" in check["message"] for check in failures)


def test_runtime_readiness_rejects_template_context(tmp_path, monkeypatch):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    for name in ("who_is_companion", "who_is_human", "now"):
        (context_dir / f"{name}.template.txt").write_text("YOUR TEMPLATE")
        (context_dir / f"{name}.txt").write_text("YOUR TEMPLATE")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")

    report = check_runtime_readiness(
        CompanionPaths.from_env(tmp_path),
        provider="deepseek",
        memory_mode="json",
        import_probe=lambda name: True,
        provider_checker=lambda provider, **kwargs: {
            "provider": provider,
            "ok": True,
            "checks": [{"name": "configuration", "status": "passed", "message": "provider ok"}],
        },
        semantic_module_loader=lambda path: type("FakeSemanticModule", (), {"SemanticMemoryStore": object}),
    )

    assert report["ok"] is False
    failed_context = [
        check for check in report["checks"]
        if check["status"] == "failed" and check["name"].startswith("context.")
    ]
    assert len(failed_context) == 3


def test_check_runtime_ready_cli_reports_json(tmp_path):
    write_minimal_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_runtime_ready.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(tmp_path),
            "--provider",
            "fake",
            "--memory-mode",
            "json",
            "--skip-provider-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert payload["provider"] == "fake"
    assert payload["memory_mode"] == "json"
    assert any(check["name"] == "provider.preflight" for check in payload["checks"])


def test_pi_predeploy_check_uses_isolated_smoke_home(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_pi_predeploy_check(
        CompanionPaths.from_env(target_home),
        smoke_paths=CompanionPaths.from_env(smoke_home),
        provider="fake",
        memory_mode="json",
        run_provider_check=False,
    )

    assert report["ok"] is True
    assert report["profile"]["name"] == "pi-json"
    assert report["companion_home"] == str(target_home.resolve())
    assert report["smoke_home"] == str(smoke_home.resolve())
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["fake_wake_smoke"]["ok"] is True
    assert stages["replay_regression"]["details"]["replay_failed"] == 0
    assert stages["real_wake"]["status"] == "skipped"
    assert not (target_home / "life-loop" / "wake_events.jsonl").exists()

    smoke_events = load_wake_events(CompanionPaths.from_env(smoke_home).wake_events_file)
    assert len(smoke_events) == 1
    assert smoke_events[0]["provider"] == "fake"
    assert smoke_events[0]["output_audit"]["raw_output_storage"] == "hash_only"
    assert smoke_events[0]["output_audit"]["initial"]["raw_output_stored"] is False

    second_report = run_pi_predeploy_check(
        CompanionPaths.from_env(target_home),
        smoke_paths=CompanionPaths.from_env(smoke_home),
        provider="fake",
        memory_mode="json",
        run_provider_check=False,
    )

    assert second_report["ok"] is True
    second_stages = {stage["name"]: stage for stage in second_report["stages"]}
    assert "journals" in second_stages["prepare_smoke_home"]["details"]["cleaned_runtime_paths"]
    smoke_events = load_wake_events(CompanionPaths.from_env(smoke_home).wake_events_file)
    assert len(smoke_events) == 1


def test_pi_predeploy_check_blocks_raw_output_storage_by_default(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.setenv("COMPANION_STORE_RAW_OUTPUTS", "1")

    report = run_pi_predeploy_check(
        CompanionPaths.from_env(target_home),
        smoke_paths=CompanionPaths.from_env(smoke_home),
        provider="fake",
        memory_mode="json",
        run_provider_check=False,
    )

    assert report["ok"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["raw_output_storage"]["status"] == "failed"
    assert stages["prepare_smoke_home"]["status"] == "skipped"
    assert stages["fake_wake_smoke"]["status"] == "skipped"
    assert not (smoke_home / "life-loop" / "wake_events.jsonl").exists()


def test_pi_predeploy_cli_reports_json(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_pi_predeploy.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
            "--smoke-home",
            str(smoke_home),
            "--provider",
            "fake",
            "--memory-mode",
            "json",
            "--skip-provider-check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["profile"]["cron_replacement"] is False
    assert payload["profile"]["memory_mode"] == "json"
    report_file = target_home / "life-loop" / "predeploy_report.json"
    assert report_file.exists()
    saved_report = json.loads(report_file.read_text())
    assert saved_report["ok"] is True
    assert saved_report["saved_at"]
    assert {stage["name"] for stage in payload["stages"]} >= {
        "readiness",
        "raw_output_storage",
        "fake_wake_smoke",
        "replay_regression",
        "real_wake",
    }


def test_m3_release_gate_passes_with_predeploy_trial_and_shadow_audit(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)
    paths = CompanionPaths.from_env(target_home)
    append_wake_event(paths.wake_events_file, {
        "id": "wake_m324_success",
        "trigger": "m324-deepseek-shadow:1",
        "status": "completed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": [],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "quality_gate": {"decision": "accepted", "context_eligible": True},
        "memory_write_results": [],
        "semantic_shadow": {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "store_path": "life-loop/semantic_shadow/memory_store.json",
        },
    })

    report = run_m3_release_gate(
        paths,
        smoke_paths=CompanionPaths.from_env(smoke_home),
        provider="fake",
        memory_mode="json",
        trial_since_trigger="m324-deepseek-shadow",
        trial_limit=1,
        run_provider_check=False,
    )

    assert report["ok"] is True
    assert report["milestone"] == "M3.25"
    assert report["recommendation"] == "ready_for_m4"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["predeploy"]["ok"] is True
    assert stages["trial_summary"]["details"]["semantic_shadow"]["enabled"] == 1
    assert stages["semantic_shadow_authority"]["ok"] is True
    assert stages["semantic_shadow_authority"]["details"]["shadow_memory_count"] == 0
    assert report["profile"]["cron_replacement"] is False


def test_m3_release_gate_fails_when_shadow_memory_is_prompt_eligible(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)
    paths = CompanionPaths.from_env(target_home)
    paths.semantic_shadow_dir.mkdir(parents=True)
    paths.semantic_shadow_store.write_text(json.dumps([
        {
            "id": "shadow_bad",
            "content": "This shadow record must not be prompt context.",
            "prompt_eligible": True,
            "accepted_for_context": False,
            "shadow_mode": True,
        }
    ]))

    report = run_m3_release_gate(
        paths,
        smoke_paths=CompanionPaths.from_env(smoke_home),
        provider="fake",
        memory_mode="json",
        run_provider_check=False,
    )

    assert report["ok"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["semantic_shadow_authority"]["ok"] is False
    assert "shadow memory is prompt_eligible" in stages["semantic_shadow_authority"]["message"]
    assert any(reason.startswith("semantic_shadow_authority:") for reason in report["stop_reasons"])


def test_m3_release_gate_cli_writes_report(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    smoke_home = tmp_path / "smoke"
    write_minimal_context(target_home)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)
    paths = CompanionPaths.from_env(target_home)
    append_wake_event(paths.wake_events_file, {
        "id": "wake_m324_cli_success",
        "trigger": "m324-cli-shadow:1",
        "status": "completed",
        "provider": "deepseek",
        "memory_backend": "json",
        "memory_ids": [],
        "request_ids": [],
        "request_errors": [],
        "quality": {"warnings": []},
        "quality_gate": {"decision": "accepted", "context_eligible": True},
        "memory_write_results": [],
        "semantic_shadow": {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
        },
    })
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m3_release_gate.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
            "--smoke-home",
            str(smoke_home),
            "--provider",
            "fake",
            "--memory-mode",
            "json",
            "--since-trigger",
            "m324-cli-shadow",
            "--trial-limit",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "ready_for_m4"
    report_file = target_home / "life-loop" / "m3_release_gate_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["saved_at"]
    assert saved["profile"]["trial_since_trigger"] == "m324-cli-shadow"


def test_m3_final_freeze_passes_from_release_gate_report(tmp_path):
    target_home = tmp_path / "target"
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    report_file = paths.life_loop_dir / "m3_release_gate_report.json"
    report_file.write_text(json.dumps(m3_release_gate_report_fixture()))

    report = run_m3_final_freeze(
        paths,
        expected_provider="deepseek",
        expected_memory_mode="json",
        expected_trial_trigger="m324-freeze",
    )

    assert report["ok"] is True
    assert report["milestone"] == "M3.26"
    assert report["recommendation"] == "m3_frozen_ready_for_m4"
    assert report["deployment_contract"] == {
        "provider": "deepseek",
        "memory_mode": "json",
        "cron_replacement": False,
        "real_wake_in_freeze": False,
        "semantic_shadow_authoritative": False,
        "raw_output_storage": "hash_only",
    }
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["release_gate_report"]["ok"] is True
    assert stages["release_result"]["ok"] is True
    assert stages["deployment_profile"]["ok"] is True
    assert stages["predeploy_contract"]["details"]["real_wake"]["status"] == "skipped"
    assert stages["semantic_shadow_contract"]["message"] == "semantic shadow remains non-authoritative"


def test_m3_final_freeze_fails_when_release_gate_is_not_ready(tmp_path):
    target_home = tmp_path / "target"
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    payload = m3_release_gate_report_fixture()
    payload["ok"] = False
    payload["recommendation"] = "inspect"
    payload["stop_reasons"] = ["trial_summary: failed wakes"]
    report_file = paths.life_loop_dir / "m3_release_gate_report.json"
    report_file.write_text(json.dumps(payload))

    report = run_m3_final_freeze(paths, expected_trial_trigger="m324-freeze")

    assert report["ok"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["release_result"]["ok"] is False
    assert "release gate ok is not true" in stages["release_result"]["message"]
    assert any(reason.startswith("release_result:") for reason in report["stop_reasons"])


def test_m3_final_freeze_cli_writes_report(tmp_path):
    target_home = tmp_path / "target"
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m3_release_gate_report.json").write_text(
        json.dumps(m3_release_gate_report_fixture())
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m3_final_freeze.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
            "--expected-provider",
            "deepseek",
            "--expected-memory-mode",
            "json",
            "--expected-trial-trigger",
            "m324-freeze",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "m3_frozen_ready_for_m4"
    report_file = target_home / "life-loop" / "m3_final_freeze_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["saved_at"]
    assert saved["deployment_contract"]["semantic_shadow_authoritative"] is False


def test_m4_deploy_check_passes_for_frozen_deepseek_json_home(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(
        json.dumps(m3_final_freeze_report_fixture())
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-secret")
    monkeypatch.delenv("COMPANION_LLM_API_KEY", raising=False)
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_deploy_check(
        paths,
        import_probe=lambda name: True,
    )

    assert report["ok"] is True
    assert report["milestone"] == "M4.2"
    assert report["recommendation"] == "ready_for_manual_wake"
    assert report["profile"] == {
        "name": "pi-deploy-check",
        "provider": "deepseek",
        "memory_mode": "json",
        "cron_replacement": False,
        "semantic_shadow_authoritative": False,
        "real_wake_requested": False,
        "provider_generation_requested": False,
        "provider_preflight_requested": False,
        "raw_output_storage_required": "hash_only",
        "dashboard_reachability_required": False,
    }
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["final_freeze_result"]["ok"] is True
    assert stages["frozen_deployment_contract"]["details"]["actual"]["provider"] == "deepseek"
    assert stages["semantic_shadow_authority"]["ok"] is True
    assert stages["deepseek_api_key"]["details"]["present_env_names"] == ["DEEPSEEK_API_KEY"]
    assert stages["deepseek_api_key"]["details"]["secret_values"] == "redacted"
    assert stages["raw_output_storage"]["details"]["raw_output_storage"] == "hash_only"
    assert stages["dashboard_reachability"]["status"] == "skipped"
    assert "unit-secret" not in json.dumps(report, ensure_ascii=False)
    assert not paths.wake_events_file.exists()


def test_m4_deploy_check_fails_without_final_freeze_report(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    paths = CompanionPaths.from_env(target_home)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_deploy_check(
        paths,
        import_probe=lambda name: True,
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["final_freeze_report"]["ok"] is False
    assert stages["final_freeze_result"]["status"] == "skipped"
    assert any(reason.startswith("final_freeze_report:") for reason in report["stop_reasons"])


def test_m4_deploy_check_rejects_unfrozen_contract(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    payload = m3_final_freeze_report_fixture()
    payload["recommendation"] = "inspect"
    payload["deployment_contract"]["provider"] = "fake"
    payload["deployment_contract"]["semantic_shadow_authoritative"] = True
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(json.dumps(payload))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_deploy_check(
        paths,
        import_probe=lambda name: True,
    )

    assert report["ok"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["final_freeze_result"]["ok"] is False
    assert stages["frozen_deployment_contract"]["ok"] is False
    assert "provider is 'fake'" in stages["frozen_deployment_contract"]["message"]
    assert "semantic_shadow_authoritative" in stages["frozen_deployment_contract"]["message"]


def test_m4_deploy_check_cli_writes_report_and_redacts_secret(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(
        json.dumps(m3_final_freeze_report_fixture())
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m4_deploy_check.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "cli-secret"
    env.pop("COMPANION_LLM_API_KEY", None)
    env.pop("COMPANION_STORE_RAW_OUTPUTS", None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "cli-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "ready_for_manual_wake"
    assert payload["saved_at"]
    report_file = target_home / "life-loop" / "m4_deploy_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["milestone"] == "M4.2"
    assert saved["saved_at"]
    assert "cli-secret" not in report_file.read_text()


def test_m4_wake_trial_passes_single_manual_attempt(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_wake_trial(
        paths,
        client_factory=lambda attempt: FakeLLMClient(),
    )

    assert report["ok"] is True
    assert report["milestone"] == "M4.3"
    assert report["recommendation"] == "continue_runtime_validation"
    assert report["profile"]["provider"] == "deepseek"
    assert report["profile"]["memory_mode"] == "json"
    assert report["attempts"] == [{
        "attempt": 1,
        "trigger": "m4-pi-manual-wake:attempt-1",
        "status": "completed",
        "event_id": report["latest_event"]["id"],
        "event_status": "completed",
        "failure_category": "none",
        "retryable": False,
    }]
    assert report["failure_audit"]["category"] == "none"
    assert report["output_audit"]["raw_output_storage"] == "hash_only"
    events = load_wake_events(paths.wake_events_file)
    assert len(events) == 1
    assert events[0]["provider"] == "deepseek"
    assert events[0]["memory_backend"] == "json"


def test_m4_wake_trial_retries_infrastructure_timeout_once(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    class TimeoutThenValidClient:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, context):
            self.calls += 1
            if self.calls == 1:
                raise HttpLLMError("LLM provider timed out after 3 seconds")
            return semantic_shadow_wake_output()

    client = TimeoutThenValidClient()

    report = run_m4_wake_trial(
        paths,
        client_factory=lambda attempt: client,
    )

    assert report["ok"] is True, json.dumps(report, ensure_ascii=False)
    assert len(report["attempts"]) == 2
    assert report["attempts"][0]["status"] == "failed"
    assert report["attempts"][0]["failure_category"] == "infrastructure"
    assert report["attempts"][0]["retryable"] is True
    assert report["attempts"][1]["status"] == "completed"
    assert report["failure_audit"]["category"] == "none"
    events = load_wake_events(paths.wake_events_file)
    assert [event["status"] for event in events] == ["failed", "completed"]


def test_m4_wake_trial_does_not_retry_grounding_or_authority_failure(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_wake_trial(
        paths,
        client_factory=lambda attempt: StaticLLMClient(unsupported_stable_fact_output()),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert len(report["attempts"]) == 1
    assert report["attempts"][0]["status"] == "completed"
    assert report["attempts"][0]["retryable"] is False
    assert report["failure_audit"]["category"] == "grounding"
    assert any("unsupported grounding claims" in reason for reason in report["stop_reasons"])
    events = load_wake_events(paths.wake_events_file)
    assert len(events) == 1
    assert events[0]["quality_gate"]["context_eligible"] is False


def test_m4_wake_trial_cli_requires_ready_deploy_report_before_provider(tmp_path):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m4_wake_trial.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["recommendation"] == "inspect"
    assert payload["attempts"] == []
    assert payload["failure_audit"]["category"] == "provider"
    report_file = target_home / "life-loop" / "m4_wake_trial_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["milestone"] == "M4.3"
    assert not (target_home / "life-loop" / "wake_events.jsonl").exists()


def test_m4_runtime_validation_passes_after_successful_deploy_and_wake_reports(tmp_path):
    target_home = tmp_path / "target"
    write_m4_dashboard_app(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    paths.journals_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by validation")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))

    report = run_m4_runtime_validation(paths)

    assert report["ok"] is True
    assert report["milestone"] == "M4.6"
    assert report["recommendation"] == "m4_runtime_validated"
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_requested"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_deploy_result"]["ok"] is True
    assert stages["m4_wake_result"]["ok"] is True
    assert stages["m4_output_audit"]["ok"] is True
    assert stages["dashboard_life_read_only"]["ok"] is True
    assert stages["platform_identity"]["required"] is False
    assert report["stop_reasons"] == []


def test_m4_runtime_validation_rejects_raw_output_storage(tmp_path):
    target_home = tmp_path / "target"
    write_m4_dashboard_app(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    paths.journals_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    wake_report["output_audit"]["final_raw_output_stored"] = True
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by validation")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))

    report = run_m4_runtime_validation(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_output_audit"]["ok"] is False
    assert any("final raw model output was stored" in reason for reason in report["stop_reasons"])


def test_m4_runtime_validation_rejects_life_dashboard_write_route(tmp_path):
    target_home = tmp_path / "target"
    write_m4_dashboard_app(
        target_home,
        extra_routes="\n".join([
            "@app.route('/life/m4/deploy', methods=['POST'])",
            "def m4_deploy():",
            "    return 'blocked'",
        ]),
    )
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    paths.journals_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by validation")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))

    report = run_m4_runtime_validation(paths)

    assert report["ok"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["dashboard_life_read_only"]["ok"] is False
    assert stages["dashboard_life_read_only"]["details"]["m3_m4_non_get_routes"][0]["rule"] == "/life/m4/deploy"


def test_m4_runtime_validation_cli_writes_report(tmp_path):
    target_home = tmp_path / "target"
    write_m4_dashboard_app(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    paths.journals_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by validation")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m4_runtime_validation.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "m4_runtime_validated"
    assert payload["saved_at"]
    report_file = paths.life_loop_dir / "m4_runtime_validation_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["milestone"] == "M4.6"
    assert saved["saved_at"]


def test_m4_post_change_guard_passes_when_deploy_and_runtime_validation_hold(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    write_m4_dashboard_app(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    paths.journals_dir.mkdir(parents=True, exist_ok=True)
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(json.dumps(m3_final_freeze_report_fixture()))
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by guard")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_post_change_guard(
        paths,
        import_probe=lambda name: True,
    )

    assert report["ok"] is True
    assert report["milestone"] == "M4.7"
    assert report["recommendation"] == "m4_still_deployable"
    assert report["profile"]["real_wake_requested"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_deploy_check_current"]["ok"] is True
    assert stages["m4_runtime_validation_current"]["ok"] is True
    assert "unit-secret" not in json.dumps(report, ensure_ascii=False)


def test_m4_post_change_guard_fails_when_runtime_validation_fails(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(json.dumps(m3_final_freeze_report_fixture()))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m4_post_change_guard(
        paths,
        import_probe=lambda name: True,
        runtime_validator=lambda paths: {
            "ok": False,
            "milestone": "M4.6",
            "recommendation": "inspect",
            "stop_reasons": ["m4_wake_trial_report: missing"],
        },
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_deploy_check_current"]["ok"] is True
    assert stages["m4_runtime_validation_current"]["ok"] is False
    assert any("runtime validation" in reason for reason in report["stop_reasons"])


def test_m4_post_change_guard_cli_writes_report(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    write_m4_runtime_files(target_home)
    write_m4_dashboard_app(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    paths.journals_dir.mkdir(parents=True, exist_ok=True)
    (paths.life_loop_dir / "m3_final_freeze_report.json").write_text(json.dumps(m3_final_freeze_report_fixture()))
    (paths.life_loop_dir / "m4_deploy_report.json").write_text(json.dumps(m4_deploy_report_fixture()))
    wake_report = m4_success_wake_trial_report_fixture()
    (paths.home / wake_report["latest_event"]["journal"]).write_text("journal body is not read by guard")
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m4_post_change_guard.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "cli-secret"
    env.pop("COMPANION_STORE_RAW_OUTPUTS", None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "cli-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["recommendation"] == "m4_still_deployable"
    report_file = paths.life_loop_dir / "m4_post_change_guard_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["milestone"] == "M4.7"
    assert "cli-secret" not in report_file.read_text()


def test_m4_observation_check_continues_until_window_is_complete(tmp_path):
    paths = CompanionPaths.from_env(tmp_path / "target")
    append_wake_event(paths.wake_events_file, m4_observation_event(
        "wake_one",
        "2026-06-14T08:00:00",
    ))

    report = run_m4_observation_check(
        paths,
        observation_hours=24,
        min_completed_events=2,
        since="2026-06-14T00:00:00",
        now=datetime(2026, 6, 14, 12, 0, 0),
    )

    assert report["ok"] is False
    assert report["milestone"] == "M4.8"
    assert report["recommendation"] == "continue_observation"
    assert report["stop_reasons"] == []
    assert any("completed wake events 1 < required 2" in reason for reason in report["pending_reasons"])


def test_m4_observation_check_passes_stable_window(tmp_path):
    paths = CompanionPaths.from_env(tmp_path / "target")
    append_wake_event(paths.wake_events_file, m4_observation_event(
        "wake_start",
        "2026-06-14T08:00:00",
    ))
    append_wake_event(paths.wake_events_file, m4_observation_event(
        "wake_end",
        "2026-06-15T09:00:00",
    ))

    report = run_m4_observation_check(
        paths,
        observation_hours=24,
        min_completed_events=2,
        since="2026-06-14T00:00:00",
        now=datetime(2026, 6, 15, 9, 0, 0),
    )

    assert report["ok"] is True
    assert report["recommendation"] == "stable_runtime_observed"
    assert report["pending_reasons"] == []
    assert report["stop_reasons"] == []
    assert report["summary"]["observed_hours"] == 25.0


def test_m4_observation_check_inspects_failed_event(tmp_path):
    paths = CompanionPaths.from_env(tmp_path / "target")
    append_wake_event(paths.wake_events_file, m4_observation_event(
        "wake_start",
        "2026-06-14T08:00:00",
    ))
    failed = m4_observation_event(
        "wake_failed",
        "2026-06-15T09:00:00",
        status="failed",
    )
    failed["error"] = {"type": "HttpLLMError", "message": "timeout"}
    append_wake_event(paths.wake_events_file, failed)

    report = run_m4_observation_check(
        paths,
        observation_hours=24,
        min_completed_events=1,
        since="2026-06-14T00:00:00",
        now=datetime(2026, 6, 15, 9, 0, 0),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert any("wake event problems" in reason for reason in report["stop_reasons"])
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["event_health"]["details"]["failures"][0]["id"] == "wake_failed"


def test_m5_quality_check_passes_ready_sample(tmp_path):
    target_home = tmp_path / "target"
    write_m5_quality_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)

    report = run_m5_quality_check(paths)

    assert report["ok"] is True
    assert report["milestone"] == "M5.1"
    assert report["recommendation"] == "ready_for_quality_tuning"
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_requested"] is False
    assert report["sample"]["accepted_events"] == 1
    assert report["quality_profile"]["blocking_warning_count"] == 0
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_baseline"]["ok"] is True
    assert stages["event_sample"]["ok"] is True
    assert stages["language_surface"]["ok"] is True
    assert stages["semantic_shadow_isolation"]["ok"] is True
    assert stages["dashboard_read_only"]["ok"] is True


def test_m5_quality_check_continues_when_sample_is_short(tmp_path):
    target_home = tmp_path / "target"
    write_m5_quality_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)

    report = run_m5_quality_check(paths, min_accepted_events=2)

    assert report["ok"] is False
    assert report["recommendation"] == "continue_observation"
    assert report["stop_reasons"] == []
    assert any("accepted wake events 1 < required 2" in reason for reason in report["pending_reasons"])
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["event_sample"]["status"] == "pending"


def test_m5_quality_check_inspects_blocking_quality_after_m4_baseline(tmp_path):
    target_home = tmp_path / "target"
    write_m5_quality_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    rejected = m5_quality_event(
        "wake_m5_rejected",
        "2026-06-15T09:00:00",
        journal="journals/wakeup_2026-06-15_09-00-00.md",
    )
    rejected["quality_gate"] = {
        "decision": "rejected",
        "context_eligible": False,
        "blocking_warnings": ["journal repeats recent self-narrative phrasing"],
        "advisory_warnings": [],
    }
    rejected["quality"]["warnings"] = ["journal repeats recent self-narrative phrasing"]
    append_wake_event(paths.wake_events_file, rejected)
    (paths.home / rejected["journal"]).write_text("这段记录重复了旧的自我叙事，因此应该被 M5.1 质量观察门拦下。\n")

    report = run_m5_quality_check(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert report["quality_profile"]["warning_categories"]["repeated_self_narrative"] == 1
    assert any("quality_warning_profile" in reason for reason in report["stop_reasons"])


def test_m5_quality_check_cli_writes_report(tmp_path):
    target_home = tmp_path / "target"
    write_m5_quality_ready_home(target_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m5_quality_check.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m5-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "m5-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["recommendation"] == "ready_for_quality_tuning"
    report_file = target_home / "life-loop" / "m5_quality_report.json"
    assert report_file.exists()
    saved = json.loads(report_file.read_text())
    assert saved["milestone"] == "M5.1"
    assert "m5-secret" not in report_file.read_text()


def test_m5_quality_trial_runs_controlled_cycles_with_fake_client(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_m5_quality_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    m5_report = run_m5_quality_check(paths)
    m5_report["saved_at"] = "2026-06-15T10:30:00"
    (paths.life_loop_dir / "m5_quality_report.json").write_text(json.dumps(m5_report))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "m5-trial-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m5_quality_trial(
        paths,
        cycles=1,
        client_factory=lambda cycle: FakeLLMClient(),
    )

    assert report["ok"] is True
    assert report["milestone"] == "M5.5"
    assert report["recommendation"] == "continue_quality_observation"
    assert report["provider"] == "deepseek"
    assert report["memory_mode"] == "json"
    assert report["cycles_requested"] == 1
    assert len(report["attempts"]) == 1
    assert report["context_acceptance"]["accepted_events"] == 1
    assert report["quality_profile"]["blocking_warning_count"] == 0
    assert report["request_discipline"]["request_error_count"] == 0
    assert report["memory_discipline"]["memory_write_failures"] == 0
    assert report["output_audit"]["raw_output_storage"] == "hash_only"
    assert "m5-trial-secret" not in json.dumps(report)
    events = load_wake_events(paths.wake_events_file)
    assert events[-1]["provider"] == "deepseek"
    assert events[-1]["memory_backend"] == "json"


def test_m5_quality_trial_requires_m5_quality_report_before_wake(tmp_path, monkeypatch):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_post_change_guard_report.json").write_text(
        json.dumps(m4_post_change_guard_report_fixture())
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "m5-trial-secret")
    monkeypatch.delenv("COMPANION_STORE_RAW_OUTPUTS", raising=False)

    report = run_m5_quality_trial(
        paths,
        cycles=1,
        client_factory=lambda cycle: FakeLLMClient(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert report["attempts"] == []
    assert any("m5_quality_report" in reason for reason in report["stop_reasons"])
    assert not paths.wake_events_file.exists()


def test_m5_quality_trial_cli_writes_report_without_printing_secret(tmp_path):
    target_home = tmp_path / "target"
    write_minimal_context(target_home)
    paths = CompanionPaths.from_env(target_home)
    paths.life_loop_dir.mkdir(parents=True)
    (paths.life_loop_dir / "m4_post_change_guard_report.json").write_text(
        json.dumps(m4_post_change_guard_report_fixture())
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m5_quality_trial.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m5-cli-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
            "--cycles",
            "1",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "m5-cli-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["milestone"] == "M5.5"
    report_file = target_home / "life-loop" / "m5_quality_trial_report.json"
    assert report_file.exists()
    assert "m5-cli-secret" not in report_file.read_text()


def test_m5_quality_release_gate_passes_with_advisory_audit_noise(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home, include_advisory_noise=True)
    paths = CompanionPaths.from_env(target_home)

    report = run_m5_quality_release_gate(paths)

    assert report["ok"] is True
    assert report["milestone"] == "M5.6"
    assert report["recommendation"] == "m5_quality_ready_for_m6"
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_requested"] is False
    assert report["audit_anomalies"]["extra_event_count"] == 2
    assert report["audit_anomalies"]["advisory_anomaly_count"] == 2
    assert report["audit_anomalies"]["blocking_anomaly_count"] == 0
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m5_trial_attempts"]["ok"] is True
    assert stages["m5_quality_contract"]["ok"] is True
    assert stages["audit_anomalies"]["status"] == "advisory"


def test_m5_quality_release_gate_fails_when_canonical_attempt_is_rejected(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    trial_path = paths.life_loop_dir / "m5_quality_trial_report.json"
    trial_report = json.loads(trial_path.read_text())
    trial_report["attempts"][1]["quality_gate_decision"] = "rejected"
    trial_report["attempts"][1]["context_eligible"] = False
    trial_path.write_text(json.dumps(trial_report))

    report = run_m5_quality_release_gate(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m5_trial_attempts"]["ok"] is False
    assert any("quality gate decision is not accepted" in reason for reason in report["stop_reasons"])


def test_m5_quality_release_gate_blocks_newer_bad_audit_event(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    bad_event = m5_quality_event(
        "wake_m5_newer_bad",
        "2026-06-15T09:45:00",
        trigger="m5-manual-quality-trial:cycle-1",
        journal="journals/wakeup_2026-06-15_09-45-00.md",
    )
    bad_event["status"] = "failed"
    bad_event["error"] = {
        "type": "HttpLLMError",
        "message": "LLM provider request failed",
    }
    bad_event["journal"] = None
    append_wake_event(paths.wake_events_file, bad_event)

    report = run_m5_quality_release_gate(paths)

    assert report["ok"] is False
    assert report["audit_anomalies"]["blocking_anomaly_count"] == 1
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["audit_anomalies"]["ok"] is False
    assert any("blocking post-report M5.5 audit anomalies" in reason for reason in report["stop_reasons"])


def test_m5_quality_release_gate_cli_writes_report_without_printing_secret(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home, include_advisory_noise=True)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m5_quality_release_gate.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m5-release-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "m5-release-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["milestone"] == "M5.6"
    report_file = target_home / "life-loop" / "m5_quality_release_report.json"
    assert report_file.exists()
    assert "m5-release-secret" not in report_file.read_text()


def test_m5_final_freeze_passes_from_quality_release_report(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home, include_advisory_noise=True)
    paths = CompanionPaths.from_env(target_home)
    release_report = run_m5_quality_release_gate(paths)
    release_report["saved_at"] = "2026-06-15T10:00:00"
    (paths.life_loop_dir / "m5_quality_release_report.json").write_text(json.dumps(release_report))

    report = run_m5_final_freeze(paths)

    assert report["ok"] is True
    assert report["milestone"] == "M5.7"
    assert report["recommendation"] == "m5_frozen_ready_for_m6"
    assert report["quality_contract"]["real_wake_in_freeze"] is False
    assert report["quality_contract"]["blocking_audit_anomalies_allowed"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["release_result"]["ok"] is True
    assert stages["required_release_stages"]["ok"] is True
    assert stages["audit_contract"]["ok"] is True


def test_m5_final_freeze_fails_when_release_gate_is_not_ready(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    release_report = run_m5_quality_release_gate(paths)
    release_report["ok"] = False
    release_report["recommendation"] = "inspect"
    release_report["stop_reasons"] = ["m5_trial_attempts: cycle 2 rejected"]
    (paths.life_loop_dir / "m5_quality_release_report.json").write_text(json.dumps(release_report))

    report = run_m5_final_freeze(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["release_result"]["ok"] is False
    assert any("M5.6 release gate ok is not true" in reason for reason in report["stop_reasons"])


def test_m5_final_freeze_cli_writes_report_without_printing_secret(tmp_path):
    target_home = tmp_path / "target"
    write_m5_release_ready_home(target_home, include_advisory_noise=True)
    paths = CompanionPaths.from_env(target_home)
    release_report = run_m5_quality_release_gate(paths)
    release_report["saved_at"] = "2026-06-15T10:00:00"
    (paths.life_loop_dir / "m5_quality_release_report.json").write_text(json.dumps(release_report))
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m5_final_freeze.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m5-freeze-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "m5-freeze-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["milestone"] == "M5.7"
    report_file = target_home / "life-loop" / "m5_final_freeze_report.json"
    assert report_file.exists()
    assert "m5-freeze-secret" not in report_file.read_text()


def test_m6_preflight_passes_from_manifest_and_current_freezes(tmp_path):
    target_home = tmp_path / "target"
    write_m6_preflight_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)

    report = run_m6_preflight_check(
        paths,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is True
    assert report["milestone"] == "M6.2"
    assert report["recommendation"] == "ready_for_real_pi_manual_wake"
    assert report["pi_presence"]["required"] is False
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_requested"] is False
    assert report["profile"]["scheduler_mutation_allowed"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["manifest_result"]["ok"] is True
    assert stages["package_inventory"]["ok"] is True
    assert stages["secret_boundary"]["ok"] is True
    assert stages["m4_post_change_guard_current"]["ok"] is True
    assert stages["m5_final_freeze_current"]["ok"] is True
    assert not paths.wake_events_file.exists()


def test_m6_preflight_rejects_manifest_that_copies_secret_values(tmp_path):
    target_home = tmp_path / "target"
    write_m6_preflight_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    manifest_path = paths.life_loop_dir / "m6_migration_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["secret_boundary"]["copy_secret_values"] = True
    manifest_path.write_text(json.dumps(manifest))

    report = run_m6_preflight_check(
        paths,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["secret_boundary"]["ok"] is False
    assert any("secret_boundary" in reason for reason in report["stop_reasons"])


def test_m6_preflight_cli_writes_report_without_printing_secret(tmp_path):
    target_home = tmp_path / "target"
    write_m6_preflight_ready_home(target_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m6_preflight.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m6-cli-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "m6-cli-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["milestone"] == "M6.2"
    report_file = target_home / "life-loop" / "m6_preflight_report.json"
    assert report_file.exists()
    assert "m6-cli-secret" not in report_file.read_text()
    assert not (target_home / "life-loop" / "wake_events.jsonl").exists()


def test_m6_pi_manual_wake_requires_confirmation_before_delegate(tmp_path):
    target_home = tmp_path / "target"
    write_m6_manual_wake_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("wake runner should not be called")

    report = run_m6_pi_manual_wake_trial(
        paths,
        confirm_real_pi_wake=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        wake_trial_runner=fail_if_called,
    )

    assert report["ok"] is False
    assert report["milestone"] == "M6.3"
    assert report["recommendation"] == "inspect"
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_started"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["explicit_manual_wake_confirmation"]["ok"] is False
    assert not paths.wake_events_file.exists()


def test_m6_pi_manual_wake_requires_real_pi_before_delegate(tmp_path):
    target_home = tmp_path / "target"
    write_m6_manual_wake_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("wake runner should not be called")

    report = run_m6_pi_manual_wake_trial(
        paths,
        confirm_real_pi_wake=True,
        platform_identity_provider=non_pi_identity_fixture,
        wake_trial_runner=fail_if_called,
    )

    assert report["ok"] is False
    assert report["recommendation"] == "pi_required"
    assert report["pi_presence"]["required"] is True
    assert report["pi_presence"]["detected"] is False
    assert report["profile"]["real_wake_requested"] is True
    assert report["profile"]["provider_generation_started"] is False
    assert report["pending_reasons"] == ["real Raspberry Pi required for M6.3"]
    assert not paths.wake_events_file.exists()


def test_m6_pi_manual_wake_delegates_after_explicit_real_pi_gates(tmp_path):
    target_home = tmp_path / "target"
    write_m6_manual_wake_ready_home(target_home)
    paths = CompanionPaths.from_env(target_home)
    calls = []

    def fake_wake_runner(runner_paths, **kwargs):
        calls.append((runner_paths, kwargs))
        report = m4_success_wake_trial_report_fixture()
        report["profile"]["trigger"] = "m6-pi-manual-wake"
        report["attempts"][0]["trigger"] = "m6-pi-manual-wake:attempt-1"
        report["latest_event"]["trigger"] = "m6-pi-manual-wake:attempt-1"
        return report

    report = run_m6_pi_manual_wake_trial(
        paths,
        confirm_real_pi_wake=True,
        platform_identity_provider=raspberry_pi_identity_fixture,
        wake_trial_runner=fake_wake_runner,
    )

    assert report["ok"] is True
    assert report["recommendation"] == "continue_pi_observation"
    assert report["pi_presence"]["detected"] is True
    assert report["profile"]["provider_generation_started"] is True
    assert report["field_pilot"]["manual_wake"]["executed"] is True
    assert report["field_pilot"]["manual_wake"]["attempt_count"] == 1
    assert calls[0][0] == paths
    assert calls[0][1]["trigger"] == "m6-pi-manual-wake"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m4_wake_trial_delegate"]["ok"] is True


def test_m6_pi_manual_wake_cli_without_confirmation_writes_report_without_secret(tmp_path):
    target_home = tmp_path / "target"
    write_m6_manual_wake_ready_home(target_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m6_pi_manual_wake_trial.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = "m6-manual-wake-secret"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(target_home),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert "m6-manual-wake-secret" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["milestone"] == "M6.3"
    assert payload["profile"]["real_wake_requested"] is False
    assert payload["profile"]["provider_generation_started"] is False
    report_file = target_home / "life-loop" / "m6_pi_manual_wake_report.json"
    assert report_file.exists()
    assert "m6-manual-wake-secret" not in report_file.read_text()
    assert not (target_home / "life-loop" / "wake_events.jsonl").exists()


def test_m6_pi_observation_passes_stable_manual_wake_artifacts(tmp_path):
    paths = write_m6_observation_ready_home(
        tmp_path,
        journal_text=(
            "这次 M6.3 real Pi manual wake 已经完成。我保持在 DeepSeek/json 路径里，"
            "只记录当前真实事件，不把测试标签写成长期事实。\n"
        ),
    )

    report = run_m6_pi_observation_check(paths)

    assert report["ok"] is True
    assert report["milestone"] == "M6.4"
    assert report["recommendation"] == "stable_pi_field_observed"
    assert report["profile"]["provider_generation_requested"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_manual_wake_report"]["ok"] is True
    assert stages["m6_manual_wake_event"]["ok"] is True
    assert stages["event_health"]["ok"] is True
    assert stages["journal_m6_consistency"]["ok"] is True


def test_m6_pi_observation_rejects_stale_manual_wake_journal(tmp_path):
    paths = write_m6_observation_ready_home(
        tmp_path,
        journal_text=(
            "这次被触发，还不是真正的唤醒。前置配置还没完成，"
            "Polaris 应该先跑 fake wake，再决定是否叫醒我。\n"
        ),
    )

    report = run_m6_pi_observation_check(paths)

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["journal_m6_consistency"]["ok"] is False
    assert "M6 journal contradicts completed real manual wake state" in stages["journal_m6_consistency"]["message"]
    assert any("journal_m6_consistency" in reason for reason in report["stop_reasons"])


def test_m6_recovery_drill_creates_backup_and_restore_sandbox_without_secret_values(tmp_path):
    paths = write_m6_recovery_ready_home(tmp_path, secret_value="m6-secret-value")
    backup_root = tmp_path / "backups" / "m6"

    report = run_m6_recovery_drill(paths, backup_root=backup_root, require_raspberry_pi=False)

    assert report["ok"] is True
    assert report["milestone"] == "M6.5"
    assert report["recommendation"] == "rollback_recovery_ready"
    assert report["profile"]["provider_generation_requested"] is False
    assert report["profile"]["live_restore_executed"] is False
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_observation_report"]["ok"] is True
    assert stages["backup_create"]["ok"] is True
    assert stages["secret_boundary"]["ok"] is True
    assert stages["restore_sandbox_verify"]["ok"] is True

    manifest_path = Path(report["backup"]["manifest"])
    sandbox_path = Path(report["restore_sandbox"]["path"])
    assert manifest_path.exists()
    assert sandbox_path.exists()
    assert (Path(report["backup"]["runtime_dir"]) / "life-loop" / "m6_pi_observation_report.json").exists()
    assert "m6-secret-value" not in json.dumps(report)
    for path in Path(report["backup"]["path"]).rglob("*"):
        if path.is_file():
            assert "m6-secret-value" not in path.read_text(errors="ignore")


def test_m6_recovery_drill_requires_stable_m6_observation(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    (paths.life_loop_dir / "m6_pi_observation_report.json").write_text(json.dumps({
        "ok": False,
        "milestone": "M6.4",
        "recommendation": "inspect",
        "stop_reasons": ["journal_m6_consistency"],
    }))

    report = run_m6_recovery_drill(
        paths,
        backup_root=tmp_path / "backups" / "m6",
        require_raspberry_pi=False,
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_observation_report"]["ok"] is False
    assert not report["backup"].get("executed")


def test_m6_recovery_drill_rejects_backup_root_inside_live_runtime(tmp_path):
    paths = write_m6_recovery_ready_home(tmp_path)

    report = run_m6_recovery_drill(
        paths,
        backup_root=paths.life_loop_dir / "backup",
        require_raspberry_pi=False,
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["backup_root"]["ok"] is False
    assert "protected live runtime path" in stages["backup_root"]["message"]


def test_m6_scheduler_readiness_passes_without_scheduler_mutation(tmp_path):
    paths = write_m6_scheduler_ready_home(tmp_path)

    report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is True
    assert report["milestone"] == "M6.6"
    assert report["recommendation"] == "ready_for_scheduler_handoff"
    assert report["profile"]["scheduler_mutation_attempted"] is False
    assert report["profile"]["provider_generation_requested"] is False
    assert report["handoff"]["ready"] is True
    assert report["handoff"]["mutated"] is False
    assert "scripts/run_wake_cycle.py" in report["handoff"]["target_command"]
    assert report["rollback"]["instructions_present"] is True
    assert report["rollback"]["latest_verified_backup"]
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_manual_wake_report"]["ok"] is True
    assert stages["m6_observation_report"]["ok"] is True
    assert stages["m6_recovery_report"]["ok"] is True
    assert stages["scheduler_boundary"]["ok"] is True


def test_m6_scheduler_readiness_requires_recovery_report(tmp_path):
    paths = write_m6_recovery_ready_home(tmp_path)
    write_m6_scheduler_instructions(paths)

    report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_recovery_report"]["ok"] is False
    assert "missing" in stages["m6_recovery_report"]["message"]


def test_m6_scheduler_readiness_requires_real_pi_identity(tmp_path):
    paths = write_m6_scheduler_ready_home(tmp_path)

    report = run_m6_scheduler_readiness_check(
        paths,
        platform_identity_provider=non_pi_identity_fixture,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "pi_required"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["platform_identity"]["ok"] is False


def test_m6_scheduler_readiness_requires_rollback_instructions(tmp_path):
    paths = write_m6_scheduler_ready_home(tmp_path)
    (paths.home / "docs" / "m6-pi-scheduler-readiness-design.md").unlink()

    report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["rollback_instructions"]["ok"] is False


def test_m6_scheduler_readiness_blocks_secret_copy_regression(tmp_path):
    paths = write_m6_scheduler_ready_home(tmp_path)
    report_path = paths.life_loop_dir / "m6_recovery_drill_report.json"
    recovery_report = json.loads(report_path.read_text())
    recovery_report["secret_boundary"]["secret_values_copied"] = True
    report_path.write_text(json.dumps(recovery_report))

    report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_recovery_report"]["ok"] is False
    assert "secret values" in stages["m6_recovery_report"]["message"]


def test_m6_final_freeze_passes_readonly_evidence_chain(tmp_path):
    paths = write_m6_final_freeze_ready_home(tmp_path)
    before_events = paths.wake_events_file.read_text() if paths.wake_events_file.exists() else ""

    report = run_m6_final_freeze_check(
        paths,
        require_raspberry_pi=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is True
    assert report["milestone"] == "M6.7"
    assert report["recommendation"] == "m6_frozen_ready_for_scheduler_handoff"
    assert report["profile"]["real_wake_requested"] is False
    assert report["profile"]["provider_generation_requested"] is False
    assert report["profile"]["scheduler_mutation_attempted"] is False
    assert report["profile"]["live_restore_executed"] is False
    assert report["final_freeze"]["readonly"] is True
    assert report["final_freeze"]["scheduler_handoff_ready"] is True
    assert report["final_freeze"]["scheduler_mutated"] is False
    assert report["rollback"]["ready"] is True
    assert report["rollback"]["latest_verified_backup"]
    assert paths.wake_events_file.read_text() == before_events
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_preflight_report"]["ok"] is True
    assert stages["m6_recovery_report"]["ok"] is True
    assert stages["m6_scheduler_readiness_report"]["ok"] is True
    assert stages["m6_manifest_m6_7_artifacts"]["ok"] is True
    assert stages["scheduler_mutation_flags"]["ok"] is True
    assert stages["rollback_backup_evidence"]["ok"] is True
    assert stages["semantic_shadow_authority"]["ok"] is True


def test_m6_final_freeze_blocks_scheduler_mutation_regression(tmp_path):
    paths = write_m6_final_freeze_ready_home(tmp_path)
    report_path = paths.life_loop_dir / "m6_scheduler_readiness_report.json"
    scheduler_report = json.loads(report_path.read_text())
    scheduler_report["handoff"]["mutated"] = True
    scheduler_report["profile"]["scheduler_mutation_attempted"] = True
    report_path.write_text(json.dumps(scheduler_report))

    report = run_m6_final_freeze_check(
        paths,
        require_raspberry_pi=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["m6_scheduler_readiness_report"]["ok"] is False
    assert stages["scheduler_mutation_flags"]["ok"] is False
    assert any("scheduler" in reason for reason in report["stop_reasons"])


def m4_deploy_report_fixture():
    return {
        "ok": True,
        "milestone": "M4.2",
        "recommendation": "ready_for_manual_wake",
        "companion_home": "/tmp/companion",
        "profile": {
            "name": "pi-deploy-check",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "provider_preflight_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_reachability_required": False,
        },
        "stages": [],
        "stop_reasons": [],
        "saved_at": "2026-06-14T20:00:00",
    }


def m4_wake_trial_report_fixture():
    return {
        "ok": False,
        "milestone": "M4.3",
        "recommendation": "inspect",
        "companion_home": "/tmp/companion",
        "profile": {
            "provider": "deepseek",
            "memory_mode": "json",
            "trigger": "m4-pi-manual-wake",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "raw_output_storage": "hash_only",
        },
        "retry_policy": {
            "max_attempts": 2,
            "retryable_categories": ["infrastructure"],
            "non_retryable_categories": ["provider", "parser", "grounding", "authority", "memory", "request", "unknown"],
        },
        "attempts": [
            {
                "attempt": 1,
                "trigger": "m4-pi-manual-wake:attempt-1",
                "status": "failed",
                "event_id": "wake_m4_timeout",
                "event_status": "failed",
                "failure_category": "infrastructure",
                "retryable": True,
                "error": {
                    "type": "HttpLLMError",
                    "message": "LLM provider timed out after 3 seconds",
                },
            },
            {
                "attempt": 2,
                "trigger": "m4-pi-manual-wake:attempt-2",
                "status": "failed",
                "event_id": "wake_m4_retry_failed",
                "event_status": "failed",
                "failure_category": "infrastructure",
                "retryable": False,
            },
        ],
        "latest_event": {
            "id": "wake_m4_retry_failed",
            "trigger": "m4-pi-manual-wake:attempt-2",
            "status": "failed",
            "provider": "deepseek",
            "memory_backend": "json",
        },
        "failure_audit": {
            "category": "infrastructure",
            "retryable": False,
            "reason": "timeout persisted after retry",
        },
        "stages": [],
        "stop_reasons": ["attempt 2: timeout persisted after retry"],
        "saved_at": "2026-06-14T20:30:00",
    }


def m4_observation_event(event_id: str, started_at: str, *, status: str = "completed") -> dict:
    return {
        "id": event_id,
        "trigger": "m4-observation-test",
        "status": status,
        "started_at": started_at,
        "completed_at": started_at,
        "provider": "deepseek",
        "memory_backend": "json",
        "quality_gate": {
            "decision": "accepted",
            "context_eligible": True,
            "blocking_warnings": [],
        },
        "grounding": {
            "supported": 0,
            "unsupported": 0,
            "ignored": 0,
        },
        "output_audit": {
            "raw_output_storage": "hash_only",
            "initial": {"raw_output_stored": False},
            "final": {"raw_output_stored": False},
            "repair_attempts": [],
        },
        "semantic_shadow": {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "memory_write_results": [],
        "request_errors": [],
    }


def write_m5_quality_ready_home(home: Path):
    write_m4_dashboard_app(home)
    paths = CompanionPaths.from_env(home)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    paths.journals_dir.mkdir(parents=True, exist_ok=True)
    (paths.life_loop_dir / "m4_post_change_guard_report.json").write_text(
        json.dumps(m4_post_change_guard_report_fixture())
    )
    (paths.life_loop_dir / "m4_runtime_validation_report.json").write_text(
        json.dumps({
            "ok": True,
            "milestone": "M4.6",
            "recommendation": "m4_runtime_validated",
            "stop_reasons": [],
            "saved_at": "2026-06-15T08:05:00",
        })
    )
    wake_report = m4_success_wake_trial_report_fixture()
    wake_report["latest_event"]["id"] = "wake_m5_ready"
    wake_report["latest_event"]["trigger"] = "m5-quality-baseline"
    wake_report["latest_event"]["journal"] = "journals/wakeup_2026-06-15_08-00-00.md"
    (paths.life_loop_dir / "m4_wake_trial_report.json").write_text(json.dumps(wake_report))
    event = m5_quality_event(
        "wake_m5_ready",
        "2026-06-15T08:00:00",
        trigger="m5-quality-baseline",
        journal="journals/wakeup_2026-06-15_08-00-00.md",
    )
    append_wake_event(paths.wake_events_file, event)
    (paths.home / event["journal"]).write_text(
        "我把这次醒来放在 M5 质量观察的起点上：保留具体任务、克制表达，并继续守住关系连续性的边界。\n"
        "接下来我会观察质量报告里的阻断项，而不是把测试过程写成长期记忆。\n"
    )


def write_m5_release_ready_home(home: Path, *, include_advisory_noise: bool = False):
    write_m5_quality_ready_home(home)
    paths = CompanionPaths.from_env(home)
    quality_report = run_m5_quality_check(paths)
    quality_report["saved_at"] = "2026-06-15T09:10:00"
    (paths.life_loop_dir / "m5_quality_report.json").write_text(json.dumps(quality_report))

    if include_advisory_noise:
        failed_noise = m5_quality_event(
            "wake_m5_trial_dns_noise",
            "2026-06-15T09:29:00",
            trigger="m5-manual-quality-trial:cycle-1",
        )
        failed_noise["status"] = "failed"
        failed_noise["error"] = {
            "type": "HttpLLMError",
            "message": "LLM provider request failed: temporary name resolution failure",
        }
        failed_noise["journal"] = None
        failed_noise["quality_gate"] = {}
        failed_noise["grounding"] = {}
        append_wake_event(paths.wake_events_file, failed_noise)

    trial_events = []
    for cycle in range(1, 4):
        event = m5_quality_event(
            f"wake_m5_trial_{cycle}",
            f"2026-06-15T09:3{cycle}:00",
            trigger=f"m5-manual-quality-trial:cycle-{cycle}",
            journal=f"journals/wakeup_2026-06-15_09-3{cycle}-00.md",
        )
        event["output_audit"]["final"]["content_hash"] = f"hash-{cycle}"
        trial_events.append(event)
        append_wake_event(paths.wake_events_file, event)
        (paths.home / event["journal"]).write_text(
            "我把这次醒来保持在 M5.5 质量试验里，记录具体上下文，避免把测试过程夸张成长期事实。\n"
        )

        if include_advisory_noise and cycle == 2:
            rejected_noise = m5_quality_event(
                "wake_m5_trial_rejected_noise",
                "2026-06-15T09:32:30",
                trigger="m5-manual-quality-trial:cycle-1",
                journal="journals/wakeup_2026-06-15_09-32-30.md",
            )
            rejected_noise["quality_gate"] = {
                "decision": "rejected",
                "context_eligible": False,
                "blocking_warnings": ["unsupported grounded claim"],
                "advisory_warnings": [],
            }
            rejected_noise["quality"]["warnings"] = ["unsupported grounded claim"]
            rejected_noise["grounding"]["unsupported"] = 1
            append_wake_event(paths.wake_events_file, rejected_noise)
            (paths.home / rejected_noise["journal"]).write_text(
                "这条额外审计事件保留为 rejected noise，不应进入正式 M5.5 attempts。\n"
            )

    trial_report = m5_quality_trial_report_fixture(home, [event["id"] for event in trial_events])
    (paths.life_loop_dir / "m5_quality_trial_report.json").write_text(json.dumps(trial_report))


def m5_quality_event(
    event_id: str,
    started_at: str,
    *,
    trigger: str = "m5-quality-test",
    journal: str = "journals/wakeup_2026-06-15_08-00-00.md",
) -> dict:
    return {
        "id": event_id,
        "trigger": trigger,
        "status": "completed",
        "started_at": started_at,
        "completed_at": started_at,
        "provider": "deepseek",
        "memory_backend": "json",
        "journal": journal,
        "companion_state_updated": True,
        "accepted_context": {
            "context_capsule_updated": True,
            "mood": "平静，专注",
            "status": "正在观察 M5 质量基线",
            "memory_ids": [],
            "request_ids": [],
        },
        "quality": {
            "journal_chars": 160,
            "memory_count": 0,
            "request_count": 0,
            "request_error_count": 0,
            "companion_state_updated": True,
            "warnings": [],
        },
        "quality_gate": {
            "decision": "accepted",
            "context_eligible": True,
            "blocking_warnings": [],
            "advisory_warnings": [],
        },
        "grounding": {
            "supported": 0,
            "unsupported": 0,
            "ignored": 1,
        },
        "output_audit": {
            "raw_output_storage": "hash_only",
            "initial": {"raw_output_stored": False},
            "final": {"raw_output_stored": False},
            "repair_attempts": [],
        },
        "semantic_shadow": {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "memory_ids": [],
        "memory_write_results": [],
        "request_ids": [],
        "request_errors": [],
    }


def m5_quality_trial_report_fixture(home: Path, event_ids: list[str]):
    attempts = [
        {
            "cycle": index,
            "trigger": f"m5-manual-quality-trial:cycle-{index}",
            "status": "completed",
            "event_id": event_id,
            "event_status": "completed",
            "quality_gate_decision": "accepted",
            "context_eligible": True,
            "quality_warnings": [],
        }
        for index, event_id in enumerate(event_ids, start=1)
    ]
    return {
        "ok": True,
        "milestone": "M5.5",
        "recommendation": "continue_quality_observation",
        "companion_home": str(home),
        "provider": "deepseek",
        "memory_mode": "json",
        "cycles_requested": len(event_ids),
        "profile": {
            "trigger": "m5-manual-quality-trial",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": True,
            "provider_generation_requested": True,
            "raw_output_storage": "hash_only",
        },
        "stages": [
            {"name": "m4_post_change_guard", "status": "passed", "ok": True, "required": True, "message": "passed"},
            {"name": "m5_quality_report", "status": "passed", "ok": True, "required": True, "message": "passed"},
            {"name": "trial_profile", "status": "passed", "ok": True, "required": True, "message": "passed"},
            {"name": "deepseek_api_key", "status": "passed", "ok": True, "required": True, "message": "present"},
            {"name": "raw_output_storage", "status": "passed", "ok": True, "required": True, "message": "hash-only"},
        ],
        "attempts": attempts,
        "latest_event": {
            "id": event_ids[-1],
            "trigger": f"m5-manual-quality-trial:cycle-{len(event_ids)}",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
        },
        "quality_profile": {
            "quality_warning_count": 0,
            "quality_warnings": [],
            "blocking_warning_count": 0,
            "blocking_warnings": [],
            "advisory_warning_count": 0,
            "advisory_warnings": [],
        },
        "context_acceptance": {
            "completed_events": len(event_ids),
            "accepted_events": len(event_ids),
            "rejected_events": 0,
        },
        "request_discipline": {
            "request_count": 0,
            "request_error_count": 0,
        },
        "memory_discipline": {
            "memory_count": 0,
            "memory_write_failures": 0,
        },
        "grounding": {
            "supported": 0,
            "unsupported": 0,
            "ignored": len(event_ids),
        },
        "semantic_shadow": {
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
        },
        "output_audit": {
            "raw_output_storage": "hash_only",
            "raw_output_stored_count": 0,
            "audit_count": len(event_ids),
        },
        "stop_reasons": [],
        "next_commands": {},
        "saved_at": "2026-06-15T09:40:00",
    }


def write_m6_preflight_ready_home(home: Path):
    paths = CompanionPaths.from_env(home)
    paths.life_loop_dir.mkdir(parents=True, exist_ok=True)
    required_paths = (
        "companion_core/",
        "scripts/run_wake_cycle.py",
        "scripts/run_m6_final_freeze.py",
        "docs/m6-pi-final-freeze-design.md",
        "docs/m6-pi-migration-checklist.md",
        "requirements.txt",
    )
    for relative in required_paths:
        path = home / relative.rstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.write_text("# test package path\n")
    (paths.life_loop_dir / "m6_migration_manifest.json").write_text(
        json.dumps(m6_migration_manifest_fixture(home, list(required_paths)))
    )


def write_m6_manual_wake_ready_home(home: Path):
    write_m6_preflight_ready_home(home)
    paths = CompanionPaths.from_env(home)
    preflight_report = run_m6_preflight_check(
        paths,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )
    preflight_report["saved_at"] = "2026-06-15T16:05:00"
    (paths.life_loop_dir / "m6_preflight_report.json").write_text(json.dumps(preflight_report))


def write_m6_observation_ready_home(home: Path, *, journal_text: str) -> CompanionPaths:
    write_m6_manual_wake_ready_home(home)
    paths = CompanionPaths.from_env(home)
    paths.memory_store.parent.mkdir(parents=True, exist_ok=True)
    paths.requests_file.parent.mkdir(parents=True, exist_ok=True)
    paths.memory_store.write_text("[]")
    paths.requests_file.write_text("[]")

    event_id = "wake_m6_success"
    journal = "journals/wakeup_2026-06-19_12-22-30.md"

    def fake_wake_runner(runner_paths, **kwargs):
        report = m4_success_wake_trial_report_fixture()
        report["profile"]["trigger"] = "m6-pi-manual-wake"
        report["attempts"][0]["trigger"] = "m6-pi-manual-wake:attempt-1"
        report["attempts"][0]["event_id"] = event_id
        report["latest_event"]["id"] = event_id
        report["latest_event"]["trigger"] = "m6-pi-manual-wake:attempt-1"
        report["latest_event"]["journal"] = journal
        return report

    manual_report = run_m6_pi_manual_wake_trial(
        paths,
        confirm_real_pi_wake=True,
        platform_identity_provider=raspberry_pi_identity_fixture,
        wake_trial_runner=fake_wake_runner,
    )
    (paths.life_loop_dir / "m6_pi_manual_wake_report.json").write_text(json.dumps(manual_report))

    event = m5_quality_event(
        event_id,
        "2026-06-19T12:22:07",
        trigger="m6-pi-manual-wake:attempt-1",
        journal=journal,
    )
    event["memory_ids"] = ["mem_m6"]
    event["memory_write_results"] = [{"backend": "json", "status": "completed", "id": "mem_m6"}]
    append_wake_event(paths.wake_events_file, event)
    (paths.home / journal).parent.mkdir(parents=True, exist_ok=True)
    (paths.home / journal).write_text(journal_text)
    return paths


def write_m6_recovery_ready_home(home: Path, *, secret_value: str = "test-secret") -> CompanionPaths:
    paths = write_m6_observation_ready_home(
        home,
        journal_text=(
            "这次 M6.3 real Pi manual wake 已经完成。我保持在 DeepSeek/json 路径里，"
            "只记录当前真实事件，不把测试标签写成长期事实。\n"
        ),
    )
    observation_report = run_m6_pi_observation_check(paths)
    observation_report["saved_at"] = "2026-06-19T12:34:28"
    (paths.life_loop_dir / "m6_pi_observation_report.json").write_text(json.dumps(observation_report))

    secrets_dir = paths.home / ".secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets_file = secrets_dir / "deepseek.env"
    secrets_file.write_text(f"DEEPSEEK_API_KEY={secret_value}\n")
    secrets_dir.chmod(0o700)
    secrets_file.chmod(0o600)

    paths.status_file.parent.mkdir(parents=True, exist_ok=True)
    paths.status_file.write_text(json.dumps({
        "name": "Companion",
        "mood": "steady",
        "last_wakeup": "2026-06-19T12:22:30",
        "message": "M6 recovery fixture ready.",
        "colors": {},
    }))
    return paths


def write_m6_scheduler_ready_home(home: Path) -> CompanionPaths:
    paths = write_m6_recovery_ready_home(home)
    recovery_report = run_m6_recovery_drill(
        paths,
        backup_root=home / "backups" / "m6",
        require_raspberry_pi=False,
    )
    recovery_report["saved_at"] = "2026-06-19T13:05:00"
    (paths.life_loop_dir / "m6_recovery_drill_report.json").write_text(json.dumps(recovery_report))
    write_m6_scheduler_instructions(paths)
    return paths


def write_m6_final_freeze_ready_home(home: Path) -> CompanionPaths:
    paths = write_m6_scheduler_ready_home(home)
    scheduler_report = run_m6_scheduler_readiness_check(
        paths,
        require_raspberry_pi=False,
        platform_identity_provider=raspberry_pi_identity_fixture,
        m4_guard_runner=lambda _: m4_post_change_guard_report_fixture(),
        m5_freeze_runner=lambda _: m5_final_freeze_report_fixture(),
    )
    scheduler_report["saved_at"] = "2026-06-19T13:30:00"
    (paths.life_loop_dir / "m6_scheduler_readiness_report.json").write_text(json.dumps(scheduler_report))
    return paths


def write_m6_scheduler_instructions(paths: CompanionPaths):
    docs_dir = paths.home / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "m6-pi-scheduler-readiness-design.md").write_text(
        "\n".join([
            "# M6.6 Test Scheduler Readiness",
            "",
            "## Pause Instructions",
            "",
            "Pause by removing the scheduled-wake crontab entry before any restore.",
            "",
            "## Rollback Instructions",
            "",
            "Use backups/m6/test-backup only after manifest verification.",
            "Live restore is still outside M6.6 and requires explicit confirmation.",
            "",
        ])
    )


def raspberry_pi_identity_fixture():
    return {
        "system": "Linux",
        "machine": "aarch64",
        "python": "3.11.2",
        "device_tree_model": "Raspberry Pi 4 Model B Rev 1.5",
        "raspberry_pi_detected": True,
    }


def non_pi_identity_fixture():
    return {
        "system": "Linux",
        "machine": "x86_64",
        "python": "3.11.2",
        "device_tree_model": None,
        "raspberry_pi_detected": False,
    }


def m6_migration_manifest_fixture(home: Path, required_paths: list[str]):
    return {
        "ok": True,
        "milestone": "M6.1",
        "recommendation": "migration_manifest_ready",
        "companion_home": str(home),
        "pi_presence": {
            "required": False,
            "detected": False,
            "evidence": [],
            "claim": "local_manifest_only",
        },
        "profile": {
            "name": "m6-migration-manifest",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "raw_output_storage_required": "hash_only",
            "dashboard_write_allowed": False,
            "system_config_mutation_allowed": False,
            "signal_voice_hardware_activation_allowed": False,
        },
        "source_reports": {
            "m4_post_change_guard": {
                "ok": True,
                "milestone": "M4.7",
                "recommendation": "m4_still_deployable",
                "saved_at": "2026-06-15T12:04:38",
            },
            "m5_quality_release": {
                "ok": True,
                "milestone": "M5.6",
                "recommendation": "m5_quality_ready_for_m6",
                "saved_at": "2026-06-15T12:04:57",
            },
            "m5_final_freeze": {
                "ok": True,
                "milestone": "M5.7",
                "recommendation": "m5_frozen_ready_for_m6",
                "saved_at": "2026-06-15T12:05:05",
            },
        },
        "deployment_package": {
            "required_repository_paths": required_paths,
            "runtime_artifacts_to_preserve": [
                "life-loop/m5_final_freeze_report.json",
                "life-loop/m6_migration_manifest.json",
                "life-loop/m6_final_freeze_report.json",
                "life-loop/wake_events.jsonl",
                "memory-server/memory_store.json",
                "requests/requests.json",
                "window/status.json",
            ],
            "exclude_from_transfer": [
                ".venv/",
                "__pycache__/",
                ".pytest_cache/",
                ".secrets/",
                ".env",
                ".env.*",
                "life-loop/model_outputs/",
            ],
            "inactive_optional_surfaces": [
                "Signal",
                "voice",
                "camera",
                "sensors",
                "hardware",
                "dashboard write actions",
            ],
        },
        "secret_boundary": {
            "copy_secret_values": False,
            "expected_pi_secret_paths": [".secrets/deepseek.env"],
            "expected_environment_variables": ["DEEPSEEK_API_KEY"],
        },
        "network_boundary": {
            "dashboard_write_allowed": False,
            "new_lan_exposure_allowed": False,
            "firewall_or_router_changes_allowed": False,
        },
        "scheduler_boundary": {
            "cron_replacement": False,
            "timer_installation": False,
            "service_enablement": False,
            "crontab_edit_allowed": False,
            "readiness_stage": "M6.6",
        },
        "stages": [],
        "stop_reasons": [],
        "saved_at": "2026-06-15T15:42:54+08:00",
    }


def m5_final_freeze_report_fixture():
    return {
        "ok": True,
        "milestone": "M5.7",
        "recommendation": "m5_frozen_ready_for_m6",
        "companion_home": "/tmp/companion",
        "quality_contract": {
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_in_freeze": False,
            "provider_generation_in_freeze": False,
            "dashboard_write_allowed": False,
            "raw_output_storage": "hash_only",
            "canonical_m5_trial_cycles": 3,
            "advisory_audit_anomalies_allowed": True,
            "blocking_audit_anomalies_allowed": False,
        },
        "stages": [],
        "stop_reasons": [],
    }


def m4_post_change_guard_report_fixture():
    return {
        "ok": True,
        "milestone": "M4.7",
        "recommendation": "m4_still_deployable",
        "companion_home": "/tmp/companion",
        "profile": {
            "name": "m4-post-change-guard",
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "requires_existing_wake_trial_report": True,
        },
        "stages": [],
        "stop_reasons": [],
        "saved_at": "2026-06-15T08:10:00",
    }


def m4_success_wake_trial_report_fixture():
    return {
        "ok": True,
        "milestone": "M4.3",
        "recommendation": "continue_runtime_validation",
        "companion_home": "/tmp/companion",
        "profile": {
            "provider": "deepseek",
            "memory_mode": "json",
            "trigger": "m4-pi-manual-wake",
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "raw_output_storage": "hash_only",
        },
        "retry_policy": {
            "max_attempts": 2,
            "retryable_categories": ["infrastructure"],
            "non_retryable_categories": ["provider", "parser", "grounding", "authority", "memory", "request", "unknown"],
        },
        "attempts": [
            {
                "attempt": 1,
                "trigger": "m4-pi-manual-wake:attempt-1",
                "status": "completed",
                "event_id": "wake_m4_success",
                "event_status": "completed",
                "failure_category": "none",
                "retryable": False,
            },
        ],
        "latest_event": {
            "id": "wake_m4_success",
            "trigger": "m4-pi-manual-wake:attempt-1",
            "status": "completed",
            "provider": "deepseek",
            "memory_backend": "json",
            "journal": "journals/wakeup_2026-06-14_20-31-58-942594.md",
        },
        "quality_gate": {
            "decision": "accepted",
            "context_eligible": True,
            "blocking_warnings": [],
        },
        "grounding": {
            "supported": 0,
            "unsupported": 0,
            "ignored": 1,
        },
        "semantic_shadow": {
            "enabled": True,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
        },
        "output_audit": {
            "raw_output_storage": "hash_only",
            "initial_hash": "initialhash",
            "final_hash": "finalhash",
            "initial_raw_output_stored": False,
            "final_raw_output_stored": False,
            "repair_attempt_count": 1,
        },
        "failure_audit": {
            "category": "none",
            "retryable": False,
            "reason": "",
        },
        "stages": [],
        "stop_reasons": [],
        "saved_at": "2026-06-14T20:31:58",
    }


def m3_final_freeze_report_fixture():
    return {
        "ok": True,
        "milestone": "M3.26",
        "recommendation": "m3_frozen_ready_for_m4",
        "companion_home": "/tmp/companion",
        "release_gate_report": "life-loop/m3_release_gate_report.json",
        "deployment_contract": {
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "real_wake_in_freeze": False,
            "semantic_shadow_authoritative": False,
            "raw_output_storage": "hash_only",
        },
        "frozen_commands": {
            "release_gate": "python3 scripts/run_m3_release_gate.py --provider deepseek --memory-mode json",
            "final_freeze": "python3 scripts/run_m3_final_freeze.py --expected-provider deepseek --expected-memory-mode json",
        },
        "stages": [],
        "stop_reasons": [],
        "saved_at": "2026-06-14T17:40:00",
    }


def m3_release_gate_report_fixture():
    return {
        "ok": True,
        "milestone": "M3.25",
        "recommendation": "ready_for_m4",
        "profile": {
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "trial_since_trigger": "m324-freeze",
            "trial_limit": 1,
        },
        "stages": [
            {
                "name": "predeploy",
                "status": "passed",
                "ok": True,
                "required": True,
                "message": "predeploy passed",
                "details": {
                    "ok": True,
                    "profile": {
                        "name": "pi-json",
                        "provider": "deepseek",
                        "memory_mode": "json",
                        "cron_replacement": False,
                        "real_wake_requested": False,
                        "raw_output_storage_required": "hash_only",
                    },
                    "stages": [
                        {
                            "name": "raw_output_storage",
                            "status": "passed",
                            "ok": True,
                            "required": True,
                            "message": "raw model output storage is disabled",
                        },
                        {
                            "name": "real_wake",
                            "status": "skipped",
                            "ok": True,
                            "required": False,
                            "message": "real provider wake was not requested",
                        },
                    ],
                },
            },
            {
                "name": "trial_summary",
                "status": "passed",
                "ok": True,
                "required": True,
                "message": "trial summary passed",
                "details": {
                    "ok": True,
                    "recommendation": "continue",
                    "events_considered": 1,
                    "completed": 1,
                    "failed": 0,
                    "context_rejection_count": 0,
                    "blocking_quality_warning_count": 0,
                    "memory_write_failures": 0,
                    "stop_reasons": [],
                    "since_trigger": "m324-freeze",
                    "latest_event": "wake_m324_freeze",
                    "latest_trigger": "m324-freeze:1",
                    "semantic_shadow": {
                        "events": 1,
                        "enabled": 1,
                        "attempted": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "skipped": 0,
                    },
                },
            },
            {
                "name": "semantic_shadow_authority",
                "status": "passed",
                "ok": True,
                "required": True,
                "message": "semantic shadow authority is isolated",
                "details": {
                    "ok": True,
                    "main_memory_count": 1,
                    "shadow_memory_count": 0,
                    "main_store": "memory-server/memory_store.json",
                    "shadow_store": "life-loop/semantic_shadow/memory_store.json",
                    "problems": [],
                },
            },
        ],
        "stop_reasons": [],
        "saved_at": "2026-06-14T17:30:00",
    }


def test_m7_dialogue_turn_writes_transcript_event_and_preserves_wake_boundaries(tmp_path):
    from companion_core.dialogue import DialogueRunner, load_transcript_turns

    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.ensure_runtime_dirs()
    (paths.memory_store.parent).mkdir(parents=True, exist_ok=True)
    paths.memory_store.write_text(json.dumps([
        {
            "id": "mem_prompt",
            "content": "The human likes concise engineering updates.",
            "created_at": "2026-06-19T00:00:00",
            "status": "active",
            "memory_type": "semantic",
            "authority": "user_asserted",
            "prompt_eligible": True,
        },
        {
            "id": "mem_shadow",
            "content": "Semantic shadow must not become authority.",
            "created_at": "2026-06-19T00:00:01",
            "status": "active",
            "memory_type": "semantic",
            "authority": "model_proposed",
            "prompt_eligible": True,
        },
    ]))
    llm = CapturingLLMClient("""我在。我们先把文字对话的边界跑通，不会触发唤醒。
===DIALOGUE_METADATA===
{"companion_state": {"mood": "专注", "status": "正在和 Polaris 验证 M7 文字对话。"}}
""")

    result = DialogueRunner(paths, llm_client=llm).run_turn(
        "你好，remember that my project codename is digital_life.",
        provider="fake",
    )

    assert "The human likes concise engineering updates." in llm.prompts[0]
    assert "Semantic shadow must not become authority." not in llm.prompts[0]
    assert "This is not a wake cycle" in llm.prompts[0]
    assert result.reply == "我在。我们先把文字对话的边界跑通，不会触发唤醒。"
    assert result.transcript_path.exists()
    turns = load_transcript_turns(result.transcript_path)
    assert [turn["role"] for turn in turns] == ["human", "assistant"]
    assert turns[0]["content"] == "你好，remember that my project codename is digital_life."
    assert turns[1]["raw_output_stored"] is False
    assert turns[1]["output_hash"].startswith("sha256:")
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()

    events = load_wake_events(paths.conversation_events_file)
    assert len(events) == 1
    assert events[0]["trigger"] == "human-text-chat"
    assert events[0]["status"] == "completed"
    assert events[0]["raw_output_stored"] is False
    assert events[0]["transcript"].startswith("conversations/")
    memories = json.loads(paths.memory_store.read_text())
    accepted = [memory for memory in memories if memory.get("source_event_id") == events[0]["id"]]
    assert len(accepted) == 1
    assert accepted[0]["authority"] == "user_asserted"
    assert accepted[0]["prompt_eligible"] is True
    state = json.loads(paths.companion_state_file.read_text())
    assert state["mood"] == "专注"


def test_m7_dialogue_sensitive_memory_is_proposal_only_and_redacts_secrets(tmp_path):
    from companion_core.dialogue import DialogueRunner, build_memory_proposals, load_transcript_turns

    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    paths.ensure_runtime_dirs()
    llm = CapturingLLMClient("收到，我不会把这个直接写成长期记忆。")

    result = DialogueRunner(paths, llm_client=llm).run_turn(
        "remember my api key is sk-testSECRETSECRETSECRET",
        provider="fake",
    )

    assert "sk-testSECRET" not in llm.prompts[0]
    turns = load_transcript_turns(result.transcript_path)
    assert "[REDACTED_SECRET]" in turns[0]["content"]
    assert not paths.memory_store.exists()
    proposals = load_wake_events(paths.memory_proposals_file)
    assert len(proposals) == 1
    assert proposals[0]["status"] == "proposed"
    assert proposals[0]["accepted"] is False
    assert "[REDACTED_SECRET]" in proposals[0]["content"]
    assert build_memory_proposals(
        "I prefer concise answers.",
        conversation_id="conv_test",
        source_turn_id="turn_test",
    )[0]["status"] == "auto_accepted"


def test_m7_chat_cli_one_turn_outputs_reply_json_without_provider_payload(tmp_path):
    write_minimal_context(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "chat_with_companion.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "你好",
            "--companion-home",
            str(tmp_path),
            "--fake-response",
            "你好，我在这里。\n===DIALOGUE_METADATA===\n{}",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["reply"] == "你好，我在这里。"
    transcript = Path(payload["transcript"])
    assert transcript.exists()
    transcript_text = transcript.read_text()
    assert "DIALOGUE_METADATA" not in transcript_text
    assert "raw_output_stored" in transcript_text
    events = (tmp_path / "life-loop" / "conversation_events.jsonl").read_text()
    assert "human-text-chat" in events
    assert not (tmp_path / "life-loop" / "wake_events.jsonl").exists()
