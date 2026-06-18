"""Context acceptance decisions for wake artifacts."""

from __future__ import annotations

from datetime import datetime

ADVISORY_WARNING_PREFIXES = (
    "journal is short",
)


def decide_context_acceptance(quality: dict) -> dict:
    warnings = list(quality.get("warnings", []))
    blocking = [
        warning
        for warning in warnings
        if not _is_advisory_warning(warning)
    ]
    decision = "rejected" if blocking else "accepted"
    return {
        "decision": decision,
        "context_eligible": decision == "accepted",
        "blocking_warnings": blocking,
        "advisory_warnings": [
            warning for warning in warnings if _is_advisory_warning(warning)
        ],
        "checked_at": datetime.now().isoformat(),
    }


def is_context_eligible(decision: dict | None) -> bool:
    return bool(decision and decision.get("context_eligible"))


def _is_advisory_warning(warning: str) -> bool:
    return any(
        warning.startswith(prefix)
        for prefix in ADVISORY_WARNING_PREFIXES
    )
