import fcntl
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    DialogueRunner,
    FailingDialogueLLMClient,
    FakeFeishuTransport,
    InboundSignalMessage,
    JsonMemoryStore,
    StaticDialogueLLMClient,
    load_signal_chat_attempts,
    run_m13_feishu_activation,
    run_m13_feishu_disable,
    run_m13_feishu_dry_run,
    run_m13_feishu_freeze,
    run_m13_feishu_observation,
    run_m13_feishu_trial,
    write_m13_feishu_activation_report,
    write_m13_feishu_dry_run_report,
    write_m13_feishu_freeze_report,
    write_m13_feishu_observation_report,
    write_m13_feishu_trial_report,
)
from companion_core.m13_feishu_activation import UNIT_MARKER, UNIT_NAME

from m10_evidence import make_home, write_upstream_freezes

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ID = "cli_gate_app"
ALLOWED = "ou_gate_human"


def write_feishu_config(paths, **overrides):
    payload = {
        "account": APP_ID,
        "allowed_senders": [ALLOWED],
        "daily_reply_budget": 50,
        "outbound_enabled": False,
    }
    payload.update(overrides)
    paths.feishu_chat_config_file.write_text(json.dumps(payload))


def write_runner_stub(paths):
    script_dir = paths.home / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "run_m13_feishu_chat.py").write_text(
        "# test stub\n# --confirm-real-feishu-send\n# run_loop\n# start_listener\n# feishu_chat_lock_file\n"
    )


def make_runner(paths, llm_client=None):
    return DialogueRunner(
        paths,
        llm_client=llm_client or StaticDialogueLLMClient(),
        memory_store=JsonMemoryStore(paths.memory_store),
    )


def inbound(timestamp=1000, sender=ALLOWED, body="你好,试验消息"):
    return InboundSignalMessage(sender=sender, timestamp=timestamp, body=body)


# --- M13.1 dry run ---


