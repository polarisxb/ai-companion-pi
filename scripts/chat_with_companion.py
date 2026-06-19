#!/usr/bin/env python3
"""Run one M7 text dialogue turn with the companion."""

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
    DialogueEngine,
    FakeDialogueClient,
    JsonMemoryStore,
    SUPPORTED_LLM_PROVIDERS,
    SemanticFirstMemoryStore,
    create_llm_client,
    load_local_secrets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one companion text-chat turn")
    parser.add_argument("message", nargs="?", help="Human message. If omitted, stdin is read.")
    parser.add_argument("--companion-home", default=None, help="Override COMPANION_HOME")
    parser.add_argument("--conversation-id", default=None, help="Continue a known conversation id")
    parser.add_argument("--fake-llm", action="store_true", help="Use deterministic fake dialogue provider")
    parser.add_argument(
        "--provider",
        choices=SUPPORTED_LLM_PROVIDERS,
        default=None,
        help="LLM provider. Defaults to COMPANION_LLM_PROVIDER or deepseek.",
    )
    parser.add_argument("--claude-bin", default="claude", help="Claude CLI executable")
    parser.add_argument("--model", default=None, help="Model name for HTTP-backed providers")
    parser.add_argument("--base-url", default=None, help="Base URL for HTTP-backed providers")
    parser.add_argument(
        "--api-key-env",
        default="COMPANION_LLM_API_KEY",
        help="Environment variable containing the API key for OpenAI-compatible providers",
    )
    parser.add_argument("--timeout", type=int, default=300, help="LLM provider timeout in seconds")
    parser.add_argument(
        "--memory-mode",
        choices=("json", "dual"),
        default=None,
        help="Memory write mode. Defaults to COMPANION_MEMORY_MODE or json.",
    )
    parser.add_argument("--json", action="store_true", help="Print structured metadata instead of reply-only text")
    args = parser.parse_args()

    if args.fake_llm and args.provider and args.provider != "fake":
        parser.error("--fake-llm cannot be combined with a non-fake --provider")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    message = args.message if args.message is not None else sys.stdin.read()
    paths = CompanionPaths.from_env(args.companion_home)
    load_local_secrets(paths)
    provider = "fake" if args.fake_llm else (args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "deepseek"))
    memory_mode = args.memory_mode or os.environ.get("COMPANION_MEMORY_MODE", "json")
    model = args.model or os.environ.get("COMPANION_LLM_MODEL")
    base_url = args.base_url or os.environ.get("COMPANION_LLM_BASE_URL")

    try:
        llm_client = FakeDialogueClient() if provider == "fake" else create_llm_client(
            provider,
            claude_bin=args.claude_bin,
            timeout_seconds=args.timeout,
            model=model,
            base_url=base_url,
            api_key_env=args.api_key_env,
        )
    except ValueError as exc:
        parser.error(str(exc))

    engine = DialogueEngine(
        paths,
        llm_client,
        memory_store=_create_memory_store(paths, memory_mode),
        provider=provider,
        memory_mode=memory_mode,
    )
    result = engine.run_turn(message, conversation_id=args.conversation_id)
    if args.json:
        print(json.dumps({
            "conversation_id": result.conversation_id,
            "reply": result.reply,
            "transcript": str(result.transcript_path),
            "event": result.event.get("id"),
            "stored_memories": result.stored_memory_ids,
            "memory_proposals": result.memory_proposal_ids,
        }, ensure_ascii=False, indent=2))
    else:
        print(result.reply)
    return 0


def _create_memory_store(paths: CompanionPaths, memory_mode: str):
    if memory_mode == "dual":
        return SemanticFirstMemoryStore(paths.memory_store)
    return JsonMemoryStore(paths.memory_store)


if __name__ == "__main__":
    raise SystemExit(main())
