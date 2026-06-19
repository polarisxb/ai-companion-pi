#!/usr/bin/env python3
"""Run one M7 text dialogue turn from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from companion_core import CompanionPaths, DialogueRunner, JsonMemoryStore, SemanticFirstMemoryStore, create_llm_client, load_local_secrets
from companion_core.dialogue import _clean_visible_text
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
    parser.add_argument("--interactive", action="store_true", help="Start a continuous local REPL using one conversation id.")
    args = parser.parse_args()

    human_text = args.message if args.message is not None else (None if args.interactive else sys.stdin.read())
    if not args.interactive and not human_text.strip():
        parser.error("message or stdin text is required")
    if args.interactive and args.message is not None:
        parser.error("message is not used with --interactive; type turns at the prompt")
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
    runner = DialogueRunner(paths, llm_client=llm_client, memory_store=memory_store)
    if args.interactive:
        return run_interactive(
            runner,
            conversation_id=args.conversation_id or _new_repl_conversation_id(),
            provider=provider,
            memory_mode=memory_mode,
            json_output=args.json,
        )

    try:
        result = runner.run_turn(human_text, conversation_id=args.conversation_id, provider=provider, memory_mode=memory_mode)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {_clean_visible_text(str(exc))}", file=sys.stderr)
        return 1
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


def run_interactive(
    runner: DialogueRunner,
    *,
    conversation_id: str,
    provider: str,
    memory_mode: str,
    json_output: bool = False,
) -> int:
    print(f"M7 interactive dialogue started: conversation_id={conversation_id}", file=sys.stderr)
    print("Type exit or quit to end. Type /retry to resend the last failed input.", file=sys.stderr)
    failed_text: str | None = None
    while True:
        try:
            print("you> ", end="", file=sys.stderr, flush=True)
            line = input()
        except EOFError:
            print("", file=sys.stderr)
            return 0
        command = line.strip().lower()
        if command in {"exit", "quit"}:
            return 0
        if command == "/retry":
            if failed_text is None:
                print("No failed input is available to retry.", file=sys.stderr)
                continue
            human_text = failed_text
        else:
            human_text = line
        if not human_text.strip():
            continue
        try:
            result = runner.run_turn(
                human_text,
                conversation_id=conversation_id,
                provider=provider,
                memory_mode=memory_mode,
                auto_memory=False,
            )
        except Exception as exc:  # preserve failed input for /retry and transcript audit
            failed_text = human_text
            print(f"ERROR: {type(exc).__name__}: {_clean_visible_text(str(exc))}", file=sys.stderr)
            print("Input preserved. Type /retry to resend it, edit and send a new line, or exit.", file=sys.stderr)
            continue
        failed_text = None
        if json_output:
            print(json.dumps({
                "conversation_id": result.conversation_id,
                "reply": result.reply,
                "transcript": str(result.transcript_path),
                "event": result.event["id"],
                "memory_ids": [memory["id"] for memory in result.stored_memories],
                "memory_proposal_ids": [proposal["id"] for proposal in result.memory_proposals],
            }, ensure_ascii=False, sort_keys=True))
        else:
            print(f"companion> {result.reply}")


def _new_repl_conversation_id() -> str:
    return f"conv_repl_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"


if __name__ == "__main__":
    raise SystemExit(main())
