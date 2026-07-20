"""M15 gate regression: the dry-run gate itself must stay green and honest."""

import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    run_m15_consolidation_dry_run,
    write_m15_consolidation_dry_run_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_home(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def test_m15_dry_run_passes_all_stages(tmp_path):
    paths = make_home(tmp_path)
    result = run_m15_consolidation_dry_run(paths)
    report = result.to_dict()

    assert result.ok, report["errors"]
    assert report["recommendation"] == "m15_consolidation_dry_run_ready"
    stage_names = {stage["name"] for stage in report["stages"]}
    assert {
        "crash_before_save",
        "crash_after_save",
        "idempotent_apply",
        "whole_plan_rollback",
        "stale_plan_refusal",
        "policy_gates",
        "catch_up_debt",
        "scripted_full_pass",
        "config_template",
        "static_guard",
    } <= stage_names
    assert report["provider_calls"] == 0
    assert report["boundaries"]["real_memory_store_mutated"] is False
    assert report["boundaries"]["memories_deleted"] is False


def test_m15_dry_run_never_touches_home_memory_store(tmp_path):
    paths = make_home(tmp_path)
    paths.memory_store.parent.mkdir(parents=True, exist_ok=True)
    paths.memory_store.write_text(json.dumps([{"id": "mem_real", "content": "真实记忆"}]))
    before = paths.memory_store.read_text()

    run_m15_consolidation_dry_run(paths)

    assert paths.memory_store.read_text() == before
    assert not paths.consolidation_state_file.exists()
    assert not paths.consolidation_ledger_file.exists()


def test_m15_dry_run_report_write_and_cli(tmp_path):
    paths = make_home(tmp_path)
    result = run_m15_consolidation_dry_run(paths)
    report_path = write_m15_consolidation_dry_run_report(paths, result.to_dict())
    assert report_path.name == "m15_consolidation_dry_run_report.json"
    saved = json.loads(report_path.read_text())
    assert saved["milestone"] == "M15.2"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m15_consolidation_dry_run.py"),
            "--companion-home",
            str(tmp_path / "cli_home"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert Path(payload["report_file"]).exists()


def test_m15_runner_cli_check_and_guard_rails(tmp_path):
    home = tmp_path / "runner_home"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m15_consolidation.py"),
            "--companion-home",
            str(home),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["mode"] == "check"
    assert payload["due"]["due"] is False

    unconfirmed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m15_consolidation.py"),
            "--companion-home",
            str(home),
            "--fake-llm",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unconfirmed.returncode != 0
    assert "--confirm-consolidation" in unconfirmed.stderr
