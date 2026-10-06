"""Source Precedence Policy (guide Annex A) as deterministic code.

Order of resolution (A.2):
  1. Applicability   effective on as_of_date AND programme/batch scope covers the context.
                     Not-yet-effective documents are excluded (reported as upcoming changes).
  2. Supersession    a document that explicitly supersedes another (or one clause of it) replaces it,
                     but ONLY if the superseding document is issued at authority level 1 or 2.
  3. Authority       a higher-authority document prevails over a lower one, regardless of date.
  4. Recency         between documents of the same authority the later effective_from prevails.
  5. Unresolved      otherwise: conflict_flagged, both documents are cited, contact the issuing office.
Level 5 content is informational only and can never override another document.

Vector similarity plays NO role here. The LLM plays NO role here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from app.sources.conflict import (
    RULE_TEXT,
    ConflictRecord,
    ConflictSource,
    UpcomingChange,
    explain,
)
from app.sources.models import DocInfo, SupersedeRef
from app.sources.scope import scope_match
from app.sources.temporal import intersect_windows, window_status

_TEXT_SIM_THRESHOLD = 0.8


# ------------------------------------------------------------------------------------------------
# Inputs
# ------------------------------------------------------------------------------------------------
@dataclass
class ResolutionContext:
    as_of: date
    programme: Optional[str] = None
    batch_year: Optional[int] = None


@dataclass
class Candidate:
    """One statement that could answer the question: a rule_registry row or a retrieved chunk."""

    cid: str
    doc_id: str
    section: str
    topic: str
    authority_level: int
    effective_from: Optional[date]
    effective_to: Optional[date] = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    value: Optional[str] = None  # comparable value (e.g. ">=80%"); None => compare by text similarity
    text: str = ""
    payload: Any = None
    scope: str = "yes"  # set by the engine: yes | unknown
    page: Optional[int] = None

    def sort_key(self) -> tuple:
        eff = self.effective_from.toordinal() if self.effective_from else 0
        return (self.authority_level, -eff, self.doc_id, self.cid)


# ------------------------------------------------------------------------------------------------
# Outputs
# ------------------------------------------------------------------------------------------------
@dataclass
class Exclusion:
    cid: str
    doc_id: str
    section: str
    topic: str
    reason: str  # not_yet_effective | expired | out_of_scope | superseded | untrusted_informational | lower_authority
    detail: str


@dataclass
class Supersession:
    target: Candidate
    by: DocInfo
    mode: str  # full | partial


@dataclass
class TopicOutcome:
    topic: str
    status: str  # resolved | unresolved | clarification | none
    winner: Optional[Candidate] = None
    supporting: list[Candidate] = field(default_factory=list)
    losers: list[Candidate] = field(default_factory=list)
    tied: list[Candidate] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    clarification: Optional[str] = None


@dataclass
class Resolution:
    ctx: ResolutionContext
    outcomes: dict[str, TopicOutcome] = field(default_factory=dict)
    exclusions: list[Exclusion] = field(default_factory=list)
    upcoming: list[Candidate] = field(default_factory=list)
    informational: list[Candidate] = field(default_factory=list)
    superseded: list[Supersession] = field(default_factory=list)
    ignored_supersessions: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    docs: dict[str, DocInfo] = field(default_factory=dict)

    @property
    def conflicts(self) -> list[ConflictRecord]:
        out: list[ConflictRecord] = []
        for outcome in self.outcomes.values():
            out.extend(outcome.conflicts)
        return out

    @property
    def status(self) -> str:
        statuses = {o.status for o in self.outcomes.values()}
        for s in ("unresolved", "clarification"):
            if s in statuses:
                return s
        if "resolved" in statuses:
            return "resolved"
        return "none"

    def winners(self) -> list[Candidate]:
        return [o.winner for o in self.outcomes.values() if o.winner is not None]

    def superseders_without_candidates(self, present_doc_ids: set[str]) -> set[str]:
        """Documents that supersede something we hold but whose own text was not among the candidates."""
        return {s.by.doc_id for s in self.superseded if s.by.doc_id not in present_doc_ids}

    def upcoming_changes(self) -> list[UpcomingChange]:
        out = []
        for c in self.upcoming:
            doc = self.docs.get(c.doc_id)
            out.append(
                UpcomingChange(
                    doc_id=c.doc_id, title=doc.title if doc else "", section=c.section, topic=c.topic,
                    value=display_value(c), authority_level=c.authority_level,
                    effective_from=c.effective_from.isoformat() if c.effective_from else None,
                    note=f"Not yet effective on {self.ctx.as_of.isoformat()}; effective from "
                         f"{c.effective_from.isoformat() if c.effective_from else 'n/a'}. Not treated as current.",
                )
            )
        return out


# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------
def _norm_clause(clause: str) -> str:
    c = re.sub(r"\b(clause|section|sec|article|para|paragraph)\b\.?", "", clause or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", "", c).strip(".").lower()


def clause_relation(ref_clause: str, section: str) -> str:
    """How a superseded clause reference relates to a candidate's section number.

    same / nested : the candidate IS (or lies inside) the superseded clause -> replaced
    parent        : the reference is narrower than the candidate -> only partially replaced
    none          : unrelated
    """
    ref = _norm_clause(ref_clause)
    sec = _norm_clause(section)
    if not ref or not sec:
        return "none"
    if ref == sec:
        return "same"
    if sec.startswith(ref + ".") or sec.startswith(ref + "("):
        return "nested"
    if ref.startswith(sec + ".") or ref.startswith(sec + "("):
        return "parent"
    return "none"


def normalize_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", "", str(value)).lower()
    m = re.fullmatch(r"([<>=!]{1,2}|between)?(-?\d+(?:\.\d+)?)(%|[a-z]*)", text)
    if m:
        try:
            num = format(Decimal(m.group(2)).normalize(), "f")
        except InvalidOperation:
            num = m.group(2)
        return f"{m.group(1) or ''}{num}{m.group(3)}"
    return text


def display_value(c: Candidate) -> Optional[str]:
    return c.value


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _src(c: Candidate, docs: dict[str, DocInfo]) -> ConflictSource:
    doc = docs.get(c.doc_id)
    return ConflictSource(
        doc_id=c.doc_id,
        title=doc.title if doc else "",
        section=c.section,
        value=display_value(c),
        authority_level=c.authority_level,
        issuer=doc.issuer if doc else "",
        effective_from=c.effective_from.isoformat() if c.effective_from else None,
        effective_to=c.effective_to.isoformat() if c.effective_to else None,
    )


def candidate_window(doc: Optional[DocInfo], own_from: Optional[date], own_to: Optional[date]) -> tuple[Optional[date], Optional[date]]:
    """A rule/chunk is valid only while BOTH its own window and its document's window are valid."""
    if doc is None:
        return own_from, own_to
    return intersect_windows(own_from, own_to, doc.effective_from, doc.effective_to)


