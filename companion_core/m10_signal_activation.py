"""M10.3 Signal chat listener activation gate.

Installs the Signal chat bridge as exactly one managed systemd user service
behind explicit operator action, mirroring how M9.3 managed exactly one cron
artifact. Enable and disable are both explicit, reversible, and recorded.
The default mechanism is a systemd user unit because it is native on
Raspberry Pi OS; the report records the mechanism so an operator choosing pm2
later has a documented seam.

Activation never runs a wake cycle, never calls a provider, never sends a
Signal message, and never touches cron or the M9 scheduler artifact.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .paths import CompanionPaths
from .signal_chat import (
    SignalChatConfigError,
    load_m10_freeze_evidence,
    load_signal_chat_config,
)

READY_RECOMMENDATION = "m10_signal_activation_ready"
DISABLED_RECOMMENDATION = "m10_signal_activation_disabled"
M10_DRY_RUN_RECOMMENDATION = "m10_signal_dry_run_ready"
M10_TRIAL_RECOMMENDATION = "m10_signal_trial_ready"
SERVICE_MECHANISM = "systemd-user"
UNIT_NAME = "companion-signal-chat.service"
UNIT_MARKER = "digital-life-m10-signal-chat-m10.3"

SystemctlRunner = Callable[[list[str]], None]


@dataclass
class M10SignalActivationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m10_signal_activation(
    paths: CompanionPaths,
    *,
    enable: bool = True,
    unit_dir: Path | None = None,
    systemctl_runner: SystemctlRunner | None = None,
    now: datetime | None = None,
) -> M10SignalActivationResult:
    """Enable or disable the single managed Signal chat service artifact."""

    current = now or datetime.now()
    resolved_unit_dir = unit_dir or default_unit_dir()
    runner = systemctl_runner or run_systemctl_user
    if not enable:
        return run_m10_signal_disable(
            paths,
            unit_dir=resolved_unit_dir,
            systemctl_runner=runner,
            now=current,
        )

    stages: list[dict] = []
    source_reports: dict[str, dict] = {}
    dry_run_path = paths.life_loop_dir / "m10_signal_dry_run_report.json"
    trial_path = paths.life_loop_dir / "m10_signal_trial_report.json"
    dry_run_report = _load_report(dry_run_path)
    trial_report = _load_report(trial_path)
    source_reports["m10_signal_dry_run"] = _report_snapshot(paths, dry_run_path, dry_run_report)
    source_reports["m10_signal_trial"] = _report_snapshot(paths, trial_path, trial_report)
    stages.append(_ready_report_stage(
        dry_run_report,
        name="m10_dry_run_ready",
        expected_milestone="M10.1",
        expected_recommendation=M10_DRY_RUN_RECOMMENDATION,
    ))
    stages.append(_ready_report_stage(
        trial_report,
        name="m10_signal_trial_ready",
        expected_milestone="M10.2",
        expected_recommendation=M10_TRIAL_RECOMMENDATION,
    ))

    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_freeze_stage(freeze_evidence))

    try:
        config = load_signal_chat_config(paths)
        stages.append(_stage(
            "config_ready",
            True,
            "signal chat config loaded",
            details={"allowed_sender_count": len(config.allowed_senders)},
        ))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    stages.append(_runner_contract_stage(paths))

    unit_path = resolved_unit_dir / UNIT_NAME
    unit_content = build_signal_chat_unit(paths)
    unit_changed = False
    unit_error = None
    existing_content = None
    if unit_path.exists():
        try:
            existing_content = unit_path.read_text()
        except OSError as exc:
            unit_error = f"could not read existing unit: {exc}"
    if unit_error is None and existing_content is not None and existing_content != unit_content:
        unit_error = f"existing {UNIT_NAME} does not match the managed M10.3 unit content"
    unit_changed = existing_content is None
    stages.append(_stage(
        "unit_plan",
        unit_error is None,
        "planned exactly one managed signal chat service unit" if unit_error is None else unit_error,
        details={
            "unit_path": str(unit_path),
            "unit_name": UNIT_NAME,
            "marker": UNIT_MARKER,
            "changed": unit_changed,
        },
    ))

    enabled = False
    if _all_pass(stages):
        try:
            if unit_changed:
                unit_path.parent.mkdir(parents=True, exist_ok=True)
                unit_path.write_text(unit_content)
            runner(["daemon-reload"])
            runner(["enable", "--now", UNIT_NAME])
            enabled = True
            stages.append(_stage(
                "service_enablement",
                True,
                "one managed signal chat service artifact is enabled",
                details={"unit_path": str(unit_path), "changed": unit_changed},
            ))
        except Exception as exc:  # noqa: BLE001 - enablement failures become stage evidence.
            stages.append(_stage(
                "service_enablement",
                False,
                f"could not enable service: {type(exc).__name__}: {exc}",
            ))
    else:
        stages.append(_stage(
            "service_enablement",
            False,
            "service enablement skipped because activation preflight failed",
        ))

    stages.append(_activation_boundary_stage())
    stages.append(_rollback_record_stage(paths))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M10.3",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M10 signal chat listener activation",
            "mechanism": SERVICE_MECHANISM,
            "service_mutation_allowed": True,
            "writes_exactly_one_service_artifact": True,
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "proactive_outbound_sent": False,
        },
        "source_reports": source_reports,
        "freeze_evidence": freeze_evidence,
        "service": _service_payload(paths, unit_path, enabled=enabled and ok, changed=unit_changed and ok),
        "boundaries": {
            "service_mutation_allowed": True,
            "scheduler_mutated": False,
            "cron_replacement": False,
            "timer_installation": False,
            "wake_cycle_run": False,
            "wake_events_written": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "proactive_outbound_sent": False,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "memory_authority_expanded": False,
            "voice_output": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_signal_chat": _shell_command(["touch", str(paths.signal_chat_pause_flag)]),
            "disable_or_rollback_signal_chat": disable_signal_chat_command(paths),
            "m10_signal_observation_later": "requires m10_signal_activation_ready",
        },
    }
    return M10SignalActivationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def run_m10_signal_disable(
    paths: CompanionPaths,
    *,
    unit_dir: Path | None = None,
    systemctl_runner: SystemctlRunner | None = None,
    now: datetime | None = None,
) -> M10SignalActivationResult:
    """Disable the managed service artifact and remove the unit file."""

    current = now or datetime.now()
    resolved_unit_dir = unit_dir or default_unit_dir()
    runner = systemctl_runner or run_systemctl_user
    unit_path = resolved_unit_dir / UNIT_NAME
    stages: list[dict] = []
    removed = False
    try:
        if unit_path.exists():
            runner(["disable", "--now", UNIT_NAME])
            unit_path.unlink()
            runner(["daemon-reload"])
            removed = True
        stages.append(_stage(
            "service_disablement",
            True,
            "managed M10.3 service artifact disabled" if removed else "managed M10.3 service artifact was already absent",
            details={"removed": removed, "unit_path": str(unit_path)},
        ))
    except Exception as exc:  # noqa: BLE001 - disablement failures become stage evidence.
        stages.append(_stage(
            "service_disablement",
            False,
            f"could not disable managed service: {type(exc).__name__}: {exc}",
        ))
    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M10.3.rollback",
        "recommendation": DISABLED_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "service": _service_payload(paths, unit_path, enabled=False, changed=removed),
        "boundaries": {
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "proactive_outbound_sent": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M10SignalActivationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m10_signal_activation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m10_signal_activation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def default_unit_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "systemd" / "user"


def build_signal_chat_unit(paths: CompanionPaths) -> str:
    exec_start = build_exec_start(paths)
    return (
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        "Description=Companion M10 Signal chat bridge\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={paths.home}\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def build_exec_start(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m10_signal_chat.py"
    return _shell_command([
        str(python_bin),
        str(script),
        "--companion-home",
        str(paths.home),
        "--confirm-real-signal-send",
    ])


def enable_signal_chat_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m10_signal_activation.py"
    return _shell_command([str(python_bin), str(script), "--companion-home", str(paths.home), "--enable"])


def disable_signal_chat_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m10_signal_activation.py"
    return _shell_command([str(python_bin), str(script), "--companion-home", str(paths.home), "--disable"])


def run_systemctl_user(args: list[str]) -> None:
    completed = subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or f"systemctl --user {' '.join(args)} failed").strip()
        )


def _service_payload(paths: CompanionPaths, unit_path: Path, *, enabled: bool, changed: bool) -> dict:
    return {
        "mechanism": SERVICE_MECHANISM,
        "unit_name": UNIT_NAME,
        "unit_path": str(unit_path),
        "marker": UNIT_MARKER,
        "artifact_count": 1 if enabled else 0,
        "enabled": enabled,
        "changed": changed,
        "exec_start": build_exec_start(paths),
        "enable_command": enable_signal_chat_command(paths),
        "disable_command": disable_signal_chat_command(paths),
        "rollback_command": disable_signal_chat_command(paths),
        "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
        "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
        "state_file": _relative(paths, paths.signal_chat_state_file),
        "lock_file": _relative(paths, paths.signal_chat_lock_file),
        "config_file": _relative(paths, paths.signal_chat_config_file),
    }


def _ready_report_stage(
    report: dict | None,
    *,
    name: str,
    expected_milestone: str,
    expected_recommendation: str,
) -> dict:
    problems = []
    if not isinstance(report, dict):
        problems.append(f"{expected_milestone} report is missing or invalid")
    else:
        if report.get("ok") is not True:
            problems.append(f"{expected_milestone} ok is not true")
        if report.get("milestone") != expected_milestone:
            problems.append(f"milestone is not {expected_milestone}")
        if report.get("recommendation") != expected_recommendation:
            problems.append(f"recommendation is not {expected_recommendation}")
        if report.get("stop_reasons"):
            problems.append(f"{expected_milestone} report has stop_reasons")
    return _stage(
        name,
        not problems,
        f"{expected_milestone} readiness report is ready" if not problems else "; ".join(problems),
    )


def _freeze_stage(freeze_evidence: dict) -> dict:
    if freeze_evidence.get("ok") is True:
        return _stage("upstream_freeze_evidence", True, "M7/M8/M9 freeze evidence passes")
    missing = [
        name
        for name, snapshot in (freeze_evidence.get("reports") or {}).items()
        if not snapshot.get("ok")
    ]
    return _stage(
        "upstream_freeze_evidence",
        False,
        f"freeze evidence not ready: {missing or 'reports missing'}",
    )


def _runner_contract_stage(paths: CompanionPaths) -> dict:
    problems = []
    runner_script = paths.home / "scripts" / "run_m10_signal_chat.py"
    if not runner_script.exists():
        problems.append("scripts/run_m10_signal_chat.py is missing")
    else:
        source = runner_script.read_text()
        for token in ("--confirm-real-signal-send", "run_loop"):
            if token not in source:
                problems.append(f"chat runner is missing {token}")
    return _stage(
        "runner_contract",
        not problems,
        "chat runner script exposes the confirmed loop entrypoint" if not problems else "; ".join(problems),
        details={"exec_start": build_exec_start(paths)},
    )


def _activation_boundary_stage() -> dict:
    return _stage(
        "activation_runtime_boundary",
        True,
        "activation installs the listener service only; it does not chat, wake, or touch the scheduler",
        details={
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "proactive_outbound_sent": False,
            "scheduler_mutated": False,
        },
    )


def _rollback_record_stage(paths: CompanionPaths) -> dict:
    return _stage(
        "rollback_record",
        True,
        "rollback command, pause flag, and runtime paths are recorded",
        details={
            "disable_command": disable_signal_chat_command(paths),
            "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
        },
    )


def _all_pass(stages: list[dict]) -> bool:
    return all(stage.get("status") == "pass" for stage in stages)


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


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


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
