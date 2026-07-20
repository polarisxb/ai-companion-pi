import fcntl
import json
from datetime import datetime, timedelta

import pytest

from companion_core import (
    CompanionPaths,
    FakeSignalTransport,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatLockError,
    append_signal_outbox_entry,
    build_signal_outbox_entry,
    load_signal_chat_attempts,
    load_signal_chat_config,
    load_signal_chat_state,
)

ACCOUNT = "+15550000000"
RECIPIENT = "+15550001111"
NOON = datetime(2026, 7, 20, 12, 0, 0)


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def make_config(**overrides) -> SignalChatConfig:
    defaults = dict(
        account=ACCOUNT,
        allowed_senders=(RECIPIENT,),
        outbound_enabled=True,
        daily_outbound_budget=2,
        outbound_quiet_hours=("00:00", "08:00"),
        outbound_max_length=100,
        outbound_max_age_minutes=360,
        outbound_max_send_attempts=3,
    )
    defaults.update(overrides)
    return SignalChatConfig(**defaults)


def make_bridge(paths, *, config=None, transport=None, now=NOON):
    config = config or make_config()
    transport = transport or FakeSignalTransport()
    bridge = SignalChatBridge(paths, config, transport, now_fn=lambda: now, mode="live")
    return bridge, transport


def seed(paths, content="今晚的月亮很亮。", *, created_at=NOON, source_event_id="wake_1"):
    return append_signal_outbox_entry(
        paths.signal_outbox_file,
        build_signal_outbox_entry(
            content=content,
            source_event_id=source_event_id,
            trigger="scheduled-wake",
            now=created_at,
        ),
    )


def test_delivery_happy_path_updates_state_and_ledger(tmp_path):
    paths = make_paths(tmp_path)
    entry = seed(paths)
    bridge, transport = make_bridge(paths)

    records = bridge.deliver_outbox_once()

    assert len(records) == 1
    record = records[0]
    assert record["decision"] == "delivered"
    assert record["direction"] == "outbound"
    assert record["recipient"] == RECIPIENT
    assert record["outbox_entry_id"] == entry["id"]
    assert record["source_event_id"] == "wake_1"
    assert record["send_attempts"] == 1
    assert transport.sent == [{"recipient": RECIPIENT, "text": "今晚的月亮很亮。"}]

    state = load_signal_chat_state(paths.signal_chat_state_file)
    assert state["outbox"][entry["id"]]["status"] == "delivered"
    assert state["outbound_daily"]["delivered"] == 1

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert len(ledger) == 1
    assert "今晚的月亮很亮" not in json.dumps(ledger, ensure_ascii=False)

    # A second pass has nothing pending and sends nothing more.
    assert bridge.deliver_outbox_once() == []
    assert len(transport.sent) == 1


def test_delivery_disabled_is_strict_noop(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths)
    bridge, transport = make_bridge(paths, config=make_config(outbound_enabled=False))

    assert bridge.deliver_outbox_once() == []
    assert transport.send_calls == 0
    assert not paths.signal_chat_state_file.exists()
    assert not paths.signal_chat_attempts_file.exists()


@pytest.mark.parametrize("flag_name", ["signal_chat_pause_flag", "signal_outbound_pause_flag"])
def test_delivery_defers_silently_when_paused(tmp_path, flag_name):
    paths = make_paths(tmp_path)
    seed(paths)
    getattr(paths, flag_name).touch()
    bridge, transport = make_bridge(paths)

    assert bridge.deliver_outbox_once() == []
    assert transport.send_calls == 0
    assert not paths.signal_chat_attempts_file.exists()

    getattr(paths, flag_name).unlink()
    records = bridge.deliver_outbox_once()
    assert [r["decision"] for r in records] == ["delivered"]


def test_delivery_defers_in_quiet_hours_and_over_budget(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths, source_event_id="wake_q")
    bridge, transport = make_bridge(paths, now=NOON.replace(hour=3))
    assert bridge.deliver_outbox_once() == []
    assert transport.send_calls == 0

    seed(paths, "第二条", source_event_id="wake_2")
    seed(paths, "第三条", source_event_id="wake_3")
    day_bridge, day_transport = make_bridge(paths, config=make_config(daily_outbound_budget=2))
    records = day_bridge.deliver_outbox_once()
    assert [r["decision"] for r in records] == ["delivered", "delivered"]

    seed(paths, "第四条", source_event_id="wake_4")
    more = day_bridge.deliver_outbox_once()
    assert more == []
    assert len(day_transport.sent) == 2


def test_delivery_terminal_skips(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths, "太老的消息", created_at=NOON - timedelta(hours=7), source_event_id="wake_old")
    seed(paths, "超" * 200, source_event_id="wake_long")
    seed(paths, "正常消息", source_event_id="wake_ok")
    seed(paths, "同源重复消息", source_event_id="wake_ok")
    bridge, transport = make_bridge(paths)

    records = bridge.deliver_outbox_once()

    outcomes = [(r["decision"], r["skip_reason"]) for r in records]
    assert outcomes == [
        ("skipped", "expired"),
        ("skipped", "content_too_long"),
        ("delivered", None),
        ("skipped", "duplicate_delivery"),
    ]
    assert len(transport.sent) == 1

    state = load_signal_chat_state(paths.signal_chat_state_file)
    statuses = {info["status"] for info in state["outbox"].values()}
    assert statuses == {"skipped", "delivered"}


