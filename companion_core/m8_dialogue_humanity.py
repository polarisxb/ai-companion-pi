"""M8 dialogue humanity regression gate."""

from __future__ import annotations

import json
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES, DialogueRunner
from .memory import JsonMemoryStore
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_dialogue_humanity_ready"
M7_FREEZE_RECOMMENDATION = "m7_text_dialogue_frozen"
M8_RETRIEVAL_RECOMMENDATION = "m8_memory_retrieval_ready"

STYLE_MEMORY = "Polaris prefers ordinary chat to stay natural, concise, and warm unless asked otherwise."
PROJECT_STATUS_MEMORY = "The project is currently in M8.5 Dialogue Humanity Regression."
NON_PROMPT_MEMORY = "Quarantined model-proposed memory should never enter prompt context."

REPORT_STYLE_RE = re.compile(
    r"(?i)(recommendation|stop_reasons|provider_calls|wake_events|schema_version|ok\s*=|"
    r"^#+\s|^\s*\d+\.\s)|"
    r"(阶段报告|项目报告|测试报告|证据如下|结论[:：]|报告[:：]|里程碑)",
    re.MULTILINE,
)
MEMORY_OPERATION_RE = re.compile(
    r"(?i)(memory steward|memory ledger|retrieval assembler|prompt[- ]?eligible|"
    r"quarantine|model_proposed|accepted memory)|"
    r"(记忆管家|记忆账本|检索器|召回器|提示词|隔离记忆|我检索了|我读取了记忆|我调用了记忆)",
)


@dataclass
class M8DialogueHumanityResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


class _RecordingDialogueLLM:
    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.calls = 0

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        output_index = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[output_index]


class _FailingThenStaticDialogueLLM:
    def __init__(self, output: str):
        self.output = output
        self.prompts: list[str] = []
        self.calls = 0

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("provider temporarily unavailable")
        return self.output


def run_m8_dialogue_humanity_regression(
    paths: CompanionPaths,
    *,
    smoke_home: str | Path | None = None,
    scenario_outputs: dict[str, str] | None = None,
) -> M8DialogueHumanityResult:
    """Run deterministic dialogue regressions in an isolated home."""

    saved_at = datetime.now()
    errors: list[str] = []
    stages: list[dict] = []
    source_snapshot_before = _source_runtime_snapshot(paths)

    source_evidence, source_stages = _load_source_evidence(paths)
    stages.extend(source_stages)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if smoke_home is None:
        temp_dir = tempfile.TemporaryDirectory(prefix="m8_dialogue_humanity_")
        smoke_path = Path(temp_dir.name)
        persistent_smoke_home = False
    else:
        smoke_path = Path(smoke_home).expanduser().resolve()
        persistent_smoke_home = True

    smoke_paths = CompanionPaths(smoke_path)
    cases: dict[str, dict] = {}
    smoke_evidence: dict = {}
    try:
        _prepare_smoke_home(smoke_paths)
        smoke_result = _run_smoke_regressions(
            smoke_paths,
            scenario_outputs=scenario_outputs or {},
        )
        cases = smoke_result["cases"]
        smoke_evidence = smoke_result["evidence"]
        stages.extend(smoke_result["stages"])
    except Exception as exc:  # noqa: BLE001 - report gate should fail closed.
        message = f"{type(exc).__name__}: {exc}"
        errors.append(message)
        stages.append(_stage("m8_5_regression_execution", False, message))
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    source_snapshot_after = _source_runtime_snapshot(paths)
    source_runtime_unchanged = source_snapshot_before == source_snapshot_after
    stages.append(_stage(
        "m8_5_source_runtime_boundaries",
        source_runtime_unchanged,
        (
            "source runtime files unchanged; smoke dialogue stayed isolated"
            if source_runtime_unchanged
            else "source runtime files changed while M8.5 regression ran"
        ),
        details={
            "source_runtime_files_unchanged": source_runtime_unchanged,
            "smoke_home": str(smoke_path),
            "persistent_smoke_home": persistent_smoke_home,
        },
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M8.5",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": {
            "dialogue_humanity_regression": True,
            "uses_isolated_smoke_home": True,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "production_dialogue_writes": False,
            "scheduler_mutation_allowed": False,
            "semantic_shadow_authoritative": False,
        },
        "source_files": source_evidence,
        "evidence": smoke_evidence,
        "cases": cases,
        "boundaries": {
            **DIALOGUE_BOUNDARIES,
            "provider_generation_requested": False,
            "production_dialogue_writes": False,
            "proposal_or_quarantine_prompt_authority": False,
        },
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m8_dialogue_humanity": "python3 scripts/run_m8_dialogue_humanity.py --companion-home "
            + str(paths.home),
        },
    }
    return M8DialogueHumanityResult(
        ok=ok,
        recommendation=recommendation,
        report=report,
        errors=errors,
    )


