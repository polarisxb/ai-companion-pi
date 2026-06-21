import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from companion_core import (
    CompanionPaths,
    load_scheduler_attempts,
    run_m9_scheduler_dry_run,
    write_m9_scheduler_dry_run_report,
)


EXPECTED_SKIP_REASONS = {
    "paused",
    "quiet_hours",
    "daily_budget_exhausted",
    "min_gap_not_met",
    "wake_lock_active",
    "failure_cooldown",
    "recent_human_chat_dampening",
}


def write_ready_home(home: Path) -> CompanionPaths:
    for name in ("life-loop", "journals", "memory-server", "conversations", "requests", "scripts", "window"):
        (home / name).mkdir(parents=True, exist_ok=True)
    handoff_command = (
        f"cd {home} && .venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {home} --provider deepseek --memory-mode json --trigger scheduled-wake"
    )
    (home / "life-loop" / "m9_scheduler_revalidation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.1",
        "recommendation": "m9_scheduler_revalidation_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "handoff": {
            "ready": True,
            "target_command": handoff_command,
            "target_script": "scripts/run_wake_cycle.py",
            "provider": "deepseek",
            "memory_mode": "json",
            "trigger": "scheduled-wake",
            "scheduler_mutated": False,
            "wake_cycle_run": False,
        },
        "boundaries": {
            "scheduler_mutated": False,
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "life_write_route_added": False,
        },
    }))
    return CompanionPaths(home)


def test_m9_scheduler_dry_run_exercises_controls_and_writes_runtime_evidence(tmp_path):
    paths = write_ready_home(tmp_path)

    result = run_m9_scheduler_dry_run(
        paths,
        random_seed=7,
        base_date=date(2026, 6, 21),
    )
    report = result.to_dict()
    attempts = load_scheduler_attempts(paths.scheduler_attempts_file)
    state = json.loads(paths.scheduler_presence_state_file.read_text())

    assert result.ok is True
    assert result.recommendation == "m9_scheduler_dry_run_ready"
    assert report["milestone"] == "M9.2"
    assert report["provider_calls"] == 0
    assert report["boundaries"]["scheduler_mutated"] is False
    assert report["boundaries"]["wake_cycle_run"] is False
    assert report["boundaries"]["provider_generation_requested"] is False
    assert report["dry_run"]["attempt_count"] == 8
    assert len(attempts) == 8
    assert {attempt["skip_reason"] for attempt in attempts if attempt["skip_reason"]} == EXPECTED_SKIP_REASONS
    assert [attempt["decision"] for attempt in attempts].count("would_run") == 1
    assert all(attempt["provider_calls"] == 0 for attempt in attempts)
    assert all(attempt["wake_cycle_run"] is False for attempt in attempts)
    assert state["dry_run"] is True
    assert state["daily_live_wake_count"] == 1
    assert state["daily_live_wake_budget"] == 2
    assert state["quiet_hours"] == ["00:00", "08:00"]
    assert datetime.fromisoformat(state["next_candidate_after"]) > datetime.fromisoformat(state["last_scheduled_wake_at"])
    assert not paths.wake_events_file.exists()
    assert paths.scheduler_wake_lock_file.exists()


def test_m9_scheduler_dry_run_requires_m9_revalidation_before_runtime_writes(tmp_path):
    paths = CompanionPaths(tmp_path)
    paths.life_loop_dir.mkdir(parents=True)

    result = run_m9_scheduler_dry_run(paths, base_date=date(2026, 6, 21))
    report = result.to_dict()

    assert result.ok is False
    assert report["recommendation"] == "inspect"
    assert "m9_scheduler_revalidation" in report["stop_reasons"]
    assert not paths.scheduler_attempts_file.exists()
    assert not paths.scheduler_presence_state_file.exists()
    assert not paths.wake_events_file.exists()


def test_m9_scheduler_dry_run_preserves_existing_pause_flag(tmp_path):
    paths = write_ready_home(tmp_path)
    paths.scheduler_pause_flag.write_text("paused for operator")

    result = run_m9_scheduler_dry_run(paths, base_date=date(2026, 6, 21))
    report = result.to_dict()

    assert result.ok is True
    assert paths.scheduler_pause_flag.read_text() == "paused for operator"
    assert "paused" in report["dry_run"]["skip_reasons_observed"]
    assert paths.scheduler_presence_state_file.exists()


def test_m9_scheduler_dry_run_can_run_without_runtime_writes(tmp_path):
    paths = write_ready_home(tmp_path)

    result = run_m9_scheduler_dry_run(
        paths,
        base_date=date(2026, 6, 21),
        write_runtime=False,
    )
    report = result.to_dict()

    assert result.ok is True
    assert report["profile"]["writes_scheduler_attempts"] is False
    assert report["dry_run"]["runtime_written"] is False
    assert report["dry_run"]["attempt_count"] == 8
    assert not paths.scheduler_attempts_file.exists()
    assert not paths.scheduler_presence_state_file.exists()


def test_m9_scheduler_dry_run_report_writer_and_cli_write_report(tmp_path):
    paths = write_ready_home(tmp_path)
    result = run_m9_scheduler_dry_run(paths, base_date=date(2026, 6, 21))
    report_path = write_m9_scheduler_dry_run_report(paths, result.to_dict())

    assert report_path == paths.life_loop_dir / "m9_scheduler_dry_run_report.json"
    assert json.loads(report_path.read_text())["recommendation"] == "m9_scheduler_dry_run_ready"

    cli_home = tmp_path / "cli"
    write_ready_home(cli_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m9_scheduler_dry_run.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(cli_home),
            "--base-date",
            "2026-06-21",
            "--seed",
            "7",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m9_scheduler_dry_run_ready"
    assert payload["dry_run"]["attempt_count"] == 8
    assert (cli_home / "life-loop" / "m9_scheduler_dry_run_report.json").exists()
    assert (cli_home / "life-loop" / "scheduler_attempts.jsonl").exists()
    assert (cli_home / "life-loop" / "scheduler_presence_state.json").exists()
