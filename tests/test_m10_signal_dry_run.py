import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    SIGNAL_CHAT_SKIP_REASONS,
    load_signal_chat_attempts,
    run_m10_signal_dry_run,
    write_m10_signal_dry_run_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


def test_m10_dry_run_passes_and_covers_every_policy_branch(tmp_path):
    paths = make_paths(tmp_path)

    result = run_m10_signal_dry_run(paths)
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m10_signal_dry_run_ready"
    assert report["milestone"] == "M10.1"
    assert report["provider_calls"] == 0
    assert report["stop_reasons"] == []

    dry_run = report["dry_run"]
    assert dry_run["skip_reasons_missing"] == []
    assert sorted(dry_run["skip_reasons_covered"]) == sorted(SIGNAL_CHAT_SKIP_REASONS)
    for decision in ("replied", "skipped", "failed"):
        assert dry_run["decision_counts"][decision] > 0
    assert dry_run["failed_branches_covered"] == {"dialogue_failure": True, "send_failure": True}

    transport = report["transport"]
    assert transport["fake_transport_only"] is True
    assert transport["signal_cli_invoked"] is False
    assert transport["outbound_sends"] == dry_run["decision_counts"]["replied"]
    assert transport["outbound_recipients_match_senders"] is True
    assert transport["proactive_outbound_sent"] is False

    boundaries = report["boundaries"]
    for key in (
        "wake_cycle_run",
        "scheduler_mutated",
        "proactive_outbound_sent",
        "raw_provider_payload_stored",
        "raw_signal_envelope_stored",
        "semantic_shadow_authority_promoted",
        "memory_authority_expanded",
        "voice_output",
        "provider_generation_requested",
        "signal_cli_invoked",
        "cron_replacement",
        "timer_installation",
    ):
        assert boundaries[key] is False

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert len(ledger) == dry_run["attempt_count"]
    assert all(record["mode"] == "dry_run" for record in ledger)
    assert all(record["transport"] == "fake" for record in ledger)

    # Scenario dialogue turns must stay in the isolated smoke home.
    assert not paths.signal_chat_state_file.exists()
    assert list(paths.conversations_dir.glob("*.jsonl")) == []


def test_m10_dry_run_can_skip_runtime_writes(tmp_path):
    paths = make_paths(tmp_path)

    result = run_m10_signal_dry_run(paths, write_runtime=False)

    assert result.ok is True
    assert not paths.signal_chat_attempts_file.exists()


def test_m10_dry_run_report_writer_and_cli(tmp_path):
    paths = make_paths(tmp_path)
    result = run_m10_signal_dry_run(paths, write_runtime=False)
    report_path = write_m10_signal_dry_run_report(paths, result.to_dict())
    assert report_path == paths.life_loop_dir / "m10_signal_dry_run_report.json"
    saved = json.loads(report_path.read_text())
    assert saved["recommendation"] == "m10_signal_dry_run_ready"

    cli_home = tmp_path / "cli-home"
    cli_home.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m10_signal_dry_run.py"),
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
    assert payload["recommendation"] == "m10_signal_dry_run_ready"
    assert (cli_home / "life-loop" / "m10_signal_dry_run_report.json").exists()


def test_m10_chat_runner_check_mode_reports_not_ready_without_config(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m10_signal_chat.py"),
            "--companion-home",
            str(tmp_path),
            "--check",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ready"] is False
    assert payload["config"]["ok"] is False
    assert payload["freeze_evidence"]["ok"] is False
    assert payload["confirm_flag_required"] is True


def test_m10_chat_runner_refuses_real_mode_without_confirm_flag(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m10_signal_chat.py"),
            "--companion-home",
            str(tmp_path),
            "--once",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert "confirm-real-signal-send" in payload["error"]


def test_m10_chat_runner_fake_mode_replies_locally(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m10_signal_chat.py"),
            "--companion-home",
            str(tmp_path),
            "--fake",
            "--fake-message",
            "本地烟囱测试消息",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert len(payload["attempts"]) == 1
    assert payload["attempts"][0]["decision"] == "replied"
    assert payload["attempts"][0]["mode"] == "fake"
    assert len(payload["outbound"]) == 1


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m10_signal_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m10_signal_chat_evidence(tmp_path, monkeypatch):
    paths = make_paths(tmp_path)
    (paths.life_loop_dir / "m10_signal_dry_run_report.json").write_text(json.dumps({
        "ok": True,
        "milestone": "M10.1",
        "recommendation": "m10_signal_dry_run_ready",
        "saved_at": "2026-07-20T15:00:00",
        "stop_reasons": [],
        "stages": [{"name": "policy_scenario_coverage", "status": "pass"}],
        "dry_run": {
            "attempt_count": 14,
            "decision_counts": {"replied": 2, "skipped": 10, "failed": 2},
            "skip_reasons_missing": [],
        },
        "transport": {
            "fake_transport_only": True,
            "signal_cli_invoked": False,
            "proactive_outbound_sent": False,
        },
        "signal_chat": {
            "attempts_file": "life-loop/signal_chat_attempts.jsonl",
            "pause_flag_path": "life-loop/signal_chat_pause.flag",
            "config_present": False,
        },
    }))

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M10 Signal Chat" in html
    assert "m10_signal_dry_run_ready" in html
    assert "policy_scenario_coverage=pass" in html
    assert "dry_run_attempts=14" in html
    assert "decision_replied=2" in html
    assert "decision_skipped=10" in html
    assert "decision_failed=2" in html
    assert "skip_reasons_missing=none" in html
    assert "fake_transport_only=True" in html
    assert "signal_cli_invoked=False" in html
    assert "proactive_outbound_sent=False" in html
    assert "signal_attempts_file=life-loop/signal_chat_attempts.jsonl" in html
    assert "signal_pause_flag_path=life-loop/signal_chat_pause.flag" in html


def test_life_dashboard_handles_missing_m10_reports(tmp_path, monkeypatch):
    make_paths(tmp_path)
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    assert "No M10 signal chat report captured." in response.data.decode()
