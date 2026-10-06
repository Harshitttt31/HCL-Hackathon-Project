"""Identity, hashing and redaction helpers.

The student identity is taken ONLY from the request context (X-Student-Id header).
Nothing in the question text can change it (guide requirement R7).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

from app.core.config import get_settings

# Annex C: "Format S followed by 4 digits". We accept this strictly for identities.
STUDENT_ID_RE = re.compile(r"^S\d{4}$")
# Anything that looks like a student identifier inside free text (used for refusal and redaction).
ID_IN_TEXT_RE = re.compile(r"\b(?:S\d{3,6}|STU[-_ ]?\d{2,8}|ST[-_ ]?\d{3,8})\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)")


def normalize_student_id(raw: Optional[str]) -> Optional[str]:
    """Return the canonical identity, or None if absent/invalid."""
    if raw is None:
        return None
    candidate = raw.strip().upper()
    if STUDENT_ID_RE.match(candidate):
        return candidate
    return None


def _hmac(value: str) -> str:
    key = get_settings().audit_salt.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_student_id(student_id: Optional[str]) -> Optional[str]:
    if not student_id:
        return None
    return _hmac("sid:" + student_id)[:16]


def hash_question(question: str) -> str:
    return _hmac("q:" + question.strip().lower())[:16]


def redact_text(text: str, extra_names: Optional[list[str]] = None) -> str:
    """Mask identifiers, e-mails, phone numbers and (optionally) known personal names."""
    out = ID_IN_TEXT_RE.sub("[ID]", text)
    out = EMAIL_RE.sub("[EMAIL]", out)
    out = PHONE_RE.sub("[PHONE]", out)
    for name in extra_names or []:
        if len(name) >= 3:
            out = re.sub(rf"\b{re.escape(name)}\b", "[NAME]", out, flags=re.IGNORECASE)
    return out


def constant_time_equals(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return False
    return secrets.compare_digest(a.encode(), b.encode())


def is_admin(token: Optional[str]) -> bool:
    configured = get_settings().admin_token
    return bool(configured) and constant_time_equals(token, configured)
