import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    FakeFeishuTransport,
    FakeTTSBackend,
    append_signal_chat_attempts,
    load_signal_chat_attempts,
    run_m14_feishu_media_dry_run,
    run_m14_feishu_media_freeze,
    run_m14_feishu_media_observation,
    run_m14_feishu_media_trial,
    write_m14_feishu_media_dry_run_report,
    write_m14_feishu_media_freeze_report,
    write_m14_feishu_media_observation_report,
    write_m14_feishu_media_trial_report,
)

from m10_evidence import make_home, write_upstream_freezes

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ID = "cli_m14_gate"
ALLOWED = "ou_m14_gate"


def write_feishu_config(paths, **overrides):
    payload = {
        "account": APP_ID,
        "allowed_senders": [ALLOWED],
        "voice_replies": "always",
        "tts_command": "fake {output}",
        "image_attachments_enabled": True,
    }
    payload.update(overrides)
    paths.feishu_chat_config_file.write_text(json.dumps(payload))


def write_m13_trial_report(paths, *, ok=True):
    (paths.life_loop_dir / "m13_feishu_trial_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M13.2",
        "recommendation": "m13_feishu_trial_ready" if ok else "inspect",
        "saved_at": "2026-07-20T18:00:00",
        "stop_reasons": [],
    }))


def write_m13_freeze_report(paths, *, ok=True):
    (paths.life_loop_dir / "m13_feishu_freeze_report.json").write_text(json.dumps({
        "ok": ok,
        "milestone": "M13.5",
        "recommendation": "m13_feishu_chat_frozen" if ok else "inspect",
        "saved_at": "2026-07-20T18:30:00",
        "stop_reasons": [],
    }))


# --- M14.1 dry run ---


