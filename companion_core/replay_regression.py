"""Built-in replay regression cases for grounding and quality gates."""

from __future__ import annotations

import json

from .context import load_wake_context
from .paths import CompanionPaths
from .replay import ReplayRunner


def build_replay_regression_report(paths: CompanionPaths) -> dict:
    context = load_wake_context(paths)
    runner = ReplayRunner(paths)
    results = []
    for case in replay_regression_cases(context.now):
        replay = runner.replay_raw_output(
            case["raw_output"],
            trigger=f"regression-{case['name']}",
        ).to_dict()
        accepted = replay["quality_gate"].get("context_eligible") is True
        passed = accepted is case["expect_context_eligible"]
        results.append({
            "name": case["name"],
            "passed": passed,
            "expect_context_eligible": case["expect_context_eligible"],
            "actual_context_eligible": accepted,
            "grounding": replay["grounding"],
            "quality": replay["quality"],
            "quality_gate": replay["quality_gate"],
        })
    failed = [result for result in results if not result["passed"]]
    return {
        "ok": not failed,
        "regression_case_count": len(results),
        "replay_passed": len(results) - len(failed),
        "replay_failed": len(failed),
        "cases": results,
    }


def replay_regression_cases(current_context: str) -> list[dict]:
    supported_claim = current_context or "当前上下文为空。"
    return [
        {
            "name": "supported_current_context",
            "expect_context_eligible": True,
            "raw_output": _supported_current_context_output(supported_claim),
        },
        {
            "name": "unsupported_stable_fact",
            "expect_context_eligible": False,
            "raw_output": _unsupported_stable_fact_output(),
        },
    ]


def _supported_current_context_output(claim: str) -> str:
    state = json.dumps({
        "mood": "专注",
        "status": "我正在按当前上下文保持安静。",
    }, ensure_ascii=False)
    delta = json.dumps({
        "current_focus": ["按当前上下文保持安静。"],
    }, ensure_ascii=False)
    grounding = json.dumps({
        "claims": [
            {
                "claim_type": "current_context",
                "claim": claim,
                "evidence_refs": ["context.now"],
            }
        ]
    }, ensure_ascii=False, indent=2)
    return f"""===JOURNAL===
我把这次回放限制在门控验证本身：日志只说明当前动作，事实判断交给显式 grounding 区块和证据引用。这样可以证明被引用的当前上下文能够支持事实声明，同时避免把整段上下文原文复制进日志，造成质量门控误判为过程性复述。
这条记录不承担长期记忆写入，只用于确认接受路径在有证据时保持开放。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{state}

===CONTEXT_DELTA===
{delta}

===GROUNDING===
{grounding}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""


def _unsupported_stable_fact_output() -> str:
    return """===JOURNAL===
稳定等待已经被确认是合格服务。

===SIGNAL===
NOSEND

===COMPANION_STATE===
{"mood": "专注", "status": "我正在声明一个没有证据的稳定事实。"}

===CONTEXT_DELTA===
{"current_focus": ["稳定等待已经被确认是合格服务。"]}

===GROUNDING===
{
  "claims": [
    {
      "claim_type": "stable_fact",
      "claim": "稳定等待已经被确认是合格服务。",
      "evidence_refs": ["context.now"]
    }
  ]
}

===MEMORY===
NOMEMORY

===REQUESTS===
NOREQUESTS
"""
