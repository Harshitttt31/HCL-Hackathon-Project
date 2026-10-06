"""Sign-in endpoints and the signed-in student's own overview (used by the web interface)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_principal, require_student
from app.auth.service import AuthError, Principal, authenticate, change_password, issue_token
from app.core.clock import today
from app.core.errors import RecordNotFound
from app.database.repositories import StudentRepo
from app.tools.base import ToolContext
from app.tools.rules import GetRuleInput, get_rule
from app.tools.student import CourseCodeInput, get_attendance, get_results

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, description="Student identifier (e.g. S1001) or administrator username")
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    username: str
    role: Literal["student", "admin"]
    student_id: Optional[str] = None
    full_name: Optional[str] = None
    programme: Optional[str] = None
    batch_year: Optional[int] = None
    current_semester: Optional[int] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _user_out(p: Principal) -> UserOut:
    out = UserOut(username=p.username, role=p.role, student_id=p.student_id)
    if p.student_id and (s := StudentRepo().get(p.student_id)):
        out.full_name, out.programme, out.batch_year, out.current_semester = s.full_name, s.programme, s.batch_year, s.current_semester
    return out


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest):
    """Exchange a username and password for a Bearer access token."""
    try:
        principal = await run_in_threadpool(authenticate, body.username, body.password)  # PBKDF2 is deliberately slow
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    token, ttl = issue_token(principal)
    return TokenResponse(access_token=token, expires_in=ttl, user=_user_out(principal))


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(principal: Principal = Depends(require_principal)):
    return _user_out(principal)


@router.post("/auth/change-password", status_code=204, tags=["auth"])
async def change_my_password(body: ChangePasswordRequest, principal: Principal = Depends(require_principal)):
    try:
        await run_in_threadpool(change_password, principal, body.current_password, body.new_password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/me/overview", tags=["auth"])
def overview(principal: Principal = Depends(require_student)) -> dict[str, Any]:
    """The signed-in student's profile, attendance and results, computed by the same deterministic tools as /ask."""
    ctx = ToolContext(student_id=principal.student_id, as_of=today())
    student = ctx.student()
    if student is None:
        raise HTTPException(status_code=404, detail="no student record for this account")
    out: dict[str, Any] = {"as_of": ctx.as_of.isoformat(), "profile": _user_out(principal).model_dump(),
                           "cgpa": student.cgpa, "active_backlogs": student.active_backlogs,
                           "attendance": None, "results": None, "min_attendance_rule": None}
    try:
        out["attendance"] = get_attendance(ctx, CourseCodeInput()).model_dump(include={"courses", "overall_pct"})
    except RecordNotFound:
        pass
    try:
        out["results"] = get_results(ctx, CourseCodeInput()).model_dump(include={"course_status", "backlog_courses"})
    except RecordNotFound:
        pass
    rule = get_rule(ctx, GetRuleInput(parameter="min_attendance_pct"))
    if rule.status == "resolved" and rule.rule:
        r = rule.rule
        out["min_attendance_rule"] = {"rule_id": r.rule_id, "value": r.value, "display": r.display,
                                      "source_doc_id": r.source_doc_id, "source_section": r.source_section}
    return out