# ------------------------------------------------------------------------------------------------
# The engine
# ------------------------------------------------------------------------------------------------
def resolve(candidates: list[Candidate], ctx: ResolutionContext, docs: dict[str, DocInfo]) -> Resolution:
    res = Resolution(ctx=ctx, docs=docs)
    survivors: list[Candidate] = []

    # ---- Step 1: applicability (effective on as_of AND programme/batch scope) ---------------------
    for c in candidates:
        win = window_status(c.effective_from, c.effective_to, ctx.as_of)
        sc = scope_match(c.scope_programmes, c.scope_batches, ctx.programme, ctx.batch_year)
        if win == "upcoming":
            res.exclusions.append(Exclusion(c.cid, c.doc_id, c.section, c.topic, "not_yet_effective",
                                            f"effective_from {c.effective_from} is after as_of {ctx.as_of}"))
            if sc != "no" and c.authority_level <= 4:
                res.upcoming.append(c)
            continue
        if win == "expired":
            res.exclusions.append(Exclusion(c.cid, c.doc_id, c.section, c.topic, "expired",
                                            f"effective_to {c.effective_to} is before as_of {ctx.as_of}"))
            continue
        if sc == "no":
            res.exclusions.append(Exclusion(c.cid, c.doc_id, c.section, c.topic, "out_of_scope",
                                            f"scope ({c.scope_programmes}; {c.scope_batches}) does not cover "
                                            f"programme={ctx.programme} batch={ctx.batch_year}"))
            continue
        c.scope = sc
        if c.authority_level >= 5:
            res.informational.append(c)
            res.exclusions.append(Exclusion(c.cid, c.doc_id, c.section, c.topic, "untrusted_informational",
                                            "authority level 5 content is informational only and can never override another document"))
            continue
        survivors.append(c)
    res.trace.append(
        f"step1 applicability as_of={ctx.as_of} programme={ctx.programme} batch={ctx.batch_year}: "
        f"{len(candidates)} candidates -> {len(survivors)} applicable, {len(res.upcoming)} upcoming, "
        f"{len(res.informational)} informational"
    )

    # ---- Step 2: explicit supersession (only by level 1-2 documents that are in force) --------------
    in_force: list[DocInfo] = []
    candidate_docs = {c.doc_id for c in candidates}
    for doc in docs.values():
        if not doc.supersedes:
            continue
        if doc.authority_level > 2:
            if any(ref.doc_id in candidate_docs for ref in doc.supersedes):
                res.ignored_supersessions.append(
                    f"{doc.doc_id}: supersession claim ignored, issued at authority level {doc.authority_level} (only level 1-2 may supersede)"
                )
            continue
        if window_status(doc.effective_from, doc.effective_to, ctx.as_of) != "effective":
            continue
        if scope_match(doc.scope_programmes, doc.scope_batches, ctx.programme, ctx.batch_year) != "yes":
            continue
        in_force.append(doc)

    remaining: list[Candidate] = []
    for c in survivors:
        replaced_by: Optional[Supersession] = None
        for doc in in_force:
            if doc.doc_id == c.doc_id:
                continue
            mode = _supersedes(doc, c)
            if mode == "none":
                continue
            if c.effective_from and doc.effective_from < c.effective_from:
                res.ignored_supersessions.append(
                    f"{doc.doc_id} claims to supersede {c.doc_id} but is dated earlier ({doc.effective_from} < {c.effective_from}); ignored"
                )
                continue
            if mode == "full":
                replaced_by = Supersession(c, doc, "full")
                break
            res.trace.append(f"step2 {c.doc_id}#{c.section}: partially affected by {doc.doc_id} (narrower clause superseded); kept")
        if replaced_by is None:
            remaining.append(c)
        else:
            res.superseded.append(replaced_by)
            res.exclusions.append(Exclusion(c.cid, c.doc_id, c.section, c.topic, "superseded",
                                            f"explicitly superseded by {replaced_by.by.doc_id} (level {replaced_by.by.authority_level}, "
                                            f"effective {replaced_by.by.effective_from})"))
            res.trace.append(f"step2 {c.doc_id}#{c.section} superseded by {replaced_by.by.doc_id}")
    survivors = remaining

    # ---- Steps 3-5 per topic ---------------------------------------------------------------------------
    topics: dict[str, list[Candidate]] = {}
    for c in survivors:
        topics.setdefault(c.topic, []).append(c)
    for topic, group in topics.items():
        res.outcomes[topic] = _resolve_topic(topic, group, res)

    # Topics that only exist as superseded (replacement text not among the candidates).
    for sup in res.superseded:
        if sup.target.topic not in res.outcomes:
            res.outcomes[sup.target.topic] = TopicOutcome(topic=sup.target.topic, status="none")
    # Record supersession events as (resolved) conflicts for explainability.
    for sup in res.superseded:
        outcome = res.outcomes.get(sup.target.topic)
        if outcome is None:
            continue
        winner_src = None
        for cand in (outcome.winner, *outcome.supporting):
            if cand is not None and cand.doc_id == sup.by.doc_id:
                winner_src = _src(cand, docs)
                break
        if winner_src is None:
            winner_src = ConflictSource(doc_id=sup.by.doc_id, title=sup.by.title, authority_level=sup.by.authority_level,
                                        issuer=sup.by.issuer, effective_from=sup.by.effective_from.isoformat())
        old = _src(sup.target, docs)
        outcome.conflicts.insert(0, ConflictRecord(
            topic=sup.target.topic, kind="supersession", resolved=True, resolution_rule=RULE_TEXT["supersession"],
            winner=winner_src, overridden=[old], explanation=explain("supersession", sup.target.topic, winner_src, [old])))
    return res


