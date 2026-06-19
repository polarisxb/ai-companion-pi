#!/usr/bin/env python3
"""Run one M7 text-dialogue turn from the CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from companion_core import CompanionPaths, JsonMemoryStore, SemanticFirstMemoryStore, SUPPORTED_LLM_PROVIDERS, create_llm_client
from companion_core.dialogue import DialogueRunner
from companion_core.secrets import load_local_secrets


class FakeDialogueLLM:
    """Natural deterministic text-dialogue fake, separate from wake-cycle fake output."""

    def generate(self, prompt, context):
        return "我在这里。这个本地 M7 文本对话烟测没有运行唤醒循环。"


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with the companion for one text turn")
    parser.add_argument("message", nargs="?", help="Human message. If omitted, stdin is read.")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument("--provider", choices=SUPPORTED_LLM_PROVIDERS, default=None, help="LLM provider")
    parser.add_argument("--fake-llm", action="store_true", help="Use deterministic fake provider")
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable")
    parser.add_argument("--model", default=None, help="Model for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument("--api-key-env", default="COMPANION_LLM_API_KEY", help="API key environment variable")
    parser.add_argument("--timeout", type=int, default=300, help="Provider timeout seconds")
    parser.add_argument("--memory-mode", choices=("json", "dual"), default=None, help="Memory backend")
    parser.add_argument("--conversation-id", default=None, help="Continue an existing conversation id")
    parser.add_argument("--json", action="store_true", help="Print machine-readable result metadata")
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout must be at least 1")
    provider = "fake" if args.fake_llm else (args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "claude-cli"))
    if args.fake_llm and args.provider and args.provider != "fake":
        parser.error("--fake-llm cannot be combined with a non-fake --provider")

    message = args.message if args.message is not None else sys.stdin.read()
    if not message.strip():
        parser.error("message is required")

    paths = CompanionPaths.from_env(args.companion_home)
    load_local_secrets(paths)
    memory_mode = args.memory_mode or os.environ.get("COMPANION_MEMORY_MODE", "json")
    model = args.model or os.environ.get("COMPANION_LLM_MODEL")
    base_url = args.base_url or os.environ.get("COMPANION_LLM_BASE_URL")
    if provider == "fake":
        llm_client = FakeDialogueLLM()
    else:
        try:
            llm_client = create_llm_client(
                provider,
                claude_bin=args.claude_bin,
                timeout_seconds=args.timeout,
                model=model,
                base_url=base_url,
                api_key_env=args.api_key_env,
            )
        except ValueError as exc:
            parser.error(str(exc))
    runner = DialogueRunner(
        paths,
        llm_client=llm_client,
        memory_store=_create_memory_store(paths, memory_mode),
        provider=provider,
    )
    try:
        result = runner.run_turn(message, conversation_id=args.conversation_id)
    except Exception as exc:
        print(f"Dialogue failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "reply": result.reply,
            "conversation_id": result.conversation_id,
            "turn_id": result.turn_id,
            "transcript": str(result.transcript_path),
            "event": result.event.get("id"),
            "memory_ids": [memory.get("id") for memory in result.accepted_memories],
            "memory_proposal_ids": [proposal.get("id") for proposal in result.memory_proposals],
        }, indent=2))
    else:
        print(result.reply)
        print(f"\n[transcript: {result.transcript_path}]")
    return 0


def _create_memory_store(paths: CompanionPaths, memory_mode: str):
    if memory_mode == "dual":
        return SemanticFirstMemoryStore(paths.memory_store)
    return JsonMemoryStore(paths.memory_store)


if __name__ == "__main__":
    raise SystemExit(main())
