from companion_core import (
    append_signal_chat_attempts,
    run_m10_signal_observation,
)

from m10_evidence import (
    ALLOWED,
    OTHER,
    make_attempt,
    make_home,
    make_outbound_record,
    write_activation_report,
    write_config,
)


def ready_home(tmp_path, attempts=None):
    paths = make_home(tmp_path)
    write_activation_report(paths)
    write_config(paths)
    if attempts is None:
        attempts = [
            make_attempt(timestamp=1000),
            make_attempt(timestamp=2000),
            make_attempt(
                decision="skipped",
                timestamp=3000,
                sender=OTHER,
                skip_reason="sender_not_allowed",
            ),
        ]
    append_signal_chat_attempts(paths.signal_chat_attempts_file, attempts)
    return paths


def test_observation_passes_on_healthy_ledger(tmp_path):
    paths = ready_home(tmp_path)
    # M11 outbound rows share the same ledger; M10.4 must ignore them even
    # when they are live-mode failures or carry M11-only skip reasons.
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_outbound_record(
            decision="failed",
            entry_id="outbox_live_fail",
            source_event_id="wake_out_1",
            error={"type": "SignalTransportError", "message": "send timed out"},
        ),
        make_outbound_record(
            decision="skipped",
            skip_reason="expired",
            entry_id="outbox_live_expired",
            source_event_id="wake_out_2",
        ),
    ])

    result = run_m10_signal_observation(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m10_signal_observation_ready"
    assert report["milestone"] == "M10.4"
    assert report["observation"]["observed_attempts"] == 3
    assert report["observation"]["decision_counts"] == {"replied": 2, "skipped": 1}
    assert report["pause_drill"]["ready"] is True
    assert report["pause_drill"]["flag_restored"] is True
    assert not paths.signal_chat_pause_flag.exists()
    assert report["boundaries"]["provider_calls"] == 0


def test_observation_ignores_dry_run_records(tmp_path):
    paths = make_home(tmp_path)
    write_activation_report(paths)
    write_config(paths)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_attempt(timestamp=1000, mode="dry_run"),
        make_attempt(timestamp=2000, mode="dry_run"),
        make_attempt(timestamp=3000, mode="dry_run"),
    ])

    result = run_m10_signal_observation(paths)

    assert result.ok is False
    assert "attempt_volume" in result.report["stop_reasons"]
    assert result.report["observation"]["observed_attempts"] == 0


def test_observation_flags_failures_and_missing_activation(tmp_path):
    paths = ready_home(tmp_path, attempts=[
        make_attempt(timestamp=1000),
        make_attempt(timestamp=2000),
        make_attempt(
            decision="failed",
            timestamp=3000,
            error={"type": "SignalTransportError", "message": "send timed out"},
        ),
    ])

    result = run_m10_signal_observation(paths)
    assert result.ok is False
    assert "decision_health" in result.report["stop_reasons"]

    bare = make_home(tmp_path / "bare")
    write_config(bare)
    bare_result = run_m10_signal_observation(bare)
    assert bare_result.ok is False
    assert "m10_activation_ready" in bare_result.report["stop_reasons"]


def test_observation_flags_allowlist_and_dedupe_violations(tmp_path):
    paths = ready_home(tmp_path, attempts=[
        make_attempt(timestamp=1000),
        make_attempt(timestamp=1000),
        make_attempt(timestamp=2000, sender=OTHER),
    ])

    result = run_m10_signal_observation(paths)

    stop = result.report["stop_reasons"]
    assert result.ok is False
    assert "dedupe_correctness" in stop
    assert "reply_discipline" in stop


def test_observation_flags_budget_violation(tmp_path):
    attempts = [
        make_attempt(timestamp=1000 + offset, created_at="2026-07-20T10:00:00")
        for offset in range(3)
    ]
    paths = make_home(tmp_path)
    write_activation_report(paths)
    write_config(paths, daily_reply_budget=2)
    from companion_core import append_signal_chat_attempts as append
    append(paths.signal_chat_attempts_file, attempts)

    result = run_m10_signal_observation(paths)

    assert result.ok is False
    assert "budget_discipline" in result.report["stop_reasons"]


def test_observation_flags_raw_body_storage(tmp_path):
    paths = ready_home(tmp_path, attempts=[
        make_attempt(timestamp=1000),
        make_attempt(timestamp=2000),
        make_attempt(timestamp=3000, body="raw text that must not exist"),
    ])

    result = run_m10_signal_observation(paths)

    assert result.ok is False
    assert "hashed_storage" in result.report["stop_reasons"]


def test_observation_pause_drill_preserves_existing_flag(tmp_path):
    paths = ready_home(tmp_path)
    paths.signal_chat_pause_flag.touch()

    result = run_m10_signal_observation(paths)

    assert result.report["pause_drill"]["ready"] is True
    assert result.report["pause_drill"]["flag_existed_before"] is True
    assert paths.signal_chat_pause_flag.exists()


def test_observation_can_skip_pause_drill(tmp_path):
    paths = ready_home(tmp_path)

    result = run_m10_signal_observation(paths, perform_pause_drill=False)

    assert result.ok is True
    assert result.report["pause_drill"]["performed"] is False
