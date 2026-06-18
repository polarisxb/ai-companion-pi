"""M3 final-freeze readiness checks."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from .paths import CompanionPaths


REQUIRED_RELEASE_STAGES = ("predeploy", "trial_summary", "semantic_shadow_authority")


def run_m3_final_freeze(
    paths: CompanionPaths,
    *,
    release_gate_report_path: str | Path | None = None,
    expected_provider: str = "deepseek",
    expected_memory_mode: str = "json",
    expected_trial_trigger: str | None = None,
) -> dict:
    report_path = (
        Path(release_gate_report_path).expanduser().resolve()
        if release_gate_report_path
        else paths.life_loop_dir / "m3_release_gate_report.json"
    )
    release_report, load_stage = _load_release_report(report_path)
    stages = [load_stage]

    if isinstance(release_report, dict):
        stages.append(_release_result_stage(release_report))
        stages.append(_profile_stage(
            release_report,
            expected_provider=expected_provider,
            expected_memory_mode=expected_memory_mode,
            expected_trial_trigger=expected_trial_trigger,
        ))
        stages.append(_required_stages_stage(release_report))
        stages.append(_predeploy_contract_stage(release_report))
        stages.append(_trial_contract_stage(release_report, expected_trial_trigger=expected_trial_trigger))
        stages.append(_semantic_shadow_contract_stage(release_report))
    else:
        stages.extend([
            _stage("release_result", False, required=True, status="skipped", message="release gate report did not load"),
            _stage("deployment_profile", False, required=True, status="skipped", message="release gate report did not load"),
            _stage("required_stages", False, required=True, status="skipped", message="release gate report did not load"),
            _stage("predeploy_contract", False, required=True, status="skipped", message="release gate report did not load"),
            _stage("trial_contract", False, required=True, status="skipped", message="release gate report did not load"),
            _stage("semantic_shadow_contract", False, required=True, status="skipped", message="release gate report did not load"),
        ])

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    return {
        "ok": not stop_reasons,
        "milestone": "M3.26",
        "recommendation": "m3_frozen_ready_for_m4" if not stop_reasons else "inspect",
        "companion_home": str(paths.home),
        "release_gate_report": _relative(paths, report_path),
        "deployment_contract": {
            "provider": expected_provider,
            "memory_mode": expected_memory_mode,
            "cron_replacement": False,
            "real_wake_in_freeze": False,
            "semantic_shadow_authoritative": False,
            "raw_output_storage": "hash_only",
        },
        "frozen_commands": {
            "release_gate": _release_gate_command(
                paths,
                release_report if isinstance(release_report, dict) else None,
                expected_provider=expected_provider,
                expected_memory_mode=expected_memory_mode,
                expected_trial_trigger=expected_trial_trigger,
            ),
            "final_freeze": _final_freeze_command(
                paths,
                expected_provider=expected_provider,
                expected_memory_mode=expected_memory_mode,
                expected_trial_trigger=expected_trial_trigger,
            ),
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
    }


def _release_gate_command(
    paths: CompanionPaths,
    report: dict | None,
    *,
    expected_provider: str,
    expected_memory_mode: str,
    expected_trial_trigger: str | None,
) -> str:
    profile = report.get("profile") if isinstance(report, dict) and isinstance(report.get("profile"), dict) else {}
    smoke_home = report.get("smoke_home") if isinstance(report, dict) else None
    trigger = expected_trial_trigger or profile.get("trial_since_trigger")
    trial_limit = profile.get("trial_limit") or 1
    args = [
        "python3",
        "scripts/run_m3_release_gate.py",
        "--companion-home",
        str(paths.home),
        "--smoke-home",
        smoke_home or "/tmp/companion-m325-release-gate-smoke",
        "--provider",
        expected_provider,
        "--memory-mode",
        expected_memory_mode,
    ]
    if trigger:
        args.extend(["--since-trigger", trigger, "--trial-limit", str(trial_limit)])
    return _shell_command(args)


def _final_freeze_command(
    paths: CompanionPaths,
    *,
    expected_provider: str,
    expected_memory_mode: str,
    expected_trial_trigger: str | None,
) -> str:
    args = [
        "python3",
        "scripts/run_m3_final_freeze.py",
        "--companion-home",
        str(paths.home),
        "--expected-provider",
        expected_provider,
        "--expected-memory-mode",
        expected_memory_mode,
    ]
    if expected_trial_trigger:
        args.extend(["--expected-trial-trigger", expected_trial_trigger])
    return _shell_command(args)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def _load_release_report(path: Path) -> tuple[dict | None, dict]:
    if not path.exists():
        return None, _stage(
            "release_gate_report",
            False,
            required=True,
            message=f"release gate report is missing: {path}",
        )
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return None, _stage(
            "release_gate_report",
            False,
            required=True,
            message=f"release gate report is invalid JSON: {exc.msg}",
        )
    except OSError as exc:
        return None, _stage(
            "release_gate_report",
            False,
            required=True,
            message=f"release gate report could not be read: {exc}",
        )
    if not isinstance(payload, dict):
        return None, _stage(
            "release_gate_report",
            False,
            required=True,
            message="release gate report must be a JSON object",
        )
    return payload, _stage(
        "release_gate_report",
        True,
        required=True,
        message="release gate report loaded",
        details={
            "path": str(path),
            "milestone": payload.get("milestone"),
            "recommendation": payload.get("recommendation"),
            "saved_at": payload.get("saved_at"),
        },
    )


def _release_result_stage(report: dict) -> dict:
    problems = []
    if report.get("ok") is not True:
        problems.append("release gate ok is not true")
    if report.get("milestone") != "M3.25":
        problems.append("release gate milestone is not M3.25")
    if report.get("recommendation") != "ready_for_m4":
        problems.append("release gate recommendation is not ready_for_m4")
    return _stage(
        "release_result",
        not problems,
        required=True,
        message="release gate is ready_for_m4" if not problems else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _profile_stage(
    report: dict,
    *,
    expected_provider: str,
    expected_memory_mode: str,
    expected_trial_trigger: str | None,
) -> dict:
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    problems = []
    if profile.get("provider") != expected_provider:
        problems.append(f"provider is {profile.get('provider')!r}, expected {expected_provider!r}")
    if profile.get("memory_mode") != expected_memory_mode:
        problems.append(f"memory_mode is {profile.get('memory_mode')!r}, expected {expected_memory_mode!r}")
    if profile.get("cron_replacement") is not False:
        problems.append("cron_replacement must be false")
    if expected_trial_trigger is not None and profile.get("trial_since_trigger") != expected_trial_trigger:
        problems.append(
            f"trial_since_trigger is {profile.get('trial_since_trigger')!r}, "
            f"expected {expected_trial_trigger!r}"
        )
    return _stage(
        "deployment_profile",
        not problems,
        required=True,
        message="deployment profile is frozen" if not problems else "; ".join(problems),
        details={
            "expected_provider": expected_provider,
            "expected_memory_mode": expected_memory_mode,
            "expected_trial_trigger": expected_trial_trigger,
            "actual": profile,
        },
    )


def _required_stages_stage(report: dict) -> dict:
    stages = _stages_by_name(report)
    problems = []
    for name in REQUIRED_RELEASE_STAGES:
        stage = stages.get(name)
        if stage is None:
            problems.append(f"missing release stage: {name}")
        elif stage.get("ok") is not True:
            problems.append(f"release stage did not pass: {name}")
    return _stage(
        "required_stages",
        not problems,
        required=True,
        message="all required release stages passed" if not problems else "; ".join(problems),
        details={"required": list(REQUIRED_RELEASE_STAGES), "present": sorted(stages)},
    )


def _predeploy_contract_stage(report: dict) -> dict:
    predeploy = _stages_by_name(report).get("predeploy", {})
    details = predeploy.get("details") if isinstance(predeploy.get("details"), dict) else {}
    profile = details.get("profile") if isinstance(details.get("profile"), dict) else {}
    nested_stages = _stages_by_name(details)
    real_wake = nested_stages.get("real_wake", {})
    raw_output = nested_stages.get("raw_output_storage", {})
    problems = []
    if profile.get("cron_replacement") is not False:
        problems.append("predeploy cron_replacement must be false")
    if profile.get("real_wake_requested") is not False:
        problems.append("predeploy must not request a real wake")
    if profile.get("raw_output_storage_required") != "hash_only":
        problems.append("predeploy raw output storage must be hash_only")
    if real_wake.get("status") != "skipped" or real_wake.get("ok") is not True:
        problems.append("predeploy real_wake stage must be skipped and ok")
    if raw_output.get("ok") is not True:
        problems.append("predeploy raw_output_storage stage must pass")
    return _stage(
        "predeploy_contract",
        not problems,
        required=True,
        message="predeploy contract is frozen" if not problems else "; ".join(problems),
        details={
            "profile": profile,
            "real_wake": real_wake,
            "raw_output_storage": raw_output,
        },
    )


def _trial_contract_stage(report: dict, *, expected_trial_trigger: str | None) -> dict:
    trial = _stages_by_name(report).get("trial_summary", {})
    details = trial.get("details") if isinstance(trial.get("details"), dict) else {}
    problems = []
    if details.get("ok") is not True:
        problems.append("trial summary ok is not true")
    if details.get("recommendation") != "continue":
        problems.append("trial summary recommendation is not continue")
    if details.get("events_considered", 0) < 1:
        problems.append("trial summary must include at least one event")
    if details.get("failed", 0) != 0:
        problems.append("trial summary failed count must be 0")
    if details.get("context_rejection_count", 0) != 0:
        problems.append("trial summary context_rejection_count must be 0")
    if details.get("blocking_quality_warning_count", 0) != 0:
        problems.append("trial summary blocking_quality_warning_count must be 0")
    if details.get("memory_write_failures", 0) != 0:
        problems.append("trial summary memory_write_failures must be 0")
    if details.get("stop_reasons"):
        problems.append("trial summary stop_reasons must be empty")
    if expected_trial_trigger is not None and details.get("since_trigger") != expected_trial_trigger:
        problems.append(
            f"trial summary since_trigger is {details.get('since_trigger')!r}, "
            f"expected {expected_trial_trigger!r}"
        )
    semantic_shadow = details.get("semantic_shadow") if isinstance(details.get("semantic_shadow"), dict) else {}
    if semantic_shadow.get("failed", 0) != 0:
        problems.append("semantic shadow failed count must be 0 for M3 freeze")
    return _stage(
        "trial_contract",
        not problems,
        required=True,
        message="trial contract is frozen" if not problems else "; ".join(problems),
        details={
            "events_considered": details.get("events_considered"),
            "latest_event": details.get("latest_event"),
            "latest_trigger": details.get("latest_trigger"),
            "since_trigger": details.get("since_trigger"),
            "semantic_shadow": semantic_shadow,
        },
    )


def _semantic_shadow_contract_stage(report: dict) -> dict:
    shadow = _stages_by_name(report).get("semantic_shadow_authority", {})
    details = shadow.get("details") if isinstance(shadow.get("details"), dict) else {}
    problems = []
    if shadow.get("ok") is not True:
        problems.append("semantic shadow authority stage did not pass")
    if details.get("ok") is not True:
        problems.append("semantic shadow authority details ok is not true")
    if details.get("problems"):
        problems.append("semantic shadow authority problems must be empty")
    return _stage(
        "semantic_shadow_contract",
        not problems,
        required=True,
        message="semantic shadow remains non-authoritative" if not problems else "; ".join(problems),
        details={
            "main_memory_count": details.get("main_memory_count"),
            "shadow_memory_count": details.get("shadow_memory_count"),
            "main_store": details.get("main_store"),
            "shadow_store": details.get("shadow_store"),
            "problems": details.get("problems", []),
        },
    )


def _stages_by_name(report: dict) -> dict[str, dict]:
    stages = report.get("stages")
    if not isinstance(stages, list):
        return {}
    return {
        stage["name"]: stage
        for stage in stages
        if isinstance(stage, dict) and isinstance(stage.get("name"), str)
    }


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _stage(
    name: str,
    ok: bool,
    *,
    required: bool,
    message: str,
    status: str | None = None,
    details: dict | None = None,
) -> dict:
    stage = {
        "name": name,
        "status": status or ("passed" if ok else "failed"),
        "ok": ok,
        "required": required,
        "message": message,
    }
    if details is not None:
        stage["details"] = details
    return stage
