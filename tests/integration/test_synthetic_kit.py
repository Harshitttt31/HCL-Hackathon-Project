import pytest

from app.agent.service import AssistantService
from app.database.repositories import RuleRepo
from app.database.seed import seed_database
from app.synthetic.build import build_all
from app.synthetic.corpus import GOLD_RULES
from app.synthetic.validate import validate_dir

LEXICON_PARAMS = {"min_attendance_pct", "condonation_min_attendance_pct", "pass_marks_pct", "sup_max_attempts", "sup_application_deadline",
                  "sup_exam_fee", "placement_min_cgpa", "placement_max_active_backlogs", "scholarship_min_cgpa", "scholarship_application_deadline"}


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    d = tmp_path_factory.mktemp("kit")
    build_all(d / "data")
    return d / "data"


def test_generated_kit_passes_validation(kit):
    rep = validate_dir(kit)
    assert rep["passed"], [c for c in rep["checks"] if not c["passed"]]


def test_generation_is_deterministic(tmp_path):
    build_all(tmp_path / "a", 7)
    build_all(tmp_path / "b", 7)
    for name in ("students.csv", "attendance.csv", "results.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


@pytest.fixture()
def seeded(isolated_env, kit):
    from app.rag.retriever import HybridRetriever
    seed_database(kit)
    return AssistantService()


def norm(v: str) -> str:
    return v[:-2] if v.endswith(".0") else v


def test_rule_extraction_matches_the_gold_rules_exactly(seeded):
    extracted = {(r.source_doc_id, r.parameter, norm(r.value)) for r in RuleRepo().list_all() if r.origin == "extracted" and r.parameter in LEXICON_PARAMS}
    gold = {(g["source_doc_id"], g["parameter"], norm(g["value"])) for g in GOLD_RULES if g["parameter"] in LEXICON_PARAMS}
    assert extracted == gold, (sorted(gold - extracted), sorted(extracted - gold))


def ask(svc, q, sid=None, as_of="2026-10-06"):
    return svc.ask(q, sid, as_of)


def test_designed_boundary_students(seeded):
    # S1001 CS301 77.5% vs the 80% circular: condonation band; S1005 (M.Tech) 75% exactly is eligible; S1008 60% is not eligible
    a = ask(seeded, "Am I eligible to appear in the end-semester exam for CS301?", "S1001")
    assert "not eligible outright" in a["answer"] and a["applied_rules"][0]["rule_id"] in ("ATT-MIN-02",) or "ACAD-2026-08" in a["applied_rules"][0]["source_doc_id"]
    b = ask(seeded, "Am I eligible to appear in the end-semester exam for MC201?", "S1005")
    assert b["answer"].startswith("Yes") and "75" in b["answer"]
    c = ask(seeded, "Am I eligible to appear in the end-semester exam for CS301?", "S1008")
    assert c["answer"].startswith("No")
    d = ask(seeded, "Am I eligible to appear in the end-semester exam for CS201?", "S1004")
    assert "74.58" in d["answer"] and "condonation" in d["answer"]


def test_scope_ambiguity_for_anonymous_question(seeded):
    r = ask(seeded, "What is the minimum attendance required?")
    assert r["answer_type"] == "clarification_needed" and "programme" in r["answer"].lower()
    r2 = ask(seeded, "What is the minimum attendance required for B.Tech CSE students?")
    assert r2["answer_type"] == "retrieved_fact" and "80" in r2["answer"]


def test_upcoming_change_not_applied_but_reported(seeded):
    r = ask(seeded, "What is the minimum CGPA needed for placements for B.Tech CSE?")
    assert "6.5" in r["answer"] and "7.0" in r["answer"] and "not in force" in r["answer"]
    later = ask(seeded, "What is the minimum CGPA needed for placements for B.Tech CSE?", as_of="2027-02-01")
    assert "7.0" in later["answer"].split("Note")[0]


def test_conflict_and_unofficial_sources(seeded):
    r = ask(seeded, "What is the last date to apply for the merit scholarship?")
    assert r["answer_type"] == "conflict_flagged" and {c["doc_id"] for c in r["citations"]} == {"SCH-NOTICE-A", "SCH-NOTICE-B"}
    f = ask(seeded, "Is 60% attendance enough for B.Tech CSE according to seniors?")
    assert "STUDENT-FORUM-NOTES" not in [c["doc_id"] for c in f["citations"]]


def test_ocr_document_answers(seeded):
    r = ask(seeded, "How many books can a student borrow from the library and for how many days?")
    assert r["answer_type"] == "retrieved_fact" and r["citations"][0]["doc_id"] == "LIBRARY-RULES" and "14" in r["answer"]
