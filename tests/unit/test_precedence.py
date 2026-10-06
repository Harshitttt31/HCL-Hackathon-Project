"""Annex A: Source Precedence Policy. These tests encode the worked example and every resolution step."""
from datetime import date

import pytest

from app.sources.models import DocInfo, parse_supersedes
from app.sources.precedence import Candidate, ResolutionContext, clause_relation, resolve

AS_OF = date(2026, 10, 6)


def doc(doc_id, level, eff_from, eff_to=None, supersedes="", progs="ALL", batches="ALL", issuer="Office"):
    return DocInfo(doc_id=doc_id, title=doc_id.title(), issuer=issuer, authority_level=level, doc_type="circular",
                   version="1", effective_from=date.fromisoformat(eff_from),
                   effective_to=date.fromisoformat(eff_to) if eff_to else None,
                   supersedes=parse_supersedes(supersedes), scope_programmes=progs, scope_batches=batches)


def cand(d: DocInfo, value, section="7.2", topic="min_attendance_pct", progs=None, batches=None):
    return Candidate(cid=f"{d.doc_id}:{section}", doc_id=d.doc_id, section=section, topic=topic, authority_level=d.authority_level,
                     effective_from=d.effective_from, effective_to=d.effective_to,
                     scope_programmes=progs or d.scope_programmes, scope_batches=batches or d.scope_batches, value=value)


def run(docs, cands, programme="B.Tech CSE", batch=2024, as_of=AS_OF):
    return resolve(cands, ResolutionContext(as_of=as_of, programme=programme, batch_year=batch), {d.doc_id: d for d in docs})


# ---------------------------------------------------------------------------------------------
def test_annex_a_worked_example():
    reg = doc("ACAD-REG-2024", 1, "2024-07-01")
    circ = doc("ACAD-2026-08", 2, "2026-08-01", supersedes="ACAD-REG-2024#7.2")
    faq = doc("DEPT-FAQ", 4, "2026-09-15")
    r = run([reg, circ, faq], [cand(reg, ">=75%"), cand(circ, ">=80%", section="2"), cand(faq, ">=65%", section="3")])
    out = r.outcomes["min_attendance_pct"]
    assert out.status == "resolved"
    assert out.winner.doc_id == "ACAD-2026-08" and out.winner.value == ">=80%"
    kinds = {c.kind for c in out.conflicts}
    assert "supersession" in kinds and "authority" in kinds  # the FAQ conflict is noted, not silently dropped
    reasons = {e.doc_id: e.reason for e in r.exclusions}
    assert reasons["ACAD-REG-2024"] == "superseded"
    assert reasons["DEPT-FAQ"] == "lower_authority"


def test_future_document_is_excluded_and_reported_as_upcoming():
    reg = doc("REG", 1, "2024-07-01")
    circ = doc("CIRC", 2, "2026-11-01", supersedes="REG#7.2")
    r = run([reg, circ], [cand(reg, ">=75%"), cand(circ, ">=80%", section="2")])
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "REG"  # circular is not current yet
    assert [u.doc_id for u in r.upcoming_changes()] == ["CIRC"]
    assert r.superseded == []  # an upcoming document must not supersede anything yet


def test_expired_document_is_excluded():
    old = doc("OLD", 2, "2025-01-01", eff_to="2026-06-30")
    new = doc("NEW", 2, "2026-07-01")
    r = run([old, new], [cand(old, ">=70%"), cand(new, ">=75%")])
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "NEW"
    assert {e.doc_id: e.reason for e in r.exclusions}["OLD"] == "expired"


def test_effective_to_boundary_is_inclusive():
    d = doc("D", 1, "2024-01-01", eff_to="2026-10-06")
    assert run([d], [cand(d, ">=75%")]).outcomes["min_attendance_pct"].status == "resolved"
    d2 = doc("D2", 1, "2026-10-06")
    assert run([d2], [cand(d2, ">=75%")]).outcomes["min_attendance_pct"].status == "resolved"