def test_delivery_recipient_missing_skip(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths)
    config = SignalChatConfig(account=ACCOUNT, allowed_senders=(), outbound_enabled=True)
    bridge, transport = make_bridge(paths, config=config)

    records = bridge.deliver_outbox_once()

    assert [r["skip_reason"] for r in records] == ["recipient_missing"]
    assert transport.send_calls == 0


def test_delivery_failure_retries_then_abandons(tmp_path):
    paths = make_paths(tmp_path)
    entry = seed(paths)
    bridge, transport = make_bridge(paths)

    transport.fail_next_sends = 2
    first = bridge.deliver_outbox_once()
    assert [r["decision"] for r in first] == ["failed"]
    assert first[0]["send_attempts"] == 2
    state = load_signal_chat_state(paths.signal_chat_state_file)
    assert state["outbox"][entry["id"]] == {
        "status": "pending",
        "attempts": 2,
        "source_event_id": "wake_1",
        "updated_at": state["outbox"][entry["id"]]["updated_at"],
    }

    transport.fail_next_sends = 2
    second = bridge.deliver_outbox_once()
    assert [(r["decision"], r["skip_reason"]) for r in second] == [
        ("skipped", "abandoned_after_max_attempts"),
    ]
    state = load_signal_chat_state(paths.signal_chat_state_file)
    assert state["outbox"][entry["id"]]["status"] == "abandoned"
    assert state["outbound_daily"]["delivered"] == 0

    # Abandoned entries are terminal; nothing further happens.
    assert bridge.deliver_outbox_once() == []


def test_delivery_transient_failure_recovers_on_retry(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths)
    bridge, transport = make_bridge(paths)
    transport.fail_next_sends = 1

    records = bridge.deliver_outbox_once()

    assert [r["decision"] for r in records] == ["delivered"]
    assert records[0]["send_attempts"] == 2
    assert len(transport.sent) == 1


def test_run_loop_processes_inbound_and_outbound(tmp_path):
    from companion_core import DialogueRunner, InboundSignalMessage, JsonMemoryStore, StaticDialogueLLMClient

    paths = make_paths(tmp_path)
    seed(paths)
    config = make_config()
    transport = FakeSignalTransport([[InboundSignalMessage(sender=RECIPIENT, timestamp=1000, body="你好")]])
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=DialogueRunner(
            paths,
            llm_client=StaticDialogueLLMClient(),
            memory_store=JsonMemoryStore(paths.memory_store),
        ),
        provider="fake",
        now_fn=lambda: NOON,
        mode="live",
    )

    records = bridge.run_loop(max_polls=1)

    directions = sorted(r["direction"] for r in records)
    assert directions == ["inbound", "outbound"]
    assert len(transport.sent) == 2  # one chat reply + one outbound delivery


def test_run_outbox_delivery_respects_single_instance_lock(tmp_path):
    paths = make_paths(tmp_path)
    seed(paths)
    bridge, transport = make_bridge(paths)

    with open(paths.signal_chat_lock_file, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(SignalChatLockError):
            bridge.run_outbox_delivery()

    records = bridge.run_outbox_delivery()
    assert [r["decision"] for r in records] == ["delivered"]


def test_outbound_config_loading_roundtrip(tmp_path):
    paths = make_paths(tmp_path)
    paths.signal_chat_config_file.write_text(json.dumps({
        "account": ACCOUNT,
        "allowed_senders": [RECIPIENT],
        "outbound_enabled": True,
        "outbound_recipient": "+15550009999",
        "daily_outbound_budget": 5,
        "outbound_quiet_hours": ["23:00", "07:30"],
        "outbound_max_length": 500,
        "outbound_max_age_minutes": 120,
        "outbound_max_send_attempts": 4,
    }))

    config = load_signal_chat_config(paths)

    assert config.outbound_enabled is True
    assert config.outbound_recipient == "+15550009999"
    assert config.resolved_outbound_recipient() == "+15550009999"
    assert config.daily_outbound_budget == 5
    assert config.outbound_quiet_hours == ("23:00", "07:30")
    assert config.outbound_max_length == 500
    assert config.outbound_max_age_minutes == 120
    assert config.outbound_max_send_attempts == 4

    minimal = make_paths(tmp_path / "minimal")
    minimal.signal_chat_config_file.write_text(json.dumps({
        "account": ACCOUNT,
        "allowed_senders": [RECIPIENT],
    }))
    default_config = load_signal_chat_config(minimal)
    assert default_config.outbound_enabled is False
    assert default_config.resolved_outbound_recipient() == RECIPIENT
