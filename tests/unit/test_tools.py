"""Tools read thresholds from the rule registry, are scoped to the context student, and never raise."""
from datetime import date

import pytest

from app.tools.base import Services, ToolContext
from app.tools.registry import build_registry
from tests.helpers import seed_minimal

AS_OF = date(2026, 10, 6)


@pytest.fixture()
def env(isolated_env):
    seed_minimal()
    return build_registry()


def ctx(student="S1001", as_of=AS_OF):
    return ToolContext(student_id=student, as_of=as_of, services=Services())


def test_get_attendance_matches_guide_shape(env):
    call = env.call("get_attendance", ctx(), {"course_code": "CS201"})
    assert call.status == "ok" and call.input == {"course_code": "CS201"}
    out = call.output
    assert (out["classes_held"], out["classes_attended"], out["attendance_pct"]) == (40, 31, 77.5)


def test_tool_cannot_be_pointed_at_another_student(env):
    call = env.call("get_attendance", ctx(), {"course_code": "CS201", "student_id": "S1002"})
    assert call.status == "error" and "student_id" in call.error and call.output is None


def test_no_identity_is_denied(env):
    call = env.call("get_attendance", ctx(student=None), {})
    assert call.status == "denied"


def test_unknown_identity_is_not_found(env):
    assert env.call("get_student_profile", ctx(student="S4444"), {}).status == "not_found"


def test_missing_records_are_not_found_not_crashes(env):
    assert env.call("get_attendance", ctx(), {"course_code": "CS999"}).status == "not_found"
    assert env.call("get_results", ctx("S1001"), {"course_code": "CS301"}).status == "not_found"
    assert env.call("get_course", ctx(), {"course_code": "ZZ999"}).status == "not_found"


def test_invalid_input_is_reported_not_raised(env):
    assert env.call("get_attendance", ctx(), {"course_code": "not a code!"}).status == "error"
    assert env.call("calculate_attendance", ctx(), {"classes_attended": 5, "classes_held": 0}).status == "error"
    assert env.call("nope", ctx(), {}).status == "error"


def test_get_rule_applies_precedence_today_and_before_circular(env):
    today = env.call("get_rule", ctx(), {"parameter": "min_attendance_pct"}).output
    assert today["status"] == "resolved" and today["rule"]["rule_id"] == "ATT-MIN-02" and today["rule"]["display"] == ">=80%"
    kinds = {c["kind"] for c in today["conflicts"]}
    assert {"supersession", "authority"} <= kinds
    before = env.call("get_rule", ctx(as_of=date(2026, 7, 15)), {"parameter": "min_attendance_pct"}).output
    assert before["rule"]["rule_id"] == "ATT-MIN-01"
    assert {u["doc_id"] for u in before["upcoming"]} == {"ACAD-2026-08", "DEPT-FAQ"}  # both are future-dated on 2026-07-15


def test_regular_eligibility_uses_current_threshold(env):
    # S1001 CS201 = 77.5%: eligible under the 75% regulation, NOT under the 80% circular in force on 2026-10-06
    now = env.call("check_exam_eligibility", ctx(), {"course_code": "CS201"}).output
    assert now["result"] == "ELIGIBLE_WITH_CONDONATION" and now["rule_id"] == "ATT-MIN-02"
    earlier = env.call("check_exam_eligibility", ctx(as_of=date(2026, 7, 1)), {"course_code": "CS201"}).output
    assert earlier["result"] == "ELIGIBLE" and earlier["rule_id"] == "ATT-MIN-01"


def test_exactly_at_threshold_is_eligible(env):
    # S1001 CS202 = 40/50 = 80.0% exactly
    out = env.call("check_exam_eligibility", ctx(), {"course_code": "CS202"}).output
    assert out["result"] == "ELIGIBLE" and out["calculation"]["value"] == 80.0


