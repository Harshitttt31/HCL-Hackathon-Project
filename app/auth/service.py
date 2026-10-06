"""Accounts, password hashing, JWT issue/verify and sign-in.

The token only carries who the caller is (username, role, student_id). It never widens what a student can see:
every student tool is still scoped to the one student_id taken from the request context.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt

from app.core.clock import now_iso
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import constant_time_equals, normalize_student_id
from app.database.repositories import StudentRepo
from app.database.sqlite import get_conn

log = get_logger(__name__)

ALGORITHM = "HS256"
ISSUER = "univ-assistant"
PBKDF2_ITERATIONS = 310_000
MIN_PASSWORD_LENGTH = 8

Role = Literal["student", "admin"]


class AuthError(Exception):
    """Sign-in or token failure. `status` is the HTTP status the API returns."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    username: str
    role: Role
    student_id: Optional[str] = None


# ------------------------------------------------------------------------------------------------
# Passwords
# ------------------------------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Used when the username does not exist, so a failed sign-in takes the same time either way.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# ------------------------------------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------------------------------------
@dataclass
class UserRow:
    username: str
    password_hash: str
    role: Role
    student_id: Optional[str]


class UserRepo:
    def get(self, username: str) -> Optional[UserRow]:
        with get_conn() as c:
            row = c.execute("SELECT username, password_hash, role, student_id FROM users WHERE username = ?", (username,)).fetchone()
        return UserRow(**dict(row)) if row else None

    def create(self, username: str, password: str, role: Role, student_id: Optional[str] = None) -> UserRow:
        row = UserRow(username, hash_password(password), role, student_id)
        with get_conn() as c:
            c.execute("INSERT INTO users(username, password_hash, role, student_id, created_at) VALUES (?,?,?,?,?)",
                      (row.username, row.password_hash, row.role, row.student_id, now_iso()))
        return row

    def set_password(self, username: str, password: str) -> None:
        with get_conn() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username))

    def touch_login(self, username: str) -> None:
        with get_conn() as c:
            c.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (now_iso(), username))


# ------------------------------------------------------------------------------------------------
# Tokens
# ------------------------------------------------------------------------------------------------
_process_secret = secrets.token_urlsafe(48)


def _secret() -> str:
    return get_settings().jwt_secret or _process_secret


def issue_token(principal: Principal) -> tuple[str, int]:
    """Return (token, seconds until it expires)."""
    ttl = max(1, get_settings().jwt_expire_minutes) * 60
    now = datetime.now(timezone.utc)
    claims = {"sub": principal.username, "role": principal.role, "sid": principal.student_id, "iss": ISSUER,
              "iat": now, "exp": now + timedelta(seconds=ttl), "jti": uuid.uuid4().hex}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM), ttl


def decode_token(token: str) -> Principal:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[ALGORITHM], issuer=ISSUER, options={"require": ["sub", "role", "exp", "iat"]})
    except jwt.ExpiredSignatureError:
        raise AuthError("session expired, sign in again")
    except jwt.InvalidTokenError:
        raise AuthError("invalid token")
    role, sid = claims.get("role"), claims.get("sid")
    if role not in ("student", "admin") or (role == "student" and not normalize_student_id(sid)):
        raise AuthError("invalid token")
    return Principal(username=str(claims["sub"]), role=role, student_id=sid if role == "student" else None)


# ------------------------------------------------------------------------------------------------
# Sign-in
# ------------------------------------------------------------------------------------------------
class _Lockout:
    """Counts failed sign-ins per username and blocks it for a while after too many."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}

    def check(self, key: str) -> None:
        with self._lock:
            _, until = self._state.get(key, (0, 0.0))
        if until > time.monotonic():
            raise AuthError(f"too many failed attempts, try again in {int(until - time.monotonic()) + 1} seconds", status=429)

    def failed(self, key: str) -> None:
        s = get_settings()
        with self._lock:
            count, _ = self._state.get(key, (0, 0.0))
            count += 1
            until = time.monotonic() + s.login_lockout_seconds if count >= s.login_max_failures else 0.0
            self._state[key] = (0 if until else count, until)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


lockout = _Lockout()


def canonical_username(username: str) -> str:
    """Student ids are case-insensitive (s1001 == S1001); other usernames are lower-cased."""
    raw = username.strip()
    return normalize_student_id(raw) or raw.lower()


def _provision(username: str, password: str, repo: UserRepo) -> Optional[UserRow]:
    """Create the account on first sign-in: the admin from ADMIN_PASSWORD, a student from DEMO_STUDENT_PASSWORD."""
    s = get_settings()
    if username == s.admin_username.lower() and s.admin_password:
        if constant_time_equals(password, s.admin_password):
            return repo.create(username, password, "admin")
        return None
    sid = normalize_student_id(username)
    if sid and s.demo_student_password and StudentRepo().get(sid) is not None:
        if constant_time_equals(password, s.demo_student_password):
            return repo.create(sid, password, "student", sid)
    return None


def authenticate(username: str, password: str) -> Principal:
    key = canonical_username(username)
    lockout.check(key)
    repo = UserRepo()
    user = repo.get(key)
    if user is not None:
        ok = verify_password(password, user.password_hash)
    else:
        verify_password(password, _DUMMY_HASH)
        user = _provision(key, password, repo)
        ok = user is not None
    if not ok or user is None:
        lockout.failed(key)
        log.info("login_failed", extra={"username_known": repo.get(key) is not None})
        raise AuthError("incorrect username or password")
    lockout.succeeded(key)
    repo.touch_login(user.username)
    return Principal(username=user.username, role=user.role, student_id=user.student_id)


def change_password(principal: Principal, current: str, new: str) -> None:
    repo = UserRepo()
    user = repo.get(principal.username)
    if user is None or not verify_password(current, user.password_hash):
        raise AuthError("current password is incorrect", status=400)
    if len(new) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"new password must be at least {MIN_PASSWORD_LENGTH} characters", status=400)
    if new == current:
        raise AuthError("new password must be different from the current one", status=400)
    repo.set_password(principal.username, new)
