import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from companion_core import (
    CompanionPaths,
    append_scheduler_attempts,
    append_wake_event,
    build_m9_cron_line,
    run_m9_presence_observation,
)


class FakeCrontab:
    def __init__(self, text: str):
        self.text = text

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> None:
        self.text = text


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
    (life_loop / "m8_memory_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M8.7",
        "recommendation": "m8_memory_dialogue_frozen",
        "stop_reasons": [],
        "provider_calls": 0,
        "final_freeze": {"frozen": True, "readonly": True},
        "boundaries": {
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
        },
    }))
    (life_loop / "m9_scheduler_revalidation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.1",
        "recommendation": "m9_scheduler_revalidation_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "handoff": {"target_command": handoff_command},
        "boundaries": {
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
        "dry_run": {"target_command": handoff_command},
        "boundaries": {
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
    }))
    (life_loop / "m9_scheduler_activation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.3",
        "recommendation": "m9_scheduler_activation_ready",
        "saved_at": "2026-07-01T17:39:30",
        "stop_reasons": [],
        "boundaries": {
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "raw_provider_payload_stored": False,
        },
        "scheduler": {
            "artifact": {"line": build_m9_cron_line(CompanionPaths(home))},
            "artifact_count": 1,
            "enabled": True,
        },
    }))
    return CompanionPaths(home)


def append_live_skip(paths: CompanionPaths, *, attempted_at: str = "2026-07-01T17:45:00") -> None:
    append_scheduler_attempts(paths.scheduler_attempts_file, [{
        "id": "m9live_test_skip",
        "source": "m9_scheduler_live_tick",
        "attempted_at": attempted_at,
        "trigger": "scheduled-wake",
        "decision": "skipped",
        "skip_reason": "next_candidate_not_reached",
        "lock_acquired": False,
        "wake_cycle_run": False,
        "scheduled_wake_output": "internal_only",
        "raw_provider_payload_stored": False,
        "voice_signal_hardware_output": False,
    }])


def test_m9_presence_observation_passes_with_live_attempt_pause_and_rollback_drills(tmp_path):
    paths = write_ready_home(tmp_path)
    append_live_skip(paths)
    fake_cron = FakeCrontab(build_m9_cron_line(paths) + "\n")

    result = run_m9_presence_observation(
        paths,
        perform_pause_drill=True,
        perform_rollback_drill=True,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        now=datetime(2026, 7, 1, 18, 0),
        random_seed=7,
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m9_presence_observation_ready"
    assert report["observation"]["attempt_count"] >= 2
    assert report["drills"]["pause"]["ok"] is True
    assert report["drills"]["pause"]["attempt"]["skip_reason"] == "paused"
    assert report["drills"]["pause"]["wake_cycle_run"] is False
    assert report["drills"]["rollback"]["ok"] is True
    assert report["drills"]["rollback"]["artifact_count_after_disable"] == 0
    assert report["drills"]["rollback"]["artifact_count_after_restore"] == 1
    assert fake_cron.text.count("digital-life-m9-scheduler-m9.3") == 1
    assert report["boundaries"]["provider_calls_by_observation_gate"] == 0


def test_m9_presence_observation_requires_activation_report(tmp_path):
    paths = write_ready_home(tmp_path)
    (paths.life_loop_dir / "m9_scheduler_activation_report.json").unlink()
    fake_cron = FakeCrontab(build_m9_cron_line(paths) + "\n")

    result = run_m9_presence_observation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        require_live_attempt=False,
    )
    report = result.to_dict()

    assert result.ok is False
    assert "m9_scheduler_activation" in report["stop_reasons"]


def test_m9_presence_observation_requires_live_attempt_when_configured(tmp_path):
    paths = write_ready_home(tmp_path)
    fake_cron = FakeCrontab(build_m9_cron_line(paths) + "\n")

    result = run_m9_presence_observation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
        require_live_attempt=True,
    )
    report = result.to_dict()

    assert result.ok is False
    assert "scheduler_attempt_observation" in report["stop_reasons"]


def test_m9_presence_observation_rejects_raw_scheduled_wake_output(tmp_path):
    paths = write_ready_home(tmp_path)
    append_live_skip(paths)
    append_wake_event(paths.wake_events_file, {
        "id": "wake_raw",
        "trigger": "scheduled-wake:1",
        "status": "completed",
        "started_at": "2026-07-01T18:30:00",
        "completed_at": "2026-07-01T18:31:00",
        "duration_seconds": 60,
        "provider": "deepseek",
        "output_audit": {
            "raw_output_storage": "enabled",
            "initial": {"raw_output_stored": True},
            "final": {"raw_output_stored": True},
        },
    })
    fake_cron = FakeCrontab(build_m9_cron_line(paths) + "\n")

    result = run_m9_presence_observation(
        paths,
        crontab_reader=fake_cron.read,
        crontab_writer=fake_cron.write,
    )
    report = result.to_dict()

    assert result.ok is False
    assert "scheduled_wake_event_boundaries" in report["stop_reasons"]


def test_m9_presence_observation_cli_writes_report_with_fake_crontab(tmp_path):
    paths = write_ready_home(tmp_path / "home")
    append_live_skip(paths)
    crontab_file = tmp_path / "crontab.txt"
    crontab_file.write_text(build_m9_cron_line(paths) + "\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m9_presence_observation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(paths.home),
            "--perform-pause-drill",
            "--perform-rollback-drill",
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
    assert payload["recommendation"] == "m9_presence_observation_ready"
    assert (paths.life_loop_dir / "m9_presence_observation_report.json").exists()
    assert crontab_file.read_text().count("digital-life-m9-scheduler-m9.3") == 1
