"""Student-record tools. Every one is scoped to ToolContext.student_id (from the X-Student-Id header).

None of the input models has a student_id field and all forbid extra keys, so a question such as
"show me S1002's marks" cannot be turned into a lookup of another student.
"""
from __future__ import annotations

import re
from fractions import Fraction
from typing import Literal, Optional

from pydantic import Field, field_validator

from app.core.errors import RecordNotFound
from app.tools.base import StrictModel, ToolContext
from app.tools.calculator import (
    CalcResult,
    calculate_attendance,
    calculate_cgpa,
    classes_can_miss,
    classes_needed_to_reach,
    grade_point_for,
    marks_percentage,
    round_half_up,
)
from app.tools.rules import RuleInfo, get_rule_service

COURSE_RE = re.compile(r"^[A-Za-z]{2,5}\s?-?\d{2,4}[A-Za-z]?$")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def norm_course(code: str) -> str:
    code = code.strip().upper().replace(" ", "").replace("-", "")
    return code


def session_key(session: str) -> tuple[int, int]:
    """'2026-MAY' -> (2026, 5). Unparseable sessions sort first."""
    m = re.match(r"^(\d{4})[-_ ]?([A-Za-z]{3})", session or "")
    if not m:
        return (0, 0)
    return (int(m.group(1)), _MONTHS.get(m.group(2).upper(), 0))


def attempt_key(session: str, exam_type: str) -> tuple[int, int, int]:
    y, m = session_key(session)
    return (y, m, 1 if exam_type == "SUPPLEMENTARY" else 0)


