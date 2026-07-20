import json
from pathlib import Path

import pytest

from companion_core import (
    CommandTTSBackend,
    CompanionPaths,
    DialogueRunner,
    FakeFeishuTransport,
    FakeTTSBackend,
    FeishuApiClient,
    InboundSignalMessage,
    JsonMemoryStore,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatConfigError,
    StaticDialogueLLMClient,
    TTSError,
    load_feishu_chat_config,
    media_prompt_hints,
    validate_image_attachments,
    voice_decision,
)

APP_ID = "cli_media_test"
ALLOWED = "ou_media_test"


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def make_config(**overrides) -> SignalChatConfig:
    defaults = dict(account=APP_ID, allowed_senders=(ALLOWED,))
    defaults.update(overrides)
    return SignalChatConfig(**defaults)


class MetadataLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        return self.reply


def make_bridge(paths, config, *, transport=None, llm=None, tts=None):
    transport = transport or FakeFeishuTransport()
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=DialogueRunner(
            paths,
            llm_client=llm or StaticDialogueLLMClient(),
            memory_store=JsonMemoryStore(paths.memory_store),
        ),
        provider="fake",
        lock_path=paths.feishu_chat_lock_file,
        tts_backend=tts or FakeTTSBackend(),
    )
    return bridge, transport


def msg(timestamp=1000, body="你好"):
    return InboundSignalMessage(sender=ALLOWED, timestamp=timestamp, body=body)


# --- tts backends ---


def test_command_tts_backend_full_pipeline(tmp_path):
    calls = []

    def runner(command, *, input_text):
        calls.append((command[0], input_text))
        if command[0] == "engine":
            Path([token for token in command if token.endswith("voice.wav")][0]).write_bytes(b"WAV")
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"OPUS")
        elif command[0] == "ffprobe":
            return "1.5"
        return ""

    backend = CommandTTSBackend("engine --out {output}", runner=runner)
    voice = backend.synthesize_opus("你好", tmp_path)
    assert voice.duration_ms == 1500
    assert voice.opus_path.exists()
    assert calls[0] == ("engine", "你好")  # stdin text when no {text} placeholder


def test_command_tts_backend_text_placeholder_and_failures(tmp_path):
    def runner(command, *, input_text):
        if command[0] == "engine":
            assert input_text is None
            assert "你好" in command
            Path([token for token in command if token.endswith("voice.wav")][0]).write_bytes(b"WAV")
        elif command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"OPUS")
        elif command[0] == "ffprobe":
            return "0.8"
        return ""

    backend = CommandTTSBackend("engine --say {text} --out {output}", runner=runner)
    assert backend.synthesize_opus("你好", tmp_path).duration_ms == 800

    with pytest.raises(TTSError):
        CommandTTSBackend("engine --no-output-placeholder")
    with pytest.raises(TTSError):
        backend.synthesize_opus("  ", tmp_path)

    def no_output_runner(command, *, input_text):
        return ""

    silent = CommandTTSBackend("engine {output}", runner=no_output_runner)
    with pytest.raises(TTSError):
        silent.synthesize_opus("你好", tmp_path)


# --- voice decision / attachment validation ---


def test_voice_decision_modes():
    assert voice_decision(make_config(voice_replies="off"), {}, "hi") == (False, None)
    assert voice_decision(make_config(voice_replies="always"), {}, "hi") == (True, None)
    long_config = make_config(voice_replies="always", voice_max_chars=3)
    assert voice_decision(long_config, {}, "太长了吧") == (False, "reply_too_long_for_voice")
    choice = make_config(voice_replies="companion_choice")
    assert voice_decision(choice, {}, "hi") == (False, None)
    assert voice_decision(choice, {"voice": True}, "hi") == (True, None)
    assert voice_decision(choice, {"voice": "yes"}, "hi") == (False, None)


def test_validate_image_attachments_guards(tmp_path):
    paths = make_paths(tmp_path)
    art = paths.home / "creations" / "art"
    art.mkdir(parents=True)
    good = art / "ok.png"
    good.write_bytes(b"PNG" * 10)
    big = art / "big.png"
    big.write_bytes(b"X" * 500)
    (art / "notes.txt").write_text("not an image")

    config = make_config(image_attachments_enabled=True, max_images_per_reply=1, image_max_bytes=100)
    metadata = {"attachments": [
        {"type": "image", "path": "creations/art/ok.png"},
        {"type": "image", "path": "../../etc/passwd"},
        {"type": "image", "path": "creations/art/../../window/status.json"},
        {"type": "image", "path": "creations/art/missing.png"},
        {"type": "image", "path": "creations/art/big.png"},
        {"type": "image", "path": "creations/art/notes.txt"},
        {"type": "file", "path": "creations/art/ok.png"},
        "not-a-dict",
        {"type": "image", "path": "creations/art/ok.png"},
    ]}

    valid, rejected = validate_image_attachments(paths, config, metadata)

    assert len(valid) == 1
    assert valid[0]["relative"] == "creations/art/ok.png"
    assert valid[0]["filename"] == "ok.png"
    assert valid[0]["data"] == b"PNG" * 10  # byte snapshot taken at validation time
    reasons = [item["reason"] for item in rejected]
    assert reasons.count("path_outside_creations") == 2
    assert "file_not_found" in reasons
    assert "file_too_large" in reasons
    assert "unsupported_extension" in reasons
    assert "unsupported_attachment_type" in reasons
    assert "malformed_attachment" in reasons
    assert "over_max_images_per_reply" in reasons

    disabled_valid, disabled_rejected = validate_image_attachments(paths, make_config(), metadata)
    assert disabled_valid == [] and disabled_rejected == []


