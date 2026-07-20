"""M13.3 Feishu chat listener activation gate.

Installs the Feishu chat bridge as exactly one managed systemd user service,
mirroring the M10.3 pattern with a Feishu-specific unit, marker, and runner.
Enable and disable are explicit and reversible; activation never chats,
never wakes, and never touches cron or the M9 scheduler artifact.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .m10_signal_activation import default_unit_dir, run_systemctl_user
from .paths import CompanionPaths
from .signal_chat import (
    SignalChatConfigError,
    load_feishu_chat_config,
    load_m10_freeze_evidence,
)

READY_RECOMMENDATION = "m13_feishu_activation_ready"
DISABLED_RECOMMENDATION = "m13_feishu_activation_disabled"
SERVICE_MECHANISM = "systemd-user"
UNIT_NAME = "companion-feishu-chat.service"
UNIT_MARKER = "digital-life-m13-feishu-chat-m13.3"
REQUIRED_SOURCE_REPORTS = (
    ("m13_feishu_dry_run_report.json", "M13.1", "m13_feishu_dry_run_ready"),
    ("m13_feishu_trial_report.json", "M13.2", "m13_feishu_trial_ready"),
)

SystemctlRunner = Callable[[list[str]], None]


@dataclass
class M13FeishuActivationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m13_feishu_activation(
    paths: CompanionPaths,
    *,
    enable: bool = True,
    unit_dir: Path | None = None,
    systemctl_runner: SystemctlRunner | None = None,
    now: datetime | None = None,
) -> M13FeishuActivationResult:
    current = now or datetime.now()
    resolved_unit_dir = unit_dir or default_unit_dir()
    runner = systemctl_runner or run_systemctl_user
    if not enable:
        return run_m13_feishu_disable(paths, unit_dir=resolved_unit_dir, systemctl_runner=runner, now=current)

    stages: list[dict] = []
    source_reports: dict[str, dict] = {}
    for name, milestone, recommendation in REQUIRED_SOURCE_REPORTS:
        path = paths.life_loop_dir / name
        report = _load_report(path)
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_source_report_stage(report, milestone=milestone, recommendation=recommendation))

    freeze_evidence = load_m10_freeze_evidence(paths)
    if freeze_evidence.get("ok") is True:
        stages.append(_stage("upstream_freeze_evidence", True, "M7/M8/M9 freeze evidence passes"))
    else:
        missing = [name for name, snap in (freeze_evidence.get("reports") or {}).items() if not snap.get("ok")]
        stages.append(_stage("upstream_freeze_evidence", False, f"freeze evidence not ready: {missing}"))

    try:
        config = load_feishu_chat_config(paths)
        stages.append(_stage(
            "config_ready",
            True,
            f"feishu chat config loaded with {len(config.allowed_senders)} allowlisted open_id(s)",
        ))
    except SignalChatConfigError as exc:
        stages.append(_stage("config_ready", False, str(exc)))

    stages.append(_runner_contract_stage(paths))

    unit_path = resolved_unit_dir / UNIT_NAME
    unit_content = build_feishu_chat_unit(paths)
    unit_error = None
    existing_content = None
    if unit_path.exists():
        try:
            existing_content = unit_path.read_text()
        except OSError as exc:
            unit_error = f"could not read existing unit: {exc}"
    if unit_error is None and existing_content is not None and existing_content != unit_content:
        unit_error = f"existing {UNIT_NAME} does not match the managed M13.3 unit content"
    unit_changed = existing_content is None
    stages.append(_stage(
        "unit_plan",
        unit_error is None,
        "planned exactly one managed feishu chat service unit" if unit_error is None else unit_error,
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
                "one managed feishu chat service artifact is enabled",
            ))
        except Exception as exc:  # noqa: BLE001 - enablement failures become stage evidence.
            stages.append(_stage("service_enablement", False, f"could not enable service: {type(exc).__name__}: {exc}"))
    else:
        stages.append(_stage("service_enablement", False, "service enablement skipped because activation preflight failed"))

    stages.append(_stage(
        "activation_runtime_boundary",
        True,
        "activation installs the listener service only; it does not chat, wake, or touch the scheduler",
    ))
    stages.append(_stage(
        "rollback_record",
        True,
        f"rollback command and pause flag recorded: {disable_feishu_chat_command(paths)}",
    ))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M13.3",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M13 feishu chat listener activation",
            "channel": "feishu",
            "mechanism": SERVICE_MECHANISM,
            "service_mutation_allowed": True,
            "writes_exactly_one_service_artifact": True,
            "wake_cycle_run": False,
            "provider_calls": 0,
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
            "provider_generation_requested": False,
            "provider_calls": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "secrets_in_reports_or_ledger": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_feishu_chat": _shell_command(["touch", str(paths.signal_chat_pause_flag)]),
            "disable_or_rollback_feishu_chat": disable_feishu_chat_command(paths),
            "m13_feishu_observation_later": "requires m13_feishu_activation_ready",
        },
    }
    return M13FeishuActivationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def run_m13_feishu_disable(
    paths: CompanionPaths,
    *,
    unit_dir: Path | None = None,
    systemctl_runner: SystemctlRunner | None = None,
    now: datetime | None = None,
) -> M13FeishuActivationResult:
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
            "managed M13.3 service artifact disabled" if removed else "managed M13.3 service artifact was already absent",
        ))
    except Exception as exc:  # noqa: BLE001 - disablement failures become stage evidence.
        stages.append(_stage("service_disablement", False, f"could not disable managed service: {type(exc).__name__}: {exc}"))
    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M13.3.rollback",
        "recommendation": DISABLED_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "service": _service_payload(paths, unit_path, enabled=False, changed=removed),
        "boundaries": {
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M13FeishuActivationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m13_feishu_activation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m13_feishu_activation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def build_feishu_chat_unit(paths: CompanionPaths) -> str:
    exec_start = build_exec_start(paths)
    return (
        f"# {UNIT_MARKER}\n"
        "[Unit]\n"
        "Description=Companion M13 Feishu chat bridge\n"
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
    script = paths.home / "scripts" / "run_m13_feishu_chat.py"
    return _shell_command([
        str(python_bin),
        str(script),
        "--companion-home",
        str(paths.home),
        "--confirm-real-feishu-send",
    ])


def enable_feishu_chat_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m13_feishu_activation.py"
    return _shell_command([str(python_bin), str(script), "--companion-home", str(paths.home), "--enable"])


def disable_feishu_chat_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m13_feishu_activation.py"
    return _shell_command([str(python_bin), str(script), "--companion-home", str(paths.home), "--disable"])


def _service_payload(paths: CompanionPaths, unit_path: Path, *, enabled: bool, changed: bool) -> dict:
    return {
        "mechanism": SERVICE_MECHANISM,
        "channel": "feishu",
        "unit_name": UNIT_NAME,
        "unit_path": str(unit_path),
        "marker": UNIT_MARKER,
        "artifact_count": 1 if enabled else 0,
        "enabled": enabled,
        "changed": changed,
        "exec_start": build_exec_start(paths),
        "enable_command": enable_feishu_chat_command(paths),
        "disable_command": disable_feishu_chat_command(paths),
        "rollback_command": disable_feishu_chat_command(paths),
        "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
        "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
        "lock_file": _relative(paths, paths.feishu_chat_lock_file),
        "config_file": _relative(paths, paths.feishu_chat_config_file),
    }


def _runner_contract_stage(paths: CompanionPaths) -> dict:
    problems = []
    runner_script = paths.home / "scripts" / "run_m13_feishu_chat.py"
    if not runner_script.exists():
        problems.append("scripts/run_m13_feishu_chat.py is missing")
    else:
        source = runner_script.read_text()
        for token in ("--confirm-real-feishu-send", "run_loop", "start_listener"):
            if token not in source:
                problems.append(f"feishu runner is missing {token}")
    return _stage(
        "runner_contract",
        not problems,
        "feishu runner script exposes the confirmed listener loop" if not problems else "; ".join(problems),
    )


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
        f"{milestone} readiness report is ready" if not problems else "; ".join(problems),
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
