import importlib.util
import json
from pathlib import Path

from companion_core import (
    append_signal_chat_attempts,
    run_m10_signal_freeze,
    write_m10_signal_freeze_report,
)

from m10_evidence import (
    make_attempt,
    make_home,
    make_outbound_record,
    write_activation_report,
    write_config,
    write_dry_run_report,
    write_observation_report,
    write_runner_stub,
    write_trial_report,
    write_upstream_freezes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def frozen_home(tmp_path):
    paths = make_home(tmp_path)
    write_dry_run_report(paths)
    write_trial_report(paths)
    write_activation_report(paths)
    write_observation_report(paths)
    write_upstream_freezes(paths)
    write_config(paths)
    write_runner_stub(paths)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_attempt(timestamp=1000, mode="trial"),
        make_attempt(timestamp=2000),
        make_attempt(timestamp=3000, decision="skipped", skip_reason="duplicate_message"),
        # An M11 outbound row with an M11-only skip reason: the M10 freeze
        # must filter it by direction instead of flagging an unknown reason.
        make_outbound_record(
            decision="skipped",
            skip_reason="expired",
            entry_id="outbox_live_1",
            source_event_id="wake_out_1",
        ),
    ])
    return paths


def test_freeze_passes_with_full_evidence(tmp_path):
    paths = frozen_home(tmp_path)

    result = run_m10_signal_freeze(paths)
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m10_signal_chat_frozen"
    assert report["milestone"] == "M10.5"
    assert report["final_freeze"] == {
        "frozen": True,
        "readonly": True,
        "signal_chat_ready": True,
        "service_reversible": True,
    }
    assert report["evidence"]["live_attempts_observed"] == 3
    assert report["evidence"]["replied_observed"] == 2
    assert report["evidence"]["failed_observed"] == 0
    assert report["evidence"]["pause_drill_ready"] is True
    assert report["boundaries"]["voice_camera_hardware_activation_allowed"] is False
    assert report["provider_calls"] == 0

    report_path = write_m10_signal_freeze_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m10_signal_chat_frozen"


def test_freeze_requires_every_source_report(tmp_path):
    paths = frozen_home(tmp_path)
    (paths.life_loop_dir / "m10_signal_observation_report.json").unlink()

    result = run_m10_signal_freeze(paths)

    assert result.ok is False
    assert "source_report_m10_4" in result.report["stop_reasons"]
    assert result.report["final_freeze"]["frozen"] is False


def test_freeze_requires_upstream_freezes_intact(tmp_path):
    paths = frozen_home(tmp_path)
    (paths.life_loop_dir / "m8_memory_freeze_report.json").write_text(json.dumps({
        "ok": False,
        "milestone": "M8.7",
        "recommendation": "inspect",
    }))

    result = run_m10_signal_freeze(paths)

    assert result.ok is False
    assert "upstream_freezes_intact" in result.report["stop_reasons"]


def test_freeze_rejects_boundary_violations_in_live_ledger(tmp_path):
    paths = frozen_home(tmp_path)
    violating = make_attempt(timestamp=9000)
    violating["boundaries"]["proactive_outbound_sent"] = True
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [violating])

    result = run_m10_signal_freeze(paths)

    assert result.ok is False
    assert "chat_boundaries_preserved" in result.report["stop_reasons"]


def test_freeze_requires_pause_drill_evidence(tmp_path):
    paths = frozen_home(tmp_path)
    write_observation_report(paths, pause_ready=False)

    result = run_m10_signal_freeze(paths)

    assert result.ok is False
    assert "pause_and_rollback_ready" in result.report["stop_reasons"]


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m10_freeze_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m10_service_and_freeze_lines(tmp_path, monkeypatch):
    paths = frozen_home(tmp_path)
    result = run_m10_signal_freeze(paths)
    write_m10_signal_freeze_report(paths, result.to_dict())

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "m10_signal_chat_frozen" in html
    assert "signal_service_mechanism=systemd-user" in html
    assert "signal_service_enabled=True" in html
    assert "signal_observed_attempts=3" in html
    assert "signal_live_attempts_observed=3" in html
    assert "signal_pause_drill_ready=True" in html
    assert "m10_frozen=True" in html
    assert "signal_service_reversible=True" in html
