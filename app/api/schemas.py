"""Request and response models. /ask follows the guide's fixed contract (section 6.1) field for field."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000, description="The question in natural language.")
    as_of_date: Optional[str] = Field(default=None, description="YYYY-MM-DD; rules and documents are applied as in force on this date. Defaults to today.",
                                      pattern=r"^\d{4}-\d{2}-\d{2}$")


class CitationOut(BaseModel):
    doc_id: str
    title: str = ""
    section: str = ""
    page: Optional[int] = None
    version: str = ""
    effective_from: Optional[str] = None


class ToolInvocation(BaseModel):
    tool: str
    input: dict[str, Any]
    output: Optional[dict[str, Any]] = None


class AppliedRule(BaseModel):
    rule_id: str
    value: str
    source_doc_id: str


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: Literal["retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged"]
    citations: list[CitationOut]
    tools_invoked: list[ToolInvocation]
    applied_rules: list[AppliedRule]
    conflicts_detected: list[dict[str, Any]]
    explanation: str
    as_of_date: str


class IngestResponse(BaseModel):
    doc_id: str
    chunks_indexed: int
    status: str
    rules_registered: int = 0
    ocr_used: bool = False
    pages: int = 0
    injection_chunks_flagged: int = 0
    replaced_previous_version: bool = False
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    api: str = "ok"
    sqlite: str
    vector_store: str
    llm: str
    llm_backend: str = ""
    llm_model: str = ""
    embedder: str = ""
    chunks_indexed: int = 0
    vectors: int = 0
    documents: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class SourceOut(BaseModel):
    doc_id: str
    title: str
    issuer: str
    authority_level: int
    authority_label: str = ""
    doc_type: str
    version: str
    effective_from: str
    effective_to: Optional[str] = None
    supersedes: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = ""
    retrieved_on: str = ""
    synthetic: str = "N"
    status_as_of: str = ""
    chunks_indexed: int = 0
    ocr_used: bool = False


class ErrorResponse(BaseModel):
    detail: Any