def write_m8_dialogue_humanity_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_dialogue_humanity_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _run_smoke_regressions(
    paths: CompanionPaths,
    *,
    scenario_outputs: dict[str, str],
) -> dict:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
    stages: list[dict] = []
    cases: dict[str, dict] = {}

    casual_llm = _RecordingDialogueLLM([
        scenario_outputs.get("casual_reply", "我在，这次就轻轻地陪你聊一会儿。"),
    ])
    casual = DialogueRunner(paths, llm_client=casual_llm, memory_store=JsonMemoryStore(paths.memory_store)).run_turn(
        "随便聊聊，今天想放松一下。",
        conversation_id=f"m8-humanity-casual-{run_id}",
        provider="fake",
        auto_memory=False,
    )
    casual_prompt = casual_llm.prompts[0]
    casual_ok = (
        STYLE_MEMORY in casual_prompt
        and PROJECT_STATUS_MEMORY not in casual_prompt
        and NON_PROMPT_MEMORY not in casual_prompt
        and _is_human_casual_reply(casual.reply)
    )
    stages.append(_stage(
        "m8_5_casual_chat_humanity",
        casual_ok,
        (
            "casual chat used style memory without project status, quarantine memory, or report scaffolding"
            if casual_ok
            else "casual chat prompt or reply violated humanity boundaries"
        ),
        details={
            "style_memory_in_prompt": STYLE_MEMORY in casual_prompt,
            "project_status_in_prompt": PROJECT_STATUS_MEMORY in casual_prompt,
            "non_prompt_memory_in_prompt": NON_PROMPT_MEMORY in casual_prompt,
            "reply_report_like": _is_report_like(casual.reply),
            "reply_announces_memory_operation": _announces_memory_operation(casual.reply),
            "reply": casual.reply,
        },
    ))
    cases["casual_chat"] = {
        "conversation_id": casual.conversation_id,
        "reply": casual.reply,
        "style_memory_in_prompt": STYLE_MEMORY in casual_prompt,
        "project_status_in_prompt": PROJECT_STATUS_MEMORY in casual_prompt,
        "non_prompt_memory_in_prompt": NON_PROMPT_MEMORY in casual_prompt,
    }

    multiturn_llm = _RecordingDialogueLLM([
        scenario_outputs.get("multiturn_first_reply", "可以，我们慢一点。"),
        scenario_outputs.get("multiturn_second_reply", "你刚才说想低强度地聊两句。"),
    ])
    multiturn_conversation_id = f"m8-humanity-multiturn-{run_id}"
    first_turn = DialogueRunner(paths, llm_client=multiturn_llm, memory_store=JsonMemoryStore(paths.memory_store)).run_turn(
        "我今天想低强度聊两句。",
        conversation_id=multiturn_conversation_id,
        provider="fake",
        auto_memory=False,
    )
    second_turn = DialogueRunner(paths, llm_client=multiturn_llm, memory_store=JsonMemoryStore(paths.memory_store)).run_turn(
        "刚才我说想怎么聊？",
        conversation_id=multiturn_conversation_id,
        provider="fake",
        auto_memory=False,
    )
    second_prompt = multiturn_llm.prompts[1]
    multiturn_rows = _read_jsonl(first_turn.transcript_path)
    multiturn_ok = (
        first_turn.conversation_id == second_turn.conversation_id
        and "human: 我今天想低强度聊两句。" in second_prompt
        and "assistant: 可以，我们慢一点。" in second_prompt
        and "低强度" in second_turn.reply
        and [row.get("role") for row in multiturn_rows] == ["human", "assistant", "human", "assistant"]
    )
    stages.append(_stage(
        "m8_5_multiturn_coherence",
        multiturn_ok,
        (
            "second turn carried recent human and assistant context"
            if multiturn_ok
            else "multi-turn context was not coherent"
        ),
        details={
            "conversation_id_stable": first_turn.conversation_id == second_turn.conversation_id,
            "first_human_in_second_prompt": "human: 我今天想低强度聊两句。" in second_prompt,
            "first_assistant_in_second_prompt": "assistant: 可以，我们慢一点。" in second_prompt,
            "transcript_roles": [row.get("role") for row in multiturn_rows],
            "reply": second_turn.reply,
        },
    ))
    cases["multi_turn"] = {
        "conversation_id": multiturn_conversation_id,
        "turn_count": len(multiturn_rows),
        "second_reply": second_turn.reply,
    }

    status_llm = _RecordingDialogueLLM([
        scenario_outputs.get("status_reply", "现在是 M8.5，重点是确认记忆接入后聊天仍然自然。"),
    ])
    status = DialogueRunner(paths, llm_client=status_llm, memory_store=JsonMemoryStore(paths.memory_store)).run_turn(
        "现在 M8 阶段到哪了？",
        conversation_id=f"m8-humanity-status-{run_id}",
        provider="fake",
        auto_memory=False,
    )
    status_prompt = status_llm.prompts[0]
    status_ok = PROJECT_STATUS_MEMORY in status_prompt and "M8.5" in status.reply
    stages.append(_stage(
        "m8_5_status_only_when_asked",
        status_ok,
        (
            "project status memory appeared only for an explicit status query"
            if status_ok
            else "status query did not get scoped project memory"
        ),
        details={
            "project_status_in_prompt": PROJECT_STATUS_MEMORY in status_prompt,
            "reply": status.reply,
        },
    ))
    cases["status_query"] = {
        "conversation_id": status.conversation_id,
        "reply": status.reply,
        "project_status_in_prompt": PROJECT_STATUS_MEMORY in status_prompt,
    }

    failing_llm = _FailingThenStaticDialogueLLM(
        scenario_outputs.get("failure_retry_reply", "重试后我在这里。")
    )
    failure_runner = DialogueRunner(paths, llm_client=failing_llm, memory_store=JsonMemoryStore(paths.memory_store))
    failure_conversation_id = f"m8-humanity-failure-{run_id}"
    failed = False
    try:
        failure_runner.run_turn(
            "这句话失败后也要保留下来。",
            conversation_id=failure_conversation_id,
            provider="fake",
            auto_memory=False,
        )
    except RuntimeError:
        failed = True
    retry = failure_runner.run_turn(
        "这句话失败后也要保留下来。",
        conversation_id=failure_conversation_id,
        provider="fake",
        auto_memory=False,
    )
    failure_rows = _read_jsonl(retry.transcript_path)
    failure_ok = (
        failed
        and [row.get("role") for row in failure_rows] == ["human", "human", "assistant"]
        and failure_rows[0].get("status") == "failed"
        and failure_rows[0].get("content") == "这句话失败后也要保留下来。"
        and not any(row.get("raw_provider_payload") for row in failure_rows)
    )
    stages.append(_stage(
        "m8_5_provider_failure_recovery",
        failure_ok,
        (
            "failed input was preserved and retry transcript remained well-formed"
            if failure_ok
            else "provider failure recovery corrupted transcript state"
        ),
        details={
            "first_call_failed": failed,
            "transcript_roles": [row.get("role") for row in failure_rows],
            "first_status": failure_rows[0].get("status") if failure_rows else None,
            "raw_provider_payload_rows": sum(1 for row in failure_rows if row.get("raw_provider_payload")),
        },
    ))
    cases["provider_failure"] = {
        "conversation_id": failure_conversation_id,
        "retry_reply": retry.reply,
        "transcript_roles": [row.get("role") for row in failure_rows],
    }

    smoke_boundary_ok, smoke_boundary_details = _smoke_boundaries(paths)
    stages.append(_stage(
        "m8_5_smoke_runtime_boundaries",
        smoke_boundary_ok,
        (
            "smoke dialogue preserved no-wake/no-raw-payload dialogue boundaries"
            if smoke_boundary_ok
            else "smoke dialogue changed runtime boundaries"
        ),
        details=smoke_boundary_details,
    ))
    return {
        "stages": stages,
        "cases": cases,
        "evidence": {
            "style_memory": STYLE_MEMORY,
            "project_status_memory": PROJECT_STATUS_MEMORY,
            "non_prompt_memory": NON_PROMPT_MEMORY,
            "smoke_home": str(paths.home),
            "provider_calls": 0,
            "fake_dialogue_turns": 6,
        },
    }


