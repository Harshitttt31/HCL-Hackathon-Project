"""Typed state carried through the LangGraph. Every node reads and writes named fields; nothing is hidden in prompts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional, TypedDict

AnswerType = Literal["retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged"]

# Allowlisted intents. The classifier (deterministic first, LLM tie-break second) can only choose from this set.
INTENTS = (
    "attendance_status",       # "what is my attendance in CS201"
    "attendance_projection",   # "how many classes must I attend to reach 75%"
    "attendance_what_if",      # "if my attendance is 70%, am I eligible"
    "exam_eligibility",        # regular exam
    "supplementary_eligibility",
    "placement_eligibility",
    "cgpa",
    "results_status",
    "student_profile",
    "policy_parameter",        # a parameter that lives in the rule registry (deadline, fee, minimum, limit)
    "policy_text",             # any other question about documents
)

PERSONAL_INTENTS = {"attendance_status", "attendance_projection", "exam_eligibility", "supplementary_eligibility",
                    "placement_eligibility", "cgpa", "results_status", "student_profile"}


@dataclass
class Slots:
    course_codes: list[str] = field(default_factory=list)
    exam_type: Optional[str] = None
    parameters: list[str] = field(default_factory=list)
    hypothetical_pct: Optional[float] = None
    threshold_pct: Optional[float] = None
    future_classes: Optional[int] = None
    assume_cleared: list[str] = field(default_factory=list)
    programme: Optional[str] = None
    batch_year: Optional[int] = None
    first_person: bool = False
    asks_in_depth_explanation: bool = False


@dataclass
class Classification:
    intents: list[str] = field(default_factory=list)
    slots: Slots = field(default_factory=Slots)
    confidence: float = 0.0
    source: str = "rules"  # rules | llm | none
    notes: list[str] = field(default_factory=list)
    injection_flag: bool = False

    @property
    def personal(self) -> bool:
        return any(i in PERSONAL_INTENTS for i in self.intents)


@dataclass
class PrivacyVerdict:
    allowed: bool = True
    reason: str = ""
    code: str = ""  # other_student | no_identity | bulk_request | unknown_identity | prompt_injection | ""
    detail: str = ""


@dataclass
class Finding:
    """One deterministic result block. The composer turns findings into the answer; the LLM never creates one."""

    kind: str  # attendance | eligibility | projection | policy_rule | policy_text | cgpa | results | profile | not_found | clarification | conflict
    answer_type: AnswerType
    text: str  # deterministic sentence(s)
    facts: list[str] = field(default_factory=list)  # every number/date/id that may legally appear in the final answer
    citations: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)  # {"rule_id","value","source_doc_id","section"}
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    upcoming: list[dict[str, Any]] = field(default_factory=list)
    explanation: str = ""


class AgentState(TypedDict, total=False):
    # request
    trace_id: str
    question: str
    student_id: Optional[str]
    as_of: date
    started: float
    # processing
    cleaned_question: str
    classification: Classification
    privacy: PrivacyVerdict
    plan: list[dict[str, Any]]
    tool_calls: list[Any]
    evidence: list[Any]
    retrieval_stats: dict[str, Any]
    coverage: float
    missing_terms: list[str]
    findings: list[Finding]
    node_trace: list[dict[str, Any]]
    # output
    answer_type: AnswerType
    draft_answer: str
    answer: str
    explanation: str
    citations: list[dict[str, Any]]
    applied_rules: list[dict[str, Any]]
    conflicts_detected: list[dict[str, Any]]
    allowed_facts: list[str]
    llm_used: bool
    validation: dict[str, Any]
    warnings: list[str]
