#!/usr/bin/env python3
"""Run the M11.4 supervised real Signal outbound delivery trial."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import (  # noqa: E402
    CompanionPaths,
    SignalChatConfigError,
    SignalCliTransport,
    load_local_secrets,
    load_signal_chat_config,
    run_m11_outbound_trial,
    write_m11_outbound_trial_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M11.4 supervised Signal outbound trial")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--confirm-real-signal-send", action="store_true")
    parser.add_argument("--max-passes", type=int, default=1)
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
    except SignalChatConfigError:
        account = "unconfigured"
    transport = SignalCliTransport(
        account=account,
        signal_cli_bin=args.signal_cli_bin,
        send_lock_file=paths.life_loop_dir / "signal_send.lock",
    )
    result = run_m11_outbound_trial(
        paths,
        transport=transport,
        confirm_real_signal_send=args.confirm_real_signal_send,
        max_passes=args.max_passes,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m11_outbound_trial_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
