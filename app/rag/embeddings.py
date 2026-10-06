"""Embedding backends behind one interface, with a persistent cache.

* sentence-transformers (default model: all-MiniLM-L6-v2, configurable via EMBEDDING_MODEL)
* hashing: a dependency-free, deterministic lexical embedder. It exists so the whole system (tests, CI,
  offline demos) runs with no model download. It has NO semantic generalisation; retrieval quality with it
  comes from BM25 + metadata. /health reports which backend is active so this is never hidden.
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
import zlib
from typing import Optional, Protocol

import numpy as np

from app.core.config import get_settings
from app.core.errors import EmbeddingError
from app.core.logging import get_logger
from app.database.repositories import EmbeddingCacheRepo
from app.rag.text import tokenize

log = get_logger(__name__)


class Embedder(Protocol):
    name: str
    dim: int
    degraded: bool

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Signed feature hashing of stemmed unigrams and bigrams, sublinear tf, L2-normalised."""

    degraded = True

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        feats: dict[str, float] = {}
        for t in toks:
            feats[t] = feats.get(t, 0.0) + 1.0
        for a, b in zip(toks, toks[1:]):
            key = f"{a}_{b}"
            feats[key] = feats.get(key, 0.0) + 0.5
        for key, tf in feats.items():
            h = zlib.crc32(key.encode("utf-8"))
            idx = h % self.dim
            sign = 1.0 if (zlib.crc32(b"s" + key.encode("utf-8")) & 1) else -1.0
            v[idx] += sign * (1.0 + math.log(tf))
        norm = float(np.linalg.norm(v))
        return v / norm if norm > 0 else v

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)


class SentenceTransformerEmbedder:
    degraded = False

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError("sentence-transformers is not installed") from exc
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:
            raise EmbeddingError(f"could not load embedding model '{model_name}': {exc}") from exc
        self.name = model_name
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            vecs = self._model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        except Exception as exc:
            raise EmbeddingError(f"embedding failed: {exc}") from exc
        return np.asarray(vecs, dtype=np.float32)


class CachedEmbedder:
    """Wraps an embedder with a SQLite cache keyed by (model, sha256(text))."""

    def __init__(self, inner: Embedder, enabled: bool = True):
        self.inner = inner
        self.enabled = enabled
        self.repo = EmbeddingCacheRepo()
        self.name = inner.name
        self.dim = inner.dim
        self.degraded = inner.degraded
        self.cache_hits = 0
        self.cache_misses = 0

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        if not self.enabled:
            return self.inner.embed(texts)
        keys = [self._key(t) for t in texts]
        try:
            cached = self.repo.get_many(self.name, list(set(keys)))
        except Exception:
            cached = {}
        out: list[Optional[np.ndarray]] = [None] * len(texts)
        missing_idx: list[int] = []
        for i, k in enumerate(keys):
            blob = cached.get(k)
            if blob is not None and len(blob) == self.dim * 4:
                out[i] = np.frombuffer(blob, dtype=np.float32)
                self.cache_hits += 1
            else:
                missing_idx.append(i)
        if missing_idx:
            self.cache_misses += len(missing_idx)
            fresh = self.inner.embed([texts[i] for i in missing_idx])
            new_items: dict[str, bytes] = {}
            for j, i in enumerate(missing_idx):
                out[i] = fresh[j]
                new_items[keys[i]] = fresh[j].astype(np.float32).tobytes()
            try:
                self.repo.put_many(self.name, new_items)
            except Exception:
                pass
        return np.vstack(out)  # type: ignore[arg-type]


_lock = threading.Lock()
_embedder: Optional[CachedEmbedder] = None


def get_embedder(force_reload: bool = False) -> CachedEmbedder:
    """Build the configured embedder once. With EMBEDDING_BACKEND=auto, fall back to hashing if the model cannot load."""
    global _embedder
    with _lock:
        if _embedder is not None and not force_reload:
            return _embedder
        s = get_settings()
        inner: Embedder
        if s.embedding_backend == "hashing":
            inner = HashingEmbedder()
        else:
            try:
                inner = SentenceTransformerEmbedder(s.embedding_model)
            except EmbeddingError as exc:
                if s.embedding_backend == "sentence-transformers":
                    raise
                log.warning("embedding_fallback", extra={"reason": str(exc)[:300], "using": "hashing"})
                inner = HashingEmbedder()
        _embedder = CachedEmbedder(inner, enabled=s.embedding_cache_enabled)
        return _embedder


def reset_embedder() -> None:
    global _embedder
    with _lock:
        _embedder = None
