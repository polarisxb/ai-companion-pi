import json

import pytest

from companion_core import (
    CompanionPaths,
    HashingEmbeddingBackend,
    JsonMemoryStore,
    SemanticRetrievalConfig,
    SemanticRetrievalConfigError,
    apply_semantic_ranking,
    assemble_dialogue_memory_context,
    cosine_similarity,
    load_semantic_index,
    load_semantic_retrieval_config,
    run_m12_semantic_backfill,
    save_semantic_index,
)
from companion_core.memory_retrieval import RetrievedMemory
from companion_core.semantic_retrieval import content_hash

# Fixture texts deliberately avoid the lexical style/project bonus words so the
# lexical baseline scores both memories identically and recency breaks the tie.
TARGET = "海边散步让人放松，那天的浪声一直记得。"
DECOY = "今天整理了房间，把书架擦干净了。"
QUERY = "想去海边玩"


def make_paths(tmp_path) -> CompanionPaths:
    paths = CompanionPaths(tmp_path)
    paths.ensure_runtime_dirs()
    return paths


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


def seeded_home(tmp_path):
    paths = make_paths(tmp_path)
    JsonMemoryStore(paths.memory_store).save([
        memory_row("mem_target", TARGET, created_at="2026-07-01T10:00:00"),
        memory_row("mem_decoy", DECOY, created_at="2026-07-02T10:00:00"),
        memory_row("mem_quarantine", QUERY, eligible=False, created_at="2026-07-03T10:00:00"),
    ])
    return paths


def enabled_config(**overrides):
    defaults = dict(enabled=True, backend="hashing", min_similarity=0.05, semantic_scale=10)
    defaults.update(overrides)
    return SemanticRetrievalConfig(**defaults)


# --- config ---


def test_config_missing_file_means_disabled_defaults(tmp_path):
    paths = make_paths(tmp_path)
    config = load_semantic_retrieval_config(paths)
    assert config.enabled is False
    assert config.backend == "hashing"
    assert config.resolved_model() == "hashing-v1"


def test_config_loads_sentence_transformers_settings(tmp_path):
    paths = make_paths(tmp_path)
    paths.semantic_retrieval_config_file.write_text(json.dumps({
        "enabled": True,
        "backend": "sentence-transformers",
        "min_similarity": 0.3,
        "semantic_scale": 20,
    }))
    config = load_semantic_retrieval_config(paths)
    assert config.enabled is True
    assert config.backend == "sentence-transformers"
    assert config.resolved_model() == "paraphrase-multilingual-MiniLM-L12-v2"
    assert config.min_similarity == 0.3
    assert config.semantic_scale == 20


@pytest.mark.parametrize("payload,fragment", [
    ("{broken", "invalid JSON"),
    (json.dumps([1]), "JSON object"),
    (json.dumps({"backend": "faiss"}), "unsupported semantic backend"),
    (json.dumps({"min_similarity": "high"}), "must be a number"),
    (json.dumps({"min_similarity": 3}), "0..1"),
    (json.dumps({"semantic_scale": 0}), "1..100"),
])
def test_config_rejects_bad_files(tmp_path, payload, fragment):
    paths = make_paths(tmp_path)
    paths.semantic_retrieval_config_file.write_text(payload)
    with pytest.raises(SemanticRetrievalConfigError) as excinfo:
        load_semantic_retrieval_config(paths)
    assert fragment in str(excinfo.value)


# --- hashing backend geometry ---


def test_hashing_backend_is_deterministic_and_normalized():
    backend = HashingEmbeddingBackend()
    first, second = backend.embed([TARGET, TARGET])
    assert first == second
    assert abs(cosine_similarity(first, second) - 1.0) < 1e-9
    norm = sum(value * value for value in first) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_hashing_backend_ranks_related_text_above_unrelated():
    backend = HashingEmbeddingBackend()
    query_vec, target_vec, decoy_vec = backend.embed([QUERY, TARGET, DECOY])
    related = cosine_similarity(query_vec, target_vec)
    unrelated = cosine_similarity(query_vec, decoy_vec)
    assert related > unrelated
    assert related > 0.05


def test_hashing_backend_empty_text_gives_zero_vector():
    backend = HashingEmbeddingBackend()
    vector = backend.embed([""])[0]
    assert all(value == 0.0 for value in vector)


# --- ranking layer ---


