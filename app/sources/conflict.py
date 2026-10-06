"""Conflict records and their plain-language explanations (generated from the resolution, never by an LLM)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.sources.models import AUTHORITY_LABELS


class ConflictSource(BaseModel):
    doc_id: str
    title: str = ""
    section: str = ""
    value: Optional[str] = None
    authority_level: int
    issuer: str = ""
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


class ConflictRecord(BaseModel):
    topic: str
    kind: Literal["supersession", "authority", "recency", "unresolved", "scope_ambiguity"]
    resolved: bool
    resolution_rule: str
    winner: Optional[ConflictSource] = None
    overridden: list[ConflictSource] = Field(default_factory=list)
    explanation: str


class UpcomingChange(BaseModel):
    doc_id: str
    title: str = ""
    section: str = ""
    topic: str = ""
    value: Optional[str] = None
    authority_level: int
    effective_from: Optional[str] = None
    note: str = ""


RULE_TEXT = {
    "supersession": "Annex A step 2: a level 1 or 2 document that explicitly supersedes another replaces it",
    "authority": "Annex A step 3: a higher-authority document prevails over a lower-authority one regardless of date",
    "recency": "Annex A step 4: between documents of the same authority the later effective_from prevails",
    "unresolved": "Annex A step 5: not resolvable by the policy; both documents are cited and the issuing office should be contacted",
    "scope_ambiguity": "Annex A step 1: applicability depends on programme or batch that is not known",
}


def label(src: ConflictSource) -> str:
    val = f" says {src.value}" if src.value else ""
    sec = f" {src.section}" if src.section else ""
    return f"{src.doc_id}{sec} (level {src.authority_level}, effective {src.effective_from or 'n/a'}){val}"


def explain(kind: str, topic: str, winner: Optional[ConflictSource], others: list[ConflictSource]) -> str:
    topic_h = topic.replace("_", " ")
    if kind == "supersession" and winner and others:
        o = others[0]
        return (f"{label(o)} was explicitly superseded by {winner.doc_id} "
                f"({AUTHORITY_LABELS.get(winner.authority_level, '')}, effective {winner.effective_from}); "
                f"the earlier text is no longer applicable for '{topic_h}'.")
    if kind == "authority" and winner and others:
        o = others[0]
        return (f"{label(winner)} prevails over {label(o)} because it has higher authority "
                f"(level {winner.authority_level} vs level {o.authority_level}); date does not matter across authority levels.")
    if kind == "recency" and winner and others:
        o = others[0]
        return (f"Both documents have authority level {winner.authority_level}; {label(winner)} is later than "
                f"{label(o)}, so the later effective_from prevails.")
    if kind == "unresolved":
        offices = sorted({s.issuer for s in others if s.issuer})
        who = " and ".join(offices) if offices else "the issuing offices"
        return (f"Sources disagree on '{topic_h}' and the precedence policy cannot decide between them: "
                f"{'; '.join(label(s) for s in others)}. Please contact {who} to confirm.")
    if kind == "scope_ambiguity":
        return (f"The answer for '{topic_h}' differs by programme or batch and the question does not say which applies: "
                f"{'; '.join(label(s) for s in others)}.")
    return ""