def test_one_class_below_threshold_is_not_eligible_without_condonation(env):
    # S1002 CS202: 26/40 = 65.0% -> below 80 but equals the 65% condonation limit -> condonation
    out = env.call("check_exam_eligibility", ctx("S1002"), {"course_code": "CS202"}).output
    assert out["result"] == "ELIGIBLE_WITH_CONDONATION"


def test_supplementary_eligibility_for_failed_course(env):
    out = env.call("check_exam_eligibility", ctx("S1002"), {"course_code": "CS201", "exam_type": "SUPPLEMENTARY"}).output
    assert out["result"] == "ELIGIBLE"  # 33/40 = 82.5% >= 80, FAIL result, attempt 2 <= 3
    names = {c["name"]: c["passed"] for c in out["checks"]}
    assert all(names.values()) and len(names) == 3


def test_supplementary_not_needed_when_course_passed(env):
    out = env.call("check_exam_eligibility", ctx("S1001"), {"course_code": "CS201", "exam_type": "SUPPLEMENTARY"}).output
    assert out["result"] == "NOT_ELIGIBLE"
    assert any("PASS" in r for r in out["reasons"])


def test_placement_what_if_clearing_a_backlog(env):
    now = env.call("check_placement_eligibility", ctx("S1002"), {}).output
    assert now["result"] == "ELIGIBLE"  # cgpa 6.5 >= 6.5 (exactly at cut-off), 1 backlog <= 1
    assert any(c["name"].startswith("CGPA") and c["passed"] for c in now["checks"])
    what_if = env.call("check_placement_eligibility", ctx("S1002"), {"assume_cleared_courses": ["CS201"]}).output
    assert what_if["result"] == "ELIGIBLE" and any("What-if" in a for a in what_if["assumptions"])


def test_attendance_band_what_if(env):
    out = env.call("check_attendance_band", ctx(), {"attendance_pct": 68}).output
    assert out["result"] == "ELIGIBLE_WITH_CONDONATION"
    out2 = env.call("check_attendance_band", ctx(), {"attendance_pct": 60}).output
    assert out2["result"] == "NOT_ELIGIBLE"
    anon = env.call("check_attendance_band", ToolContext(student_id=None, as_of=AS_OF, programme_hint="B.Tech", services=Services()), {"attendance_pct": 79.99}).output
    assert anon["result"] == "ELIGIBLE_WITH_CONDONATION"


def test_unresolved_conflict_blocks_a_decision(env):
    from tests.helpers import doc, rule
    from app.sources.registry import SourceRegister
    from app.database.repositories import RuleRepo
    reg = SourceRegister()
    reg.upsert(doc("CIRC-A", 2, "circular", "2026-09-01", issuer="Registrar"))
    reg.upsert(doc("CIRC-B", 2, "circular", "2026-09-01", issuer="Controller of Examinations"))
    # both claim a different attempt limit on the same date at the same authority; neither supersedes the other
    rr = RuleRepo()
    rr.upsert(rule("ATT-X1", "min_attendance_pct", ">=", "82", "CIRC-A", "1", "pct", frm="2026-09-01"))
    rr.upsert(rule("ATT-X2", "min_attendance_pct", ">=", "78", "CIRC-B", "1", "pct", frm="2026-09-01"))
    # the earlier circular (ACAD-2026-08, level 2, 2026-08-01) is now older than both; they tie with each other
    out = env.call("check_exam_eligibility", ctx(), {"course_code": "CS201"}).output
    assert out["result"] == "CONFLICT_UNRESOLVED" and out["checks"] == []
    assert out["conflicts"][-1]["kind"] == "unresolved"


def test_calculate_cgpa_falls_back_to_record_without_grade_scale(env):
    out = env.call("calculate_cgpa", ctx(), {}).output
    assert out["status"] == "record_only" and out["stored_cgpa"] == 7.2


def test_registry_has_aliases_and_allowlist(env):
    assert env.spec("calculate_eligibility") is env.spec("check_exam_eligibility")
    assert env.spec("execute_sql") is None and env.spec("run_query") is None
