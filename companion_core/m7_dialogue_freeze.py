"""Read-only M7.6 dialogue hardening freeze gate."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DIALOGUE_BOUNDARIES, SECRET_LIKE_RE
from .dialogue_replay import check_dialogue_transcript
from .m7_memory_gate import run_m7_memory_proposal_gate
from .paths import CompanionPaths

READY_RECOMMENDATION = "m7_text_dialogue_frozen"
M6_READY_RECOMMENDATION = "m6_frozen_ready_for_scheduler_handoff"
M7_DIALOGUE_READY_RECOMMENDATION = "m7_cli_dialogue_ready"
M7_MEMORY_READY_RECOMMENDATION = "m7_memory_proposals_ready"
M7_REPLAY_READY_RECOMMENDATION = "m7_dialogue_transcript_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]

RAW_PAYLOAD_KEYS = {
    "raw_output",
    "raw_provider_payload",
    "provider_payload",
    "raw_response",
    "provider_response",
    "request_payload",
    "response_payload",
}
SCHEDULER_MUTATION_RE = re.compile(
    r"\b(crontab\s+-|systemctl\s+(?:enable|start|restart|stop|disable)|"
    r"timer_installation\s*[:=]\s*true|scheduler_mutat(?:ed|ion_allowed|ion_attempted)\s*[:=]\s*true)\b",
    re.IGNORECASE,
)


@dataclass
class M7DialogueFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m7_dialogue_freeze_check(paths: CompanionPaths) -> M7DialogueFreezeResult:
    """Freeze M7 text dialogue by inspecting existing evidence only.

    The gate is read-only with respect to runtime behavior: it does not run wake
    cycles, does not create an LLM/provider client, does not touch scheduler
    state, does not write to `/life`, and does not promote semantic-shadow or
    proposed memory authority. The CLI wrapper is the only layer that writes the
    final M7.6 report.
    """

    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    m6_report = _load_report(paths.life_loop_dir / "m6_final_freeze_report.json")
    source_reports["m6_final_freeze"] = _report_snapshot(paths, paths.life_loop_dir / "m6_final_freeze_report.json", m6_report)
    stages.append(_m6_stage(m6_report))

    dialogue_report = _load_report(paths.life_loop_dir / "m7_text_dialogue_report.json")
    source_reports["m7_text_dialogue"] = _report_snapshot(paths, paths.life_loop_dir / "m7_text_dialogue_report.json", dialogue_report)
    stages.append(_dialogue_report_stage(dialogue_report))

    transcript_stages, replay_summaries = _transcript_replay_stages(paths)
    stages.extend(transcript_stages)

    memory_result = run_m7_memory_proposal_gate(paths)
    memory_report = memory_result.to_dict()
    source_reports["m7_memory_proposal_current"] = _report_snapshot_from_payload(memory_report)
    stages.append(_memory_gate_stage(memory_report))

    dashboard_stage = _dashboard_chat_stage(paths)
    stages.append(dashboard_stage)

    evidence_scan_stage = _secret_and_payload_scan_stage(paths)
    stages.append(evidence_scan_stage)

    stages.extend([
        _dialogue_boundaries_stage(paths, dialogue_report, memory_report),
        _scheduler_static_boundary_stage(paths),
        _readonly_profile_stage(),
    ])

    stop_reasons = _stop_reasons(stages)
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": datetime.now().isoformat(),
        "ok": ok,
        "milestone": "M7.6",
        "recommendation": recommendation,
        "stop_reasons": stop_reasons,
        "profile": _readonly_profile(),
        "source_reports": source_reports,
        "replay_checks": replay_summaries,
        "evidence": {
            "m7_1_cli_dialogue_ready": _stage_ok(stages, "m7_text_dialogue_report"),
            "m7_2_interactive_cli_implemented": _file_contains(REPO_ROOT / "scripts" / "chat_with_companion.py", "--interactive"),
            "m7_3_replay_checks_pass": all(stage.get("status") == "pass" for stage in transcript_stages),
            "m7_4_memory_gate_ready": _stage_ok(stages, "m7_memory_proposal_gate_current"),
            "m7_5_dashboard_chat_implemented": dashboard_stage.get("status") == "pass",
            "m6_7_final_freeze_still_ready": _stage_ok(stages, "m6_final_freeze"),
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "next_stage": "M8" if ok else "M7.6",
            "voice_signal_scheduler_handoff_allowed_after_freeze": ok,
        },
        "boundaries": dict(DIALOGUE_BOUNDARIES),
        "stages": stages,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m7_dialogue_freeze": "python3 scripts/run_m7_dialogue_freeze.py --companion-home " + str(paths.home),
            "m8_handoff": "requires m7_text_dialogue_frozen",
        },
    }
    return M7DialogueFreezeResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m7_dialogue_freeze_report(paths: CompanionPaths, report: dict, report_file: str | Path | None = None) -> Path:
    report_path = Path(report_file).expanduser() if report_file else paths.life_loop_dir / "m7_dialogue_freeze_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _m6_stage(report: dict | None) -> dict:
    ok = bool(
        isinstance(report, dict)
        and report.get("ok") is True
        and report.get("recommendation") == M6_READY_RECOMMENDATION
    )
    return _stage("m6_final_freeze", ok, "M6.7 final freeze remains ready" if ok else "M6.7 final freeze evidence is not ready")


def _dialogue_report_stage(report: dict | None) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append("M7 text dialogue report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append("M7 text dialogue report ok is not true")
        if report.get("recommendation") != M7_DIALOGUE_READY_RECOMMENDATION:
            problems.append("M7 text dialogue report recommendation is not ready")
        if report.get("raw_provider_payload_stored") is not False:
            problems.append("M7 text dialogue report permits raw provider payload storage")
        if report.get("boundaries") != DIALOGUE_BOUNDARIES:
            problems.append("M7 text dialogue boundaries changed")
        m6 = report.get("m6_final_freeze") if isinstance(report.get("m6_final_freeze"), dict) else {}
        if m6.get("ok") is not True or m6.get("recommendation") != M6_READY_RECOMMENDATION:
            problems.append("M7 text dialogue report does not carry ready M6.7 evidence")
    return _stage("m7_text_dialogue_report", not problems, "M7.1 dialogue evidence is ready" if not problems else "; ".join(problems))


def _transcript_replay_stages(paths: CompanionPaths) -> tuple[list[dict], list[dict]]:
    transcripts = sorted(paths.conversations_dir.glob("*.jsonl")) if paths.conversations_dir.exists() else []
    if not transcripts:
        return [_stage("m7_dialogue_replay", False, "no dialogue transcripts found for replay check")], []
    stages = []
    summaries = []
    for transcript in transcripts:
        result = check_dialogue_transcript(paths, transcript)
        payload = result.to_dict()
        summaries.append({
            "transcript": _relative_to_home(paths, transcript),
            "ok": result.ok,
            "recommendation": payload["recommendation"],
            "rows_checked": result.rows_checked,
            "events_checked": result.events_checked,
            "errors": result.errors,
            "provider_calls": payload.get("provider_calls", 0),
        })
        stages.append(_stage(
            "m7_dialogue_replay",
            result.ok and payload.get("recommendation") == M7_REPLAY_READY_RECOMMENDATION,
            f"{_relative_to_home(paths, transcript)} replay ready" if result.ok else f"{_relative_to_home(paths, transcript)} replay failed: {'; '.join(result.errors)}",
            details={"transcript": _relative_to_home(paths, transcript), "rows_checked": result.rows_checked, "events_checked": result.events_checked},
        ))
    return stages, summaries


def _memory_gate_stage(report: dict) -> dict:
    ok = bool(report.get("ok") is True and report.get("recommendation") == M7_MEMORY_READY_RECOMMENDATION)
    authority = report.get("prompt_authority_status") if isinstance(report.get("prompt_authority_status"), dict) else {}
    separation = report.get("separation") if isinstance(report.get("separation"), dict) else {}
    problems = []
    if not ok:
        problems.append("M7.4 memory proposal gate is not ready")
    if authority.get("proposal_prompt_authoritative_count", 0) != 0 or authority.get("proposal_authority_promoted") is not False:
        problems.append("memory proposals became prompt-authoritative")
    if separation.get("proposals_separate_from_accepted_memory") is not True:
        problems.append("memory proposals are not separate from accepted memory")
    if separation.get("acceptance_workflow_present") is not False:
        problems.append("proposal acceptance workflow unexpectedly present during freeze")
    if report.get("provider_calls") != 0:
        problems.append("memory gate reported provider calls")
    return _stage("m7_memory_proposal_gate_current", not problems, "M7.4 memory proposal gate remains ready" if not problems else "; ".join(problems))


def _dashboard_chat_stage(paths: CompanionPaths) -> dict:
    source = _read_text(REPO_ROOT / "window" / "window.py")
    required = [
        '@app.route("/chat")',
        '@app.route("/chat/send", methods=["POST"])',
        "DialogueRunner",
        "DEFAULT_CHAT_ERROR",
        "preserved_input",
    ]
    problems = [f"missing dashboard chat evidence: {needle}" for needle in required if needle not in source]
    if '@app.route("/life", methods=["POST"])' in source or "@app.post(\"/life\")" in source:
        problems.append("/life write route detected")
    return _stage("m7_dashboard_chat", not problems, "M7.5 dashboard chat route evidence is present" if not problems else "; ".join(problems))


def _secret_and_payload_scan_stage(paths: CompanionPaths) -> dict:
    files = []
    for directory in (paths.conversations_dir, paths.life_loop_dir):
        if not directory.exists():
            continue
        files.extend(path for path in directory.glob("*.json*") if path.is_file())
    problems = []
    for path in files:
        text = _read_text(path)
        if SECRET_LIKE_RE.search(text):
            problems.append(f"secret-like string found in {_relative_to_home(paths, path)}")
        for key in RAW_PAYLOAD_KEYS:
            if f'"{key}"' in text:
                problems.append(f"raw provider payload key {key} found in {_relative_to_home(paths, path)}")
    return _stage("m7_secret_and_payload_scan", not problems, "no secret-like strings or raw provider payload keys in dialogue evidence" if not problems else "; ".join(sorted(set(problems))))


def _dialogue_boundaries_stage(paths: CompanionPaths, dialogue_report: dict | None, memory_report: dict) -> dict:
    problems = []
    for label, report in (("m7_text_dialogue_report", dialogue_report), ("m7_memory_proposal_report", memory_report)):
        if isinstance(report, dict) and report.get("boundaries") != DIALOGUE_BOUNDARIES:
            problems.append(f"{label} boundaries changed")
    for event in _read_jsonl(paths.conversation_events_file):
        if event.get("trigger") != "human-text-chat":
            problems.append(f"dialogue event {event.get('id')} trigger is not human-text-chat")
        if event.get("boundaries") != DIALOGUE_BOUNDARIES:
            problems.append(f"dialogue event {event.get('id')} boundaries changed")
        if event.get("raw_output_stored") is not False:
            problems.append(f"dialogue event {event.get('id')} stores raw output")
    return _stage("m7_dialogue_boundaries", not problems, "dialogue events preserve no-wake/no-scheduler/no-raw-payload/no-semantic-authority boundaries" if not problems else "; ".join(problems))


def _scheduler_static_boundary_stage(paths: CompanionPaths) -> dict:
    files = [REPO_ROOT / "companion_core" / "dialogue.py", REPO_ROOT / "window" / "window.py", REPO_ROOT / "scripts" / "chat_with_companion.py"]
    problems = []
    for path in files:
        text = _read_text(path)
        if SCHEDULER_MUTATION_RE.search(text):
            problems.append(f"scheduler mutation pattern found in {_relative_to_home(paths, path)}")
    return _stage("m7_scheduler_static_boundary", not problems, "dialogue/chat sources contain no scheduler mutation commands" if not problems else "; ".join(problems))


def _readonly_profile_stage() -> dict:
    return _stage("m7_freeze_readonly_profile", True, "freeze gate performs read-only inspection; only CLI/report writer emits m7_dialogue_freeze_report.json", details=_readonly_profile())


def _readonly_profile() -> dict:
    return {
        "name": "M7.6 dialogue hardening freeze",
        "readonly_gate": True,
        "writes_report_only": True,
        "wake_cycle_run": False,
        "provider_generation_requested": False,
        "scheduler_mutation_allowed": False,
        "cron_replacement": False,
        "timer_installation": False,
        "service_mutation_allowed": False,
        "life_write_route_allowed": False,
        "semantic_shadow_authoritative": False,
        "raw_provider_payload_storage_allowed": False,
        "memory_proposal_acceptance_allowed": False,
    }


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


def _stage_ok(stages: list[dict], name: str) -> bool:
    matches = [stage for stage in stages if stage.get("name") == name]
    return bool(matches) and all(stage.get("status") == "pass" for stage in matches)


def _stop_reasons(stages: list[dict]) -> list[str]:
    return [stage["name"] for stage in stages if stage.get("status") != "pass"]


def _load_report(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    snapshot = {"path": _relative_to_home(paths, path), "exists": path.exists(), "ok": False, "recommendation": None}
    if isinstance(report, dict):
        snapshot.update({"ok": report.get("ok") is True, "recommendation": report.get("recommendation"), "saved_at": report.get("saved_at")})
    return snapshot


def _report_snapshot_from_payload(report: dict) -> dict:
    return {"ok": report.get("ok") is True, "recommendation": report.get("recommendation"), "saved_at": report.get("saved_at")}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text().splitlines()
    except (FileNotFoundError, OSError):
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def _file_contains(path: Path, needle: str) -> bool:
    return needle in _read_text(path)


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
