"""Pi predeploy readiness and smoke orchestration for the internal life loop."""

from __future__ import annotations

import shutil

from .lifecycle import LifeLoopRunner
from .llm import FakeLLMClient, create_llm_client
from .memory import JsonMemoryStore, SemanticFirstMemoryStore
from .output_archive import should_store_raw_outputs
from .paths import CompanionPaths
from .readiness import check_runtime_readiness
from .replay_regression import build_replay_regression_report
from .secrets import load_local_secrets
from .trial_summary import build_trial_summary

CONTEXT_FILE_NAMES = ("who_is_companion.txt", "who_is_human.txt", "now.txt")


def run_pi_predeploy_check(
    paths: CompanionPaths,
    *,
    smoke_paths: CompanionPaths,
    provider: str = "deepseek",
    memory_mode: str = "json",
    trigger: str = "m321-pi-predeploy",
    run_provider_check: bool = True,
    run_real_wake: bool = False,
    allow_raw_output_storage: bool = False,
    claude_bin: str = "claude",
    readiness_timeout_seconds: int = 10,
    wake_timeout_seconds: int = 300,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str = "COMPANION_LLM_API_KEY",
) -> dict:
    """Run the M3.21 Pi predeploy profile.

    The target ``paths`` are used for readiness checks and optional real wakes.
    Fake wake and replay regression run in ``smoke_paths`` so predeploy does not
    pollute the real companion journal, memory, request, or wake-event stores.
    """

    stages: list[dict] = []
    readiness = check_runtime_readiness(
        paths,
        provider=provider,
        memory_mode=memory_mode,
        claude_bin=claude_bin,
        timeout_seconds=readiness_timeout_seconds,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        run_provider_check=run_provider_check,
    )
    stages.append(_stage(
        "readiness",
        readiness["ok"],
        required=True,
        message="target runtime readiness passed" if readiness["ok"] else "target runtime readiness failed",
        details=readiness,
    ))

    raw_enabled = should_store_raw_outputs()
    raw_ok = allow_raw_output_storage or not raw_enabled
    stages.append(_stage(
        "raw_output_storage",
        raw_ok,
        required=True,
        status="passed" if not raw_enabled else ("warning" if allow_raw_output_storage else "failed"),
        message=(
            "raw model output storage is disabled"
            if not raw_enabled
            else "raw model output storage is explicitly allowed"
            if allow_raw_output_storage
            else "raw model output storage is enabled; disable COMPANION_STORE_RAW_OUTPUTS for Pi predeploy"
        ),
        details={
            "raw_output_storage_enabled": raw_enabled,
            "allow_raw_output_storage": allow_raw_output_storage,
        },
    ))

    prerequisites_ok = readiness["ok"] and raw_ok
    smoke_prepared = False
    if prerequisites_ok:
        prepare_stage = _prepare_smoke_home(paths, smoke_paths)
        stages.append(prepare_stage)
        smoke_prepared = prepare_stage["ok"]
    else:
        stages.append(_stage(
            "prepare_smoke_home",
            False,
            required=True,
            status="skipped",
            message="skipped because predeploy prerequisites failed",
        ))

    if smoke_prepared:
        stages.append(_run_fake_wake_smoke(
            smoke_paths,
            trigger=trigger,
            allow_raw_output_storage=allow_raw_output_storage,
        ))
        stages.append(_run_replay_regression(smoke_paths))
    else:
        stages.append(_stage(
            "fake_wake_smoke",
            False,
            required=True,
            status="skipped",
            message="skipped because smoke home was not prepared",
        ))
        stages.append(_stage(
            "replay_regression",
            False,
            required=True,
            status="skipped",
            message="skipped because smoke home was not prepared",
        ))

    if run_real_wake:
        if prerequisites_ok:
            stages.append(_run_real_wake(
                paths,
                provider=provider,
                memory_mode=memory_mode,
                trigger=trigger,
                allow_raw_output_storage=allow_raw_output_storage,
                claude_bin=claude_bin,
                timeout_seconds=wake_timeout_seconds,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            ))
        else:
            stages.append(_stage(
                "real_wake",
                False,
                required=True,
                status="skipped",
                message="skipped because predeploy prerequisites failed",
            ))
    else:
        stages.append(_stage(
            "real_wake",
            True,
            required=False,
            status="skipped",
            message="real provider wake was not requested",
        ))

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    return {
        "ok": not stop_reasons,
        "profile": {
            "name": "pi-json" if memory_mode == "json" else "pi-dual",
            "provider": provider,
            "memory_mode": memory_mode,
            "cron_replacement": False,
            "real_wake_requested": run_real_wake,
            "raw_output_storage_required": "hash_only" if not allow_raw_output_storage else "explicitly_allowed",
        },
        "companion_home": str(paths.home),
        "smoke_home": str(smoke_paths.home),
        "trigger": trigger,
        "stages": stages,
        "stop_reasons": stop_reasons,
        "recommendation": "ready" if not stop_reasons else "inspect",
    }


def _prepare_smoke_home(paths: CompanionPaths, smoke_paths: CompanionPaths) -> dict:
    if smoke_paths.home == paths.home:
        return _stage(
            "prepare_smoke_home",
            False,
            required=True,
            message="smoke home must be different from companion home",
    )
    try:
        cleaned = _reset_smoke_runtime(smoke_paths)
        smoke_paths.ensure_runtime_dirs()
        copied = []
        for filename in CONTEXT_FILE_NAMES:
            source = paths.context_file(filename)
            target = smoke_paths.context_file(filename)
            target.write_text(source.read_text())
            copied.append(filename)
    except Exception as exc:
        return _stage(
            "prepare_smoke_home",
            False,
            required=True,
            message=f"failed to prepare smoke home: {type(exc).__name__}: {exc}",
        )
    return _stage(
        "prepare_smoke_home",
        True,
        required=True,
        message="isolated smoke home prepared",
        details={
            "copied_context_files": copied,
            "cleaned_runtime_paths": cleaned,
        },
    )


