#!/usr/bin/env python3
"""Run M7 text dialogue turns from the terminal."""

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
    parser = argparse.ArgumentParser(description="Start user-initiated M7 text dialogue.")
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

    if args.message is not None:
        human_text = args.message
    elif args.interactive:
        human_text = ""
    else:
        human_text = sys.stdin.read()
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
        return run_interactive_session(
            runner,
            initial_text=human_text if human_text.strip() else None,
            conversation_id=args.conversation_id,
            provider=provider,
            memory_mode=memory_mode,
            json_output=args.json,
        )

    try:
        result = runner.run_turn(
            human_text,
            conversation_id=args.conversation_id,
            provider=provider,
            memory_mode=memory_mode,
        )
    except Exception as exc:  # noqa: BLE001 - CLI keeps provider/preflight errors terse and redacted.
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


def run_interactive_session(
    runner: DialogueRunner,
    *,
    initial_text: str | None,
    conversation_id: str | None,
    provider: str,
    memory_mode: str,
    json_output: bool,
) -> int:
    """Run a small local REPL without wake/scheduler side effects."""

    conversation_id = conversation_id or f"conv_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}"
    print(f"M7 interactive dialogue started. conversation_id={conversation_id}", file=sys.stderr)
    print("Type exit or quit to leave.", file=sys.stderr)
    pending_text = initial_text
    while True:
        if pending_text is None:
            line = _read_repl_line("you> ")
            if line is None:
                print("", file=sys.stderr)
                break
            if line.strip().lower() in {"exit", "quit"}:
                break
            if not line.strip():
                continue
            current_text = line
        else:
            current_text = pending_text
            pending_text = None

        try:
            result = runner.run_turn(
                current_text,
                conversation_id=conversation_id,
                provider=provider,
                memory_mode=memory_mode,
                auto_memory=False,
            )
        except Exception as exc:  # keep failed human input available for retry
            pending_text = current_text
            print(f"turn failed: {type(exc).__name__}: {_clean_visible_text(str(exc))}", file=sys.stderr)
            print("Press Enter to retry the same input, type replacement text, or type exit/quit.", file=sys.stderr)
            line = _read_repl_line("retry> ")
            if line is None:
                print("", file=sys.stderr)
                break
            if line.strip().lower() in {"exit", "quit"}:
                break
            if line.strip():
                pending_text = line
            continue

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
    print(f"M7 interactive dialogue ended. conversation_id={conversation_id}", file=sys.stderr)
    return 0


def _read_repl_line(prompt: str) -> str | None:
    print(prompt, end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if line == "":
        return None
    return line.rstrip("\n")


if __name__ == "__main__":
    raise SystemExit(main())