def candidates_for(memories):
    return [RetrievedMemory(memory=memory, score=1, reasons=["prompt_eligible_accepted_memory"]) for memory in memories]


def test_ranking_disabled_and_missing_index_statuses(tmp_path):
    paths = seeded_home(tmp_path)
    items = candidates_for([memory_row("mem_target", TARGET)])

    disabled = apply_semantic_ranking(paths, QUERY, items, config=SemanticRetrievalConfig(enabled=False))
    assert disabled.status == "disabled"
    assert disabled.backend == "lexical"

    missing = apply_semantic_ranking(paths, QUERY, items, config=enabled_config())
    assert missing.status == "index_missing_fallback"
    assert items[0].score == 1  # untouched


def test_ranking_applies_threshold_stale_and_missing_counters(tmp_path):
    paths = seeded_home(tmp_path)
    backend = HashingEmbeddingBackend()
    config = enabled_config()
    assert run_m12_semantic_backfill(paths, backend=backend).ok is True

    index = load_semantic_index(paths)
    index["entries"]["mem_decoy"]["content_hash"] = "sha256:stale"
    save_semantic_index(paths, index)

    items = candidates_for([
        memory_row("mem_target", TARGET),
        memory_row("mem_decoy", DECOY),
        memory_row("mem_unknown", "从未索引过的记忆"),
    ])
    outcome = apply_semantic_ranking(paths, QUERY, items, config=config, backend=backend)

    assert outcome.status == "applied"
    assert outcome.backend == "semantic+lexical"
    assert outcome.scored == 1
    assert outcome.stale_in_index == 1
    assert outcome.missing_from_index == 1
    assert items[0].score > 1
    assert any(reason.startswith("semantic_similarity:") for reason in items[0].reasons)
    assert items[1].score == 1
    assert items[2].score == 1


def test_ranking_backend_failure_falls_back(tmp_path):
    paths = seeded_home(tmp_path)
    assert run_m12_semantic_backfill(paths, backend=HashingEmbeddingBackend()).ok is True

    class Exploding:
        name = "hashing"
        model_name = "hashing-v1"

        def embed(self, texts):
            raise RuntimeError("boom")

    items = candidates_for([memory_row("mem_target", TARGET)])
    outcome = apply_semantic_ranking(paths, QUERY, items, config=enabled_config(), backend=Exploding())
    assert outcome.status == "backend_unavailable_fallback"
    assert items[0].score == 1


# --- assembler integration ---


def test_assembler_semantic_gain_over_lexical(tmp_path):
    paths = seeded_home(tmp_path)
    backend = HashingEmbeddingBackend()
    assert run_m12_semantic_backfill(paths, backend=backend).ok is True

    baseline = assemble_dialogue_memory_context(
        paths, QUERY, semantic_config=SemanticRetrievalConfig(enabled=False),
    )
    assert baseline.memories[0]["id"] == "mem_decoy"  # recency tie-break wins lexically
    assert baseline.semantic["status"] == "disabled"

    semantic = assemble_dialogue_memory_context(
        paths, QUERY, semantic_config=enabled_config(), semantic_backend=backend,
    )
    assert semantic.memories[0]["id"] == "mem_target"
    assert semantic.semantic["status"] == "applied"
    assert semantic.semantic["scored"] >= 1
    assert "semantic" in semantic.to_dict()


def test_assembler_policy_filters_beat_any_similarity(tmp_path):
    paths = seeded_home(tmp_path)
    backend = HashingEmbeddingBackend()
    assert run_m12_semantic_backfill(paths, backend=backend).ok is True

    # Force the quarantined memory into the index with an identical-content vector.
    index = load_semantic_index(paths)
    index["entries"]["mem_quarantine"] = {
        "content_hash": content_hash(QUERY),
        "vector": backend.embed([QUERY])[0],
    }
    save_semantic_index(paths, index)

    result = assemble_dialogue_memory_context(
        paths, QUERY, semantic_config=enabled_config(), semantic_backend=backend,
    )
    assert "mem_quarantine" not in {memory.get("id") for memory in result.memories}
    assert {item.get("id") for item in result.filtered} >= {"mem_quarantine"}


def test_assembler_default_behavior_untouched_without_config(tmp_path):
    paths = seeded_home(tmp_path)
    result = assemble_dialogue_memory_context(paths, QUERY)
    assert result.semantic["status"] == "disabled"
    assert result.memories  # lexical retrieval still serves
