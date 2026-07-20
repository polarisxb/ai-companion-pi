import json

import pytest

from companion_core import (
    CompanionPaths,
    DialogueRunner,
    FakeFeishuTransport,
    FeishuApiClient,
    FeishuApiError,
    FeishuCredentialsError,
    FeishuTransport,
    JsonMemoryStore,
    SignalChatBridge,
    SignalChatConfig,
    StaticDialogueLLMClient,
    load_feishu_chat_config,
    load_signal_chat_attempts,
    parse_feishu_message_event,
)

APP_ID = "cli_test_app"
ALLOWED = "ou_test_human"


def event(
    *,
    open_id=ALLOWED,
    message_type="text",
    text="你好",
    chat_type="p2p",
    create_time="1753000000000",
    sender_type="user",
    event_type="im.message.receive_v1",
    wrap=True,
):
    content = json.dumps({"text": text}, ensure_ascii=False) if message_type == "text" else json.dumps({"image_key": "img"})
    body = {
        "sender": {"sender_type": sender_type, "sender_id": {"open_id": open_id} if open_id else {}},
        "message": {
            "chat_type": chat_type,
            "message_type": message_type,
            "create_time": create_time,
            "content": content,
        },
    }
    if not wrap:
        return body
    return {"header": {"event_type": event_type}, "event": body}


# --- event parsing ---


def test_parse_text_p2p_event():
    message = parse_feishu_message_event(event())
    assert message is not None
    assert message.sender == ALLOWED
    assert message.timestamp == 1753000000000
    assert message.body == "你好"
    assert message.has_attachment is False
    assert message.is_group is False


def test_parse_accepts_bare_event_body_and_json_string():
    assert parse_feishu_message_event(event(wrap=False)) is not None
    assert parse_feishu_message_event(json.dumps(event(), ensure_ascii=False)) is not None


def test_parse_rejects_non_message_and_malformed_payloads():
    assert parse_feishu_message_event(event(event_type="im.chat.updated_v1")) is None
    assert parse_feishu_message_event(event(sender_type="app")) is None
    assert parse_feishu_message_event(event(open_id=None)) is None
    assert parse_feishu_message_event("{broken") is None
    assert parse_feishu_message_event([1, 2]) is None
    assert parse_feishu_message_event(None) is None
    assert parse_feishu_message_event({"header": {}, "event": {"no_message": True}}) is None


def test_parse_group_attachment_and_bad_timestamp():
    group = parse_feishu_message_event(event(chat_type="group"))
    assert group is not None and group.is_group is True

    image = parse_feishu_message_event(event(message_type="image"))
    assert image is not None
    assert image.body == ""
    assert image.has_attachment is True
    assert image.attachment_types == ("image",)

    bad_time = parse_feishu_message_event(event(create_time="soon"))
    assert bad_time is not None and bad_time.timestamp == 0


def test_parse_tolerates_malformed_text_content():
    payload = event()
    payload["event"]["message"]["content"] = "{not json"
    message = parse_feishu_message_event(payload)
    assert message is not None
    assert message.body == ""


# --- api client ---


def test_api_client_caches_token_and_sends():
    calls = []
    responses = [
        {"code": 0, "tenant_access_token": "tok-1", "expire": 7200},
        {"code": 0, "data": {"message_id": "om_1"}},
        {"code": 0, "data": {"message_id": "om_2"}},
    ]

    def stub(url, payload, headers):
        calls.append({"url": url, "payload": payload, "headers": headers})
        return responses.pop(0)

    client = FeishuApiClient(APP_ID, "secret-x", http_post=stub)
    first = client.send_text(ALLOWED, "第一条")
    second = client.send_text(ALLOWED, "第二条")

    assert first["message_id"] == "om_1"
    assert second["message_id"] == "om_2"
    token_calls = [call for call in calls if "tenant_access_token" in call["url"]]
    assert len(token_calls) == 1  # cached across sends
    send_calls = [call for call in calls if "im/v1/messages" in call["url"]]
    assert all(call["headers"]["Authorization"] == "Bearer tok-1" for call in send_calls)
    assert json.loads(send_calls[0]["payload"]["content"]) == {"text": "第一条"}
    assert send_calls[0]["payload"]["receive_id"] == ALLOWED


def test_api_client_refreshes_stale_token_once():
    responses = [
        {"code": 0, "tenant_access_token": "tok-old", "expire": 7200},
        {"code": 99991663, "msg": "token expired"},
        {"code": 0, "tenant_access_token": "tok-new", "expire": 7200},
        {"code": 0, "data": {"message_id": "om_ok"}},
    ]
    calls = []

    def stub(url, payload, headers):
        calls.append(headers.get("Authorization"))
        return responses.pop(0)

    client = FeishuApiClient(APP_ID, "secret-x", http_post=stub)
    result = client.send_text(ALLOWED, "重试")
    assert result["message_id"] == "om_ok"
    assert calls[-1] == "Bearer tok-new"


