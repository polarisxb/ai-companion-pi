"""Operator shutdown command over the chat bridge.

Shutdown is a code-direct control command: it is matched deterministically,
gated by an explicit enable flag and the sender allowlist, and never routed
through the model. These tests prove the trigger fires only for exact,
allowlisted phrases, that the model is not consulted, that evidence is
recorded, and that a failed shutdown degrades to a spoken failure notice
without powering anything off.
"""

import json
from datetime import datetime

import pytest

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    FakeSignalTransport,
    InboundSignalMessage,
    JsonMemoryStore,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatConfigError,
    StaticDialogueLLMClient,
    load_feishu_chat_config,
    load_signal_chat_attempts,
)

ACCOUNT = "+15550000000"
ALLOWED = "+15550001111"
OTHER = "+15550002222"
NOW = datetime(2026, 7, 20, 22, 30, 0)


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


class RecordingRunner:
    """Captures shutdown commands instead of powering the machine off."""

    def __init__(self, fail: bool = False):
        self.commands: list[str] = []
        self.fail = fail

    def __call__(self, command: str) -> None:
        self.commands.append(command)
        if self.fail:
            raise RuntimeError("simulated shutdown failure (sudo denied)")


def shutdown_config(**overrides):
    payload = dict(
        account=ACCOUNT,
        allowed_senders=(ALLOWED,),
        shutdown_enabled=True,
        shutdown_command="sudo shutdown -h now",
        shutdown_triggers=("关机", "shutdown"),
        shutdown_ack_message="好，我先睡了。",
    )
    payload.update(overrides)
    return SignalChatConfig(**payload)


def make_bridge(paths, *, config, runner, llm_client=None, transport=None):
    llm_client = llm_client or StaticDialogueLLMClient()
    transport = transport if transport is not None else FakeSignalTransport()
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=DialogueRunner(
            paths,
            llm_client=llm_client,
            memory_store=JsonMemoryStore(paths.memory_store),
        ),
        provider="fake",
        memory_mode="json",
        now_fn=lambda: NOW,
        command_runner=runner,
    )
    return bridge, transport, llm_client


def msg(body, *, sender=ALLOWED, timestamp=1000):
    return InboundSignalMessage(sender=sender, timestamp=timestamp, body=body)


# --- happy path ---


def test_exact_trigger_shuts_down_without_consulting_model(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, transport, llm = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=FakeSignalTransport([[msg("关机")]]),
    )

    attempts = bridge.poll_once()

    assert runner.commands == ["sudo shutdown -h now"]
    assert llm.calls == 0  # the model never decides a shutdown
    assert transport.sent == [{"recipient": ALLOWED, "text": "好，我先睡了。"}]
    assert len(attempts) == 1
    record = attempts[0]
    assert record["decision"] == "control_executed"
    assert record["control"] == {"command": "shutdown", "ack_sent": True, "executed": True}
    # evidence is flushed before the machine powers off
    assert load_signal_chat_attempts(paths.signal_chat_attempts_file)[0]["decision"] == "control_executed"


def test_english_trigger_is_case_insensitive(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, _, _ = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=FakeSignalTransport([[msg("  ShutDown  ")]]),
    )
    bridge.poll_once()
    assert runner.commands == ["sudo shutdown -h now"]


def test_ack_is_sent_before_shutdown_runs(tmp_path):
    paths = make_paths(tmp_path)
    order: list[str] = []

    class OrderTransport(FakeSignalTransport):
        def send(self, recipient, text):
            order.append("ack")
            return super().send(recipient, text)

    def runner(command):
        order.append("shutdown")

    bridge, _, _ = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=OrderTransport([[msg("关机")]]),
    )
    bridge.poll_once()
    assert order == ["ack", "shutdown"]


# --- guards: only exact, enabled, allowlisted triggers fire ---


