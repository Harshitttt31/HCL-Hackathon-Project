"""Citations are built from registry + chunk records only. The LLM never writes or edits a citation."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from app.database.repositories import ChunkRepo
from app.sources.models import DocInfo


class Citation(BaseModel):
    doc_id: str
    title: str
    section: str
    page: Optional[int] = None
    version: str = ""
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    authority_level: Optional[int] = None
    excerpt: str = ""
    role: str = "supports"  # supports | superseded | informational | conflicting


def _excerpt(text: str, limit: int = 320) -> str:
    flat = re.sub(r"\s+", " ", text or "").strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def build_citation(doc: Optional[DocInfo], doc_id: str, section: str, page: Optional[int] = None,
                   excerpt: str = "", role: str = "supports") -> Citation:
    return Citation(
        doc_id=doc_id,
        title=doc.title if doc else "",
        section=section or "",
        page=page,
        version=doc.version if doc else "",
        effective_from=doc.effective_from.isoformat() if doc and doc.effective_from else None,
        effective_to=doc.effective_to.isoformat() if doc and doc.effective_to else None,
        authority_level=doc.authority_level if doc else None,
        excerpt=_excerpt(excerpt),
        role=role,
    )


def citation_for_clause(chunks: ChunkRepo, doc: Optional[DocInfo], doc_id: str, section: str,
                        page: Optional[int] = None, quote: Optional[str] = None, role: str = "supports") -> Citation:
    """Cite a clause, enriching page and excerpt from the ingested chunk that holds it (when present)."""
    excerpt = quote or ""
    if section and (page is None or not excerpt):
        found = chunks.find_by_section(doc_id, section)
        if found:
            best = found[0]
            page = page if page is not None else best.page
            if not excerpt:
                excerpt = best.text
    return build_citation(doc, doc_id, section, page, excerpt, role)
