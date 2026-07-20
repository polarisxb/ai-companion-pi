import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from companion_core import (
    CompanionPaths,
    HashingEmbeddingBackend,
    JsonMemoryStore,
    load_semantic_index,
    run_m12_semantic_backfill,
    run_m12_semantic_freeze,
    run_m12_semantic_observation,
    run_m12_semantic_readiness,
    run_m12_semantic_retrieval_check,
    write_m12_semantic_backfill_report,
    write_m12_semantic_freeze_report,
    write_m12_semantic_observation_report,
    write_m12_semantic_readiness_report,
    write_m12_semantic_retrieval_report,
)

from m10_evidence import write_upstream_freezes

REPO_ROOT = Path(__file__).resolve().parents[1]


class CountingBackend(HashingEmbeddingBackend):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return super().embed(texts)


def memory_row(memory_id, content, *, eligible=True, created_at="2026-07-01T10:00:00"):
    return {
        "id": memory_id,
        "content": content,
        "context": [],
        "date": created_at[:10],
        "created_at": created_at,
        "source": "human",
        "memory_type": "semantic",
        "source_type": "user",
        "authority": "user_asserted" if eligible else "model_proposed",
        "prompt_eligible": eligible,
        "accepted_for_context": eligible,
        "evidence_refs": [],
        "status": "active",
        "schema_refs": [],
    }


def make_home(tmp_path, *, enabled=True) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    JsonMemoryStore(paths.memory_store).save([
        memory_row("mem_sea", "海边散步让人放松，那天的浪声一直记得。"),
        memory_row("mem_room", "今天整理了房间，把书架擦干净了。", created_at="2026-07-02T10:00:00"),
        memory_row("mem_blocked", "未获批准的记忆", eligible=False, created_at="2026-07-03T10:00:00"),
    ])
    paths.semantic_retrieval_config_file.write_text(json.dumps({
        "enabled": enabled,
        "backend": "hashing",
        "min_similarity": 0.05,
        "semantic_scale": 10,
    }))
    return paths


# --- M12.3 backfill ---


def test_backfill_builds_then_stays_idempotent(tmp_path):
    paths = make_home(tmp_path)
    backend = CountingBackend()

    first = run_m12_semantic_backfill(paths, backend=backend)
    assert first.ok is True
    assert first.recommendation == "m12_semantic_backfill_ready"
    assert first.report["milestone"] == "M12.3"
    assert first.report["counts"]["embedded_new"] == 2
    assert first.report["counts"]["prompt_eligible"] == 2
    assert first.report["counts"]["index_entries"] == 2
    index = load_semantic_index(paths)
    assert set(index["entries"]) == {"mem_sea", "mem_room"}
    assert "mem_blocked" not in index["entries"]

    calls_after_first = backend.calls
    second = run_m12_semantic_backfill(paths, backend=backend)
    assert second.ok is True
    assert second.report["counts"]["unchanged"] == 2
    assert second.report["counts"]["embedded_new"] == 0
    # Idempotent rerun embeds nothing beyond the readiness probe.
    assert backend.calls == calls_after_first + 1


def test_backfill_refreshes_stale_and_prunes_removed(tmp_path):
    paths = make_home(tmp_path)
    backend = HashingEmbeddingBackend()
    assert run_m12_semantic_backfill(paths, backend=backend).ok is True

    store = JsonMemoryStore(paths.memory_store)
    memories = store.load()
    memories[0]["content"] = "海边散步的记忆被编辑过了。"
    memories = [memory for memory in memories if memory["id"] != "mem_room"]
    store.save(memories)

    result = run_m12_semantic_backfill(paths, backend=backend)
    counts = result.report["counts"]
    assert result.ok is True
    assert counts["refreshed_stale"] == 1
    assert counts["pruned"] == 1
    assert counts["index_entries"] == 1
    assert result.report["boundaries"]["store_mutated"] is False


