"""Shared evidence builders for M10 gate tests."""

from __future__ import annotations

import json
from datetime import datetime

from companion_core import CompanionPaths
from companion_core.signal_chat import SIGNAL_CHAT_BOUNDARIES

ACCOUNT = "+15550000000"
ALLOWED = "+15550001111"
OTHER = "+15550002222"


def make_home(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def write_runner_stub(paths: CompanionPaths) -> None:
    """Minimal chat runner stub carrying the tokens contract checks look for."""

    script_dir = paths.home / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "run_m10_signal_chat.py").write_text(
        "# test stub for contract checks\n"
        "# --confirm-real-signal-send\n"
        "# run_loop\n"
    )


def write_config(paths: CompanionPaths, **overrides) -> None:
    payload = {
        "account": ACCOUNT,
        "allowed_senders": [ALLOWED],
        "daily_reply_budget": 50,
        "max_replies_per_poll": 3,
        "max_inbound_length": 4000,
        "respect_quiet_hours": False,
    }
    payload.update(overrides)
    paths.signal_chat_config_file.write_text(json.dumps(payload))


def write_upstream_freezes(paths: CompanionPaths) -> None:
    for name, milestone, recommendation in (
        ("m7_dialogue_freeze_report.json", "M7.6", "m7_text_dialogue_frozen"),
        ("m8_memory_freeze_report.json", "M8.7", "m8_memory_dialogue_frozen"),
        ("m9_presence_freeze_report.json", "M9.5", "m9_controlled_presence_frozen"),
    ):
        (paths.life_loop_dir / name).write_text(json.dumps({
            "ok": True,
            "milestone": milestone,
            "recommendation": recommendation,
            "stop_reasons": [],
        }))


def write_dry_run_report(paths: CompanionPaths, *, ok: bool = True) -> None:
    (paths.life_loop_dir / "m10_signal_dry_run_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M10.1",
        "recommendation": "m10_signal_dry_run_ready" if ok else "inspect",
        "saved_at": "2026-07-20T15:00:00",
        "stop_reasons": [] if ok else ["policy_scenario_coverage"],
        "stages": [{"name": "real_mode_guard", "status": "pass" if ok else "fail"}],
    }))


def write_trial_report(paths: CompanionPaths, *, ok: bool = True) -> None:
    (paths.life_loop_dir / "m10_signal_trial_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M10.2",
        "recommendation": "m10_signal_trial_ready" if ok else "inspect",
        "saved_at": "2026-07-20T15:10:00",
        "stop_reasons": [] if ok else ["trial_execution"],
    }))


def write_activation_report(paths: CompanionPaths, *, ok: bool = True, enabled: bool = True) -> None:
    (paths.life_loop_dir / "m10_signal_activation_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M10.3",
        "recommendation": "m10_signal_activation_ready" if ok else "inspect",
        "saved_at": "2026-07-20T15:20:00",
        "stop_reasons": [] if ok else ["service_enablement"],
        "service": {
            "mechanism": "systemd-user",
            "unit_name": "companion-signal-chat.service",
            "enabled": enabled,
            "artifact_count": 1 if enabled else 0,
            "rollback_command": ".venv/bin/python scripts/run_m10_signal_activation.py --disable",
            "pause_flag_path": "life-loop/signal_chat_pause.flag",
        },
    }))


def write_observation_report(paths: CompanionPaths, *, ok: bool = True, pause_ready: bool = True) -> None:
    (paths.life_loop_dir / "m10_signal_observation_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M10.4",
        "recommendation": "m10_signal_observation_ready" if ok else "inspect",
        "saved_at": "2026-07-20T15:30:00",
        "stop_reasons": [] if ok else ["decision_health"],
        "pause_drill": {"performed": True, "ready": pause_ready},
        "observation": {
            "observed_attempts": 3,
            "decision_counts": {"replied": 2, "skipped": 1},
        },
    }))


