"""M8 retrieval assembler for dialogue memory context."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .memory import JsonMemoryStore, LEGACY_PROMPT_SOURCES, PROMPT_AUTHORITIES, PROMPT_MEMORY_TYPES
from .paths import CompanionPaths


READY_RECOMMENDATION = "m8_memory_retrieval_ready"

STATUS_QUERY_RE = re.compile(
    r"(?i)\b(status|phase|stage|progress|milestone|current|roadmap|tests?|evidence|m[0-9]+(?:\.[0-9]+)?)\b|"
    r"(状态|阶段|进度|里程碑|当前|现在|测试|证据|冻结|报告)"
)
PROJECT_STATE_RE = re.compile(
    r"(?i)\b(project|phase|stage|progress|milestone|scheduler|freeze|frozen|handoff|m[0-9]+(?:\.[0-9]+)?)\b|"
    r"(项目|阶段|进度|里程碑|当前|调度|冻结|交接)"
)
STYLE_MEMORY_RE = re.compile(
    r"(?i)\b(prefer|preference|like|want|chat|reply|respond|report|style|tone)\b|"
    r"(偏好|喜欢|希望|想要|聊天|回复|报告|风格|语气)"
)


@dataclass
class RetrievedMemory:
    memory: dict
    score: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.memory.get("id"),
            "content": self.memory.get("content"),
            "score": self.score,
            "reasons": list(self.reasons),
            "memory_type": self.memory.get("memory_type"),
            "authority": self.memory.get("authority"),
        }


@dataclass
class MemoryRetrievalResult:
    memories: list[dict]
    retrieved: list[RetrievedMemory]
    filtered: list[dict]
    query: str

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "memories": [item.to_dict() for item in self.retrieved],
            "filtered": list(self.filtered),
        }


def assemble_dialogue_memory_context(
    paths: CompanionPaths,
    query: str,
    *,
    memory_store: JsonMemoryStore | None = None,
    limit: int = 5,
) -> MemoryRetrievalResult:
    """Return small prompt-safe accepted memory context plus audit reasons."""

    memory_store = memory_store or JsonMemoryStore(paths.memory_store)
    memories = memory_store.load()
    status_requested = _is_status_query(query)
    candidates: list[RetrievedMemory] = []
    filtered: list[dict] = []
    for memory in memories:
        memory_id = str(memory.get("id") or "")
        if not _is_prompt_eligible(memory):
            filtered.append({
                "id": memory_id,
                "reason": "not_prompt_eligible_accepted_memory",
            })
            continue
        content = str(memory.get("content") or "")
        if _is_project_state_memory(content) and not status_requested:
            filtered.append({
                "id": memory_id,
                "reason": "project_state_filtered_without_status_query",
            })
            continue
        score, reasons = _score_memory(memory, query, status_requested=status_requested)
        candidates.append(RetrievedMemory(memory=memory, score=score, reasons=reasons))

    candidates.sort(
        key=lambda item: (
            item.score,
            str(item.memory.get("created_at", item.memory.get("timestamp", ""))),
        ),
        reverse=True,
    )
    selected = candidates[:limit]
    return MemoryRetrievalResult(
        memories=[item.memory for item in selected],
        retrieved=selected,
        filtered=filtered,
        query=query,
    )


def run_m8_memory_retrieval_check(
    paths: CompanionPaths,
    *,
    query: str = "",
    limit: int = 5,
) -> dict:
    result = assemble_dialogue_memory_context(paths, query, limit=limit)
    report = {
        "schema_version": 1,
        "saved_at": datetime.now().isoformat(),
        "ok": True,
        "milestone": "M8.4",
        "recommendation": READY_RECOMMENDATION,
        "stop_reasons": [],
        "query": query,
        "counts": {
            "selected": len(result.retrieved),
            "filtered": len(result.filtered),
        },
        "retrieval": result.to_dict(),
        "provider_calls": 0,
        "boundaries": {
            "provider_generation_requested": False,
            "wake_cycle_run": False,
            "wake_events_written": False,
            "scheduler_mutated": False,
            "raw_provider_payload_stored": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
        },
    }
    return report


def write_m8_memory_retrieval_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m8_memory_retrieval_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _is_prompt_eligible(memory: dict) -> bool:
    if memory.get("status", "active") != "active":
        return False
    if any(key in memory for key in ("memory_type", "source_type", "authority", "prompt_eligible")):
        return (
            memory.get("prompt_eligible") is True
            and memory.get("memory_type") in PROMPT_MEMORY_TYPES
            and memory.get("authority") in PROMPT_AUTHORITIES
        )
    return (
        memory.get("accepted_for_context") is True
        and str(memory.get("source", "")).lower() in LEGACY_PROMPT_SOURCES
    )


def _score_memory(memory: dict, query: str, *, status_requested: bool) -> tuple[int, list[str]]:
    content = str(memory.get("content") or "")
    score = 1
    reasons = ["prompt_eligible_accepted_memory"]
    matched_terms = _matched_terms(content, query)
    if matched_terms:
        score += 3 + len(matched_terms)
        reasons.append("matched_query_terms:" + ",".join(matched_terms[:5]))
    if STYLE_MEMORY_RE.search(content):
        score += 2
        reasons.append("style_or_preference_memory")
    if status_requested and _is_project_state_memory(content):
        score += 2
        reasons.append("status_query_allows_project_state")
    if memory.get("memory_decision_id"):
        score += 1
        reasons.append("m8_policy_accepted")
    return score, reasons


def _matched_terms(content: str, query: str) -> list[str]:
    lowered_content = content.lower()
    terms = []
    for term in re.findall(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]{2,}", query.lower()):
        if len(term) < 3 and not re.fullmatch(r"m[0-9]+(?:\.[0-9]+)?", term):
            continue
        if term and term in lowered_content:
            terms.append(term)
    return terms


def _is_status_query(query: str) -> bool:
    return bool(STATUS_QUERY_RE.search(query or ""))


def _is_project_state_memory(content: str) -> bool:
    return bool(PROJECT_STATE_RE.search(content or ""))
