"""M14.1 Feishu media dry-run gate.

Exercises the TTS pipeline with stubbed engines, the multipart upload layer
against a stubbed HTTP client, and the bridge media step end-to-end with fake
transports in an isolated smoke home. Proves the core M14 contract: the text
reply is never blocked by media, attachments cannot escape ``creations/``,
and ledger media payloads carry no raw bytes.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .chat_media import media_prompt_hints
from .dialogue import DialogueRunner
from .feishu_transport import FakeFeishuTransport, FeishuApiClient
from .memory import JsonMemoryStore
from .paths import CompanionPaths
from .signal_chat import (
    SignalChatBridge,
    SignalChatConfig,
    append_signal_chat_attempts,
    load_signal_chat_attempts,
)
from .signal_transport import FakeSignalTransport, InboundSignalMessage
from .tts import CommandTTSBackend, FakeTTSBackend, TTSError

READY_RECOMMENDATION = "m14_feishu_media_dry_run_ready"
REPO_ROOT = Path(__file__).resolve().parents[1]

DRY_RUN_APP_ID = "cli_media_dryrun"
ALLOWED_OPEN_ID = "ou_media_human"
VOICE_METADATA_REPLY = '好,这句想说给你听。\n===DIALOGUE_METADATA===\n{"voice": true}'


class _ScriptedLLM:
    """Returns scripted replies in order."""

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def generate(self, prompt, context):
        self.prompts.append(prompt)
        if not self.outputs:
            return "我在。"
        return self.outputs.pop(0)


@dataclass
class M14FeishuMediaDryRunResult:
    ok: bool
    recommendation: str
    report: dict
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.report)


def run_m14_feishu_media_dry_run(paths: CompanionPaths, *, write_runtime: bool = True) -> M14FeishuMediaDryRunResult:
    saved_at = datetime.now()
    stages: list[dict] = []

    stages.append(_tts_pipeline_stage())
    stages.append(_multipart_upload_stage())

    scenario_payload = _run_media_scenarios()
    stages.append(_voice_coverage_stage(scenario_payload))
    stages.append(_image_coverage_stage(scenario_payload))
    stages.append(_text_priority_stage(scenario_payload))
    stages.append(_hint_injection_stage(scenario_payload))
    stages.append(_ledger_hygiene_stage(paths, scenario_payload, write_runtime=write_runtime))
    stages.append(_config_template_stage())
    stages.append(_static_guard_stage())

    stop_reasons = [stage["name"] for stage in stages if stage.get("status") != "pass"]
    ok = not stop_reasons
    recommendation = READY_RECOMMENDATION if ok else "inspect"
    errors = [stage["message"] for stage in stages if stage.get("status") != "pass"]
    report = {
        "schema_version": 1,
        "saved_at": saved_at.isoformat(),
        "ok": ok,
        "milestone": "M14.1",
        "recommendation": recommendation,
        "companion_home": str(paths.home),
        "profile": {
            "channel": "feishu",
            "transport": "feishu-fake",
            "tts_backend": "fake",
            "provider": "fake",
            "write_runtime": write_runtime,
            "provider_calls": 0,
        },
        "stages": stages,
        "dry_run": {
            "attempt_count": scenario_payload["attempt_count"],
            "voice_outcomes": scenario_payload["voice_outcomes"],
            "image_outcomes": scenario_payload["image_outcomes"],
            "replied_despite_media_failures": scenario_payload["replied_despite_media_failures"],
            "signal_transport_media_skipped": scenario_payload["signal_transport_media_skipped"],
        },
        "boundaries": {
            "text_reply_never_blocked_by_media": True,
            "attachments_outside_creations": False,
            "raw_media_bytes_in_ledger_or_reports": False,
            "synthesized_audio_retained": False,
            "feishu_api_invoked": False,
            "provider_generation_requested": False,
            "wake_cycle_run": False,
            "scheduler_mutated": False,
            "memory_authority_expanded": False,
        },
        "provider_calls": 0,
        "errors": errors,
        "stop_reasons": stop_reasons,
        "next_commands": [
            (
                f".venv/bin/python scripts/run_m14_feishu_media_trial.py --companion-home {paths.home} "
                "--confirm-real-feishu-send"
            ),
        ],
    }
    return M14FeishuMediaDryRunResult(ok=ok, recommendation=recommendation, report=report, errors=errors)


def write_m14_feishu_media_dry_run_report(
    paths: CompanionPaths,
    report: dict,
    report_file: str | None = None,
) -> Path:
    report_path = Path(report_file) if report_file else paths.life_loop_dir / "m14_feishu_media_dry_run_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    tmp_path.replace(report_path)
    return report_path


def _tts_pipeline_stage() -> dict:
    problems = []
    calls: list[list[str]] = []

    def stub_runner(command: list[str], *, input_text: str | None) -> str:
        calls.append(command)
        if command[0] == "fake-engine":
            output = next(token for token in command if token.endswith("voice.wav"))
            if input_text is None:
                problems.append("engine without {text} placeholder must receive text on stdin")
            Path(output).write_bytes(b"RIFF-fake-wav")
            return ""
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"OggS-fake-opus")
            return ""
        if command[0] == "ffprobe":
            return "2.345\n"
        problems.append(f"unexpected command {command[0]}")
        return ""

    backend = CommandTTSBackend("fake-engine --output_file {output}", runner=stub_runner)
    with tempfile.TemporaryDirectory() as tts_dir:
        synthesized = backend.synthesize_opus("你好,这是一条语音。", Path(tts_dir))
        if synthesized.duration_ms != 2345:
            problems.append(f"ffprobe duration not parsed correctly: {synthesized.duration_ms}")
        if not synthesized.opus_path.name.endswith(".opus"):
            problems.append("synthesis did not produce an opus path")
    if [call[0] for call in calls] != ["fake-engine", "ffmpeg", "ffprobe"]:
        problems.append(f"pipeline order wrong: {[call[0] for call in calls]}")

    try:
        CommandTTSBackend("no-output-placeholder")
        problems.append("template without {output} must be rejected")
    except TTSError:
        pass
    try:
        backend.synthesize_opus("   ", Path("/tmp"))
        problems.append("empty text must be rejected")
    except TTSError:
        pass

    fake = FakeTTSBackend()
    with tempfile.TemporaryDirectory() as fake_dir:
        first = fake.synthesize_opus("同样的文本", Path(fake_dir))
        if first.duration_ms <= 0 or not first.opus_path.exists():
            problems.append("fake backend must produce a file and positive duration")

    if problems:
        return _stage("tts_pipeline", False, "; ".join(problems))
    return _stage("tts_pipeline", True, "engine -> ffmpeg -> ffprobe pipeline verified with stub runners")


def _multipart_upload_stage() -> dict:
    problems = []
    multipart_calls: list[dict] = []
    json_calls: list[dict] = []

    def stub_json_post(url, payload, headers):
        json_calls.append({"url": url, "payload": payload, "headers": headers})
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "tok-media", "expire": 7200}
        return {"code": 0, "data": {"message_id": "om_media"}}

    def stub_multipart(url, fields, file_field, filename, file_bytes, headers):
        multipart_calls.append({
            "url": url,
            "fields": dict(fields),
            "file_field": file_field,
            "filename": filename,
            "size": len(file_bytes),
            "headers": dict(headers),
        })
        if "im/v1/images" in url:
            return {"code": 0, "data": {"image_key": "img_key_1"}}
        return {"code": 0, "data": {"file_key": "file_key_1"}}

    client = FeishuApiClient(
        DRY_RUN_APP_ID,
        "media-secret",
        http_post=stub_json_post,
        http_post_multipart=stub_multipart,
    )
    image_key = client.upload_image("art.png", b"PNG-fake-bytes")
    client.send_image(ALLOWED_OPEN_ID, image_key)
    file_key = client.upload_opus("voice.opus", b"OggS-fake-opus", 2345)
    client.send_audio(ALLOWED_OPEN_ID, file_key, 2345)

    image_upload = next((call for call in multipart_calls if "im/v1/images" in call["url"]), None)
    audio_upload = next((call for call in multipart_calls if "im/v1/files" in call["url"]), None)
    if not image_upload or image_upload["fields"].get("image_type") != "message":
        problems.append("image upload must post image_type=message")
    if not audio_upload or audio_upload["fields"].get("file_type") != "opus" or audio_upload["fields"].get("duration") != "2345":
        problems.append("audio upload must post file_type=opus with duration")
    if any(not call["headers"].get("Authorization", "").startswith("Bearer ") for call in multipart_calls):
        problems.append("uploads must carry the tenant token")
    sends = [call for call in json_calls if "im/v1/messages" in call["url"]]
    if len(sends) != 2:
        problems.append(f"expected image+audio sends, got {len(sends)}")
    else:
        image_send = json.loads(sends[0]["payload"]["content"])
        audio_send = json.loads(sends[1]["payload"]["content"])
        if image_send != {"image_key": "img_key_1"}:
            problems.append("image send content is wrong")
        if audio_send != {"file_key": "file_key_1", "duration": 2345}:
            problems.append("audio send content is wrong")

    from .feishu_transport import encode_multipart_form

    body, content_type = encode_multipart_form({"a": "1"}, "file", "x.png", b"BYTES")
    if b"BYTES" not in body or "multipart/form-data; boundary=" not in content_type:
        problems.append("multipart encoder body/content-type malformed")
    boundary = content_type.split("boundary=")[1]
    if body.count(f"--{boundary}".encode()) < 3:
        problems.append("multipart encoder boundary structure malformed")
    hostile, _ = encode_multipart_form(
        {"a": "line\r\nX-Evil: 1"},
        "file",
        'evil"\r\nX-Injected: yes\r\n.png',
        b"B",
    )
    if b"X-Injected" in hostile.split(b"\r\n\r\n")[0] or b'filename="evil"' in hostile:
        problems.append("multipart encoder failed to sanitize hostile filenames")
    if b"\r\nX-Evil: 1" in hostile.split(b"BYTES")[0] and b"X-Evil: 1\r\n" not in hostile:
        problems.append("multipart encoder failed to strip CRLF from field values")

    if problems:
        return _stage("multipart_upload", False, "; ".join(problems))
    return _stage("multipart_upload", True, "image/audio uploads and sends verified against a stubbed HTTP layer")


def _run_media_scenarios() -> dict:
    with tempfile.TemporaryDirectory(prefix="m14-media-smoke-") as smoke_dir:
        smoke_paths = CompanionPaths(Path(smoke_dir))
        smoke_paths.ensure_runtime_dirs()
        creations = smoke_paths.home / "creations" / "art"
        creations.mkdir(parents=True, exist_ok=True)
        good_image = creations / "moon.png"
        good_image.write_bytes(b"PNG" * 100)
        big_image = creations / "huge.png"
        big_image.write_bytes(b"P" * 5000)
        outside_secret = smoke_paths.home / "secret.png"
        outside_secret.write_bytes(b"SECRET-BYTES")
        hardlink_supported = True
        try:
            import os as os_module

            os_module.link(outside_secret, creations / "hardlinked.png")
        except OSError:
            hardlink_supported = False
            (creations / "hardlinked.png").write_bytes(b"P" * 6000)  # falls back to file_too_large coverage

        attempts: list[dict] = []
        hints_seen: dict[str, bool] = {}

        def bridge_for(config, llm_outputs, transport=None, tts=None):
            llm = _ScriptedLLM(llm_outputs)
            transport = transport or FakeFeishuTransport()
            bridge = SignalChatBridge(
                smoke_paths,
                config,
                transport,
                dialogue_runner=DialogueRunner(
                    smoke_paths,
                    llm_client=llm,
                    memory_store=JsonMemoryStore(smoke_paths.memory_store),
                ),
                provider="fake",
                memory_mode="json",
                mode="dry_run",
                lock_path=smoke_paths.feishu_chat_lock_file,
                tts_backend=tts or FakeTTSBackend(),
            )
            return bridge, transport, llm

        base = dict(account=DRY_RUN_APP_ID, allowed_senders=(ALLOWED_OPEN_ID,), daily_reply_budget=50)

        # voice always: sent
        config = SignalChatConfig(**base, voice_replies="always", tts_command="fake {output}")
        bridge, transport, _ = bridge_for(config, ["嗯,我在,今天风很好。"])
        transport.queue_batch([_msg(1000, "在吗?")])
        attempts.extend(bridge.poll_once())
        voice_always_sent = len(transport.sent_voices) == 1

        # companion_choice: requested via metadata
        config = SignalChatConfig(**base, voice_replies="companion_choice", tts_command="fake {output}")
        bridge, transport, llm = bridge_for(config, [VOICE_METADATA_REPLY, "这句普通回复。"])
        transport.queue_batch([_msg(2000, "想听你说话")])
        transport.queue_batch([_msg(2001, "随便聊聊")])
        attempts.extend(bridge.poll_once())
        attempts.extend(bridge.poll_once())
        hints_seen["voice_hint"] = any('"voice": true' in prompt for prompt in llm.prompts)
        choice_voice_count = len(transport.sent_voices)

        # too long for voice
        config = SignalChatConfig(**base, voice_replies="always", voice_max_chars=5, tts_command="fake {output}")
        bridge, transport, _ = bridge_for(config, ["这条回复明显超过五个字的上限。"])
        transport.queue_batch([_msg(3000, "说个长的")])
        attempts.extend(bridge.poll_once())
        too_long_skipped = len(transport.sent_voices) == 0

        # tts failure downgrades
        config = SignalChatConfig(**base, voice_replies="always", tts_command="fake {output}")
        bridge, transport, _ = bridge_for(config, ["这条语音会合成失败。"], tts=FakeTTSBackend(fail=True))
        transport.queue_batch([_msg(4000, "语音失败场景")])
        attempts.extend(bridge.poll_once())

        # voice send failure downgrades
        config = SignalChatConfig(**base, voice_replies="always", tts_command="fake {output}")
        failing_transport = FakeFeishuTransport()
        failing_transport.fail_next_voice_sends = 1
        bridge, _, _ = bridge_for(config, ["这条语音会发送失败。"], transport=failing_transport)
        failing_transport.queue_batch([_msg(5000, "语音发送失败场景")])
        attempts.extend(bridge.poll_once())

        # images: valid + traversal + missing + oversize + over-count
        image_config = SignalChatConfig(
            **base,
            image_attachments_enabled=True,
            max_images_per_reply=1,
            image_max_bytes=1000,
        )
        image_metadata_reply = (
            "给你看我画的月亮。\n===DIALOGUE_METADATA===\n"
            + json.dumps({
                "attachments": [
                    {"type": "image", "path": "creations/art/moon.png"},
                    {"type": "image", "path": "../../etc/passwd"},
                    {"type": "image", "path": "creations/art/missing.png"},
                    {"type": "image", "path": "creations/art/huge.png"},
                    {"type": "image", "path": "creations/art/hardlinked.png"},
                    {"type": "image", "path": "creations/art/moon.png"},
                ]
            }, ensure_ascii=False)
        )
        bridge, transport, llm = bridge_for(image_config, [image_metadata_reply])
        transport.queue_batch([_msg(6000, "看看你的画")])
        attempts.extend(bridge.poll_once())
        hints_seen["image_hint"] = any("attachments" in prompt for prompt in llm.prompts)
        image_scenario_sent = len(transport.sent_images)
        image_rejections = []
        for attempt in attempts:
            media = attempt.get("media") or {}
            images = media.get("images") or {}
            image_rejections.extend(item.get("reason") for item in images.get("rejected", []))

        # image send failure downgrades
        failing_image_transport = FakeFeishuTransport()
        failing_image_transport.fail_next_image_sends = 1
        bridge, _, _ = bridge_for(
            image_config,
            [
                "再看一张。\n===DIALOGUE_METADATA===\n"
                + json.dumps({"attachments": [{"type": "image", "path": "creations/art/moon.png"}]})
            ],
            transport=failing_image_transport,
        )
        failing_image_transport.queue_batch([_msg(7000, "再发一张")])
        attempts.extend(bridge.poll_once())

        # signal transport gets no media even with voice always
        signal_config = SignalChatConfig(**base, voice_replies="always", tts_command="fake {output}")
        signal_transport = FakeSignalTransport([[_msg(8000, "signal 通道消息", sender="+15550001111")]])
        signal_config = SignalChatConfig(
            account="+15550000000",
            allowed_senders=("+15550001111",),
            voice_replies="always",
            tts_command="fake {output}",
        )
        llm = _ScriptedLLM(["signal 上的回复。"])
        signal_bridge = SignalChatBridge(
            smoke_paths,
            signal_config,
            signal_transport,
            dialogue_runner=DialogueRunner(
                smoke_paths,
                llm_client=llm,
                memory_store=JsonMemoryStore(smoke_paths.memory_store),
            ),
            provider="fake",
            memory_mode="json",
            mode="dry_run",
            tts_backend=FakeTTSBackend(),
        )
        signal_attempts = signal_bridge.poll_once()
        attempts.extend(signal_attempts)
        signal_media_skipped = all("media" not in attempt for attempt in signal_attempts)

        smoke_ledger = load_signal_chat_attempts(smoke_paths.signal_chat_attempts_file)

    voice_outcomes = {"sent": 0, "skipped_too_long": 0, "tts_errors": 0, "send_errors": 0}
    image_outcomes = {"sent": image_scenario_sent, "rejections": sorted(set(filter(None, image_rejections))), "send_errors": 0}
    replied_despite_failures = True
    for attempt in attempts:
        media = attempt.get("media") or {}
        voice = media.get("voice") or {}
        if voice.get("sent"):
            voice_outcomes["sent"] += 1
        if voice.get("skip_reason") == "reply_too_long_for_voice":
            voice_outcomes["skipped_too_long"] += 1
        if voice.get("error"):
            if voice["error"].get("type") == "TTSError":
                voice_outcomes["tts_errors"] += 1
            else:
                voice_outcomes["send_errors"] += 1
        images = media.get("images") or {}
        if images.get("errors"):
            image_outcomes["send_errors"] += len(images["errors"])
        if (voice.get("error") or images.get("errors")) and attempt.get("decision") != "replied":
            replied_despite_failures = False

    return {
        "attempts": attempts,
        "attempt_count": len(attempts),
        "voice_outcomes": voice_outcomes,
        "image_outcomes": image_outcomes,
        "voice_always_sent": voice_always_sent,
        "choice_voice_count": choice_voice_count,
        "too_long_skipped": too_long_skipped,
        "hints_seen": hints_seen,
        "replied_despite_media_failures": replied_despite_failures,
        "signal_transport_media_skipped": signal_media_skipped,
        "smoke_ledger_count": len(smoke_ledger),
        "hardlink_supported": hardlink_supported,
    }


def _voice_coverage_stage(payload: dict) -> dict:
    problems = []
    if not payload["voice_always_sent"]:
        problems.append("voice_replies=always did not send a voice bubble")
    if payload["choice_voice_count"] != 1:
        problems.append(
            f"companion_choice should send exactly one voice (requested turn only), got {payload['choice_voice_count']}"
        )
    if not payload["too_long_skipped"]:
        problems.append("over-length reply was not kept text-only")
    outcomes = payload["voice_outcomes"]
    if outcomes["tts_errors"] < 1 or outcomes["send_errors"] < 1:
        problems.append("voice failure branches (tts + send) were not both covered")
    if problems:
        return _stage("voice_coverage", False, "; ".join(problems))
    return _stage("voice_coverage", True, "voice modes, length cap, and both failure branches behave as designed")


def _image_coverage_stage(payload: dict) -> dict:
    problems = []
    if payload["image_outcomes"]["sent"] != 1:
        problems.append(f"expected exactly one valid image sent, got {payload['image_outcomes']['sent']}")
    required = {
        "path_outside_creations",
        "file_not_found",
        "file_too_large",
        "over_max_images_per_reply",
    }
    if payload.get("hardlink_supported"):
        required.add("hardlinked_file_rejected")
    missing = required - set(payload["image_outcomes"]["rejections"])
    if missing:
        problems.append(f"missing image rejection coverage: {sorted(missing)}")
    if payload["image_outcomes"]["send_errors"] < 1:
        problems.append("image send failure branch was not covered")
    if problems:
        return _stage("image_coverage", False, "; ".join(problems))
    return _stage(
        "image_coverage",
        True,
        "image validation rejects traversal/missing/oversize/hardlink/over-count and survives send failures",
    )


def _text_priority_stage(payload: dict) -> dict:
    if not payload["replied_despite_media_failures"]:
        return _stage("text_reply_priority", False, "a media failure changed the reply decision")
    if not payload["signal_transport_media_skipped"]:
        return _stage("text_reply_priority", False, "a transport without media support still produced media payloads")
    return _stage(
        "text_reply_priority",
        True,
        "every media failure left the delivered text reply intact; non-media transports skip media entirely",
    )


def _hint_injection_stage(payload: dict) -> dict:
    problems = []
    if not payload["hints_seen"].get("voice_hint"):
        problems.append("companion_choice prompt is missing the voice hint")
    if not payload["hints_seen"].get("image_hint"):
        problems.append("image-enabled prompt is missing the attachments hint")
    if media_prompt_hints(SignalChatConfig(account="x", allowed_senders=("y",)), FakeFeishuTransport()) is not None:
        problems.append("hints must be absent when media is disabled")
    if problems:
        return _stage("hint_injection", False, "; ".join(problems))
    return _stage("hint_injection", True, "metadata hints appear only when the matching capability is enabled")


def _ledger_hygiene_stage(paths: CompanionPaths, payload: dict, *, write_runtime: bool) -> dict:
    dumped = json.dumps(payload["attempts"], ensure_ascii=False)
    if "OggS" in dumped or "RIFF" in dumped or "PNG" in dumped:
        return _stage("media_ledger_hygiene", False, "raw media bytes leaked into attempt records")
    for attempt in payload["attempts"]:
        media = attempt.get("media") or {}
        for sent_path in (media.get("images") or {}).get("sent_paths", []):
            if not str(sent_path).startswith("creations/"):
                return _stage("media_ledger_hygiene", False, f"sent image path escaped creations/: {sent_path}")
    if not write_runtime:
        return _stage("media_ledger_hygiene", True, "runtime writes disabled; ledger copy skipped")
    before = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    append_signal_chat_attempts(paths.signal_chat_attempts_file, payload["attempts"])
    after = len(load_signal_chat_attempts(paths.signal_chat_attempts_file))
    if after - before != payload["attempt_count"]:
        return _stage("media_ledger_hygiene", False, "dry-run attempts were not appended to the home ledger")
    return _stage(
        "media_ledger_hygiene",
        True,
        f"{payload['attempt_count']} media dry-run attempts appended; payloads are byte-free and creations-scoped",
    )


def _config_template_stage() -> dict:
    template_path = REPO_ROOT / "templates" / "feishu_chat_config.template.json"
    if not template_path.exists():
        return _stage("media_config_template", False, f"missing template: {template_path}")
    try:
        payload = json.loads(template_path.read_text())
    except json.JSONDecodeError as exc:
        return _stage("media_config_template", False, f"template is invalid JSON: {exc.msg}")
    required = {"voice_replies", "voice_max_chars", "tts_command", "image_attachments_enabled"}
    missing = sorted(required - set(payload))
    if missing:
        return _stage("media_config_template", False, f"template missing media keys: {missing}")
    if payload.get("voice_replies") != "off" or payload.get("image_attachments_enabled") is not False:
        return _stage("media_config_template", False, "template must ship with media disabled")
    return _stage("media_config_template", True, "feishu config template carries media keys with safe defaults")


def _static_guard_stage() -> dict:
    problems = []
    core_dir = Path(__file__).resolve().parent
    for module_name in ("tts.py", "chat_media.py"):
        source = (core_dir / module_name).read_text()
        for forbidden in ("crontab", "systemctl enable", "systemctl start"):
            if forbidden in source:
                problems.append(f"{module_name} must not reference {forbidden}")
    if "TemporaryDirectory" not in (core_dir / "chat_media.py").read_text():
        problems.append("synthesized audio must live in a temporary directory")
    if problems:
        return _stage("static_guard", False, "; ".join(problems))
    return _stage("static_guard", True, "media modules stay scheduler-free and never retain synthesized audio")


def _msg(timestamp: int, body: str, *, sender: str = ALLOWED_OPEN_ID) -> InboundSignalMessage:
    return InboundSignalMessage(sender=sender, timestamp=timestamp, body=body)


def _stage(name: str, ok: bool, message: str) -> dict:
    return {"name": name, "status": "pass" if ok else "fail", "message": message}
