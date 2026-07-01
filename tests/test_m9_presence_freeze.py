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
    run_m9_presence_freeze,
    write_m9_presence_freeze_report,
)


REQUIRED_SKIP_REASONS = [
    "paused",
    "quiet_hours",
    "daily_budget_exhausted",
    "min_gap_not_met",
    "wake_lock_active",
    "failure_cooldown",
    "recent_human_chat_dampening",
]


def write_ready_home(home: Path) -> CompanionPaths:
    for name in ("life-loop", "journals", "memory-server", "conversations", "requests", "scripts", "window"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "window" / "window.py").write_text(
        "\n".join([
            "from flask import Flask",
            "app = Flask(__name__)",
            '@app.route("/life")',
            "def life_dashboard():",
            "    return 'life'",
        ])
    )
    paths = CompanionPaths(home)
    write_ready_reports(paths)
    append_live_skip(paths)
    return paths


def write_ready_reports(paths: CompanionPaths) -> None:
    life_loop = paths.life_loop_dir
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
        "saved_at": "2026-07-01T17:39:30",
        "recommendation": "m9_scheduler_activation_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "cadence": {
            "model": "randomized_presence_windows",
            "quiet_hours": ["00:00", "08:00"],
            "daily_live_wake_budget": 2,
            "scheduled_wake_output": "internal_only",
            "skip_reasons": REQUIRED_SKIP_REASONS,
        },
        "scheduler": {
            "mechanism": "cron",
            "artifact_count": 1,
            "enabled": True,
        },
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
    (life_loop / "m9_presence_observation_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M9.4",
        "recommendation": "m9_presence_observation_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "scheduler": {
            "mechanism": "cron",
            "artifact_count": 1,
            "enabled": True,
        },
        "drills": {
            "pause": {
                "performed": True,
                "ok": True,
                "wake_cycle_run": False,
                "provider_calls": 0,
            },
            "rollback": {
                "performed": True,
                "ok": True,
                "artifact_count_after_disable": 0,
                "artifact_count_after_restore": 1,
            },
        },
        "boundaries": {
            "wake_cycle_run_by_observation_gate": False,
            "provider_generation_requested_by_observation_gate": False,
            "provider_calls_by_observation_gate": 0,
            "raw_provider_payload_stored": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "voice_signal_hardware_activation_allowed": False,
        },
    }))


def append_live_skip(paths: CompanionPaths, *, attempted_at: str = "2026-07-01T17:45:00") -> None:
    append_scheduler_attempts(paths.scheduler_attempts_file, [{
        "id": "m9live_freeze_skip",
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


def crontab_reader_for(paths: CompanionPaths):
    def reader() -> str:
        return build_m9_cron_line(paths) + "\n"

    return reader


def test_m9_presence_freeze_passes_with_ready_milestones_and_live_evidence(tmp_path):
    paths = write_ready_home(tmp_path)

    result = run_m9_presence_freeze(
        paths,
        crontab_reader=crontab_reader_for(paths),
        now=datetime(2026, 7, 1, 18, 30),
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m9_controlled_presence_frozen"
    assert report["final_freeze"]["frozen"] is True
    assert report["final_freeze"]["scheduler_artifact_known"] is True
    assert report["final_freeze"]["scheduler_observable"] is True
    assert report["scheduler"]["mechanism"] == "cron"
    assert report["scheduler"]["artifact_count"] == 1
    assert report["scheduler"]["pause_flag_path"] == "life-loop/scheduler_pause.flag"
    assert report["scheduler"]["presence_state_path"] == "life-loop/scheduler_presence_state.json"
    assert report["observation"]["live_attempt_count"] == 1
    assert report["observation"]["scheduled_wake_event_count"] == 0
    assert report["boundaries"]["scheduler_mutated_by_freeze"] is False
    assert report["boundaries"]["provider_calls_by_freeze"] == 0
    assert report["provider_calls"] == 0


def test_m9_presence_freeze_requires_m9_4_report(tmp_path):
    paths = write_ready_home(tmp_path)
    (paths.life_loop_dir / "m9_presence_observation_report.json").unlink()

    result = run_m9_presence_freeze(paths, crontab_reader=crontab_reader_for(paths))
    report = result.to_dict()

    assert result.ok is False
    assert "m9_presence_observation" in report["stop_reasons"]


def test_m9_presence_freeze_requires_current_cron_artifact(tmp_path):
    paths = write_ready_home(tmp_path)

    result = run_m9_presence_freeze(paths, crontab_reader=lambda: "")
    report = result.to_dict()

    assert result.ok is False
    assert "scheduler_artifact_current" in report["stop_reasons"]
    assert report["scheduler"]["artifact_count"] == 0


def test_m9_presence_freeze_requires_pause_and_rollback_drills(tmp_path):
    paths = write_ready_home(tmp_path)
    observation_path = paths.life_loop_dir / "m9_presence_observation_report.json"
    observation = json.loads(observation_path.read_text())
    observation["drills"]["pause"]["ok"] = False
    observation["drills"]["rollback"]["performed"] = False
    observation_path.write_text(json.dumps(observation))

    result = run_m9_presence_freeze(paths, crontab_reader=crontab_reader_for(paths))
    report = result.to_dict()

    assert result.ok is False
    assert "observation_and_drills" in report["stop_reasons"]
    assert report["evidence"]["pause_drill_ready"] is False
    assert report["evidence"]["rollback_drill_ready"] is False


def test_m9_presence_freeze_rejects_raw_scheduled_wake_and_memory_authority_violation(tmp_path):
    paths = write_ready_home(tmp_path)
    append_wake_event(paths.wake_events_file, {
        "id": "wake_raw_m9",
        "trigger": "scheduled-wake:1",
        "status": "completed",
        "started_at": "2026-07-01T18:30:00",
        "completed_at": "2026-07-01T18:31:00",
        "duration_seconds": 60,
        "provider": "deepseek",
        "raw_provider_payload_stored": True,
        "output_audit": {
            "raw_output_storage": "enabled",
            "initial": {"raw_output_stored": True},
            "final": {"raw_output_stored": True},
        },
        "semantic_shadow": {"authoritative": True},
        "memory_policy": {
            "decisions": [
                {"decision": "proposal", "prompt_eligible": True},
            ],
        },
    })

    result = run_m9_presence_freeze(paths, crontab_reader=crontab_reader_for(paths))
    report = result.to_dict()

    assert result.ok is False
    assert "scheduled_wake_event_boundaries" in report["stop_reasons"]
    assert "memory_authority_boundaries" in report["stop_reasons"]


def test_m9_presence_freeze_report_writer_and_cli_write_report(tmp_path):
    paths = write_ready_home(tmp_path / "home")
    report_path = write_m9_presence_freeze_report(
        paths,
        run_m9_presence_freeze(paths, crontab_reader=crontab_reader_for(paths)).to_dict(),
    )
    assert report_path == paths.life_loop_dir / "m9_presence_freeze_report.json"
    assert json.loads(report_path.read_text())["recommendation"] == "m9_controlled_presence_frozen"

    crontab_file = tmp_path / "crontab.txt"
    crontab_file.write_text(build_m9_cron_line(paths) + "\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m9_presence_freeze.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(paths.home),
            "--crontab-file",
            str(crontab_file),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m9_controlled_presence_frozen"
    assert (paths.life_loop_dir / "m9_presence_freeze_report.json").exists()
    assert crontab_file.read_text().count("digital-life-m9-scheduler-m9.3") == 1