def _supersedes(doc: DocInfo, c: Candidate) -> str:
    best = "none"
    for ref in doc.supersedes:
        if ref.doc_id != c.doc_id:
            continue
        if ref.clause is None:
            return "full"
        rel = clause_relation(ref.clause, c.section)
        if rel in ("same", "nested"):
            return "full"
        if rel == "parent":
            best = "partial"
    return best


def _value_groups(group: list[Candidate]) -> list[list[Candidate]]:
    """Cluster candidates that state the same thing.

    Comparable values (rules): equal normalised values cluster together.
    Free text (chunks): all chunks of one document cluster together; documents cluster when their text is near-identical.
    """
    clusters: list[list[Candidate]] = []
    keys: list[Any] = []
    # values first
    for c in group:
        if c.value is not None:
            key = ("v", normalize_value(c.value))
            for i, k in enumerate(keys):
                if k == key:
                    clusters[i].append(c)
                    break
            else:
                keys.append(key)
                clusters.append([c])
    # text candidates: per-document clusters then merge by similarity
    by_doc: dict[str, list[Candidate]] = {}
    for c in group:
        if c.value is None:
            by_doc.setdefault(c.doc_id, []).append(c)
    doc_clusters: list[list[Candidate]] = []
    for doc_cands in by_doc.values():
        toks = set().union(*(_tokens(x.text) for x in doc_cands))
        placed = False
        for cl in doc_clusters:
            ctoks = set().union(*(_tokens(x.text) for x in cl))
            if _jaccard(toks, ctoks) >= _TEXT_SIM_THRESHOLD:
                cl.extend(doc_cands)
                placed = True
                break
        if not placed:
            doc_clusters.append(list(doc_cands))
    clusters.extend(doc_clusters)
    return clusters


