"""M13.1 Feishu chat dry-run gate.

Exercises Feishu event parsing, the REST client against a stubbed HTTP layer
(including the stale-token retry), and the chat bridge end-to-end with the
fake Feishu transport plus a fake dialogue model in an isolated smoke home.
The gate never calls the real Feishu API, never calls a provider, and never
lets secrets into configs, reports, or the ledger. Hashed dry-run records are
copied into the real home ledger as evidence.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dialogue import DialogueRunner
from .feishu_transport import (
    FakeFeishuTransport,
    FeishuApiClient,
    FeishuApiError,
    parse_feishu_message_event,
)
from .memory import JsonMemoryStore
from .paths import CompanionPaths
from .signal_chat import (
    SIGNAL_CHAT_BOUNDARIES,
    FailingDialogueLLMClient,
    SignalChatBridge,
    SignalChatConfig,
    StaticDialogueLLMClient,
    append_signal_chat_attempts,
    load_m10_freeze_evidence,
    load_signal_chat_attempts,
)
from .signal_transport import InboundSignalMessage

READY_RECOMMENDATION = "m13_feishu_dry_run_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIRM_FLAG = "--confirm-real-feishu-send"

DRY_RUN_APP_ID = "cli_dryrun_app"
ALLOWED_OPEN_ID = "ou_dryrun_human"
UNKNOWN_OPEN_ID = "ou_dryrun_stranger"
SENTINEL_SECRET = "dryrun-secret-must-never-leak"


def _event(
    *,
    open_id: str | None = ALLOWED_OPEN_ID,
    message_type: str = "text",
    text: str = "你好",
    chat_type: str = "p2p",
    create_time: str = "1753000000000",
    sender_type: str = "user",
    event_type: str = "im.message.receive_v1",
) -> dict:
    content = json.dumps({"text": text}, ensure_ascii=False) if message_type == "text" else json.dumps({"image_key": "img_x"})
    return {
        "header": {"event_type": event_type},
        "event": {
            "sender": {
                "sender_type": sender_type,
                "sender_id": {"open_id": open_id} if open_id else {},
            },
            "message": {
                "message_id": "om_x",
                "chat_type": chat_type,
                "message_type": message_type,
                "create_time": create_time,
                "content": content,
            },
        },
    }


EVENT_FIXTURES = (
    {"name": "text_p2p", "payload": _event(), "expect": "message"},
    {"name": "group_message", "payload": _event(chat_type="group"), "expect": "group"},
    {"name": "image_only", "payload": _event(message_type="image"), "expect": "attachment"},
    {"name": "bot_sender", "payload": _event(sender_type="app"), "expect": "none"},
    {"name": "wrong_event_type", "payload": _event(event_type="im.chat.updated_v1"), "expect": "none"},
    {"name": "missing_open_id", "payload": _event(open_id=None), "expect": "none"},
    {"name": "malformed_string", "payload": "{not json", "expect": "none"},
    {"name": "non_dict", "payload": [1, 2, 3], "expect": "none"},
    {"name": "bad_create_time", "payload": _event(create_time="soon"), "expect": "zero_timestamp"},
)


@dataclass
class M13FeishuDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m13_feishu_dry_run(paths: CompanionPaths, *, write_runtime: bool = True) -> M13FeishuDryRunResult:
    saved_at = datetime.now()
    stages: list[dict] = []

    stages.append(_event_parsing_stage())
    stages.append(_api_client_stage())

    freeze_evidence = load_m10_freeze_evidence(paths)
    stages.append(_stage(
        "freeze_evidence_visibility",
        True,
        "informational for fake dry-run; required before real feishu traffic "
        + ", ".join(f"{name}={'ok' if snap['ok'] else 'not-ready'}" for name, snap in freeze_evidence["reports"].items()),
    ))

    scenario_payload = _run_bridge_scenarios()
    stages.append(_scenario_stage(scenario_payload))
    stages.append(_channel_stage(scenario_payload))
    stages.append(_ledger_stage(paths, scenario_payload, write_runtime=write_runtime))
    stages.append(_secret_hygiene_stage(paths, scenario_payload))
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
        "milestone": "M13.1",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "channel": "feishu",
            "transport": "feishu-fake",
            "provider": "fake",
            "write_runtime": write_runtime,
            "provider_calls": 0,
        },
        "freeze_evidence": freeze_evidence,
        "stages": stages,
        "dry_run": {
            "attempt_count": scenario_payload["attempt_count"],
            "decision_counts": scenario_payload["decision_counts"],
            "skip_reasons_covered": scenario_payload["skip_reasons_covered"],
            "failed_branches_covered": scenario_payload["failed_branches_covered"],
            "conversation_prefix_confirmed": scenario_payload["conversation_prefix_confirmed"],
        },
        "transport": {
            "fake_transport_only": True,
            "feishu_api_invoked": False,
            "outbound_sends": scenario_payload["outbound_sends"],
            "recipients_match_senders": scenario_payload["recipients_match"],
        },
        "feishu_chat": {
            "config_file": _relative(paths, paths.feishu_chat_config_file),
            "config_present": paths.feishu_chat_config_file.exists(),
            "lock_file": _relative(paths, paths.feishu_chat_lock_file),
            "attempts_file": _relative(paths, paths.signal_chat_attempts_file),
            "pause_flag_path": _relative(paths, paths.signal_chat_pause_flag),
        },
        "boundaries": {
            **dict(SIGNAL_CHAT_BOUNDARIES),
            "provider_generation_requested": False,
            "feishu_api_invoked": False,
            "secrets_in_reports_or_ledger": False,
            "cron_replacement": False,
            "timer_installation": False,
            "service_mutated": False,
        },
        "provider_calls": 0,
        "errors": errors,
        "stop_reasons": stop_reasons,
        "next_commands": [
            f".venv/bin/python scripts/run_m13_feishu_chat.py --companion-home {paths.home} --check",
            (
                f".venv/bin/python scripts/run_m13_feishu_trial.py --companion-home {paths.home} "
                f"{CONFIRM_FLAG}"
            ),
        ],
    }
    return M13FeishuDryRunResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m13_feishu_dry_run_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m13_feishu_dry_run_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _event_parsing_stage() -> dict:
    failures = []
    for fixture in EVENT_FIXTURES:
        message = parse_feishu_message_event(fixture["payload"])
        expect = fixture["expect"]
        if expect == "none" and message is not None:
            failures.append(f"{fixture['name']} should parse to None")
        elif expect == "message" and (message is None or message.body != "你好" or message.is_group or message.has_attachment):
            failures.append(f"{fixture['name']} should parse to a p2p text message")
        elif expect == "group" and (message is None or not message.is_group):
            failures.append(f"{fixture['name']} should parse with is_group=True")
        elif expect == "attachment" and (message is None or message.body != "" or not message.has_attachment):
            failures.append(f"{fixture['name']} should parse as attachment-only")
        elif expect == "zero_timestamp" and (message is None or message.timestamp != 0):
            failures.append(f"{fixture['name']} should degrade to timestamp 0")
    if failures:
        return _stage("feishu_event_parsing", False, "; ".join(failures))
    return _stage("feishu_event_parsing", True, f"{len(EVENT_FIXTURES)} event fixtures parsed as expected")


def _api_client_stage() -> dict:
    calls: list[dict] = []
    responses: list[dict] = [
        {"code": 0, "tenant_access_token": "token-one", "expire": 7200},
        {"code": 99991663, "msg": "token expired"},
        {"code": 0, "tenant_access_token": "token-two", "expire": 7200},
        {"code": 0, "data": {"message_id": "om_sent"}},
    ]

    def stub_http_post(url: str, payload: dict, headers: dict) -> dict:
        calls.append({"url": url, "payload": payload, "headers": headers})
        return responses.pop(0)

    problems = []
    client = FeishuApiClient(DRY_RUN_APP_ID, SENTINEL_SECRET, http_post=stub_http_post)
    result = client.send_text(ALLOWED_OPEN_ID, "第一条消息")
    if result.get("message_id") != "om_sent":
        problems.append("stale-token retry did not recover the send")
    token_calls = [call for call in calls if "tenant_access_token" in call["url"]]
    send_calls = [call for call in calls if "im/v1/messages" in call["url"]]
    if len(token_calls) != 2:
        problems.append(f"expected 2 token requests (initial + refresh), got {len(token_calls)}")
    if len(send_calls) != 2:
        problems.append(f"expected 2 send attempts (stale + retry), got {len(send_calls)}")
    if send_calls and send_calls[-1]["headers"].get("Authorization") != "Bearer token-two":
        problems.append("retry send did not use the refreshed token")
    if send_calls and json.loads(send_calls[-1]["payload"]["content"]).get("text") != "第一条消息":
        problems.append("send payload does not carry the message text")

    failing_client = FeishuApiClient(DRY_RUN_APP_ID, SENTINEL_SECRET, http_post=lambda *args: {"code": 230001, "msg": "no permission"})
    try:
        failing_client.send_text(ALLOWED_OPEN_ID, "会失败")
        problems.append("non-zero api code must raise FeishuApiError")
    except FeishuApiError as exc:
        if SENTINEL_SECRET in str(exc):
            problems.append("api error text leaked the app secret")
    if problems:
        return _stage("feishu_api_client", False, "; ".join(problems))
    return _stage(
        "feishu_api_client",
        True,
        "token cache, stale-token retry, send payload, and error hygiene verified against a stubbed HTTP layer",
    )


def _run_bridge_scenarios() -> dict:
    attempts: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="m13-feishu-smoke-") as smoke_dir:
        smoke_paths = CompanionPaths(Path(smoke_dir))
        smoke_paths.ensure_runtime_dirs()
        config = SignalChatConfig(
            account=DRY_RUN_APP_ID,
            allowed_senders=(ALLOWED_OPEN_ID,),
            daily_reply_budget=10,
        )
        static_client = StaticDialogueLLMClient()
        transport = FakeFeishuTransport()
        bridge = SignalChatBridge(
            smoke_paths,
            config,
            transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=static_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
            lock_path=smoke_paths.feishu_chat_lock_file,
        )
        transport.queue_batch([
            _msg(UNKNOWN_OPEN_ID, 1000, "陌生人的消息"),
            _msg(ALLOWED_OPEN_ID, 1001, "群里的消息", is_group=True),
            _msg(ALLOWED_OPEN_ID, 1002, "", has_attachment=True),
            _msg(ALLOWED_OPEN_ID, 1003, "你好,今天怎么样?"),
        ])
        transport.queue_batch([_msg(ALLOWED_OPEN_ID, 1003, "你好,今天怎么样?")])
        for _ in range(2):
            attempts.extend(bridge.poll_once())

        failing_bridge = SignalChatBridge(
            smoke_paths,
            config,
            FakeFeishuTransport([[_msg(ALLOWED_OPEN_ID, 2000, "这条对话会失败")]]),
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=FailingDialogueLLMClient(),
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
            lock_path=smoke_paths.feishu_chat_lock_file,
        )
        attempts.extend(failing_bridge.poll_once())

        send_fail_transport = FakeFeishuTransport([[_msg(ALLOWED_OPEN_ID, 3000, "发送阶段会失败")]])
        send_fail_transport.fail_next_sends = 2
        send_fail_bridge = SignalChatBridge(
            smoke_paths,
            config,
            send_fail_transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=static_client,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
            lock_path=smoke_paths.feishu_chat_lock_file,
        )
        attempts.extend(send_fail_bridge.poll_once())

        outbound_sends = len(transport.sent) + len(send_fail_transport.sent)
        recipients_match = all(sent["recipient"] == ALLOWED_OPEN_ID for sent in transport.sent)
        conversation_files = [path.name for path in smoke_paths.conversations_dir.glob("*.jsonl")]

    decision_counts: dict[str, int] = {}
    skip_covered: set[str] = set()
    dialogue_fail = send_fail = False
    for attempt in attempts:
        decision_counts[attempt["decision"]] = decision_counts.get(attempt["decision"], 0) + 1
        if attempt.get("skip_reason"):
            skip_covered.add(attempt["skip_reason"])
        if attempt["decision"] == "failed":
            if attempt.get("dialogue_event_id"):
                send_fail = True
            else:
                dialogue_fail = True
    return {
        "attempts": attempts,
        "attempt_count": len(attempts),
        "decision_counts": decision_counts,
        "skip_reasons_covered": sorted(skip_covered),
        "failed_branches_covered": {"dialogue_failure": dialogue_fail, "send_failure": send_fail},
        "outbound_sends": outbound_sends,
        "recipients_match": recipients_match,
        "conversation_prefix_confirmed": any(name.startswith("feishu_") for name in conversation_files),
    }


def _scenario_stage(payload: dict) -> dict:
    problems = []
    required_skips = {"sender_not_allowed", "group_message_unsupported", "attachment_only_unsupported", "duplicate_message"}
    missing = required_skips - set(payload["skip_reasons_covered"])
    if missing:
        problems.append(f"missing skip coverage: {sorted(missing)}")
    if not payload["decision_counts"].get("replied"):
        problems.append("missing replied coverage")
    if not payload["failed_branches_covered"]["dialogue_failure"]:
        problems.append("dialogue failure branch not covered")
    if not payload["failed_branches_covered"]["send_failure"]:
        problems.append("send failure branch not covered")
    if problems:
        return _stage("feishu_scenario_coverage", False, "; ".join(problems))
    return _stage(
        "feishu_scenario_coverage",
        True,
        f"{payload['attempt_count']} attempts cover reply, key skips, and both failure branches",
    )


def _channel_stage(payload: dict) -> dict:
    problems = []
    for attempt in payload["attempts"]:
        if attempt.get("channel") != "feishu":
            problems.append("an attempt is missing channel=feishu")
        if attempt.get("transport") != "feishu-fake":
            problems.append("an attempt escaped the fake feishu transport")
        if attempt.get("decision") == "replied" and not str(attempt.get("conversation_id", "")).startswith("feishu_"):
            problems.append("a reply is missing the feishu_ conversation prefix")
    if not payload["conversation_prefix_confirmed"]:
        problems.append("no feishu_-prefixed transcript file was created")
    if not payload["recipients_match"]:
        problems.append("a reply targeted a non-sender recipient")
    if problems:
        return _stage("feishu_channel_wiring", False, "; ".join(sorted(set(problems))))
    return _stage(
        "feishu_channel_wiring",
        True,
        "records carry channel=feishu and transcripts use the feishu_ conversation prefix",
    )


def _ledger_stage(paths: CompanionPaths, payload: dict, *, write_runtime: bool) -> dict:
    for attempt in payload["attempts"]:
        if not str(attempt.get("body_hash", "")).startswith("sha256:"):
            return _stage("feishu_ledger_writes", False, "attempt records must hash message bodies")
        if "你好,今天怎么样?" in json.dumps(attempt, ensure_ascii=False):
            return _stage("feishu_ledger_writes", False, "attempt records must not contain message bodies")
    if not write_runtime:
        return _stage("feishu_ledger_writes", True, "runtime writes disabled; ledger copy skipped")
    before = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    append_signal_chat_attempts(paths.signal_chat_attempts_file, payload["attempts"])
    after = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    if after - before != payload["attempt_count"]:
        return _stage("feishu_ledger_writes", False, "dry-run attempts were not appended to the home ledger")
    return _stage(
        "feishu_ledger_writes",
        True,
        f"{payload['attempt_count']} hashed dry-run attempts appended to the shared ledger",
    )


def _secret_hygiene_stage(paths: CompanionPaths, payload: dict) -> dict:
    problems = []
    dumped = json.dumps(payload["attempts"], ensure_ascii=False)
    if SENTINEL_SECRET in dumped:
        problems.append("the sentinel secret leaked into attempt records")
    if paths.feishu_chat_config_file.exists():
        config_text = paths.feishu_chat_config_file.read_text()
        if "app_secret" in config_text or "APP_SECRET" in config_text:
            problems.append("real feishu config must not contain the app secret; keep it in .secrets/feishu.env")
    template_path = REPO_ROOT / "templates" / "feishu_chat_config.template.json"
    if template_path.exists() and "secret" in template_path.read_text().lower():
        problems.append("config template must not mention secrets")
    if problems:
        return _stage("feishu_secret_hygiene", False, "; ".join(problems))
    return _stage("feishu_secret_hygiene", True, "secrets stay in .secrets/feishu.env, never in configs or the ledger")


def _config_template_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "feishu_chat_config.template.json"
    if not template_path.exists():
        return _stage("feishu_config_template", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("feishu_config_template", False, f"template is invalid JSON: {exc.msg}")
    required = {"account", "allowed_senders", "daily_reply_budget", "outbound_enabled"}
    missing = sorted(required - set(payload))
    if missing:
        return _stage("feishu_config_template", False, f"template missing keys: {missing}")
    if payload.get("outbound_enabled") is not False:
        return _stage("feishu_config_template", False, "template must ship with outbound_enabled=false")
    return _stage("feishu_config_template", True, "feishu config template present with safe defaults")


def _real_mode_guard_stage() -> dict:
    problems = []
    runner = REPO_ROOT / "scripts" / "run_m13_feishu_chat.py"
    if not runner.exists():
        problems.append("scripts/run_m13_feishu_chat.py is missing")
    else:
        source = runner.read_text()
        for token in (CONFIRM_FLAG, "run_loop", "feishu_chat_lock_file"):
            if token not in source:
                problems.append(f"feishu runner is missing {token}")
    core_dir = Path(__file__).resolve().parent
    for module_name in ("feishu_transport.py",):
        source = (core_dir / module_name).read_text()
        for forbidden in ("crontab", "systemctl enable", "systemctl start"):
            if forbidden in source:
                problems.append(f"{module_name} must not reference {forbidden}")
    if problems:
        return _stage("real_mode_guard", False, "; ".join(problems))
    return _stage(
        "real_mode_guard",
        True,
        "real feishu traffic requires explicit confirmation and transport code never touches scheduler state",
    )


def _msg(sender: str, timestamp: int, body: str, *, has_attachment: bool = False, is_group: bool = False) -> InboundSignalMessage:
    return InboundSignalMessage(
        sender=sender,
        timestamp=timestamp,
        body=body,
        has_attachment=has_attachment,
        attachment_types=("image",) if has_attachment else (),
        is_group=is_group,
    )


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}


def _relative(paths: CompanionPaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.home))
    except ValueError:
        return str(path)