def test_validate_image_attachments_rejects_hardlinks_and_symlink_targets(tmp_path):
    import os

    paths = make_paths(tmp_path)
    art = paths.home / "creations" / "art"
    art.mkdir(parents=True)
    secret = paths.home / "secret.png"
    secret.write_bytes(b"SECRET")
    os.link(secret, art / "hardlinked.png")
    (art / "sneaky.png").symlink_to(secret)

    config = make_config(image_attachments_enabled=True)
    metadata = {"attachments": [
        {"type": "image", "path": "creations/art/hardlinked.png"},
        {"type": "image", "path": "creations/art/sneaky.png"},
    ]}

    valid, rejected = validate_image_attachments(paths, config, metadata)

    assert valid == []
    reasons = {item["reason"] for item in rejected}
    # The symlink resolves outside creations; the hardlink is caught by fstat.
    assert reasons == {"hardlinked_file_rejected", "path_outside_creations"}


def test_snapshot_helper_rejects_symlink_swap_directly(tmp_path):
    from companion_core.chat_media import _snapshot_regular_file

    target = tmp_path / "outside.png"
    target.write_bytes(b"DATA")
    link = tmp_path / "link.png"
    link.symlink_to(target)

    reason, data = _snapshot_regular_file(link, 1000)

    assert reason == "symlink_rejected"
    assert data is None


# --- bridge media integration ---


def test_bridge_voice_always_sends_bubble_and_records(tmp_path):
    paths = make_paths(tmp_path)
    config = make_config(voice_replies="always", tts_command="fake {output}")
    bridge, transport = make_bridge(paths, config)
    transport.queue_batch([msg()])

    attempts = bridge.poll_once()

    attempt = attempts[0]
    assert attempt["decision"] == "replied"
    media = attempt["media"]
    assert media["voice"]["sent"] is True
    assert media["voice"]["duration_ms"] > 0
    assert len(transport.sent) == 1  # text still sent first
    assert len(transport.sent_voices) == 1
    assert transport.sent_voices[0]["recipient"] == ALLOWED


def test_bridge_companion_choice_voice_via_metadata(tmp_path):
    paths = make_paths(tmp_path)
    config = make_config(voice_replies="companion_choice", tts_command="fake {output}")
    llm = MetadataLLM('这句说给你听。\n===DIALOGUE_METADATA===\n{"voice": true}')
    bridge, transport = make_bridge(paths, config, llm=llm)
    transport.queue_batch([msg()])

    attempts = bridge.poll_once()

    assert attempts[0]["media"]["voice"]["sent"] is True
    assert len(transport.sent_voices) == 1
    assert any('"voice": true' in prompt for prompt in llm.prompts)  # hint injected

    plain = MetadataLLM("普通回复。")
    bridge2, transport2 = make_bridge(paths, config, llm=plain)
    transport2.queue_batch([msg(timestamp=2000)])
    attempts2 = bridge2.poll_once()
    assert attempts2[0]["media"]["voice"]["requested"] is False
    assert transport2.sent_voices == []


def test_bridge_media_failures_never_break_the_reply(tmp_path):
    paths = make_paths(tmp_path)
    config = make_config(voice_replies="always", tts_command="fake {output}")

    tts_fail_bridge, transport = make_bridge(paths, config, tts=FakeTTSBackend(fail=True))
    transport.queue_batch([msg()])
    attempts = tts_fail_bridge.poll_once()
    assert attempts[0]["decision"] == "replied"
    assert attempts[0]["media"]["voice"]["error"]["type"] == "TTSError"
    assert len(transport.sent) == 1

    send_fail_transport = FakeFeishuTransport()
    send_fail_transport.fail_next_voice_sends = 1
    send_fail_bridge, _ = make_bridge(paths, config, transport=send_fail_transport)
    send_fail_transport.queue_batch([msg(timestamp=2000)])
    attempts = send_fail_bridge.poll_once()
    assert attempts[0]["decision"] == "replied"
    assert attempts[0]["media"]["voice"]["error"]["type"] == "FeishuApiError"


