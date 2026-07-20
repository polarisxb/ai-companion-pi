"""M14.2 supervised real Feishu media trial gate.

Sends one real voice bubble (synthesized on this machine) and optionally one
real creations image to the configured recipient, behind the explicit
confirmation flag. Requires M14.1 and M13.2 evidence plus upstream freezes.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .chat_media import (
    IMAGE_EXTENSIONS,
    CREATIONS_DIR_NAME,
    _safe_filename,
    _snapshot_regular_file,
)
from .paths import CompanionPaths
from .signal_chat import SignalChatConfigError, load_feishu_chat_config, load_m10_freeze_evidence
from .tts import TTSError, create_tts_backend

READY_RECOMMENDATION = "m14_feishu_media_trial_ready"
TRIAL_VOICE_TEXT = "这是一条来自树莓派的试验语音,听到就说明我的声音通了。"
REQUIRED_SOURCE_REPORTS = (
    ("m14_feishu_media_dry_run_report.json", "M14.1", "m14_feishu_media_dry_run_ready"),
    ("m13_feishu_trial_report.json", "M13.2", "m13_feishu_trial_ready"),
)


@dataclass
class M14FeishuMediaTrialResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m14_feishu_media_trial(
    paths: CompanionPaths,
    *,
    transport,
    confirm_real_feishu_send: bool = False,
    image_path: str | None = None,
    tts_backend=None,
    now: datetime | None = None,
) -> M14FeishuMediaTrialResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    for name, milestone, recommendation in REQUIRED_SOURCE_REPORTS:
        path = paths.life_loop_dir / name
        report = _load_report(path)
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_source_report_stage(report, milestone=milestone, recommendation=recommendation))

    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_stage(
        "upstream_freeze_evidence",
        freeze_evidence.get("ok") is True,
        "M7/M8/M9 freeze evidence passes" if freeze_evidence.get("ok") else "upstream freeze evidence not ready",
    ))

    config = None
    recipient = None
    try:
        config = load_feishu_chat_config(paths)
        recipient = config.resolved_outbound_recipient() or (config.allowed_senders[0] if config.allowed_senders else None)
        stages.append(_stage(
            "config_ready",
            recipient is not None,
            f"trial recipient resolved: {recipient}" if recipient else "no resolvable trial recipient",
        ))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    stages.append(_stage(
        "operator_confirmation",
        confirm_real_feishu_send,
        "operator explicitly confirmed real feishu traffic"
        if confirm_real_feishu_send
        else "trial requires --confirm-real-feishu-send",
    ))

    transport_error = None
    check_available = getattr(transport, "check_available", None)
    if callable(check_available):
        try:
            check_available()
        except Exception as exc:  # noqa: BLE001 - availability failures become stage evidence.
            transport_error = f"{type(exc).__name__}: {exc}"
    stages.append(_stage(
        "transport_ready",
        transport_error is None and getattr(transport, "supports_media", False),
        "media-capable transport is ready" if transport_error is None else transport_error,
    ))

    voice_evidence: dict = {"attempted": False, "sent": False}
    image_evidence: dict = {"attempted": False, "sent": False}
    if _all_pass(stages):
        backend = tts_backend or _safe_backend(config)
        if backend is not None:
            voice_evidence["attempted"] = True
            try:
                with tempfile.TemporaryDirectory(prefix="m14-trial-voice-") as voice_dir:
                    synthesized = backend.synthesize_opus(TRIAL_VOICE_TEXT, Path(voice_dir))
                    transport.send_voice(recipient, synthesized.opus_path, synthesized.duration_ms)
                voice_evidence["sent"] = True
                voice_evidence["duration_ms"] = synthesized.duration_ms
            except Exception as exc:  # noqa: BLE001 - trial failures become stage evidence.
                voice_evidence["error"] = f"{type(exc).__name__}: {exc}"

        if image_path:
            image_evidence["attempted"] = True
            resolved = (paths.home / image_path).resolve()
            creations_root = (paths.home / CREATIONS_DIR_NAME).resolve()
            if creations_root != resolved and creations_root not in resolved.parents:
                image_evidence["error"] = "trial image must live under creations/"
            elif resolved.suffix.lower() not in IMAGE_EXTENSIONS or not resolved.is_file():
                image_evidence["error"] = "trial image is missing or not an image"
            else:
                snapshot_reason, data = _snapshot_regular_file(resolved, 10 * 1024 * 1024)
                if snapshot_reason is not None:
                    image_evidence["error"] = f"trial image rejected: {snapshot_reason}"
                else:
                    try:
                        transport.send_image(recipient, _safe_filename(resolved.name), data)
                        image_evidence["sent"] = True
                        image_evidence["path"] = image_path
                    except Exception as exc:  # noqa: BLE001 - trial failures become stage evidence.
                        image_evidence["error"] = f"{type(exc).__name__}: {exc}"

        problems = []
        if not voice_evidence["attempted"] and not image_evidence["attempted"]:
            problems.append("nothing to trial: configure tts_command and/or pass --image")
        if voice_evidence["attempted"] and not voice_evidence["sent"]:
            problems.append(f"voice trial failed: {voice_evidence.get('error')}")
        if image_evidence["attempted"] and not image_evidence["sent"]:
            problems.append(f"image trial failed: {image_evidence.get('error')}")
        stages.append(_stage(
            "trial_execution",
            not problems,
            "supervised media delivery succeeded" if not problems else "; ".join(problems),
        ))
    else:
        stages.append(_stage("trial_execution", False, "trial execution skipped because preflight failed"))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M14.2",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M14 supervised feishu media trial",
            "channel": "feishu",
            "transport": getattr(transport, "name", type(transport).__name__),
            "confirm_real_feishu_send": confirm_real_feishu_send,
            "provider_calls": 0,
        },
        "source_reports": source_reports,
        "trial": {"voice": voice_evidence, "image": image_evidence},
        "boundaries": {
            "text_chat_unaffected": True,
            "raw_media_bytes_in_report": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
            "provider_generation_requested": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M14FeishuMediaTrialResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m14_feishu_media_trial_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m14_feishu_media_trial_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _safe_backend(config):
    if config is None:
        return None
    try:
        return create_tts_backend(config)
    except TTSError:
        return None


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
    return _stage(
        f"source_report_{milestone.lower().replace('.', '_')}",
        not problems,
        f"{milestone} evidence is ready" if not problems else "; ".join(problems),
    )


def _all_pass(stages: list[dict]) -> bool:
    return all(stage.get("status") == "pass" for stage in stages)


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
