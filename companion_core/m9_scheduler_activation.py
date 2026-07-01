"""M9.3 limited live scheduler activation gate."""

from __future__ import annotations

import getpass
import json
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .m9_scheduler_dry_run import (
    DEFAULT_DAILY_LIVE_WAKE_BUDGET,
    DEFAULT_QUIET_HOURS,
    REQUIRED_SKIP_REASONS,
)
from .m9_scheduler_tick import (
    CADENCE_MODEL,
    SCHEDULED_WAKE_OUTPUT,
    initialize_scheduler_presence_state,
)
from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_scheduler_activation_ready"
DISABLED_RECOMMENDATION = "m9_scheduler_activation_disabled"
M9_REVALIDATION_RECOMMENDATION = "m9_scheduler_revalidation_ready"
M9_DRY_RUN_RECOMMENDATION = "m9_scheduler_dry_run_ready"
CRON_MARKER = "digital-life-m9-scheduler-m9.3"
CRON_SCHEDULE = "*/15 * * * *"

CrontabReader = Callable[[], str]
CrontabWriter = Callable[[str], None]


@dataclass
class M9SchedulerActivationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m9_scheduler_activation(
    paths: CompanionPaths,
    *,
    enable: bool = True,
    crontab_reader: CrontabReader | None = None,
    crontab_writer: CrontabWriter | None = None,
    now: datetime | None = None,
    random_seed: int | None = None,
) -> M9SchedulerActivationResult:
    """Enable or disable the single M9.3 cron scheduler artifact."""

    current = now or datetime.now()
    reader = crontab_reader or read_user_crontab
    writer = crontab_writer or write_user_crontab
    if not enable:
        return run_m9_scheduler_disable(paths, crontab_reader=reader, crontab_writer=writer, now=current)

    stages: list[dict] = []
    source_reports: dict[str, dict] = {}
    revalidation_path = paths.life_loop_dir / "m9_scheduler_revalidation_report.json"
    dry_run_path = paths.life_loop_dir / "m9_scheduler_dry_run_report.json"
    revalidation_report = _load_report(revalidation_path)
    dry_run_report = _load_report(dry_run_path)
    source_reports["m9_scheduler_revalidation"] = _report_snapshot(paths, revalidation_path, revalidation_report)
    source_reports["m9_scheduler_dry_run"] = _report_snapshot(paths, dry_run_path, dry_run_report)
    revalidation_stage = _ready_report_stage(
        revalidation_report,
        name="m9_scheduler_revalidation",
        expected_milestone="M9.1",
        expected_recommendation=M9_REVALIDATION_RECOMMENDATION,
    )
    dry_run_stage = _ready_report_stage(
        dry_run_report,
        name="m9_scheduler_dry_run",
        expected_milestone="M9.2",
        expected_recommendation=M9_DRY_RUN_RECOMMENDATION,
    )
    stages.extend([revalidation_stage, dry_run_stage])

    target_command = _target_command(paths, revalidation_report, dry_run_report)
    cron_line = build_m9_cron_line(paths)
    artifact = _artifact(paths, cron_line)
    stages.append(_wrapper_contract_stage(paths, target_command))

    existing_crontab = ""
    planned_crontab = ""
    cron_changed = False
    cron_error = None
    try:
        existing_crontab = reader()
        planned_crontab, cron_changed = _install_cron_line(existing_crontab, cron_line)
    except Exception as exc:  # pragma: no cover - exercised through result shape in integration.
        cron_error = f"{type(exc).__name__}: {exc}"
    stages.append(_cron_plan_stage(planned_crontab, cron_error))

    presence_state = None
    if _all_pass(stages):
        try:
            presence_state = initialize_scheduler_presence_state(
                paths,
                now=current,
                random_seed=random_seed,
                write_runtime=True,
            )
            stages.append(_stage(
                "presence_state_initialization",
                True,
                "live scheduler presence state initialized without running wake",
                details={
                    "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
                    "next_candidate_after": presence_state.get("next_candidate_after"),
                },
            ))
        except OSError as exc:
            stages.append(_stage(
                "presence_state_initialization",
                False,
                f"could not initialize presence state: {exc}",
            ))
    else:
        stages.append(_stage(
            "presence_state_initialization",
            False,
            "presence state initialization skipped because activation preflight failed",
        ))

    if _all_pass(stages):
        try:
            if cron_changed:
                writer(planned_crontab)
            stages.append(_stage(
                "cron_artifact_enablement",
                True,
                "one managed cron scheduler artifact is enabled",
                details={
                    "changed": cron_changed,
                    "artifact_count": _marker_count(planned_crontab),
                    "artifact_name": CRON_MARKER,
                },
            ))
        except Exception as exc:  # pragma: no cover - exercised through result shape in integration.
            stages.append(_stage(
                "cron_artifact_enablement",
                False,
                f"could not write user crontab: {type(exc).__name__}: {exc}",
            ))
    else:
        stages.append(_stage(
            "cron_artifact_enablement",
            False,
            "cron artifact enablement skipped because activation preflight failed",
        ))

    stages.append(_activation_boundary_stage())
    stages.append(_rollback_record_stage(paths))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = _activation_report(
        paths,
        current,
        ok=ok,
        cron_changed=cron_changed if ok else False,
        artifact=artifact,
        target_command=target_command,
        source_reports=source_reports,
        stages=stages,
        stop_reasons=stop_reasons,
        errors=errors,
        presence_state=presence_state,
    )
    return M9SchedulerActivationResult(
        ok=ok,
        recommendation=report["recommendation"],
        report=report,
        errors=errors,
    )


