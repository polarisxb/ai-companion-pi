"""M11.4 supervised real outbound delivery trial gate.

Delivers pending outbox entries once, under the bridge's single-instance
lock, after verifying M11.3 dry-run evidence, M10.2/M10.3 inbound evidence,
upstream freezes, outbound-enabled config, and the explicit operator
confirmation. Passing requires at least one delivery and zero failures.
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
    load_m10_freeze_evidence,
    load_signal_chat_config,
)
from .signal_outbox import load_signal_outbox_entries

READY_RECOMMENDATION = "m11_signal_outbound_trial_ready"
REQUIRED_SOURCE_REPORTS = (
    ("m11_signal_outbound_dry_run_report.json", "M11.3", "m11_signal_outbound_dry_run_ready"),
    ("m10_signal_trial_report.json", "M10.2", "m10_signal_trial_ready"),
    ("m10_signal_activation_report.json", "M10.3", "m10_signal_activation_ready"),
)


@dataclass
class M11OutboundTrialResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m11_outbound_trial(
    paths: CompanionPaths,
    *,
    transport,
    confirm_real_signal_send: bool = False,
    max_passes: int = 1,
    now: datetime | None = None,
) -> M11OutboundTrialResult:
    current = now or datetime.now()
    stages: list[dict] = []
    source_reports: dict[str, dict] = {}

    for name, milestone, recommendation in REQUIRED_SOURCE_REPORTS:
        path = paths.life_loop_dir / name
        report = _load_report(path)
        source_reports[name] = _report_snapshot(paths, path, report)
        stages.append(_source_report_stage(report, milestone=milestone, recommendation=recommendation))

    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_freeze_stage(freeze_evidence))

    config = None
    try:
        config = load_signal_chat_config(paths)
        problems = []
        if not config.outbound_enabled:
            problems.append("config outbound_enabled must be true for the outbound trial")
        if config.resolved_outbound_recipient() is None:
            problems.append("config has no resolvable outbound recipient")
        stages.append(_stage(
            "outbound_config_ready",
            not problems,
            "outbound config is enabled with a resolvable recipient" if not problems else "; ".join(problems),
        ))
    except SignalChatConfigError as exc:
        stages.append(_stage("outbound_config_ready", False, str(exc)))

    stages.append(_stage(
        "operator_confirmation",
        confirm_real_signal_send,
        "operator explicitly confirmed real signal traffic"
        if confirm_real_signal_send
        else "trial requires --confirm-real-signal-send",
    ))

    paused = paths.signal_chat_pause_flag.exists() or paths.signal_outbound_pause_flag.exists()
    stages.append(_stage(
        "pause_flags_clear",
        not paused,
        "chat and outbound pause flags are absent" if not paused else "a pause flag is present; clear it before the trial",
    ))

    pending_count = _pending_count(paths)
    stages.append(_stage(
        "outbox_has_pending_entry",
        pending_count > 0,
        f"{pending_count} pending outbox entr(y/ies) ready for supervised delivery"
        if pending_count > 0
        else "outbox has no pending entry; run a wake that produces a SIGNAL section first",
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

    if max_passes < 1 or max_passes > 3:
        stages.append(_stage("trial_bound", False, "max_passes must stay within 1..3 for a supervised trial"))
    else:
        stages.append(_stage("trial_bound", True, f"trial is bounded to {max_passes} delivery pass(es)"))

    records: list[dict] = []
    if _all_pass(stages):
        bridge = SignalChatBridge(
            paths,
            config,
            transport,
            mode="trial",
        )
        try:
            records = bridge.run_outbox_delivery(max_passes=max_passes)
            stages.append(_trial_execution_stage(records))
        except SignalChatLockError as exc:
            stages.append(_stage(
                "trial_execution",
                False,
                f"another signal chat bridge holds the loop lock; stop the listener service before the trial ({exc})",
            ))
        except Exception as exc:  # noqa: BLE001 - execution failures become stage evidence.
            stages.append(_stage("trial_execution", False, f"trial execution raised {type(exc).__name__}: {exc}"))
    else:
        stages.append(_stage("trial_execution", False, "trial execution skipped because preflight failed"))

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    decision_counts = _decision_counts(records)
    report = {
        "schema_version": 1,
        "saved_at": current.isoformat(),
        "ok": ok,
        "milestone": "M11.4",
        "recommendation": READY_RECOMMENDATION if ok else "inspect",
        "companion_home": str(paths.home),
        "profile": {
            "name": "M11 supervised signal outbound trial",
            "transport": transport_name,
            "max_passes": max_passes,
            "confirm_real_signal_send": confirm_real_signal_send,
            "provider_calls": 0,
        },
        "source_reports": source_reports,
        "freeze_evidence": freeze_evidence,
        "trial": {
            "record_count": len(records),
            "decision_counts": decision_counts,
            "delivered_count": decision_counts.get("delivered", 0),
            "failed_count": decision_counts.get("failed", 0),
            "records": [_public_record(record) for record in records],
        },
        "boundaries": {
            **dict(SIGNAL_CHAT_BOUNDARIES),
            "provider_generation_requested": False,
            "service_mutation_allowed": False,
            "life_write_route_added": False,
        },
        "stages": stages,
        "stop_reasons": stop_reasons,
        "errors": errors,
        "provider_calls": 0,
    }
    return M11OutboundTrialResult(ok=ok, recommendation=report["recommendation"], report=report, errors=errors)


def write_m11_outbound_trial_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m11_signal_outbound_trial_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _pending_count(paths: CompanionPaths) -> int:
    from .signal_chat import OUTBOUND_TERMINAL_STATUSES, load_signal_chat_state

    entries = load_signal_outbox_entries(paths.signal_outbox_file)
    outbox_state = load_signal_chat_state(paths.signal_chat_state_file)["outbox"]
    return sum(
        1
        for entry in entries
        if entry.get("id")
        and (outbox_state.get(entry["id"]) or {}).get("status") not in OUTBOUND_TERMINAL_STATUSES
    )


def _trial_execution_stage(records: list[dict]) -> dict:
    counts = _decision_counts(records)
    problems = []
    if counts.get("failed"):
        problems.append(f"{counts['failed']} delivery attempt(s) failed")
    if any(record.get("skip_reason") == "abandoned_after_max_attempts" for record in records):
        problems.append("an outbox entry was abandoned during the trial")
    if not counts.get("delivered"):
        problems.append("no outbox entry was delivered during the trial window")
    if problems:
        return _stage("trial_execution", False, "; ".join(problems))
    return _stage(
        "trial_execution",
        True,
        f"trial delivered {counts['delivered']} outbox entr(y/ies) with no failures",
    )


def _public_record(record: dict) -> dict:
    return {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "decision": record.get("decision"),
        "skip_reason": record.get("skip_reason"),
        "recipient": record.get("recipient"),
        "outbox_entry_id": record.get("outbox_entry_id"),
        "source_event_id": record.get("source_event_id"),
        "content_hash": record.get("content_hash"),
        "send_attempts": record.get("send_attempts"),
        "error": record.get("error"),
    }


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


def _freeze_stage(freeze_evidence: dict) -> dict:
    if freeze_evidence.get("ok") is True:
        return _stage("upstream_freeze_evidence", True, "M7/M8/M9 freeze evidence passes")
    missing = [
        name
        for name, snapshot in (freeze_evidence.get("reports") or {}).items()
        if not snapshot.get("ok")
    ]
    return _stage("upstream_freeze_evidence", False, f"freeze evidence not ready: {missing or 'reports missing'}")


def _decision_counts(records: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["decision"]] = counts.get(record["decision"], 0) + 1
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
