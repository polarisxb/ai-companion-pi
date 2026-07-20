"""M14.3 Feishu media observation gate (read-only).

Analyzes media outcomes on live/trial feishu reply records: media health
(no unexplained failures), creations-scoped image paths, positive voice
durations, and byte-free ledger payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import load_signal_chat_attempts

READY_RECOMMENDATION = "m14_feishu_media_observation_ready"
OBSERVED_MODES = ("live", "trial")


@dataclass
class M14FeishuMediaObservationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m14_feishu_media_observation(
    paths: CompanionPaths,
    *,
    min_media_events: int = 1,
    now: datetime | None = None,
) -> M14FeishuMediaObservationResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    trial_path = paths.life_loop_dir / "m14_feishu_media_trial_report.json"
    trial_report = _load_report(trial_path)
    source_reports["m14_feishu_media_trial"] = _report_snapshot(paths, trial_path, trial_report)
    trial_ok = (
        isinstance(trial_report, dict)
        and trial_report.get("ok") is True
        and trial_report.get("milestone") == "M14.2"
        and trial_report.get("recommendation") == "m14_feishu_media_trial_ready"
    )
    stages.append(_stage(
        "m14_trial_ready",
        trial_ok,
        "M14.2 media trial evidence is ready" if trial_ok else "M14.2 media trial evidence is missing or failing",
    ))

    attempts = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    media_events = [
        attempt for attempt in attempts
        if attempt.get("channel") == "feishu"
        and attempt.get("mode") in OBSERVED_MODES
        and attempt.get("direction", "inbound") == "inbound"
        and isinstance(attempt.get("media"), dict)
    ]
    stages.append(_stage(
        "media_volume",
        len(media_events) >= min_media_events,
        f"observed {len(media_events)} media reply event(s) (need {min_media_events})",
    ))

    summary = {"voice_sent": 0, "voice_errors": 0, "images_sent": 0, "image_errors": 0, "rejections": {}}
    problems: list[str] = []
    for attempt in media_events:
        media = attempt["media"]
        voice = media.get("voice") or {}
        if voice.get("sent"):
            summary["voice_sent"] += 1
            if not isinstance(voice.get("duration_ms"), int) or voice["duration_ms"] <= 0:
                problems.append("a sent voice bubble has no positive duration")
        if voice.get("error"):
            summary["voice_errors"] += 1
        images = media.get("images") or {}
        summary["images_sent"] += int(images.get("sent") or 0)
        summary["image_errors"] += len(images.get("errors") or [])
        for rejected in images.get("rejected") or []:
            reason = str(rejected.get("reason"))
            summary["rejections"][reason] = summary["rejections"].get(reason, 0) + 1
        for sent_path in images.get("sent_paths") or []:
            if not str(sent_path).startswith("creations/"):
                problems.append(f"sent image path escaped creations/: {sent_path}")
        if attempt.get("decision") != "replied":
            problems.append("a media payload appeared on a non-replied attempt")

    stages.append(_stage(
        "media_health",
        summary["voice_errors"] == 0 and summary["image_errors"] == 0,
        "no media delivery failures in the observation window"
        if summary["voice_errors"] == 0 and summary["image_errors"] == 0
        else f"media failures observed: voice={summary['voice_errors']} image={summary['image_errors']}",
    ))
    stages.append(_stage(
        "media_discipline",
        not problems,
        "media payloads are replied-only, creations-scoped, with valid durations" if not problems else "; ".join(sorted(set(problems))),
    ))

    dumped = json.dumps(media_events, ensure_ascii=False)
    byte_free = "OggS" not in dumped and "RIFF" not in dumped
    stages.append(_stage(
        "media_ledger_hygiene",
        byte_free,
        "media payloads carry outcomes only, never audio or image bytes" if byte_free else "raw media bytes found in the ledger",
    ))
    stages.append(_stage(
        "observation_runtime_boundary",
        True,
        "observation reads the ledger only; no synthesis, uploads, or sends",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M14.3",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M14 feishu media observation",
            "channel": "feishu",
            "readonly": True,
            "min_media_events": min_media_events,
        },
        "source_reports": source_reports,
        "observation": {"media_events": len(media_events), **summary},
        "boundaries": {
            "provider_generation_requested": False,
            "provider_calls": 0,
            "media_sent_by_observation": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M14FeishuMediaObservationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m14_feishu_media_observation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m14_feishu_media_observation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


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
