"""Rule resolution: rule_registry rows -> precedence engine -> one applicable rule (or an explained conflict).

Tools never hard-code a threshold. They name a PARAMETER (e.g. "min_attendance_pct") and this module
returns the value in force for the student's programme/batch on as_of_date, with the document clause it traces to.
When a new circular is ingested its rule rows enter the registry (see rag/claims.py); the next call here applies
the Source Precedence Policy to the enlarged candidate set automatically.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal, Optional

from pydantic import Field, field_validator

from app.database.models import RuleRow
from app.sources.conflict import ConflictRecord, UpcomingChange
from app.sources.models import DocInfo
from app.sources.precedence import (
    Candidate,
    ResolutionContext,
    candidate_window,
    resolve,
)
from app.sources.temporal import to_date
from app.tools.base import StrictModel, ToolContext

PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
UNIT_SYMBOL = {"pct": "%"}


class RuleInfo(StrictModel):
    rule_id: str
    parameter: str
    operator: str
    value: str
    unit: str = ""
    display: str  # e.g. ">=80%" (matches the "applied_rules.value" format of the guide)
    description: str = ""
    source_doc_id: str
    source_section: str
    source_page: Optional[int] = None
    effective_from: str
    effective_to: Optional[str] = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    quote: Optional[str] = None
    attributes: Optional[dict[str, Any]] = None


class RuleResolution(StrictModel):
    parameter: str
    status: Literal["resolved", "unresolved", "clarification", "none"]
    rule: Optional[RuleInfo] = None
    tied_rules: list[RuleInfo] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    upcoming: list[UpcomingChange] = Field(default_factory=list)
    clarification: Optional[str] = None
    excluded: list[dict[str, str]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    as_of: str = ""

    def number(self) -> Optional[str]:
        return self.rule.value if self.rule else None


def display_of(row: RuleRow) -> str:
    if row.operator == "in":
        return "in [" + ", ".join(v.strip() for v in row.value.replace(",", ";").split(";") if v.strip()) + "]"
    return f"{row.operator}{row.value}{UNIT_SYMBOL.get(row.unit, '')}"


def to_info(row: RuleRow) -> RuleInfo:
    return RuleInfo(
        rule_id=row.rule_id, parameter=row.parameter, operator=row.operator, value=row.value, unit=row.unit,
        display=display_of(row), description=row.description, source_doc_id=row.source_doc_id,
        source_section=row.source_section, source_page=row.source_page, effective_from=row.effective_from,
        effective_to=row.effective_to, scope_programmes=row.scope_programmes, scope_batches=row.scope_batches,
        quote=row.quote, attributes=row.attributes,
    )


def _to_candidate(row: RuleRow, docs: dict[str, DocInfo], topic: Optional[str] = None) -> Optional[Candidate]:
    doc = docs.get(row.source_doc_id)
    if doc is None:
        return None  # a rule whose source document is not in the register is never used
    eff_from, eff_to = candidate_window(doc, to_date(row.effective_from), to_date(row.effective_to))
    progs = row.scope_programmes if row.scope_programmes.strip().upper() != "ALL" else doc.scope_programmes
    batches = row.scope_batches if row.scope_batches.strip().upper() != "ALL" else doc.scope_batches
    return Candidate(
        cid=row.rule_id, doc_id=row.source_doc_id, section=row.source_section, topic=topic or row.parameter,
        authority_level=doc.authority_level, effective_from=eff_from, effective_to=eff_to,
        scope_programmes=progs, scope_batches=batches, value=display_of(row), text=row.description, payload=row,
        page=row.source_page,
    )


class RuleService:
    """Resolve parameters against the rule registry using the precedence engine."""

    def resolve(self, ctx: ToolContext, parameter: str, programme: Optional[str] = None,
                batch_year: Optional[int] = None) -> RuleResolution:
        if not PARAM_RE.match(parameter or ""):
            raise ValueError(f"invalid parameter name '{parameter}'")
        docs = ctx.docs()
        ctx_prog, ctx_batch = ctx.scope()
        # explicit overrides are honoured only for anonymous policy questions
        prog = ctx_prog if ctx.student() else (programme or ctx_prog)
        batch = ctx_batch if ctx.student() else (batch_year or ctx_batch)
        rows = ctx.services.rules.candidates(parameter)
        cands = [c for c in (_to_candidate(r, docs) for r in rows) if c is not None]
        if not cands:
            return RuleResolution(parameter=parameter, status="none", as_of=ctx.as_of.isoformat(),
                                  trace=[f"no rule_registry rows for parameter '{parameter}'"])
        res = resolve(cands, ResolutionContext(as_of=ctx.as_of, programme=prog, batch_year=batch), docs)
        outcome = res.outcomes.get(parameter)
        out = RuleResolution(
            parameter=parameter, status=outcome.status if outcome else "none", as_of=ctx.as_of.isoformat(),
            conflicts=res.conflicts, upcoming=res.upcoming_changes(), trace=res.trace + res.ignored_supersessions,
            excluded=[{"rule_id": e.cid, "reason": e.reason, "detail": e.detail} for e in res.exclusions],
        )
        if outcome:
            if outcome.winner is not None:
                out.rule = to_info(outcome.winner.payload)
            out.tied_rules = [to_info(c.payload) for c in outcome.tied]
            out.clarification = outcome.clarification
        return out

    def resolve_banded(self, ctx: ToolContext, parameter: str, band_key: str) -> tuple[list[RuleInfo], list[ConflictRecord], list[str]]:
        """Resolve a parameter that has several independent rows (e.g. grade bands), one topic per band."""
        docs = ctx.docs()
        prog, batch = ctx.scope()
        cands: list[Candidate] = []
        for row in ctx.services.rules.candidates(parameter):
            band = (row.attributes or {}).get(band_key)
            cand = _to_candidate(row, docs, topic=f"{parameter}:{band}")
            if cand is not None:
                cands.append(cand)
        res = resolve(cands, ResolutionContext(as_of=ctx.as_of, programme=prog, batch_year=batch), docs)
        winners = [to_info(c.payload) for c in res.winners()]
        problems = [t for t, o in res.outcomes.items() if o.status in ("unresolved", "clarification")]
        return winners, res.conflicts, problems


_service = RuleService()


def get_rule_service() -> RuleService:
    return _service


# ------------------------------------------------------------------------------------------------
# Tool: get_rule
# ------------------------------------------------------------------------------------------------
class GetRuleInput(StrictModel):
    parameter: str = Field(description="rule parameter, e.g. min_attendance_pct")
    programme: Optional[str] = Field(default=None, description="only used for anonymous policy questions")
    batch_year: Optional[int] = Field(default=None, ge=1990, le=2100)

    @field_validator("parameter")
    @classmethod
    def _p(cls, v: str) -> str:
        v = v.strip().lower()
        if not PARAM_RE.match(v):
            raise ValueError("parameter must be a snake_case identifier")
        return v


def get_rule(ctx: ToolContext, inp: GetRuleInput) -> RuleResolution:
    return get_rule_service().resolve(ctx, inp.parameter, inp.programme, inp.batch_year)
