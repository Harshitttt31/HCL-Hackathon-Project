"""Document ingestion: validate -> parse (OCR if scanned) -> structure -> chunk -> embed -> ChromaDB + SQLite -> register.

A document ingested while the system is running is searchable immediately: the BM25 index is rebuilt in memory
and vectors are written to the persistent collection. There is no restart and no code change (guide R11).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import EmbeddingError, ParseError, ValidationFailed, VectorStoreError
from app.core.logging import get_logger
from app.database.models import ChunkRow, RuleRow
from app.database.repositories import ChunkRepo, RuleRepo
from app.database.sqlite import get_conn
from app.rag.chunker import get_chunker
from app.rag.claims import Claim, claims_to_rules, get_lexicon, grade_band_rules
from app.rag.parser import parse_document
from app.rag.retriever import HybridRetriever
from app.sources.models import SourceMetadata
from app.sources.registry import SourceRegister, get_register

log = get_logger(__name__)


class IngestResult(BaseModel):
    doc_id: str
    chunks_indexed: int
    status: str  # success | unchanged | partial
    rules_registered: int = 0
    ocr_used: bool = False
    pages: int = 0
    warnings: list[str] = Field(default_factory=list)
    injection_chunks_flagged: int = 0
    replaced_previous_version: bool = False
    elapsed_ms: int = 0


class IngestionService:
    def __init__(self, retriever: HybridRetriever, register: Optional[SourceRegister] = None,
                 chunks: Optional[ChunkRepo] = None, rules: Optional[RuleRepo] = None):
        self.retriever = retriever
        self.register = register or get_register()
        self.chunks = chunks or ChunkRepo()
        self.rules = rules or RuleRepo()

    def ingest(self, data: bytes, filename: str, meta: SourceMetadata, strategy: Optional[str] = None) -> IngestResult:
        s = get_settings()
        t0 = time.perf_counter()
        if len(data) > s.max_upload_mb * 1024 * 1024:
            raise ValidationFailed(f"file is larger than the {s.max_upload_mb} MB limit")
        content_hash = hashlib.sha256(data).hexdigest()[:32]
        warnings: list[str] = list(meta.warnings())

        existing = self.register.get(meta.doc_id)
        if existing and existing.content_hash == content_hash and existing.chunks_indexed > 0 and self._same_metadata(existing, meta):
            return IngestResult(doc_id=meta.doc_id, chunks_indexed=existing.chunks_indexed, status="unchanged",
                                warnings=["identical document and metadata already ingested; nothing changed"],
                                ocr_used=existing.ocr_used, elapsed_ms=int((time.perf_counter() - t0) * 1000))

        parsed = parse_document(data, filename)  # raises ParseError (corrupt, empty, unsupported)
        warnings.extend(parsed.warnings)
        chunker = get_chunker(strategy)
        chunks = chunker.chunk(meta.doc_id, meta.title, parsed)
        if not chunks:
            raise ParseError("the document produced no text chunks")
        flagged = sum(1 for c in chunks if c.injection_suspected)
        if flagged:
            warnings.append(f"{flagged} chunk(s) contain instruction-like text; they are stored but never used as evidence")

        # ---- rules: claims and grade bands extracted from non-suspicious chunks --------------------------------------
        rule_rows: list[RuleRow] = []
        if s.auto_extract_rules:
            lex = get_lexicon()
            claims: list[Claim] = []
            for ch in chunks:
                if ch.injection_suspected:
                    continue
                claims.extend(lex.extract(ch))
                if ch.kind == "table":
                    bands = lex.extract_grade_bands(ch)
                    if bands:
                        rule_rows.extend(grade_band_rules(bands, ch, meta))
            curated = {(r.source_doc_id, r.parameter, r.source_section) for r in self.rules.for_doc(meta.doc_id) if r.origin == "curated"}
            rule_rows.extend(claims_to_rules(claims, meta, curated))
        if meta.supersedes:
            from app.sources.models import parse_supersedes
            for ref in parse_supersedes(meta.supersedes):
                if not self.register.exists(ref.doc_id):
                    warnings.append(f"supersedes '{ref.doc_id}', which is not in the Source Register yet; it will apply once that document is registered")

        # ---- vectors first (so a failure here cannot leave SQLite pointing at missing vectors) --------------------------
        status = "success"
        vectors_written = False
        try:
            embedder = self.retriever.embedder
            vectors = embedder.embed([c.embed_text for c in chunks])
            store = self.retriever.vector_store()
            if store is None:
                raise VectorStoreError(self.retriever._store_error or "vector store unavailable")
            store.delete_doc(meta.doc_id)
            store.upsert(chunks, vectors, {"authority_level": meta.authority_level, "effective_from": meta.effective_from,
                                           "effective_to": meta.effective_to})
            vectors_written = True
        except (EmbeddingError, VectorStoreError) as exc:
            status = "partial"
            warnings.append(f"vector index not updated ({str(exc)[:160]}); keyword (BM25) retrieval still works for this document")
            log.error("ingest_vector_failure", extra={"doc_id": meta.doc_id, "error": str(exc)[:300]})

        # ---- SQLite: one transaction ---------------------------------------------------------------------------------------
        try:
            with get_conn() as conn:
                self.chunks.delete_by_doc(meta.doc_id, conn)
                self.rules.delete_extracted_for_doc(meta.doc_id, conn)
                self.register.upsert(meta, content_hash=content_hash, filename=filename, chunks_indexed=len(chunks),
                                     ocr_used=parsed.ocr_used, parse_warnings="; ".join(warnings)[:1000] or None, conn=conn)
                self.chunks.insert_many(chunks, conn)
                for r in rule_rows:
                    self.rules.upsert(r, conn)
                conn.execute("INSERT INTO ingestion_log(ts, doc_id, status, chunks, details) VALUES (datetime('now'),?,?,?,?)",
                             (meta.doc_id, status, len(chunks), "; ".join(warnings)[:500]))
        except Exception:
            if vectors_written:
                try:
                    self.retriever.vector_store().delete_ids([c.chunk_id for c in chunks])  # type: ignore[union-attr]
                except Exception:
                    pass
            raise
        self._save_original(meta.doc_id, filename, data)
        self.retriever.reload()
        log.info("document_ingested", extra={"doc_id": meta.doc_id, "chunks": len(chunks), "rules": len(rule_rows),
                                             "ocr": parsed.ocr_used, "status": status})
        return IngestResult(doc_id=meta.doc_id, chunks_indexed=len(chunks), status=status, rules_registered=len(rule_rows),
                            ocr_used=parsed.ocr_used, pages=parsed.page_count, warnings=warnings, injection_chunks_flagged=flagged,
                            replaced_previous_version=existing is not None, elapsed_ms=int((time.perf_counter() - t0) * 1000))

    @staticmethod
    def _same_metadata(existing, meta: SourceMetadata) -> bool:
        keys = ["title", "issuer", "authority_level", "doc_type", "version", "effective_from", "effective_to", "supersedes",
                "scope_programmes", "scope_batches", "synthetic"]
        return all((getattr(existing, k) or "") == (getattr(meta, k) or "") for k in keys)

    @staticmethod
    def _save_original(doc_id: str, filename: str, data: bytes) -> None:
        try:
            ext = Path(filename).suffix.lower()[:8] or ".bin"
            folder = Path(get_settings().documents_dir)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{doc_id}{ext}").write_bytes(data)
        except OSError as exc:  # persistence of the original is a convenience; ingestion already succeeded
            log.warning("original_not_saved", extra={"doc_id": doc_id, "error": str(exc)[:200]})