def _resolve_topic(topic: str, group: list[Candidate], res: Resolution) -> TopicOutcome:
    docs = res.docs
    ranked = sorted(group, key=lambda c: c.sort_key())
    clusters = _value_groups(ranked)
    clusters.sort(key=lambda cl: min(c.sort_key() for c in cl))
    best_cluster = clusters[0]
    best = sorted(best_cluster, key=lambda c: c.sort_key())[0]

    # --- single distinct statement: nothing to resolve ----------------------------------------------
    if len(clusters) == 1:
        res.trace.append(f"topic '{topic}': {len(group)} candidate(s) agree -> winner {best.doc_id}#{best.section}")
        return TopicOutcome(topic=topic, status="resolved", winner=best,
                            supporting=[c for c in ranked if c is not best])

    # --- scope uncertainty: a statement that might not apply to this student could change the answer ---
    uncertain = [c for cl in clusters[1:] for c in cl if c.scope == "unknown"]
    if best.scope == "unknown" or any(c.sort_key()[:2] <= best.sort_key()[:2] for c in uncertain):
        srcs = [_src(sorted(cl, key=lambda c: c.sort_key())[0], docs) for cl in clusters]
        text = explain("scope_ambiguity", topic, None, srcs)
        res.trace.append(f"topic '{topic}': scope ambiguity between {[s.doc_id for s in srcs]} -> clarification")
        return TopicOutcome(
            topic=topic, status="clarification", tied=[sorted(cl, key=lambda c: c.sort_key())[0] for cl in clusters],
            clarification="Which programme and admission batch do you mean? " + text,
            conflicts=[ConflictRecord(topic=topic, kind="scope_ambiguity", resolved=False,
                                      resolution_rule=RULE_TEXT["scope_ambiguity"], overridden=srcs, explanation=text)],
        )

    # --- steps 3/4/5: compare the best cluster against every other statement ------------------------------
    losers: list[Candidate] = []
    conflicts: list[ConflictRecord] = []
    tied_other: list[Candidate] = []
    for cl in clusters[1:]:
        other = sorted(cl, key=lambda c: c.sort_key())[0]
        if other.authority_level > best.authority_level:
            kind = "authority"
        elif other.authority_level == best.authority_level and (other.effective_from or date.min) < (best.effective_from or date.min):
            kind = "recency"
        else:
            kind = "unresolved"
        if kind == "unresolved":
            tied_other.append(other)
        else:
            losers.extend(cl)
            w, o = _src(best, docs), _src(other, docs)
            conflicts.append(ConflictRecord(topic=topic, kind=kind, resolved=True, resolution_rule=RULE_TEXT[kind],
                                            winner=w, overridden=[o], explanation=explain(kind, topic, w, [o])))
    if tied_other:
        tied = [best] + tied_other
        srcs = [_src(c, docs) for c in tied]
        text = explain("unresolved", topic, None, srcs)
        res.trace.append(f"topic '{topic}': UNRESOLVED between {[s.doc_id for s in srcs]} (same authority and effective date)")
        return TopicOutcome(
            topic=topic, status="unresolved", tied=tied, losers=losers,
            conflicts=conflicts + [ConflictRecord(topic=topic, kind="unresolved", resolved=False,
                                                  resolution_rule=RULE_TEXT["unresolved"], overridden=srcs, explanation=text)],
        )
    for lo in losers:
        res.exclusions.append(Exclusion(lo.cid, lo.doc_id, lo.section, lo.topic, "lower_authority",
                                        f"outranked by {best.doc_id} (level {best.authority_level}, effective {best.effective_from})"))
    res.trace.append(f"topic '{topic}': winner {best.doc_id}#{best.section} (level {best.authority_level}, effective {best.effective_from}); "
                     f"{len(losers)} overridden")
    return TopicOutcome(topic=topic, status="resolved", winner=best,
                        supporting=[c for c in best_cluster if c is not best], losers=losers, conflicts=conflicts)