def test_backfill_dry_run_writes_nothing(tmp_path):
    paths = make_home(tmp_path)
    result = run_m12_semantic_backfill(paths, backend=HashingEmbeddingBackend(), write_index=False)
    assert result.ok is True
    assert not paths.semantic_index_file.exists()


def test_backfill_cli_writes_report(tmp_path):
    paths = make_home(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_m12_semantic_backfill.py"),
            "--companion-home",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["recommendation"] == "m12_semantic_backfill_ready"
    assert (paths.life_loop_dir / "m12_semantic_backfill_report.json").exists()
    assert paths.semantic_index_file.exists()


# --- M12.1 readiness ---


def test_readiness_passes_before_backfill_and_enforces_index_when_required(tmp_path):
    paths = make_home(tmp_path)
    backend = HashingEmbeddingBackend()

    informational = run_m12_semantic_readiness(paths, backend=backend)
    assert informational.ok is True
    assert informational.report["milestone"] == "M12.1"
    assert informational.report["semantic_index"]["exists"] is False

    strict = run_m12_semantic_readiness(paths, backend=backend, require_index=True)
    assert strict.ok is False
    assert "index_coverage" in strict.report["stop_reasons"]

    assert run_m12_semantic_backfill(paths, backend=backend).ok is True
    ready = run_m12_semantic_readiness(paths, backend=backend, require_index=True)
    assert ready.ok is True
    assert ready.report["semantic_index"]["coverage_ratio"] == 1.0
    assert ready.report["census"]["prompt_eligible"] == 2


def test_readiness_fails_on_invalid_config(tmp_path):
    paths = make_home(tmp_path)
    paths.semantic_retrieval_config_file.write_text("{broken")
    result = run_m12_semantic_readiness(paths)
    assert result.ok is False
    assert "config_valid" in result.report["stop_reasons"]


# --- M12.2 behavior gate ---


def test_retrieval_check_gate_passes(tmp_path):
    paths = make_home(tmp_path)
    result = run_m12_semantic_retrieval_check(paths)
    report = result.to_dict()
    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m12_semantic_retrieval_ready"
    assert report["milestone"] == "M12.2"
    stage_names = {stage["name"] for stage in report["stages"] if stage["status"] == "pass"}
    assert {"semantic_gain", "policy_immunity", "deterministic_fallback", "retrieval_readonly"} <= stage_names
    # The gate runs in an isolated smoke home; the real home gains no index.
    assert not paths.semantic_index_file.exists()


# --- M12.4 observation ---


def observed_home(tmp_path):
    paths = make_home(tmp_path)
    backend = HashingEmbeddingBackend()
    backfill = run_m12_semantic_backfill(paths, backend=backend)
    write_m12_semantic_backfill_report(paths, backfill.to_dict())
    return paths, backend


def test_observation_passes_when_enabled_and_covered(tmp_path):
    paths, backend = observed_home(tmp_path)

    result = run_m12_semantic_observation(paths, backend=backend)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m12_semantic_observation_ready"
    assert report["milestone"] == "M12.4"
    assert report["index_coverage"]["ok"] is True
    assert report["live_probe"]["semantic"]["status"] == "applied"
    assert report["boundaries"]["index_written"] is False


def test_observation_requires_enabled_config_and_backfill_evidence(tmp_path):
    paths, backend = observed_home(tmp_path)
    paths.semantic_retrieval_config_file.write_text(json.dumps({"enabled": False, "backend": "hashing"}))
    disabled = run_m12_semantic_observation(paths, backend=backend)
    assert disabled.ok is False
    assert "config_enabled" in disabled.report["stop_reasons"]

    bare = make_home(tmp_path / "bare")
    missing = run_m12_semantic_observation(bare, backend=HashingEmbeddingBackend())
    assert missing.ok is False
    assert "source_report_m12_3" in missing.report["stop_reasons"]


def test_observation_flags_stale_index(tmp_path):
    paths, backend = observed_home(tmp_path)
    store = JsonMemoryStore(paths.memory_store)
    memories = store.load()
    memories[0]["content"] = "内容被改了但索引没同步。"
    store.save(memories)

    result = run_m12_semantic_observation(paths, backend=backend)

    assert result.ok is False
    assert "index_coverage" in result.report["stop_reasons"]
    assert result.report["index_coverage"]["stale"] == 1


# --- M12.5 freeze ---


def frozen_home(tmp_path):
    paths, backend = observed_home(tmp_path)
    write_upstream_freezes(paths)
    readiness = run_m12_semantic_readiness(paths, backend=backend, require_index=True)
    write_m12_semantic_readiness_report(paths, readiness.to_dict())
    check = run_m12_semantic_retrieval_check(paths)
    write_m12_semantic_retrieval_report(paths, check.to_dict())
    observation = run_m12_semantic_observation(paths, backend=backend)
    write_m12_semantic_observation_report(paths, observation.to_dict())
    return paths


def test_freeze_passes_with_full_real_evidence(tmp_path):
    paths = frozen_home(tmp_path)

    result = run_m12_semantic_freeze(paths)
    report = result.to_dict()

    assert result.ok is True, report["stop_reasons"]
    assert result.recommendation == "m12_semantic_retrieval_frozen"
    assert report["milestone"] == "M12.5"
    assert report["final_freeze"] == {
        "frozen": True,
        "readonly": True,
        "semantic_retrieval_ready": True,
        "json_store_authoritative": True,
        "index_reversible": True,
    }
    assert report["evidence"]["index_coverage_ratio"] == 1.0
    assert report["evidence"]["policy_immunity_proven"] is True
    assert report["evidence"]["fallback_drilled"] is True

    report_path = write_m12_semantic_freeze_report(paths, report)
    assert json.loads(report_path.read_text())["recommendation"] == "m12_semantic_retrieval_frozen"


def test_freeze_requires_sources_and_upstream(tmp_path):
    paths = frozen_home(tmp_path)
    (paths.life_loop_dir / "m12_semantic_observation_report.json").unlink()
    missing = run_m12_semantic_freeze(paths)
    assert missing.ok is False
    assert "source_report_m12_4" in missing.report["stop_reasons"]

    broken = frozen_home(tmp_path / "broken")
    (broken.life_loop_dir / "m8_memory_freeze_report.json").write_text(json.dumps({
        "ok": False,
        "milestone": "M8.7",
        "recommendation": "inspect",
    }))
    upstream = run_m12_semantic_freeze(broken)
    assert upstream.ok is False
    assert "memory_adjacent_freezes_intact" in upstream.report["stop_reasons"]


# --- /life panel ---


def load_window_module(home: Path, monkeypatch):
    monkeypatch.setenv("COMPANION_HOME", str(home))
    monkeypatch.setenv("COMPANION_SCRIPTS_DIR", str(home / "scripts"))
    module_path = REPO_ROOT / "window" / "window.py"
    spec = importlib.util.spec_from_file_location("window_m12_semantic_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_life_dashboard_shows_m12_section(tmp_path, monkeypatch):
    paths = frozen_home(tmp_path)
    freeze = run_m12_semantic_freeze(paths)
    write_m12_semantic_freeze_report(paths, freeze.to_dict())

    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    html = response.data.decode()
    assert "M12 Semantic Retrieval" in html
    assert "m12_semantic_retrieval_frozen" in html
    assert "semantic_backend=hashing" in html
    assert "semantic_coverage_ratio=1.0" in html
    assert "semantic_live_status=applied" in html
    assert "m12_frozen=True" in html
    assert "json_store_authoritative=True" in html


def test_life_dashboard_handles_missing_m12_reports(tmp_path, monkeypatch):
    CompanionPaths(tmp_path).ensure_runtime_dirs()
    window = load_window_module(tmp_path, monkeypatch)
    client = window.app.test_client()
    response = client.get("/life")

    assert response.status_code == 200
    assert "No M12 semantic retrieval report captured." in response.data.decode()
