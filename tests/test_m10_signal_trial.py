import fcntl
import json

from companion_core import (
    DialogueRunner,
    FailingDialogueLLMClient,
    FakeSignalTransport,
    InboundSignalMessage,
    JsonMemoryStore,
    StaticDialogueLLMClient,
    load_signal_chat_attempts,
    run_m10_signal_trial,
    write_m10_signal_trial_report,
)

from m10_evidence import (
    ALLOWED,
    OTHER,
    make_home,
    write_config,
    write_dry_run_report,
    write_upstream_freezes,
)


def make_runner(paths, llm_client=None):
    return DialogueRunner(
        paths,
        llm_client=llm_client or StaticDialogueLLMClient(),
        memory_store=JsonMemoryStore(paths.memory_store),
    )


def ready_home(tmp_path):
    paths = make_home(tmp_path)
    write_dry_run_report(paths)
    write_upstream_freezes(paths)
    write_config(paths)
    return paths


def inbound(timestamp=1000, sender=ALLOWED, body="你好，试验消息"):
    return InboundSignalMessage(sender=sender, timestamp=timestamp, body=body)


def test_trial_passes_with_reply_and_writes_evidence(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport([[inbound(), inbound(timestamp=1001, sender=OTHER)]])

    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=True,
    )
    report = result.to_dict()

    assert result.ok is True
    assert result.recommendation == "m10_signal_trial_ready"
    assert report["milestone"] == "M10.2"
    assert report["trial"]["replied_count"] == 1
    assert report["trial"]["failed_count"] == 0
    assert report["trial"]["decision_counts"]["skipped"] == 1
    assert len(transport.sent) == 1
    assert transport.sent[0]["recipient"] == ALLOWED

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert all(record["mode"] == "trial" for record in ledger)
    public_attempts = report["trial"]["attempts"]
    assert all("body" not in attempt for attempt in public_attempts)
    assert all(str(attempt["body_hash"]).startswith("sha256:") for attempt in public_attempts)

    report_path = write_m10_signal_trial_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m10_signal_trial_ready"


def test_trial_refuses_without_confirmation(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport([[inbound()]])

    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=False,
    )

    assert result.ok is False
    assert result.recommendation == "inspect"
    assert "operator_confirmation" in result.report["stop_reasons"]
    assert transport.sent == []


def test_trial_requires_dry_run_and_freeze_evidence(tmp_path):
    paths = make_home(tmp_path)
    write_config(paths)
    transport = FakeSignalTransport([[inbound()]])

    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=True,
    )

    assert result.ok is False
    stop = result.report["stop_reasons"]
    assert "m10_dry_run_ready" in stop
    assert "upstream_freeze_evidence" in stop
    assert transport.sent == []


def test_trial_fails_without_any_reply(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport([[]])

    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=True,
    )

    assert result.ok is False
    assert "trial_execution" in result.report["stop_reasons"]


def test_trial_fails_when_dialogue_fails(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport([[inbound()]])

    result = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths, llm_client=FailingDialogueLLMClient()),
        provider="fake",
        confirm_real_signal_send=True,
    )

    assert result.ok is False
    assert result.report["trial"]["failed_count"] == 1
    assert "trial_execution" in result.report["stop_reasons"]


def test_trial_refuses_to_race_running_bridge(tmp_path):
    paths = ready_home(tmp_path)
    transport = FakeSignalTransport([[inbound()]])

    with open(paths.signal_chat_lock_file, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = run_m10_signal_trial(
            paths,
            transport=transport,
            dialogue_runner=make_runner(paths),
            provider="fake",
            confirm_real_signal_send=True,
        )

    assert result.ok is False
    assert "trial_execution" in result.report["stop_reasons"]
    assert any(
        "loop lock" in stage["message"]
        for stage in result.report["stages"]
        if stage["name"] == "trial_execution"
    )
    assert transport.sent == []


def test_trial_respects_pause_flag_and_bound(tmp_path):
    paths = ready_home(tmp_path)
    paths.signal_chat_pause_flag.touch()
    transport = FakeSignalTransport([[inbound()]])

    paused = run_m10_signal_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=True,
    )
    assert paused.ok is False
    assert "pause_flag_clear" in paused.report["stop_reasons"]
    paths.signal_chat_pause_flag.unlink()

    unbounded = run_m10_signal_trial(
        paths,
        transport=FakeSignalTransport([[inbound(timestamp=2000)]]),
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_signal_send=True,
        max_polls=9,
    )
    assert unbounded.ok is False
    assert "trial_bound" in unbounded.report["stop_reasons"]