def test_api_client_raises_clean_errors_without_secret():
    client = FeishuApiClient(APP_ID, "super-secret-value", http_post=lambda *a: {"code": 230001})
    with pytest.raises(FeishuApiError) as excinfo:
        client.send_text(ALLOWED, "hi")
    assert "super-secret-value" not in str(excinfo.value)

    token_fail = FeishuApiClient(APP_ID, "super-secret-value", http_post=lambda *a: {"code": 10003})
    with pytest.raises(FeishuApiError) as token_exc:
        token_fail.tenant_access_token()
    assert "super-secret-value" not in str(token_exc.value)


# --- transport ---


def test_transport_queue_and_receive(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    transport = FeishuTransport(app_id=APP_ID, require_listener=False)
    assert transport.receive() == []
    assert transport.enqueue_event(event()) is True
    assert transport.enqueue_event(event(sender_type="app")) is False
    messages = transport.receive()
    assert len(messages) == 1
    assert messages[0].sender == ALLOWED
    assert transport.receive() == []
    assert transport.channel == "feishu"
    assert transport.conversation_prefix == "feishu"


def test_transport_requires_credentials(monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    transport = FeishuTransport(app_id=APP_ID, require_listener=False)
    with pytest.raises(FeishuCredentialsError):
        transport.send(ALLOWED, "hi")


def test_transport_send_uses_injected_api(monkeypatch):
    class StubApi:
        def __init__(self):
            self.sent = []

        def send_text(self, open_id, text):
            self.sent.append((open_id, text))
            return {"message_id": "om_x"}

    api = StubApi()
    transport = FeishuTransport(app_id=APP_ID, api=api, require_listener=False)
    transport.send(ALLOWED, "你好")
    assert api.sent == [(ALLOWED, "你好")]


# --- bridge integration ---


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def test_bridge_with_fake_feishu_transport_stamps_channel(tmp_path):
    paths = make_paths(tmp_path)
    config = SignalChatConfig(account=APP_ID, allowed_senders=(ALLOWED,))
    transport = FakeFeishuTransport()
    from companion_core import InboundSignalMessage

    transport.queue_batch([InboundSignalMessage(sender=ALLOWED, timestamp=1000, body="你好呀")])
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
        lock_path=paths.feishu_chat_lock_file,
    )

    attempts = bridge.poll_once()

    attempt = attempts[0]
    assert attempt["decision"] == "replied"
    assert attempt["channel"] == "feishu"
    assert attempt["transport"] == "feishu-fake"
    assert attempt["conversation_id"] == f"feishu_{ALLOWED.replace('_', '')}"
    transcript = paths.conversations_dir / f"feishu_{ALLOWED.replace('_', '')}.jsonl"
    assert transcript.exists()
    assert len(transport.sent) == 1
    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert ledger[0]["channel"] == "feishu"


def test_feishu_and_signal_locks_are_independent(tmp_path):
    import fcntl

    paths = make_paths(tmp_path)
    config = SignalChatConfig(account=APP_ID, allowed_senders=(ALLOWED,))
    transport = FakeFeishuTransport()
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
        lock_path=paths.feishu_chat_lock_file,
    )
    # Holding the SIGNAL lock must not block the FEISHU loop.
    with open(paths.signal_chat_lock_file, "w") as signal_lock:
        fcntl.flock(signal_lock, fcntl.LOCK_EX)
        assert bridge.run_loop(max_polls=1) == []


# --- config ---


def test_load_feishu_chat_config_from_own_file(tmp_path):
    paths = make_paths(tmp_path)
    paths.feishu_chat_config_file.write_text(json.dumps({
        "account": APP_ID,
        "allowed_senders": [ALLOWED],
        "daily_reply_budget": 30,
        "outbound_enabled": False,
    }))
    config = load_feishu_chat_config(paths)
    assert config.account == APP_ID
    assert config.allowed_senders == (ALLOWED,)
    assert config.daily_reply_budget == 30

    from companion_core import SignalChatConfigError

    bare = make_paths(tmp_path / "bare")
    with pytest.raises(SignalChatConfigError) as excinfo:
        load_feishu_chat_config(bare)
    assert "feishu chat config not found" in str(excinfo.value)


def test_feishu_secrets_load_from_secrets_file(tmp_path, monkeypatch):
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    paths = make_paths(tmp_path)
    secrets_dir = tmp_path / ".secrets"
    secrets_dir.mkdir()
    (secrets_dir / "feishu.env").write_text("FEISHU_APP_ID=cli_from_file\nFEISHU_APP_SECRET=shhh\n")

    from companion_core import load_local_secrets

    result = load_local_secrets(paths)
    assert "FEISHU_APP_ID" in result["loaded"]
    assert "FEISHU_APP_SECRET" in result["loaded"]
    import os

    assert os.environ["FEISHU_APP_ID"] == "cli_from_file"
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
