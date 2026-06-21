import json
import subprocess
import sys
from pathlib import Path

import pytest

from companion_core import (
    CompanionPaths,
    run_m9_scheduler_revalidation_check,
    source_only_m9_scheduler_inventory,
    write_m9_scheduler_revalidation_report,
)


def write_ready_home(home: Path) -> CompanionPaths:
    for name in ("life-loop", "journals", "memory-server", "conversations", "requests", "scripts", "window"):
        (home / name).mkdir(parents=True, exist_ok=True)
    (home / "scripts" / "run_wake_cycle.py").write_text(
        "\n".join([
            "import argparse",
            "parser = argparse.ArgumentParser()",
            'parser.add_argument("--companion-home")',
            'parser.add_argument("--trigger")',
            'parser.add_argument("--memory-mode")',
            'parser.add_argument("--provider")',
            'parser.add_argument("--fake-llm", action="store_true")',
            'parser.add_argument("--check-provider", action="store_true")',
            'SUPPORTED_LLM_PROVIDERS = ("fake", "deepseek")',
        ])
    )
    (home / "window" / "window.py").write_text(
        "\n".join([
            "from flask import Flask",
            "app = Flask(__name__)",
            '@app.route("/life")',
            "def life_dashboard():",
            "    return 'life'",
        ])
    )
    write_ready_reports(home)
    return CompanionPaths(home)


def write_ready_reports(home: Path):
    life_loop = home / "life-loop"
    handoff_command = (
        f"cd {home} && .venv/bin/python scripts/run_wake_cycle.py "
        f"--companion-home {home} --provider deepseek --memory-mode json --trigger scheduled-wake"
    )
    (life_loop / "m9_controlled_presence_design_report.json").write_text(json.dumps({
        "schema_version": 1,
        "saved_at": "2026-06-21T18:30:00",
        "ok": True,
        "milestone": "M9.0",
        "recommendation": "m9_controlled_presence_design_ready",
        "stop_reasons": [],
        "provider_calls": 0,
        "design": {
            "cadence": {
                "model": "randomized_presence_windows",
                "quiet_hours": ["00:00", "08:00"],
                "daily_live_wake_budget": 2,
                "scheduled_wake_output": "internal_only",
            }
        },
        "boundaries": {
            "scheduler_mutated": False,
            "wake_cycle_run": False,
            "provider_generation_requested": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "raw_provider_payload_stored": False,
        },
    }))
    (life_loop / "m6_final_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M6.7",
        "recommendation": "m6_frozen_ready_for_scheduler_handoff",
        "stop_reasons": [],
        "provider_calls": 0,
        "profile": {
            "provider": "deepseek",
            "memory_mode": "json",
            "cron_replacement": False,
            "timer_installation": False,
            "scheduler_mutation_allowed": False,
            "scheduler_mutation_attempted": False,
            "real_wake_requested": False,
            "provider_generation_requested": False,
            "signal_voice_hardware_activation_allowed": False,
        },
        "final_freeze": {
            "frozen": True,
            "readonly": True,
            "scheduler_handoff_ready": True,
            "scheduler_mutated": False,
            "target_command": handoff_command,
        },
        "handoff": {
            "ready": True,
            "mutated": False,
            "target_command": handoff_command,
            "recommended_trigger": "scheduled-wake",
        },
    }))
    (life_loop / "m8_memory_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M8.7",
        "recommendation": "m8_memory_dialogue_frozen",
        "stop_reasons": [],
        "provider_calls": 0,
        "final_freeze": {
            "frozen": True,
            "readonly": True,
            "memory_stewardship_ready": True,
            "dialogue_humanity_ready": True,
        },
        "boundaries": {
            "scheduler_mutated": False,
            "wake_cycle_run": False,
            "wake_events_written": False,
            "provider_generation_requested": False,
            "life_write_route_added": False,
            "semantic_shadow_authority_promoted": False,
            "proposal_or_quarantine_prompt_authority": False,
            "raw_provider_payload_stored": False,
        },
    }))


