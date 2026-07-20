import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    SIGNAL_OUTBOUND_DEFER_REASONS,
    SIGNAL_OUTBOUND_SKIP_REASONS,
    load_signal_chat_attempts,
    run_m11_outbound_dry_run,
    write_m11_outbound_dry_run_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def test_m11_dry_run_passes_and_covers_every_branch(tmp_path):
    paths = make_paths(tmp_path)

    result = run_m11_outbound_dry_run(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m11_signal_outbound_dry_run_ready"
    assert report["milestone"] == "M11.3"
    assert report["provider_calls"] == 0

    dry_run = report["dry_run"]
    assert dry_run["skip_reasons_missing"] == []
    assert sorted(dry_run["skip_reasons_covered"]) == sorted(SIGNAL_OUTBOUND_SKIP_REASONS)
    assert dry_run["defer_reasons_missing"] == []
    assert sorted(dry_run["defer_reasons_covered"]) == sorted(SIGNAL_OUTBOUND_DEFER_REASONS)
    for decision in ("delivered", "skipped", "failed"):
        assert dry_run["decision_counts"][decision] > 0
    assert dry_run["disabled_noop_confirmed"] is True

    transport = report["transport"]
    assert transport["fake_transport_only"] is True
    assert transport["signal_cli_invoked"] is False
    assert transport["outbound_sends"] == dry_run["decision_counts"]["delivered"]
    assert transport["recipients_match_configured"] is True

    boundaries = report["boundaries"]
    for key in ("wake_path_sends", "service_mutated", "scheduler_mutated", "voice_output"):
        assert boundaries[key] is False

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert len(ledger) == dry_run["record_count"]
    assert all(record["mode"] == "dry_run" for record in ledger)
    assert all(record["direction"] == "outbound" for record in ledger)
    # Scenario outbox stays in the smoke home; the real home only gets hashed records.
    assert not paths.signal_outbox_file.exists()


def test_m11_dry_run_can_skip_runtime_writes(tmp_path):
    paths = make_paths(tmp_path)

    result = run_m11_outbound_dry_run(paths, write_runtime=False)

    assert result.ok is True
    assert not paths.signal_chat_attempts_file.exists()


def test_m11_dry_run_report_writer_and_cli(tmp_path):
    paths = make_paths(tmp_path)
    result = run_m11_outbound_dry_run(paths, write_runtime=False)
    report_path = write_m11_outbound_dry_run_report(paths, result.to_dict())
    assert report_path == paths.life_loop_dir / "m11_signal_outbound_dry_run_report.json"
    assert json.loads(report_path.read_text())["recommendation"] == "m11_signal_outbound_dry_run_ready"

    cli_home = tmp_path / "cli-home"
    cli_home.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m11_outbound_dry_run.py"),
            "--companion-home",
            str(cli_home),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert (cli_home / "life-loop" / "m11_signal_outbound_dry_run_report.json").exists()


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m11_outbound_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m11_outbound_section(tmp_path, monkeypatch):
    paths = make_paths(tmp_path)
    result = run_m11_outbound_dry_run(paths)
    write_m11_outbound_dry_run_report(paths, result.to_dict())

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M11 Signal Outbound" in html
    assert "m11_signal_outbound_dry_run_ready" in html
    assert "outbound_skip_reasons_missing=none" in html
    assert "outbound_disabled_noop=True" in html


def test_life_dashboard_handles_missing_m11_reports(tmp_path, monkeypatch):
    make_paths(tmp_path)
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    assert "No M11 signal outbound report captured." in response.data.decode()
