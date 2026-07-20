#!/usr/bin/env python3
"""Run the M10.2 supervised real Signal send trial."""

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
    JsonMemoryStore,
    SemanticFirstMemoryStore,
    SignalChatConfigError,
    SignalCliTransport,
    create_llm_client,
    load_local_secrets,
    load_signal_chat_config,
    run_m10_signal_trial,
    write_m10_signal_trial_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M10.2 supervised Signal send trial")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--confirm-real-signal-send", action="store_true")
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
    parser.add_argument("--signal-cli-bin", default="signal-cli")
    parser.add_argument("--no-write-report", action="store_true")
    parser.add_argument("--report-file", default=None)
    args = parser.parse_args()

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    load_local_secrets(paths)

    try:
        config = load_signal_chat_config(paths)
        account = config.account
        receive_timeout = config.receive_timeout_seconds
    except SignalChatConfigError:
        # The gate itself reports the config failure as a stage.
        account = "unconfigured"
        receive_timeout = 5
    transport = SignalCliTransport(
        account=account,
        signal_cli_bin=args.signal_cli_bin,
        receive_timeout_seconds=receive_timeout,
        send_lock_file=paths.life_loop_dir / "signal_send.lock",
    )
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
    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store),
        provider=args.provider,
        memory_mode=args.memory_mode,
        confirm_real_signal_send=args.confirm_real_signal_send,
        max_polls=args.max_polls,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m10_signal_trial_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
