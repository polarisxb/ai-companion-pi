from companion_core import (
    append_signal_chat_attempts,
    run_m11_outbound_observation,
)

from m10_evidence import (
    ALLOWED,
    OTHER,
    make_home,
    make_outbound_record,
    write_config,
    write_m11_trial_report,
)


def ready_home(tmp_path, records=None, **config_overrides):
    paths = make_home(tmp_path)
    write_m11_trial_report(paths)
    config_overrides.setdefault("outbound_enabled", True)
    config_overrides.setdefault("outbound_recipient", ALLOWED)
    config_overrides.setdefault("daily_outbound_budget", 2)
    write_config(paths, **config_overrides)
    if records is None:
        records = [
            make_outbound_record(entry_id="outbox_1", source_event_id="wake_1", created_at="2026-07-20T12:00:00"),
            make_outbound_record(
                decision="skipped",
                skip_reason="expired",
                entry_id="outbox_2",
                source_event_id="wake_2",
                created_at="2026-07-20T12:05:00",
            ),
        ]
    append_signal_chat_attempts(paths.signal_chat_attempts_file, records)
    return paths


def test_observation_passes_on_healthy_outbound_ledger(tmp_path):
    paths = ready_home(tmp_path)

    result = run_m11_outbound_observation(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m11_signal_outbound_observation_ready"
    assert report["milestone"] == "M11.5"
    assert report["observation"]["observed_records"] == 2
    assert report["observation"]["decision_counts"] == {"delivered": 1, "skipped": 1}
    assert report["pause_drill"]["ready"] is True
    assert report["pause_drill"]["flag_restored"] is True
    assert not paths.signal_outbound_pause_flag.exists()


def test_observation_ignores_inbound_and_dry_run_records(tmp_path):
    from m10_evidence import make_attempt

    paths = ready_home(tmp_path, records=[
        make_attempt(timestamp=1000),
        make_outbound_record(entry_id="outbox_dry", mode="dry_run"),
    ])

    result = run_m11_outbound_observation(paths)

    assert result.ok is False
    assert "delivery_volume" in result.report["stop_reasons"]
    assert result.report["observation"]["observed_records"] == 0


def test_observation_flags_failures_and_abandonment(tmp_path):
    paths = ready_home(tmp_path, records=[
        make_outbound_record(entry_id="outbox_1", source_event_id="wake_1"),
        make_outbound_record(
            decision="failed",
            entry_id="outbox_2",
            source_event_id="wake_2",
            error={"type": "SignalTransportError", "message": "send timed out"},
        ),
        make_outbound_record(
            decision="skipped",
            skip_reason="abandoned_after_max_attempts",
            entry_id="outbox_3",
            source_event_id="wake_3",
        ),
    ])

    result = run_m11_outbound_observation(paths)

    assert result.ok is False
    assert "delivery_health" in result.report["stop_reasons"]


def test_observation_flags_recipient_budget_quiet_and_dedupe_violations(tmp_path):
    paths = ready_home(tmp_path, records=[
        make_outbound_record(entry_id="outbox_1", source_event_id="wake_1", recipient=OTHER),
        make_outbound_record(entry_id="outbox_2", source_event_id="wake_2", created_at="2026-07-20T03:00:00"),
        make_outbound_record(entry_id="outbox_3", source_event_id="wake_3", created_at="2026-07-20T12:10:00"),
        make_outbound_record(entry_id="outbox_4", source_event_id="wake_3", created_at="2026-07-20T12:20:00"),
    ])

    result = run_m11_outbound_observation(paths)
    stop = result.report["stop_reasons"]

    assert result.ok is False
    assert "recipient_discipline" in stop
    assert "outbound_budget_discipline" in stop  # 4 deliveries > budget 2
    assert "outbound_quiet_hours" in stop
    assert "outbound_dedupe_correctness" in stop


def test_observation_flags_raw_content_storage(tmp_path):
    paths = ready_home(tmp_path, records=[
        make_outbound_record(entry_id="outbox_1", source_event_id="wake_1"),
        make_outbound_record(entry_id="outbox_2", source_event_id="wake_2", content="原文不应存在"),
    ])

    result = run_m11_outbound_observation(paths)

    assert result.ok is False
    assert "outbound_hashed_storage" in result.report["stop_reasons"]


def test_observation_requires_trial_evidence(tmp_path):
    paths = make_home(tmp_path)
    write_config(paths, outbound_enabled=True, outbound_recipient=ALLOWED)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_outbound_record(entry_id="outbox_1", source_event_id="wake_1"),
    ])

    result = run_m11_outbound_observation(paths)

    assert result.ok is False
    assert "m11_outbound_trial_ready" in result.report["stop_reasons"]


def test_observation_pause_drill_preserves_existing_flag(tmp_path):
    paths = ready_home(tmp_path)
    paths.signal_outbound_pause_flag.touch()

    result = run_m11_outbound_observation(paths)

    assert result.report["pause_drill"]["ready"] is True
    assert result.report["pause_drill"]["flag_existed_before"] is True
    assert paths.signal_outbound_pause_flag.exists()
