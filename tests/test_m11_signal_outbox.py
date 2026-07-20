import json
from pathlib import Path

from companion_core import (
    CompanionPaths,
    LifeLoopRunner,
    load_signal_outbox_entries,
    normalize_signal_section,
)


class StaticLLMClient:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt, context):
        return self.output


def write_minimal_context(home: Path):
    context_dir = home / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "who_is_companion.txt").write_text("You are a continuity-focused companion.")
    (context_dir / "who_is_human.txt").write_text("The human is developing your internal life loop.")
    (context_dir / "now.txt").write_text("今天只需要平稳收束，并等待用户给出下一步事实。")


def wake_output(signal_section: str) -> str:
    return f"""===JOURNAL===
今天只需要平稳收束，并等待用户给出下一步事实。我会按当前上下文保持安静。

===SIGNAL===
{signal_section}

===COMPANION_STATE===
{{"mood": "专注", "status": "我正在按当前上下文保持安静。"}}

===CONTEXT_DELTA===
{{"current_focus": ["按当前上下文保持安静。"]}}

===GROUNDING===
{{
  "claims": [
    {{
      "claim_type": "current_context",
      "claim": "今天只需要平稳收束，并等待用户给出下一步事实。",
      "evidence_refs": ["context.now"]
    }}
  ]
}}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


def rejected_wake_output(signal_section: str) -> str:
    return f"""===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
{signal_section}

===COMPANION_STATE===
{{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}}

===GROUNDING===
{{
  "claims": [
    {{
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }}
  ]
}}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


def run_wake(tmp_path, output: str):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)
    runner = LifeLoopRunner(paths, llm_client=StaticLLMClient(output))
    result = runner.run_once(trigger="m11-outbox-test", provider="fake")
    return paths, result


# --- normalization ---


def test_normalize_signal_section_handles_nosend_and_noise():
    assert normalize_signal_section("NOSEND") is None
    assert normalize_signal_section(" nosend. ") is None
    assert normalize_signal_section("NOSEND。") is None
    assert normalize_signal_section("") is None
    assert normalize_signal_section("   \n ") is None
    assert normalize_signal_section(None) is None
    assert normalize_signal_section("今晚的月亮很亮。\n想让你也看看。") == "今晚的月亮很亮。 想让你也看看。"


def test_normalize_signal_section_redacts_secret_like_text():
    result = normalize_signal_section("api_key=sk-abcdefghijklmnop 之后再说")
    assert result is not None
    assert "[REDACTED_SECRET]" in result
    assert "sk-abcdefghijklmnop" not in result


# --- lifecycle capture ---


def test_accepted_wake_with_signal_captures_one_outbox_entry(tmp_path):
    paths, result = run_wake(tmp_path, wake_output("今晚的月亮很亮，想让你也看看。"))

    assert result.quality_gate["context_eligible"] is True
    entries = load_signal_outbox_entries(paths.signal_outbox_file)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["content"] == "今晚的月亮很亮，想让你也看看。"
    assert entry["source_event_id"] == result.event["id"]
    assert entry["trigger"] == "m11-outbox-test"
    assert str(entry["content_hash"]).startswith("sha256:")

    assert result.signal_outbox_entry["id"] == entry["id"]
    event_meta = result.event["signal_outbox"]
    assert event_meta["captured"] is True
    assert event_meta["entry_id"] == entry["id"]
    assert event_meta["content_hash"] == entry["content_hash"]
    assert "content" not in event_meta
    assert result.event["suppressed"]["signal_capture"] is False

    # The wake event ledger itself must not contain the message text.
    events_text = paths.wake_events_file.read_text()
    assert "今晚的月亮很亮" not in events_text


def test_accepted_wake_with_nosend_captures_nothing(tmp_path):
    paths, result = run_wake(tmp_path, wake_output("NOSEND"))

    assert result.quality_gate["context_eligible"] is True
    assert load_signal_outbox_entries(paths.signal_outbox_file) == []
    assert result.signal_outbox_entry is None
    assert "signal_outbox" not in result.event
    assert result.event["suppressed"]["signal_capture"] is False


def test_rejected_wake_suppresses_signal_capture(tmp_path):
    paths, result = run_wake(tmp_path, rejected_wake_output("被拒绝唤醒里的消息不应该发出。"))

    assert result.quality_gate["context_eligible"] is False
    assert load_signal_outbox_entries(paths.signal_outbox_file) == []
    assert result.signal_outbox_entry is None
    assert result.event["suppressed"]["signal_capture"] is True


def test_wake_prompt_offers_optional_signal_instead_of_hardcoded_nosend(tmp_path):
    write_minimal_context(tmp_path)
    paths = CompanionPaths.from_env(tmp_path)

    class Capturing(StaticLLMClient):
        def __init__(self, output):
            super().__init__(output)
            self.prompts = []

        def generate(self, prompt, context):
            self.prompts.append(prompt)
            return super().generate(prompt, context)

    client = Capturing(wake_output("NOSEND"))
    LifeLoopRunner(paths, llm_client=client).run_once(trigger="prompt-check", provider="fake")

    prompt = client.prompts[0]
    assert "NOSEND for the first milestone." not in prompt
    assert "Optional short Signal message" in prompt
    assert "never assume or claim the message was already sent" in prompt
