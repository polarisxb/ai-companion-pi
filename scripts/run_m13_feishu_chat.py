#!/usr/bin/env python3
"""Run the M13 Feishu chat bridge.

Modes:

- ``--check``: read-only readiness diagnostics (config, secrets, SDK, freeze
  evidence). No traffic.
- ``--fake``: fake transport plus deterministic fake dialogue model for local
  smoke runs. Never touches the Feishu API or a real provider.
- real mode: requires a valid config, Feishu credentials, the lark-oapi SDK,
  passing M7/M8/M9 freeze evidence, and the explicit
  ``--confirm-real-feishu-send`` flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (  # noqa: E402
    CompanionPaths,
    DialogueRunner,
    FakeFeishuTransport,
    FeishuTransport,
    InboundSignalMessage,
    JsonMemoryStore,
    SemanticFirstMemoryStore,
    SignalChatBridge,
    SignalChatConfig,
    SignalChatConfigError,
    StaticDialogueLLMClient,
    create_llm_client,
    load_feishu_chat_config,
    load_local_secrets,
    load_m10_freeze_evidence,
    load_signal_chat_state,
)

FAKE_DEFAULT_ACCOUNT = "cli_fake_app"
FAKE_DEFAULT_SENDER = "ou_fake_human"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M13 Feishu chat bridge")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--check", action="store_true", help="print readiness diagnostics and exit")
    parser.add_argument("--fake", action="store_true", help="use fake transport and fake dialogue model")
    parser.add_argument("--fake-sender", default=None)
    parser.add_argument("--fake-message", action="append", default=[])
    parser.add_argument("--once", action="store_true", help="run exactly one poll and exit")
    parser.add_argument("--max-polls", type=int, default=None)
    parser.add_argument("--provider", default=os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"))
    parser.add_argument("--memory-mode", default=os.environ.get("COMPANION_MEMORY_MODE", "json"))
    parser.add_argument("--model", default=os.environ.get("COMPANION_LLM_MODEL"))
    parser.add_argument("--base-url", default=os.environ.get("COMPANION_LLM_BASE_URL"))
    parser.add_argument(
        "--api-key-env",
        default=os.environ.get("COMPANION_LLM_API_KEY_ENV", "COMPANION_LLM_API_KEY"),
    )
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("COMPANION_CHAT_TIMEOUT", "300")))
    parser.add_argument(
        "--confirm-real-feishu-send",
        action="store_true",
        help="explicitly allow real Feishu traffic",
    )
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()

    if args.check:
        return _run_check(paths, args)
    if args.fake:
        return _run_fake(paths, args)
    return _run_real(paths, args)


def _readiness(paths: CompanionPaths, args: argparse.Namespace) -> dict:
    load_local_secrets(paths)
    config_status: dict = {"path": str(paths.feishu_chat_config_file), "ok": False}
    config = None
    try:
        config = load_feishu_chat_config(paths)
        config_status.update(
            ok=True,
            account_present=bool(config.account),
            allowed_sender_count=len(config.allowed_senders),
        )
    except SignalChatConfigError as exc:
        config_status["error"] = str(exc)

    transport_status: dict = {"ok": False}
    if config is not None:
        transport = FeishuTransport(app_id=config.account)
        try:
            transport.check_available()
            transport_status.update(ok=True, app_id_present=True, sdk_available=True)
        except Exception as exc:  # noqa: BLE001 - diagnostics report every failure kind.
            transport_status["error"] = str(exc)
    else:
        transport_status["error"] = "config must load before feishu credentials are checked"

    freeze = load_m10_freeze_evidence(paths)
    pause = paths.signal_chat_pause_flag.exists()
    ready = bool(config_status["ok"] and transport_status["ok"] and freeze["ok"] and not pause)
    return {
        "ready": ready,
        "mode": "check",
        "companion_home": str(paths.home),
        "config": config_status,
        "feishu": transport_status,
        "freeze_evidence": freeze,
        "pause_flag_present": pause,
        "confirm_flag_required": True,
    }


def _run_check(paths: CompanionPaths, args: argparse.Namespace) -> int:
    diagnostics = _readiness(paths, args)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if diagnostics["ready"] else 1


def _run_fake(paths: CompanionPaths, args: argparse.Namespace) -> int:
    try:
        config = load_feishu_chat_config(paths)
    except SignalChatConfigError:
        sender = args.fake_sender or FAKE_DEFAULT_SENDER
        config = SignalChatConfig(account=FAKE_DEFAULT_ACCOUNT, allowed_senders=(sender,))
    sender = args.fake_sender or config.allowed_senders[0]
    messages = args.fake_message or ["你好,这是一条本地假消息。"]
    base_timestamp = _fake_base_timestamp(paths)
    batch = [
        InboundSignalMessage(sender=sender, timestamp=base_timestamp + index, body=body)
        for index, body in enumerate(messages)
    ]
    transport = FakeFeishuTransport([batch])
    fake_reply = os.environ.get("COMPANION_CHAT_FAKE_RESPONSE")
    llm_client = StaticDialogueLLMClient(fake_reply) if fake_reply else StaticDialogueLLMClient()
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=DialogueRunner(paths, llm_client=llm_client, memory_store=JsonMemoryStore(paths.memory_store)),
        provider="fake",
        memory_mode="json",
        mode="fake",
        lock_path=paths.feishu_chat_lock_file,
    )
    attempts = bridge.run_loop(max_polls=1)
    print(json.dumps(
        {"mode": "fake", "attempts": attempts, "outbound": transport.sent},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))
    return 0


def _run_real(paths: CompanionPaths, args: argparse.Namespace) -> int:
    diagnostics = _readiness(paths, args)
    if not args.confirm_real_feishu_send:
        diagnostics["error"] = "real feishu traffic requires --confirm-real-feishu-send"
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    if not diagnostics["ready"]:
        diagnostics["error"] = "readiness checks failed; refusing real feishu traffic"
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    config = load_feishu_chat_config(paths)
    transport = FeishuTransport(app_id=config.account, timeout_seconds=args.timeout)
    transport.start_listener()
    llm_client = create_llm_client(
        args.provider,
        timeout_seconds=args.timeout,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
    )
    memory_store = (
        SemanticFirstMemoryStore(paths.memory_store)
        if args.memory_mode == "dual"
        else JsonMemoryStore(paths.memory_store)
    )
    bridge = SignalChatBridge(
        paths,
        config,
        transport,
        dialogue_runner=DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store),
        provider=args.provider,
        memory_mode=args.memory_mode,
        mode="live",
        lock_path=paths.feishu_chat_lock_file,
    )
    max_polls = 1 if args.once else args.max_polls
    try:
        attempts = bridge.run_loop(max_polls=max_polls)
    except KeyboardInterrupt:
        print(json.dumps({"mode": "live", "stopped": "keyboard_interrupt"}, ensure_ascii=False))
        return 0
    print(json.dumps(
        {
            "mode": "live",
            "polls": max_polls,
            "attempt_count": len(attempts),
            "decisions": _decision_counts(attempts),
            "listener_error": transport.listener_error,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))
    return 0


def _decision_counts(attempts: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for attempt in attempts:
        counts[attempt["decision"]] = counts.get(attempt["decision"], 0) + 1
    return counts


def _fake_base_timestamp(paths: CompanionPaths) -> int:
    state = load_signal_chat_state(paths.signal_chat_state_file)
    highest = 0
    for sender_state in state["senders"].values():
        last = sender_state.get("last_timestamp")
        if isinstance(last, int) and last > highest:
            highest = last
    return highest + 1


if __name__ == "__main__":
    raise SystemExit(main())
