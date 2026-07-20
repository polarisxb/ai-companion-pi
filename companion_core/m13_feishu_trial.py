"""M13.2 supervised real Feishu reply trial gate.

Mirrors M10.2 for the Feishu channel: one bounded receive/reply pass under
the Feishu loop lock, after verifying M13.1 dry-run evidence, upstream
M7/M8/M9 freezes, feishu config, transport readiness, and explicit operator
confirmation. Passing requires at least one allowlisted reply and zero
failures, all records labeled ``channel=feishu``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_BOUNDARIES,
    SignalChatBridge,
    SignalChatConfigError,
    SignalChatLockError,
    load_feishu_chat_config,
    load_m10_freeze_evidence,
)

READY_RECOMMENDATION = "m13_feishu_trial_ready"


@dataclass
class M13FeishuTrialResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m13_feishu_trial(
    paths: CompanionPaths,
    *,
    transport,
    dialogue_runner,
    provider: str,
    memory_mode: str = "json",
    confirm_real_feishu_send: bool = False,
    max_polls: int = 1,
    now: datetime | None = None,
) -> M13FeishuTrialResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    dry_run_path = paths.life_loop_dir / "m13_feishu_dry_run_report.json"
    dry_run_report = _load_report(dry_run_path)
    source_reports["m13_feishu_dry_run"] = _report_snapshot(paths, dry_run_path, dry_run_report)
    stages.append(_source_report_stage(
        dry_run_report,
        milestone="M13.1",
        recommendation="m13_feishu_dry_run_ready",
    ))

    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_freeze_stage(freeze_evidence))

    config = None
    try:
        config = load_feishu_chat_config(paths)
        stages.append(_stage(
            "config_ready",
            True,
            f"feishu chat config loaded with {len(config.allowed_senders)} allowlisted open_id(s)",
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

    paused = paths.signal_chat_pause_flag.exists()
    stages.append(_stage(
        "pause_flag_clear",
        not paused,
        "pause flag is absent" if not paused else f"pause flag present at {paths.signal_chat_pause_flag}",
    ))

    transport_name = getattr(transport, "name", type(transport).__name__)
    transport_error = None
    check_available = getattr(transport, "check_available", None)
    if callable(check_available):
        try:
            check_available()
        except Exception as exc:  # noqa: BLE001 - availability failures become stage evidence.
            transport_error = f"{type(exc).__name__}: {exc}"
    stages.append(_stage(
        "transport_ready",
        transport_error is None,
        f"transport '{transport_name}' is ready" if transport_error is None else transport_error,
    ))

    if max_polls < 1 or max_polls > 5:
        stages.append(_stage("trial_bound", False, "max_polls must stay within 1..5 for a supervised trial"))
    else:
        stages.append(_stage("trial_bound", True, f"trial is bounded to {max_polls} poll(s)"))

    attempts: list[dict] = []
    if _all_pass(stages):
        bridge = SignalChatBridge(
            paths,
            config,
            transport,
            dialogue_runner=dialogue_runner,
            provider=provider,
            memory_mode=memory_mode,
            mode="trial",
            lock_path=paths.feishu_chat_lock_file,
        )
        try:
            attempts = bridge.run_loop(max_polls=max_polls)
            stages.append(_trial_execution_stage(attempts))
        except SignalChatLockError as exc:
            stages.append(_stage(
                "trial_execution",
                False,
                f"another feishu bridge holds the loop lock; stop the listener service before the trial ({exc})",
            ))
        except Exception as exc:  # noqa: BLE001 - execution failures become stage evidence.
            stages.append(_stage("trial_execution", False, f"trial execution raised {type(exc).__name__}: {exc}"))
    else:
        stages.append(_stage("trial_execution", False, "trial execution skipped because preflight failed"))

    stages.append(_channel_boundary_stage(attempts))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    decision_counts = _decision_counts(attempts)
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M13.2",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M13 supervised feishu reply trial",
            "channel": "feishu",
            "transport": transport_name,
            "provider": provider,
            "memory_mode": memory_mode,
            "max_polls": max_polls,
            "confirm_real_feishu_send": confirm_real_feishu_send,
        },
        "source_reports": source_reports,
        "freeze_evidence": freeze_evidence,
        "trial": {
            "attempt_count": len(attempts),
            "decision_counts": decision_counts,
            "replied_count": decision_counts.get("replied", 0),
            "failed_count": decision_counts.get("failed", 0),
            "attempts": [_public_attempt(attempt) for attempt in attempts],
        },
        "boundaries": {
            **dict(SIGNAL_CHAT_BOUNDARIES),
            "scheduler_mutated": False,
            "service_mutation_allowed": False,
            "life_write_route_added": False,
            "secrets_in_reports_or_ledger": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
    }
    return M13FeishuTrialResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m13_feishu_trial_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m13_feishu_trial_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _trial_execution_stage(attempts: list[dict]) -> dict:
    counts = _decision_counts(attempts)
    problems = []
    if counts.get("failed"):
        problems.append(f"{counts['failed']} trial attempt(s) failed")
    if not counts.get("replied"):
        problems.append("no allowlisted inbound message received a reply during the trial window")
    if problems:
        return _stage("trial_execution", False, "; ".join(problems))
    return _stage("trial_execution", True, f"trial replied to {counts['replied']} message(s) with no failures")


def _channel_boundary_stage(attempts: list[dict]) -> dict:
    problems = []
    for attempt in attempts:
        if attempt.get("channel") != "feishu":
            problems.append("an attempt is missing channel=feishu")
        if attempt.get("mode") != "trial":
            problems.append("an attempt escaped the trial mode label")
        if attempt.get("decision") == "replied" and not attempt.get("dialogue_event_id"):
            problems.append("a replied attempt is missing dialogue evidence")
    if problems:
        return _stage("trial_channel_boundary", False, "; ".join(sorted(set(problems))))
    return _stage("trial_channel_boundary", True, "trial records stayed labeled, reactive, and evidence-backed")


def _public_attempt(attempt: dict) -> dict:
    return {
        "id": attempt.get("id"),
        "created_at": attempt.get("created_at"),
        "decision": attempt.get("decision"),
        "skip_reason": attempt.get("skip_reason"),
        "sender": attempt.get("sender"),
        "message_timestamp": attempt.get("message_timestamp"),
        "body_hash": attempt.get("body_hash"),
        "reply_hash": attempt.get("reply_hash"),
        "conversation_id": attempt.get("conversation_id"),
        "dialogue_event_id": attempt.get("dialogue_event_id"),
        "error": attempt.get("error"),
    }


def _freeze_stage(freeze_evidence: dict) -> dict:
    if freeze_evidence.get("ok") is True:
        return _stage("upstream_freeze_evidence", True, "M7/M8/M9 freeze evidence passes")
    missing = [name for name, snap in (freeze_evidence.get("reports") or {}).items() if not snap.get("ok")]
    return _stage("upstream_freeze_evidence", False, f"freeze evidence not ready: {missing or 'reports missing'}")


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
        f"{milestone} evidence is ready" if not problems else "; ".join(problems),
    )


def _decision_counts(attempts: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for attempt in attempts:
        counts[attempt["decision"]] = counts.get(attempt["decision"], 0) + 1
    return counts


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
