"""M11.3 Signal outbound dry-run gate.

Exercises outbox capture normalization, every delivery policy branch, and the
disabled no-op contract with fake transport in an isolated smoke home. The
gate never calls a provider, never invokes signal-cli, and never mutates the
wake path, scheduler, or service artifacts. Hashed dry-run records are copied
into the real home ledger as evidence.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_BOUNDARIES,
    SIGNAL_OUTBOUND_DEFER_REASONS,
    SIGNAL_OUTBOUND_SKIP_REASONS,
    SignalChatBridge,
    SignalChatConfig,
    append_signal_chat_attempts,
    load_signal_chat_attempts,
    load_signal_chat_state,
    outbound_defer_reason,
    save_signal_chat_state,
)
from .signal_outbox import (
    append_signal_outbox_entry,
    build_signal_outbox_entry,
    load_signal_outbox_entries,
    normalize_signal_section,
)
from .signal_transport import FakeSignalTransport

READY_RECOMMENDATION = "m11_signal_outbound_dry_run_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DECISIONS = ("delivered", "skipped", "failed")

DRY_RUN_ACCOUNT = "+19990000000"
RECIPIENT = "+19990000001"

NORMALIZATION_FIXTURES = (
    {"name": "nosend", "text": "NOSEND", "expect": None},
    {"name": "nosend_punctuated", "text": " nosend. ", "expect": None},
    {"name": "empty", "text": "   \n  ", "expect": None},
    {"name": "plain", "text": "  今晚的月亮很亮。\n想让你也看看。 ", "expect": "今晚的月亮很亮。 想让你也看看。"},
    {
        "name": "secret_redacted",
        "text": "api_key=sk-abcdefghijklmnop 之后再说",
        "expect_contains": "[REDACTED_SECRET]",
    },
)


@dataclass
class M11OutboundDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m11_outbound_dry_run(paths: CompanionPaths, *, write_runtime: bool = True) -> M11OutboundDryRunResult:
    saved_at = datetime.now()
    stages: list[dict] = []

    stages.append(_normalization_stage())

    scenario_payload = _run_delivery_scenarios()
    stages.append(_scenario_coverage_stage(scenario_payload))
    stages.append(_defer_probe_stage(scenario_payload))
    stages.append(_disabled_noop_stage(scenario_payload))
    stages.append(_transport_boundary_stage(scenario_payload))
    stages.append(_ledger_hygiene_stage(scenario_payload))
    stages.append(_attempt_ledger_stage(paths, scenario_payload, write_runtime=write_runtime))
    stages.append(_capture_boundary_stage())
    stages.append(_config_template_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M11.3",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "transport": "fake",
            "provider_calls": 0,
            "write_runtime": write_runtime,
        },
        "stages": stages,
        "dry_run": {
            "record_count": scenario_payload["record_count"],
            "decision_counts": scenario_payload["decision_counts"],
            "skip_reasons_covered": scenario_payload["skip_reasons_covered"],
            "skip_reasons_missing": scenario_payload["skip_reasons_missing"],
            "defer_reasons_covered": scenario_payload["defer_reasons_covered"],
            "defer_reasons_missing": scenario_payload["defer_reasons_missing"],
            "disabled_noop_confirmed": scenario_payload["disabled_noop_confirmed"],
        },
        "transport": {
            "fake_transport_only": True,
            "signal_cli_invoked": False,
            "outbound_sends": scenario_payload["outbound_sends"],
            "recipients_match_configured": scenario_payload["recipients_match"],
        },
        "signal_outbound": {
            "outbox_file": _relative(paths, paths.signal_outbox_file),
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
            "outbound_pause_flag_path": _relative(paths, paths.signal_outbound_pause_flag),
            "chat_pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
        },
        "boundaries": {
            **dict(SIGNAL_CHAT_BOUNDARIES),
            "provider_generation_requested": False,
            "signal_cli_invoked": False,
            "wake_path_sends": False,
            "cron_replacement": False,
            "timer_installation": False,
            "service_mutated": False,
        },
        "provider_calls": 0,
        "errors": errors,
        "stop_reasons": stop_reasons,
        "next_commands": [
            f".venv/bin/python scripts/run_m11_outbound_trial.py --companion-home {paths.home} --confirm-real-signal-send",
        ],
    }
    return M11OutboundDryRunResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m11_outbound_dry_run_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = (
        Path(report_file) if report_file else paths.life_loop_dir / "m11_signal_outbound_dry_run_report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _normalization_stage() -> dict:
    failures = []
    for fixture in NORMALIZATION_FIXTURES:
        result = normalize_signal_section(fixture["text"])
        if "expect" in fixture and result != fixture["expect"]:
            failures.append(f"{fixture['name']} expected {fixture['expect']!r}, got {result!r}")
        if "expect_contains" in fixture and (result is None or fixture["expect_contains"] not in result):
            failures.append(f"{fixture['name']} should contain {fixture['expect_contains']!r}")
    if failures:
        return _stage("signal_normalization", False, "; ".join(failures))
    return _stage("signal_normalization", True, f"{len(NORMALIZATION_FIXTURES)} normalization fixtures behave as expected")


def _run_delivery_scenarios() -> dict:
    records: list[dict] = []
    defer_covered: set[str] = set()
    now = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    with tempfile.TemporaryDirectory(prefix="m11-outbound-smoke-") as smoke_dir:
        smoke_paths = CompanionPaths(Path(smoke_dir))
        smoke_paths.ensure_runtime_dirs()
        config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(RECIPIENT,),
            outbound_enabled=True,
            daily_outbound_budget=10,
            outbound_quiet_hours=("00:00", "08:00"),
            outbound_max_length=60,
            outbound_max_age_minutes=360,
            outbound_max_send_attempts=3,
        )
        transport = FakeSignalTransport()
        bridge = SignalChatBridge(
            smoke_paths,
            config,
            transport,
            now_fn=lambda: now,
            mode="dry_run",
        )

        source_ok = "wake_dry_run_ok"
        _seed(smoke_paths, "过期的消息", now - timedelta(hours=7), source_event_id="wake_dry_run_expired")
        _seed(smoke_paths, "超" * 100, now, source_event_id="wake_dry_run_long")
        _seed(smoke_paths, "第一条正式出站消息", now, source_event_id=source_ok)
        _seed(smoke_paths, "同一唤醒的重复消息", now, source_event_id=source_ok)
        records.extend(bridge.deliver_outbox_once())

        transport.fail_next_sends = 2
        _seed(smoke_paths, "这条发送会失败", now, source_event_id="wake_dry_run_fail")
        records.extend(bridge.deliver_outbox_once())
        transport.fail_next_sends = 2
        records.extend(bridge.deliver_outbox_once())

        no_recipient_config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(),
            outbound_enabled=True,
        )
        no_recipient_bridge = SignalChatBridge(
            smoke_paths,
            no_recipient_config,
            FakeSignalTransport(),
            now_fn=lambda: now,
            mode="dry_run",
        )
        _seed(smoke_paths, "找不到收件人的消息", now, source_event_id="wake_dry_run_norecipient")
        records.extend(no_recipient_bridge.deliver_outbox_once())

        # Defer probes: prove every retryable hold reason, then restore state.
        state = load_signal_chat_state(smoke_paths.signal_chat_state_file)
        smoke_paths.signal_chat_pause_flag.touch()
        defer_covered.add(outbound_defer_reason(smoke_paths, config, state, now) or "none")
        smoke_paths.signal_chat_pause_flag.unlink()
        smoke_paths.signal_outbound_pause_flag.touch()
        defer_covered.add(outbound_defer_reason(smoke_paths, config, state, now) or "none")

        # Behavioral confirmation: a paused bridge defers silently.
        _seed(smoke_paths, "暂停期间的消息", now, source_event_id="wake_dry_run_paused")
        ledger_before = len(load_signal_chat_attempts(smoke_paths.signal_chat_attempts_file))
        paused_records = bridge.deliver_outbox_once()
        ledger_after = len(load_signal_chat_attempts(smoke_paths.signal_chat_attempts_file))
        silent_defer_confirmed = not paused_records and ledger_before == ledger_after
        smoke_paths.signal_outbound_pause_flag.unlink()

        defer_covered.add(
            outbound_defer_reason(smoke_paths, config, state, now.replace(hour=3)) or "none"
        )
        spent_state = dict(state)
        spent_state["outbound_daily"] = {"date": now.date().isoformat(), "delivered": 10}
        defer_covered.add(outbound_defer_reason(smoke_paths, config, spent_state, now) or "none")

        # Disabled no-op: outbound_enabled=False must not touch anything.
        disabled_config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(RECIPIENT,),
            outbound_enabled=False,
        )
        disabled_transport = FakeSignalTransport()
        disabled_bridge = SignalChatBridge(
            smoke_paths,
            disabled_config,
            disabled_transport,
            now_fn=lambda: now,
            mode="dry_run",
        )
        state_before = json.dumps(load_signal_chat_state(smoke_paths.signal_chat_state_file), sort_keys=True)
        disabled_records = disabled_bridge.deliver_outbox_once()
        state_after = json.dumps(load_signal_chat_state(smoke_paths.signal_chat_state_file), sort_keys=True)
        disabled_noop_confirmed = (
            not disabled_records
            and not disabled_transport.sent
            and disabled_transport.send_calls == 0
            and state_before == state_after
        )

        outbound_sends = len(transport.sent)
        recipients_match = all(sent["recipient"] == RECIPIENT for sent in transport.sent)
        pending_after = [
            entry["id"]
            for entry in load_signal_outbox_entries(smoke_paths.signal_outbox_file)
            if (load_signal_chat_state(smoke_paths.signal_chat_state_file)["outbox"].get(entry["id"]) or {}).get("status")
            not in ("delivered", "skipped", "abandoned")
        ]

    decision_counts: dict[str, int] = {}
    skip_covered: set[str] = set()
    for record in records:
        decision_counts[record["decision"]] = decision_counts.get(record["decision"], 0) + 1
        if record.get("skip_reason"):
            skip_covered.add(record["skip_reason"])
    return {
        "records": records,
        "record_count": len(records),
        "decision_counts": decision_counts,
        "skip_reasons_covered": sorted(skip_covered),
        "skip_reasons_missing": sorted(set(SIGNAL_OUTBOUND_SKIP_REASONS) - skip_covered),
        "defer_reasons_covered": sorted(defer_covered - {"none"}),
        "defer_reasons_missing": sorted(set(SIGNAL_OUTBOUND_DEFER_REASONS) - defer_covered),
        "silent_defer_confirmed": silent_defer_confirmed,
        "disabled_noop_confirmed": disabled_noop_confirmed,
        "outbound_sends": outbound_sends,
        "recipients_match": recipients_match,
        "pending_after": pending_after,
    }


def _seed(paths: CompanionPaths, content: str, created_at: datetime, *, source_event_id: str) -> dict:
    return append_signal_outbox_entry(
        paths.signal_outbox_file,
        build_signal_outbox_entry(
            content=content,
            source_event_id=source_event_id,
            trigger="dry-run",
            now=created_at,
        ),
    )


def _scenario_coverage_stage(payload: dict) -> dict:
    problems = []
    if payload["skip_reasons_missing"]:
        problems.append(f"missing skip reasons: {payload['skip_reasons_missing']}")
    for decision in EXPECTED_DECISIONS:
        if not payload["decision_counts"].get(decision):
            problems.append(f"missing decision coverage: {decision}")
    if problems:
        return _stage("outbound_scenario_coverage", False, "; ".join(problems))
    return _stage(
        "outbound_scenario_coverage",
        True,
        f"{payload['record_count']} records cover all terminal decisions and skip reasons",
    )


def _defer_probe_stage(payload: dict) -> dict:
    problems = []
    if payload["defer_reasons_missing"]:
        problems.append(f"missing defer reasons: {payload['defer_reasons_missing']}")
    if not payload["silent_defer_confirmed"]:
        problems.append("paused delivery wrote records instead of deferring silently")
    if problems:
        return _stage("outbound_defer_coverage", False, "; ".join(problems))
    return _stage(
        "outbound_defer_coverage",
        True,
        "all retryable hold reasons probe correctly and defer silently",
    )


def _disabled_noop_stage(payload: dict) -> dict:
    if not payload["disabled_noop_confirmed"]:
        return _stage("outbound_disabled_noop", False, "disabled outbound still touched transport, ledger, or state")
    return _stage("outbound_disabled_noop", True, "outbound_enabled=false is a strict no-op")


def _transport_boundary_stage(payload: dict) -> dict:
    delivered = payload["decision_counts"].get("delivered", 0)
    if payload["outbound_sends"] != delivered:
        return _stage(
            "outbound_transport_boundary",
            False,
            f"successful sends ({payload['outbound_sends']}) must equal delivered decisions ({delivered})",
        )
    if not payload["recipients_match"]:
        return _stage("outbound_transport_boundary", False, "an outbound send targeted a non-configured recipient")
    return _stage(
        "outbound_transport_boundary",
        True,
        f"fake transport only; {delivered} deliveries, all to the configured recipient",
    )


def _ledger_hygiene_stage(payload: dict) -> dict:
    problems = []
    for record in payload["records"]:
        if record.get("direction") != "outbound":
            problems.append("an outbound record is missing direction=outbound")
        if "content" in record or "body" in record:
            problems.append("an outbound record stores raw message text")
        if not str(record.get("content_hash", "")).startswith("sha256:"):
            problems.append("an outbound record is missing a sha256 content hash")
        boundaries = record.get("boundaries") or {}
        if boundaries.get("proactive_outbound_sent") is not False:
            problems.append("outbound record boundaries must keep proactive_outbound_sent=false (M11 sends are gated wake output, not free-form proactive text)")
    if problems:
        return _stage("outbound_ledger_hygiene", False, "; ".join(sorted(set(problems))))
    return _stage("outbound_ledger_hygiene", True, "outbound records are hashed, labeled, and boundary-clean")


def _attempt_ledger_stage(paths: CompanionPaths, payload: dict, *, write_runtime: bool) -> dict:
    if not write_runtime:
        return _stage("outbound_ledger_writes", True, "runtime writes disabled; ledger copy skipped")
    before = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    append_signal_chat_attempts(paths.signal_chat_attempts_file, payload["records"])
    after = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    if after - before != payload["record_count"]:
        return _stage("outbound_ledger_writes", False, "dry-run records were not appended to the home ledger")
    return _stage(
        "outbound_ledger_writes",
        True,
        f"{payload['record_count']} hashed dry-run records appended to {paths.signal_chat_attempts_file.name}",
    )


def _capture_boundary_stage() -> dict:
    problems = []
    core_dir = Path(__file__).resolve().parent
    for module_name in ("lifecycle.py", "signal_outbox.py"):
        source = (core_dir / module_name).read_text()
        for forbidden in ("signal_transport", "SignalCliTransport", "subprocess"):
            if forbidden in source:
                problems.append(f"{module_name} must not reference {forbidden}; the wake path never sends")
    if problems:
        return _stage("capture_boundary", False, "; ".join(problems))
    return _stage(
        "capture_boundary",
        True,
        "wake capture stays network-free; only the bridge delivers",
    )


def _config_template_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "signal_chat_config.template.json"
    if not template_path.exists():
        return _stage("outbound_config_template", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("outbound_config_template", False, f"template is invalid JSON: {exc.msg}")
    required = {"outbound_enabled", "outbound_recipient", "daily_outbound_budget", "outbound_quiet_hours"}
    missing = sorted(required - set(payload))
    if missing:
        return _stage("outbound_config_template", False, f"template missing keys: {missing}")
    if payload.get("outbound_enabled") is not False:
        return _stage("outbound_config_template", False, "template must ship with outbound_enabled=false")
    return _stage("outbound_config_template", True, "config template carries outbound keys with safe defaults")


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
