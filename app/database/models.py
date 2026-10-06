"""Typed row models for the SQLite tables."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class StudentRow(BaseModel):
    student_id: str
    full_name: str
    programme: str
    batch_year: int
    current_semester: int
    cgpa: Optional[float] = None
    active_backlogs: int = 0


class CourseRow(BaseModel):
    course_code: str
    course_name: str
    programme: str
    semester: int
    credits: int


class AttendanceRow(BaseModel):
    student_id: str
    course_code: str
    classes_held: int
    classes_attended: int


class ResultRow(BaseModel):
    student_id: str
    course_code: str
    exam_session: str
    exam_type: str
    internal_marks: Optional[int] = None
    external_marks: Optional[int] = None
    total_marks: Optional[int] = None
    max_marks: Optional[int] = None
    result: str


class RuleRow(BaseModel):
    """A row of rule_registry (Annex C) plus additive columns."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str
    description: str
    parameter: str
    operator: str
    value: str
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    effective_from: str
    effective_to: Optional[str] = None
    source_doc_id: str
    source_section: str
    unit: str = ""
    attributes: Optional[dict[str, Any]] = None
    origin: str = "curated"
    quote: Optional[str] = None
    chunk_id: Optional[str] = None
    source_page: Optional[int] = None


class SourceDocRow(BaseModel):
    """A row of the Source Register (Annex B) plus ingestion bookkeeping."""

    model_config = ConfigDict(extra="ignore")

    doc_id: str
    title: str
    issuer: str
    authority_level: int
    doc_type: str
    version: str
    effective_from: str
    effective_to: Optional[str] = None
    supersedes: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = ""
    retrieved_on: str
    synthetic: str = "N"
    content_hash: Optional[str] = None
    filename: Optional[str] = None
    chunks_indexed: int = 0
    ocr_used: bool = False
    parse_warnings: Optional[str] = None
    ingested_at: Optional[str] = None


class ChunkRow(BaseModel):
    chunk_id: str
    doc_id: str
    ordinal: int
    section_number: str = ""
    section_title: str = ""
    section_path: list[str] = Field(default_factory=list)
    heading_level: int = 0
    page: Optional[int] = None
    kind: str = "paragraph"
    text: str
    embed_text: str
    table_json: Optional[list[list[str]]] = None
    references: list[str] = Field(default_factory=list)
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    content_hash: str
    injection_suspected: bool = False
    confidence: float = 1.0
    ocr: bool = False