def run_m9_scheduler_disable(
    paths: CompanionPaths,
    *,
    crontab_reader: CrontabReader | None = None,
    crontab_writer: CrontabWriter | None = None,
    now: datetime | None = None,
) -> M9SchedulerActivationResult:
    """Remove the managed M9.3 cron artifact and preserve unrelated crontab lines."""

    current = now or datetime.now()
    reader = crontab_reader or read_user_crontab
    writer = crontab_writer or write_user_crontab
    stages = []
    try:
        existing = reader()
        planned, removed = _remove_cron_line(existing)
        if removed:
            writer(planned)
        stages.append(_stage(
            "cron_artifact_disablement",
            True,
            "managed M9.3 cron artifact disabled" if removed else "managed M9.3 cron artifact was already absent",
            details={"removed": removed, "artifact_name": CRON_MARKER},
        ))
    except Exception as exc:
        stages.append(_stage(
            "cron_artifact_disablement",
            False,
            f"could not disable managed crontab entry: {type(exc).__name__}: {exc}",
        ))
    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M9.3.rollback",
        "recommendation": DISABLED_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "scheduler": {
            "mechanism": "cron",
            "artifact": _artifact(paths, build_m9_cron_line(paths)),
            "enabled": False,
            "disable_command": disable_command(paths),
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
        },
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
    return M9SchedulerActivationResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m9_scheduler_activation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m9_scheduler_activation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def build_m9_cron_line(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    tick_script = paths.home / "scripts" / "run_m9_scheduler_tick.py"
    log_file = paths.home / "window" / "m9_scheduler_tick.log"
    command = (
        f"cd {shlex.quote(str(paths.home))} && "
        f"{shlex.quote(str(python_bin))} {shlex.quote(str(tick_script))} "
        f"--companion-home {shlex.quote(str(paths.home))} "
        f">> {shlex.quote(str(log_file))} 2>&1"
    )
    return f"{CRON_SCHEDULE} {command} # {CRON_MARKER}"


def enable_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m9_scheduler_activation.py"
    return _shell_command([
        str(python_bin),
        str(script),
        "--companion-home",
        str(paths.home),
        "--enable",
    ])


def disable_command(paths: CompanionPaths) -> str:
    python_bin = paths.home / ".venv" / "bin" / "python"
    script = paths.home / "scripts" / "run_m9_scheduler_activation.py"
    return _shell_command([
        str(python_bin),
        str(script),
        "--companion-home",
        str(paths.home),
        "--disable",
    ])


def read_user_crontab() -> str:
    completed = subprocess.run(["crontab", "-l"], text=True, capture_output=True, check=False)
    if completed.returncode == 0:
        return completed.stdout
    if completed.returncode == 1 and "no crontab" in completed.stderr.lower():
        return ""
    raise RuntimeError((completed.stderr or completed.stdout or "crontab -l failed").strip())


def write_user_crontab(text: str) -> None:
    completed = subprocess.run(["crontab", "-"], input=text, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "crontab - failed").strip())