def test_disabled_shutdown_falls_through_to_model(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, transport, llm = make_bridge(
        paths,
        config=shutdown_config(shutdown_enabled=False),
        runner=runner,
        transport=FakeSignalTransport([[msg("关机")]]),
    )
    attempts = bridge.poll_once()
    assert runner.commands == []
    assert llm.calls == 1  # treated as an ordinary message
    assert attempts[0]["decision"] == "replied"


def test_non_allowlisted_sender_cannot_shut_down(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, _, llm = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=FakeSignalTransport([[msg("关机", sender=OTHER)]]),
    )
    attempts = bridge.poll_once()
    assert runner.commands == []
    assert llm.calls == 0
    assert attempts[0]["decision"] == "skipped"
    assert attempts[0]["skip_reason"] == "sender_not_allowed"


def test_non_exact_message_is_normal_conversation(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, _, llm = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=FakeSignalTransport([[msg("帮我看看关机脚本哪里错了")]]),
    )
    attempts = bridge.poll_once()
    assert runner.commands == []
    assert llm.calls == 1
    assert attempts[0]["decision"] == "replied"


def test_custom_trigger_phrase(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    bridge, _, _ = make_bridge(
        paths,
        config=shutdown_config(shutdown_triggers=("睡吧",)),
        runner=runner,
        transport=FakeSignalTransport([[msg("睡吧")]]),
    )
    bridge.poll_once()
    assert runner.commands == ["sudo shutdown -h now"]


# --- failure + dedupe ---


def test_failed_shutdown_degrades_to_failure_notice(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner(fail=True)
    bridge, transport, _ = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=FakeSignalTransport([[msg("关机")]]),
    )
    attempts = bridge.poll_once()
    assert runner.commands == ["sudo shutdown -h now"]
    # two messages: the goodbye ack, then the failure notice
    assert len(transport.sent) == 2
    assert transport.sent[0]["text"] == "好，我先睡了。"
    assert "没执行成功" in transport.sent[1]["text"]
    record = attempts[0]
    assert record["decision"] == "control_failed"
    assert record["control"]["executed"] is False
    assert record["error"]["type"] == "RuntimeError"


def test_replayed_command_does_not_shut_down_twice(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner(fail=True)  # fail so the loop continues and can see a replay
    transport = FakeSignalTransport([[msg("关机", timestamp=1000), msg("关机", timestamp=1000)]])
    bridge, _, _ = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=transport,
    )
    attempts = bridge.poll_once()
    assert runner.commands == ["sudo shutdown -h now"]  # only the first is executed
    assert attempts[0]["decision"] == "control_failed"
    assert attempts[1]["decision"] == "skipped"
    assert attempts[1]["skip_reason"] == "duplicate_message"


def test_executed_shutdown_stops_processing_remaining_messages(tmp_path):
    paths = make_paths(tmp_path)
    runner = RecordingRunner()
    transport = FakeSignalTransport([[msg("关机", timestamp=1000), msg("你还在吗", timestamp=1001)]])
    bridge, _, llm = make_bridge(
        paths,
        config=shutdown_config(),
        runner=runner,
        transport=transport,
    )
    attempts = bridge.poll_once()
    # the shutdown record is the last one; the trailing chat message is not processed
    assert len(attempts) == 1
    assert attempts[0]["decision"] == "control_executed"
    assert llm.calls == 0


# --- config validation ---


def test_config_requires_command_when_enabled(tmp_path):
    paths = make_paths(tmp_path)
    paths.feishu_chat_config_file.write_text(json.dumps({
        "account": "cli_app",
        "allowed_senders": ["ou_x"],
        "shutdown_enabled": True,
    }))
    with pytest.raises(SignalChatConfigError, match="shutdown_command"):
        load_feishu_chat_config(paths)


def test_config_loads_shutdown_fields(tmp_path):
    paths = make_paths(tmp_path)
    paths.feishu_chat_config_file.write_text(json.dumps({
        "account": "cli_app",
        "allowed_senders": ["ou_x"],
        "shutdown_enabled": True,
        "shutdown_command": "sudo systemctl poweroff",
        "shutdown_triggers": ["关机", "晚安"],
        "shutdown_ack_message": "晚安。",
    }))
    config = load_feishu_chat_config(paths)
    assert config.shutdown_enabled is True
    assert config.shutdown_command == "sudo systemctl poweroff"
    assert config.shutdown_triggers == ("关机", "晚安")
    assert config.shutdown_ack_message == "晚安。"
