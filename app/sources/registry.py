"""Source Register (Annex B): the system of record for authority, scope, versions and supersession.

The vector store is NOT the register. Retrieval joins vector hits back to this table.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from app.core.clock import now_iso
from app.database.models import SourceDocRow
from app.database.sqlite import get_conn
from app.sources.models import DocInfo, SourceMetadata, parse_supersedes
from app.sources.temporal import to_date

REGISTER_COLUMNS = [
    "doc_id", "title", "issuer", "authority_level", "doc_type", "version", "effective_from", "effective_to",
    "supersedes", "scope_programmes", "scope_batches", "provenance", "retrieved_on", "synthetic",
]

_COLS = (
    "doc_id, title, issuer, authority_level, doc_type, version, effective_from, effective_to, supersedes, "
    "scope_programmes, scope_batches, provenance, retrieved_on, synthetic, content_hash, filename, "
    "chunks_indexed, ocr_used, parse_warnings, ingested_at"
)


def _row(r: sqlite3.Row) -> SourceDocRow:
    d = dict(r)
    d["effective_to"] = d["effective_to"] or None
    d["ocr_used"] = bool(d["ocr_used"])
    return SourceDocRow(**d)


def to_info(row: SourceDocRow) -> DocInfo:
    return DocInfo(
        doc_id=row.doc_id,
        title=row.title,
        issuer=row.issuer,
        authority_level=row.authority_level,
        doc_type=row.doc_type,
        version=row.version,
        effective_from=to_date(row.effective_from),  # type: ignore[arg-type]
        effective_to=to_date(row.effective_to),
        supersedes=parse_supersedes(row.supersedes),
        scope_programmes=row.scope_programmes,
        scope_batches=row.scope_batches,
        provenance=row.provenance,
        synthetic=row.synthetic == "Y",
    )


class SourceRegister:
    def upsert(
        self,
        meta: SourceMetadata,
        *,
        content_hash: Optional[str] = None,
        filename: Optional[str] = None,
        chunks_indexed: int = 0,
        ocr_used: bool = False,
        parse_warnings: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        sql = (
            f"INSERT INTO source_register({_COLS}) VALUES ({','.join('?' * 20)}) "
            "ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title, issuer=excluded.issuer, "
            "authority_level=excluded.authority_level, doc_type=excluded.doc_type, version=excluded.version, "
            "effective_from=excluded.effective_from, effective_to=excluded.effective_to, supersedes=excluded.supersedes, "
            "scope_programmes=excluded.scope_programmes, scope_batches=excluded.scope_batches, "
            "provenance=excluded.provenance, retrieved_on=excluded.retrieved_on, synthetic=excluded.synthetic, "
            "content_hash=excluded.content_hash, filename=excluded.filename, chunks_indexed=excluded.chunks_indexed, "
            "ocr_used=excluded.ocr_used, parse_warnings=excluded.parse_warnings, ingested_at=excluded.ingested_at"
        )
        args = (
            meta.doc_id, meta.title, meta.issuer, meta.authority_level, meta.doc_type, meta.version, meta.effective_from,
            meta.effective_to, meta.supersedes, meta.scope_programmes, meta.scope_batches, meta.provenance,
            meta.retrieved_on, meta.synthetic, content_hash, filename, chunks_indexed, int(ocr_used), parse_warnings,
            now_iso(),
        )
        if conn is not None:
            conn.execute(sql, args)
        else:
            with get_conn() as c:
                c.execute(sql, args)

    def get(self, doc_id: str) -> Optional[SourceDocRow]:
        with get_conn() as c:
            r = c.execute(f"SELECT {_COLS} FROM source_register WHERE doc_id = ?", (doc_id,)).fetchone()
        return _row(r) if r else None

    def exists(self, doc_id: str) -> bool:
        return self.get(doc_id) is not None

    def all(self) -> list[SourceDocRow]:
        with get_conn() as c:
            rows = c.execute(f"SELECT {_COLS} FROM source_register ORDER BY authority_level, effective_from, doc_id").fetchall()
        return [_row(r) for r in rows]

    def infos(self) -> dict[str, DocInfo]:
        return {r.doc_id: to_info(r) for r in self.all()}

    def info(self, doc_id: str) -> Optional[DocInfo]:
        row = self.get(doc_id)
        return to_info(row) if row else None

    def count(self) -> int:
        with get_conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM source_register").fetchone()[0])

    def delete(self, doc_id: str) -> None:
        with get_conn() as c:
            c.execute("DELETE FROM source_register WHERE doc_id = ?", (doc_id,))

    def set_chunks_indexed(self, doc_id: str, n: int, conn: Optional[sqlite3.Connection] = None) -> None:
        sql = "UPDATE source_register SET chunks_indexed = ? WHERE doc_id = ?"
        if conn is not None:
            conn.execute(sql, (n, doc_id))
        else:
            with get_conn() as c:
                c.execute(sql, (n, doc_id))

    # ---- derived relations ---------------------------------------------------------------------
    def superseded_by(self, doc_id: str) -> list[str]:
        """doc_ids that declare they supersede (any part of) the given document."""
        out: list[str] = []
        for info in self.infos().values():
            if any(ref.doc_id == doc_id for ref in info.supersedes):
                out.append(info.doc_id)
        return sorted(out)

    # ---- CSV import / export (source_register.csv, Annex B) -----------------------------------------
    def export_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=REGISTER_COLUMNS)
        writer.writeheader()
        for row in self.all():
            d = row.model_dump()
            writer.writerow({k: ("" if d.get(k) is None else d.get(k)) for k in REGISTER_COLUMNS})
        return buf.getvalue()

    @staticmethod
    def read_csv(path: Path) -> list[SourceMetadata]:
        """Parse a source_register.csv into validated metadata (raises ValidationError on bad rows)."""
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [SourceMetadata(**{k: v for k, v in row.items() if k}) for row in csv.DictReader(fh)]


_default: Optional[SourceRegister] = None


def get_register() -> SourceRegister:
    global _default
    if _default is None:
        _default = SourceRegister()
    return _default
