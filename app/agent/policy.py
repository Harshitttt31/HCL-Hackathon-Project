"""Document path: retrieval -> candidates -> source precedence -> evidence sufficiency -> a quoted answer.

Retrieval supplies candidates. It decides nothing: authority, supersession, scope and dates are applied by the
precedence engine, and an evidence gate refuses to answer when the sources do not actually contain the answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.agent.handlers import conflict_dicts, conflict_notes, facts_from, upcoming_notes
from app.agent.state import Finding
from app.core.config import get_settings
from app.rag.retriever import EvidenceChunk, HybridRetriever
from app.database.repositories import ChunkRepo
from app.rag.text import stem, tokenize
from app.sources.citations import build_citation
from app.sources.models import DocInfo
from app.sources.precedence import Candidate, Resolution, ResolutionContext, TopicOutcome, resolve
from app.sources.temporal import to_date

_FACT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|percent|days?|hours?|hrs?|weeks?|months?|marks?|credits?|classes|rs\.?|inr)|"
    r"(?:rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)|(\d{1,2}:\d{2})|(\d{4}-\d{2}-\d{2})|"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{4})", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|\s+(?=Row \d+:)")
_PROCEDURAL_RE = re.compile(r"^\s*(?:how (?:do|can|should|would|does|to)\b|what (?:is|are) the (?:procedure|process|steps)\b)", re.IGNORECASE)
_PROCEDURE_HEADING_RE = re.compile(r"\b(?:how to|procedure|process|steps|application process|apply)\b", re.IGNORECASE)
_PREMISE_RE = re.compile(r"\b(?:says?|said|told|heard|claims?|mentioned|rumou?rs?|according to)\b", re.IGNORECASE)
_PLAIN_QUERY_STOP = frozenset("what is are the of for how much does do which in to".split())


@dataclass
class PolicyOutcome:
    findings: list[Finding] = field(default_factory=list)
    evidence: list[EvidenceChunk] = field(default_factory=list)
    coverage: float = 0.0
    missing_terms: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    trace: list[str] = field(default_factory=list)
    injection_dropped: int = 0
    resolution: Optional[Resolution] = None


def chunk_value(text: str) -> Optional[str]:
    """Canonical, human-readable signature of the numbers, times and dates in a chunk (used to compare and to display)."""
    facts = set()
    for m in _FACT_RE.finditer(text or ""):
        f = re.sub(r"\s+", " ", m.group(0).lower().replace("percent", "%")).strip()
        facts.add(re.sub(r"(\d)\s+%", r"\1%", f))
    return "; ".join(sorted(facts)) if facts else None


def _title_tokens(c: EvidenceChunk) -> set[str]:
    return set(tokenize(c.section_title or " ".join(c.section_path[-1:]) or ""))


def _jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _chunk_parameter(text: str) -> Optional[str]:
    """The rule parameter a chunk states (from the parameter lexicon), if any: the most reliable sign that two chunks are
    about the same thing, whatever their headings say."""
    from app.rag.claims import get_lexicon
    for key, cfg in get_lexicon().params.items():
        if any(rx.search(text) for rx in cfg["_patterns"]):
            return key
    return None


def assign_topics(chunks: list[EvidenceChunk]) -> dict[str, str]:
    """chunk_id -> topic id. Chunks state the same rule parameter, or (without one) share heading and wording."""
    topics: list[tuple[str, set[str], set[str], Optional[str]]] = []  # (topic_id, title tokens, text tokens, parameter)
    mapping: dict[str, str] = {}
    for c in chunks:
        tt, xt, par = _title_tokens(c), set(tokenize(c.text)), _chunk_parameter(c.text)
        found = None
        for tid, ttoks, xtoks, tpar in topics:
            if par and tpar:
                if par == tpar:
                    found = tid
                    break
                continue
            # no parameter on one side: same subject only if the wording is also similar (two clauses under one heading
            # can be about different things)
            if (tt and ttoks and _jac(tt, ttoks) >= 0.6 and _jac(xt, xtoks) >= 0.2) or (tt & ttoks and _jac(xt, xtoks) >= 0.3):
                found = tid
                break
        if found is None:
            found = f"topic{len(topics) + 1}:" + "-".join(sorted(tt))[:40]
            topics.append((found, tt, xt, par))
        mapping[c.chunk_id] = found
    return mapping


def to_candidates(chunks: list[EvidenceChunk], topics: dict[str, str]) -> list[Candidate]:
    cands = []
    for c in chunks:
        cands.append(Candidate(
            cid=c.chunk_id, doc_id=c.doc_id, section=c.section or c.section_title, topic=topics[c.chunk_id],
            authority_level=c.authority_level, effective_from=to_date(c.effective_from), effective_to=to_date(c.effective_to),
            scope_programmes=c.scope_programmes, scope_batches=c.scope_batches, value=chunk_value(c.text), text=c.text,
            payload=c, page=c.page))
    return cands


def _drop_valueless_when_topic_has_values(cands: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    top_cid = max(cands, key=lambda c: getattr(c.payload, "score", 0.0)).cid if cands else None
    by_topic: dict[str, list[Candidate]] = {}
    for c in cands:
        by_topic.setdefault(c.topic, []).append(c)
    keep, aside = [], []
    valued_docs = {c.doc_id for c in cands if c.value is not None}
    # an introductory clause with no stated value yields to a clause of its own document that does state one
    shadowed = {c.cid for c in cands if c.value is None and c.doc_id in valued_docs and c.cid != top_cid}
    aside += [c for c in cands if c.cid in shadowed]
    cands = [c for c in cands if c.cid not in shadowed]
    by_topic = {}
    for c in cands:
        by_topic.setdefault(c.topic, []).append(c)
    for group in by_topic.values():
        if any(c.value is not None for c in group):
            keep += [c for c in group if c.value is not None or c.cid == top_cid]
            aside += [c for c in group if c.value is None and c.cid != top_cid]
        else:
            keep += group
    return keep, aside


def _split_sentences(text: str) -> list[str]:
    protected = re.sub(r"\b(Rs|Dr|Prof|No|Sr)\. ", lambda m: m.group(1) + ".\x00", text)
    return [x.replace("\x00", " ").strip() for x in _SENT_SPLIT.split(protected) if x.strip()]


def best_sentences(text: str, query: str, limit: int = 3) -> str:
    sents = _split_sentences(text)
    if text.lstrip().startswith("Table:"):
        # a table chunk: keep the caption and column line, then the row(s) that match the question
        head = [x for x in sents if not x.startswith("Row ")][:2]
        rows = [x for x in sents if x.startswith("Row ")]
        words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in _PLAIN_QUERY_STOP}
        stems = {stem(w) for w in words}

        def hit(row: str) -> int:
            rw = set(re.findall(r"[a-z0-9]+", row.lower()))
            return len(words & rw) + len(stems & {stem(w) for w in rw})

        scored = sorted(rows, key=lambda r: (-hit(r), rows.index(r)))
        best_hit = hit(scored[0]) if scored else 0
        chosen = [r for r in scored[:2] if hit(r) == best_hit and best_hit > 0] or scored[:2]
        return " ".join(head + chosen)
    q = set(tokenize(query))
    if len(sents) <= limit:
        return " ".join(sents)
    scored_i = sorted(range(len(sents)), key=lambda i: (-len(q & set(tokenize(sents[i]))), i))
    chosen_i = sorted(scored_i[:limit])
    return " ".join(sents[i] for i in chosen_i)


def answer_from_documents(retriever: HybridRetriever, question: str, as_of: date, programme: Optional[str], batch: Optional[int],
                          docs: dict[str, DocInfo], top_k: Optional[int] = None) -> PolicyOutcome:
    s = get_settings()
    out = PolicyOutcome()
    hits, stats = retriever.retrieve_with_stats(question, top_k=top_k or s.top_k)
    out.stats = stats.model_dump()
    trusted = [h for h in hits if not h.injection_suspected]
    out.injection_dropped = len(hits) - len(trusted)
    if out.injection_dropped:
        out.trace.append(f"{out.injection_dropped} retrieved chunk(s) contain instruction-like text and were excluded from evidence")
    if not trusted:
        out.findings.append(Finding("not_found", "not_found", _not_found_text(question, []), []))
        out.trace.append("no usable chunks retrieved")
        return out
    expanded = retriever.expand_references(trusted[:3])
    pool = trusted + [e for e in expanded if e.chunk_id not in {t.chunk_id for t in trusted}]
    out.trace.append(f"retrieved {len(hits)} chunk(s); {len(expanded)} added through cross-references")

    # a document that surfaced only through a clause with no stated value (an introduction, a heading) may hold the actual
    # rule elsewhere: fetch its best-matching clauses so that it can compete on authority with the other documents
    by_doc: dict[str, list[EvidenceChunk]] = {}
    for e in pool:
        by_doc.setdefault(e.doc_id, []).append(e)
    thin = sorted(d for d, items in by_doc.items() if all(chunk_value(i.text) is None for i in items))
    if thin:
        more, _ = retriever.retrieve_with_stats(question, top_k=3, doc_ids=thin)
        more = [m for m in more if not m.injection_suspected and chunk_value(m.text) is not None
                and m.chunk_id not in {p.chunk_id for p in pool}]
        if more:
            out.trace.append(f"added {len(more)} clause(s) with stated values from {thin}, which surfaced only through headings")
            pool = pool + more

    ctx = ResolutionContext(as_of=as_of, programme=programme, batch_year=batch)
    topics = assign_topics(pool)
    cands = to_candidates(pool, topics)
    keep, aside = _drop_valueless_when_topic_has_values(cands)
    res = resolve(keep, ctx, docs)

    # supersession expansion: a retrieved clause may have been replaced by a document that retrieval did not surface
    present = {c.doc_id for c in cands}
    missing_docs = res.superseders_without_candidates(present)
    if missing_docs:
        extra, _ = retriever.retrieve_with_stats(question, top_k=3, doc_ids=sorted(missing_docs))
        extra = [e for e in extra if not e.injection_suspected]
        if extra:
            out.trace.append(f"supersession expansion: added {len(extra)} chunk(s) from {sorted(missing_docs)}")
            pool = pool + [e for e in extra if e.chunk_id not in {p.chunk_id for p in pool}]
            topics = assign_topics(pool)
            # attach the replacement text to the topic of the clause it replaces
            for sup in res.superseded:
                for e in extra:
                    if e.doc_id == sup.by.doc_id:
                        topics[e.chunk_id] = sup.target.topic
            cands = to_candidates(pool, topics)
            keep, aside = _drop_valueless_when_topic_has_values(cands)
            res = resolve(keep, ctx, docs)
    out.resolution = res
    out.trace.extend(res.trace[:8])
    out.evidence = pool

    # ---- choose the topic that best matches the question --------------------------------------------------------------
    score = {c.chunk_id: c.score for c in pool}

    def topic_score(o: TopicOutcome) -> float:
        members = [x for x in (o.winner, *o.supporting, *o.tied) if x is not None]
        return max((score.get(m.cid, 0.0) for m in members), default=0.0)

    live = [o for o in res.outcomes.values() if o.status != "none"]
    if not live:
        up = res.upcoming_changes()
        if up:
            text = ("The documents I found describe a rule that is not yet in force on " + as_of.isoformat() + ". " + upcoming_notes(up))
            out.findings.append(Finding("not_found", "not_found", text, facts_from(text), [], [], [], [u.model_dump(mode="json") for u in up]))
        elif res.informational:
            names = ", ".join(sorted({c.doc_id for c in res.informational}))
            text = (f"Only informational or unofficial material ({names}) mentions this, and it cannot be used as an authoritative source. "
                    "Please check with the issuing office.")
            out.findings.append(Finding("not_found", "not_found", text, facts_from(text),
                                        [build_citation(docs.get(c.doc_id), c.doc_id, c.section, c.page, c.text, "informational").model_dump(mode="json")
                                         for c in res.informational[:2]]))
        else:
            out.findings.append(Finding("not_found", "not_found", _not_found_text(question, pool), []))
        return out
    live.sort(key=lambda o: -topic_score(o))
    best = live[0]

    # ---- evidence sufficiency gate -----------------------------------------------------------------------------------------
    members = [x for x in (best.winner, *best.supporting, *best.tied) if x is not None]
    ev = [m.payload for m in sorted(members, key=lambda m: -score.get(m.cid, 0.0))]
    cov, missing = retriever.coverage(coverage_query(question), ev or pool)
    out.coverage, out.missing_terms = cov, missing
    out.trace.append(f"evidence coverage {cov:.2f} (min {s.min_term_coverage}, full {s.partial_coverage_below}); missing terms: {missing[:6]}")
    subject_missing = [t for t in missing if t in retriever.subject_terms]
    if subject_missing and cov < s.partial_coverage_below:
        out.trace.append(f"subject term(s) {subject_missing} of the question never appear in the best evidence: abstaining")
        out.findings.append(Finding("not_found", "not_found", _not_found_text(question, pool, missing), []))
        return out
    if cov < s.min_term_coverage:
        out.findings.append(Finding("not_found", "not_found", _not_found_text(question, pool, missing), []))
        return out

    conflicts = conflict_dicts(best.conflicts)
    cites = []
    if best.status == "clarification":
        out.findings.append(Finding("clarification", "clarification_needed", best.clarification or "Which programme and batch do you mean?",
                                    facts_from(best.clarification or ""), [], [], conflicts))
        return out
    if best.status == "unresolved":
        tied = best.tied
        quotes = "; ".join(f"{t.doc_id} section {t.section} says: \"{best_sentences(t.text, question, 1)}\"" for t in tied)
        text = (f"The sources disagree and the precedence policy cannot decide which applies: {quotes}. "
                + conflict_notes([c for c in conflicts if not c.get('resolved')]))
        cites = [build_citation(docs.get(t.doc_id), t.doc_id, t.section, t.page, t.text, "conflicting").model_dump(mode="json") for t in tied]
        out.findings.append(Finding("conflict", "conflict_flagged", text, facts_from(text), cites, [], conflicts))
        return out

    w = best.winner
    assert w is not None
    # several chunks of the same document may sit in one topic (a paragraph and the table under it): quote the one
    # that scores highest for this question. Authority and supersession were already decided across documents.
    same_doc = [m for m in (w, *best.supporting) if m.doc_id == w.doc_id]
    w = max(same_doc, key=lambda m: getattr(m.payload, "score", 0.0))
    w = _prefer_table(w, best, pool)
    chunk = w.payload
    steps = _procedure_steps(question, w.doc_id)
    if steps:
        quote = " ".join(f"({i}) {r.text.strip()}" for i, r in enumerate(steps, 1))
        sec = f", section {(steps[0].section_number or '').split('.')[0]} ({steps[0].section_title})"
        chunk = chunk.model_copy(update={"text": quote, "section": steps[0].section_number, "page": steps[0].page})
        w = Candidate(cid=steps[0].chunk_id, doc_id=w.doc_id, section=steps[0].section_number or w.section, topic=w.topic,
                      authority_level=w.authority_level, effective_from=w.effective_from, effective_to=w.effective_to,
                      scope_programmes=w.scope_programmes, scope_batches=w.scope_batches, value=None, text=quote, payload=chunk, page=steps[0].page)
    else:
        quote = best_sentences(chunk.text, question, 3)
        sec = f", section {chunk.section}" if chunk.section else ""
    text = f"According to {chunk.title or chunk.doc_id}{sec}: {quote}"
    if cov < s.partial_coverage_below and missing:
        text += f" Note: the documents do not mention: {', '.join(missing[:5])}."
    note = conflict_notes([c for c in conflicts if c.get("resolved")])
    if note:
        text += " " + note
    ups = _relevant_upcoming(res, w, best, chunk)
    if ups:
        text += " " + upcoming_notes(ups)
    cites = [build_citation(docs.get(w.doc_id), w.doc_id, w.section, w.page, chunk.text, "supports").model_dump(mode="json")]
    for sp in best.supporting[:2]:
        cites.append(build_citation(docs.get(sp.doc_id), sp.doc_id, sp.section, sp.page, sp.text, "supports").model_dump(mode="json"))
    for lo in best.losers[:2]:
        cites.append(build_citation(docs.get(lo.doc_id), lo.doc_id, lo.section, lo.page, lo.text, "superseded" if any(
            x.target.cid == lo.cid for x in res.superseded) else "overridden").model_dump(mode="json"))
    seen, uniq = set(), []
    for c in cites:
        k = (c["doc_id"], c["section"], c["role"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    out.findings.append(Finding("policy_text", "retrieved_fact", text, facts_from(text + " " + chunk.text), uniq, [], conflicts,
                                [u.model_dump(mode="json") for u in ups],
                                explanation=f"{w.doc_id} section {w.section} (authority level {w.authority_level}, effective {w.effective_from}) "
                                            f"was selected by the source precedence policy; retrieval score {chunk.score:.2f}."))
    return out


def coverage_query(question: str) -> str:
    """The part of the question that asks for something. A quoted claim ("seniors say 60% is enough. Is that true?") names its
    source and its number; those are what is being checked, not what must be found in the documents."""
    if not _PREMISE_RE.search(question):
        return question
    q = re.sub(r"\b\d+(?:\.\d+)?\s*(?:%|percent)", " ", question)
    q = re.sub(r"\b(?:the\s+)?[\w\s]{0,30}?\b(?:says?|said|told me|claims?|mentioned|heard)\b(?:\s+that)?", " ", q, count=1, flags=re.IGNORECASE)
    return q


def _prefer_table(w: Candidate, best: TopicOutcome, pool: list[EvidenceChunk]) -> Candidate:
    """A paragraph that says "as set out in the table below" is not the answer; the table under it is."""
    chunk: EvidenceChunk = w.payload
    if not re.search(r"\b(?:table|below|following)\b", chunk.text, re.IGNORECASE):
        return w
    parent = (chunk.section or "").split(".")[0]
    for m in (w, *best.supporting):
        c: EvidenceChunk = m.payload
        if m.doc_id == w.doc_id and (c.kind == "table" or c.text.lstrip().startswith("Table:")):
            return m
    for c in pool:
        if c.doc_id == w.doc_id and (c.kind == "table" or c.text.lstrip().startswith("Table:")) and (c.section or "").split(".")[0] == parent:
            fake = Candidate(cid=c.chunk_id, doc_id=c.doc_id, section=c.section or c.section_title, topic=w.topic,
                             authority_level=c.authority_level, effective_from=w.effective_from, effective_to=w.effective_to,
                             scope_programmes=c.scope_programmes, scope_batches=c.scope_batches, value=chunk_value(c.text), text=c.text,
                             payload=c, page=c.page)
            return fake
    return w


def _procedure_steps(question: str, doc_id: str) -> list[Any]:
    """For "how do I ..." questions: the steps listed under a procedure heading of the selected document, in order."""
    if not _PROCEDURAL_RE.search(question):
        return []
    rows = [r for r in ChunkRepo().by_doc(doc_id) if r.section_title and _PROCEDURE_HEADING_RE.search(r.section_title)
            and not r.injection_suspected]
    return sorted(rows, key=lambda r: r.ordinal)


def _relevant_upcoming(res: Resolution, winner: Candidate, best: TopicOutcome, chunk: EvidenceChunk) -> list[Any]:
    """Upcoming (not yet effective) changes that are about the same subject as the answer, not any future rule that was retrieved."""
    base = set(tokenize(chunk.text))
    keep = []
    for u in res.upcoming_changes():
        cand = next((c for c in res.upcoming if c.doc_id == u.doc_id and c.section == u.section), None)
        if cand is None:
            continue
        if cand.topic == best.topic or _jac(set(tokenize(cand.text)), base) >= 0.2:
            keep.append(u)
    return keep


def _not_found_text(question: str, pool: list[EvidenceChunk], missing: Optional[list[str]] = None) -> str:
    base = "I could not find this in the authorised university documents I have, so I won't guess."
    if missing:
        base += f" The closest text does not cover: {', '.join(missing[:5])}."
    return base + " Please contact the relevant university office to confirm."
