"""M5.7 companion-quality final freeze."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from .paths import CompanionPaths


EXPECTED_PROVIDER = "deepseek"
EXPECTED_MEMORY_MODE = "json"
REQUIRED_M5_RELEASE_STAGES = (
    "m4_post_change_guard",
    "m5_quality_report",
    "m5_trial_report",
    "m5_quality_stages",
    "m5_trial_profile",
    "m5_trial_attempts",
    "m5_quality_contract",
    "semantic_shadow_authority",
    "audit_anomalies",
)


def run_m5_final_freeze(
    paths: CompanionPaths,
    *,
    release_gate_report_path: str | Path | None = None,
    expected_provider: str = EXPECTED_PROVIDER,
    expected_memory_mode: str = EXPECTED_MEMORY_MODE,
) -> dict:
    """Freeze M5 after a passing M5.6 quality release gate.

    This final freeze is non-generative. It reads the M5.6 report only and
    records the frozen quality contract for the next milestone.
    """

    report_path = (
        Path(release_gate_report_path).expanduser().resolve()
        if release_gate_report_path
        else paths.life_loop_dir / "m5_quality_release_report.json"
    )
    release_report, load_stage = _load_release_report(paths, report_path)
    stages = [load_stage]

    if isinstance(release_report, dict):
        stages.append(_release_result_stage(release_report))
        stages.append(_frozen_profile_stage(
            release_report,
            expected_provider=expected_provider,
            expected_memory_mode=expected_memory_mode,
        ))
        stages.append(_required_release_stages_stage(release_report))
        stages.append(_source_report_contract_stage(release_report))
        stages.append(_audit_contract_stage(release_report))
    else:
        stages.extend([
            _stage("release_result", False, True, "M5.6 release report did not load", status="skipped"),
            _stage("frozen_profile", False, True, "M5.6 release report did not load", status="skipped"),
            _stage("required_release_stages", False, True, "M5.6 release report did not load", status="skipped"),
            _stage("source_report_contract", False, True, "M5.6 release report did not load", status="skipped"),
            _stage("audit_contract", False, True, "M5.6 release report did not load", status="skipped"),
        ])

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    ok = not stop_reasons
    min_trial_cycles = _min_trial_cycles(release_report)
    return {
        "ok": ok,
        "milestone": "M5.7",
        "recommendation": "m5_frozen_ready_for_m6" if ok else "inspect",
        "companion_home": str(paths.home),
        "release_gate_report": _relative(paths, report_path),
        "quality_contract": {
            "provider": expected_provider,
            "memory_mode": expected_memory_mode,
            "cron_replacement": False,
            "semantic_shadow_authoritative": False,
            "real_wake_in_freeze": False,
            "provider_generation_in_freeze": False,
            "dashboard_write_allowed": False,
            "raw_output_storage": "hash_only",
            "canonical_m5_trial_cycles": min_trial_cycles,
            "advisory_audit_anomalies_allowed": True,
            "blocking_audit_anomalies_allowed": False,
        },
        "frozen_commands": {
            "quality_release_gate": _quality_release_command(paths, min_trial_cycles=min_trial_cycles),
            "final_freeze": _final_freeze_command(
                paths,
                expected_provider=expected_provider,
                expected_memory_mode=expected_memory_mode,
            ),
            "m4_post_change_guard": _shell_command([
                "python3",
                "scripts/run_m4_post_change_guard.py",
                "--companion-home",
                str(paths.home),
            ]),
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
    }


def _load_release_report(paths: CompanionPaths, path: Path) -> tuple[dict | None, dict]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return None, _stage("release_gate_report", False, True, f"release gate report is missing: {path}")
    except json.JSONDecodeError as exc:
        return None, _stage("release_gate_report", False, True, f"release gate report is invalid JSON: {exc.msg}")
    except OSError as exc:
        return None, _stage("release_gate_report", False, True, f"release gate report could not be read: {exc}")
    if not isinstance(payload, dict):
        return None, _stage("release_gate_report", False, True, f"release gate report must be a JSON object: {path}")
    return payload, _stage(
        "release_gate_report",
        True,
        True,
        "M5.6 release gate report loaded",
        details={
            "path": _relative(paths, path),
            "ok": payload.get("ok"),
            "milestone": payload.get("milestone"),
            "recommendation": payload.get("recommendation"),
            "saved_at": payload.get("saved_at"),
        },
    )


def _release_result_stage(report: dict) -> dict:
    problems = []
    if report.get("ok") is not True:
        problems.append("M5.6 release gate ok is not true")
    if report.get("milestone") != "M5.6":
        problems.append("M5.6 release gate milestone is not M5.6")
    if report.get("recommendation") != "m5_quality_ready_for_m6":
        problems.append("M5.6 release gate recommendation is not m5_quality_ready_for_m6")
    if report.get("stop_reasons"):
        problems.append("M5.6 release gate stop_reasons must be empty")
    return _stage(
        "release_result",
        not problems,
        True,
        "M5.6 release gate is ready for final freeze" if not problems else "; ".join(problems),
        details={
            "ok": report.get("ok"),
            "milestone": report.get("milestone"),
            "recommendation": report.get("recommendation"),
            "stop_reasons": report.get("stop_reasons", []),
        },
    )


def _frozen_profile_stage(
    report: dict,
    *,
    expected_provider: str,
    expected_memory_mode: str,
) -> dict:
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    problems = []
    if profile.get("provider") != expected_provider:
        problems.append(f"provider is {profile.get('provider')!r}, expected {expected_provider!r}")
    if profile.get("memory_mode") != expected_memory_mode:
        problems.append(f"memory_mode is {profile.get('memory_mode')!r}, expected {expected_memory_mode!r}")
    if profile.get("cron_replacement") is not False:
        problems.append("cron_replacement must be false")
    if profile.get("semantic_shadow_authoritative") is not False:
        problems.append("semantic_shadow_authoritative must be false")
    if profile.get("real_wake_requested") is not False:
        problems.append("M5.6 release gate must not request a real wake")
    if profile.get("provider_generation_requested") is not False:
        problems.append("M5.6 release gate must not request provider generation")
    if profile.get("raw_output_storage_required") != "hash_only":
        problems.append("raw_output_storage_required must be hash_only")
    if profile.get("dashboard_write_allowed") is not False:
        problems.append("dashboard_write_allowed must be false")
    return _stage(
        "frozen_profile",
        not problems,
        True,
        "M5 quality profile is frozen" if not problems else "; ".join(problems),
        details={
            "expected_provider": expected_provider,
            "expected_memory_mode": expected_memory_mode,
            "profile": profile,
        },
    )


def _required_release_stages_stage(report: dict) -> dict:
    stages = _stages_by_name(report)
    problems = []
    for name in REQUIRED_M5_RELEASE_STAGES:
        stage = stages.get(name)
        if stage is None:
            problems.append(f"missing M5.6 release stage: {name}")
        elif stage.get("ok") is not True:
            problems.append(f"M5.6 release stage did not pass: {name}")
    return _stage(
        "required_release_stages",
        not problems,
        True,
        "all required M5.6 release stages passed" if not problems else "; ".join(problems),
        details={
            "required": list(REQUIRED_M5_RELEASE_STAGES),
            "present": sorted(stages),
        },
    )


def _source_report_contract_stage(report: dict) -> dict:
    sources = report.get("source_reports") if isinstance(report.get("source_reports"), dict) else {}
    required = {
        "m4_post_change_guard": ("M4.7", "m4_still_deployable"),
        "m5_quality": ("M5.1", "ready_for_quality_tuning"),
        "m5_trial": ("M5.5", "continue_quality_observation"),
    }
    problems = []
    details = {}
    for name, (milestone, recommendation) in required.items():
        source = sources.get(name) if isinstance(sources.get(name), dict) else {}
        details[name] = source
        if source.get("loaded") is not True:
            problems.append(f"{name} source report was not loaded")
        if source.get("ok") is not True:
            problems.append(f"{name} source report ok is not true")
        if source.get("milestone") != milestone:
            problems.append(f"{name} source milestone is not {milestone}")
        if source.get("recommendation") != recommendation:
            problems.append(f"{name} source recommendation is not {recommendation}")
    return _stage(
        "source_report_contract",
        not problems,
        True,
        "M4/M5 source reports are frozen" if not problems else "; ".join(problems),
        details=details,
    )


def _audit_contract_stage(report: dict) -> dict:
    audit = report.get("audit_anomalies") if isinstance(report.get("audit_anomalies"), dict) else {}
    problems = []
    if _count_int(audit.get("canonical_event_count")) < _min_trial_cycles(report):
        problems.append("canonical M5.5 event count is below frozen minimum")
    if audit.get("missing_canonical_event_ids"):
        problems.append("canonical M5.5 event ids are missing from wake_events")
    if _count_int(audit.get("blocking_anomaly_count")) != 0:
        problems.append("blocking M5.5 audit anomalies must be 0")
    return _stage(
        "audit_contract",
        not problems,
        True,
        "M5.5 audit anomalies are advisory-only" if not problems else "; ".join(problems),
        details={
            "trigger_prefix": audit.get("trigger_prefix"),
            "canonical_event_count": audit.get("canonical_event_count"),
            "same_trigger_event_count": audit.get("same_trigger_event_count"),
            "extra_event_count": audit.get("extra_event_count"),
            "advisory_anomaly_count": audit.get("advisory_anomaly_count"),
            "blocking_anomaly_count": audit.get("blocking_anomaly_count"),
            "missing_canonical_event_ids": audit.get("missing_canonical_event_ids", []),
        },
    )


def _min_trial_cycles(report: dict | None) -> int:
    if not isinstance(report, dict):
        return 3
    profile = report.get("profile") if isinstance(report.get("profile"), dict) else {}
    value = profile.get("min_trial_cycles")
    return value if type(value) is int and value > 0 else 3


def _quality_release_command(paths: CompanionPaths, *, min_trial_cycles: int) -> str:
    return _shell_command([
        "python3",
        "scripts/run_m5_quality_release_gate.py",
        "--companion-home",
        str(paths.home),
        "--min-trial-cycles",
        str(min_trial_cycles),
    ])


def _final_freeze_command(
    paths: CompanionPaths,
    *,
    expected_provider: str,
    expected_memory_mode: str,
) -> str:
    return _shell_command([
        "python3",
        "scripts/run_m5_final_freeze.py",
        "--companion-home",
        str(paths.home),
        "--expected-provider",
        expected_provider,
        "--expected-memory-mode",
        expected_memory_mode,
    ])


def _stages_by_name(report: dict) -> dict[str, dict]:
    stages = report.get("stages")
    if not isinstance(stages, list):
        return {}
    return {
        stage["name"]: stage
        for stage in stages
        if isinstance(stage, dict) and isinstance(stage.get("name"), str)
    }


def _stage(
    name: str,
    ok: bool,
    required: bool,
    message: str,
    *,
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


def _count_int(value) -> int:
    return value if type(value) is int else 0


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)


def _shell_command(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)
