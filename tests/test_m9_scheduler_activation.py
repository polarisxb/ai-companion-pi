import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from companion_core import (
    CompanionPaths,
    WakeCommandResult,
    build_m9_cron_line,
    initialize_scheduler_presence_state,
    load_scheduler_attempts,
    run_m9_scheduler_activation,
    run_m9_scheduler_disable,
    run_m9_scheduler_tick,
)


MARKER = "digital-life-m9-scheduler-m9.3"


class FakeCrontab:
    def __init__(self, text: str = ""):
        self.text = text
        self.write_count = 0

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> None:
        self.text = text
        self.write_count += 1


def write_ready_home(home: Path) -> CompanionPaths:
    for name in ("life-loop", "journals", "memory-server", "conversations", "requests", "scripts", "window"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "scripts" / "run_m9_scheduler_tick.py").write_text("# tick wrapper placeholder\n")
    (home / "scripts" / "run_wake_cycle.py").write_text("# wake placeholder\n")
    handoff_command = (
        f"cd {home} && .venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {home} --provider deepseek --memory-mode json --trigger scheduled-wake"
    )
    life_loop = home / "life-loop"
    (life_loop / "m9_scheduler_revalidation_report.json").write_text(json.dumps({
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
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
    }))
    (life_loop / "m9_scheduler_dry_run_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.2",
        "recommendation": "m9_scheduler_dry_run_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "dry_run": {
            "target_command": handoff_command,
            "attempt_count": 8,
            "skip_reasons_observed": [
                "daily_budget_exhausted",
                "failure_cooldown",
                "min_gap_not_met",
                "paused",
                "quiet_hours",
                "recent_human_chat_dampening",
                "wake_lock_active",
            ],
            "wake_commands_simulated": 1,
        },
        "boundaries": {
            "scheduler_mutated": False,
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
    }))
    return CompanionPaths(home)


def write_activation_report(paths: CompanionPaths) -> None:
    (paths.life_loop_dir / "m9_scheduler_activation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.3",
        "recommendation": "m9_scheduler_activation_ready",
        "stop_reasons": [],
    }))


def write_live_state(paths: CompanionPaths, *, now: datetime, next_candidate_delta_minutes: int = -30) -> None:
    state = {
        "last_scheduled_wake_at": None,
        "next_candidate_after": (now + timedelta(minutes=next_candidate_delta_minutes)).isoformat(),
        "daily_live_wake_budget": 2,
        "daily_live_wake_count": 0,
        "daily_budget_date": now.date().isoformat(),
        "quiet_hours": ["00:00", "08:00"],
        "min_gap_minutes": 180,
        "cooldown_until": None,
        "last_skip_reason": None,
        "scheduled_wake_output": "internal_only",
        "cadence_model": "randomized_presence_windows",
    }
    paths.scheduler_presence_state_file.write_text(json.dumps(state))


def test_m9_scheduler_activation_installs_one_cron_artifact_and_records_rollback(tmp_path):
    paths = write_ready_home(tmp_path)
    fake_cron = FakeCrontab("MAILTO=ops@example.test\n")
    now = datetime(2026, 6, 21, 19, 45)

    result = run_m9_scheduler_activation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        now=now,
        random_seed=7,
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m9_scheduler_activation_ready"
    assert fake_cron.write_count == 1
    assert fake_cron.text.count(MARKER) == 1
    assert fake_cron.text.startswith("MAILTO=ops@example.test\n")
    assert report["scheduler"]["artifact"]["line"] == build_m9_cron_line(paths)
    assert report["scheduler"]["artifact_count"] == 1
    assert report["scheduler"]["enabled"] is True
    assert report["scheduler"]["disable_command"].endswith(f"{tmp_path}/scripts/run_m9_scheduler_activation.py --companion-home {tmp_path} --disable")
    assert report["scheduler"]["pause_flag_path"] == "life-loop/scheduler_pause.flag"
    assert report["scheduler"]["presence_state_path"] == "life-loop/scheduler_presence_state.json"
    assert report["boundaries"]["wake_cycle_run"] is False
    assert report["boundaries"]["provider_generation_requested"] is False
    assert report["boundaries"]["provider_calls"] == 0
    state = json.loads(paths.scheduler_presence_state_file.read_text())
    assert state["daily_live_wake_budget"] == 2
    assert state["quiet_hours"] == ["00:00", "08:00"]
    assert state["scheduled_wake_output"] == "internal_only"
    assert "dry_run" not in state
    assert datetime.fromisoformat(state["next_candidate_after"]) > now
    assert not paths.wake_events_file.exists()


def test_m9_scheduler_activation_requires_revalidation_and_dry_run_reports(tmp_path):
    paths = write_ready_home(tmp_path)
    (paths.life_loop_dir / "m9_scheduler_dry_run_report.json").unlink()
    fake_cron = FakeCrontab()

    result = run_m9_scheduler_activation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        now=datetime(2026, 6, 21, 19, 45),
    )
    report = result.to_dict()

    assert result.ok is False
    assert report["recommendation"] == "inspect"
    assert "m9_scheduler_dry_run" in report["stop_reasons"]
    assert fake_cron.text == ""
    assert fake_cron.write_count == 0
    assert not paths.scheduler_presence_state_file.exists()


