from __future__ import annotations

import threading
from typing import Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agent.service import AssistantService
from app.auth.service import AuthError, Principal, decode_token
from app.core.config import get_settings
from app.core.security import constant_time_equals, normalize_student_id
from app.rag.ingestion import IngestionService

_lock = threading.Lock()
_service: Optional[AssistantService] = None
_ingestion: Optional[IngestionService] = None


def get_service() -> AssistantService:
    global _service, _ingestion
    with _lock:
        if _service is None:
            _service = AssistantService()
            _ingestion = IngestionService(_service.retriever)
        return _service


def get_ingestion() -> IngestionService:
    get_service()
    assert _ingestion is not None
    return _ingestion


def reset_container() -> None:
    global _service, _ingestion
    with _lock:
        _service = None
        _ingestion = None


_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_principal(credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Optional[Principal]:
    """The signed-in caller, or None when no Bearer token was sent. A token that is sent must be valid."""
    if credentials is None:
        return None
    try:
        return decode_token(credentials.credentials)
    except AuthError as exc:
        raise _unauthorized(str(exc))


def require_principal(principal: Optional[Principal] = Depends(get_principal)) -> Principal:
    if principal is None:
        raise _unauthorized("sign in required")
    return principal


def require_student(principal: Principal = Depends(require_principal)) -> Principal:
    if principal.role != "student" or not principal.student_id:
        raise HTTPException(status_code=403, detail="only available to a signed-in student")
    return principal


def resolve_student_id(principal: Optional[Principal] = Depends(get_principal),
                       x_student_id: Optional[str] = Header(default=None)) -> Optional[str]:
    """The one student identity for this request.

    With a token, it is the token's student (an administrator asks as nobody). An X-Student-Id that disagrees with the
    token is refused rather than silently ignored. Without a token, X-Student-Id is used as the guide's contract
    specifies, unless AUTH_REQUIRED is set.
    """
    if principal is not None:
        if x_student_id and x_student_id.strip() and normalize_student_id(x_student_id) != principal.student_id:
            raise HTTPException(status_code=403, detail="X-Student-Id does not match the signed-in student")
        return principal.student_id
    if x_student_id and x_student_id.strip() and get_settings().auth_required:
        raise _unauthorized("sign in required: send a Bearer token instead of X-Student-Id")
    return x_student_id


def require_admin(principal: Optional[Principal] = Depends(get_principal), x_admin_token: Optional[str] = Header(default=None)) -> None:
    """An administrator token (JWT role admin) or the X-Admin-Token header.

    Without either, admin endpoints stay open only while neither ADMIN_TOKEN nor AUTH_REQUIRED is set (hackathon default).
    """
    if principal is not None:
        if principal.role != "admin":
            raise HTTPException(status_code=403, detail="administrator access required")
        return
    s = get_settings()
    if s.admin_token:
        if not constant_time_equals(x_admin_token, s.admin_token):
            raise _unauthorized("admin token required")
    elif s.auth_required:
        raise _unauthorized("sign in as an administrator")
