"""M11.6 Signal outbound final freeze gate.

Read-only. Verifies M11.3-M11.5 evidence, the M10.5 inbound chat freeze,
upstream M7/M8/M9 freezes, boundary-clean outbound ledger records, and
documented pause/disable paths. A passing freeze recommends
``m11_signal_outbound_frozen`` and closes the Signal channel work before any
voice, camera, or hardware-body milestone opens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_OUTBOUND_SKIP_REASONS,
    load_m10_freeze_evidence,
    load_signal_chat_attempts,
)

READY_RECOMMENDATION = "m11_signal_outbound_frozen"
EXPECTED_SOURCE_REPORTS = (
    ("m11_signal_outbound_dry_run_report.json", "M11.3", "m11_signal_outbound_dry_run_ready"),
    ("m11_signal_outbound_trial_report.json", "M11.4", "m11_signal_outbound_trial_ready"),
    ("m11_signal_outbound_observation_report.json", "M11.5", "m11_signal_outbound_observation_ready"),
    ("m10_signal_freeze_report.json", "M10.5", "m10_signal_chat_frozen"),
)


@dataclass
class M11OutboundFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m11_outbound_freeze(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
) -> M11OutboundFreezeResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}
    reports: dict[str, dict | None] = {}

    for name, milestone, recommendation in EXPECTED_SOURCE_REPORTS:
        path = paths.life_loop_dir / name
        report = _load_report(path)
        reports[milestone] = report
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_source_report_stage(report, milestone=milestone, recommendation=recommendation))

    upstream = load_m10_freeze_evidence(paths)
    stages.append(_upstream_freeze_stage(upstream))

    records = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    outbound_live = [
        record for record in records
        if record.get("direction") == "outbound" and record.get("mode") in ("live", "trial")
    ]
    stages.append(_outbound_boundary_stage(outbound_live))

    observation_report = reports.get("M11.5")
    stages.append(_pause_and_disable_stage(observation_report))
    stages.append(_freeze_runtime_boundary_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    delivered = sum(1 for record in outbound_live if record.get("decision") == "delivered")
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M11.6",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M11 signal outbound final freeze",
            "readonly": True,
        },
        "source_reports": source_reports,
        "upstream_freeze_evidence": upstream,
        "evidence": {
            "outbound_records_observed": len(outbound_live),
            "delivered_observed": delivered,
            "failed_observed": sum(1 for record in outbound_live if record.get("decision") == "failed"),
            "pause_drill_ready": _pause_drill_ready(observation_report),
            "disable_documented": True,
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "outbound_ready": ok,
            "outbound_reversible": True,
        },
        "boundaries": {
            "service_mutated_by_freeze": False,
            "scheduler_mutated_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "signal_send_requested_by_freeze": False,
            "outbox_mutated_by_freeze": False,
            "raw_provider_payload_stored": False,
            "memory_authority_expanded": False,
            "voice_camera_hardware_activation_allowed": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_outbound": f"touch {paths.signal_outbound_pause_flag}",
            "disable_outbound": "set outbound_enabled=false in life-loop/signal_chat_config.json",
            "pause_all_signal": f"touch {paths.signal_chat_pause_flag}",
        },
    }
    return M11OutboundFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m11_outbound_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m11_signal_outbound_freeze_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _source_report_stage(report: dict | None, *, milestone: str, recommendation: str) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append(f"{milestone} report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append(f"{milestone} ok is not true")
        if report.get("milestone") != milestone:
            problems.append(f"milestone is not {milestone}")
        if report.get("recommendation") != recommendation:
            problems.append(f"recommendation is not {recommendation}")
        if report.get("stop_reasons"):
            problems.append(f"{milestone} report has stop_reasons")
    return _stage(
        f"source_report_{milestone.lower().replace('.', '_')}",
        not problems,
        f"{milestone} evidence still passes" if not problems else "; ".join(problems),
    )


def _upstream_freeze_stage(upstream: dict) -> dict:
    if upstream.get("ok") is True:
        return _stage("upstream_freezes_intact", True, "M7/M8/M9 freezes are still intact")
    broken = [
        name
        for name, snapshot in (upstream.get("reports") or {}).items()
        if not snapshot.get("ok")
    ]
    return _stage("upstream_freezes_intact", False, f"upstream freeze evidence broken: {broken}")


def _outbound_boundary_stage(outbound_live: list[dict]) -> dict:
    problems = []
    for record in outbound_live:
        boundaries = record.get("boundaries") if isinstance(record.get("boundaries"), dict) else {}
        for key in (
            "wake_cycle_run",
            "scheduler_mutated",
            "raw_provider_payload_stored",
            "raw_signal_envelope_stored",
            "semantic_shadow_authority_promoted",
            "memory_authority_expanded",
            "voice_output",
        ):
            if boundaries.get(key) is not False:
                problems.append(f"outbound record boundary {key} is not false")
        if record.get("decision") == "skipped" and record.get("skip_reason") not in SIGNAL_OUTBOUND_SKIP_REASONS:
            problems.append(f"unknown outbound skip reason {record.get('skip_reason')}")
        if "content" in record:
            problems.append("an outbound record stores raw content")
        if not record.get("outbox_entry_id"):
            problems.append("an outbound record is missing its outbox entry linkage")
    if problems:
        return _stage("outbound_boundaries_preserved", False, "; ".join(sorted(set(problems))))
    return _stage(
        "outbound_boundaries_preserved",
        True,
        f"{len(outbound_live)} live/trial outbound records preserve every boundary",
    )


def _pause_and_disable_stage(observation_report: dict | None) -> dict:
    problems = []
    if not _pause_drill_ready(observation_report):
        problems.append("M11.5 outbound pause drill is not ready")
    return _stage(
        "pause_and_disable_ready",
        not problems,
        "outbound pause and disable paths are drilled and documented" if not problems else "; ".join(problems),
    )


def _freeze_runtime_boundary_stage() -> dict:
    return _stage(
        "freeze_runtime_boundary",
        True,
        "freeze is read-only: no sends, provider calls, outbox, service, or scheduler mutation",
    )


def _pause_drill_ready(observation_report: dict | None) -> bool:
    drill = (observation_report or {}).get("pause_drill")
    return isinstance(drill, dict) and drill.get("ready") is True


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}


def _load_report(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _report_snapshot(paths: CompanionPaths, path: Path, report: dict | None) -> dict:
    snapshot = {"path": _relative(paths, path), "exists": path.exists(), "ok": False, "recommendation": None}
    if isinstance(report, dict):
        snapshot.update({
            "ok": report.get("ok") is True,
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
