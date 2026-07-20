#!/usr/bin/env python3
"""Run the M13.2 supervised real Feishu reply trial."""

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
    FeishuTransport,
    JsonMemoryStore,
    SemanticFirstMemoryStore,
    SignalChatConfigError,
    create_llm_client,
    load_feishu_chat_config,
    load_local_secrets,
    run_m13_feishu_trial,
    write_m13_feishu_trial_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M13.2 supervised Feishu reply trial")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--confirm-real-feishu-send", action="store_true")
    parser.add_argument("--max-polls", type=int, default=1)
    parser.add_argument("--provider", default=os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"))
    parser.add_argument("--memory-mode", default=os.environ.get("COMPANION_MEMORY_MODE", "json"))
    parser.add_argument("--model", default=os.environ.get("COMPANION_LLM_MODEL"))
    parser.add_argument("--base-url", default=os.environ.get("COMPANION_LLM_BASE_URL"))
    parser.add_argument(
        "--api-key-env",
        default=os.environ.get("COMPANION_LLM_API_KEY_ENV", "COMPANION_LLM_API_KEY"),
    )
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("COMPANION_CHAT_TIMEOUT", "300")))
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    load_local_secrets(paths)

    try:
        config = load_feishu_chat_config(paths)
        app_id = config.account
    except SignalChatConfigError:
        app_id = None
    transport = FeishuTransport(app_id=app_id, timeout_seconds=args.timeout)
    try:
        transport.start_listener()
    except Exception:  # noqa: BLE001 - the gate reports listener/SDK failures as stages.
        pass
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
    result = run_m13_feishu_trial(
        paths,
        transport=transport,
        dialogue_runner=DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store),
        provider=args.provider,
        memory_mode=args.memory_mode,
        confirm_real_feishu_send=args.confirm_real_feishu_send,
        max_polls=args.max_polls,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m13_feishu_trial_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
