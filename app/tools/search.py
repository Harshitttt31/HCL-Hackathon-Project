"""Document and source-register tools. They wrap the hybrid retriever and the Source Register."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from app.core.errors import RecordNotFound
from app.sources.models import AUTHORITY_LABELS, DOC_TYPES
from app.sources.scope import describe_scope, scope_match
from app.sources.temporal import window_status
from app.tools.base import StrictModel, ToolContext


class SearchDocumentsInput(StrictModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class EvidenceItem(StrictModel):
    chunk_id: str
    doc_id: str
    section: str
    page: Optional[int] = None
    score: float
    authority_level: int
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    excerpt: str = ""


class SearchDocumentsOutput(StrictModel):
    results: list[EvidenceItem]
    note: str = "Raw similarity results. Authority, supersession and date filtering are applied later by the precedence engine; similarity is not authority."


def search_documents(ctx: ToolContext, inp: SearchDocumentsInput) -> SearchDocumentsOutput:
    retriever = ctx.services.retriever
    if retriever is None:
        raise ValueError("document retriever is not available")
    hits = retriever.retrieve(inp.query, top_k=inp.top_k)
    return SearchDocumentsOutput(results=[
        EvidenceItem(chunk_id=h.chunk_id, doc_id=h.doc_id, section=h.section, page=h.page, score=round(h.score, 4),
                     authority_level=h.authority_level, effective_from=h.effective_from, effective_to=h.effective_to,
                     excerpt=h.text[:240]) for h in hits
    ])


class GetSourceMetadataInput(StrictModel):
    doc_id: str = Field(min_length=2, max_length=64)


class SourceMetadataOutput(StrictModel):
    doc_id: str
    title: str
    issuer: str
    authority_level: int
    authority_label: str
    doc_type: str
    version: str
    effective_from: str
    effective_to: Optional[str] = None
    supersedes: str = ""
    superseded_by: list[str] = Field(default_factory=list)
    scope_programmes: str
    scope_batches: str
    provenance: str = ""
    retrieved_on: str
    synthetic: str
    window_on_as_of: str


def get_source_metadata(ctx: ToolContext, inp: GetSourceMetadataInput) -> SourceMetadataOutput:
    reg = ctx.services.register
    row = reg.get(inp.doc_id)
    if row is None:
        raise RecordNotFound(f"source {inp.doc_id}")
    return SourceMetadataOutput(
        **row.model_dump(include={"doc_id", "title", "issuer", "authority_level", "doc_type", "version", "effective_from",
                                  "effective_to", "supersedes", "scope_programmes", "scope_batches", "provenance",
                                  "retrieved_on", "synthetic"}),
        authority_label=AUTHORITY_LABELS.get(row.authority_level, ""),
        superseded_by=reg.superseded_by(row.doc_id),
        window_on_as_of=window_status(row.effective_from, row.effective_to, ctx.as_of),
    )


class ApplicableSourcesInput(StrictModel):
    doc_type: Optional[str] = Field(default=None, description="regulation | circular | notice | faq | handbook")


class ApplicableSource(StrictModel):
    doc_id: str
    title: str
    version: str
    authority_level: int
    doc_type: str
    effective_from: str
    effective_to: Optional[str] = None
    scope: str
    supersedes: str = ""
    superseded_by_in_force: list[str] = Field(default_factory=list)


class ApplicableSourcesOutput(StrictModel):
    programme: Optional[str] = None
    batch_year: Optional[int] = None
    as_of: str
    applicable: list[ApplicableSource]
    upcoming: list[ApplicableSource] = Field(default_factory=list)


def list_applicable_sources(ctx: ToolContext, inp: ApplicableSourcesInput) -> ApplicableSourcesOutput:
    """Which documents govern this student (programme + batch) on as_of_date. Pure metadata logic, no similarity."""
    if inp.doc_type and inp.doc_type not in DOC_TYPES:
        raise ValueError(f"doc_type must be one of {', '.join(DOC_TYPES)}")
    programme, batch = ctx.scope()
    docs = ctx.docs()
    applicable, upcoming = [], []
    for d in docs.values():
        if inp.doc_type and d.doc_type != inp.doc_type:
            continue
        if d.authority_level >= 5:
            continue
        sc = scope_match(d.scope_programmes, d.scope_batches, programme, batch)
        if sc == "no":
            continue
        win = window_status(d.effective_from, d.effective_to, ctx.as_of)
        if win == "expired":
            continue
        in_force_supers = [
            o.doc_id for o in docs.values()
            if o.authority_level <= 2 and o.doc_id != d.doc_id and any(r.doc_id == d.doc_id and r.clause is None for r in o.supersedes)
            and window_status(o.effective_from, o.effective_to, ctx.as_of) == "effective"
            and scope_match(o.scope_programmes, o.scope_batches, programme, batch) == "yes"
        ]
        item = ApplicableSource(doc_id=d.doc_id, title=d.title, version=d.version, authority_level=d.authority_level,
                                doc_type=d.doc_type, effective_from=d.effective_from.isoformat(),
                                effective_to=d.effective_to.isoformat() if d.effective_to else None,
                                scope=describe_scope(d.scope_programmes, d.scope_batches),
                                supersedes=";".join(r.label() for r in d.supersedes), superseded_by_in_force=in_force_supers)
        (upcoming if win == "upcoming" else applicable).append(item)
    applicable = [a for a in applicable if not a.superseded_by_in_force]
    key = lambda a: (a.authority_level, a.effective_from, a.doc_id)
    return ApplicableSourcesOutput(programme=programme, batch_year=batch, as_of=ctx.as_of.isoformat(),
                                   applicable=sorted(applicable, key=key), upcoming=sorted(upcoming, key=key))
