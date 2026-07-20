import fcntl
import json
from datetime import datetime

from companion_core import (
    FakeSignalTransport,
    append_signal_outbox_entry,
    build_signal_outbox_entry,
    run_m11_outbound_trial,
    write_m11_outbound_trial_report,
)

from m10_evidence import (
    ALLOWED,
    make_home,
    write_activation_report,
    write_config,
    write_m11_dry_run_report,
    write_trial_report,
    write_upstream_freezes,
)


def ready_home(tmp_path, *, seed_outbox=True):
    paths = make_home(tmp_path)
    write_m11_dry_run_report(paths)
    write_trial_report(paths)  # M10.2
    write_activation_report(paths)  # M10.3
    write_upstream_freezes(paths)
    # Quiet hours 00:00-00:00 never match, so the trial is wall-clock independent.
    write_config(
        paths,
        outbound_enabled=True,
        outbound_recipient=ALLOWED,
        outbound_quiet_hours=["00:00", "00:00"],
    )
    if seed_outbox:
        append_signal_outbox_entry(
            paths.signal_outbox_file,
            build_signal_outbox_entry(
                content="试验用的出站消息",
                source_event_id="wake_trial_1",
                trigger="scheduled-wake",
                now=datetime.now(),
            ),
        )
    return paths


def run_trial(paths, transport, **kwargs):
    kwargs.setdefault("confirm_real_signal_send", True)
    return run_m11_outbound_trial(paths, transport=transport, **kwargs)


def test_outbound_trial_delivers_and_writes_evidence(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport()

    result = run_trial(paths, transport)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m11_signal_outbound_trial_ready"
    assert report["milestone"] == "M11.4"
    assert report["trial"]["delivered_count"] == 1
    assert report["trial"]["failed_count"] == 0
    assert transport.sent == [{"recipient": ALLOWED, "text": "试验用的出站消息"}]
    public = report["trial"]["records"][0]
    assert "content" not in public
    assert str(public["content_hash"]).startswith("sha256:")

    report_path = write_m11_outbound_trial_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m11_signal_outbound_trial_ready"


def test_outbound_trial_refuses_without_confirmation(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport()

    result = run_trial(paths, transport, confirm_real_signal_send=False)

    assert result.ok is False
    assert "operator_confirmation" in result.report["stop_reasons"]
    assert transport.send_calls == 0


def test_outbound_trial_requires_evidence_and_enabled_config(tmp_path):
    paths = make_home(tmp_path)
    write_config(paths)  # outbound_enabled defaults false

    result = run_trial(paths, FakeSignalTransport())

    stop = result.report["stop_reasons"]
    assert "source_report_m11_3" in stop
    assert "source_report_m10_2" in stop
    assert "source_report_m10_3" in stop
    assert "upstream_freeze_evidence" in stop
    assert "outbound_config_ready" in stop


def test_outbound_trial_requires_pending_entry_and_clear_flags(tmp_path):
    paths = ready_home(tmp_path, seed_outbox=False)
    result = run_trial(paths, FakeSignalTransport())
    assert result.ok is False
    assert "outbox_has_pending_entry" in result.report["stop_reasons"]

    paused = ready_home(tmp_path / "paused")
    paused.signal_outbound_pause_flag.touch()
    paused_result = run_trial(paused, FakeSignalTransport())
    assert "pause_flags_clear" in paused_result.report["stop_reasons"]


def test_outbound_trial_fails_when_delivery_fails(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport()
    transport.fail_next_sends = 2

    result = run_trial(paths, transport)

    assert result.ok is False
    assert "trial_execution" in result.report["stop_reasons"]
    assert result.report["trial"]["failed_count"] == 1


def test_outbound_trial_refuses_to_race_running_bridge(tmp_path):
    paths = ready_home(tmp_path)

    with open(paths.signal_chat_lock_file, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = run_trial(paths, FakeSignalTransport())

    assert result.ok is False
    assert any(
        "loop lock" in stage["message"]
        for stage in result.report["stages"]
        if stage["name"] == "trial_execution"
    )


def test_outbound_trial_bound_is_enforced(tmp_path):
    paths = ready_home(tmp_path)

    result = run_trial(paths, FakeSignalTransport(), max_passes=9)

    assert result.ok is False
    assert "trial_bound" in result.report["stop_reasons"]