def test_higher_authority_beats_newer_lower_authority():
    reg = doc("REG", 1, "2024-07-01")
    notice = doc("DEPT", 3, "2026-09-01")
    r = run([reg, notice], [cand(reg, ">=75%"), cand(notice, ">=85%")])
    out = r.outcomes["min_attendance_pct"]
    assert out.winner.doc_id == "REG"
    assert out.conflicts[0].kind == "authority" and out.conflicts[0].resolved


def test_same_authority_later_effective_date_wins():
    a = doc("A", 2, "2025-01-01")
    b = doc("B", 2, "2026-01-01")
    out = run([a, b], [cand(a, ">=70%"), cand(b, ">=75%")]).outcomes["min_attendance_pct"]
    assert out.winner.doc_id == "B" and out.conflicts[0].kind == "recency"


def test_same_authority_same_date_is_unresolved_and_cites_both():
    a = doc("CIRC-EXAM", 2, "2026-09-01", issuer="Controller of Examinations")
    b = doc("CIRC-REG", 2, "2026-09-01", issuer="Registrar")
    r = run([a, b], [cand(a, "15Nov", topic="deadline"), cand(b, "22Nov", topic="deadline")])
    out = r.outcomes["deadline"]
    assert out.status == "unresolved" and out.winner is None
    assert {c.doc_id for c in out.tied} == {"CIRC-EXAM", "CIRC-REG"}
    assert "contact" in out.conflicts[-1].explanation.lower()
    assert r.status == "unresolved"


def test_level5_never_overrides_and_is_informational_only():
    reg = doc("REG", 1, "2024-07-01")
    forum = doc("FORUM", 5, "2026-09-30")
    r = run([reg, forum], [cand(reg, ">=75%"), cand(forum, ">=40%")])
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "REG"
    assert [c.doc_id for c in r.informational] == ["FORUM"]


def test_level5_alone_yields_no_winner():
    forum = doc("FORUM", 5, "2026-09-30")
    r = run([forum], [cand(forum, ">=40%")])
    assert r.winners() == [] and r.status == "none" and len(r.informational) == 1


def test_supersession_ignored_when_issuer_is_level_3_or_lower():
    reg = doc("REG", 1, "2024-07-01")
    notice = doc("DEPT", 3, "2026-09-01", supersedes="REG#7.2")
    r = run([reg, notice], [cand(reg, ">=75%"), cand(notice, ">=85%")])
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "REG"
    assert r.superseded == []
    assert any("ignored" in s for s in r.ignored_supersessions)


def test_clause_level_supersession_leaves_other_clauses_alone():
    reg = doc("REG", 1, "2024-07-01")
    circ = doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2")
    r = run([reg, circ], [cand(reg, ">=75%", section="7.2"), cand(reg, ">=65%", section="7.4", topic="condonation_min_attendance_pct")])
    assert r.outcomes["condonation_min_attendance_pct"].winner.doc_id == "REG"
    assert [s.target.section for s in r.superseded] == ["7.2"]


def test_whole_document_supersession():
    old = doc("REG-2021", 1, "2021-07-01")
    new = doc("REG-2024", 1, "2024-07-01", supersedes="REG-2021")
    r = run([old, new], [cand(old, ">=70%"), cand(new, ">=75%")])
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "REG-2024"


def test_supersession_without_replacement_text_requests_expansion():
    reg = doc("REG", 1, "2024-07-01")
    circ = doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2")
    r = run([reg, circ], [cand(reg, ">=75%")])  # circular text was not retrieved
    assert r.winners() == []
    assert r.superseders_without_candidates({"REG"}) == {"CIRC"}