class CourseCodeInput(StrictModel):
    course_code: Optional[str] = Field(default=None, description="e.g. CS201; omit for all of the student's courses")

    @field_validator("course_code")
    @classmethod
    def _c(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        if not COURSE_RE.match(v.strip()):
            raise ValueError("course_code must look like CS201")
        return norm_course(v)


class EmptyInput(StrictModel):
    pass


# ------------------------------------------------------------------------------------------------
# get_student_profile
# ------------------------------------------------------------------------------------------------
class StudentProfileOutput(StrictModel):
    programme: str
    batch_year: int
    current_semester: int
    cgpa: Optional[float] = None
    active_backlogs: int


def get_student_profile(ctx: ToolContext, _: EmptyInput) -> StudentProfileOutput:
    s = ctx.require_student()
    return StudentProfileOutput(programme=s.programme, batch_year=s.batch_year, current_semester=s.current_semester,
                                cgpa=s.cgpa, active_backlogs=s.active_backlogs)


# ------------------------------------------------------------------------------------------------
# get_course (public catalogue)
# ------------------------------------------------------------------------------------------------
class GetCourseInput(StrictModel):
    course_code: str

    @field_validator("course_code")
    @classmethod
    def _c(cls, v: str) -> str:
        if not COURSE_RE.match(v.strip()):
            raise ValueError("course_code must look like CS201")
        return norm_course(v)


class CourseOutput(StrictModel):
    course_code: str
    course_name: str
    programme: str
    semester: int
    credits: int


def get_course(ctx: ToolContext, inp: GetCourseInput) -> CourseOutput:
    row = ctx.services.courses.get(inp.course_code)
    if row is None:
        raise RecordNotFound(f"course {inp.course_code}")
    return CourseOutput(**row.model_dump())


# ------------------------------------------------------------------------------------------------
# get_attendance / calculate_attendance
# ------------------------------------------------------------------------------------------------
class AttendanceLine(StrictModel):
    course_code: str
    course_name: Optional[str] = None
    classes_held: int
    classes_attended: int
    attendance_pct: float
    calculation: CalcResult


class AttendanceOutput(StrictModel):
    """Single-course shape mirrors the guide's example: classes_held, classes_attended, attendance_pct."""

    course_code: Optional[str] = None
    course_name: Optional[str] = None
    classes_held: Optional[int] = None
    classes_attended: Optional[int] = None
    attendance_pct: Optional[float] = None
    calculation: Optional[CalcResult] = None
    courses: list[AttendanceLine] = Field(default_factory=list)
    overall_pct: Optional[float] = None


def get_attendance(ctx: ToolContext, inp: CourseCodeInput) -> AttendanceOutput:
    student = ctx.require_student()
    if inp.course_code:
        row = ctx.services.attendance.get(student.student_id, inp.course_code)
        if row is None:
            raise RecordNotFound(f"attendance record for {inp.course_code}")
        calc = calculate_attendance(row.classes_attended, row.classes_held)
        course = ctx.services.courses.get(row.course_code)
        return AttendanceOutput(course_code=row.course_code, course_name=course.course_name if course else None,
                                classes_held=row.classes_held, classes_attended=row.classes_attended,
                                attendance_pct=calc.value, calculation=calc)
    rows = ctx.services.attendance.list_for_student(student.student_id)
    if not rows:
        raise RecordNotFound("attendance records")
    # "my attendance" means the running semester; fall back to every record when the catalogue does not say which one that is
    current = [r for r in rows if (c := ctx.services.courses.get(r.course_code)) is not None and c.semester == student.current_semester]
    rows = current or rows
    lines = []
    for r in rows:
        calc = calculate_attendance(r.classes_attended, r.classes_held)
        course = ctx.services.courses.get(r.course_code)
        lines.append(AttendanceLine(course_code=r.course_code, course_name=course.course_name if course else None,
                                    classes_held=r.classes_held, classes_attended=r.classes_attended,
                                    attendance_pct=calc.value, calculation=calc))
    overall = calculate_attendance(sum(r.classes_attended for r in rows), sum(r.classes_held for r in rows))
    return AttendanceOutput(courses=lines, overall_pct=overall.value)


class CalculateAttendanceInput(StrictModel):
    classes_attended: int = Field(ge=0, le=100000)
    classes_held: int = Field(gt=0, le=100000)


def calculate_attendance_tool(ctx: ToolContext, inp: CalculateAttendanceInput) -> CalcResult:
    return calculate_attendance(inp.classes_attended, inp.classes_held)


class AttendanceProjectionInput(StrictModel):
    course_code: str
    threshold_pct: float = Field(gt=0, le=100)
    future_classes: int = Field(default=0, ge=0, le=1000)

    @field_validator("course_code")
    @classmethod
    def _c(cls, v: str) -> str:
        if not COURSE_RE.match(v.strip()):
            raise ValueError("course_code must look like CS201")
        return norm_course(v)


class AttendanceProjectionOutput(StrictModel):
    course_code: str
    threshold_pct: float
    classes_needed: CalcResult
    classes_can_miss: Optional[CalcResult] = None


def calculate_attendance_projection(ctx: ToolContext, inp: AttendanceProjectionInput) -> AttendanceProjectionOutput:
    student = ctx.require_student()
    row = ctx.services.attendance.get(student.student_id, inp.course_code)
    if row is None:
        raise RecordNotFound(f"attendance record for {inp.course_code}")
    needed = classes_needed_to_reach(row.classes_attended, row.classes_held, inp.threshold_pct)
    can_miss = classes_can_miss(row.classes_attended, row.classes_held, inp.threshold_pct, inp.future_classes) if inp.future_classes else None
    return AttendanceProjectionOutput(course_code=row.course_code, threshold_pct=inp.threshold_pct, classes_needed=needed,
                                      classes_can_miss=can_miss)


# ------------------------------------------------------------------------------------------------
# get_results
# ------------------------------------------------------------------------------------------------
class ResultLine(StrictModel):
    course_code: str
    course_name: Optional[str] = None
    exam_session: str
    exam_type: str
    internal_marks: Optional[int] = None
    external_marks: Optional[int] = None
    total_marks: Optional[int] = None
    max_marks: Optional[int] = None
    result: str


class CourseStatus(StrictModel):
    course_code: str
    course_name: Optional[str] = None
    attempts: int
    current_result: str  # result of the latest attempt
    latest_session: str
    latest_exam_type: str


class ResultsOutput(StrictModel):
    results: list[ResultLine]
    course_status: list[CourseStatus]
    backlog_courses: list[str]


def latest_attempts(rows) -> dict[str, list]:
    by_course: dict[str, list] = {}
    for r in rows:
        by_course.setdefault(r.course_code, []).append(r)
    for code in by_course:
        by_course[code].sort(key=lambda r: attempt_key(r.exam_session, r.exam_type))
    return by_course


def get_results(ctx: ToolContext, inp: CourseCodeInput) -> ResultsOutput:
    student = ctx.require_student()
    rows = ctx.services.results.list_for_student(student.student_id, inp.course_code)
    if not rows:
        raise RecordNotFound(f"result records{' for ' + inp.course_code if inp.course_code else ''}")
    names = {}
    for code in {r.course_code for r in rows}:
        c = ctx.services.courses.get(code)
        names[code] = c.course_name if c else None
    lines = [ResultLine(**r.model_dump(exclude={"student_id"}), course_name=names.get(r.course_code)) for r in rows]
    status: list[CourseStatus] = []
    for code, attempts in sorted(latest_attempts(rows).items()):
        last = attempts[-1]
        status.append(CourseStatus(course_code=code, course_name=names.get(code), attempts=len(attempts),
                                   current_result=last.result, latest_session=last.exam_session, latest_exam_type=last.exam_type))
    return ResultsOutput(results=lines, course_status=status,
                         backlog_courses=[s.course_code for s in status if s.current_result != "PASS"])


# ------------------------------------------------------------------------------------------------
# calculate_cgpa
# ------------------------------------------------------------------------------------------------
class CgpaOutput(StrictModel):
    stored_cgpa: Optional[float] = None
    recomputed_cgpa: Optional[float] = None
    matches_record: Optional[bool] = None
    status: Literal["computed", "record_only", "insufficient_data", "grade_scale_conflict"]
    calculation: Optional[CalcResult] = None
    courses_used: int = 0
    grade_scale_rules: list[RuleInfo] = Field(default_factory=list)
    note: Optional[str] = None


def calculate_cgpa_tool(ctx: ToolContext, _: EmptyInput) -> CgpaOutput:
    student = ctx.require_student()
    stored = student.cgpa
    rows = ctx.services.results.list_for_student(student.student_id)
    bands_rules, conflicts, problems = get_rule_service().resolve_banded(ctx, "grade_points", "grade")
    if problems:
        return CgpaOutput(stored_cgpa=stored, status="grade_scale_conflict",
                          note="The grade scale in the available documents is ambiguous; showing the recorded CGPA only.")
    if not rows or not bands_rules:
        return CgpaOutput(stored_cgpa=stored, status="record_only",
                          note="No results or no grade scale available to recompute from; the official record value is shown.")
    bands = []
    for r in bands_rules:
        a = r.attributes or {}
        if {"grade", "min", "max", "points"} <= set(a):
            bands.append(a)
    entries = []
    skipped = 0
    for code, attempts in latest_attempts(rows).items():
        last = attempts[-1]
        course = ctx.services.courses.get(code)
        if course is None or course.credits <= 0:
            skipped += 1
            continue
        if last.result == "PASS" and last.total_marks is not None and last.max_marks:
            gp = grade_point_for(marks_percentage(last.total_marks, last.max_marks), bands)
            if gp is None:
                skipped += 1
                continue
            entries.append((code, course.credits, gp[1]))
        else:
            entries.append((code, course.credits, Fraction(0)))  # not passed: zero grade points until cleared
    if not entries:
        return CgpaOutput(stored_cgpa=stored, status="insufficient_data", note="No course had enough information to compute a CGPA.")
    calc = calculate_cgpa(entries)
    matches = None if stored is None else abs(float(calc.value) - float(stored)) < 0.0051
    note = None
    if skipped:
        note = f"{skipped} course(s) lacked credits or a matching grade band and were left out of the recomputation."
    elif matches is False:
        note = ("The recomputed value differs from the official record; the record may include semesters whose results "
                "are not in the available data. The official record value is authoritative.")
    return CgpaOutput(stored_cgpa=stored, recomputed_cgpa=float(calc.value), matches_record=matches, status="computed",
                      calculation=calc, courses_used=len(entries), grade_scale_rules=bands_rules, note=note)