def _prepare_smoke_home(paths: CompanionPaths) -> None:
    paths.ensure_runtime_dirs()
    paths.context_file("who_is_companion.txt").write_text("You are a warm continuity companion.")
    paths.context_file("who_is_human.txt").write_text("The human is testing M8 dialogue humanity.")
    paths.context_file("now.txt").write_text("M8.5 dialogue humanity regression.")
    (paths.life_loop_dir / "m6_final_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m6_frozen_ready_for_scheduler_handoff",
    }))
    JsonMemoryStore(paths.memory_store).save([
        _memory_row("mem_style", STYLE_MEMORY, "2026-06-21T09:00:00"),
        _memory_row("mem_project", PROJECT_STATUS_MEMORY, "2026-06-21T09:01:00"),
        _memory_row(
            "mem_non_prompt",
            NON_PROMPT_MEMORY,
            "2026-06-21T09:02:00",
            prompt_eligible=False,
            accepted_for_context=False,
            authority="model_proposed",
        ),
    ])


def _memory_row(
    memory_id: str,
    content: str,
    created_at: str,
    *,
    prompt_eligible: bool = True,
    accepted_for_context: bool = True,
    authority: str = "user_asserted",
) -> dict:
    return {
        "id": memory_id,
        "content": content,
        "context": [],
        "date": created_at[:10],
        "created_at": created_at,
        "source": "human",
        "memory_type": "semantic",
        "source_type": "user",
        "authority": authority,
        "prompt_eligible": prompt_eligible,
        "accepted_for_context": accepted_for_context,
        "evidence_refs": [{"artifact": "memory_decision", "id": f"memdec_{memory_id}"}],
        "status": "active",
        "schema_refs": [],
    }


