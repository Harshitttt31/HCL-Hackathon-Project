"""Source Register models (Annex B) with strict validation.

SourceMetadata is the ONLY shape accepted by POST /ingest: unknown keys are dropped (and reported),
every known key is validated. Nothing from the request is stored unvalidated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.clock import today
from app.sources.scope import is_all, parse_batch_spec, split_programmes

DOC_TYPES = ("regulation", "circular", "notice", "faq", "handbook", "unofficial")
# plausible authority levels for each doc_type (Annex A). Mismatches produce warnings, not errors,
# because an office decides the level, e.g. a Registrar "notice" is level 2 while a department notice is level 3.
PLAUSIBLE_LEVELS = {
    "regulation": {1},
    "circular": {2},
    "notice": {2, 3},
    "faq": {3, 4, 5},
    "handbook": {3, 4},
    "unofficial": {5},
}
AUTHORITY_LABELS = {
    1: "Statutes, ordinances, academic regulations",
    2: "Official circulars and notifications from an authorised office",
    3: "Department notices",
    4: "Handbooks and FAQs",
    5: "Unofficial content (untrusted)",
}

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
_SUPERSEDE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{1,63})(?:#([A-Za-z0-9][A-Za-z0-9._()-]*))?$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(text: Any, max_len: int = 500) -> str:
    out = _CTRL_RE.sub("", str(text if text is not None else "")).strip()
    return out[:max_len]


def _opt_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "n/a", "na", "-"):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"'{text}' is not a valid YYYY-MM-DD date") from exc


@dataclass(frozen=True)
class SupersedeRef:
    doc_id: str
    clause: Optional[str] = None  # None = whole document

    def label(self) -> str:
        return f"{self.doc_id}#{self.clause}" if self.clause else self.doc_id


def parse_supersedes(value: Optional[str]) -> list[SupersedeRef]:
    refs: list[SupersedeRef] = []
    for raw in re.split(r"[;,]", value or ""):
        token = raw.strip()
        if not token:
            continue
        m = _SUPERSEDE_RE.match(token)
        if not m:
            raise ValueError(f"'{token}' is not a valid supersedes reference (use DOC_ID or DOC_ID#clause)")
        refs.append(SupersedeRef(m.group(1), m.group(2)))
    return refs


class SourceMetadata(BaseModel):
    """Annex B fields, validated."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    doc_id: str
    title: str
    issuer: str
    authority_level: int = Field(ge=1, le=5)
    doc_type: str
    version: str = "1.0"
    effective_from: str
    effective_to: Optional[str] = None
    supersedes: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = ""
    retrieved_on: Optional[str] = None
    synthetic: str = "N"

    @field_validator("doc_id")
    @classmethod
    def _v_doc_id(cls, v: str) -> str:
        v = _clean(v, 64)
        if not _DOC_ID_RE.match(v):
            raise ValueError("doc_id must be 2-64 chars of letters, digits, '.', '_' or '-' and must not contain '#', ';' or spaces")
        return v

    @field_validator("title", "issuer")
    @classmethod
    def _v_text(cls, v: str) -> str:
        v = _clean(v, 300)
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("version")
    @classmethod
    def _v_version(cls, v: Any) -> str:
        v = _clean(v, 40)
        return v or "1.0"

    @field_validator("doc_type")
    @classmethod
    def _v_type(cls, v: str) -> str:
        v = _clean(v, 30).lower()
        if v not in DOC_TYPES:
            raise ValueError(f"doc_type must be one of {', '.join(DOC_TYPES)}")
        return v

    @field_validator("authority_level", mode="before")
    @classmethod
    def _v_level(cls, v: Any) -> int:
        try:
            return int(str(v).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("authority_level must be an integer from 1 to 5") from exc

    @field_validator("effective_from", mode="before")
    @classmethod
    def _v_from(cls, v: Any) -> str:
        out = _opt_date(v)
        if out is None:
            raise ValueError("effective_from is required (YYYY-MM-DD)")
        return out

    @field_validator("effective_to", mode="before")
    @classmethod
    def _v_to(cls, v: Any) -> Optional[str]:
        return _opt_date(v)

    @field_validator("retrieved_on", mode="before")
    @classmethod
    def _v_retrieved(cls, v: Any) -> Optional[str]:
        return _opt_date(v)

    @field_validator("supersedes", mode="before")
    @classmethod
    def _v_supersedes(cls, v: Any) -> str:
        text = _clean(v if v is not None else "", 1000)
        refs = parse_supersedes(text)
        return ";".join(r.label() for r in refs)

    @field_validator("scope_programmes", mode="before")
    @classmethod
    def _v_progs(cls, v: Any) -> str:
        text = _clean(v if v is not None else "ALL", 300)
        if not text or is_all(text):
            return "ALL"
        return "; ".join(split_programmes(text))

    @field_validator("scope_batches", mode="before")
    @classmethod
    def _v_batches(cls, v: Any) -> str:
        text = _clean(v if v is not None else "ALL", 100)
        if not text or is_all(text):
            return "ALL"
        parse_batch_spec(text)  # raises on malformed
        return text.replace("–", "-")

    @field_validator("provenance", mode="before")
    @classmethod
    def _v_prov(cls, v: Any) -> str:
        return _clean(v if v is not None else "", 500)

    @field_validator("synthetic", mode="before")
    @classmethod
    def _v_syn(cls, v: Any) -> str:
        text = _clean(v if v is not None else "N", 10).lower()
        if text in ("y", "yes", "true", "1"):
            return "Y"
        if text in ("n", "no", "false", "0", ""):
            return "N"
        raise ValueError("synthetic must be Y or N")

    @model_validator(mode="after")
    def _v_window(self) -> "SourceMetadata":
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be earlier than effective_from")
        if not self.retrieved_on:
            self.retrieved_on = today().isoformat()
        for ref in parse_supersedes(self.supersedes):
            if ref.doc_id == self.doc_id and ref.clause is None:
                raise ValueError("a document cannot supersede itself")
        return self

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.authority_level not in PLAUSIBLE_LEVELS.get(self.doc_type, set(range(1, 6))):
            out.append(
                f"authority_level {self.authority_level} is unusual for doc_type '{self.doc_type}' "
                f"(expected {sorted(PLAUSIBLE_LEVELS[self.doc_type])}); the level you supplied is used as given"
            )
        if self.supersedes and self.authority_level > 2:
            out.append("supersedes is only honoured for authority level 1 or 2 documents (Annex A step 2); it will be ignored for this document")
        return out


@dataclass
class DocInfo:
    """A Source Register entry parsed for the precedence engine."""

    doc_id: str
    title: str
    issuer: str
    authority_level: int
    doc_type: str
    version: str
    effective_from: date
    effective_to: Optional[date]
    supersedes: list[SupersedeRef] = field(default_factory=list)
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = ""
    synthetic: bool = False
