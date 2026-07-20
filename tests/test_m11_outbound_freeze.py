import importlib.util
import json
from pathlib import Path

from companion_core import (
    append_signal_chat_attempts,
    run_m11_outbound_freeze,
    write_m11_outbound_freeze_report,
)

from m10_evidence import (
    make_home,
    make_outbound_record,
    write_m10_freeze_report,
    write_m11_dry_run_report,
    write_m11_observation_report,
    write_m11_trial_report,
    write_upstream_freezes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def frozen_home(tmp_path):
    paths = make_home(tmp_path)
    write_m11_dry_run_report(paths)
    write_m11_trial_report(paths)
    write_m11_observation_report(paths)
    write_m10_freeze_report(paths)
    write_upstream_freezes(paths)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_outbound_record(entry_id="outbox_1", source_event_id="wake_1", mode="trial"),
        make_outbound_record(entry_id="outbox_2", source_event_id="wake_2"),
        make_outbound_record(
            decision="skipped",
            skip_reason="expired",
            entry_id="outbox_3",
            source_event_id="wake_3",
        ),
    ])
    return paths


def test_m11_freeze_passes_with_full_evidence(tmp_path):
    paths = frozen_home(tmp_path)

    result = run_m11_outbound_freeze(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m11_signal_outbound_frozen"
    assert report["milestone"] == "M11.6"
    assert report["final_freeze"] == {
        "frozen": True,
        "readonly": True,
        "outbound_ready": True,
        "outbound_reversible": True,
    }
    assert report["evidence"]["outbound_records_observed"] == 3
    assert report["evidence"]["delivered_observed"] == 2
    assert report["evidence"]["failed_observed"] == 0
    assert report["boundaries"]["voice_camera_hardware_activation_allowed"] is False

    report_path = write_m11_outbound_freeze_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m11_signal_outbound_frozen"


def test_m11_freeze_requires_every_source_report(tmp_path):
    paths = frozen_home(tmp_path)
    (paths.life_loop_dir / "m11_signal_outbound_observation_report.json").unlink()

    result = run_m11_outbound_freeze(paths)

    assert result.ok is False
    assert "source_report_m11_5" in result.report["stop_reasons"]


def test_m11_freeze_requires_m10_freeze_and_upstream(tmp_path):
    paths = frozen_home(tmp_path)
    write_m10_freeze_report(paths, ok=False)

    result = run_m11_outbound_freeze(paths)
    assert result.ok is False
    assert "source_report_m10_5" in result.report["stop_reasons"]

    broken = frozen_home(tmp_path / "broken")
    (broken.life_loop_dir / "m9_presence_freeze_report.json").write_text(json.dumps({
        "ok": False,
        "milestone": "M9.5",
        "recommendation": "inspect",
    }))
    broken_result = run_m11_outbound_freeze(broken)
    assert broken_result.ok is False
    assert "upstream_freezes_intact" in broken_result.report["stop_reasons"]


def test_m11_freeze_rejects_boundary_violations(tmp_path):
    paths = frozen_home(tmp_path)
    violating = make_outbound_record(entry_id="outbox_bad", source_event_id="wake_bad")
    violating["boundaries"]["voice_output"] = True
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [violating])

    result = run_m11_outbound_freeze(paths)

    assert result.ok is False
    assert "outbound_boundaries_preserved" in result.report["stop_reasons"]


def test_m11_freeze_requires_pause_drill(tmp_path):
    paths = frozen_home(tmp_path)
    write_m11_observation_report(paths, pause_ready=False)

    result = run_m11_outbound_freeze(paths)

    assert result.ok is False
    assert "pause_and_disable_ready" in result.report["stop_reasons"]


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m11_freeze_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m11_freeze_lines(tmp_path, monkeypatch):
    paths = frozen_home(tmp_path)
    result = run_m11_outbound_freeze(paths)
    write_m11_outbound_freeze_report(paths, result.to_dict())

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M11 Signal Outbound" in html
    assert "m11_signal_outbound_frozen" in html
    assert "outbound_records_observed=3" in html
    assert "outbound_delivered_observed=2" in html
    assert "outbound_pause_drill_ready=True" in html
    assert "m11_frozen=True" in html
    assert "outbound_reversible=True" in html
