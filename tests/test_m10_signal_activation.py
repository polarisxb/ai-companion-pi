import json

from companion_core import (
    build_signal_chat_unit,
    run_m10_signal_activation,
    run_m10_signal_disable,
    write_m10_signal_activation_report,
)
from companion_core.m10_signal_activation import UNIT_MARKER, UNIT_NAME

from m10_evidence import (
    make_home,
    write_config,
    write_dry_run_report,
    write_runner_stub,
    write_trial_report,
    write_upstream_freezes,
)


def ready_home(tmp_path):
    paths = make_home(tmp_path)
    write_dry_run_report(paths)
    write_trial_report(paths)
    write_upstream_freezes(paths)
    write_config(paths)
    write_runner_stub(paths)
    return paths


def test_activation_enables_exactly_one_unit(tmp_path):
    paths = ready_home(tmp_path / "home")
    unit_dir = tmp_path / "units"
    systemctl_calls = []

    result = run_m10_signal_activation(
        paths,
        unit_dir=unit_dir,
        systemctl_runner=systemctl_calls.append,
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m10_signal_activation_ready"
    assert report["milestone"] == "M10.3"
    unit_path = unit_dir / UNIT_NAME
    assert unit_path.exists()
    content = unit_path.read_text()
    assert UNIT_MARKER in content
    assert "--confirm-real-signal-send" in content
    assert str(paths.home) in content
    assert systemctl_calls == [["daemon-reload"], ["enable", "--now", UNIT_NAME]]

    service = report["service"]
    assert service["enabled"] is True
    assert service["artifact_count"] == 1
    assert service["mechanism"] == "systemd-user"
    assert service["rollback_command"].endswith("--disable")
    assert report["boundaries"]["scheduler_mutated"] is False
    assert report["boundaries"]["proactive_outbound_sent"] is False

    report_path = write_m10_signal_activation_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m10_signal_activation_ready"


def test_activation_is_idempotent_for_matching_unit(tmp_path):
    paths = ready_home(tmp_path / "home")
    unit_dir = tmp_path / "units"

    first = run_m10_signal_activation(paths, unit_dir=unit_dir, systemctl_runner=lambda args: None)
    second = run_m10_signal_activation(paths, unit_dir=unit_dir, systemctl_runner=lambda args: None)

    assert first.ok is True and second.ok is True
    assert first.report["service"]["changed"] is True
    assert second.report["service"]["changed"] is False


def test_activation_rejects_foreign_unit_content(tmp_path):
    paths = ready_home(tmp_path / "home")
    unit_dir = tmp_path / "units"
    unit_dir.mkdir(parents=True)
    (unit_dir / UNIT_NAME).write_text("[Unit]\nDescription=someone else's unit\n")
    systemctl_calls = []

    result = run_m10_signal_activation(paths, unit_dir=unit_dir, systemctl_runner=systemctl_calls.append)

    assert result.ok is False
    assert "unit_plan" in result.report["stop_reasons"]
    assert systemctl_calls == []
    assert "someone else's unit" in (unit_dir / UNIT_NAME).read_text()


def test_activation_requires_trial_evidence(tmp_path):
    paths = make_home(tmp_path / "home")
    write_dry_run_report(paths)
    write_upstream_freezes(paths)
    write_config(paths)
    write_runner_stub(paths)
    systemctl_calls = []

    result = run_m10_signal_activation(
        paths,
        unit_dir=tmp_path / "units",
        systemctl_runner=systemctl_calls.append,
    )

    assert result.ok is False
    assert "m10_signal_trial_ready" in result.report["stop_reasons"]
    assert systemctl_calls == []
    assert not (tmp_path / "units" / UNIT_NAME).exists()


def test_disable_removes_managed_unit_and_reports_rollback(tmp_path):
    paths = ready_home(tmp_path / "home")
    unit_dir = tmp_path / "units"
    run_m10_signal_activation(paths, unit_dir=unit_dir, systemctl_runner=lambda args: None)
    systemctl_calls = []

    result = run_m10_signal_disable(paths, unit_dir=unit_dir, systemctl_runner=systemctl_calls.append)

    assert result.ok is True
    assert result.recommendation == "m10_signal_activation_disabled"
    assert result.report["milestone"] == "M10.3.rollback"
    assert not (unit_dir / UNIT_NAME).exists()
    assert systemctl_calls == [["disable", "--now", UNIT_NAME], ["daemon-reload"]]

    again = run_m10_signal_disable(paths, unit_dir=unit_dir, systemctl_runner=systemctl_calls.append)
    assert again.ok is True
    assert again.report["stages"][0]["details"]["removed"] is False


def test_enable_flag_routes_to_disable(tmp_path):
    paths = ready_home(tmp_path / "home")
    unit_dir = tmp_path / "units"
    run_m10_signal_activation(paths, unit_dir=unit_dir, systemctl_runner=lambda args: None)

    result = run_m10_signal_activation(
        paths,
        enable=False,
        unit_dir=unit_dir,
        systemctl_runner=lambda args: None,
    )

    assert result.recommendation == "m10_signal_activation_disabled"
    assert not (unit_dir / UNIT_NAME).exists()


def test_unit_content_shape(tmp_path):
    paths = make_home(tmp_path)
    content = build_signal_chat_unit(paths)
    assert content.startswith(f"# {UNIT_MARKER}\n")
    assert "[Unit]" in content and "[Service]" in content and "[Install]" in content
    assert "Restart=on-failure" in content
    assert f"WorkingDirectory={paths.home}" in content
