"""M14.4 Feishu media final freeze gate (read-only).

Verifies M14.1-M14.3 evidence plus the M13.5 chat freeze, and that media
boundaries held across the observed ledger. A passing freeze recommends
``m14_feishu_media_frozen``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import load_m10_freeze_evidence

READY_RECOMMENDATION = "m14_feishu_media_frozen"
EXPECTED_SOURCE_REPORTS = (
    ("m14_feishu_media_dry_run_report.json", "M14.1", "m14_feishu_media_dry_run_ready"),
    ("m14_feishu_media_trial_report.json", "M14.2", "m14_feishu_media_trial_ready"),
    ("m14_feishu_media_observation_report.json", "M14.3", "m14_feishu_media_observation_ready"),
    ("m13_feishu_freeze_report.json", "M13.5", "m13_feishu_chat_frozen"),
)


@dataclass
class M14FeishuMediaFreezeResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m14_feishu_media_freeze(
    paths: CompanionPaths,
    *,
    now: datetime | None = None,
) -> M14FeishuMediaFreezeResult:
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
    stages.append(_stage(
        "upstream_freezes_intact",
        upstream.get("ok") is True,
        "M7/M8/M9 freezes are still intact" if upstream.get("ok") else "upstream freeze evidence broken",
    ))

    observation = reports.get("M14.3") or {}
    obs_payload = observation.get("observation") if isinstance(observation.get("observation"), dict) else {}
    problems = []
    if obs_payload.get("voice_errors") or obs_payload.get("image_errors"):
        problems.append("observed media failures must be resolved before freeze")
    dry_run = reports.get("M14.1") or {}
    boundaries = dry_run.get("boundaries") if isinstance(dry_run.get("boundaries"), dict) else {}
    if boundaries.get("text_reply_never_blocked_by_media") is not True:
        problems.append("M14.1 did not prove text-reply priority")
    if boundaries.get("attachments_outside_creations") is not False:
        problems.append("M14.1 did not prove creations scoping")
    stages.append(_stage(
        "media_boundaries_preserved",
        not problems,
        "media boundaries held across dry-run and observation evidence" if not problems else "; ".join(problems),
    ))
    stages.append(_stage(
        "freeze_runtime_boundary",
        True,
        "freeze is read-only: no synthesis, uploads, sends, or service mutation",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M14.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {"name": "M14 feishu media final freeze", "channel": "feishu", "readonly": True},
        "source_reports": source_reports,
        "upstream_freeze_evidence": upstream,
        "evidence": {
            "media_events_observed": obs_payload.get("media_events"),
            "voice_sent_observed": obs_payload.get("voice_sent"),
            "images_sent_observed": obs_payload.get("images_sent"),
        },
        "final_freeze": {
            "frozen": ok,
            "readonly": True,
            "feishu_media_ready": ok,
            "media_reversible": True,
        },
        "boundaries": {
            "media_sent_by_freeze": False,
            "wake_cycle_run_by_freeze": False,
            "scheduler_mutated_by_freeze": False,
            "provider_generation_requested_by_freeze": False,
            "raw_media_bytes_in_reports": False,
            "inbound_media_understanding": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "disable_media": "set voice_replies=off and image_attachments_enabled=false in life-loop/feishu_chat_config.json",
        },
    }
    return M14FeishuMediaFreezeResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m14_feishu_media_freeze_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m14_feishu_media_freeze_report.json"
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
            "saved_at": report.get("saved_at"),
        })
    return snapshot


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