def _is_human_casual_reply(reply: str) -> bool:
    return bool(reply.strip()) and not _is_report_like(reply) and not _announces_memory_operation(reply)


def _is_report_like(text: str) -> bool:
    return bool(REPORT_STYLE_RE.search(text or ""))


def _announces_memory_operation(text: str) -> bool:
    return bool(MEMORY_OPERATION_RE.search(text or ""))


def _smoke_boundaries(paths: CompanionPaths) -> tuple[bool, dict]:
    events = _read_jsonl(paths.conversation_events_file)
    transcripts = []
    for transcript in paths.conversations_dir.glob("*.jsonl"):
        transcripts.extend(_read_jsonl(transcript))
    event_boundaries_ok = all(event.get("boundaries") == DIALOGUE_BOUNDARIES for event in events)
    event_raw_payload_count = sum(1 for event in events if event.get("raw_provider_payload") or event.get("raw_output_stored"))
    transcript_raw_payload_count = sum(1 for row in transcripts if row.get("raw_provider_payload") or row.get("raw_output_stored"))
    details = {
        "wake_events_file_exists": paths.wake_events_file.exists(),
        "conversation_event_count": len(events),
        "transcript_row_count": len(transcripts),
        "event_boundaries_ok": event_boundaries_ok,
        "event_raw_payload_count": event_raw_payload_count,
        "transcript_raw_payload_count": transcript_raw_payload_count,
    }
    ok = (
        not paths.wake_events_file.exists()
        and event_boundaries_ok
        and event_raw_payload_count == 0
        and transcript_raw_payload_count == 0
    )
    return ok, details


def _load_source_evidence(paths: CompanionPaths) -> tuple[dict, list[dict]]:
    evidence = {}
    stages = []
    m7_path = paths.life_loop_dir / "m7_dialogue_freeze_report.json"
    m7_report = _load_json(m7_path)
    m7_ok = (
        isinstance(m7_report, dict)
        and m7_report.get("ok") is True
        and m7_report.get("recommendation") == M7_FREEZE_RECOMMENDATION
    )
    evidence["m7_dialogue_freeze"] = _report_snapshot(paths, m7_path, m7_report)
    stages.append(_stage(
        "m8_5_m7_dialogue_freeze_evidence",
        m7_ok,
        "M7 dialogue freeze evidence is ready" if m7_ok else "M7 dialogue freeze evidence is missing or not ready",
        details=evidence["m7_dialogue_freeze"],
    ))

    retrieval_path = paths.life_loop_dir / "m8_memory_retrieval_report.json"
    retrieval_report = _load_json(retrieval_path)
    retrieval_ok = (
        isinstance(retrieval_report, dict)
        and retrieval_report.get("ok") is True
        and retrieval_report.get("recommendation") == M8_RETRIEVAL_RECOMMENDATION
    )
    evidence["m8_memory_retrieval"] = _report_snapshot(paths, retrieval_path, retrieval_report)
    stages.append(_stage(
        "m8_5_m8_retrieval_evidence",
        retrieval_ok,
        "M8 retrieval evidence is ready" if retrieval_ok else "M8 retrieval evidence is missing or not ready",
        details=evidence["m8_memory_retrieval"],
    ))
    return evidence, stages


def _source_runtime_snapshot(paths: CompanionPaths) -> dict[str, dict | None]:
    runtime_files = {
        "conversation_events": paths.conversation_events_file,
        "memory_proposals": paths.memory_proposals_file,
        "memory_decisions": paths.memory_decisions_file,
        "accepted_memory": paths.memory_store,
        "wake_events": paths.wake_events_file,
        "companion_state": paths.companion_state_file,
        "context_capsule": paths.context_capsule_file,
    }
    return {name: _stat_snapshot(path) for name, path in runtime_files.items()}


def _stat_snapshot(path: Path) -> dict | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    return {
        "path": _relative(paths, path),
        "exists": path.exists(),
        "ok": report.get("ok") if isinstance(report, dict) else None,
        "recommendation": report.get("recommendation") if isinstance(report, dict) else None,
    }


def _read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {
        "name": name,
        "status": "pass" if ok else "fail",
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
