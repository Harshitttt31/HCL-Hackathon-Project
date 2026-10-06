"""ChromaDB wrapper (persisted to disk; never re-ingested on restart).

Vectors only. Chunk text, authority, dates and scope live in SQLite / the Source Register and are joined back
at retrieval time, so the vector store is NOT treated as the register (spec section 18).
One collection per embedding model+dimension so switching models never mixes incompatible vectors.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Optional

import numpy as np

from app.core.config import get_settings
from app.core.errors import VectorStoreError
from app.core.logging import get_logger
from app.database.models import ChunkRow

log = get_logger(__name__)


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()[:40]


def _to_int_date(value: Optional[str], default: int) -> int:
    if not value:
        return default
    try:
        return int(value.replace("-", ""))
    except ValueError:
        return default


class VectorStore:
    def __init__(self, embedder_name: str, dim: int):
        s = get_settings()
        self._lock = threading.Lock()
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            cfg = ChromaSettings(anonymized_telemetry=False, allow_reset=False)
            if s.chroma_host:
                self._client = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port, settings=cfg)
            else:
                self._client = chromadb.PersistentClient(path=s.chroma_path, settings=cfg)
            self.collection_name = f"{s.chroma_collection_prefix}__{_slug(embedder_name)}__{dim}"
            self._col = self._client.get_or_create_collection(
                name=self.collection_name, metadata={"hnsw:space": "cosine"}, embedding_function=None)
        except Exception as exc:
            raise VectorStoreError(f"could not open ChromaDB: {exc}") from exc

    # ---- writes -------------------------------------------------------------------------------------
    def upsert(self, chunks: list[ChunkRow], vectors: np.ndarray, doc_meta: dict[str, Any]) -> None:
        if not chunks:
            return
        metas = []
        for ch in chunks:
            metas.append({
                "doc_id": ch.doc_id, "section": ch.section_number, "page": ch.page or 0, "kind": ch.kind,
                "authority_level": int(doc_meta.get("authority_level", 0)),
                "effective_from_i": _to_int_date(doc_meta.get("effective_from"), 0),
                "effective_to_i": _to_int_date(doc_meta.get("effective_to"), 99991231),
                "injection": int(ch.injection_suspected),
            })
        try:
            with self._lock:
                self._col.upsert(ids=[c.chunk_id for c in chunks], embeddings=vectors.astype(np.float32).tolist(),
                                 metadatas=metas, documents=[c.text for c in chunks])
        except Exception as exc:
            raise VectorStoreError(f"vector upsert failed: {exc}") from exc

    def delete_doc(self, doc_id: str) -> None:
        try:
            with self._lock:
                self._col.delete(where={"doc_id": doc_id})
        except Exception as exc:
            raise VectorStoreError(f"vector delete failed: {exc}") from exc

    def delete_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            with self._lock:
                self._col.delete(ids=ids)
        except Exception as exc:  # best-effort cleanup
            log.warning("vector_cleanup_failed", extra={"error": str(exc)[:200]})

    # ---- reads --------------------------------------------------------------------------------------------
    def query(self, vector: np.ndarray, n: int, doc_ids: Optional[list[str]] = None) -> list[tuple[str, float]]:
        """Return [(chunk_id, cosine_similarity)] best first."""
        total = self.count()
        if total == 0:
            return []
        where = {"doc_id": {"$in": doc_ids}} if doc_ids else None
        try:
            res = self._col.query(query_embeddings=[vector.astype(np.float32).tolist()], n_results=min(n, total), where=where,
                                  include=["distances"])
        except Exception as exc:
            raise VectorStoreError(f"vector query failed: {exc}") from exc
        ids = res["ids"][0] if res.get("ids") else []
        dists = res["distances"][0] if res.get("distances") else []
        return [(i, 1.0 - float(d)) for i, d in zip(ids, dists)]

    def count(self) -> int:
        try:
            return int(self._col.count())
        except Exception as exc:
            raise VectorStoreError(f"vector count failed: {exc}") from exc

    def healthy(self) -> bool:
        try:
            self._client.heartbeat()
            return True
        except Exception:
            return False
