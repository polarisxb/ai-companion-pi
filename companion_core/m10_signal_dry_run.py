"""M10.1 Signal chat dry-run gate.

Exercises envelope parsing, every chat policy branch, and both failure paths
with fake transport plus a deterministic fake dialogue model. The gate never
invokes signal-cli, never calls a real provider, and never mutates scheduler
state. Scenario dialogue turns run in an isolated smoke home; only hashed
attempt records are copied into the real home ledger as evidence.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DialogueRunner
from .memory import JsonMemoryStore
from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_BOUNDARIES,
    SIGNAL_CHAT_SKIP_REASONS,
    FailingDialogueLLMClient,
    SignalChatBridge,
    SignalChatConfig,
    StaticDialogueLLMClient,
    append_signal_chat_attempts,
    load_m10_freeze_evidence,
    load_signal_chat_attempts,
)
from .signal_transport import FakeSignalTransport, InboundSignalMessage, parse_signal_envelope_line

READY_RECOMMENDATION = "m10_signal_dry_run_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIRM_FLAG = "--confirm-real-signal-send"
EXPECTED_DECISIONS = ("replied", "skipped", "failed")

DRY_RUN_ACCOUNT = "+19990000000"
ALLOWED_SENDER = "+19990000001"
UNKNOWN_SENDER = "+19990000002"

ENVELOPE_FIXTURES = (
    {
        "name": "data_message",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1000,
                "dataMessage": {"message": "你好", "attachments": []},
            }
        }),
        "expect": "message",
    },
    {
        "name": "receipt_message",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1001,
                "receiptMessage": {"isDelivery": True},
            }
        }),
        "expect": "none",
    },
    {
        "name": "typing_message",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1002,
                "typingMessage": {"action": "STARTED"},
            }
        }),
        "expect": "none",
    },
    {
        "name": "sync_message",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1003,
                "syncMessage": {"sentMessage": {"message": "self"}},
            }
        }),
        "expect": "none",
    },
    {
        "name": "group_message",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1004,
                "dataMessage": {"message": "group hi", "groupInfo": {"groupId": "g1"}},
            }
        }),
        "expect": "group",
    },
    {
        "name": "attachment_only",
        "line": json.dumps({
            "envelope": {
                "sourceNumber": ALLOWED_SENDER,
                "timestamp": 1005,
                "dataMessage": {"message": None, "attachments": [{"contentType": "image/jpeg"}]},
            }
        }),
        "expect": "attachment_only",
    },
    {
        "name": "missing_source",
        "line": json.dumps({
            "envelope": {"timestamp": 1006, "dataMessage": {"message": "无来源"}}
        }),
        "expect": "none",
    },
    {"name": "malformed_json", "line": "{not valid json", "expect": "none"},
    {"name": "empty_line", "line": "   ", "expect": "none"},
)


@dataclass
class M10SignalDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m10_signal_dry_run(paths: CompanionPaths, *, write_runtime: bool = True) -> M10SignalDryRunResult:
    saved_at = datetime.now()
    stages: list[dict] = []

    stages.append(_envelope_parsing_stage())
    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_freeze_visibility_stage(freeze_evidence))

    scenario_payload = _run_policy_scenarios()
    stages.append(_scenario_coverage_stage(scenario_payload))
    stages.append(_transport_boundary_stage(scenario_payload))
    stages.append(_provider_boundary_stage(scenario_payload))
    stages.append(_attempt_ledger_stage(paths, scenario_payload, write_runtime=write_runtime))
    stages.append(_config_template_stage())
    stages.append(_real_mode_guard_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M10.1",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "transport": "fake",
            "provider": "fake",
            "memory_mode": "json",
            "write_runtime": write_runtime,
        },
        "freeze_evidence": freeze_evidence,
        "stages": stages,
        "dry_run": {
            "attempt_count": scenario_payload["attempt_count"],
            "decision_counts": scenario_payload["decision_counts"],
            "skip_reasons_covered": scenario_payload["skip_reasons_covered"],
            "skip_reasons_missing": scenario_payload["skip_reasons_missing"],
            "failed_branches_covered": scenario_payload["failed_branches_covered"],
            "smoke_home_cleaned": scenario_payload["smoke_home_cleaned"],
        },
        "transport": {
            "fake_transport_only": True,
            "signal_cli_invoked": False,
            "outbound_sends": scenario_payload["outbound_sends"],
            "outbound_recipients_match_senders": scenario_payload["recipients_match"],
            "proactive_outbound_sent": False,
        },
        "signal_chat": {
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
            "state_file": _relative(paths, paths.signal_chat_state_file),
            "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
            "config_file": _relative(paths, paths.signal_chat_config_file),
            "config_present": paths.signal_chat_config_file.exists(),
        },
        "boundaries": {
            **dict(SIGNAL_CHAT_BOUNDARIES),
            "provider_generation_requested": False,
            "signal_cli_invoked": False,
            "cron_replacement": False,
            "timer_installation": False,
        },
        "provider_calls": 0,
        "errors": errors,
        "stop_reasons": stop_reasons,
        "next_commands": [
            f".venv/bin/python scripts/run_m10_signal_chat.py --companion-home {paths.home} --check",
            (
                f".venv/bin/python scripts/run_m10_signal_chat.py --companion-home {paths.home} "
                f"--once {CONFIRM_FLAG}"
            ),
        ],
    }
    return M10SignalDryRunResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m10_signal_dry_run_report(paths: CompanionPaths, report: dict, report_file: str | None = None) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m10_signal_dry_run_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _envelope_parsing_stage() -> dict:
    failures = []
    for fixture in ENVELOPE_FIXTURES:
        message = parse_signal_envelope_line(fixture["line"])
        expect = fixture["expect"]
        if expect == "none" and message is not None:
            failures.append(f"{fixture['name']} should parse to None")
        elif expect == "message" and (message is None or message.body != "你好"):
            failures.append(f"{fixture['name']} should parse to a text message")
        elif expect == "group" and (message is None or not message.is_group):
            failures.append(f"{fixture['name']} should parse with is_group=True")
        elif expect == "attachment_only" and (
            message is None or message.body != "" or not message.has_attachment
        ):
            failures.append(f"{fixture['name']} should parse as attachment-only")
    if failures:
        return _stage("envelope_parsing", False, "; ".join(failures))
    return _stage("envelope_parsing", True, f"{len(ENVELOPE_FIXTURES)} envelope fixtures parsed as expected")


def _freeze_visibility_stage(freeze_evidence: dict) -> dict:
    summary = ", ".join(
        f"{name}={'ok' if snapshot['ok'] else 'not-ready'}"
        for name, snapshot in freeze_evidence["reports"].items()
    )
    return _stage(
        "freeze_evidence_visibility",
        True,
        f"informational for fake dry-run; required before real traffic ({summary})",
    )


def _run_policy_scenarios() -> dict:
    attempts: list[dict] = []
    static_calls = 0
    outbound_sends = 0
    recipients_match = True
    with tempfile.TemporaryDirectory(prefix="m10-signal-smoke-") as smoke_dir:
        smoke_paths = CompanionPaths(Path(smoke_dir))
        smoke_paths.ensure_runtime_dirs()

        base_config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(ALLOWED_SENDER,),
            daily_reply_budget=2,
            max_replies_per_poll=1,
            max_inbound_length=60,
            respect_quiet_hours=False,
        )
        static_client = StaticDialogueLLMClient()
        transport = FakeSignalTransport()
        bridge = SignalChatBridge(
            smoke_paths,
            base_config,
            transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=static_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
        )

        transport.queue_batch([
            _msg(UNKNOWN_SENDER, 1000, "hi"),
            _msg(ALLOWED_SENDER, 1001, "group hi", is_group=True),
            _msg(ALLOWED_SENDER, 1002, ""),
            _msg(ALLOWED_SENDER, 1003, "", has_attachment=True),
            _msg(ALLOWED_SENDER, 1004, "x" * 100),
        ])
        transport.queue_batch([
            _msg(ALLOWED_SENDER, 2000, "你好，今天怎么样？"),
            _msg(ALLOWED_SENDER, 2001, "还在吗？"),
        ])
        transport.queue_batch([_msg(ALLOWED_SENDER, 2000, "你好，今天怎么样？")])
        transport.queue_batch([_msg(ALLOWED_SENDER, 3000, "第二条正式消息")])
        transport.queue_batch([_msg(ALLOWED_SENDER, 4000, "预算应该用完了")])
        for _ in range(5):
            attempts.extend(bridge.poll_once())

        smoke_paths.signal_chat_pause_flag.touch()
        transport.queue_batch([_msg(ALLOWED_SENDER, 5000, "暂停时的消息")])
        attempts.extend(bridge.poll_once())
        smoke_paths.signal_chat_pause_flag.unlink()

        quiet_config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(ALLOWED_SENDER,),
            daily_reply_budget=50,
            respect_quiet_hours=True,
            quiet_hours=("00:00", "08:00"),
        )
        quiet_transport = FakeSignalTransport([[_msg(ALLOWED_SENDER, 6000, "深夜消息")]])
        quiet_bridge = SignalChatBridge(
            smoke_paths,
            quiet_config,
            quiet_transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=static_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            now_fn=lambda: datetime.now().replace(hour=3, minute=0),
            mode="dry_run",
        )
        attempts.extend(quiet_bridge.poll_once())

        failing_config = SignalChatConfig(
            account=DRY_RUN_ACCOUNT,
            allowed_senders=(ALLOWED_SENDER,),
            daily_reply_budget=50,
        )
        failing_client = FailingDialogueLLMClient()
        failing_transport = FakeSignalTransport([[_msg(ALLOWED_SENDER, 7000, "这条会失败")]])
        failing_bridge = SignalChatBridge(
            smoke_paths,
            failing_config,
            failing_transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=failing_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
        )
        attempts.extend(failing_bridge.poll_once())

        send_fail_transport = FakeSignalTransport([[_msg(ALLOWED_SENDER, 8000, "发送阶段会失败")]])
        send_fail_transport.fail_next_sends = 2  # survive the bounded send retry
        send_fail_bridge = SignalChatBridge(
            smoke_paths,
            failing_config,
            send_fail_transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=static_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
        )
        attempts.extend(send_fail_bridge.poll_once())

        static_calls = static_client.calls
        outbound_sends = len(transport.sent) + len(quiet_transport.sent) + len(send_fail_transport.sent)
        for sent in transport.sent + quiet_transport.sent + send_fail_transport.sent:
            if sent["recipient"] != ALLOWED_SENDER:
                recipients_match = False
        smoke_ledger = load_signal_chat_attempts(smoke_paths.signal_chat_attempts_file)

    decision_counts: dict[str, int] = {}
    skip_reasons_covered: set[str] = set()
    dialogue_fail_covered = False
    send_fail_covered = False
    for attempt in attempts:
        decision_counts[attempt["decision"]] = decision_counts.get(attempt["decision"], 0) + 1
        if attempt["skip_reason"]:
            skip_reasons_covered.add(attempt["skip_reason"])
        if attempt["decision"] == "failed":
            if attempt.get("dialogue_event_id"):
                send_fail_covered = True
            else:
                dialogue_fail_covered = True
    return {
        "attempts": attempts,
        "attempt_count": len(attempts),
        "decision_counts": decision_counts,
        "skip_reasons_covered": sorted(skip_reasons_covered),
        "skip_reasons_missing": sorted(set(SIGNAL_CHAT_SKIP_REASONS) - skip_reasons_covered),
        "failed_branches_covered": {
            "dialogue_failure": dialogue_fail_covered,
            "send_failure": send_fail_covered,
        },
        "static_provider_calls": static_calls,
        "outbound_sends": outbound_sends,
        "recipients_match": recipients_match,
        "smoke_ledger_count": len(smoke_ledger),
        "smoke_home_cleaned": True,
    }


def _scenario_coverage_stage(payload: dict) -> dict:
    problems = []
    if payload["skip_reasons_missing"]:
        problems.append(f"missing skip reasons: {payload['skip_reasons_missing']}")
    for decision in EXPECTED_DECISIONS:
        if not payload["decision_counts"].get(decision):
            problems.append(f"missing decision coverage: {decision}")
    if not payload["failed_branches_covered"]["dialogue_failure"]:
        problems.append("dialogue failure branch not covered")
    if not payload["failed_branches_covered"]["send_failure"]:
        problems.append("send failure branch not covered")
    if payload["smoke_ledger_count"] != payload["attempt_count"]:
        problems.append("smoke home ledger does not match attempt count")
    if problems:
        return _stage("policy_scenario_coverage", False, "; ".join(problems))
    return _stage(
        "policy_scenario_coverage",
        True,
        f"{payload['attempt_count']} attempts cover all skip reasons and decisions",
    )


def _transport_boundary_stage(payload: dict) -> dict:
    replied = payload["decision_counts"].get("replied", 0)
    if payload["outbound_sends"] != replied:
        return _stage(
            "transport_boundary",
            False,
            f"outbound sends ({payload['outbound_sends']}) must equal replied decisions ({replied})",
        )
    if not payload["recipients_match"]:
        return _stage("transport_boundary", False, "an outbound reply targeted a non-sender recipient")
    return _stage(
        "transport_boundary",
        True,
        f"fake transport only; {payload['outbound_sends']} replies, each to its inbound sender",
    )


def _provider_boundary_stage(payload: dict) -> dict:
    for attempt in payload["attempts"]:
        if attempt["provider"] != "fake" or attempt["transport"] != "fake":
            return _stage("provider_boundary", False, "an attempt escaped the fake provider/transport profile")
    return _stage(
        "provider_boundary",
        True,
        f"all {payload['attempt_count']} attempts used fake provider and fake transport",
    )


def _attempt_ledger_stage(paths: CompanionPaths, payload: dict, *, write_runtime: bool) -> dict:
    for attempt in payload["attempts"]:
        record_dump = json.dumps(attempt, ensure_ascii=False)
        if "body_hash" not in attempt or not str(attempt["body_hash"]).startswith("sha256:"):
            return _stage("attempt_ledger_writes", False, "attempt records must hash message bodies")
        if "深夜消息" in record_dump or "你好，今天怎么样？" in record_dump:
            return _stage("attempt_ledger_writes", False, "attempt records must not contain message bodies")
    if not write_runtime:
        return _stage("attempt_ledger_writes", True, "runtime writes disabled; ledger copy skipped")
    before = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    append_signal_chat_attempts(paths.signal_chat_attempts_file, payload["attempts"])
    after = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    if after - before != payload["attempt_count"]:
        return _stage("attempt_ledger_writes", False, "dry-run attempts were not appended to the home ledger")
    return _stage(
        "attempt_ledger_writes",
        True,
        f"{payload['attempt_count']} hashed dry-run attempts appended to {paths.signal_chat_attempts_file.name}",
    )


def _config_template_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "signal_chat_config.template.json"
    if not template_path.exists():
        return _stage("config_template", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("config_template", False, f"template is invalid JSON: {exc.msg}")
    required = {"account", "allowed_senders", "daily_reply_budget", "respect_quiet_hours"}
    missing = sorted(required - set(payload))
    if missing:
        return _stage("config_template", False, f"template missing keys: {missing}")
    return _stage("config_template", True, "signal chat config template present and valid")


def _real_mode_guard_stage() -> dict:
    problems = []
    chat_script = REPO_ROOT / "scripts" / "run_m10_signal_chat.py"
    if not chat_script.exists():
        problems.append("scripts/run_m10_signal_chat.py is missing")
    else:
        source = chat_script.read_text()
        if CONFIRM_FLAG not in source:
            problems.append(f"chat runner must gate real traffic behind {CONFIRM_FLAG}")
    # The gate module itself is excluded because it defines these forbidden strings.
    for module_name in ("signal_transport.py", "signal_chat.py"):
        source = (Path(__file__).resolve().parent / module_name).read_text()
        for forbidden in ("crontab", "systemctl enable", "systemctl start"):
            if forbidden in source:
                problems.append(f"{module_name} must not reference {forbidden}")
    if problems:
        return _stage("real_mode_guard", False, "; ".join(problems))
    return _stage(
        "real_mode_guard",
        True,
        "real sends require explicit confirmation and M10 code never touches scheduler state",
    )


def _msg(
    sender: str,
    timestamp: int,
    body: str,
    *,
    has_attachment: bool = False,
    is_group: bool = False,
) -> InboundSignalMessage:
    return InboundSignalMessage(
        sender=sender,
        timestamp=timestamp,
        body=body,
        has_attachment=has_attachment,
        attachment_types=("image/jpeg",) if has_attachment else (),
        is_group=is_group,
    )


def _stage(name: str, passed: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if passed else "fail", "message": message}


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