def make_outbound_record(
    *,
    decision: str = "delivered",
    recipient: str = ALLOWED,
    entry_id: str = "outbox_test_1",
    source_event_id: str = "wake_test_1",
    created_at: str | None = None,
    skip_reason: str | None = None,
    mode: str = "live",
    error: dict | None = None,
    **extra,
) -> dict:
    record = {
        "id": f"sigout_test_{entry_id}_{decision}",
        "created_at": created_at or datetime(2026, 7, 20, 12, 0, 0).isoformat(),
        "direction": "outbound",
        "mode": mode,
        "transport": "signal-cli" if mode == "live" else "fake",
        "outbox_entry_id": entry_id,
        "source_event_id": source_event_id,
        "trigger": "scheduled-wake",
        "recipient": recipient,
        "content_hash": "sha256:" + "2" * 64,
        "content_length": 20,
        "decision": decision,
        "skip_reason": skip_reason,
        "send_attempts": 1 if decision == "delivered" else 2,
        "boundaries": dict(SIGNAL_CHAT_BOUNDARIES),
        "error": error,
    }
    record.update(extra)
    return record


def write_m11_dry_run_report(paths: CompanionPaths, *, ok: bool = True) -> None:
    (paths.life_loop_dir / "m11_signal_outbound_dry_run_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M11.3",
        "recommendation": "m11_signal_outbound_dry_run_ready" if ok else "inspect",
        "saved_at": "2026-07-20T16:00:00",
        "stop_reasons": [] if ok else ["outbound_scenario_coverage"],
    }))


def write_m11_trial_report(paths: CompanionPaths, *, ok: bool = True) -> None:
    (paths.life_loop_dir / "m11_signal_outbound_trial_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M11.4",
        "recommendation": "m11_signal_outbound_trial_ready" if ok else "inspect",
        "saved_at": "2026-07-20T16:10:00",
        "stop_reasons": [] if ok else ["trial_execution"],
    }))


def write_m11_observation_report(paths: CompanionPaths, *, ok: bool = True, pause_ready: bool = True) -> None:
    (paths.life_loop_dir / "m11_signal_outbound_observation_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M11.5",
        "recommendation": "m11_signal_outbound_observation_ready" if ok else "inspect",
        "saved_at": "2026-07-20T16:20:00",
        "stop_reasons": [] if ok else ["delivery_health"],
        "pause_drill": {"performed": True, "ready": pause_ready},
        "observation": {
            "observed_records": 2,
            "decision_counts": {"delivered": 1, "skipped": 1},
        },
    }))


def write_m10_freeze_report(paths: CompanionPaths, *, ok: bool = True) -> None:
    (paths.life_loop_dir / "m10_signal_freeze_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M10.5",
        "recommendation": "m10_signal_chat_frozen" if ok else "inspect",
        "saved_at": "2026-07-20T15:40:00",
        "stop_reasons": [] if ok else ["chat_boundaries_preserved"],
    }))


def make_attempt(
    *,
    decision: str = "replied",
    sender: str = ALLOWED,
    timestamp: int = 1000,
    created_at: str | None = None,
    skip_reason: str | None = None,
    mode: str = "live",
    error: dict | None = None,
    **extra,
) -> dict:
    record = {
        "id": f"sigchat_test_{timestamp}_{decision}",
        "created_at": created_at or datetime(2026, 7, 20, 12, 0, 0).isoformat(),
        "direction": "inbound",
        "mode": mode,
        "transport": "signal-cli" if mode == "live" else "fake",
        "provider": "deepseek" if mode == "live" else "fake",
        "memory_mode": "json",
        "sender": sender,
        "message_timestamp": timestamp,
        "body_hash": "sha256:" + "0" * 64,
        "body_length": 12,
        "has_attachment": False,
        "is_group": False,
        "decision": decision,
        "skip_reason": skip_reason,
        "conversation_id": f"signal_{sender.lstrip('+')}" if decision == "replied" else None,
        "dialogue_event_id": f"dialogue_test_{timestamp}" if decision == "replied" else None,
        "reply_hash": ("sha256:" + "1" * 64) if decision == "replied" else None,
        "reply_length": 24 if decision == "replied" else 0,
        "memory_proposal_count": 0,
        "boundaries": dict(SIGNAL_CHAT_BOUNDARIES),
        "error": error,
    }
    record.update(extra)
    return record
