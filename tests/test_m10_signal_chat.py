import fcntl
import json
from datetime import datetime

import pytest

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    FailingDialogueLLMClient,
    FakeSignalTransport,
    InboundSignalMessage,
    JsonMemoryStore,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatConfigError,
    SignalChatLockError,
    StaticDialogueLLMClient,
    evaluate_signal_message,
    load_signal_chat_attempts,
    load_signal_chat_config,
    load_signal_chat_state,
    parse_signal_envelope_line,
    signal_conversation_id,
)
from companion_core.dialogue import load_transcript_turns

ACCOUNT = "+15550000000"
ALLOWED = "+15550001111"
OTHER = "+15550002222"


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def make_bridge(paths, *, config=None, transport=None, llm_client=None, now_fn=None):
    config = config or SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,))
    transport = transport or FakeSignalTransport()
    runner = DialogueRunner(
        paths,
        llm_client=llm_client or StaticDialogueLLMClient(),
        memory_store=JsonMemoryStore(paths.memory_store),
    )
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=runner,
        provider="fake",
        memory_mode="json",
        now_fn=now_fn,
    )
    return bridge, transport


def msg(sender=ALLOWED, timestamp=1000, body="你好", **kwargs) -> InboundSignalMessage:
    return InboundSignalMessage(sender=sender, timestamp=timestamp, body=body, **kwargs)


def envelope_line(sender=ALLOWED, timestamp=1000, message="你好", data_extra=None, envelope_extra=None):
    data_message = {"message": message}
    if data_extra:
        data_message.update(data_extra)
    envelope = {"sourceNumber": sender, "timestamp": timestamp, "dataMessage": data_message}
    if envelope_extra:
        envelope.update(envelope_extra)
    return json.dumps({"envelope": envelope})


# --- envelope parsing ---


def test_parse_data_message_extracts_fields():
    message = parse_signal_envelope_line(envelope_line())
    assert message is not None
    assert message.sender == ALLOWED
    assert message.timestamp == 1000
    assert message.body == "你好"
    assert message.has_attachment is False
    assert message.is_group is False


def test_parse_non_data_messages_return_none():
    receipt = json.dumps({"envelope": {"sourceNumber": ALLOWED, "timestamp": 1, "receiptMessage": {"isDelivery": True}}})
    typing = json.dumps({"envelope": {"sourceNumber": ALLOWED, "timestamp": 2, "typingMessage": {"action": "STARTED"}}})
    sync = json.dumps({"envelope": {"sourceNumber": ALLOWED, "timestamp": 3, "syncMessage": {"sentMessage": {}}}})
    missing_source = json.dumps({"envelope": {"timestamp": 4, "dataMessage": {"message": "hi"}}})
    assert parse_signal_envelope_line(receipt) is None
    assert parse_signal_envelope_line(typing) is None
    assert parse_signal_envelope_line(sync) is None
    assert parse_signal_envelope_line(missing_source) is None
    assert parse_signal_envelope_line("{broken json") is None
    assert parse_signal_envelope_line("[1, 2, 3]") is None
    assert parse_signal_envelope_line("   ") is None


def test_parse_group_and_attachment_messages():
    group = parse_signal_envelope_line(
        envelope_line(data_extra={"groupInfo": {"groupId": "g1"}})
    )
    assert group is not None and group.is_group is True

    attachment_only = parse_signal_envelope_line(
        envelope_line(message=None, data_extra={"attachments": [{"contentType": "image/jpeg"}]})
    )
    assert attachment_only is not None
    assert attachment_only.body == ""
    assert attachment_only.has_attachment is True
    assert attachment_only.attachment_types == ("image/jpeg",)


def test_parse_uses_source_fallback_when_source_number_missing():
    line = json.dumps({
        "envelope": {"source": OTHER, "timestamp": 9, "dataMessage": {"message": "hey"}}
    })
    message = parse_signal_envelope_line(line)
    assert message is not None and message.sender == OTHER


# --- config ---


