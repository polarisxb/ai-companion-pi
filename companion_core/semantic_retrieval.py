"""M12 semantic retrieval: config, embedding backends, index, and ranking.

The JSON memory store stays authoritative. This module derives a rebuildable
vector index from accepted memories and lets the M8 retrieval assembler rank
already-policy-filtered candidates by meaning. Retrieval never writes the
index; the explicit backfill/sync command owns every index mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import CompanionPaths

SCHEMA_VERSION = 1
HASHING_BACKEND = "hashing"
SENTENCE_TRANSFORMERS_BACKEND = "sentence-transformers"
SUPPORTED_BACKENDS = (HASHING_BACKEND, SENTENCE_TRANSFORMERS_BACKEND)
HASHING_MODEL_NAME = "hashing-v1"
HASHING_DIMS = 256
DEFAULT_SENTENCE_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_MIN_SIMILARITY = 0.15
DEFAULT_SEMANTIC_SCALE = 10

_TOKEN_RE = re.compile(r"[a-z0-9_.-]+|[\u4e00-\u9fff]")


class SemanticRetrievalConfigError(RuntimeError):
    """Raised when the semantic retrieval config file is invalid."""


class EmbeddingBackendError(RuntimeError):
    """Raised when an embedding backend cannot embed."""


@dataclass(frozen=True)
class SemanticRetrievalConfig:
    enabled: bool = False
    backend: str = HASHING_BACKEND
    model: str | None = None
    min_similarity: float = DEFAULT_MIN_SIMILARITY
    semantic_scale: int = DEFAULT_SEMANTIC_SCALE

    def resolved_model(self) -> str:
        if self.backend == HASHING_BACKEND:
            return HASHING_MODEL_NAME
        return self.model or DEFAULT_SENTENCE_MODEL


def load_semantic_retrieval_config(paths: CompanionPaths) -> SemanticRetrievalConfig:
    """Load the config; a missing file means disabled with defaults."""

    config_path = paths.semantic_retrieval_config_file
    if not config_path.exists():
        return SemanticRetrievalConfig()
    try:
        payload = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise SemanticRetrievalConfigError(
            f"semantic retrieval config is invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise SemanticRetrievalConfigError("semantic retrieval config must be a JSON object")
    backend = str(payload.get("backend") or HASHING_BACKEND)
    if backend not in SUPPORTED_BACKENDS:
        raise SemanticRetrievalConfigError(f"unsupported semantic backend: {backend}")
    min_similarity = payload.get("min_similarity", DEFAULT_MIN_SIMILARITY)
    try:
        min_similarity = float(min_similarity)
    except (TypeError, ValueError) as exc:
        raise SemanticRetrievalConfigError("min_similarity must be a number") from exc
    if not 0.0 <= min_similarity <= 1.0:
        raise SemanticRetrievalConfigError("min_similarity must stay within 0..1")
    semantic_scale = payload.get("semantic_scale", DEFAULT_SEMANTIC_SCALE)
    try:
        semantic_scale = int(semantic_scale)
    except (TypeError, ValueError) as exc:
        raise SemanticRetrievalConfigError("semantic_scale must be an integer") from exc
    if not 1 <= semantic_scale <= 100:
        raise SemanticRetrievalConfigError("semantic_scale must stay within 1..100")
    model = payload.get("model")
    return SemanticRetrievalConfig(
        enabled=bool(payload.get("enabled", False)),
        backend=backend,
        model=str(model) if model else None,
        min_similarity=min_similarity,
        semantic_scale=semantic_scale,
    )


class HashingEmbeddingBackend:
    """Deterministic dependency-free n-gram hashing vectors.

    Not semantically intelligent, but produces honest cosine geometry over
    shared words and CJK characters. Used by tests and as a degraded backend.
    """

    name = HASHING_BACKEND

    def __init__(self, dims: int = HASHING_DIMS):
        self.dims = dims
        self.model_name = HASHING_MODEL_NAME

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        tokens = _TOKEN_RE.findall(str(text or "").lower())
        grams = list(tokens)
        grams.extend(a + b for a, b in zip(tokens, tokens[1:]))
        for gram in grams:
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dims
            vector[slot] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector


class SentenceTransformerEmbeddingBackend:
    """Real semantic vectors via a locally installed sentence-transformers model."""

    name = SENTENCE_TRANSFORMERS_BACKEND

    def __init__(self, model_name: str = DEFAULT_SENTENCE_MODEL):
        self.model_name = model_name
        self._model = None
        self.dims = 0

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingBackendError(
                    "sentence-transformers is not installed on this machine"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        vectors = model.encode(list(texts), show_progress_bar=False, normalize_embeddings=True)
        result = [[float(value) for value in vector] for vector in vectors]
        if result:
            self.dims = len(result[0])
        return result


def create_embedding_backend(config: SemanticRetrievalConfig):
    if config.backend == HASHING_BACKEND:
        return HashingEmbeddingBackend()
    return SentenceTransformerEmbeddingBackend(config.resolved_model())


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def load_semantic_index(paths: CompanionPaths) -> dict | None:
    try:
        payload = json.loads(paths.semantic_index_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        return None
    return payload


def save_semantic_index(paths: CompanionPaths, index: dict) -> Path:
    index_path = paths.semantic_index_file
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = index_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(index, ensure_ascii=False, sort_keys=True))
    tmp_path.replace(index_path)
    return index_path


def empty_semantic_index(backend) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "backend": backend.name,
        "model": backend.model_name,
        "dims": getattr(backend, "dims", 0),
        "updated_at": datetime.now().isoformat(),
        "entries": {},
    }


def index_matches_config(index: dict | None, config: SemanticRetrievalConfig) -> bool:
    if not index:
        return False
    return (
        index.get("backend") == config.backend
        and index.get("model") == config.resolved_model()
    )


def summarize_index_coverage(
    index: dict | None,
    config: SemanticRetrievalConfig | None,
    eligible_memories: list[dict],
) -> dict:
    """Read-only coverage/staleness summary of the derived index."""

    summary = {
        "exists": index is not None,
        "matches_config": bool(config and index_matches_config(index, config)),
        "entries": len((index or {}).get("entries") or {}),
        "eligible_memories": len(eligible_memories),
        "covered": 0,
        "stale": 0,
        "missing": 0,
        "coverage_ratio": 0.0,
        "ok": False,
        "message": "",
    }
    entries = (index or {}).get("entries") or {}
    for memory in eligible_memories:
        memory_id = str(memory.get("id") or "")
        entry = entries.get(memory_id)
        if not isinstance(entry, dict):
            summary["missing"] += 1
        elif entry.get("content_hash") != content_hash(memory.get("content") or ""):
            summary["stale"] += 1
        else:
            summary["covered"] += 1
    if eligible_memories:
        summary["coverage_ratio"] = round(summary["covered"] / len(eligible_memories), 4)
    summary["ok"] = bool(
        summary["exists"]
        and summary["matches_config"]
        and summary["missing"] == 0
        and summary["stale"] == 0
        and summary["covered"] == len(eligible_memories)
    )
    summary["message"] = (
        f"index exists={summary['exists']} matches_config={summary['matches_config']} "
        f"covered={summary['covered']}/{len(eligible_memories)} "
        f"stale={summary['stale']} missing={summary['missing']}"
    )
    return summary


@dataclass
class SemanticRankingOutcome:
    backend: str
    status: str
    model: str | None = None
    scored: int = 0
    skipped_below_threshold: int = 0
    missing_from_index: int = 0
    stale_in_index: int = 0

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "status": self.status,
            "model": self.model,
            "scored": self.scored,
            "skipped_below_threshold": self.skipped_below_threshold,
            "missing_from_index": self.missing_from_index,
            "stale_in_index": self.stale_in_index,
        }


def apply_semantic_ranking(
    paths: CompanionPaths,
    query: str,
    candidates,
    *,
    config: SemanticRetrievalConfig | None = None,
    backend=None,
    index: dict | None = None,
) -> SemanticRankingOutcome:
    """Boost the scores of policy-approved candidates by semantic similarity.

    Mutates ``candidates`` (RetrievedMemory items) in place. Every failure
    mode degrades to lexical-only scoring with an explicit status; nothing
    raises into the dialogue path.
    """

    try:
        config = config or load_semantic_retrieval_config(paths)
    except SemanticRetrievalConfigError:
        return SemanticRankingOutcome(backend="lexical", status="config_invalid_fallback")
    if not config.enabled:
        return SemanticRankingOutcome(backend="lexical", status="disabled")
    if not str(query or "").strip() or not candidates:
        return SemanticRankingOutcome(
            backend="lexical",
            status="no_query_or_candidates",
            model=config.resolved_model(),
        )

    index = index if index is not None else load_semantic_index(paths)
    if not index_matches_config(index, config):
        return SemanticRankingOutcome(
            backend="lexical",
            status="index_missing_fallback",
            model=config.resolved_model(),
        )

    try:
        backend = backend or create_embedding_backend(config)
        query_vector = backend.embed([query])[0]
    except Exception:  # noqa: BLE001 - any backend failure must fall back, never raise.
        return SemanticRankingOutcome(
            backend="lexical",
            status="backend_unavailable_fallback",
            model=config.resolved_model(),
        )

    outcome = SemanticRankingOutcome(
        backend="semantic+lexical",
        status="applied",
        model=config.resolved_model(),
    )
    entries = index.get("entries") or {}
    for candidate in candidates:
        memory = candidate.memory
        memory_id = str(memory.get("id") or "")
        entry = entries.get(memory_id)
        if not isinstance(entry, dict) or not isinstance(entry.get("vector"), list):
            outcome.missing_from_index += 1
            continue
        if entry.get("content_hash") != content_hash(memory.get("content") or ""):
            outcome.stale_in_index += 1
            continue
        similarity = cosine_similarity(query_vector, entry["vector"])
        if similarity < config.min_similarity:
            outcome.skipped_below_threshold += 1
            continue
        candidate.score += int(round(similarity * config.semantic_scale))
        candidate.reasons.append(f"semantic_similarity:{similarity:.2f}")
        outcome.scored += 1
    return outcome
