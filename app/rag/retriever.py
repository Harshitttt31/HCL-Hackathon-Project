"""Hybrid retrieval: dense (ChromaDB) + BM25 over the same chunks, fused with Reciprocal Rank Fusion, then reranked.

Retrieval returns CANDIDATES with similarity scores. It never decides what is authoritative: authority, effective
dates, scope and supersession are applied afterwards by the precedence engine using the Source Register.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional

import numpy as np
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from app.core.config import get_settings
from app.core.errors import EmbeddingError, VectorStoreError
from app.core.logging import get_logger
from app.database.models import ChunkRow
from app.database.repositories import ChunkRepo
from app.rag.embeddings import CachedEmbedder, get_embedder
from app.rag.reranker import Reranker, get_reranker
from app.rag.text import GENERIC_HEADING_TERMS, SCOPE_TERMS, IdfTable, expand_terms, tokenize
from app.rag.vectorstore import VectorStore
from app.sources.models import DocInfo
from app.sources.registry import SourceRegister, get_register

log = get_logger(__name__)


class EvidenceChunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str = ""
    section: str = ""
    section_title: str = ""
    section_path: list[str] = Field(default_factory=list)
    page: Optional[int] = None
    text: str
    kind: str = "paragraph"
    references: list[str] = Field(default_factory=list)
    score: float = 0.0  # final retrieval score in [0, 1]
    dense_score: float = 0.0
    bm25_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float = 0.0
    authority_level: int = 5
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    version: str = ""
    issuer: str = ""
    doc_type: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    injection_suspected: bool = False
    confidence: float = 1.0
    expanded_from: Optional[str] = None  # set when pulled in through a cross-reference or supersession expansion


class RetrievalStats(BaseModel):
    dense_ms: int = 0
    bm25_ms: int = 0
    rerank_ms: int = 0
    total_ms: int = 0
    dense_candidates: int = 0
    bm25_candidates: int = 0
    reranker: str = ""
    embedder: str = ""
    degraded_embeddings: bool = False
    errors: list[str] = Field(default_factory=list)


class HybridRetriever:
    def __init__(self, embedder: Optional[CachedEmbedder] = None, store: Optional[VectorStore] = None,
                 reranker: Optional[Reranker] = None, chunks: Optional[ChunkRepo] = None,
                 register: Optional[SourceRegister] = None):
        self.embedder = embedder or get_embedder()
        self.store = store  # opened lazily so a Chroma outage degrades to BM25 instead of failing startup
        self._store_error: Optional[str] = None
        self.reranker = reranker or get_reranker()
        self.chunks = chunks or ChunkRepo()
        self.register = register or get_register()
        self._lock = threading.RLock()
        self._ids: list[str] = []
        self._by_id: dict[str, ChunkRow] = {}
        self._bm25: Optional[BM25Okapi] = None
        self._idf = IdfTable([])
        self._subject_terms: set[str] = set()
        self.reload()

    # ---- index management ------------------------------------------------------------------------------
    def vector_store(self) -> Optional[VectorStore]:
        if self.store is None and self._store_error is None:
            try:
                self.store = VectorStore(self.embedder.name, self.embedder.dim)
            except VectorStoreError as exc:
                self._store_error = str(exc)
                log.error("vector_store_unavailable", extra={"error": str(exc)[:300]})
        return self.store

    def reload(self) -> int:
        """Rebuild the in-memory BM25 index from SQLite (called on startup and after every ingestion)."""
        rows = self.chunks.all()
        tokens = [tokenize(r.embed_text) for r in rows]
        with self._lock:
            self._ids = [r.chunk_id for r in rows]
            self._by_id = {r.chunk_id: r for r in rows}
            self._bm25 = BM25Okapi(tokens) if tokens else None
            self._idf = IdfTable(tokens)
            titles: set[str] = set()
            for r in rows:
                titles.update(tokenize(r.section_title or ""))
            self._subject_terms = {t for t in titles if not t.isdigit()} - GENERIC_HEADING_TERMS
        return len(rows)

    @property
    def subject_terms(self) -> set[str]:
        """Words that name what a section is about (taken from section headings). A question about one of these subjects
        must not be answered from text that never mentions it."""
        return self._subject_terms

    @property
    def idf(self) -> IdfTable:
        return self._idf

    def size(self) -> int:
        return len(self._ids)

    # ---- retrieval ------------------------------------------------------------------------------------------
    def retrieve(self, query: str, top_k: Optional[int] = None, doc_ids: Optional[list[str]] = None, **kw: Any) -> list[EvidenceChunk]:
        return self.retrieve_with_stats(query, top_k=top_k, doc_ids=doc_ids, **kw)[0]

    def retrieve_with_stats(self, query: str, top_k: Optional[int] = None, doc_ids: Optional[list[str]] = None,
                            use_dense: Optional[bool] = None, use_bm25: Optional[bool] = None, use_rerank: bool = True) -> tuple[list[EvidenceChunk], RetrievalStats]:
        s = get_settings()
        top_k = top_k or s.top_k
        pool = s.candidate_pool
        use_dense = s.retrieval_dense if use_dense is None else use_dense
        use_bm25 = s.retrieval_bm25 if use_bm25 is None else use_bm25
        t0 = time.perf_counter()
        stats = RetrievalStats(reranker=self.reranker.name if use_rerank else "off", embedder=self.embedder.name,
                               degraded_embeddings=bool(self.embedder.degraded))
        with self._lock:
            ids, by_id, bm25 = self._ids, self._by_id, self._bm25
        if not ids:
            stats.total_ms = int((time.perf_counter() - t0) * 1000)
            return [], stats
        allowed = set(doc_ids) if doc_ids else None

        # ---- dense --------------------------------------------------------------------------------------------
        dense: list[tuple[str, float]] = []
        if use_dense:
            t = time.perf_counter()
            try:
                store = self.vector_store()
                if store is None:
                    raise VectorStoreError(self._store_error or "vector store unavailable")
                qvec = self.embedder.embed([query])[0]
                dense = store.query(qvec, pool, list(allowed) if allowed else None)
            except (EmbeddingError, VectorStoreError) as exc:
                stats.errors.append(f"dense retrieval unavailable: {str(exc)[:160]}")
                log.warning("dense_retrieval_failed", extra={"error": str(exc)[:200]})
            stats.dense_ms = int((time.perf_counter() - t) * 1000)
            stats.dense_candidates = len(dense)
        dense = [(cid, sim) for cid, sim in dense if cid in by_id and (allowed is None or by_id[cid].doc_id in allowed)]

        # ---- BM25 -----------------------------------------------------------------------------------------------
        bm: list[tuple[str, float]] = []
        if use_bm25 and bm25 is not None:
            t = time.perf_counter()
            q_tokens = expand_terms(tokenize(query))
            if q_tokens:
                scores = bm25.get_scores(q_tokens)
                order = np.argsort(-scores)[: pool * 2]
                for i in order:
                    if scores[i] <= 0:
                        break
                    cid = ids[i]
                    if allowed is None or by_id[cid].doc_id in allowed:
                        bm.append((cid, float(scores[i])))
                    if len(bm) >= pool:
                        break
            stats.bm25_ms = int((time.perf_counter() - t) * 1000)
            stats.bm25_candidates = len(bm)

        # ---- fusion (RRF) -----------------------------------------------------------------------------------------
        k = s.rrf_k
        fused: dict[str, float] = {}
        max_bm = max((sc for _, sc in bm), default=1.0) or 1.0
        dense_map, bm_map = dict(dense), {cid: sc / max_bm for cid, sc in bm}
        wd = s.dense_weight if use_dense else 0.0
        wb = s.bm25_weight if use_bm25 else 0.0
        for rank, (cid, _) in enumerate(dense, 1):
            fused[cid] = fused.get(cid, 0.0) + wd / (k + rank)
        for rank, (cid, _) in enumerate(bm, 1):
            fused[cid] = fused.get(cid, 0.0) + wb / (k + rank)
        if not fused:
            stats.total_ms = int((time.perf_counter() - t0) * 1000)
            return [], stats
        ceiling = (wd + wb) / (k + 1) or 1.0
        ranked = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
        rerank_pool = ranked[: max(top_k * 2, 12)]

        # ---- join metadata from the register (the system of record), then rerank -----------------------------------
        infos = self.register.infos()
        out: list[EvidenceChunk] = []
        for cid, f in rerank_pool:
            row = by_id[cid]
            out.append(self._to_evidence(row, infos.get(row.doc_id), fused=f / ceiling, dense=dense_map.get(cid, 0.0), bm=bm_map.get(cid, 0.0)))
        if use_rerank and out:
            t = time.perf_counter()
            try:
                texts = [c.text if not c.section_path else " ".join(c.section_path[1:]) + " " + c.text for c in out]
                raw = self.reranker.score(query, texts)
                lo, hi = (min(raw), max(raw)) if raw else (0.0, 1.0)
                for c, r in zip(out, raw):
                    c.rerank_score = (r - lo) / (hi - lo) if hi > lo else (1.0 if r > 0 else 0.0)
                    c.score = 0.5 * c.fused_score + 0.5 * c.rerank_score
            except Exception as exc:  # reranker failure must not lose retrieval
                stats.errors.append(f"reranker failed: {str(exc)[:120]}")
                for c in out:
                    c.score = c.fused_score
            stats.rerank_ms = int((time.perf_counter() - t) * 1000)
        else:
            for c in out:
                c.score = c.fused_score
        out.sort(key=lambda c: (-c.score, c.chunk_id))
        stats.total_ms = int((time.perf_counter() - t0) * 1000)
        return out[:top_k], stats

    # ---- helpers ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _to_evidence(row: ChunkRow, info: Optional[DocInfo], fused: float = 0.0, dense: float = 0.0, bm: float = 0.0,
                     expanded_from: Optional[str] = None) -> EvidenceChunk:
        return EvidenceChunk(
            chunk_id=row.chunk_id, doc_id=row.doc_id, title=info.title if info else "", section=row.section_number,
            section_title=row.section_title, section_path=row.section_path, page=row.page, text=row.text, kind=row.kind,
            references=row.references, score=fused, dense_score=dense, bm25_score=bm, fused_score=fused,
            authority_level=info.authority_level if info else 5,
            effective_from=info.effective_from.isoformat() if info else None,
            effective_to=info.effective_to.isoformat() if info and info.effective_to else None,
            version=info.version if info else "", issuer=info.issuer if info else "", doc_type=info.doc_type if info else "",
            scope_programmes=info.scope_programmes if info else "ALL", scope_batches=info.scope_batches if info else "ALL",
            injection_suspected=row.injection_suspected, confidence=row.confidence, expanded_from=expanded_from)

    def expand_references(self, hits: list[EvidenceChunk], limit: int = 4) -> list[EvidenceChunk]:
        """Pull in the clauses a hit cross-references ("subject to clause 7.3") from the SAME document."""
        if not get_settings().expand_cross_references:
            return []
        have = {h.chunk_id for h in hits}
        infos = self.register.infos()
        extra: list[EvidenceChunk] = []
        for h in hits:
            for ref in h.references:
                if ref == h.section:
                    continue
                for row in self.chunks.find_by_section(h.doc_id, ref)[:2]:
                    if row.chunk_id not in have and not row.injection_suspected:
                        have.add(row.chunk_id)
                        extra.append(self._to_evidence(row, infos.get(row.doc_id), expanded_from=h.chunk_id))
                        if len(extra) >= limit:
                            return extra
        return extra

    def coverage(self, query: str, chunks: list[EvidenceChunk]) -> tuple[float, list[str]]:
        """IDF-weighted share of the question's terms supported by the best of the given chunks."""
        q = [t for t in tokenize(query) if t not in SCOPE_TERMS] or tokenize(query)
        if not q or not chunks:
            return 0.0, q
        best, best_missing = 0.0, q
        union_tokens: set[str] = set()
        for c in chunks[:3]:
            toks = set(tokenize(" ".join([c.title, " ".join(c.section_path), c.text])))
            union_tokens |= toks
            cov, missing = self._idf.coverage(q, toks)
            if cov > best:
                best, best_missing = cov, missing
        # the answer may legitimately be spread over the top chunks: use the union as an upper bound blend
        cov_union, miss_union = self._idf.coverage(q, union_tokens)
        blended = max(best, 0.5 * best + 0.5 * cov_union)
        return blended, (best_missing if blended == best else miss_union)

    def stats(self) -> dict[str, Any]:
        store = self.store
        try:
            vectors = store.count() if store else 0
        except Exception:
            vectors = -1
        return {"chunks_indexed": self.size(), "vectors": vectors, "embedder": self.embedder.name,
                "embeddings_degraded": bool(self.embedder.degraded), "reranker": self.reranker.name,
                "vector_store_error": self._store_error}