def test_load_config_reads_file_and_defaults(tmp_path):
    paths = make_paths(tmp_path)
    paths.signal_chat_config_file.write_text(json.dumps({
        "account": ACCOUNT,
        "allowed_senders": [ALLOWED],
        "daily_reply_budget": 5,
    }))
    config = load_signal_chat_config(paths)
    assert config.account == ACCOUNT
    assert config.allowed_senders == (ALLOWED,)
    assert config.daily_reply_budget == 5
    assert config.max_replies_per_poll == 3
    assert config.respect_quiet_hours is False
    assert config.quiet_hours == ("00:00", "08:00")


@pytest.mark.parametrize("payload,fragment", [
    (None, "not found"),
    ("{broken", "invalid JSON"),
    (json.dumps([1]), "JSON object"),
    (json.dumps({"allowed_senders": [ALLOWED]}), "account"),
    (json.dumps({"account": ACCOUNT}), "allowed_senders"),
    (json.dumps({"account": ACCOUNT, "allowed_senders": []}), "allowed_senders"),
    (json.dumps({"account": ACCOUNT, "allowed_senders": [ALLOWED], "daily_reply_budget": 0}), "positive"),
    (json.dumps({"account": ACCOUNT, "allowed_senders": [ALLOWED], "quiet_hours": ["00:00"]}), "quiet_hours"),
    (json.dumps({"account": ACCOUNT, "allowed_senders": [ALLOWED], "quiet_hours": ["aa", "bb"]}), "quiet hours"),
])
def test_load_config_rejects_bad_files(tmp_path, payload, fragment):
    paths = make_paths(tmp_path)
    if payload is not None:
        paths.signal_chat_config_file.write_text(payload)
    with pytest.raises(SignalChatConfigError) as excinfo:
        load_signal_chat_config(paths)
    assert fragment in str(excinfo.value)


# --- policy ---


def evaluate(message, *, config=None, state=None, now=None, paused=False, replies_this_poll=0):
    return evaluate_signal_message(
        message,
        config=config or SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,)),
        state=state or {"senders": {}, "daily": {"date": None, "replies_sent": 0}},
        now=now or datetime(2026, 7, 20, 15, 0, 0),
        paused=paused,
        replies_this_poll=replies_this_poll,
    )


def test_policy_covers_every_skip_reason():
    assert evaluate(msg(), paused=True) == "paused"
    assert evaluate(msg(is_group=True)) == "group_message_unsupported"
    assert evaluate(msg(sender=OTHER)) == "sender_not_allowed"
    state = {"senders": {ALLOWED: {"last_timestamp": 1000}}, "daily": {"date": None, "replies_sent": 0}}
    assert evaluate(msg(timestamp=1000), state=state) == "duplicate_message"
    assert evaluate(msg(body="   ")) == "empty_body"
    assert evaluate(msg(body="", has_attachment=True)) == "attachment_only_unsupported"
    long_config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), max_inbound_length=3)
    assert evaluate(msg(body="太长的一条消息"), config=long_config) == "body_too_long"
    quiet_config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), respect_quiet_hours=True)
    assert evaluate(msg(), config=quiet_config, now=datetime(2026, 7, 20, 3, 0)) == "quiet_hours"
    budget_config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), daily_reply_budget=1)
    spent = {"senders": {}, "daily": {"date": "2026-07-20", "replies_sent": 1}}
    assert evaluate(msg(), config=budget_config, state=spent, now=datetime(2026, 7, 20, 15, 0)) == "daily_budget_exhausted"
    batch_config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), max_replies_per_poll=1)
    assert evaluate(msg(), config=batch_config, replies_this_poll=1) == "poll_batch_limit"
    assert evaluate(msg()) is None


def test_policy_quiet_hours_off_by_default_and_daily_rollover():
    assert evaluate(msg(), now=datetime(2026, 7, 20, 3, 0)) is None
    budget_config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), daily_reply_budget=1)
    yesterday_state = {"senders": {}, "daily": {"date": "2026-07-19", "replies_sent": 1}}
    assert evaluate(msg(), config=budget_config, state=yesterday_state, now=datetime(2026, 7, 20, 15, 0)) is None


# --- bridge ---


