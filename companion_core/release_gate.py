"""M3 deployment-candidate release gate."""

from __future__ import annotations

import json

from .paths import CompanionPaths
from .predeploy import run_pi_predeploy_check
from .trial_summary import build_trial_summary


def run_m3_release_gate(
    paths: CompanionPaths,
    *,
    smoke_paths: CompanionPaths,
    provider: str = "deepseek",
    memory_mode: str = "json",
    trial_since_trigger: str | None = None,
    trial_limit: int = 5,
    run_provider_check: bool = False,
) -> dict:
    stages = []

    predeploy = run_pi_predeploy_check(
        paths,
        smoke_paths=smoke_paths,
        provider=provider,
        memory_mode=memory_mode,
        trigger="m325-release-gate",
        run_provider_check=run_provider_check,
        run_real_wake=False,
    )
    stages.append(_stage(
        "predeploy",
        predeploy.get("ok") is True,
        required=True,
        message="predeploy passed" if predeploy.get("ok") is True else "predeploy failed",
        details=predeploy,
    ))

    if trial_since_trigger:
        trial_summary = build_trial_summary(
            paths,
            limit=trial_limit,
            since_trigger=trial_since_trigger,
        )
        stages.append(_stage(
            "trial_summary",
            trial_summary.get("ok") is True,
            required=True,
            message=(
                "trial summary passed"
                if trial_summary.get("ok") is True
                else "trial summary recommends stop"
            ),
            details=trial_summary,
        ))
    else:
        stages.append(_stage(
            "trial_summary",
            True,
            required=False,
            status="skipped",
            message="no trial trigger supplied",
        ))

    shadow_audit = audit_semantic_shadow_authority(paths)
    stages.append(_stage(
        "semantic_shadow_authority",
        shadow_audit.get("ok") is True,
        required=True,
        message=shadow_audit.get("message", "semantic shadow authority audit completed"),
        details=shadow_audit,
    ))

    stop_reasons = [
        f"{stage['name']}: {stage['message']}"
        for stage in stages
        if stage["required"] and not stage["ok"]
    ]
    return {
        "ok": not stop_reasons,
        "milestone": "M3.25",
        "recommendation": "ready_for_m4" if not stop_reasons else "inspect",
        "profile": {
            "provider": provider,
            "memory_mode": memory_mode,
            "cron_replacement": False,
            "trial_since_trigger": trial_since_trigger,
            "trial_limit": trial_limit,
        },
        "companion_home": str(paths.home),
        "smoke_home": str(smoke_paths.home),
        "stages": stages,
        "stop_reasons": stop_reasons,
    }


def audit_semantic_shadow_authority(paths: CompanionPaths) -> dict:
    problems = []
    main_memories = _load_json_list(paths.memory_store, missing_ok=True)
    shadow_memories = _load_json_list(paths.semantic_shadow_store, missing_ok=True)

    if isinstance(main_memories, dict):
        problems.append(f"main memory store invalid: {main_memories['error']}")
        main_count = 0
    else:
        main_count = len(main_memories)
        for memory in main_memories:
            if isinstance(memory, dict) and memory.get("shadow_mode") is True:
                problems.append(f"main memory contains shadow_mode record: {memory.get('id', 'unknown')}")

    if isinstance(shadow_memories, dict):
        problems.append(f"semantic shadow store invalid: {shadow_memories['error']}")
        shadow_count = 0
    else:
        shadow_count = len(shadow_memories)
        for memory in shadow_memories:
            if not isinstance(memory, dict):
                problems.append("semantic shadow store contains a non-object record")
                continue
            memory_id = memory.get("id", "unknown")
            if memory.get("prompt_eligible") is True:
                problems.append(f"shadow memory is prompt_eligible: {memory_id}")
            if memory.get("accepted_for_context") is True:
                problems.append(f"shadow memory is accepted_for_context: {memory_id}")
            if memory.get("shadow_mode") is not True:
                problems.append(f"shadow memory is missing shadow_mode=true: {memory_id}")

    return {
        "ok": not problems,
        "message": "semantic shadow authority is isolated" if not problems else "; ".join(problems),
        "main_memory_count": main_count,
        "shadow_memory_count": shadow_count,
        "main_store": _relative(paths, paths.memory_store),
        "shadow_store": _relative(paths, paths.semantic_shadow_store),
        "problems": problems,
    }


def _load_json_list(path, *, missing_ok: bool):
    if not path.exists():
        return [] if missing_ok else {"error": f"{path} is missing"}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc.msg}"}
    except OSError as exc:
        return {"error": str(exc)}
    if not isinstance(payload, list):
        return {"error": "expected JSON list"}
    return payload


def _relative(paths: CompanionPaths, path) -> str:
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
