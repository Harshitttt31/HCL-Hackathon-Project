from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import ValidationError

from app.agent.service import AssistantService
from app.api.deps import get_ingestion, get_principal, get_service, require_admin, resolve_student_id
from app.api.schemas import AskRequest, AskResponse, HealthResponse, IngestResponse, SourceOut
from app.auth.service import Principal
from app.core.config import get_settings
from app.core.errors import ParseError, ValidationFailed
from app.core.logging import get_logger
from app.core.security import constant_time_equals, hash_student_id, normalize_student_id
from app.database import loader
from app.database.repositories import ChunkRepo, StudentRepo
from app.database.sqlite import db_reachable
from app.rag.ingestion import IngestionService
from app.sources.models import AUTHORITY_LABELS, SourceMetadata
from app.sources.registry import get_register
from app.sources.temporal import to_date, window_status

log = get_logger(__name__)
router = APIRouter()


@router.post("/ask", response_model=AskResponse, tags=["assistant"])
def ask(body: AskRequest, student_id: Optional[str] = Depends(resolve_student_id), service: AssistantService = Depends(get_service)):
    """Answer a question. The student identity comes only from the request context: a Bearer token, or the X-Student-Id header."""
    try:
        return service.ask(body.question, student_id, body.as_of_date)
    except ValidationFailed as exc:
        raise HTTPException(status_code=422, detail=exc.errors)


@router.post("/ingest", response_model=IngestResponse, tags=["documents"], dependencies=[Depends(require_admin)])
async def ingest(file: UploadFile = File(...), metadata: str = Form(..., description="Source Register fields (Annex B) as a JSON object"),
                 ingestion: IngestionService = Depends(get_ingestion)):
    """Add a document while the system is running. It is searchable as soon as this call returns."""
    try:
        raw = json.loads(metadata)
        if not isinstance(raw, dict):
            raise ValueError("metadata must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=[f"metadata is not valid JSON: {exc}"])
    try:
        meta = SourceMetadata(**raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()])
    data = await file.read()
    from fastapi.concurrency import run_in_threadpool

    try:
        result = await run_in_threadpool(ingestion.ingest, data, file.filename or "upload", meta)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=[f"could not read the document: {exc}"])
    except ValidationFailed as exc:
        raise HTTPException(status_code=413 if "larger" in str(exc) else 422, detail=exc.errors)
    return result.model_dump()


@router.get("/health", response_model=HealthResponse, tags=["operations"])
def health(service: AssistantService = Depends(get_service)):
    s = get_settings()
    sqlite_ok = db_reachable()
    store = service.retriever.vector_store()
    vec_ok = bool(store and store.healthy())
    llm = service.deps.llm
    llm_ok = llm.healthy()
    stats = service.retriever.stats()
    status = "down" if not sqlite_ok else ("ok" if vec_ok and llm_ok else "degraded")
    return HealthResponse(
        status=status, sqlite="ok" if sqlite_ok else "unreachable", vector_store="ok" if vec_ok else "unavailable (keyword search only)",
        llm=("mock" if llm.is_mock else ("ok" if llm_ok else "unavailable (template answers are used)")), llm_backend=llm.name, llm_model=llm.model,
        embedder=stats["embedder"], chunks_indexed=stats["chunks_indexed"], vectors=stats["vectors"], documents=get_register().count(),
        details={"embeddings_degraded": stats["embeddings_degraded"], "reranker": stats["reranker"], "vector_store_error": stats["vector_store_error"],
                 "prompt_version": s.prompt_version})


@router.get("/audit/{trace_id}", tags=["operations"])
def audit(trace_id: str, principal: Optional[Principal] = Depends(get_principal), student_id: Optional[str] = Depends(resolve_student_id),
          x_admin_token: Optional[str] = Header(default=None), service: AssistantService = Depends(get_service)):
    """Full decision record. Readable by the student it belongs to or by an administrator."""
    if not trace_id.isalnum() or len(trace_id) > 32:
        raise HTTPException(status_code=404, detail="no audit record with this trace_id")
    rec = service.deps.audit.read(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="no audit record with this trace_id")
    token = get_settings().admin_token
    is_admin = (principal is not None and principal.role == "admin") or bool(token and constant_time_equals(x_admin_token, token))
    owner = rec.get("student_id_hash")
    if owner and not is_admin:
        caller = hash_student_id(normalize_student_id(student_id))
        if not caller or caller != owner:
            raise HTTPException(status_code=403, detail="this audit record belongs to a different student")
    return rec


@router.get("/sources", response_model=list[SourceOut], tags=["documents"])
def sources(as_of: Optional[str] = None):
    """The Source Register: every ingested document with authority, version, validity window and scope."""
    try:
        when = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        raise HTTPException(status_code=422, detail=["as_of must be YYYY-MM-DD"])
    out = []
    for d in get_register().all():
        status = window_status(to_date(d.effective_from), to_date(d.effective_to), when)
        out.append(SourceOut(
            doc_id=d.doc_id, title=d.title, issuer=d.issuer, authority_level=d.authority_level, authority_label=AUTHORITY_LABELS.get(d.authority_level, ""),
            doc_type=d.doc_type, version=d.version, effective_from=str(d.effective_from), effective_to=str(d.effective_to) if d.effective_to else None,
            supersedes=d.supersedes or "", scope_programmes=d.scope_programmes, scope_batches=d.scope_batches, provenance=d.provenance or "",
            retrieved_on=str(d.retrieved_on), synthetic=d.synthetic, status_as_of=status, chunks_indexed=d.chunks_indexed, ocr_used=bool(d.ocr_used)))
    return out


@router.post("/admin/load", tags=["admin"], dependencies=[Depends(require_admin)])
async def admin_load(courses: Optional[UploadFile] = File(default=None), students: Optional[UploadFile] = File(default=None),
                     attendance: Optional[UploadFile] = File(default=None), results: Optional[UploadFile] = File(default=None),
                     rules: Optional[UploadFile] = File(default=None)):
    """Load CSV files in the Annex C schema (judge test data). Invalid rows are reported with their line numbers."""
    files = {}
    for name, up in (("courses", courses), ("students", students), ("attendance", attendance), ("results", results), ("rules", rules)):
        if up is not None:
            files[name] = await up.read()
    if not files:
        raise HTTPException(status_code=422, detail=["send at least one CSV file (courses, students, attendance, results, rules)"])
    from fastapi.concurrency import run_in_threadpool

    reports = await run_in_threadpool(loader.load_all, files)
    return {"reports": [r.model_dump() for r in reports], "students_total": StudentRepo().count()}