def _reset_smoke_runtime(smoke_paths: CompanionPaths) -> list[str]:
    cleaned = []
    runtime_paths = [
        smoke_paths.journals_dir,
        smoke_paths.life_loop_dir,
        smoke_paths.memory_store,
        smoke_paths.memory_store.with_name("memory_store.lock"),
        smoke_paths.requests_file,
        smoke_paths.requests_file.with_suffix(".lock"),
        smoke_paths.status_file,
    ]
    for path in runtime_paths:
        if path.is_dir():
            shutil.rmtree(path)
            cleaned.append(str(path.relative_to(smoke_paths.home)))
        elif path.exists():
            path.unlink()
            cleaned.append(str(path.relative_to(smoke_paths.home)))
    return cleaned


def _run_fake_wake_smoke(
    smoke_paths: CompanionPaths,
    *,
    trigger: str,
    allow_raw_output_storage: bool,
) -> dict:
    try:
        result = LifeLoopRunner(
            smoke_paths,
            llm_client=FakeLLMClient(),
            memory_store=JsonMemoryStore(smoke_paths.memory_store),
        ).run_once(trigger=f"{trigger}-fake", provider="fake")
    except Exception as exc:
        return _stage(
            "fake_wake_smoke",
            False,
            required=True,
            message=f"fake wake failed: {type(exc).__name__}: {exc}",
        )

    event = result.event or {}
    audit = event.get("output_audit", {})
    problems = []
    if event.get("status") != "completed":
        problems.append("event did not complete")
    if event.get("quality_gate", {}).get("context_eligible") is not True:
        problems.append("context gate did not accept fake wake")
    if not allow_raw_output_storage:
        if audit.get("raw_output_storage") != "hash_only":
            problems.append("output audit is not hash_only")
        if audit.get("initial", {}).get("raw_output_stored") is True:
            problems.append("initial raw output was stored")
        if audit.get("final", {}).get("raw_output_stored") is True:
            problems.append("final raw output was stored")
    return _stage(
        "fake_wake_smoke",
        not problems,
        required=True,
        message="fake wake smoke passed" if not problems else "; ".join(problems),
        details={
            "event_id": event.get("id"),
            "journal": str(result.journal_path),
            "quality_gate": event.get("quality_gate"),
            "grounding": event.get("grounding"),
            "output_audit": audit,
        },
    )


def _run_replay_regression(smoke_paths: CompanionPaths) -> dict:
    try:
        report = build_replay_regression_report(smoke_paths)
    except Exception as exc:
        return _stage(
            "replay_regression",
            False,
            required=True,
            message=f"replay regression failed: {type(exc).__name__}: {exc}",
        )
    return _stage(
        "replay_regression",
        report["ok"],
        required=True,
        message="replay regression passed" if report["ok"] else "replay regression failed",
        details=report,
    )


def _run_real_wake(
    paths: CompanionPaths,
    *,
    provider: str,
    memory_mode: str,
    trigger: str,
    allow_raw_output_storage: bool,
    claude_bin: str,
    timeout_seconds: int,
    model: str | None,
    base_url: str | None,
    api_key_env: str,
) -> dict:
    real_trigger = f"{trigger}-real"
    try:
        load_local_secrets(paths)
        runner = LifeLoopRunner(
            paths,
            llm_client=create_llm_client(
                provider,
                claude_bin=claude_bin,
                timeout_seconds=timeout_seconds,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            ),
            memory_store=_create_memory_store(paths, memory_mode),
        )
        result = runner.run_once(trigger=real_trigger, provider=provider)
        summary = build_trial_summary(paths, limit=5, since_trigger=real_trigger)
    except Exception as exc:
        summary = build_trial_summary(paths, limit=5, since_trigger=real_trigger)
        return _stage(
            "real_wake",
            False,
            required=True,
            message=f"real wake failed: {type(exc).__name__}: {exc}",
            details={"summary": summary},
        )

    event = result.event or {}
    audit = event.get("output_audit", {})
    problems = []
    if not summary.get("ok"):
        problems.extend(str(reason) for reason in summary.get("stop_reasons", []))
    if not allow_raw_output_storage:
        if audit.get("raw_output_storage") != "hash_only":
            problems.append("output audit is not hash_only")
        if audit.get("initial", {}).get("raw_output_stored") is True:
            problems.append("initial raw output was stored")
        if audit.get("final", {}).get("raw_output_stored") is True:
            problems.append("final raw output was stored")
    return _stage(
        "real_wake",
        not problems,
        required=True,
        message="real wake passed" if not problems else "; ".join(problems),
        details={
            "event_id": event.get("id"),
            "quality_gate": event.get("quality_gate"),
            "grounding": event.get("grounding"),
            "output_audit": audit,
            "summary": summary,
        },
    )


def _create_memory_store(paths: CompanionPaths, memory_mode: str):
    if memory_mode == "dual":
        return SemanticFirstMemoryStore(paths.memory_store)
    return JsonMemoryStore(paths.memory_store)


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