def test_m14_dry_run_passes_all_stages(tmp_path):
    paths = make_home(tmp_path)

    result = run_m14_feishu_media_dry_run(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m14_feishu_media_dry_run_ready"
    assert report["milestone"] == "M14.1"
    stage_names = {stage["name"] for stage in report["stages"] if stage["status"] == "pass"}
    assert {
        "tts_pipeline",
        "multipart_upload",
        "voice_coverage",
        "image_coverage",
        "text_reply_priority",
        "hint_injection",
        "media_ledger_hygiene",
        "media_config_template",
        "static_guard",
    } <= stage_names
    assert report["dry_run"]["replied_despite_media_failures"] is True
    assert report["dry_run"]["signal_transport_media_skipped"] is True

    ledger = load_signal_chat_attempts(paths.signal_chat_attempts_file)
    assert ledger and all(record["mode"] == "dry_run" for record in ledger)


def test_m14_dry_run_cli(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m14_feishu_media_dry_run.py"),
            "--companion-home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m14_feishu_media_dry_run_ready"
    assert (tmp_path / "life-loop" / "m14_feishu_media_dry_run_report.json").exists()


# --- M14.2 trial ---


def trial_home(tmp_path):
    paths = make_home(tmp_path)
    dry = run_m14_feishu_media_dry_run(paths, write_runtime=False)
    write_m14_feishu_media_dry_run_report(paths, dry.to_dict())
    write_m13_trial_report(paths)
    write_upstream_freezes(paths)
    write_feishu_config(paths)
    art = paths.home / "creations" / "art"
    art.mkdir(parents=True, exist_ok=True)
    (art / "trial.png").write_bytes(b"PNG-trial")
    return paths


def test_m14_trial_sends_voice_and_image(tmp_path):
    paths = trial_home(tmp_path)
    transport = FakeFeishuTransport()

    result = run_m14_feishu_media_trial(
        paths,
        transport=transport,
        confirm_real_feishu_send=True,
        image_path="creations/art/trial.png",
        tts_backend=FakeTTSBackend(),
    )
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m14_feishu_media_trial_ready"
    assert report["trial"]["voice"]["sent"] is True
    assert report["trial"]["image"]["sent"] is True
    assert len(transport.sent_voices) == 1
    assert len(transport.sent_images) == 1
    write_m14_feishu_media_trial_report(paths, report)


def test_m14_trial_refusals(tmp_path):
    paths = trial_home(tmp_path)
    transport = FakeFeishuTransport()

    no_confirm = run_m14_feishu_media_trial(
        paths,
        transport=transport,
        confirm_real_feishu_send=False,
        tts_backend=FakeTTSBackend(),
    )
    assert no_confirm.ok is False
    assert "operator_confirmation" in no_confirm.report["stop_reasons"]
    assert transport.sent_voices == []

    escape = run_m14_feishu_media_trial(
        paths,
        transport=FakeFeishuTransport(),
        confirm_real_feishu_send=True,
        image_path="../outside.png",
        tts_backend=FakeTTSBackend(),
    )
    assert escape.ok is False
    assert "trial_execution" in escape.report["stop_reasons"]

    tts_fail = run_m14_feishu_media_trial(
        paths,
        transport=FakeFeishuTransport(),
        confirm_real_feishu_send=True,
        tts_backend=FakeTTSBackend(fail=True),
    )
    assert tts_fail.ok is False
    assert "trial_execution" in tts_fail.report["stop_reasons"]


# --- M14.3 observation / M14.4 freeze ---


def observed_home(tmp_path):
    paths = trial_home(tmp_path)
    trial = run_m14_feishu_media_trial(
        paths,
        transport=FakeFeishuTransport(),
        confirm_real_feishu_send=True,
        image_path="creations/art/trial.png",
        tts_backend=FakeTTSBackend(),
    )
    write_m14_feishu_media_trial_report(paths, trial.to_dict())
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [{
        "id": "sigchat_media_live_1",
        "created_at": "2026-07-20T19:00:00",
        "direction": "inbound",
        "channel": "feishu",
        "mode": "live",
        "transport": "feishu",
        "sender": ALLOWED,
        "message_timestamp": 9000,
        "body_hash": "sha256:" + "a" * 64,
        "decision": "replied",
        "skip_reason": None,
        "conversation_id": f"feishu_{ALLOWED}",
        "dialogue_event_id": "dialogue_media_1",
        "reply_hash": "sha256:" + "b" * 64,
        "media": {
            "voice": {"requested": True, "sent": True, "duration_ms": 2100},
            "images": {"sent": 1, "sent_paths": ["creations/art/trial.png"], "rejected": [], "errors": []},
        },
        "boundaries": {},
        "error": None,
    }])
    return paths


def test_m14_observation_and_freeze_pass(tmp_path):
    paths = observed_home(tmp_path)

    observation = run_m14_feishu_media_observation(paths)
    obs_report = observation.to_dict()
    assert observation.ok is True, obs_report["stop_reasons"]
    assert obs_report["observation"]["voice_sent"] == 1
    assert obs_report["observation"]["images_sent"] == 1
    write_m14_feishu_media_observation_report(paths, obs_report)

    write_m13_freeze_report(paths)
    freeze = run_m14_feishu_media_freeze(paths)
    freeze_report = freeze.to_dict()
    assert freeze.ok is True, freeze_report["stop_reasons"]
    assert freeze.recommendation == "m14_feishu_media_frozen"
    assert freeze_report["final_freeze"]["frozen"] is True
    write_m14_feishu_media_freeze_report(paths, freeze_report)


def test_m14_observation_flags_failures_and_escapes(tmp_path):
    paths = observed_home(tmp_path)
    append_signal_chat_attempts(paths.signal_chat_attempts_file, [{
        "id": "sigchat_media_live_2",
        "created_at": "2026-07-20T19:10:00",
        "direction": "inbound",
        "channel": "feishu",
        "mode": "live",
        "transport": "feishu",
        "sender": ALLOWED,
        "message_timestamp": 9100,
        "body_hash": "sha256:" + "c" * 64,
        "decision": "replied",
        "skip_reason": None,
        "conversation_id": f"feishu_{ALLOWED}",
        "dialogue_event_id": "dialogue_media_2",
        "reply_hash": "sha256:" + "d" * 64,
        "media": {
            "voice": {"requested": True, "sent": False, "error": {"type": "TTSError", "message": "boom"}},
            "images": {"sent": 1, "sent_paths": ["/etc/passwd"], "rejected": [], "errors": []},
        },
        "boundaries": {},
        "error": None,
    }])

    result = run_m14_feishu_media_observation(paths)

    stop = result.report["stop_reasons"]
    assert result.ok is False
    assert "media_health" in stop
    assert "media_discipline" in stop


def test_m14_freeze_requires_sources_and_m13_freeze(tmp_path):
    paths = observed_home(tmp_path)
    observation = run_m14_feishu_media_observation(paths)
    write_m14_feishu_media_observation_report(paths, observation.to_dict())

    missing_m13 = run_m14_feishu_media_freeze(paths)
    assert missing_m13.ok is False
    assert "source_report_m13_5" in missing_m13.report["stop_reasons"]

    write_m13_freeze_report(paths, ok=False)
    broken = run_m14_feishu_media_freeze(paths)
    assert broken.ok is False
    assert "source_report_m13_5" in broken.report["stop_reasons"]


# --- /life panel ---


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m14_media_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m14_section(tmp_path, monkeypatch):
    paths = observed_home(tmp_path)
    observation = run_m14_feishu_media_observation(paths)
    write_m14_feishu_media_observation_report(paths, observation.to_dict())
    write_m13_freeze_report(paths)
    freeze = run_m14_feishu_media_freeze(paths)
    write_m14_feishu_media_freeze_report(paths, freeze.to_dict())

    window = load_window_module(paths.home, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M14 Feishu Media" in html
    assert "m14_feishu_media_frozen" in html
    assert "media_voice_sent=1" in html
    assert "media_images_sent=1" in html
    assert "m14_frozen=True" in html


def test_life_dashboard_handles_missing_m14_reports(tmp_path, monkeypatch):
    CompanionPaths(tmp_path).ensure_runtime_dirs()
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")
    assert response.status_code == 200
    assert "No M14 feishu media report captured." in response.data.decode()