def test_m13_dry_run_passes_and_covers_branches(tmp_path):
    paths = make_home(tmp_path)

    result = run_m13_feishu_dry_run(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m13_feishu_dry_run_ready"
    assert report["milestone"] == "M13.1"
    assert report["provider_calls"] == 0
    dry_run = report["dry_run"]
    assert dry_run["decision_counts"]["replied"] >= 1
    assert dry_run["failed_branches_covered"] == {"dialogue_failure": True, "send_failure": True}
    assert dry_run["conversation_prefix_confirmed"] is True
    assert report["transport"]["feishu_api_invoked"] is False

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert ledger and all(record["channel"] == "feishu" for record in ledger)
    assert all(record["mode"] == "dry_run" for record in ledger)


def test_m13_dry_run_cli_writes_report(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m13_feishu_dry_run.py"),
            "--companion-home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m13_feishu_dry_run_ready"
    assert (tmp_path / "life-loop" / "m13_feishu_dry_run_report.json").exists()


# --- M13.2 trial ---


def trial_home(tmp_path):
    paths = make_home(tmp_path)
    dry = run_m13_feishu_dry_run(paths, write_runtime=False)
    write_m13_feishu_dry_run_report(paths, dry.to_dict())
    write_upstream_freezes(paths)
    write_feishu_config(paths)
    return paths


def test_m13_trial_passes_with_reply(tmp_path):
    paths = trial_home(tmp_path)
    transport = FakeFeishuTransport([[inbound(), inbound(timestamp=1001, sender="ou_stranger")]])

    result = run_m13_feishu_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_feishu_send=True,
    )
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m13_feishu_trial_ready"
    assert report["milestone"] == "M13.2"
    assert report["trial"]["replied_count"] == 1
    assert report["trial"]["decision_counts"]["skipped"] == 1
    public = report["trial"]["attempts"][0]
    assert "body" not in public and str(public["body_hash"]).startswith("sha256:")

    report_path = write_m13_feishu_trial_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m13_feishu_trial_ready"


def test_m13_trial_refusals(tmp_path):
    paths = trial_home(tmp_path)

    no_confirm = run_m13_feishu_trial(
        paths,
        transport=FakeFeishuTransport([[inbound()]]),
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_feishu_send=False,
    )
    assert no_confirm.ok is False
    assert "operator_confirmation" in no_confirm.report["stop_reasons"]

    bare = make_home(tmp_path / "bare")
    write_feishu_config(bare)
    missing = run_m13_feishu_trial(
        bare,
        transport=FakeFeishuTransport([[inbound()]]),
        dialogue_runner=make_runner(bare),
        provider="fake",
        confirm_real_feishu_send=True,
    )
    stop = missing.report["stop_reasons"]
    assert "source_report_m13_1" in stop
    assert "upstream_freeze_evidence" in stop

    failing = run_m13_feishu_trial(
        paths,
        transport=FakeFeishuTransport([[inbound(timestamp=2000)]]),
        dialogue_runner=make_runner(paths, llm_client=FailingDialogueLLMClient()),
        provider="fake",
        confirm_real_feishu_send=True,
    )
    assert failing.ok is False
    assert "trial_execution" in failing.report["stop_reasons"]


def test_m13_trial_respects_feishu_lock(tmp_path):
    paths = trial_home(tmp_path)
    with open(paths.feishu_chat_lock_file, "w") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        result = run_m13_feishu_trial(
            paths,
            transport=FakeFeishuTransport([[inbound()]]),
            dialogue_runner=make_runner(paths),
            provider="fake",
            confirm_real_feishu_send=True,
        )
    assert result.ok is False
    assert any(
        "loop lock" in stage["message"]
        for stage in result.report["stages"]
        if stage["name"] == "trial_execution"
    )


# --- M13.3 activation ---


def activation_home(tmp_path):
    paths = trial_home(tmp_path)
    transport = FakeFeishuTransport([[inbound()]])
    trial = run_m13_feishu_trial(
        paths,
        transport=transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        confirm_real_feishu_send=True,
    )
    write_m13_feishu_trial_report(paths, trial.to_dict())
    write_runner_stub(paths)
    return paths


def test_m13_activation_enable_disable_cycle(tmp_path):
    paths = activation_home(tmp_path / "home")
    unit_dir = tmp_path / "units"
    systemctl_calls = []

    result = run_m13_feishu_activation(paths, unit_dir=unit_dir, systemctl_runner=systemctl_calls.append)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m13_feishu_activation_ready"
    unit_path = unit_dir / UNIT_NAME
    content = unit_path.read_text()
    assert UNIT_MARKER in content
    assert "--confirm-real-feishu-send" in content
    assert systemctl_calls == [["daemon-reload"], ["enable", "--now", UNIT_NAME]]
    assert report["service"]["channel"] == "feishu"

    write_m13_feishu_activation_report(paths, report)

    disable = run_m13_feishu_disable(paths, unit_dir=unit_dir, systemctl_runner=systemctl_calls.append)
    assert disable.ok is True
    assert disable.recommendation == "m13_feishu_activation_disabled"
    assert not unit_path.exists()


def test_m13_activation_requires_trial_evidence(tmp_path):
    paths = trial_home(tmp_path / "home")  # no trial report written
    write_runner_stub(paths)
    calls = []
    result = run_m13_feishu_activation(paths, unit_dir=tmp_path / "units", systemctl_runner=calls.append)
    assert result.ok is False
    assert "source_report_m13_2" in result.report["stop_reasons"]
    assert calls == []


# --- M13.4 observation / M13.5 freeze ---


def observed_home(tmp_path):
    paths = activation_home(tmp_path / "home")
    activation = run_m13_feishu_activation(
        paths,
        unit_dir=tmp_path / "units",
        systemctl_runner=lambda args: None,
    )
    write_m13_feishu_activation_report(paths, activation.to_dict())
    # Additional live-mode traffic so the observation volume gate passes.
    transport = FakeFeishuTransport([
        [inbound(timestamp=3000, body="第二条")],
        [inbound(timestamp=4000, body="第三条")],
    ])
    from companion_core import SignalChatBridge, SignalChatConfig

    bridge = SignalChatBridge(
        paths,
        SignalChatConfig(account=APP_ID, allowed_senders=(ALLOWED,)),
        transport,
        dialogue_runner=make_runner(paths),
        provider="fake",
        mode="live",
        lock_path=paths.feishu_chat_lock_file,
    )
    bridge.poll_once()
    bridge.poll_once()
    return paths


def test_m13_observation_and_freeze_pass_with_real_evidence(tmp_path):
    paths = observed_home(tmp_path)

    observation = run_m13_feishu_observation(paths)
    obs_report = observation.to_dict()
    assert observation.ok is True, obs_report["stop_reasons"]
    assert observation.recommendation == "m13_feishu_observation_ready"
    assert obs_report["observation"]["observed_attempts"] == 3
    write_m13_feishu_observation_report(paths, obs_report)

    freeze = run_m13_feishu_freeze(paths)
    freeze_report = freeze.to_dict()
    assert freeze.ok is True, freeze_report["stop_reasons"]
    assert freeze.recommendation == "m13_feishu_chat_frozen"
    assert freeze_report["milestone"] == "M13.5"
    assert freeze_report["final_freeze"]["frozen"] is True
    assert freeze_report["evidence"]["feishu_attempts_observed"] == 3
    write_m13_feishu_freeze_report(paths, freeze_report)


def test_m13_observation_ignores_signal_records(tmp_path):
    from m10_evidence import make_attempt
    from companion_core import append_signal_chat_attempts

    paths = observed_home(tmp_path)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [
        make_attempt(
            decision="failed",
            timestamp=9000,
            error={"type": "SignalTransportError", "message": "signal-side failure"},
        ),
    ])

    result = run_m13_feishu_observation(paths)

    assert result.ok is True, result.report["stop_reasons"]
    assert result.report["observation"]["observed_attempts"] == 3


def test_m13_freeze_requires_all_sources(tmp_path):
    paths = observed_home(tmp_path)
    result = run_m13_feishu_freeze(paths)  # observation report not written yet
    assert result.ok is False
    assert "source_report_m13_4" in result.report["stop_reasons"]


# --- /life panel ---


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m13_feishu_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m13_section(tmp_path, monkeypatch):
    paths = observed_home(tmp_path)
    observation = run_m13_feishu_observation(paths)
    write_m13_feishu_observation_report(paths, observation.to_dict())
    freeze = run_m13_feishu_freeze(paths)
    write_m13_feishu_freeze_report(paths, freeze.to_dict())

    window = load_window_module(paths.home, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M13 Feishu Chat" in html
    assert "m13_feishu_chat_frozen" in html
    assert "feishu_service_unit=companion-feishu-chat.service" in html
    assert "feishu_observed_attempts=3" in html
    assert "m13_frozen=True" in html


def test_life_dashboard_handles_missing_m13_reports(tmp_path, monkeypatch):
    make_home(tmp_path)
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")
    assert response.status_code == 200
    assert "No M13 feishu chat report captured." in response.data.decode()
