import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    run_m8_dialogue_humanity_regression,
    write_m8_dialogue_humanity_report,
)


def write_source_evidence(home: Path):
    life_loop = home / "life-loop"
    life_loop.mkdir(parents=True, exist_ok=True)
    (life_loop / "m7_dialogue_freeze_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m7_text_dialogue_frozen",
    }))
    (life_loop / "m8_memory_retrieval_report.json").write_text(json.dumps({
        "ok": True,
        "recommendation": "m8_memory_retrieval_ready",
    }))


def test_m8_dialogue_humanity_regression_passes_in_isolated_smoke_home(tmp_path):
    write_source_evidence(tmp_path)
    paths = CompanionPaths(tmp_path)

    result = run_m8_dialogue_humanity_regression(paths, smoke_home=tmp_path / "smoke")
    report = result.to_dict()

    assert report["ok"] is True
    assert report["recommendation"] == "m8_dialogue_humanity_ready"
    assert report["stop_reasons"] == []
    assert report["provider_calls"] == 0
    assert report["profile"]["production_dialogue_writes"] is False
    assert report["cases"]["casual_chat"]["style_memory_in_prompt"] is True
    assert report["cases"]["casual_chat"]["project_status_in_prompt"] is False
    assert report["cases"]["casual_chat"]["non_prompt_memory_in_prompt"] is False
    assert report["cases"]["status_query"]["project_status_in_prompt"] is True
    assert report["cases"]["provider_failure"]["transcript_roles"] == ["human", "human", "assistant"]
    assert not paths.conversation_events_file.exists()
    assert not paths.wake_events_file.exists()
    assert (tmp_path / "smoke" / "life-loop" / "conversation_events.jsonl").exists()


def test_m8_dialogue_humanity_regression_flags_report_like_casual_reply(tmp_path):
    write_source_evidence(tmp_path)
    paths = CompanionPaths(tmp_path)

    result = run_m8_dialogue_humanity_regression(
        paths,
        smoke_home=tmp_path / "smoke",
        scenario_outputs={
            "casual_reply": "结论：当前 M8.5 阶段报告如下。",
        },
    )
    report = result.to_dict()

    assert report["ok"] is False
    assert report["recommendation"] == "inspect"
    assert "m8_5_casual_chat_humanity" in report["stop_reasons"]
    stage = next(item for item in report["stages"] if item["name"] == "m8_5_casual_chat_humanity")
    assert stage["details"]["reply_report_like"] is True


def test_m8_dialogue_humanity_report_writer_and_cli_write_report_only(tmp_path):
    write_source_evidence(tmp_path)
    paths = CompanionPaths(tmp_path)
    result = run_m8_dialogue_humanity_regression(paths, smoke_home=tmp_path / "smoke")
    report_path = write_m8_dialogue_humanity_report(paths, result.to_dict())

    assert report_path == paths.life_loop_dir / "m8_dialogue_humanity_report.json"
    assert json.loads(report_path.read_text())["recommendation"] == "m8_dialogue_humanity_ready"
    assert not paths.wake_events_file.exists()

    script = Path(__file__).resolve().parents[1] / "scripts" / "run_m8_dialogue_humanity.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--companion-home",
            str(tmp_path),
            "--smoke-home",
            str(tmp_path / "cli-smoke"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m8_dialogue_humanity_ready"
    assert (tmp_path / "life-loop" / "m8_dialogue_humanity_report.json").exists()
    assert not paths.wake_events_file.exists()
