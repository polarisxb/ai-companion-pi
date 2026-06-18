"""Audit snapshots for raw wake model outputs."""

from __future__ import annotations

import os
from pathlib import Path

from .parser import ParsedWakeOutput
from .paths import CompanionPaths
from .provenance import content_hash
from .repair import RepairResult

STORE_RAW_OUTPUTS_ENV = "COMPANION_STORE_RAW_OUTPUTS"
TRUE_VALUES = {"1", "true", "yes", "on"}


def archive_wake_outputs(
    *,
    paths: CompanionPaths,
    event_id: str,
    initial_raw_output: str,
    initial_parsed: ParsedWakeOutput,
    final_parsed: ParsedWakeOutput,
    repair: RepairResult | None = None,
) -> dict:
    store_raw = should_store_raw_outputs()
    initial_snapshot = _output_snapshot(
        paths=paths,
        event_id=event_id,
        label="initial",
        raw_output=initial_raw_output,
        parsed=initial_parsed,
        store_raw=store_raw,
    )
    final_raw_output = _final_raw_output(initial_raw_output, repair)
    final_snapshot = _output_snapshot(
        paths=paths,
        event_id=event_id,
        label="final",
        raw_output=final_raw_output,
        parsed=final_parsed,
        store_raw=store_raw and content_hash(final_raw_output) != initial_snapshot["content_hash"],
    )
    if (
        store_raw
        and final_snapshot["content_hash"] == initial_snapshot["content_hash"]
        and initial_snapshot["raw_output_stored"]
    ):
        final_snapshot["raw_output_stored"] = True
        final_snapshot["raw_output_path"] = initial_snapshot["raw_output_path"]
    return {
        "raw_output_storage": "enabled" if store_raw else "hash_only",
        "initial": initial_snapshot,
        "final": final_snapshot,
        "repair_attempts": [
            _repair_attempt_snapshot(
                paths=paths,
                event_id=event_id,
                attempt=attempt,
                store_raw=store_raw,
            )
            for attempt in (repair.attempts if repair else [])
        ],
    }


def should_store_raw_outputs() -> bool:
    return os.environ.get(STORE_RAW_OUTPUTS_ENV, "").strip().lower() in TRUE_VALUES


def _output_snapshot(
    *,
    paths: CompanionPaths,
    event_id: str,
    label: str,
    raw_output: str,
    parsed: ParsedWakeOutput,
    store_raw: bool,
) -> dict:
    raw_path = _write_raw_output(
        paths=paths,
        event_id=event_id,
        label=label,
        raw_output=raw_output,
    ) if store_raw else None
    return {
        "content_hash": content_hash(raw_output),
        "raw_output_stored": raw_path is not None,
        "raw_output_path": _relative_to_home(paths, raw_path) if raw_path else None,
        "sections": sorted(parsed.raw_sections.keys()),
        "journal_chars": len(parsed.journal.strip()),
        "grounding_claim_count": len(parsed.grounding_claims),
        "memory_count": len(parsed.memories),
        "request_count": len(parsed.requests),
    }


def _repair_attempt_snapshot(
    *,
    paths: CompanionPaths,
    event_id: str,
    attempt,
    store_raw: bool,
) -> dict:
    raw_path = _write_raw_output(
        paths=paths,
        event_id=event_id,
        label=f"repair_{attempt.attempt}",
        raw_output=attempt.raw_output,
    ) if store_raw and attempt.raw_output else None
    return {
        "attempt": attempt.attempt,
        "status": attempt.status,
        "reason": attempt.reason,
        "content_hash": attempt.output_hash,
        "raw_output_stored": raw_path is not None,
        "raw_output_path": _relative_to_home(paths, raw_path) if raw_path else None,
    }


def _final_raw_output(initial_raw_output: str, repair: RepairResult | None) -> str:
    if repair and repair.succeeded and repair.attempts:
        return repair.attempts[-1].raw_output
    return initial_raw_output


def _write_raw_output(
    *,
    paths: CompanionPaths,
    event_id: str,
    label: str,
    raw_output: str,
) -> Path:
    paths.model_outputs_dir.mkdir(parents=True, exist_ok=True)
    path = paths.model_outputs_dir / f"{event_id}_{label}.txt"
    path.write_text(raw_output)
    return path


def _relative_to_home(paths: CompanionPaths, path: Path) -> str:
    return str(path.relative_to(paths.home))