def test_bridge_replies_and_records_everything(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    transport.queue_batch([msg(timestamp=1000, body="记得给我讲讲今天")])

    attempts = bridge.poll_once()

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["decision"] == "replied"
    assert attempt["skip_reason"] is None
    assert attempt["sender"] == ALLOWED
    assert attempt["direction"] == "inbound"
    assert attempt["transport"] == "fake"
    assert attempt["provider"] == "fake"
    assert attempt["conversation_id"] == signal_conversation_id(ALLOWED)
    assert attempt["dialogue_event_id"]
    assert attempt["boundaries"]["proactive_outbound_sent"] is False

    assert len(transport.sent) == 1
    assert transport.sent[0]["recipient"] == ALLOWED

    transcript = paths.conversations_dir / f"{signal_conversation_id(ALLOWED)}.jsonl"
    turns = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert [turn["role"] for turn in turns] == ["human", "assistant"]

    state = load_signal_chat_state(paths.signal_chat_state_file)
    assert state["senders"][ALLOWED]["last_timestamp"] == 1000
    assert state["daily"]["replies_sent"] == 1

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert len(ledger) == 1
    assert ledger[0]["id"] == attempt["id"]


def test_bridge_transcript_continuity_across_polls(tmp_path):
    paths = make_paths(tmp_path)

    class CapturingClient(StaticDialogueLLMClient):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def generate(self, prompt, context):
            self.prompts.append(prompt)
            return super().generate(prompt, context)

    client = CapturingClient()
    bridge, transport = make_bridge(paths, llm_client=client)
    transport.queue_batch([msg(timestamp=1000, body="我今天去了海边")])
    transport.queue_batch([msg(timestamp=2000, body="你还记得我说过什么吗")])

    bridge.poll_once()
    bridge.poll_once()

    transcript = paths.conversations_dir / f"{signal_conversation_id(ALLOWED)}.jsonl"
    turns = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert len(turns) == 4
    assert "我今天去了海边" in client.prompts[1]


def test_bridge_dedupes_duplicate_delivery(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    transport.queue_batch([msg(timestamp=1000)])
    transport.queue_batch([msg(timestamp=1000)])

    first = bridge.poll_once()
    second = bridge.poll_once()

    assert first[0]["decision"] == "replied"
    assert second[0]["decision"] == "skipped"
    assert second[0]["skip_reason"] == "duplicate_message"
    assert len(transport.sent) == 1


def test_bridge_daily_budget_and_batch_limit(tmp_path):
    paths = make_paths(tmp_path)
    config = SignalChatConfig(
        account=ACCOUNT,
        allowed_senders=(ALLOWED,),
        daily_reply_budget=2,
        max_replies_per_poll=1,
    )
    bridge, transport = make_bridge(paths, config=config)
    transport.queue_batch([msg(timestamp=1000, body="一"), msg(timestamp=1001, body="二")])
    transport.queue_batch([msg(timestamp=1002, body="三")])
    transport.queue_batch([msg(timestamp=1003, body="四")])

    poll1 = bridge.poll_once()
    poll2 = bridge.poll_once()
    poll3 = bridge.poll_once()

    assert [a["decision"] for a in poll1] == ["replied", "skipped"]
    assert poll1[1]["skip_reason"] == "poll_batch_limit"
    assert poll2[0]["decision"] == "replied"
    assert poll3[0]["skip_reason"] == "daily_budget_exhausted"
    assert len(transport.sent) == 2


def test_bridge_pause_flag_suppresses_replies(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    paths.signal_chat_pause_flag.touch()
    transport.queue_batch([msg(timestamp=1000)])

    attempts = bridge.poll_once()

    assert attempts[0]["skip_reason"] == "paused"
    assert transport.sent == []
    paths.signal_chat_pause_flag.unlink()
    transport.queue_batch([msg(timestamp=2000)])
    assert bridge.poll_once()[0]["decision"] == "replied"


def test_bridge_skips_non_allowed_group_and_bodyless_messages(tmp_path):
    paths = make_paths(tmp_path)
    config = SignalChatConfig(account=ACCOUNT, allowed_senders=(ALLOWED,), max_inbound_length=10)
    bridge, transport = make_bridge(paths, config=config)
    transport.queue_batch([
        msg(sender=OTHER, timestamp=1000),
        msg(timestamp=1001, is_group=True),
        msg(timestamp=1002, body=""),
        msg(timestamp=1003, body="", has_attachment=True),
        msg(timestamp=1004, body="超过十个字符的超长消息内容"),
    ])

    attempts = bridge.poll_once()

    assert [a["skip_reason"] for a in attempts] == [
        "sender_not_allowed",
        "group_message_unsupported",
        "empty_body",
        "attachment_only_unsupported",
        "body_too_long",
    ]
    assert transport.sent == []


def test_bridge_dialogue_failure_lands_in_ledger(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths, llm_client=FailingDialogueLLMClient("模型故障"))
    transport.queue_batch([msg(timestamp=1000)])

    attempts = bridge.poll_once()

    assert attempts[0]["decision"] == "failed"
    assert attempts[0]["error"]["type"] == "RuntimeError"
    assert transport.sent == []
    state = load_signal_chat_state(paths.signal_chat_state_file)
    assert state["senders"][ALLOWED]["last_timestamp"] == 1000
    assert state["daily"]["replies_sent"] == 0


def test_bridge_send_failure_retracts_undelivered_reply(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    transport.fail_next_sends = 2
    transport.queue_batch([msg(timestamp=1000, body="第一句话")])

    attempts = bridge.poll_once()

    attempt = attempts[0]
    assert attempt["decision"] == "failed"
    assert attempt["error"]["type"] == "SignalTransportError"
    assert attempt["dialogue_event_id"]
    assert attempt["reply_hash"]
    assert attempt["send_attempts"] == 2
    assert attempt["retracted_turn_id"]
    assert attempt["retraction_id"]
    assert transport.sent == []
    assert transport.send_calls == 2

    # The transcript keeps the undelivered assistant turn for audit...
    transcript = paths.conversations_dir / f"{signal_conversation_id(ALLOWED)}.jsonl"
    raw_turns = [json.loads(line) for line in transcript.read_text().splitlines()]
    assert [turn["role"] for turn in raw_turns] == ["human", "assistant"]

    # ...but future prompt context must not contain it.
    context_turns = load_transcript_turns(transcript)
    assert [turn["role"] for turn in context_turns] == ["human"]

    # And the next turn's prompt no longer references the undelivered reply.
    class CapturingClient(StaticDialogueLLMClient):
        def __init__(self):
            super().__init__("第二次回复")
            self.prompts = []

        def generate(self, prompt, context):
            self.prompts.append(prompt)
            return super().generate(prompt, context)

    client = CapturingClient()
    bridge2, transport2 = make_bridge(paths, llm_client=client)
    transport2.queue_batch([msg(timestamp=2000, body="你怎么不说话")])
    follow_up = bridge2.poll_once()

    assert follow_up[0]["decision"] == "replied"
    undelivered_reply = StaticDialogueLLMClient().reply_text
    assert undelivered_reply not in client.prompts[0]
    assert "第一句话" in client.prompts[0]


def test_bridge_send_retry_recovers_from_transient_failure(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    transport.fail_next_sends = 1
    transport.queue_batch([msg(timestamp=1000)])

    attempts = bridge.poll_once()

    attempt = attempts[0]
    assert attempt["decision"] == "replied"
    assert attempt["send_attempts"] == 2
    assert attempt["retracted_turn_id"] is None
    assert len(transport.sent) == 1

    transcript = paths.conversations_dir / f"{signal_conversation_id(ALLOWED)}.jsonl"
    context_turns = load_transcript_turns(transcript)
    assert [turn["role"] for turn in context_turns] == ["human", "assistant"]


def test_attempt_records_never_store_message_bodies(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    secret_body = "这句话不应该出现在账本里"
    transport.queue_batch([msg(timestamp=1000, body=secret_body)])
    bridge.poll_once()

    ledger_text = paths.signal_chat_attempts_file.read_text()
    assert secret_body not in ledger_text
    assert "sha256:" in ledger_text


def test_run_loop_polls_and_respects_single_instance_lock(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths)
    transport.queue_batch([msg(timestamp=1000)])
    transport.queue_batch([msg(timestamp=2000)])

    sleeps = []
    attempts = bridge.run_loop(max_polls=2, sleep_fn=sleeps.append)
    assert len(attempts) == 2
    assert sleeps == [bridge.config.poll_interval_seconds]

    with open(paths.signal_chat_lock_file, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(SignalChatLockError):
            bridge.run_loop(max_polls=1)
