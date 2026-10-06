"""Deterministic eligibility decisions.

Every threshold comes from rule_registry via the precedence engine (never from a constant in this file) and
every check records the rule it used and the clause it traces to. The result is computed here; the LLM only
explains it. If a rule needed for the decision is in unresolved conflict the tool refuses to decide
(CONFLICT_UNRESOLVED) rather than pick a side.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Literal, Optional

from pydantic import Field, field_validator

from app.core.errors import RecordNotFound
from app.sources.conflict import ConflictRecord, UpcomingChange
from app.tools.base import StrictModel, ToolContext
from app.tools.calculator import CalcResult, calculate_attendance, compare, to_fraction
from app.tools.rules import RuleInfo, RuleResolution, get_rule_service
from app.tools.student import COURSE_RE, attempt_key, norm_course

# Parameter KEYS (names only; thresholds live in rule_registry).
P_MIN_ATT = "min_attendance_pct"
P_CONDONE = "condonation_min_attendance_pct"
P_SUP_RESULTS = "sup_eligible_results"
P_SUP_ATTEMPTS = "sup_max_attempts"
P_SUP_DEADLINE = "sup_application_deadline"
P_SUP_FEE = "sup_exam_fee"
P_PLC_CGPA = "placement_min_cgpa"
P_PLC_BACKLOGS = "placement_max_active_backlogs"

Result = Literal["ELIGIBLE", "NOT_ELIGIBLE", "ELIGIBLE_WITH_CONDONATION", "INSUFFICIENT_DATA", "CONFLICT_UNRESOLVED", "INSUFFICIENT_RULES"]


class EligibilityCheck(StrictModel):
    name: str
    parameter: str
    rule_id: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[str] = None
    actual: Optional[str] = None
    passed: Optional[bool] = None
    source_doc_id: Optional[str] = None
    source_section: Optional[str] = None
    note: str = ""


class EligibilityOutput(StrictModel):
    result: Result
    rule_id: Optional[str] = None  # the primary rule that decided the outcome
    exam_type: Optional[str] = None
    course_code: Optional[str] = None
    course_name: Optional[str] = None
    checks: list[EligibilityCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    applied_rules: list[RuleInfo] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    upcoming: list[UpcomingChange] = Field(default_factory=list)
    informational: list[RuleResolution] = Field(default_factory=list)  # e.g. deadline/fee, may carry conflicts
    calculation: Optional[CalcResult] = None
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: Optional[str] = None


# ------------------------------------------------------------------------------------------------
# shared helpers
# ------------------------------------------------------------------------------------------------
def _resolve(ctx: ToolContext, parameter: str) -> RuleResolution:
    return get_rule_service().resolve(ctx, parameter)


def _blocked(res: RuleResolution) -> Optional[Result]:
    if res.status == "unresolved":
        return "CONFLICT_UNRESOLVED"
    if res.status == "clarification":
        return "INSUFFICIENT_DATA"
    return None


def _collect(out: EligibilityOutput, *resolutions: RuleResolution) -> None:
    for res in resolutions:
        out.conflicts.extend(res.conflicts)
        out.upcoming.extend(res.upcoming)
        if res.rule and res.rule.rule_id not in {r.rule_id for r in out.applied_rules}:
            out.applied_rules.append(res.rule)
        for tied in res.tied_rules:
            if tied.rule_id not in {r.rule_id for r in out.applied_rules}:
                out.applied_rules.append(tied)


def _check_from_rule(name: str, res: RuleResolution, actual: str, passed: Optional[bool], note: str = "") -> EligibilityCheck:
    r = res.rule
    return EligibilityCheck(name=name, parameter=res.parameter, rule_id=r.rule_id if r else None,
                            operator=r.operator if r else None, threshold=r.display if r else None, actual=actual, passed=passed,
                            source_doc_id=r.source_doc_id if r else None, source_section=r.source_section if r else None, note=note)


def attendance_decision(pct: Fraction, min_res: RuleResolution, cond_res: Optional[RuleResolution]) -> tuple[Result, list[EligibilityCheck], list[str]]:
    """Exact decision from an attendance percentage and the resolved rules."""
    assert min_res.rule is not None
    ok = compare(pct, min_res.rule.operator, min_res.rule.value)
    shown = f"{float(pct):.2f}%"
    checks = [_check_from_rule("attendance meets the minimum", min_res, shown, ok)]
    if ok:
        return "ELIGIBLE", checks, [f"Attendance {shown} satisfies {min_res.rule.display} ({min_res.rule.rule_id}, clause {min_res.rule.source_section})."]
    reasons = [f"Attendance {shown} does not satisfy {min_res.rule.display} ({min_res.rule.rule_id}, clause {min_res.rule.source_section})."]
    if cond_res is not None and cond_res.rule is not None:
        cond_ok = compare(pct, cond_res.rule.operator, cond_res.rule.value)
        checks.append(_check_from_rule("attendance within the condonation range", cond_res, shown, cond_ok))
        if cond_ok:
            reasons.append(f"It does satisfy the condonation limit {cond_res.rule.display} ({cond_res.rule.rule_id}); eligibility is subject to condonation approval.")
            return "ELIGIBLE_WITH_CONDONATION", checks, reasons
        reasons.append(f"It is also below the condonation limit {cond_res.rule.display} ({cond_res.rule.rule_id}).")
    return "NOT_ELIGIBLE", checks, reasons


# ------------------------------------------------------------------------------------------------
# check_attendance_band (what-if, no student record needed)
# ------------------------------------------------------------------------------------------------
class AttendanceBandInput(StrictModel):
    attendance_pct: float = Field(ge=0, le=100)


def check_attendance_band(ctx: ToolContext, inp: AttendanceBandInput) -> EligibilityOutput:
    min_res = _resolve(ctx, P_MIN_ATT)
    cond_res = _resolve(ctx, P_CONDONE)
    out = EligibilityOutput(result="INSUFFICIENT_RULES", exam_type="REGULAR",
                            assumptions=[f"Hypothetical attendance of {inp.attendance_pct:g}% (supplied in the question), not read from a record."])
    _collect(out, min_res, cond_res)
    blocked = _blocked(min_res)
    if blocked:
        out.result = blocked
        out.needs_clarification = min_res.clarification
        out.reasons.append("The minimum-attendance rule could not be resolved; see conflicts.")
        return out
    if min_res.rule is None:
        out.reasons.append("No minimum-attendance rule was found in the rule registry.")
        return out
    result, checks, reasons = attendance_decision(to_fraction(inp.attendance_pct), min_res, cond_res if cond_res.status == "resolved" else None)
    out.result, out.checks, out.reasons, out.rule_id = result, checks, reasons, min_res.rule.rule_id
    return out


# ------------------------------------------------------------------------------------------------
# check_exam_eligibility
# ------------------------------------------------------------------------------------------------
class ExamEligibilityInput(StrictModel):
    course_code: str
    exam_type: Literal["REGULAR", "SUPPLEMENTARY"] = "REGULAR"

    @field_validator("course_code")
    @classmethod
    def _c(cls, v: str) -> str:
        if not COURSE_RE.match(v.strip()):
            raise ValueError("course_code must look like CS201")
        return norm_course(v)


def check_exam_eligibility(ctx: ToolContext, inp: ExamEligibilityInput) -> EligibilityOutput:
    student = ctx.require_student()
    course = ctx.services.courses.get(inp.course_code)
    if course is None:
        raise RecordNotFound(f"course {inp.course_code}")
    out = EligibilityOutput(result="INSUFFICIENT_DATA", exam_type=inp.exam_type, course_code=course.course_code,
                            course_name=course.course_name,
                            assumptions=[f"Rules are applied as in force on {ctx.as_of.isoformat()} for programme '{student.programme}', batch {student.batch_year}."])
    att = ctx.services.attendance.get(student.student_id, course.course_code)
    min_res = _resolve(ctx, P_MIN_ATT)
    cond_res = _resolve(ctx, P_CONDONE)
    _collect(out, min_res, cond_res)

    blocked = _blocked(min_res)
    if blocked:
        out.result = blocked
        out.reasons.append("The minimum-attendance rule is in unresolved conflict between sources; no decision is made.")
        return out
    if min_res.rule is None:
        out.result = "INSUFFICIENT_RULES"
        out.reasons.append("No minimum-attendance rule applies in the rule registry for this student.")
        return out
    cond_for_use = cond_res if cond_res.status == "resolved" else None

    if inp.exam_type == "REGULAR":
        if att is None:
            out.reasons.append(f"No attendance record exists for {course.course_code}.")
            return out
        out.calculation = calculate_attendance(att.classes_attended, att.classes_held)
        result, checks, reasons = attendance_decision(attendance_exact(att.classes_attended, att.classes_held), min_res, cond_for_use)
        out.result, out.checks, out.reasons, out.rule_id = result, checks, reasons, min_res.rule.rule_id
        return out

    # ---- SUPPLEMENTARY ------------------------------------------------------------------------
    rows = ctx.services.results.list_for_student(student.student_id, course.course_code)
    sup_res = _resolve(ctx, P_SUP_RESULTS)
    att_res = _resolve(ctx, P_SUP_ATTEMPTS)
    deadline_res = _resolve(ctx, P_SUP_DEADLINE)
    fee_res = _resolve(ctx, P_SUP_FEE)
    _collect(out, sup_res, att_res)
    out.informational = [r for r in (deadline_res, fee_res) if r.status != "none"]
    for r in (deadline_res, fee_res):
        out.conflicts.extend(r.conflicts)
        out.upcoming.extend(r.upcoming)
    for res in (sup_res, att_res):
        b = _blocked(res)
        if b:
            out.result = b
            out.reasons.append(f"Rule '{res.parameter}' is in unresolved conflict; no decision is made.")
            return out
    if not rows:
        out.reasons.append(f"No result record exists for {course.course_code}, so supplementary eligibility cannot be established.")
        return out
    attempts = sorted(rows, key=lambda r: attempt_key(r.exam_session, r.exam_type))
    last = attempts[-1]
    checks: list[EligibilityCheck] = []
    passed_all = True

    # (1) the latest attempt must be a result that allows a supplementary attempt
    if sup_res.rule is not None:
        allowed = [x.strip().upper() for x in sup_res.rule.value.replace(",", ";").split(";") if x.strip()]
        ok = last.result.upper() in allowed
        checks.append(_check_from_rule("latest result allows a supplementary attempt", sup_res, f"{last.result} ({last.exam_session} {last.exam_type})", ok,
                                       note="allowed results: " + ", ".join(allowed)))
        passed_all &= ok
        if last.result == "PASS":
            out.reasons.append(f"The latest attempt in {course.course_code} ({last.exam_session}) is PASS, so no supplementary attempt is needed.")
        elif not ok:
            out.reasons.append(f"The latest attempt result is {last.result}, which is not among the results that allow a supplementary attempt ({', '.join(allowed)}).")
    else:
        out.assumptions.append("No rule in the registry lists which results allow a supplementary attempt; the result check was skipped.")

    # (2) attendance requirement
    if att is None:
        out.assumptions.append(f"No attendance record exists for {course.course_code}; the attendance requirement could not be checked.")
        out.checks = checks
        out.result = "INSUFFICIENT_DATA" if passed_all else "NOT_ELIGIBLE"
        out.rule_id = sup_res.rule.rule_id if sup_res.rule else None
        return out
    out.calculation = calculate_attendance(att.classes_attended, att.classes_held)
    att_result, att_checks, att_reasons = attendance_decision(attendance_exact(att.classes_attended, att.classes_held), min_res, cond_for_use)
    checks.extend(att_checks)
    out.reasons.extend(att_reasons)

    # (3) attempts limit
    if att_res.rule is not None:
        n = len(attempts)
        ok = compare(n + 1, att_res.rule.operator, att_res.rule.value)  # the NEXT attempt must stay within the limit
        checks.append(_check_from_rule("next attempt stays within the attempt limit", att_res, f"attempt {n + 1} (after {n} used)", ok,
                                       note=f"attempt limit {att_res.rule.display}"))
        passed_all &= ok
        if not ok:
            out.reasons.append(f"{n} attempt(s) already used; another would exceed the limit {att_res.rule.display} ({att_res.rule.rule_id}).")

    out.checks = checks
    out.rule_id = min_res.rule.rule_id
    if not passed_all or att_result == "NOT_ELIGIBLE":
        out.result = "NOT_ELIGIBLE"
    elif att_result == "ELIGIBLE_WITH_CONDONATION":
        out.result = "ELIGIBLE_WITH_CONDONATION"
    else:
        out.result = "ELIGIBLE"
    return out


def attendance_exact(attended: int, held: int) -> Fraction:
    return Fraction(attended * 100, held)


# ------------------------------------------------------------------------------------------------
# check_placement_eligibility (supports what-if: assume some backlogs are cleared)
# ------------------------------------------------------------------------------------------------
class PlacementInput(StrictModel):
    assume_cleared_courses: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("assume_cleared_courses")
    @classmethod
    def _c(cls, v: list[str]) -> list[str]:
        out = []
        for code in v:
            if not COURSE_RE.match(code.strip()):
                raise ValueError(f"'{code}' is not a valid course code")
            out.append(norm_course(code))
        return out


def check_placement_eligibility(ctx: ToolContext, inp: PlacementInput) -> EligibilityOutput:
    from app.tools.student import latest_attempts

    student = ctx.require_student()
    out = EligibilityOutput(result="INSUFFICIENT_DATA", exam_type=None,
                            assumptions=[f"Rules are applied as in force on {ctx.as_of.isoformat()} for programme '{student.programme}', batch {student.batch_year}."])
    cgpa_res = _resolve(ctx, P_PLC_CGPA)
    bl_res = _resolve(ctx, P_PLC_BACKLOGS)
    _collect(out, cgpa_res, bl_res)
    for res in (cgpa_res, bl_res):
        b = _blocked(res)
        if b:
            out.result = b
            out.reasons.append(f"Rule '{res.parameter}' is in unresolved conflict; no decision is made.")
            return out
    if cgpa_res.rule is None and bl_res.rule is None:
        out.result = "INSUFFICIENT_RULES"
        out.reasons.append("No placement-eligibility rules are present in the rule registry.")
        return out

    rows = ctx.services.results.list_for_student(student.student_id)
    status = {code: att[-1].result for code, att in latest_attempts(rows).items()}
    current_backlog_courses = sorted(c for c, r in status.items() if r != "PASS")
    backlogs_now = student.active_backlogs
    if len(current_backlog_courses) != backlogs_now:
        out.assumptions.append(
            f"The record shows {backlogs_now} active backlog(s) while the available results list {len(current_backlog_courses)}; the recorded count is used.")
    cleared = []
    for code in inp.assume_cleared_courses:
        if code in current_backlog_courses:
            cleared.append(code)
        else:
            out.assumptions.append(f"{code} is not a current backlog in your results, so assuming it is cleared changes nothing.")
    backlogs_after = max(0, backlogs_now - len(cleared))
    if cleared:
        out.assumptions.append(
            f"What-if: assumes you clear {', '.join(cleared)} (backlogs {backlogs_now} -> {backlogs_after}). "
            "Your CGPA is taken from the current record; a new pass may change it.")

    checks: list[EligibilityCheck] = []
    verdicts: list[bool] = []
    if cgpa_res.rule is not None:
        if student.cgpa is None:
            out.reasons.append("No CGPA is recorded for this student.")
            return out
        ok = compare(student.cgpa, cgpa_res.rule.operator, cgpa_res.rule.value)
        checks.append(_check_from_rule("CGPA meets the placement minimum", cgpa_res, f"{student.cgpa:.2f}", ok))
        verdicts.append(ok)
        out.reasons.append(f"CGPA {student.cgpa:.2f} {'satisfies' if ok else 'does not satisfy'} {cgpa_res.rule.display} ({cgpa_res.rule.rule_id}).")
    if bl_res.rule is not None:
        ok = compare(backlogs_after, bl_res.rule.operator, bl_res.rule.value)
        checks.append(_check_from_rule("active backlogs within the placement limit", bl_res, f"{backlogs_after} active backlog(s)", ok))
        verdicts.append(ok)
        out.reasons.append(f"{backlogs_after} active backlog(s) {'satisfies' if ok else 'does not satisfy'} {bl_res.rule.display} ({bl_res.rule.rule_id}).")
    out.checks = checks
    out.rule_id = (cgpa_res.rule or bl_res.rule).rule_id  # type: ignore[union-attr]
    out.result = "ELIGIBLE" if all(verdicts) else "NOT_ELIGIBLE"
    return out
