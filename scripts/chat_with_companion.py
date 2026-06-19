#!/usr/bin/env python3
"""Run one M7 text dialogue turn from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_core import CompanionPaths, DialogueRunner, JsonMemoryStore, SemanticFirstMemoryStore, create_llm_client, load_local_secrets
from companion_core.llm import SUPPORTED_LLM_PROVIDERS


class StaticDialogueClient:
    def __init__(self, response: str):
        self.response = response

    def generate(self, prompt, context):
        return self.response


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one user-initiated M7 text dialogue turn.")
    parser.add_argument("message", nargs="?", help="Human text. If omitted, stdin is read.")
    parser.add_argument("--companion-home", default=None)
    parser.add_argument("--conversation-id", default=None)
    parser.add_argument("--provider", choices=SUPPORTED_LLM_PROVIDERS, default=None)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key-env", default="COMPANION_LLM_API_KEY")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--memory-mode", choices=("json", "dual"), default=None)
    parser.add_argument("--fake-response", default=None, help="Deterministic local response for smoke tests; bypasses provider calls.")
    parser.add_argument("--json", action="store_true", help="Print structured result instead of only companion reply.")
    args = parser.parse_args()

    human_text = args.message if args.message is not None else sys.stdin.read()
    if not human_text.strip():
        parser.error("message or stdin text is required")
    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    paths = CompanionPaths.from_env(args.companion_home)
    paths.ensure_runtime_dirs()
    load_local_secrets(paths)
    provider = args.provider or os.environ.get("COMPANION_LLM_PROVIDER", "deepseek")
    memory_mode = args.memory_mode or os.environ.get("COMPANION_MEMORY_MODE", "json")
    model = args.model or os.environ.get("COMPANION_LLM_MODEL")
    base_url = args.base_url or os.environ.get("COMPANION_LLM_BASE_URL")

    if args.fake_response is not None:
        llm_client = StaticDialogueClient(args.fake_response)
        provider = "fake"
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

    memory_store = SemanticFirstMemoryStore(paths.memory_store) if memory_mode == "dual" else JsonMemoryStore(paths.memory_store)
    result = DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store).run_turn(
        human_text,
        conversation_id=args.conversation_id,
        provider=provider,
        memory_mode=memory_mode,
    )
    if args.json:
        print(json.dumps({
            "conversation_id": result.conversation_id,
            "reply": result.reply,
            "transcript": str(result.transcript_path),
            "event": result.event["id"],
            "memory_ids": [memory["id"] for memory in result.stored_memories],
            "memory_proposal_ids": [proposal["id"] for proposal in result.memory_proposals],
        }, ensure_ascii=False, indent=2))
    else:
        print(result.reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
