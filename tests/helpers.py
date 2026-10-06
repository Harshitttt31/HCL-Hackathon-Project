"""Small, explicit fixtures shared by tests (independent of the generated corpus)."""
from __future__ import annotations

from app.database.models import AttendanceRow, CourseRow, ResultRow, RuleRow, StudentRow
from app.database.repositories import AttendanceRepo, CourseRepo, ResultRepo, RuleRepo, StudentRepo
from app.database.sqlite import init_db
from app.sources.models import SourceMetadata
from app.sources.registry import SourceRegister


def doc(doc_id, level, doc_type, eff_from, eff_to=None, supersedes="", progs="ALL", batches="ALL", issuer="Office of the Dean (Academics)", title=None):
    return SourceMetadata(doc_id=doc_id, title=title or doc_id, issuer=issuer, authority_level=level, doc_type=doc_type,
                          version="1.0", effective_from=eff_from, effective_to=eff_to, supersedes=supersedes,
                          scope_programmes=progs, scope_batches=batches, retrieved_on="2026-10-05", synthetic="Y")


def rule(rule_id, parameter, op, value, doc_id, section, unit="", frm="2024-07-01", to=None, progs="ALL", batches="ALL", attrs=None):
    return RuleRow(rule_id=rule_id, description=f"{parameter} {op}{value}", parameter=parameter, operator=op, value=value, unit=unit,
                   scope_programmes=progs, scope_batches=batches, effective_from=frm, effective_to=to, source_doc_id=doc_id,
                   source_section=section, attributes=attrs)


def seed_minimal():
    """Annex A worked example + a supplementary notice + a few students."""
    init_db()
    reg = SourceRegister()
    reg.upsert(doc("ACAD-REG-2024", 1, "regulation", "2024-07-01", title="Academic Regulations"))
    reg.upsert(doc("ACAD-2026-08", 2, "circular", "2026-08-01", supersedes="ACAD-REG-2024#7.2", title="Circular on attendance"))
    reg.upsert(doc("DEPT-FAQ", 4, "faq", "2026-09-15", issuer="CSE Department", title="Department FAQ"))
    reg.upsert(doc("SUP-NOTICE", 2, "notice", "2026-01-01", issuer="Controller of Examinations", title="Supplementary Examination Notice"))
    rules = RuleRepo()
    for r in [
        rule("ATT-MIN-01", "min_attendance_pct", ">=", "75", "ACAD-REG-2024", "7.2", "pct"),
        rule("ATT-MIN-02", "min_attendance_pct", ">=", "80", "ACAD-2026-08", "2", "pct", frm="2026-08-01"),
        rule("ATT-MIN-FAQ", "min_attendance_pct", ">=", "65", "DEPT-FAQ", "3", "pct", frm="2026-09-15"),
        rule("ATT-CON-01", "condonation_min_attendance_pct", ">=", "65", "ACAD-REG-2024", "7.4", "pct"),
        rule("SUP-RES-01", "sup_eligible_results", "in", "FAIL;ABSENT", "SUP-NOTICE", "2", "", frm="2026-01-01"),
        rule("SUP-ATT-01", "sup_max_attempts", "<=", "3", "SUP-NOTICE", "4", "count", frm="2026-01-01"),
        rule("PLC-CGPA-01", "placement_min_cgpa", ">=", "6.5", "ACAD-REG-2024", "11.1", "cgpa"),
        rule("PLC-BKL-01", "placement_max_active_backlogs", "<=", "1", "ACAD-REG-2024", "11.2", "count"),
    ]:
        rules.upsert(r)
    courses = CourseRepo()
    for code, name, sem in [("CS201", "Data Structures", 3), ("CS202", "Discrete Mathematics", 3), ("CS301", "Operating Systems", 5)]:
        courses.upsert(CourseRow(course_code=code, course_name=name, programme="B.Tech CSE", semester=sem, credits=4))
    students = StudentRepo()
    students.upsert(StudentRow(student_id="S1001", full_name="Asha Verma", programme="B.Tech CSE", batch_year=2024, current_semester=5, cgpa=7.2, active_backlogs=0))
    students.upsert(StudentRow(student_id="S1002", full_name="Rahul Menon", programme="B.Tech CSE", batch_year=2024, current_semester=5, cgpa=6.5, active_backlogs=1))
    att = AttendanceRepo()
    for sid, code, held, attended in [("S1001", "CS201", 40, 31), ("S1001", "CS202", 50, 40), ("S1001", "CS301", 40, 33),
                                      ("S1002", "CS201", 40, 33), ("S1002", "CS202", 40, 26)]:
        att.upsert(AttendanceRow(student_id=sid, course_code=code, classes_held=held, classes_attended=attended))
    res = ResultRepo()
    res.upsert(ResultRow(student_id="S1002", course_code="CS201", exam_session="2026-MAY", exam_type="REGULAR", internal_marks=18, external_marks=20, total_marks=38, max_marks=100, result="FAIL"))
    res.upsert(ResultRow(student_id="S1001", course_code="CS201", exam_session="2026-MAY", exam_type="REGULAR", internal_marks=30, external_marks=45, total_marks=75, max_marks=100, result="PASS"))