def test_m9_scheduler_activation_rejects_mismatched_existing_marker(tmp_path):
    paths = write_ready_home(tmp_path)
    fake_cron = FakeCrontab(f"* * * * * echo wrong # {MARKER}\n")

    result = run_m9_scheduler_activation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        now=datetime(2026, 6, 21, 19, 45),
    )
    report = result.to_dict()

    assert result.ok is False
    assert "cron_artifact_plan" in report["stop_reasons"]
    assert fake_cron.write_count == 0
    assert not paths.scheduler_presence_state_file.exists()


def test_m9_scheduler_disable_removes_managed_artifact_and_preserves_other_lines(tmp_path):
    paths = write_ready_home(tmp_path)
    fake_cron = FakeCrontab("MAILTO=ops@example.test\n" + build_m9_cron_line(paths) + "\n")

    result = run_m9_scheduler_disable(paths, crontab_reader=fake_cron.read, crontab_writer=fake_cron.write)
    report = result.to_dict()

    assert result.ok is True
    assert report["recommendation"] == "m9_scheduler_activation_disabled"
    assert MARKER not in fake_cron.text
    assert fake_cron.text == "MAILTO=ops@example.test\n"


def test_m9_scheduler_tick_respects_pause_without_running_wake(tmp_path):
    paths = write_ready_home(tmp_path)
    write_activation_report(paths)
    now = datetime(2026, 6, 21, 10, 0)
    initialize_scheduler_presence_state(paths, now=now - timedelta(hours=2), random_seed=7)
    paths.scheduler_pause_flag.write_text("paused for test")

    def wake_runner(_command: str) -> WakeCommandResult:
        raise AssertionError("wake runner must not be called while paused")

    result = run_m9_scheduler_tick(paths, now=now, wake_runner=wake_runner)
    report = result.to_dict()
    attempts = load_scheduler_attempts(paths.scheduler_attempts_file)
    state = json.loads(paths.scheduler_presence_state_file.read_text())

    assert result.ok is True
    assert report["attempt"]["decision"] == "skipped"
    assert report["attempt"]["skip_reason"] == "paused"
    assert attempts[-1]["skip_reason"] == "paused"
    assert attempts[-1]["wake_cycle_run"] is False
    assert state["last_skip_reason"] == "paused"
    assert not paths.wake_events_file.exists()


def test_m9_scheduler_tick_runs_existing_wake_command_after_controls_pass(tmp_path):
    paths = write_ready_home(tmp_path)
    write_activation_report(paths)
    now = datetime(2026, 6, 21, 15, 0)
    write_live_state(paths, now=now)
    calls = []

    def wake_runner(command: str) -> WakeCommandResult:
        calls.append(command)
        return WakeCommandResult(returncode=0, duration_seconds=0.01)

    result = run_m9_scheduler_tick(paths, now=now, random_seed=7, wake_runner=wake_runner)
    report = result.to_dict()
    state = json.loads(paths.scheduler_presence_state_file.read_text())
    attempts = load_scheduler_attempts(paths.scheduler_attempts_file)

    assert result.ok is True
    assert report["attempt"]["decision"] == "ran"
    assert report["attempt"]["wake_cycle_run"] is True
    assert len(calls) == 1
    assert "--provider deepseek --memory-mode json --trigger scheduled-wake" in calls[0]
    assert state["daily_live_wake_count"] == 1
    assert state["last_scheduled_wake_at"] == now.isoformat()
    assert datetime.fromisoformat(state["next_candidate_after"]) > now
    assert attempts[-1]["raw_provider_payload_stored"] is False
    assert attempts[-1]["voice_signal_hardware_output"] is False


def test_m9_scheduler_tick_dampens_after_recent_human_chat(tmp_path):
    paths = write_ready_home(tmp_path)
    write_activation_report(paths)
    now = datetime(2026, 6, 21, 15, 0)
    write_live_state(paths, now=now)
    paths.conversation_events_file.write_text(json.dumps({
        "trigger": "human-text-chat",
        "completed_at": (now - timedelta(minutes=20)).isoformat(),
    }) + "\n")

    def wake_runner(_command: str) -> WakeCommandResult:
        raise AssertionError("wake runner must not be called during chat dampening")

    result = run_m9_scheduler_tick(paths, now=now, wake_runner=wake_runner)
    report = result.to_dict()

    assert result.ok is True
    assert report["attempt"]["decision"] == "skipped"
    assert report["attempt"]["skip_reason"] == "recent_human_chat_dampening"


def test_m9_scheduler_activation_cli_writes_report_with_test_crontab_file(tmp_path):
    paths = write_ready_home(tmp_path / "home")
    crontab_file = tmp_path / "crontab.txt"
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m9_scheduler_activation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(paths.home),
            "--enable",
            "--seed",
            "7",
            "--crontab-file",
            str(crontab_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m9_scheduler_activation_ready"
    assert MARKER in crontab_file.read_text()
    report_path = paths.life_loop_dir / "m9_scheduler_activation_report.json"
    assert report_path.exists()
    assert json.loads(report_path.read_text())["scheduler"]["artifact_count"] == 1
