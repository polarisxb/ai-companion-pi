"""M9.1 read-only scheduler handoff revalidation gate."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .llm import SUPPORTED_LLM_PROVIDERS
from .paths import CompanionPaths


READY_RECOMMENDATION = "m9_scheduler_revalidation_ready"
M9_DESIGN_RECOMMENDATION = "m9_controlled_presence_design_ready"
M6_FINAL_RECOMMENDATION = "m6_frozen_ready_for_scheduler_handoff"
M8_FREEZE_RECOMMENDATION = "m8_memory_dialogue_frozen"
EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
EXPECTED_TRIGGER = "scheduled-wake"
REQUIRED_WAKE_FLAGS = (
    "--companion-home",
    "--trigger",
    "--memory-mode",
    "--provider",
    "--fake-llm",
    "--check-provider",
)
MUTATING_SCHEDULER_RE = re.compile(
    r"\b(crontab\s+-|systemctl\s+(?:enable|start|restart|stop|disable)|"
    r"timer_installation\s*[:=]\s*true|scheduler_mutat(?:ed|ion_allowed|ion_attempted)\s*[:=]\s*true)\b",
    re.IGNORECASE,
)
LIFE_WRITE_ROUTE_RE = re.compile(
    r"@app\.(?:post|put|patch|delete)\([\"']/life(?:[\"'/)]|/)|"
    r"@app\.route\([\"']/life[^)]*methods\s*=\s*\[[^\]]*[\"'](?:POST|PUT|PATCH|DELETE)[\"']",
    re.IGNORECASE | re.DOTALL,
)

SchedulerInventoryProvider = Callable[[CompanionPaths], dict]


@dataclass
class M9SchedulerRevalidationResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m9_scheduler_revalidation_check(
    paths: CompanionPaths,
    *,
    scheduler_inventory_provider: SchedulerInventoryProvider | None = None,
) -> M9SchedulerRevalidationResult:
    """Revalidate M6/M8 scheduler handoff readiness without mutating runtime state."""

    saved_at = datetime.now()
    source_reports: dict[str, dict] = {}
    stages: list[dict] = []

    m9_design_path = paths.life_loop_dir / "m9_controlled_presence_design_report.json"
    m6_final_path = paths.life_loop_dir / "m6_final_freeze_report.json"
    m8_freeze_path = paths.life_loop_dir / "m8_memory_freeze_report.json"
    m9_design_report = _load_report(m9_design_path)
    m6_final_report = _load_report(m6_final_path)
    m8_freeze_report = _load_report(m8_freeze_path)

    source_reports["m9_controlled_presence_design"] = _report_snapshot(paths, m9_design_path, m9_design_report)
    source_reports["m6_final_freeze"] = _report_snapshot(paths, m6_final_path, m6_final_report)
    source_reports["m8_memory_freeze"] = _report_snapshot(paths, m8_freeze_path, m8_freeze_report)

    stages.append(_m9_design_stage(m9_design_report))
    stages.append(_m6_final_freeze_stage(m6_final_report))
    stages.append(_m8_memory_freeze_stage(m8_freeze_report))
    wake_stage, wake_evidence = _wake_command_stage(paths, m6_final_report)
    stages.append(wake_stage)
    provider_stage, _provider_evidence = _provider_config_stage(wake_evidence)
    stages.append(provider_stage)
    stages.append(_runtime_paths_stage(paths))
    lock_stage, lock_evidence = _lock_pause_state_stage(paths)
    stages.append(lock_stage)
    inventory = (
        scheduler_inventory_provider(paths)
        if scheduler_inventory_provider
        else discover_m9_scheduler_inventory(paths)
    )
    stages.append(_scheduler_inventory_stage(inventory))
    stages.append(_static_boundary_stage(paths))
    stages.append(_readonly_profile_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M9.1",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": _readonly_profile(),
        "cadence": _cadence_from_design(m9_design_report),
        "source_reports": source_reports,
        "handoff": {
            "ready": ok,
            "target_command": wake_evidence["target_command"],
            "target_script": wake_evidence["target_script"],
            "provider": EXPECTED_PROVIDER,
            "memory_mode": EXPECTED_MEMORY_MODE,
            "trigger": EXPECTED_TRIGGER,
            "wake_command_ready": wake_stage["status"] == "pass",
            "provider_config_ready": provider_stage["status"] == "pass",
            "scheduler_mutated": False,
            "wake_cycle_run": False,
        },
        "scheduler": {
            "expected_state_before_m9_3": "no_live_scheduler_artifact",
            "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
            "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
            "lock_files": lock_evidence["lock_files"],
            "inventory": inventory,
        },
        "boundaries": {
            "scheduler_mutated": False,
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
            "m9_0_design_ready": _stage_passed(stages, "m9_controlled_presence_design"),
            "m6_7_final_freeze_ready": _stage_passed(stages, "m6_final_freeze"),
            "m8_7_memory_freeze_ready": _stage_passed(stages, "m8_memory_freeze"),
            "wake_command_shape_ready": wake_stage["status"] == "pass",
            "provider_config_shape_ready": provider_stage["status"] == "pass",
            "runtime_paths_ready": _stage_passed(stages, "runtime_paths"),
            "pause_and_state_paths_defined": lock_stage["status"] == "pass",
            "unexpected_scheduler_artifacts": inventory.get("unexpected_active_artifacts", []),
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
        "next_commands": {
            "m9_scheduler_revalidation": _shell_command([
                "python3",
                "scripts/run_m9_scheduler_revalidation.py",
                "--companion-home",
                str(paths.home),
            ]),
            "m9_scheduler_dry_run_later": "requires m9_scheduler_revalidation_ready",
        },
    }
    return M9SchedulerRevalidationResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m9_scheduler_revalidation_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | Path | None = None,
) -> Path:
    report_path = (
        Path(report_file).expanduser()
        if report_file
        else paths.life_loop_dir / "m9_scheduler_revalidation_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def discover_m9_scheduler_inventory(paths: CompanionPaths) -> dict:
    """Inspect likely scheduler surfaces with read-only commands."""

    probes = [
        _run_readonly_probe("crontab", ["crontab", "-l"], paths),
        _run_readonly_probe("systemd_user_timers", ["systemctl", "--user", "list-timers", "--all", "--no-pager", "--plain"], paths),
        _run_readonly_probe("systemd_user_services", ["systemctl", "--user", "list-units", "--type=service", "--all", "--no-pager", "--plain"], paths),
        _run_readonly_probe("systemd_system_timers", ["systemctl", "list-timers", "--all", "--no-pager", "--plain"], paths),
        _run_readonly_probe("systemd_system_services", ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--plain"], paths),
    ]
    matched = []
    for probe in probes:
        matched.extend(probe.get("matched_lines", []))
    return {
        "source": "read_only_crontab_systemctl",
        "mutation_attempted": False,
        "probes": probes,
        "unexpected_active_artifacts": matched,
    }


def source_only_m9_scheduler_inventory(paths: CompanionPaths) -> dict:
    """Return a deterministic non-shell inventory for tests or restricted shells."""

    return {
        "source": "source_only",
        "mutation_attempted": False,
        "probes": [
            {
                "name": "repo_source_scan",
                "status": "skipped",
                "matched_lines": [],
                "message": "system scheduler probes skipped by caller",
            }
        ],
        "unexpected_active_artifacts": [],
    }


def _m9_design_stage(report: dict | None) -> dict:
    problems = _base_report_problems(
        report,
        expected_milestone="M9.0",
        expected_recommendation=M9_DESIGN_RECOMMENDATION,
    )
    design = report.get("design") if isinstance(report, dict) and isinstance(report.get("design"), dict) else {}
    cadence = design.get("cadence") if isinstance(design.get("cadence"), dict) else {}
    boundaries = report.get("boundaries") if isinstance(report, dict) and isinstance(report.get("boundaries"), dict) else {}
    if cadence.get("model") != "randomized_presence_windows":
        problems.append("M9.0 cadence model is not randomized_presence_windows")
    if cadence.get("quiet_hours") != ["00:00", "08:00"]:
        problems.append("M9.0 quiet hours are not 00:00-08:00")
    if cadence.get("daily_live_wake_budget") != 2:
        problems.append("M9.0 daily_live_wake_budget is not 2")
    if cadence.get("scheduled_wake_output") != "internal_only":
        problems.append("M9.0 scheduled_wake_output is not internal_only")
    problems.extend(_false_boundary_problems(
        boundaries,
        (
            "scheduler_mutated",
            "wake_cycle_run",
            "provider_generation_requested",
            "life_write_route_added",
            "semantic_shadow_authority_promoted",
            "raw_provider_payload_stored",
        ),
        label="M9.0",
    ))
    return _stage(
        "m9_controlled_presence_design",
        not problems,
        "M9.0 controlled presence design is ready" if not problems else "; ".join(problems),
        details={"cadence": cadence},
    )


def _m6_final_freeze_stage(report: dict | None) -> dict:
    problems = _base_report_problems(
        report,
        expected_milestone="M6.7",
        expected_recommendation=M6_FINAL_RECOMMENDATION,
    )
    final_freeze = report.get("final_freeze") if isinstance(report, dict) and isinstance(report.get("final_freeze"), dict) else {}
    profile = report.get("profile") if isinstance(report, dict) and isinstance(report.get("profile"), dict) else {}
    handoff = report.get("handoff") if isinstance(report, dict) and isinstance(report.get("handoff"), dict) else {}
    if final_freeze.get("frozen") is not True:
        problems.append("M6.7 final freeze is not frozen")
    if final_freeze.get("readonly") is not True:
        problems.append("M6.7 final freeze is not readonly")
    if final_freeze.get("scheduler_handoff_ready") is not True and handoff.get("ready") is not True:
        problems.append("M6.7 scheduler handoff is not ready")
    if final_freeze.get("scheduler_mutated") is True or handoff.get("mutated") is True:
        problems.append("M6.7 reported scheduler mutation")
    if profile.get("provider") != EXPECTED_PROVIDER:
        problems.append(f"M6.7 provider is not {EXPECTED_PROVIDER}")
    if profile.get("memory_mode") != EXPECTED_MEMORY_MODE:
        problems.append(f"M6.7 memory_mode is not {EXPECTED_MEMORY_MODE}")
    problems.extend(_false_boundary_problems(
        profile,
        (
            "cron_replacement",
            "timer_installation",
            "scheduler_mutation_allowed",
            "scheduler_mutation_attempted",
            "real_wake_requested",
            "provider_generation_requested",
            "signal_voice_hardware_activation_allowed",
        ),
        label="M6.7",
    ))
    return _stage(
        "m6_final_freeze",
        not problems,
        "M6.7 final freeze remains ready for scheduler handoff" if not problems else "; ".join(problems),
        details={
            "target_command": final_freeze.get("target_command") or handoff.get("target_command"),
            "scheduler_handoff_ready": final_freeze.get("scheduler_handoff_ready") is True or handoff.get("ready") is True,
        },
    )


def _m8_memory_freeze_stage(report: dict | None) -> dict:
    problems = _base_report_problems(
        report,
        expected_milestone="M8.7",
        expected_recommendation=M8_FREEZE_RECOMMENDATION,
    )
    final_freeze = report.get("final_freeze") if isinstance(report, dict) and isinstance(report.get("final_freeze"), dict) else {}
    boundaries = report.get("boundaries") if isinstance(report, dict) and isinstance(report.get("boundaries"), dict) else {}
    if final_freeze.get("frozen") is not True:
        problems.append("M8.7 final freeze is not frozen")
    if final_freeze.get("readonly") is not True:
        problems.append("M8.7 final freeze is not readonly")
    problems.extend(_false_boundary_problems(
        boundaries,
        (
            "scheduler_mutated",
            "wake_cycle_run",
            "wake_events_written",
            "provider_generation_requested",
            "life_write_route_added",
            "semantic_shadow_authority_promoted",
            "proposal_or_quarantine_prompt_authority",
            "raw_provider_payload_stored",
        ),
        label="M8.7",
    ))
    return _stage(
        "m8_memory_freeze",
        not problems,
        "M8.7 memory/dialogue freeze remains ready" if not problems else "; ".join(problems),
        details={
            "frozen": final_freeze.get("frozen") is True,
            "memory_stewardship_ready": final_freeze.get("memory_stewardship_ready") is True,
            "dialogue_humanity_ready": final_freeze.get("dialogue_humanity_ready") is True,
        },
    )


def _wake_command_stage(paths: CompanionPaths, m6_report: dict | None) -> tuple[dict, dict]:
    script = paths.home / "scripts" / "run_wake_cycle.py"
    source = _read_text(script)
    command = _target_command(paths, m6_report)
    missing_flags = [flag for flag in REQUIRED_WAKE_FLAGS if flag not in source]
    missing_command_tokens = [
        token
        for token in (
            "--provider deepseek",
            "--memory-mode json",
            "--trigger scheduled-wake",
        )
        if token not in command
    ]
    problems = []
    if not script.exists() or not script.is_file():
        problems.append("scripts/run_wake_cycle.py is missing")
    if missing_flags:
        problems.append("wake script is missing flags: " + ", ".join(missing_flags))
    if missing_command_tokens:
        problems.append("M6.7 handoff command is missing tokens: " + ", ".join(missing_command_tokens))
    evidence = {
        "target_script": _relative(paths, script),
        "target_command": command,
        "required_flags": list(REQUIRED_WAKE_FLAGS),
        "missing_flags": missing_flags,
        "missing_command_tokens": missing_command_tokens,
    }
    return (
        _stage(
            "wake_command_shape",
            not problems,
            "wake command shape supports scheduled deepseek/json execution"
            if not problems
            else "; ".join(problems),
            details=evidence,
        ),
        evidence,
    )


def _provider_config_stage(wake_evidence: dict) -> tuple[dict, dict]:
    problems = []
    if EXPECTED_PROVIDER not in SUPPORTED_LLM_PROVIDERS:
        problems.append(f"{EXPECTED_PROVIDER} is not in SUPPORTED_LLM_PROVIDERS")
    if "--check-provider" in wake_evidence.get("missing_flags", []):
        problems.append("wake script cannot perform explicit provider checks")
    evidence = {
        "provider": EXPECTED_PROVIDER,
        "supported": EXPECTED_PROVIDER in SUPPORTED_LLM_PROVIDERS,
        "connectivity_checked": False,
        "provider_calls": 0,
        "check_provider_flag_available": "--check-provider" not in wake_evidence.get("missing_flags", []),
    }
    return (
        _stage(
            "provider_config_shape",
            not problems,
            "provider configuration shape is ready without calling provider"
            if not problems
            else "; ".join(problems),
            details=evidence,
        ),
        evidence,
    )


def _runtime_paths_stage(paths: CompanionPaths) -> dict:
    required_dirs = {
        "home": paths.home,
        "life_loop": paths.life_loop_dir,
        "journals": paths.journals_dir,
        "memory": paths.memory_dir,
        "conversations": paths.conversations_dir,
        "requests": paths.requests_dir,
    }
    missing = [name for name, path in required_dirs.items() if not path.exists() or not path.is_dir()]
    details = {
        name: {"path": _relative(paths, path), "exists": path.exists(), "is_dir": path.is_dir()}
        for name, path in required_dirs.items()
    }
    return _stage(
        "runtime_paths",
        not missing,
        "runtime directories exist for read-only scheduler handoff checks"
        if not missing
        else "missing runtime directories: " + ", ".join(missing),
        details=details,
    )


def _lock_pause_state_stage(paths: CompanionPaths) -> tuple[dict, dict]:
    lock_files = []
    try:
        lock_files = [_relative(paths, path) for path in sorted(paths.life_loop_dir.glob("*.lock")) if path.is_file()]
        glob_error = None
    except OSError as exc:
        glob_error = str(exc)
    problems = []
    if glob_error:
        problems.append(f"could not inspect lock files: {glob_error}")
    if paths.scheduler_pause_flag.parent != paths.life_loop_dir:
        problems.append("scheduler pause flag is outside life-loop")
    if paths.scheduler_presence_state_file.parent != paths.life_loop_dir:
        problems.append("scheduler presence state is outside life-loop")
    evidence = {
        "pause_flag_path": _relative(paths, paths.scheduler_pause_flag),
        "pause_flag_exists": paths.scheduler_pause_flag.exists(),
        "presence_state_path": _relative(paths, paths.scheduler_presence_state_file),
        "presence_state_exists": paths.scheduler_presence_state_file.exists(),
        "lock_files": lock_files,
    }
    return (
        _stage(
            "lock_pause_state_paths",
            not problems,
            "pause flag, presence state path, and lock file inventory are defined"
            if not problems
            else "; ".join(problems),
            details=evidence,
        ),
        evidence,
    )


def _scheduler_inventory_stage(inventory: dict) -> dict:
    unexpected = inventory.get("unexpected_active_artifacts", [])
    mutation = inventory.get("mutation_attempted") is True
    problems = []
    if mutation:
        problems.append("scheduler inventory attempted mutation")
    if unexpected:
        problems.append("unexpected scheduler artifacts detected: " + "; ".join(str(item) for item in unexpected[:5]))
    return _stage(
        "scheduler_inventory",
        not problems,
        "no unexpected project scheduler artifacts detected"
        if not problems
        else "; ".join(problems),
        details={
            "source": inventory.get("source"),
            "probe_count": len(inventory.get("probes", [])) if isinstance(inventory.get("probes"), list) else 0,
            "unexpected_active_artifacts": unexpected,
        },
    )


def _static_boundary_stage(paths: CompanionPaths) -> dict:
    files = [
        paths.home / "scripts" / "run_wake_cycle.py",
        paths.home / "scripts" / "run_m9_scheduler_revalidation.py",
        paths.home / "companion_core" / "m9_scheduler_revalidation.py",
        paths.home / "window" / "window.py",
    ]
    problems = []
    for path in files:
        text = _read_text(path)
        if not text:
            continue
        if path.name != "m9_scheduler_revalidation.py" and MUTATING_SCHEDULER_RE.search(text):
            problems.append(f"scheduler mutation pattern found in {_relative(paths, path)}")
    window_source = _read_text(paths.home / "window" / "window.py")
    if LIFE_WRITE_ROUTE_RE.search(window_source):
        problems.append("/life write route detected")
    return _stage(
        "static_runtime_boundaries",
        not problems,
        "source scan found no scheduler mutation path or /life write route"
        if not problems
        else "; ".join(problems),
    )


def _readonly_profile_stage() -> dict:
    return _stage(
        "m9_revalidation_readonly_profile",
        True,
        "M9.1 revalidates evidence only; only the CLI/report writer emits m9_scheduler_revalidation_report.json",
        details=_readonly_profile(),
    )


def _readonly_profile() -> dict:
    return {
        "name": "M9 read-only scheduler handoff revalidation",
        "readonly_gate": True,
        "writes_report_only": True,
        "wake_cycle_run": False,
        "wake_events_written": False,
        "provider_generation_requested": False,
        "provider_calls": 0,
        "scheduler_mutation_allowed": False,
        "cron_replacement": False,
        "timer_installation": False,
        "service_mutation_allowed": False,
        "life_write_route_allowed": False,
        "voice_signal_hardware_activation_allowed": False,
        "semantic_shadow_authoritative": False,
        "raw_provider_payload_storage_allowed": False,
    }


def _base_report_problems(
    report: dict | None,
    *,
    expected_milestone: str,
    expected_recommendation: str,
) -> list[str]:
    if not isinstance(report, dict):
        return [f"{expected_milestone} report is missing or invalid"]
    problems = []
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
    return problems


def _false_boundary_problems(values: dict, keys: tuple[str, ...], *, label: str) -> list[str]:
    return [f"{label} {key} is true" for key in keys if values.get(key) is True]


def _target_command(paths: CompanionPaths, report: dict | None) -> str:
    if isinstance(report, dict):
        for section in ("handoff", "final_freeze"):
            payload = report.get(section)
            if isinstance(payload, dict) and payload.get("target_command"):
                return str(payload["target_command"])
    return (
        f"cd {shlex.quote(str(paths.home))} && "
        ".venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {shlex.quote(str(paths.home))} "
        "--provider deepseek --memory-mode json --trigger scheduled-wake"
    )


def _cadence_from_design(report: dict | None) -> dict:
    skip_reasons = [
        "paused",
        "quiet_hours",
        "daily_budget_exhausted",
        "min_gap_not_met",
        "wake_lock_active",
        "failure_cooldown",
        "recent_human_chat_dampening",
    ]
    fallback = {
        "model": "randomized_presence_windows",
        "quiet_hours": ["00:00", "08:00"],
        "daily_live_wake_budget": 2,
        "scheduled_wake_output": "internal_only",
        "skip_reasons": skip_reasons,
    }
    if not isinstance(report, dict):
        return fallback
    design = report.get("design") if isinstance(report.get("design"), dict) else {}
    cadence = design.get("cadence") if isinstance(design.get("cadence"), dict) else {}
    if not cadence:
        return fallback
    return {
        "model": cadence.get("model"),
        "quiet_hours": cadence.get("quiet_hours"),
        "daily_live_wake_budget": cadence.get("daily_live_wake_budget"),
        "scheduled_wake_output": cadence.get("scheduled_wake_output"),
        "skip_reasons": skip_reasons,
    }


def _run_readonly_probe(name: str, args: list[str], paths: CompanionPaths) -> dict:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
    except FileNotFoundError:
        return {
            "name": name,
            "command": _shell_command(args),
            "status": "unavailable",
            "matched_lines": [],
            "message": f"{args[0]} is not available",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "command": _shell_command(args),
            "status": "timeout",
            "matched_lines": [],
            "message": "read-only probe timed out",
        }
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    matched_lines = _matched_scheduler_lines(paths, output)
    status = "ok" if completed.returncode == 0 else "error"
    if name == "crontab" and "no crontab for" in output.lower():
        status = "empty"
    return {
        "name": name,
        "command": _shell_command(args),
        "status": status,
        "returncode": completed.returncode,
        "matched_lines": matched_lines,
        "message": "matched project scheduler line(s)" if matched_lines else "no project scheduler lines matched",
    }


def _matched_scheduler_lines(paths: CompanionPaths, text: str) -> list[str]:
    needles = (
        str(paths.home).lower(),
        "run_wake_cycle.py",
        "scheduled-wake",
        "m9_scheduler",
        "scheduler_presence_state.json",
        "scheduler_pause.flag",
    )
    matched = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lower = stripped.lower()
        if any(needle in lower for needle in needles):
            matched.append(_short(stripped))
    return matched[:10]


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


def _stage(name: str, ok: bool, message: str, *, details: dict | None = None) -> dict:
    stage = {"name": name, "status": "pass" if ok else "fail", "message": message}
    if details is not None:
        stage["details"] = details
    return stage


def _stage_passed(stages: list[dict], name: str) -> bool:
    matches = [stage for stage in stages if stage.get("name") == name]
    return bool(matches) and all(stage.get("status") == "pass" for stage in matches)


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def _short(value: str, limit: int = 240) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