def test_bridge_image_attachments_sent_and_scoped(tmp_path):
    paths = make_paths(tmp_path)
    art = paths.home / "creations" / "art"
    art.mkdir(parents=True)
    (art / "moon.png").write_bytes(b"PNG-moon")
    config = make_config(image_attachments_enabled=True)
    reply = (
        "看看这张。\n===DIALOGUE_METADATA===\n"
        + json.dumps({"attachments": [
            {"type": "image", "path": "creations/art/moon.png"},
            {"type": "image", "path": "../secrets.txt"},
        ]})
    )
    bridge, transport = make_bridge(paths, config, llm=MetadataLLM(reply))
    transport.queue_batch([msg()])

    attempts = bridge.poll_once()

    media = attempts[0]["media"]
    assert media["images"]["sent"] == 1
    assert media["images"]["sent_paths"] == ["creations/art/moon.png"]
    assert media["images"]["rejected"][0]["reason"] == "path_outside_creations"
    assert transport.sent_images[0]["recipient"] == ALLOWED
    assert transport.sent_images[0]["filename"] == "moon.png"
    assert transport.sent_images[0]["size"] == len(b"PNG-moon")


def test_bridge_without_media_config_has_no_media_key(tmp_path):
    paths = make_paths(tmp_path)
    bridge, transport = make_bridge(paths, make_config())
    transport.queue_batch([msg()])

    attempts = bridge.poll_once()

    assert attempts[0]["decision"] == "replied"
    assert "media" not in attempts[0]
    assert bridge._media_hints is None


def test_media_hints_only_for_media_transports():
    feishu = FakeFeishuTransport()
    config = make_config(voice_replies="companion_choice", image_attachments_enabled=True)
    hints = media_prompt_hints(config, feishu)
    assert '"voice": true' in hints and "attachments" in hints

    from companion_core import FakeSignalTransport

    assert media_prompt_hints(config, FakeSignalTransport()) is None


# --- config + api ---


def test_media_config_fields_load_and_validate(tmp_path):
    paths = make_paths(tmp_path)
    paths.feishu_chat_config_file.write_text(json.dumps({
        "account": APP_ID,
        "allowed_senders": [ALLOWED],
        "voice_replies": "companion_choice",
        "voice_max_chars": 100,
        "tts_command": "piper --output_file {output}",
        "image_attachments_enabled": True,
        "max_images_per_reply": 2,
        "image_max_bytes": 2048,
    }))
    config = load_feishu_chat_config(paths)
    assert config.voice_replies == "companion_choice"
    assert config.voice_max_chars == 100
    assert config.tts_command == "piper --output_file {output}"
    assert config.image_attachments_enabled is True
    assert config.max_images_per_reply == 2
    assert config.image_max_bytes == 2048

    paths.feishu_chat_config_file.write_text(json.dumps({
        "account": APP_ID,
        "allowed_senders": [ALLOWED],
        "voice_replies": "loud",
    }))
    with pytest.raises(SignalChatConfigError) as excinfo:
        load_feishu_chat_config(paths)
    assert "voice_replies" in str(excinfo.value)


def test_api_client_media_uploads_and_sends(tmp_path):
    json_calls = []
    multipart_calls = []

    def stub_json(url, payload, headers):
        json_calls.append({"url": url, "payload": payload})
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "tok", "expire": 7200}
        return {"code": 0, "data": {"message_id": "om"}}

    def stub_multipart(url, fields, file_field, filename, file_bytes, headers):
        multipart_calls.append({"url": url, "fields": fields, "file_field": file_field, "filename": filename})
        if "images" in url:
            return {"code": 0, "data": {"image_key": "imgk"}}
        return {"code": 0, "data": {"file_key": "filek"}}

    client = FeishuApiClient(APP_ID, "s", http_post=stub_json, http_post_multipart=stub_multipart)

    assert client.upload_image("a.png", b"PNG") == "imgk"
    client.send_image(ALLOWED, "imgk")
    assert client.upload_opus("v.opus", b"OggS", 1234) == "filek"
    client.send_audio(ALLOWED, "filek", 1234)

    assert multipart_calls[0]["file_field"] == "image"
    assert multipart_calls[1]["fields"]["file_type"] == "opus"
    assert multipart_calls[1]["fields"]["duration"] == "1234"
    sends = [call for call in json_calls if "im/v1/messages" in call["url"]]
    assert json.loads(sends[0]["payload"]["content"]) == {"image_key": "imgk"}
    assert json.loads(sends[1]["payload"]["content"]) == {"file_key": "filek", "duration": 1234}


def test_multipart_encoder_sanitizes_hostile_names():
    from companion_core.feishu_transport import encode_multipart_form

    body, content_type = encode_multipart_form(
        {"file_name": "line\r\nX-Evil: 1"},
        "file",
        'evil"\r\nX-Injected: yes\r\n.png',
        b"BYTES",
    )
    headers_section = body.split(b"BYTES")[0]
    assert b"\r\nX-Injected: yes" not in headers_section
    assert b"\r\nX-Evil: 1" not in headers_section
    assert b'filename="evil' in headers_section  # quote stripped, name kept
    boundary = content_type.split("boundary=")[1]
    assert headers_section.count(f"--{boundary}".encode()) == 2
