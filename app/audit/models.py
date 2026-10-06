"""The audit record: enough to reproduce and defend a decision, with no avoidable personal data.

Stored: hashed student id, redacted question, the intent and privacy verdict, node-by-node trace, tool inputs and outputs
(the same objects the API returns), applied rules, conflicts, which sources were excluded and why, retrieval statistics,
validation result and latency. Never stored: the raw student id, names, or the LLM prompt text.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AuditRecord(BaseModel):
    trace_id: str
    ts: str
    as_of_date: str
    student_id_hash: Optional[str] = None
    question_hash: str
    question_redacted: str
    question_category: str = ""
    intents: list[str] = Field(default_factory=list)
    classification_source: str = ""
    privacy: dict[str, Any] = Field(default_factory=dict)
    answer_type: str = ""
    answer: str = ""
    explanation: str = ""
    node_trace: list[dict[str, Any]] = Field(default_factory=list)
    tools_invoked: list[dict[str, Any]] = Field(default_factory=list)
    applied_rules: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    conflicts_detected: list[dict[str, Any]] = Field(default_factory=list)
    excluded_sources: list[dict[str, Any]] = Field(default_factory=list)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    latency_ms: int = 0