def _install_cron_line(existing: str, cron_line: str) -> tuple[str, bool]:
    lines = existing.splitlines()
    marker_lines = [line for line in lines if CRON_MARKER in line]
    if len(marker_lines) > 1:
        raise ValueError(f"multiple {CRON_MARKER} entries already exist")
    if marker_lines:
        if marker_lines[0] != cron_line:
            raise ValueError(f"existing {CRON_MARKER} entry does not match expected M9.3 cron line")
        return _normalize_crontab(lines), False
    return _normalize_crontab([*lines, cron_line]), True


def _remove_cron_line(existing: str) -> tuple[str, bool]:
    lines = existing.splitlines()
    kept = [line for line in lines if CRON_MARKER not in line]
    return _normalize_crontab(kept), len(kept) != len(lines)


def _normalize_crontab(lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join(lines).rstrip() + "\n"


def _activation_report(
    paths: CompanionPaths,
    saved_at: datetime,
    *,
    ok: bool,
    cron_changed: bool,
    artifact: dict,
    target_command: str,
    source_reports: dict,
    stages: list[dict],
    stop_reasons: list[str],
    errors: list[str],
    presence_state: dict | None,
) -> dict:
    return {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M9.3",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M9 limited live scheduler activation",
            "mechanism": "cron",
            "scheduler_mutation_allowed": True,
            "writes_exactly_one_scheduler_artifact": True,
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
            "voice_signal_hardware_activation_allowed": False,
        },
        "source_reports": source_reports,
        "cadence": {
            "model": CADENCE_MODEL,
            "quiet_hours": list(DEFAULT_QUIET_HOURS),
            "daily_live_wake_budget": DEFAULT_DAILY_LIVE_WAKE_BUDGET,
            "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
            "skip_reasons": list(REQUIRED_SKIP_REASONS),
            "scheduler_check_interval_minutes": 15,
        },
        "scheduler": {
            "mechanism": "cron",
            "artifact": artifact,
            "artifact_count": 1 if ok else 0,
            "enabled": ok,
            "changed": cron_changed,
            "enable_command": enable_command(paths),
            "disable_command": disable_command(paths),
            "rollback_command": disable_command(paths),
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
            "attempts_file": _relative(paths, paths.scheduler_attempts_file),
            "scheduler_lock_file": _relative(paths, paths.scheduler_wake_lock_file),
            "wrapper_script": _relative(paths, paths.home / "scripts" / "run_m9_scheduler_tick.py"),
            "target_command": target_command,
            "presence_state_initialized": presence_state is not None,
            "next_candidate_after": presence_state.get("next_candidate_after") if presence_state else None,
        },
        "boundaries": {
            "scheduler_mutated": ok and cron_changed,
            "cron_replacement": False,
            "timer_installation": False,
            "service_mutation_allowed": False,
            "wake_cycle_run": False,
            "wake_events_written": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
        "evidence": {
            "m9_1_revalidation_ready": _stage_passed(stages, "m9_scheduler_revalidation"),
            "m9_2_dry_run_ready": _stage_passed(stages, "m9_scheduler_dry_run"),
            "scheduler_artifact_count": 1 if ok else 0,
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
            "activation_wake_cycle_run": False,
            "provider_calls": 0,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "pause_scheduler": _shell_command(["touch", str(paths.scheduler_pause_flag)]),
            "disable_or_rollback_scheduler": disable_command(paths),
            "m9_presence_observation_later": "requires m9_scheduler_activation_ready",
        },
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
        if report.get("provider_calls", 0) not in (0, None):
            problems.append(f"{expected_milestone} report has provider calls")
        boundaries = report.get("boundaries") if isinstance(report.get("boundaries"), dict) else {}
        for key in (
            "wake_cycle_run",
            "provider_generation_requested",
            "raw_provider_payload_stored",
            "life_write_route_added",
            "semantic_shadow_authority_promoted",
            "proposal_or_quarantine_prompt_authority",
            "voice_signal_hardware_activation_allowed",
        ):
            if boundaries.get(key) is True:
                problems.append(f"{expected_milestone} boundary {key} is true")
    return _stage(
        name,
        not problems,
        f"{expected_milestone} readiness report is ready" if not problems else "; ".join(problems),
    )


def _wrapper_contract_stage(paths: CompanionPaths, target_command: str) -> dict:
    problems = []
    wrapper = paths.home / "scripts" / "run_m9_scheduler_tick.py"
    if not wrapper.exists():
        problems.append("scripts/run_m9_scheduler_tick.py is missing")
    for token in ("--provider deepseek", "--memory-mode json", "--trigger scheduled-wake"):
        if token not in target_command:
            problems.append(f"target command is missing {token}")
    return _stage(
        "scheduler_wrapper_contract",
        not problems,
        "scheduler wrapper and existing wake command shape are ready" if not problems else "; ".join(problems),
        details={
            "wrapper_script": _relative(paths, wrapper),
            "target_command": target_command,
            "cadence_model": CADENCE_MODEL,
            "daily_live_wake_budget": DEFAULT_DAILY_LIVE_WAKE_BUDGET,
            "quiet_hours": list(DEFAULT_QUIET_HOURS),
            "scheduled_wake_output": SCHEDULED_WAKE_OUTPUT,
        },
    )


def _cron_plan_stage(planned_crontab: str, error: str | None) -> dict:
    problems = []
    if error:
        problems.append(error)
    if not error and _marker_count(planned_crontab) != 1:
        problems.append(f"planned crontab does not contain exactly one {CRON_MARKER} entry")
    return _stage(
        "cron_artifact_plan",
        not problems,
        "planned crontab contains exactly one managed M9.3 scheduler artifact"
        if not problems
        else "; ".join(problems),
        details={"artifact_name": CRON_MARKER, "artifact_count": _marker_count(planned_crontab)},
    )


def _activation_boundary_stage() -> dict:
    return _stage(
        "activation_runtime_boundary",
        True,
        "activation installs scheduler only; it does not run wake or call a provider",
        details={
            "wake_cycle_run": False,
            "wake_events_written": False,
            "provider_generation_requested": False,
            "provider_calls": 0,
            "voice_signal_hardware_activation_allowed": False,
        },
    )


def _rollback_record_stage(paths: CompanionPaths) -> dict:
    return _stage(
        "rollback_record",
        True,
        "rollback command, pause flag, and presence state paths are recorded",
        details={
            "disable_command": disable_command(paths),
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
        },
    )


def _target_command(paths: CompanionPaths, revalidation_report: dict | None, dry_run_report: dict | None) -> str:
    for report in (revalidation_report, dry_run_report):
        if not isinstance(report, dict):
            continue
        for section in ("handoff", "dry_run"):
            payload = report.get(section) if isinstance(report.get(section), dict) else {}
            if payload.get("target_command"):
                return str(payload["target_command"])
    return (
        f"cd {shlex.quote(str(paths.home))} && "
        ".venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {shlex.quote(str(paths.home))} "
        "--provider deepseek --memory-mode json --trigger scheduled-wake"
    )


def _artifact(paths: CompanionPaths, cron_line: str) -> dict:
    return {
        "path": f"user-crontab:{getpass.getuser()}",
        "name": CRON_MARKER,
        "line": cron_line,
        "schedule": CRON_SCHEDULE,
        "check_interval_minutes": 15,
        "wrapper_script": _relative(paths, paths.home / "scripts" / "run_m9_scheduler_tick.py"),
    }


def _marker_count(crontab_text: str) -> int:
    return sum(1 for line in crontab_text.splitlines() if CRON_MARKER in line)


def _all_pass(stages: list[dict]) -> bool:
    return all(stage.get("status") == "pass" for stage in stages)


def _stage_passed(stages: list[dict], name: str) -> bool:
    return any(stage.get("name") == name and stage.get("status") == "pass" for stage in stages)


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
