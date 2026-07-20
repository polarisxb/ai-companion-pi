#!/usr/bin/env python3
"""Run the M14.2 supervised real Feishu media trial."""

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
    FeishuTransport,
    SignalChatConfigError,
    load_feishu_chat_config,
    load_local_secrets,
    run_m14_feishu_media_trial,
    write_m14_feishu_media_trial_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the M14.2 supervised Feishu media trial")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--confirm-real-feishu-send", action="store_true")
    parser.add_argument("--image", default=None, help="creations-relative image path to send in the trial")
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
    transport = FeishuTransport(app_id=app_id, require_listener=False)
    result = run_m14_feishu_media_trial(
        paths,
        transport=transport,
        confirm_real_feishu_send=args.confirm_real_feishu_send,
        image_path=args.image,
    )
    report = result.to_dict()
    if not args.no_write_report:
        report_path = write_m14_feishu_media_trial_report(paths, report, args.report_file)
        report["report_file"] = str(report_path.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