def test_m9_scheduler_revalidation_passes_ready_baselines_without_runtime_mutation(tmp_path):
    paths = write_ready_home(tmp_path)

    result = run_m9_scheduler_revalidation_check(
        paths,
        scheduler_inventory_provider=source_only_m9_scheduler_inventory,
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m9_scheduler_revalidation_ready"
    assert report["milestone"] == "M9.1"
    assert report["provider_calls"] == 0
    assert report["cadence"]["model"] == "randomized_presence_windows"
    assert report["cadence"]["daily_live_wake_budget"] == 2
    assert report["handoff"]["provider"] == "deepseek"
    assert report["handoff"]["memory_mode"] == "json"
    assert report["handoff"]["trigger"] == "scheduled-wake"
    assert report["boundaries"]["scheduler_mutated"] is False
    assert report["boundaries"]["wake_cycle_run"] is False
    assert report["boundaries"]["provider_generation_requested"] is False
    assert not paths.scheduler_pause_flag.exists()
    assert not paths.scheduler_presence_state_file.exists()
    assert not paths.wake_events_file.exists()
    assert not (paths.life_loop_dir / "m9_scheduler_revalidation_report.json").exists()


@pytest.mark.parametrize(
    "filename, stage_name",
    [
        ("m6_final_freeze_report.json", "m6_final_freeze"),
        ("m8_memory_freeze_report.json", "m8_memory_freeze"),
    ],
)
def test_m9_scheduler_revalidation_fails_when_baseline_report_is_missing(tmp_path, filename, stage_name):
    paths = write_ready_home(tmp_path)
    (paths.life_loop_dir / filename).unlink()

    result = run_m9_scheduler_revalidation_check(
        paths,
        scheduler_inventory_provider=source_only_m9_scheduler_inventory,
    )
    report = result.to_dict()

    assert result.ok is False
    assert report["recommendation"] == "inspect"
    assert stage_name in report["stop_reasons"]


def test_m9_scheduler_revalidation_verifies_wake_command_flags(tmp_path):
    paths = write_ready_home(tmp_path)
    (paths.home / "scripts" / "run_wake_cycle.py").write_text(
        "\n".join([
            "import argparse",
            "parser = argparse.ArgumentParser()",
            'parser.add_argument("--companion-home")',
            'parser.add_argument("--trigger")',
            'parser.add_argument("--memory-mode")',
            'parser.add_argument("--provider")',
            'parser.add_argument("--fake-llm", action="store_true")',
        ])
    )

    result = run_m9_scheduler_revalidation_check(
        paths,
        scheduler_inventory_provider=source_only_m9_scheduler_inventory,
    )
    report = result.to_dict()
    stages = {stage["name"]: stage for stage in report["stages"]}

    assert result.ok is False
    assert "wake_command_shape" in report["stop_reasons"]
    assert "--check-provider" in stages["wake_command_shape"]["details"]["missing_flags"]
    assert "provider_config_shape" in report["stop_reasons"]


def test_m9_scheduler_revalidation_blocks_unexpected_scheduler_inventory(tmp_path):
    paths = write_ready_home(tmp_path)

    def inventory_provider(_paths: CompanionPaths) -> dict:
        return {
            "source": "fixture",
            "mutation_attempted": False,
            "probes": [{"name": "crontab", "status": "ok", "matched_lines": ["* * * * * run_wake_cycle.py"]}],
            "unexpected_active_artifacts": ["* * * * * run_wake_cycle.py"],
        }

    result = run_m9_scheduler_revalidation_check(paths, scheduler_inventory_provider=inventory_provider)
    report = result.to_dict()

    assert result.ok is False
    assert "scheduler_inventory" in report["stop_reasons"]
    assert report["evidence"]["unexpected_scheduler_artifacts"] == ["* * * * * run_wake_cycle.py"]


def test_m9_scheduler_revalidation_preserves_existing_lock_files(tmp_path):
    paths = write_ready_home(tmp_path)
    lock_file = paths.life_loop_dir / "wake_events.lock"
    lock_file.write_text("locked")

    result = run_m9_scheduler_revalidation_check(
        paths,
        scheduler_inventory_provider=source_only_m9_scheduler_inventory,
    )
    report = result.to_dict()

    assert result.ok is True
    assert "life-loop/wake_events.lock" in report["scheduler"]["lock_files"]
    assert lock_file.read_text() == "locked"
    assert not paths.scheduler_pause_flag.exists()
    assert not paths.scheduler_presence_state_file.exists()
    assert not paths.wake_events_file.exists()


def test_m9_scheduler_revalidation_report_writer_and_cli_write_report(tmp_path):
    paths = write_ready_home(tmp_path)
    result = run_m9_scheduler_revalidation_check(
        paths,
        scheduler_inventory_provider=source_only_m9_scheduler_inventory,
    )
    report_path = write_m9_scheduler_revalidation_report(paths, result.to_dict())

    assert report_path == paths.life_loop_dir / "m9_scheduler_revalidation_report.json"
    assert json.loads(report_path.read_text())["recommendation"] == "m9_scheduler_revalidation_ready"

    cli_home = tmp_path / "cli"
    write_ready_home(cli_home)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m9_scheduler_revalidation.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(cli_home),
            "--source-only-inventory",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m9_scheduler_revalidation_ready"
    assert (cli_home / "life-loop" / "m9_scheduler_revalidation_report.json").exists()