def test_scope_excludes_other_programme():
    mt = doc("MT-REG", 1, "2024-07-01", progs="M.Tech")
    bt = doc("BT-REG", 1, "2024-07-01", progs="B.Tech")
    r = run([mt, bt], [cand(mt, ">=70%"), cand(bt, ">=75%")], programme="B.Tech CSE")
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "BT-REG"
    assert {e.doc_id: e.reason for e in r.exclusions}["MT-REG"] == "out_of_scope"


def test_batch_scope_selects_the_regulation_for_the_batch():
    r21 = doc("REG-2021", 1, "2021-07-01", batches="2021-2023")
    r24 = doc("REG-2024", 1, "2024-07-01", batches="2024+")
    for batch, expected in ((2023, "REG-2021"), (2024, "REG-2024")):
        r = run([r21, r24], [cand(r21, ">=70%"), cand(r24, ">=75%")], batch=batch)
        assert r.outcomes["min_attendance_pct"].winner.doc_id == expected


def test_unknown_programme_with_differing_values_asks_for_clarification():
    mt = doc("MT-REG", 1, "2024-07-01", progs="M.Tech")
    bt = doc("BT-REG", 1, "2024-07-01", progs="B.Tech")
    r = run([mt, bt], [cand(mt, ">=70%"), cand(bt, ">=75%")], programme=None, batch=None)
    out = r.outcomes["min_attendance_pct"]
    assert out.status == "clarification" and "programme" in out.clarification.lower()


def test_unknown_programme_does_not_ask_when_higher_authority_decides_anyway():
    circ = doc("CIRC", 2, "2026-08-01")
    cse = doc("CSE-NOTICE", 3, "2026-09-01", progs="B.Tech CSE")
    r = run([circ, cse], [cand(circ, ">=80%"), cand(cse, ">=85%")], programme=None, batch=None)
    assert r.outcomes["min_attendance_pct"].status == "resolved"
    assert r.outcomes["min_attendance_pct"].winner.doc_id == "CIRC"


def test_agreeing_documents_are_not_a_conflict():
    a = doc("A", 1, "2024-01-01")
    b = doc("B", 4, "2025-01-01")
    out = run([a, b], [cand(a, ">=75%"), cand(b, ">= 75.0 %")]).outcomes["min_attendance_pct"]
    assert out.status == "resolved" and out.conflicts == [] and out.winner.doc_id == "A"


def test_text_candidates_cluster_per_document():
    a = doc("A", 1, "2024-01-01")
    b = doc("B", 4, "2025-01-01")
    c1 = cand(a, None, section="5", topic="how to apply"); c1.text = "Submit form S-1 to the examination cell."
    c2 = cand(a, None, section="5.1", topic="how to apply"); c2.text = "Attach the fee receipt."
    c3 = cand(b, None, section="2", topic="how to apply"); c3.text = "Apply online through the portal and pay the fee."
    out = run([a, b], [c1, c2, c3]).outcomes["how to apply"]
    assert out.winner.doc_id == "A" and out.conflicts[0].kind == "authority"


def test_clause_relation():
    assert clause_relation("7.2", "7.2") == "same"
    assert clause_relation("7.2", "7.2.1") == "nested"
    assert clause_relation("Clause 7.2", "7.2(a)") == "nested"
    assert clause_relation("7.2(a)", "7.2") == "parent"
    assert clause_relation("7.2", "7.21") == "none"
    assert clause_relation("7.2", "") == "none"


def test_resolution_is_deterministic_regardless_of_candidate_order():
    reg = doc("REG", 1, "2024-07-01")
    circ = doc("CIRC", 2, "2026-08-01", supersedes="REG#7.2")
    faq = doc("FAQ", 4, "2026-09-15")
    cands = [cand(reg, ">=75%"), cand(circ, ">=80%", section="2"), cand(faq, ">=65%", section="3")]
    a = run([reg, circ, faq], cands).outcomes["min_attendance_pct"].winner.doc_id
    b = run([reg, circ, faq], list(reversed(cands))).outcomes["min_attendance_pct"].winner.doc_id
    assert a == b == "CIRC"
